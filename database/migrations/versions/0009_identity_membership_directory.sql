CREATE TABLE control.user_membership_routes (
    tenant_id uuid NOT NULL,
    membership_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES control.users (id),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, membership_id),
    UNIQUE (tenant_id, user_id),
    FOREIGN KEY (tenant_id, membership_id)
        REFERENCES app.memberships (tenant_id, id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX user_membership_routes_identity
ON control.user_membership_routes (user_id, tenant_id);
REVOKE ALL ON control.user_membership_routes
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler;

ALTER TABLE app.memberships NO FORCE ROW LEVEL SECURITY;
INSERT INTO control.user_membership_routes (tenant_id, membership_id, user_id, created_at)
SELECT tenant_id, id, user_id, created_at
FROM app.memberships;
ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY;

CREATE FUNCTION control.route_user_membership() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM control.user_membership_routes route
        WHERE route.tenant_id = OLD.tenant_id
          AND route.membership_id = OLD.id;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        UPDATE control.user_membership_routes route
        SET tenant_id = NEW.tenant_id,
            membership_id = NEW.id,
            user_id = NEW.user_id,
            created_at = NEW.created_at
        WHERE route.tenant_id = OLD.tenant_id
          AND route.membership_id = OLD.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'membership route is missing' USING ERRCODE = '23503';
        END IF;
        RETURN NEW;
    END IF;

    INSERT INTO control.user_membership_routes (
        tenant_id,
        membership_id,
        user_id,
        created_at
    ) VALUES (
        NEW.tenant_id,
        NEW.id,
        NEW.user_id,
        NEW.created_at
    );
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.route_user_membership() FROM PUBLIC;
CREATE TRIGGER membership_route_after_change
AFTER INSERT OR UPDATE OR DELETE ON app.memberships
FOR EACH ROW EXECUTE FUNCTION control.route_user_membership();

CREATE FUNCTION control.list_identity_memberships(
    p_identity_session_hash bytea,
    p_recovery_generation text
) RETURNS TABLE (
    identity_user_id uuid,
    member_tenant_id uuid,
    tenant_name text,
    member_role_key text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE
    v_user_id uuid;
    v_tenant_id uuid;
    v_membership_found boolean := false;
BEGIN
    IF p_identity_session_hash IS NULL
       OR octet_length(p_identity_session_hash) != 32
       OR p_recovery_generation IS NULL
       OR p_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    THEN
        RETURN;
    END IF;

    SELECT identity_session.user_id
    INTO v_user_id
    FROM control.identity_sessions identity_session
    JOIN control.users identity_user
      ON identity_user.id = identity_session.user_id
     AND identity_user.disabled_at IS NULL
    WHERE identity_session.token_hash = p_identity_session_hash
      AND identity_session.recovery_generation = p_recovery_generation
      AND identity_session.revoked_at IS NULL
      AND identity_session.expires_at > transaction_timestamp();
    IF NOT FOUND THEN
        RETURN;
    END IF;

    FOR v_tenant_id IN
        SELECT route.tenant_id
        FROM control.user_membership_routes route
        JOIN control.tenant_directory directory
          ON directory.tenant_id = route.tenant_id
         AND directory.lifecycle = 'active'
        WHERE route.user_id = v_user_id
        ORDER BY route.tenant_id
    LOOP
        PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
        RETURN QUERY
        SELECT
            v_user_id,
            tenant.tenant_id,
            tenant.name,
            membership.role_key
        FROM app.tenants tenant
        JOIN app.memberships membership
          ON membership.tenant_id = tenant.tenant_id
         AND membership.user_id = v_user_id
         AND membership.state = 'active'
        WHERE tenant.tenant_id = v_tenant_id
          AND tenant.lifecycle = 'active';
        IF FOUND THEN
            v_membership_found := true;
        END IF;
    END LOOP;

    IF NOT v_membership_found THEN
        RETURN QUERY SELECT v_user_id, NULL::uuid, NULL::text, NULL::text;
    END IF;
END
$$;
REVOKE ALL ON FUNCTION control.list_identity_memberships(bytea, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.list_identity_memberships(bytea, text) TO signal_identity;
