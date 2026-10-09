ALTER TABLE control.oidc_login_attempts
ADD COLUMN purpose text NOT NULL DEFAULT 'login'
CHECK (purpose IN ('login', 'invitation_acceptance'));

CREATE OR REPLACE FUNCTION control.guard_oidc_login_attempt_update() RETURNS trigger
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
       OR NEW.purpose IS DISTINCT FROM OLD.purpose
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'oidc login attempt is immutable except for first consumption'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION control.current_invitation_identity_proof_hash() RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN current_setting('signal.invitation_identity_proof_hash', true) ~ '^[0-9a-f]{64}$'
        THEN decode(current_setting('signal.invitation_identity_proof_hash', true), 'hex')
        ELSE NULL
    END
$$;
REVOKE ALL ON FUNCTION control.current_invitation_identity_proof_hash() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.current_invitation_identity_proof_hash() TO signal_identity;

CREATE TABLE control.invitation_identity_proofs (
    id uuid PRIMARY KEY,
    token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    oidc_issuer text NOT NULL CHECK (
        length(oidc_issuer) BETWEEN 1 AND 2048
        AND oidc_issuer !~ '[[:cntrl:]]'
    ),
    oidc_subject text NOT NULL CHECK (
        length(oidc_subject) BETWEEN 1 AND 512
        AND oidc_subject !~ '[[:cntrl:]]'
    ),
    verified_email text NOT NULL CHECK (
        length(verified_email) BETWEEN 3 AND 320
        AND verified_email = lower(verified_email)
        AND verified_email !~ '[[:cntrl:]]'
    ),
    identity_issued_at timestamptz NOT NULL,
    identity_expires_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz CHECK (consumed_at IS NULL),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CHECK (identity_issued_at <= created_at + interval '30 seconds'),
    CHECK (identity_issued_at >= created_at - interval '11 minutes'),
    CHECK (identity_expires_at > created_at),
    CHECK (expires_at > created_at),
    CHECK (expires_at <= identity_expires_at),
    CHECK (expires_at <= created_at + interval '10 minutes')
);
CREATE INDEX invitation_identity_proofs_expiry
ON control.invitation_identity_proofs (expires_at) WHERE consumed_at IS NULL;

CREATE TRIGGER invitation_identity_proofs_immutable
BEFORE UPDATE OR DELETE ON control.invitation_identity_proofs
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE control.invitation_identity_proofs ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.invitation_identity_proofs FORCE ROW LEVEL SECURITY;
CREATE POLICY invitation_identity_proof_scope ON control.invitation_identity_proofs
USING (token_hash = control.current_invitation_identity_proof_hash())
WITH CHECK (token_hash = control.current_invitation_identity_proof_hash());

GRANT SELECT ON control.invitation_identity_proofs TO signal_identity;
GRANT INSERT (
    id,
    token_hash,
    oidc_issuer,
    oidc_subject,
    verified_email,
    identity_issued_at,
    identity_expires_at,
    expires_at
) ON control.invitation_identity_proofs TO signal_identity;
