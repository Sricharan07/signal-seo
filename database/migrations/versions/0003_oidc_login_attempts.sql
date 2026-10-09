CREATE FUNCTION control.current_oidc_state_hash() RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN current_setting('signal.oidc_state_hash', true) ~ '^[0-9a-f]{64}$'
        THEN decode(current_setting('signal.oidc_state_hash', true), 'hex')
        ELSE NULL
    END
$$;
GRANT EXECUTE ON FUNCTION control.current_oidc_state_hash() TO signal_identity;

CREATE FUNCTION control.current_oidc_browser_binding_hash() RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN current_setting('signal.oidc_browser_binding_hash', true) ~ '^[0-9a-f]{64}$'
        THEN decode(current_setting('signal.oidc_browser_binding_hash', true), 'hex')
        ELSE NULL
    END
$$;
GRANT EXECUTE ON FUNCTION control.current_oidc_browser_binding_hash() TO signal_identity;

CREATE TABLE control.oidc_login_attempts (
    id uuid PRIMARY KEY,
    state_hash bytea NOT NULL UNIQUE CHECK (octet_length(state_hash) = 32),
    nonce_hash bytea NOT NULL CHECK (octet_length(nonce_hash) = 32),
    browser_binding_hash bytea NOT NULL CHECK (octet_length(browser_binding_hash) = 32),
    oidc_issuer text NOT NULL CHECK (length(oidc_issuer) BETWEEN 1 AND 2048),
    client_id text NOT NULL CHECK (length(client_id) BETWEEN 1 AND 128),
    redirect_uri text NOT NULL CHECK (length(redirect_uri) BETWEEN 1 AND 2048),
    pkce_secret_reference text NOT NULL CHECK (
        length(pkce_secret_reference) BETWEEN 10 AND 512
    ),
    return_path text NOT NULL CHECK (length(return_path) BETWEEN 1 AND 1024),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK (consumed_at IS NULL OR consumed_at BETWEEN created_at AND expires_at)
);
CREATE INDEX oidc_login_attempts_expiry
ON control.oidc_login_attempts (expires_at) WHERE consumed_at IS NULL;

CREATE FUNCTION control.guard_oidc_login_attempt_update() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.consumed_at IS NOT NULL
       OR NEW.consumed_at IS NULL
       OR NEW.consumed_at < OLD.created_at
       OR NEW.consumed_at > OLD.expires_at
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.state_hash IS DISTINCT FROM OLD.state_hash
       OR NEW.nonce_hash IS DISTINCT FROM OLD.nonce_hash
       OR NEW.browser_binding_hash IS DISTINCT FROM OLD.browser_binding_hash
       OR NEW.oidc_issuer IS DISTINCT FROM OLD.oidc_issuer
       OR NEW.client_id IS DISTINCT FROM OLD.client_id
       OR NEW.redirect_uri IS DISTINCT FROM OLD.redirect_uri
       OR NEW.pkce_secret_reference IS DISTINCT FROM OLD.pkce_secret_reference
       OR NEW.return_path IS DISTINCT FROM OLD.return_path
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'oidc login attempt is immutable except for first consumption'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER oidc_login_attempt_update_guard
BEFORE UPDATE ON control.oidc_login_attempts
FOR EACH ROW EXECUTE FUNCTION control.guard_oidc_login_attempt_update();

ALTER TABLE control.oidc_login_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.oidc_login_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY oidc_login_attempt_state_scope ON control.oidc_login_attempts
USING (
    state_hash = control.current_oidc_state_hash()
    AND browser_binding_hash = control.current_oidc_browser_binding_hash()
)
WITH CHECK (
    state_hash = control.current_oidc_state_hash()
    AND browser_binding_hash = control.current_oidc_browser_binding_hash()
);

GRANT SELECT, INSERT ON control.oidc_login_attempts TO signal_identity;
GRANT UPDATE (consumed_at) ON control.oidc_login_attempts TO signal_identity;
