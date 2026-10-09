CREATE TABLE control.tenant_site_routes (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    primary_origin text NOT NULL,
    state text NOT NULL CHECK (state IN ('onboarding', 'active', 'archived')),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id),
    FOREIGN KEY (tenant_id, site_id)
        REFERENCES app.sites (tenant_id, id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX tenant_site_routes_state
ON control.tenant_site_routes (tenant_id, state, site_id);
REVOKE ALL ON control.tenant_site_routes
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

ALTER TABLE app.sites NO FORCE ROW LEVEL SECURITY;
INSERT INTO control.tenant_site_routes (
    tenant_id, site_id, primary_origin, state, created_at
)
SELECT tenant_id, id, primary_origin, state, created_at FROM app.sites;
ALTER TABLE app.sites FORCE ROW LEVEL SECURITY;

CREATE FUNCTION control.route_tenant_site() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM control.tenant_site_routes AS route
         WHERE route.tenant_id = OLD.tenant_id
           AND route.site_id = OLD.id;
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        UPDATE control.tenant_site_routes AS route
           SET tenant_id = NEW.tenant_id,
               site_id = NEW.id,
               primary_origin = NEW.primary_origin,
               state = NEW.state,
               created_at = NEW.created_at
         WHERE route.tenant_id = OLD.tenant_id
           AND route.site_id = OLD.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'tenant site route is missing' USING ERRCODE = '23503';
        END IF;
        RETURN NEW;
    END IF;
    INSERT INTO control.tenant_site_routes (
        tenant_id, site_id, primary_origin, state, created_at
    ) VALUES (
        NEW.tenant_id, NEW.id, NEW.primary_origin, NEW.state, NEW.created_at
    );
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.route_tenant_site() FROM PUBLIC;
CREATE TRIGGER tenant_site_route_after_change
AFTER INSERT OR UPDATE OR DELETE ON app.sites
FOR EACH ROW EXECUTE FUNCTION control.route_tenant_site();

CREATE TABLE app.site_onboarding_events (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    site_id uuid NOT NULL,
    site_membership_id uuid NOT NULL,
    session_id uuid NOT NULL,
    user_id uuid NOT NULL,
    event_type text NOT NULL CHECK (event_type = 'site.onboarded'),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    scope_kind text NOT NULL CHECK (scope_kind = 'tenant'),
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    site_name text NOT NULL CHECK (length(site_name) BETWEEN 1 AND 200),
    primary_origin text NOT NULL CHECK (
        length(primary_origin) BETWEEN 9 AND 2048
        AND primary_origin ~ '^https://[^/?#[:space:]]+$'
    ),
    timezone text NOT NULL CHECK (length(timezone) BETWEEN 1 AND 128),
    reporting_currency text NOT NULL CHECK (reporting_currency ~ '^[A-Z]{3}$'),
    role_key text NOT NULL CHECK (role_key = 'owner'),
    authentication_level text NOT NULL CHECK (
        authentication_level IN ('primary', 'mfa')
    ),
    membership_authorization_epoch bigint NOT NULL CHECK (
        membership_authorization_epoch > 0
    ),
    site_authorization_epoch bigint NOT NULL CHECK (site_authorization_epoch = 1),
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    ),
    session_version bigint NOT NULL CHECK (session_version > 1),
    event_hash bytea NOT NULL CHECK (octet_length(event_hash) = 32),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT site_onboarding_events_site_key UNIQUE (tenant_id, site_id),
    CONSTRAINT site_onboarding_events_request_key
        UNIQUE (tenant_id, user_id, idempotency_key),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, session_id) REFERENCES app.sessions (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app.memberships (tenant_id, user_id),
    FOREIGN KEY (tenant_id, site_id, site_membership_id)
        REFERENCES app.site_memberships (tenant_id, site_id, id)
);
CREATE INDEX site_onboarding_events_actor
ON app.site_onboarding_events (tenant_id, user_id, occurred_at DESC, id);

ALTER TABLE app.site_onboarding_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.site_onboarding_events FORCE ROW LEVEL SECURITY;
CREATE POLICY site_onboarding_event_scope
ON app.site_onboarding_events
USING (tenant_id = app.current_tenant_id())
WITH CHECK (tenant_id = app.current_tenant_id());
REVOKE ALL ON app.site_onboarding_events
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION app.validate_site_onboarding_event() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_request_hash bytea;
    v_event_hash bytea;
