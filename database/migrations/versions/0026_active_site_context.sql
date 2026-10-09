ALTER TABLE app.sessions
    ADD COLUMN active_site_id uuid,
    ADD CONSTRAINT sessions_active_site_fk
        FOREIGN KEY (tenant_id, active_site_id)
        REFERENCES app.sites (tenant_id, id);
CREATE INDEX sessions_active_site
ON app.sessions (tenant_id, active_site_id)
WHERE active_site_id IS NOT NULL;

CREATE TABLE app.session_site_context_events (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    site_id uuid NOT NULL,
    session_id uuid NOT NULL,
    user_id uuid NOT NULL,
    session_version bigint NOT NULL CHECK (session_version > 1),
    role_key text NOT NULL CHECK (
        role_key IN ('viewer', 'analyst', 'editor', 'approver', 'admin', 'owner')
    ),
    authentication_level text NOT NULL CHECK (
        authentication_level IN ('primary', 'mfa')
    ),
    membership_authorization_epoch bigint NOT NULL CHECK (
        membership_authorization_epoch > 0
    ),
    site_authorization_epoch bigint NOT NULL CHECK (site_authorization_epoch > 0),
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    ),
    previous_hash bytea CHECK (
        previous_hash IS NULL OR octet_length(previous_hash) = 32
    ),
    event_hash bytea NOT NULL CHECK (octet_length(event_hash) = 32),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, session_id, session_version),
    FOREIGN KEY (tenant_id, session_id)
        REFERENCES app.sessions (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id)
        REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX session_site_context_history
ON app.session_site_context_events (
    tenant_id, session_id, session_version DESC, id
);

CREATE FUNCTION app.validate_session_site_context_event() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_expected_hash bytea;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM app.sessions AS tenant_session
          JOIN control.identity_sessions AS identity_session
            ON identity_session.id = tenant_session.identity_session_id
           AND identity_session.user_id = tenant_session.user_id
          JOIN app.memberships AS membership
            ON membership.tenant_id = tenant_session.tenant_id
           AND membership.user_id = tenant_session.user_id
          JOIN app.site_memberships AS site_membership
            ON site_membership.tenant_id = tenant_session.tenant_id
           AND site_membership.site_id = tenant_session.active_site_id
           AND site_membership.user_id = tenant_session.user_id
         WHERE tenant_session.tenant_id = NEW.tenant_id
           AND tenant_session.id = NEW.session_id
           AND tenant_session.user_id = NEW.user_id
           AND tenant_session.active_site_id = NEW.site_id
           AND tenant_session.session_version = NEW.session_version
           AND tenant_session.updated_at IS NOT DISTINCT FROM NEW.occurred_at
           AND identity_session.authentication_level = NEW.authentication_level
           AND identity_session.recovery_generation = NEW.recovery_generation
           AND membership.role_key = NEW.role_key
           AND membership.authorization_epoch = NEW.membership_authorization_epoch
           AND site_membership.authorization_epoch = NEW.site_authorization_epoch
    ) THEN
        RAISE EXCEPTION 'session site context event reference is invalid'
            USING ERRCODE = '23503';
    END IF;

    v_expected_hash := sha256(
        convert_to(
            'authentication_level=' || NEW.authentication_level || E'\n'
            || 'event_id=' || NEW.id::text || E'\n'
            || 'membership_authorization_epoch='
            || NEW.membership_authorization_epoch::text || E'\n'
            || 'occurred_at='
            || to_char(
                NEW.occurred_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            ) || E'\n'
            || 'previous_hash=' || COALESCE(encode(NEW.previous_hash, 'hex'), '') || E'\n'
            || 'recovery_generation=' || NEW.recovery_generation || E'\n'
            || 'role_key=' || NEW.role_key || E'\n'
            || 'session_id=' || NEW.session_id::text || E'\n'
            || 'session_version=' || NEW.session_version::text || E'\n'
            || 'site_authorization_epoch=' || NEW.site_authorization_epoch::text || E'\n'
            || 'site_id=' || NEW.site_id::text || E'\n'
            || 'tenant_id=' || NEW.tenant_id::text || E'\n'
            || 'user_id=' || NEW.user_id::text,
            'UTF8'
        )
    );
    IF NEW.event_hash IS DISTINCT FROM v_expected_hash THEN
        RAISE EXCEPTION 'session site context event hash is invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION app.validate_session_site_context_event() FROM PUBLIC;
