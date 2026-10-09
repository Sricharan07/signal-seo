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
            'session_persistence_failed',
            'invitation_identity_not_verified',
            'invitation_proof_persistence_failed'
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
