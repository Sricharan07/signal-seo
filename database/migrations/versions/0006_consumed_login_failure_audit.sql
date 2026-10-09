ALTER TABLE control.platform_events
    ALTER COLUMN actor_user_id DROP NOT NULL,
    DROP CONSTRAINT platform_events_event_type_check,
    DROP CONSTRAINT platform_events_object_kind_check,
    DROP CONSTRAINT platform_events_facts_check,
    DROP CONSTRAINT platform_events_reason_check;

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
