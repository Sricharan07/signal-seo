ALTER TABLE control.platform_events
DROP CONSTRAINT platform_events_contract_check;

ALTER TABLE control.platform_events
ADD CONSTRAINT platform_events_contract_check CHECK (
    (
        event_type = 'identity.session.issued'
        AND actor_user_id IS NOT NULL
        AND object_kind = 'identity_session'
        AND reason IS NULL
        AND jsonb_typeof(facts) = 'object'
        AND facts - 'schema_version' - 'authentication_level' = '{}'::jsonb
        AND facts -> 'schema_version' = '1'::jsonb
        AND jsonb_typeof(facts -> 'authentication_level') = 'string'
        AND facts ->> 'authentication_level' IN ('primary', 'mfa')
    )
    OR
    (
        event_type = 'identity.login.failed'
        AND actor_user_id IS NULL
        AND object_kind = 'oidc_login_attempt'
        AND reason IN (
            'provider_configuration_failed',
            'pkce_unavailable',
            'pkce_consume_failed',
            'provider_assertion_failed',
            'identity_not_authorized',
            'session_persistence_failed'
        )
        AND facts = '{"schema_version":1}'::jsonb
    )
    OR
    (
        event_type = 'identity.session.revoked'
        AND actor_user_id IS NOT NULL
        AND object_kind = 'identity_session'
        AND reason = 'user_logout'
        AND jsonb_typeof(facts) = 'object'
        AND facts - 'schema_version' - 'presented_session_kind' = '{}'::jsonb
        AND facts -> 'schema_version' = '1'::jsonb
        AND jsonb_typeof(facts -> 'presented_session_kind') = 'string'
        AND facts ->> 'presented_session_kind' IN ('identity', 'tenant')
    )
);

CREATE OR REPLACE FUNCTION control.validate_platform_event_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.event_type = 'identity.session.issued' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM control.identity_sessions identity_session
            WHERE identity_session.id = NEW.object_id
              AND identity_session.user_id = NEW.actor_user_id
        ) THEN
            RAISE EXCEPTION 'platform event object reference is invalid'
                USING ERRCODE = '23503';
        END IF;
    ELSIF NEW.event_type = 'identity.session.revoked' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM control.identity_sessions identity_session
            WHERE identity_session.id = NEW.object_id
              AND identity_session.user_id = NEW.actor_user_id
              AND identity_session.revoked_at IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'platform event object reference is invalid'
                USING ERRCODE = '23503';
        END IF;
    ELSIF NEW.event_type = 'identity.login.failed' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM control.oidc_login_attempts login_attempt
            WHERE login_attempt.id = NEW.object_id
              AND login_attempt.consumed_at IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'platform event object reference is invalid'
                USING ERRCODE = '23503';
        END IF;
    ELSE
        RAISE EXCEPTION 'platform event object reference is invalid'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END
$$;

DROP POLICY platform_identity_event_insert ON control.platform_events;
CREATE POLICY platform_identity_event_insert ON control.platform_events
FOR INSERT TO signal_identity
WITH CHECK (
    (
        event_type = 'identity.session.issued'
        AND EXISTS (
            SELECT 1
            FROM control.identity_sessions identity_session
            WHERE identity_session.id = platform_events.object_id
              AND identity_session.user_id = platform_events.actor_user_id
              AND identity_session.token_hash = control.current_identity_session_hash()
        )
    )
    OR
    (
        event_type = 'identity.login.failed'
        AND actor_user_id IS NULL
        AND EXISTS (
            SELECT 1
            FROM control.oidc_login_attempts login_attempt
            WHERE login_attempt.id = platform_events.object_id
              AND login_attempt.consumed_at IS NOT NULL
              AND login_attempt.state_hash = control.current_oidc_state_hash()
              AND login_attempt.browser_binding_hash =
                  control.current_oidc_browser_binding_hash()
        )
    )
);
CREATE POLICY platform_revocation_event_insert ON control.platform_events
FOR INSERT TO signal_migrator
WITH CHECK (
    event_type = 'identity.session.revoked'
    AND EXISTS (
        SELECT 1
        FROM control.identity_sessions identity_session
        WHERE identity_session.id = platform_events.object_id
          AND identity_session.user_id = platform_events.actor_user_id
          AND identity_session.revoked_at IS NOT NULL
          AND identity_session.token_hash = control.current_identity_session_hash()
    )
);