BEGIN
    IF NEW.site_name <> btrim(NEW.site_name)
       OR NEW.site_name ~ '[[:cntrl:]]'
       OR NEW.timezone <> btrim(NEW.timezone)
       OR NEW.timezone ~ '[[:cntrl:]]'
    THEN
        RAISE EXCEPTION 'site onboarding event text is invalid' USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM app.sites AS site
          JOIN app.site_memberships AS site_membership
            ON site_membership.tenant_id = site.tenant_id
           AND site_membership.site_id = site.id
          JOIN app.memberships AS membership
            ON membership.tenant_id = site_membership.tenant_id
           AND membership.user_id = site_membership.user_id
          JOIN app.sessions AS tenant_session
            ON tenant_session.tenant_id = site.tenant_id
           AND tenant_session.id = NEW.session_id
           AND tenant_session.user_id = membership.user_id
         WHERE site.tenant_id = NEW.tenant_id
           AND site.id = NEW.site_id
           AND site.name = NEW.site_name
           AND site.primary_origin = NEW.primary_origin
           AND site.timezone = NEW.timezone
           AND site.reporting_currency = NEW.reporting_currency
           AND site.state = 'onboarding'
           AND site.ownership_status = 'unverified'
           AND site.created_at IS NOT DISTINCT FROM NEW.occurred_at
           AND site_membership.id = NEW.site_membership_id
           AND site_membership.user_id = NEW.user_id
           AND site_membership.permission_set =
               '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb
           AND site_membership.authorization_epoch = NEW.site_authorization_epoch
           AND site_membership.state = 'active'
           AND site_membership.created_at IS NOT DISTINCT FROM NEW.occurred_at
           AND membership.role_key = NEW.role_key
           AND membership.state = 'active'
           AND membership.authorization_epoch = NEW.membership_authorization_epoch
           AND tenant_session.active_site_id = NEW.site_id
           AND tenant_session.session_version = NEW.session_version
           AND tenant_session.updated_at IS NOT DISTINCT FROM NEW.occurred_at
    ) THEN
        RAISE EXCEPTION 'site onboarding event reference is invalid'
            USING ERRCODE = '23503';
    END IF;

    v_request_hash := sha256(
        convert_to(
            'site_name=' || NEW.site_name || E'\n'
            || 'primary_origin=' || NEW.primary_origin || E'\n'
            || 'timezone=' || NEW.timezone || E'\n'
            || 'reporting_currency=' || NEW.reporting_currency,
            'UTF8'
        )
    );
    IF NEW.request_hash IS DISTINCT FROM v_request_hash THEN
        RAISE EXCEPTION 'site onboarding request hash is invalid' USING ERRCODE = '23514';
    END IF;

    v_event_hash := sha256(
        convert_to(
            'authentication_level=' || NEW.authentication_level || E'\n'
            || 'event_id=' || NEW.id::text || E'\n'
            || 'idempotency_key=' || NEW.idempotency_key::text || E'\n'
            || 'membership_authorization_epoch='
            || NEW.membership_authorization_epoch::text || E'\n'
            || 'occurred_at='
            || to_char(
                NEW.occurred_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            ) || E'\n'
            || 'recovery_generation=' || NEW.recovery_generation || E'\n'
            || 'request_hash=' || encode(NEW.request_hash, 'hex') || E'\n'
            || 'scope_kind=' || NEW.scope_kind || E'\n'
            || 'session_id=' || NEW.session_id::text || E'\n'
            || 'session_version=' || NEW.session_version::text || E'\n'
            || 'site_id=' || NEW.site_id::text || E'\n'
            || 'site_membership_id=' || NEW.site_membership_id::text || E'\n'
            || 'tenant_id=' || NEW.tenant_id::text || E'\n'
            || 'user_id=' || NEW.user_id::text,
            'UTF8'
        )
    );
    IF NEW.event_hash IS DISTINCT FROM v_event_hash THEN
        RAISE EXCEPTION 'site onboarding event hash is invalid' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION app.validate_site_onboarding_event() FROM PUBLIC;
