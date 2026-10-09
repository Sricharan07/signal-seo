CREATE FUNCTION control.current_identity_session_hash() RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN current_setting('signal.identity_session_hash', true) ~ '^[0-9a-f]{64}$'
        THEN decode(current_setting('signal.identity_session_hash', true), 'hex')
        ELSE NULL
    END
$$;
REVOKE ALL ON FUNCTION control.current_identity_session_hash() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.current_identity_session_hash() TO signal_identity;

ALTER TABLE control.identity_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.identity_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY identity_session_scope ON control.identity_sessions
USING (
    CURRENT_USER != 'signal_identity'
    OR token_hash = control.current_identity_session_hash()
    OR EXISTS (
        SELECT 1
        FROM app.sessions tenant_session
        WHERE tenant_session.identity_session_id = identity_sessions.id
          AND tenant_session.user_id = identity_sessions.user_id
          AND tenant_session.session_token_hash = app.current_session_hash()
    )
)
WITH CHECK (
    CURRENT_USER != 'signal_identity'
    OR token_hash = control.current_identity_session_hash()
);

GRANT INSERT (
    id,
    user_id,
    token_hash,
    auth_time,
    authentication_level,
    recovery_generation,
    expires_at,
    last_seen_at
) ON control.identity_sessions TO signal_identity;

GRANT INSERT (
    tenant_id,
    id,
    identity_session_id,
    user_id,
    session_token_hash,
    auth_time,
    mfa_level,
    expires_at,
    last_seen_at
) ON app.sessions TO signal_identity;

DROP POLICY session_scope ON app.sessions;
CREATE POLICY session_read_scope ON app.sessions
FOR SELECT
USING (
    (
        CURRENT_USER = 'signal_identity'
        AND session_token_hash = app.current_session_hash()
    )
    OR (
        CURRENT_USER != 'signal_identity'
        AND tenant_id = app.current_tenant_id()
    )
);
CREATE POLICY session_insert_scope ON app.sessions
FOR INSERT
WITH CHECK (tenant_id = app.current_tenant_id());
