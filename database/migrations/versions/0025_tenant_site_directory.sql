CREATE TABLE control.user_site_membership_routes (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    site_membership_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES control.users (id),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_membership_id),
    UNIQUE (tenant_id, site_id, user_id),
    FOREIGN KEY (tenant_id, site_id, site_membership_id)
        REFERENCES app.site_memberships (tenant_id, site_id, id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX user_site_membership_routes_identity
ON control.user_site_membership_routes (user_id, tenant_id, site_id);
REVOKE ALL ON control.user_site_membership_routes
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

ALTER TABLE app.site_memberships NO FORCE ROW LEVEL SECURITY;
INSERT INTO control.user_site_membership_routes (
    tenant_id, site_id, site_membership_id, user_id, created_at
)
SELECT tenant_id, site_id, id, user_id, created_at
FROM app.site_memberships;
ALTER TABLE app.site_memberships FORCE ROW LEVEL SECURITY;

CREATE FUNCTION control.route_user_site_membership() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM control.user_site_membership_routes route
        WHERE route.tenant_id = OLD.tenant_id
          AND route.site_membership_id = OLD.id;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        UPDATE control.user_site_membership_routes route
        SET tenant_id = NEW.tenant_id,
            site_id = NEW.site_id,
            site_membership_id = NEW.id,
            user_id = NEW.user_id,
            created_at = NEW.created_at
        WHERE route.tenant_id = OLD.tenant_id
          AND route.site_membership_id = OLD.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'site membership route is missing' USING ERRCODE = '23503';
        END IF;
        RETURN NEW;
    END IF;

    INSERT INTO control.user_site_membership_routes (
        tenant_id, site_id, site_membership_id, user_id, created_at
    ) VALUES (
        NEW.tenant_id, NEW.site_id, NEW.id, NEW.user_id, NEW.created_at
    );
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.route_user_site_membership() FROM PUBLIC;
CREATE TRIGGER site_membership_route_after_change
AFTER INSERT OR UPDATE OR DELETE ON app.site_memberships
FOR EACH ROW EXECUTE FUNCTION control.route_user_site_membership();

CREATE FUNCTION control.list_tenant_sites(
    p_session_hash bytea,
    p_current_recovery_generation text
)
RETURNS TABLE (
    outcome text,
    directory_tenant_id uuid,
    tenant_name text,
    site_id uuid,
    site_name text,
    primary_origin text,
    timezone text,
    reporting_currency text,
    site_state text,
    ownership_status text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_tenant_id uuid;
    v_user_id uuid;
    v_tenant_name text;
    v_site_id uuid;
    v_site record;
    v_site_count integer := 0;
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    THEN
        RAISE EXCEPTION 'invalid_tenant_site_directory_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.user_id
      INTO v_tenant_id, v_user_id
      FROM app.sessions AS tenant_session
      JOIN control.identity_sessions AS identity_session
        ON identity_session.id = tenant_session.identity_session_id
       AND identity_session.user_id = tenant_session.user_id
      JOIN control.users AS identity_user
        ON identity_user.id = tenant_session.user_id
     WHERE tenant_session.session_token_hash = p_session_hash
       AND tenant_session.revoked_at IS NULL
       AND tenant_session.expires_at > transaction_timestamp()
       AND identity_session.revoked_at IS NULL
       AND identity_session.expires_at > transaction_timestamp()
       AND identity_session.recovery_generation = p_current_recovery_generation
       AND tenant_session.auth_time = identity_session.auth_time
       AND tenant_session.mfa_level = identity_session.authentication_level
       AND identity_user.disabled_at IS NULL
     FOR KEY SHARE OF tenant_session, identity_session, identity_user;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'invalid_session'::text, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    SELECT tenant.name
      INTO v_tenant_name
      FROM app.memberships AS membership
      JOIN app.tenants AS tenant
        ON tenant.tenant_id = membership.tenant_id
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.state = 'active'
       AND tenant.lifecycle = 'active'
     FOR KEY SHARE OF membership, tenant;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'invalid_session'::text, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text;
        RETURN;
    END IF;

    FOR v_site_id IN
        SELECT route.site_id
          FROM control.user_site_membership_routes AS route
         WHERE route.tenant_id = v_tenant_id
           AND route.user_id = v_user_id
         ORDER BY route.site_id
    LOOP
        PERFORM set_config('signal.site_id', v_site_id::text, true);
        SELECT site.id, site.name, site.primary_origin, site.timezone,
               site.reporting_currency, site.state, site.ownership_status
          INTO v_site
          FROM app.site_memberships AS site_membership
          JOIN app.sites AS site
            ON site.tenant_id = site_membership.tenant_id
           AND site.id = site_membership.site_id
         WHERE site_membership.tenant_id = v_tenant_id
           AND site_membership.site_id = v_site_id
           AND site_membership.user_id = v_user_id
           AND site_membership.state = 'active'
           AND site_membership.permission_set =
               '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb
           AND site.state <> 'archived'
         FOR KEY SHARE OF site_membership, site;
        IF FOUND THEN
            v_site_count := v_site_count + 1;
            IF v_site_count > 100 THEN
                RAISE EXCEPTION 'tenant_site_directory_limit_exceeded'
                    USING ERRCODE = '54000';
            END IF;
            RETURN QUERY SELECT 'listed'::text, v_tenant_id, v_tenant_name,
                v_site.id, v_site.name, v_site.primary_origin, v_site.timezone,
                v_site.reporting_currency, v_site.state, v_site.ownership_status;
        END IF;
    END LOOP;

    IF v_site_count = 0 THEN
        RETURN QUERY SELECT 'listed'::text, v_tenant_id, v_tenant_name,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text;
    END IF;
END
$$;
REVOKE ALL ON FUNCTION control.list_tenant_sites(bytea, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.list_tenant_sites(bytea, text) TO signal_identity;