CREATE TRIGGER site_onboarding_event_reference_guard
BEFORE INSERT ON app.site_onboarding_events
FOR EACH ROW EXECUTE FUNCTION app.validate_site_onboarding_event();
CREATE TRIGGER site_onboarding_events_immutable
BEFORE UPDATE OR DELETE ON app.site_onboarding_events
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.onboard_site(
    p_session_hash bytea,
    p_current_recovery_generation text,
    p_site_id uuid,
    p_site_membership_id uuid,
    p_context_event_id uuid,
    p_event_id uuid,
    p_idempotency_key uuid,
    p_request_hash bytea,
    p_site_name text,
    p_primary_origin text,
    p_timezone text,
    p_reporting_currency text,
    p_expected_session_version bigint,
    p_site_limit integer
)
RETURNS TABLE (
    outcome text,
    onboarded_tenant_id uuid,
    onboarded_user_id uuid,
    onboarded_site_id uuid,
    site_name text,
    primary_origin text,
    timezone text,
    reporting_currency text,
    selected_session_version bigint,
    request_replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_tenant_id uuid;
    v_session_id uuid;
    v_user_id uuid;
    v_previous_site_id uuid;
    v_session_version bigint;
    v_authentication_level text;
    v_membership_epoch bigint;
    v_existing record;
    v_previous_hash bytea;
    v_context_event_hash bytea;
    v_event_hash bytea;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_site_id IS NULL OR p_site_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_site_membership_id IS NULL OR p_site_membership_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_context_event_id IS NULL OR p_context_event_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_event_id IS NULL OR p_event_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_idempotency_key IS NULL OR p_idempotency_key::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_site_name IS NULL OR length(p_site_name) NOT BETWEEN 1 AND 200
       OR p_site_name <> btrim(p_site_name) OR p_site_name ~ '[[:cntrl:]]'
       OR p_primary_origin IS NULL OR length(p_primary_origin) NOT BETWEEN 9 AND 2048
       OR p_primary_origin !~ '^https://[^/?#[:space:]]+$'
       OR p_timezone IS NULL OR length(p_timezone) NOT BETWEEN 1 AND 128
       OR p_timezone <> btrim(p_timezone) OR p_timezone ~ '[[:cntrl:]]'
       OR p_reporting_currency IS NULL
       OR p_reporting_currency !~ '^[A-Z]{3}$'
       OR p_expected_session_version IS NULL OR p_expected_session_version <= 0
       OR p_site_limit IS NULL OR p_site_limit <> 100
    THEN
        RAISE EXCEPTION 'invalid_site_onboarding_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.id, tenant_session.user_id,
           tenant_session.active_site_id, tenant_session.session_version,
           identity_session.authentication_level
      INTO v_tenant_id, v_session_id, v_user_id, v_previous_site_id,
           v_session_version, v_authentication_level
      FROM app.sessions AS tenant_session
      JOIN control.identity_sessions AS identity_session
        ON identity_session.id = tenant_session.identity_session_id
       AND identity_session.user_id = tenant_session.user_id
      JOIN control.users AS identity_user
        ON identity_user.id = tenant_session.user_id
     WHERE tenant_session.session_token_hash = p_session_hash
       AND tenant_session.revoked_at IS NULL
       AND tenant_session.expires_at > v_now
       AND identity_session.revoked_at IS NULL
       AND identity_session.expires_at > v_now
       AND identity_session.recovery_generation = p_current_recovery_generation
       AND tenant_session.auth_time = identity_session.auth_time
       AND tenant_session.mfa_level = identity_session.authentication_level
       AND identity_user.disabled_at IS NULL
     FOR UPDATE OF tenant_session;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'invalid_session'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    SELECT membership.authorization_epoch
      INTO v_membership_epoch
      FROM app.memberships AS membership
      JOIN app.tenants AS tenant ON tenant.tenant_id = membership.tenant_id
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.role_key = 'owner'
       AND membership.state = 'active'
       AND tenant.lifecycle = 'active'
     FOR UPDATE OF membership, tenant;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'onboarding_denied'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    SELECT event.site_id, event.site_name, event.primary_origin, event.timezone,
           event.reporting_currency, event.session_version, event.request_hash
      INTO v_existing
      FROM app.site_onboarding_events AS event
     WHERE event.tenant_id = v_tenant_id
       AND event.user_id = v_user_id
       AND event.idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.request_hash IS DISTINCT FROM p_request_hash THEN
            RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
                NULL::bigint, NULL::boolean;
        ELSE
            RETURN QUERY SELECT 'onboarded'::text, v_tenant_id, v_user_id,
                v_existing.site_id, v_existing.site_name, v_existing.primary_origin,
                v_existing.timezone, v_existing.reporting_currency,
                v_existing.session_version, true;
        END IF;
        RETURN;
    END IF;

    IF v_session_version <> p_expected_session_version THEN
        RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM control.tenant_site_routes AS route
         WHERE route.tenant_id = v_tenant_id
           AND route.primary_origin = p_primary_origin
           AND route.state <> 'archived'
    ) THEN
        RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    IF (
        SELECT count(*)
          FROM control.tenant_site_routes AS route
         WHERE route.tenant_id = v_tenant_id
           AND route.state <> 'archived'
    ) >= p_site_limit THEN
        RETURN QUERY SELECT 'limit_reached'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    IF v_previous_site_id IS NOT NULL THEN
        PERFORM set_config('signal.site_id', v_previous_site_id::text, true);
        SELECT event.event_hash
          INTO v_previous_hash
          FROM app.session_site_context_events AS event
         WHERE event.tenant_id = v_tenant_id
           AND event.session_id = v_session_id
         ORDER BY event.session_version DESC, event.id DESC
         LIMIT 1;
    END IF;
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    INSERT INTO app.sites (
        tenant_id, id, name, primary_origin, timezone, reporting_currency,
        state, ownership_status, created_at
    ) VALUES (
        v_tenant_id, p_site_id, p_site_name, p_primary_origin, p_timezone,
        p_reporting_currency, 'onboarding', 'unverified', v_now
    );
    INSERT INTO app.site_memberships (
        tenant_id, site_id, id, user_id, permission_set, authorization_epoch,
        state, created_at, updated_at
    ) VALUES (
        v_tenant_id, p_site_id, p_site_membership_id, v_user_id,
        '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb,
        1, 'active', v_now, v_now
    );

    v_session_version := v_session_version + 1;
    UPDATE app.sessions AS tenant_session
       SET active_site_id = p_site_id,
           session_version = v_session_version,
           row_version = tenant_session.row_version + 1,
           last_seen_at = v_now,
           updated_at = v_now
     WHERE tenant_session.tenant_id = v_tenant_id
       AND tenant_session.id = v_session_id
       AND tenant_session.session_token_hash = p_session_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'site onboarding lost its locked session' USING ERRCODE = '40001';
    END IF;

    v_context_event_hash := sha256(
        convert_to(
            'authentication_level=' || v_authentication_level || E'\n'
            || 'event_id=' || p_context_event_id::text || E'\n'
            || 'membership_authorization_epoch=' || v_membership_epoch::text || E'\n'
            || 'occurred_at='
            || to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            || E'\n'
            || 'previous_hash=' || COALESCE(encode(v_previous_hash, 'hex'), '') || E'\n'
            || 'recovery_generation=' || p_current_recovery_generation || E'\n'
            || 'role_key=owner' || E'\n'
            || 'session_id=' || v_session_id::text || E'\n'
            || 'session_version=' || v_session_version::text || E'\n'
            || 'site_authorization_epoch=1' || E'\n'
            || 'site_id=' || p_site_id::text || E'\n'
            || 'tenant_id=' || v_tenant_id::text || E'\n'
            || 'user_id=' || v_user_id::text,
            'UTF8'
        )
    );
    INSERT INTO app.session_site_context_events (
        tenant_id, id, site_id, session_id, user_id, session_version,
        role_key, authentication_level, membership_authorization_epoch,
        site_authorization_epoch, recovery_generation, previous_hash,
        event_hash, occurred_at
    ) VALUES (
        v_tenant_id, p_context_event_id, p_site_id, v_session_id, v_user_id,
        v_session_version, 'owner', v_authentication_level, v_membership_epoch,
        1, p_current_recovery_generation, v_previous_hash,
        v_context_event_hash, v_now
    );

    v_event_hash := sha256(
        convert_to(
            'authentication_level=' || v_authentication_level || E'\n'
            || 'event_id=' || p_event_id::text || E'\n'
            || 'idempotency_key=' || p_idempotency_key::text || E'\n'
            || 'membership_authorization_epoch=' || v_membership_epoch::text || E'\n'
            || 'occurred_at='
            || to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            || E'\n'
            || 'recovery_generation=' || p_current_recovery_generation || E'\n'
            || 'request_hash=' || encode(p_request_hash, 'hex') || E'\n'
            || 'scope_kind=tenant' || E'\n'
            || 'session_id=' || v_session_id::text || E'\n'
            || 'session_version=' || v_session_version::text || E'\n'
            || 'site_id=' || p_site_id::text || E'\n'
            || 'site_membership_id=' || p_site_membership_id::text || E'\n'
            || 'tenant_id=' || v_tenant_id::text || E'\n'
            || 'user_id=' || v_user_id::text,
            'UTF8'
        )
    );
    INSERT INTO app.site_onboarding_events (
        tenant_id, id, site_id, site_membership_id, session_id, user_id,
        event_type, schema_version, scope_kind, idempotency_key, request_hash, site_name,
        primary_origin, timezone, reporting_currency, role_key,
        authentication_level, membership_authorization_epoch,
        site_authorization_epoch, recovery_generation, session_version,
        event_hash, occurred_at
    ) VALUES (
        v_tenant_id, p_event_id, p_site_id, p_site_membership_id, v_session_id,
        v_user_id, 'site.onboarded', 1, 'tenant', p_idempotency_key, p_request_hash,
        p_site_name, p_primary_origin, p_timezone, p_reporting_currency, 'owner',
        v_authentication_level, v_membership_epoch, 1,
        p_current_recovery_generation, v_session_version, v_event_hash, v_now
    );

    RETURN QUERY SELECT 'onboarded'::text, v_tenant_id, v_user_id, p_site_id,
        p_site_name, p_primary_origin, p_timezone, p_reporting_currency,
        v_session_version, false;
END
$$;
REVOKE ALL ON FUNCTION control.onboard_site(
    bytea, text, uuid, uuid, uuid, uuid, uuid, bytea, text, text, text, text,
    bigint, integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.onboard_site(
    bytea, text, uuid, uuid, uuid, uuid, uuid, bytea, text, text, text, text,
    bigint, integer
) TO signal_identity;