CREATE TRIGGER session_site_context_event_reference_guard
BEFORE INSERT ON app.session_site_context_events
FOR EACH ROW EXECUTE FUNCTION app.validate_session_site_context_event();
CREATE TRIGGER session_site_context_events_immutable
BEFORE UPDATE OR DELETE ON app.session_site_context_events
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.session_site_context_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.session_site_context_events FORCE ROW LEVEL SECURITY;
CREATE POLICY session_site_context_event_read
ON app.session_site_context_events
FOR SELECT TO signal_migrator
USING (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
);
CREATE POLICY session_site_context_event_insert
ON app.session_site_context_events
FOR INSERT TO signal_migrator
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
);
REVOKE ALL ON app.session_site_context_events
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.select_session_site(
    p_session_hash bytea,
    p_current_recovery_generation text,
    p_requested_site_id uuid,
    p_expected_session_version bigint,
    p_event_id uuid
)
RETURNS TABLE (
    outcome text,
    selected_tenant_id uuid,
    selected_user_id uuid,
    selected_site_id uuid,
    selected_session_version bigint,
    selection_changed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_tenant_id uuid;
    v_session_id uuid;
    v_user_id uuid;
    v_active_site_id uuid;
    v_session_version bigint;
    v_authentication_level text;
    v_role_key text;
    v_membership_epoch bigint;
    v_site_authorization_epoch bigint;
    v_previous_hash bytea;
    v_event_hash bytea;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_requested_site_id IS NULL
       OR p_expected_session_version IS NULL OR p_expected_session_version <= 0
       OR p_event_id IS NULL
       OR p_event_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    THEN
        RAISE EXCEPTION 'invalid_session_site_selection_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.id, tenant_session.user_id,
           tenant_session.active_site_id, tenant_session.session_version,
           identity_session.authentication_level
      INTO v_tenant_id, v_session_id, v_user_id, v_active_site_id,
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
            NULL::uuid, NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    IF v_session_version <> p_expected_session_version THEN
        RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_requested_site_id::text, true);
    SELECT membership.role_key, membership.authorization_epoch,
           site_membership.authorization_epoch
      INTO v_role_key, v_membership_epoch, v_site_authorization_epoch
      FROM app.memberships AS membership
      JOIN app.site_memberships AS site_membership
        ON site_membership.tenant_id = membership.tenant_id
       AND site_membership.user_id = membership.user_id
      JOIN app.tenants AS tenant
        ON tenant.tenant_id = membership.tenant_id
      JOIN app.sites AS site
        ON site.tenant_id = site_membership.tenant_id
       AND site.id = site_membership.site_id
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.state = 'active'
       AND site_membership.site_id = p_requested_site_id
       AND site_membership.state = 'active'
       AND site_membership.permission_set =
           '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb
       AND tenant.lifecycle = 'active'
       AND site.state <> 'archived'
     FOR SHARE OF membership, site_membership, tenant, site;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'selection_denied'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::bigint, NULL::boolean;
        RETURN;
    END IF;

    IF v_active_site_id IS NOT DISTINCT FROM p_requested_site_id THEN
        RETURN QUERY SELECT 'selected'::text, v_tenant_id, v_user_id,
            p_requested_site_id, v_session_version, false;
        RETURN;
    END IF;

    -- Read the preceding event through its original site scope before changing
    -- the session. This keeps the per-session chain intact across site changes
    -- without broadening the table's forced-RLS policy.
    IF v_active_site_id IS NOT NULL THEN
        PERFORM set_config('signal.site_id', v_active_site_id::text, true);
        SELECT event.event_hash
          INTO v_previous_hash
          FROM app.session_site_context_events AS event
         WHERE event.tenant_id = v_tenant_id
           AND event.session_id = v_session_id
         ORDER BY event.session_version DESC, event.id DESC
         LIMIT 1;
    END IF;
    PERFORM set_config('signal.site_id', p_requested_site_id::text, true);

    v_session_version := v_session_version + 1;
    UPDATE app.sessions AS tenant_session
       SET active_site_id = p_requested_site_id,
           session_version = v_session_version,
           row_version = tenant_session.row_version + 1,
           last_seen_at = v_now,
           updated_at = v_now
     WHERE tenant_session.tenant_id = v_tenant_id
       AND tenant_session.id = v_session_id
       AND tenant_session.session_token_hash = p_session_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'session site selection lost its locked row'
            USING ERRCODE = '40001';
    END IF;

    v_event_hash := sha256(
        convert_to(
            'authentication_level=' || v_authentication_level || E'\n'
            || 'event_id=' || p_event_id::text || E'\n'
            || 'membership_authorization_epoch=' || v_membership_epoch::text || E'\n'
            || 'occurred_at='
            || to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            || E'\n'
            || 'previous_hash=' || COALESCE(encode(v_previous_hash, 'hex'), '') || E'\n'
            || 'recovery_generation=' || p_current_recovery_generation || E'\n'
            || 'role_key=' || v_role_key || E'\n'
            || 'session_id=' || v_session_id::text || E'\n'
            || 'session_version=' || v_session_version::text || E'\n'
            || 'site_authorization_epoch=' || v_site_authorization_epoch::text || E'\n'
            || 'site_id=' || p_requested_site_id::text || E'\n'
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
        v_tenant_id, p_event_id, p_requested_site_id, v_session_id, v_user_id,
        v_session_version, v_role_key, v_authentication_level,
        v_membership_epoch, v_site_authorization_epoch,
        p_current_recovery_generation, v_previous_hash, v_event_hash, v_now
    );

    RETURN QUERY SELECT 'selected'::text, v_tenant_id, v_user_id,
        p_requested_site_id, v_session_version, true;
END
$$;
REVOKE ALL ON FUNCTION control.select_session_site(bytea, text, uuid, bigint, uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.select_session_site(bytea, text, uuid, bigint, uuid)
TO signal_identity;

CREATE OR REPLACE FUNCTION control.resolve_snapshot_authority(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text
)
RETURNS TABLE (
    outcome text,
    tenant_id uuid,
    user_id uuid,
    role_key text,
    authentication_level text,
    membership_epoch bigint,
    site_authorization_epoch bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_tenant_id uuid;
    v_user_id uuid;
    v_active_site_id uuid;
    v_authentication_level text;
    v_role_key text;
    v_membership_epoch bigint;
    v_site_authorization_epoch bigint;
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_requested_site_id IS NULL
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    THEN
        RAISE EXCEPTION 'invalid_snapshot_authority_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.user_id,
           tenant_session.active_site_id, identity_session.authentication_level
      INTO v_tenant_id, v_user_id, v_active_site_id, v_authentication_level
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
        RETURN QUERY SELECT 'invalid_session'::text, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;

    IF v_active_site_id IS DISTINCT FROM p_requested_site_id THEN
        RETURN QUERY SELECT 'authorization_denied'::text, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_requested_site_id::text, true);
    SELECT membership.role_key, membership.authorization_epoch,
           site_membership.authorization_epoch
      INTO v_role_key, v_membership_epoch, v_site_authorization_epoch
      FROM app.memberships AS membership
      JOIN app.site_memberships AS site_membership
        ON site_membership.tenant_id = membership.tenant_id
       AND site_membership.user_id = membership.user_id
      JOIN app.tenants AS tenant
        ON tenant.tenant_id = membership.tenant_id
      JOIN app.sites AS site
        ON site.tenant_id = site_membership.tenant_id
       AND site.id = site_membership.site_id
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.state = 'active'
       AND site_membership.site_id = p_requested_site_id
       AND site_membership.state = 'active'
       AND site_membership.permission_set =
           '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb
       AND tenant.lifecycle = 'active'
       AND site.state != 'archived'
     FOR KEY SHARE OF membership, site_membership, tenant, site;

    IF NOT FOUND THEN
        RETURN QUERY SELECT 'authorization_denied'::text, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;

    RETURN QUERY SELECT 'authorized'::text, v_tenant_id, v_user_id, v_role_key,
                        v_authentication_level, v_membership_epoch,
                        v_site_authorization_epoch;
END;
$$;