DROP POLICY session_read_scope ON app.sessions;
CREATE POLICY session_read_scope ON app.sessions
FOR SELECT
USING (
    (
        CURRENT_USER IN ('signal_identity', 'signal_migrator')
        AND session_token_hash = app.current_session_hash()
    )
    OR (
        CURRENT_USER != 'signal_identity'
        AND tenant_id = app.current_tenant_id()
    )
);
CREATE POLICY session_exact_revoke_scope ON app.sessions
FOR UPDATE TO signal_identity, signal_migrator
USING (session_token_hash = app.current_session_hash())
WITH CHECK (session_token_hash = app.current_session_hash());

CREATE FUNCTION control.revoke_browser_session(
    p_token_hash bytea,
    p_presented_session_kind text,
    p_event_id uuid
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE
    v_identity_session_id uuid;
    v_user_id uuid;
    v_identity_token_hash bytea;
    v_identity_revoked_at timestamptz;
    v_tenant_id uuid;
BEGIN
    IF p_token_hash IS NULL
       OR octet_length(p_token_hash) != 32
       OR p_presented_session_kind IS NULL
       OR p_presented_session_kind NOT IN ('identity', 'tenant')
       OR p_event_id IS NULL
       OR substring(p_event_id::text from 15 for 1) != '4'
       OR substring(p_event_id::text from 20 for 1) NOT IN ('8', '9', 'a', 'b')
    THEN
        RETURN false;
    END IF;

    IF p_presented_session_kind = 'identity' THEN
        PERFORM set_config('signal.identity_session_hash', encode(p_token_hash, 'hex'), true);
        SELECT identity_session.id,
               identity_session.user_id,
               identity_session.token_hash,
               identity_session.revoked_at
        INTO v_identity_session_id,
             v_user_id,
             v_identity_token_hash,
             v_identity_revoked_at
        FROM control.identity_sessions identity_session
        WHERE identity_session.token_hash = p_token_hash
        FOR UPDATE;
    ELSE
        PERFORM set_config('signal.session_hash', encode(p_token_hash, 'hex'), true);
        SELECT tenant_session.tenant_id, tenant_session.identity_session_id
        INTO v_tenant_id, v_identity_session_id
        FROM app.sessions tenant_session
        WHERE tenant_session.session_token_hash = p_token_hash
        FOR UPDATE;
        IF NOT FOUND THEN
            RETURN false;
        END IF;

        SELECT identity_session.user_id,
               identity_session.token_hash,
               identity_session.revoked_at
        INTO v_user_id, v_identity_token_hash, v_identity_revoked_at
        FROM control.identity_sessions identity_session
        WHERE identity_session.id = v_identity_session_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RETURN false;
        END IF;
        PERFORM set_config(
            'signal.identity_session_hash',
            encode(v_identity_token_hash, 'hex'),
            true
        );
    END IF;

    IF v_identity_session_id IS NULL OR v_identity_revoked_at IS NOT NULL THEN
        RETURN false;
    END IF;

    UPDATE control.identity_sessions identity_session
    SET revoked_at = transaction_timestamp()
    WHERE identity_session.id = v_identity_session_id;

    IF p_presented_session_kind = 'tenant' THEN
        PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
        UPDATE app.sessions tenant_session
        SET revoked_at = COALESCE(tenant_session.revoked_at, transaction_timestamp()),
            updated_at = transaction_timestamp()
        WHERE tenant_session.session_token_hash = p_token_hash;
    END IF;

    INSERT INTO control.platform_events (
        id,
        event_type,
        actor_user_id,
        object_kind,
        object_id,
        facts,
        reason
    ) VALUES (
        p_event_id,
        'identity.session.revoked',
        v_user_id,
        'identity_session',
        v_identity_session_id,
        jsonb_build_object(
            'schema_version', 1,
            'presented_session_kind', p_presented_session_kind
        ),
        'user_logout'
    );
    RETURN true;
END
$$;
REVOKE ALL ON FUNCTION control.revoke_browser_session(bytea, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.revoke_browser_session(bytea, text, uuid) TO signal_identity;
