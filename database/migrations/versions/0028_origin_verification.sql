ALTER TABLE app.sites
    DROP CONSTRAINT sites_ownership_status_check,
    ADD CONSTRAINT sites_ownership_status_check CHECK (
        ownership_status IN ('unverified', 'verified', 'reverification_required')
    );

CREATE TABLE app.site_origin_challenges (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    session_id uuid NOT NULL,
    proof_method text NOT NULL CHECK (proof_method = 'http_well_known'),
    origin text NOT NULL CHECK (
        length(origin) BETWEEN 9 AND 2048
        AND origin ~ '^https://[^/?#[:space:]]+$'
    ),
    proof_path text NOT NULL CHECK (
        proof_path = '/.well-known/signal-site-verification.txt'
    ),
    proof_sha256 bytea NOT NULL CHECK (octet_length(proof_sha256) = 32),
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    role_key text NOT NULL CHECK (role_key = 'owner'),
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
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (
        expires_at = issued_at + interval '30 minutes'
    ),
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT site_origin_challenges_request_key
        UNIQUE (tenant_id, user_id, idempotency_key),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, session_id) REFERENCES app.sessions (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX site_origin_challenges_current
ON app.site_origin_challenges (tenant_id, site_id, expires_at DESC, id);

CREATE TABLE app.site_origin_verification_attempts (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    challenge_id uuid NOT NULL,
    user_id uuid NOT NULL,
    session_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    origin text NOT NULL CHECK (
        length(origin) BETWEEN 9 AND 2048
        AND origin ~ '^https://[^/?#[:space:]]+$'
    ),
    proof_url text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN (
        'verified', 'proof_not_found', 'proof_mismatch', 'policy_rejected',
        'transport_unavailable', 'invalid_response', 'claim_conflict'
    )),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    media_type text CHECK (length(media_type) BETWEEN 1 AND 100),
    response_sha256 bytea CHECK (octet_length(response_sha256) = 32),
    observed_final_url text CHECK (length(observed_final_url) BETWEEN 1 AND 2048),
    resolved_address text CHECK (length(resolved_address) BETWEEN 1 AND 64),
    elapsed_ms integer CHECK (elapsed_ms BETWEEN 0 AND 2147483647),
    role_key text NOT NULL CHECK (role_key = 'owner'),
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
    attempted_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT site_origin_verification_attempts_request_key
        UNIQUE (tenant_id, user_id, idempotency_key),
    FOREIGN KEY (tenant_id, site_id, challenge_id)
        REFERENCES app.site_origin_challenges (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, session_id) REFERENCES app.sessions (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX site_origin_verification_attempts_challenge
ON app.site_origin_verification_attempts (
    tenant_id, site_id, challenge_id, attempted_at DESC, id
);

CREATE TABLE app.site_origin_verifications (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    challenge_id uuid NOT NULL,
    origin text NOT NULL,
    proof_method text NOT NULL CHECK (proof_method = 'http_well_known'),
    resource_identity text NOT NULL,
    permitted_origins jsonb NOT NULL,
    revocation_conditions jsonb NOT NULL CHECK (
        revocation_conditions =
        '["origin_changed","ownership_changed","claim_revoked","recheck_expired"]'::jsonb
    ),
    verified_at timestamptz NOT NULL,
    recheck_at timestamptz NOT NULL CHECK (
        recheck_at = verified_at + interval '30 days'
    ),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, challenge_id),
    FOREIGN KEY (tenant_id, site_id, id)
        REFERENCES app.site_origin_verification_attempts (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, challenge_id)
        REFERENCES app.site_origin_challenges (tenant_id, site_id, id),
    CHECK (resource_identity = origin),
    CHECK (permitted_origins = jsonb_build_array(origin))
);
CREATE INDEX site_origin_verifications_current
ON app.site_origin_verifications (tenant_id, site_id, recheck_at DESC, id);

CREATE TABLE control.public_origin_claims (
    origin text PRIMARY KEY,
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    verification_id uuid NOT NULL,
    proof_method text NOT NULL CHECK (proof_method = 'http_well_known'),
    verified_at timestamptz NOT NULL,
    recheck_at timestamptz NOT NULL,
    claim_generation bigint NOT NULL CHECK (claim_generation > 0),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, verification_id)
        REFERENCES app.site_origin_verifications (tenant_id, site_id, id)
        DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE app.site_origin_challenges ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.site_origin_challenges FORCE ROW LEVEL SECURITY;
CREATE POLICY site_origin_challenge_scope ON app.site_origin_challenges
USING (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
)
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
);

ALTER TABLE app.site_origin_verification_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.site_origin_verification_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY site_origin_attempt_scope ON app.site_origin_verification_attempts
USING (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
)
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
);

ALTER TABLE app.site_origin_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.site_origin_verifications FORCE ROW LEVEL SECURITY;
CREATE POLICY site_origin_verification_scope ON app.site_origin_verifications
USING (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
)
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
);

CREATE TRIGGER site_origin_challenges_immutable
BEFORE UPDATE OR DELETE ON app.site_origin_challenges
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER site_origin_verification_attempts_immutable
BEFORE UPDATE OR DELETE ON app.site_origin_verification_attempts
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER site_origin_verifications_immutable
BEFORE UPDATE OR DELETE ON app.site_origin_verifications
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

REVOKE ALL ON app.site_origin_challenges,
    app.site_origin_verification_attempts,
    app.site_origin_verifications,
    control.public_origin_claims
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.issue_site_origin_challenge(
    p_session_hash bytea,
    p_current_recovery_generation text,
    p_site_id uuid,
    p_challenge_id uuid,
    p_idempotency_key uuid,
    p_request_hash bytea,
    p_origin text,
    p_ttl_seconds integer,
    p_open_challenge_limit integer
)
RETURNS TABLE (
    outcome text,
    challenge_tenant_id uuid,
    challenge_user_id uuid,
    challenge_site_id uuid,
    challenge_id uuid,
    challenge_origin text,
    issued_at timestamptz,
    expires_at timestamptz,
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
    v_authentication_level text;
    v_membership_epoch bigint;
    v_site_epoch bigint;
    v_existing record;
    v_expected_hash bytea;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_site_id IS NULL OR p_site_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_challenge_id IS NULL OR p_challenge_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_idempotency_key IS NULL OR p_idempotency_key::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_origin IS NULL OR length(p_origin) NOT BETWEEN 9 AND 2048
       OR p_origin !~ '^https://[^/?#[:space:]]+$'
       OR p_ttl_seconds IS DISTINCT FROM 1800
       OR p_open_challenge_limit IS DISTINCT FROM 10
    THEN
        RAISE EXCEPTION 'invalid_origin_challenge_input' USING ERRCODE = '22023';
    END IF;
    v_expected_hash := sha256(convert_to(
        'origin=' || p_origin || E'\nsite_id=' || p_site_id::text,
        'UTF8'
    ));
    IF p_request_hash IS DISTINCT FROM v_expected_hash THEN
        RAISE EXCEPTION 'invalid_origin_challenge_hash' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.id, tenant_session.user_id,
           identity_session.authentication_level
      INTO v_tenant_id, v_session_id, v_user_id, v_authentication_level
      FROM app.sessions AS tenant_session
      JOIN control.identity_sessions AS identity_session
        ON identity_session.id = tenant_session.identity_session_id
       AND identity_session.user_id = tenant_session.user_id
      JOIN control.users AS identity_user
        ON identity_user.id = tenant_session.user_id
     WHERE tenant_session.session_token_hash = p_session_hash
       AND tenant_session.active_site_id = p_site_id
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
            NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
            NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT membership.authorization_epoch, site_membership.authorization_epoch
      INTO v_membership_epoch, v_site_epoch
      FROM app.memberships AS membership
      JOIN app.tenants AS tenant
        ON tenant.tenant_id = membership.tenant_id
      JOIN app.site_memberships AS site_membership
        ON site_membership.tenant_id = membership.tenant_id
       AND site_membership.user_id = membership.user_id
       AND site_membership.site_id = p_site_id
      JOIN app.sites AS site
        ON site.tenant_id = site_membership.tenant_id
       AND site.id = site_membership.site_id
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.role_key = 'owner'
       AND membership.state = 'active'
       AND tenant.lifecycle = 'active'
       AND site_membership.state = 'active'
       AND site.state <> 'archived'
       AND site.primary_origin = p_origin
       AND site.ownership_status IN (
           'unverified', 'verified', 'reverification_required'
       )
     FOR UPDATE OF membership, tenant, site_membership, site;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'denied'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
            NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;

    SELECT challenge.id, challenge.site_id, challenge.origin,
           challenge.issued_at, challenge.expires_at, challenge.request_hash
      INTO v_existing
      FROM app.site_origin_challenges AS challenge
     WHERE challenge.tenant_id = v_tenant_id
       AND challenge.user_id = v_user_id
       AND challenge.idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.request_hash IS DISTINCT FROM p_request_hash THEN
            RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                NULL::timestamptz, NULL::boolean;
        ELSE
            RETURN QUERY SELECT 'issued'::text, v_tenant_id, v_user_id,
                v_existing.site_id, v_existing.id, v_existing.origin,
                v_existing.issued_at, v_existing.expires_at, true;
        END IF;
        RETURN;
    END IF;

    IF (
        SELECT count(*)
          FROM app.site_origin_challenges AS challenge
         WHERE challenge.tenant_id = v_tenant_id
           AND challenge.site_id = p_site_id
           AND challenge.expires_at > v_now
    ) >= p_open_challenge_limit THEN
        RETURN QUERY SELECT 'limit_reached'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
            NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;

    INSERT INTO app.site_origin_challenges (
        tenant_id, site_id, id, user_id, session_id, proof_method, origin,
        proof_path, proof_sha256, idempotency_key, request_hash, role_key,
        authentication_level, membership_authorization_epoch,
        site_authorization_epoch, recovery_generation, issued_at, expires_at
    ) VALUES (
        v_tenant_id, p_site_id, p_challenge_id, v_user_id, v_session_id,
        'http_well_known', p_origin,
        '/.well-known/signal-site-verification.txt',
        sha256(convert_to(
            'signal-site-verification=' || p_challenge_id::text || E'\n',
            'UTF8'
        )),
        p_idempotency_key, p_request_hash, 'owner', v_authentication_level,
        v_membership_epoch, v_site_epoch, p_current_recovery_generation,
        v_now, v_now + make_interval(secs => p_ttl_seconds)
    );
    RETURN QUERY SELECT 'issued'::text, v_tenant_id, v_user_id,
        p_site_id, p_challenge_id, p_origin, v_now,
        v_now + make_interval(secs => p_ttl_seconds), false;
END
$$;

CREATE FUNCTION control.prepare_site_origin_verification(
    p_session_hash bytea,
    p_current_recovery_generation text,
    p_site_id uuid,
    p_challenge_id uuid,
    p_idempotency_key uuid,
    p_request_hash bytea,
    p_origin text,
    p_attempt_limit integer
)
RETURNS TABLE (
    outcome text,
    verification_tenant_id uuid,
    verification_user_id uuid,
    verification_site_id uuid,
    verification_challenge_id uuid,
    verification_origin text,
    proof_sha256 bytea,
    expires_at timestamptz,
    verified_at timestamptz,
    recheck_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_tenant_id uuid;
    v_session_id uuid;
    v_user_id uuid;
    v_existing record;
    v_challenge record;
    v_expected_hash bytea;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_site_id IS NULL OR p_site_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_challenge_id IS NULL OR p_challenge_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_idempotency_key IS NULL OR p_idempotency_key::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_origin IS NULL OR length(p_origin) NOT BETWEEN 9 AND 2048
       OR p_origin !~ '^https://[^/?#[:space:]]+$'
       OR p_attempt_limit IS DISTINCT FROM 10
    THEN
        RAISE EXCEPTION 'invalid_origin_verification_input' USING ERRCODE = '22023';
    END IF;
    v_expected_hash := sha256(convert_to(
        'challenge_id=' || p_challenge_id::text || E'\norigin=' || p_origin
        || E'\nsite_id=' || p_site_id::text,
        'UTF8'
    ));
    IF p_request_hash IS DISTINCT FROM v_expected_hash THEN
        RAISE EXCEPTION 'invalid_origin_verification_hash' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.id, tenant_session.user_id
      INTO v_tenant_id, v_session_id, v_user_id
      FROM app.sessions AS tenant_session
      JOIN control.identity_sessions AS identity_session
        ON identity_session.id = tenant_session.identity_session_id
       AND identity_session.user_id = tenant_session.user_id
      JOIN control.users AS identity_user ON identity_user.id = tenant_session.user_id
     WHERE tenant_session.session_token_hash = p_session_hash
       AND tenant_session.active_site_id = p_site_id
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
            NULL::uuid, NULL::uuid, NULL::text, NULL::bytea,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    PERFORM 1
      FROM app.memberships AS membership
      JOIN app.tenants AS tenant ON tenant.tenant_id = membership.tenant_id
      JOIN app.site_memberships AS site_membership
        ON site_membership.tenant_id = membership.tenant_id
       AND site_membership.user_id = membership.user_id
       AND site_membership.site_id = p_site_id
      JOIN app.sites AS site
        ON site.tenant_id = site_membership.tenant_id
       AND site.id = site_membership.site_id
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.role_key = 'owner'
       AND membership.state = 'active'
       AND tenant.lifecycle = 'active'
       AND site_membership.state = 'active'
       AND site.state <> 'archived'
       AND site.primary_origin = p_origin
     FOR UPDATE OF membership, tenant, site_membership, site;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'denied'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::bytea,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz;
        RETURN;
    END IF;

    SELECT attempt.outcome, attempt.request_hash,
           verification.verified_at, verification.recheck_at
      INTO v_existing
      FROM app.site_origin_verification_attempts AS attempt
      LEFT JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = attempt.tenant_id
       AND verification.site_id = attempt.site_id
       AND verification.id = attempt.id
     WHERE attempt.tenant_id = v_tenant_id
       AND attempt.user_id = v_user_id
       AND attempt.idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.request_hash IS DISTINCT FROM p_request_hash THEN
            RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid, NULL::text, NULL::bytea,
                NULL::timestamptz, NULL::timestamptz, NULL::timestamptz;
        ELSE
            RETURN QUERY SELECT v_existing.outcome, v_tenant_id, v_user_id,
                p_site_id, p_challenge_id, p_origin, NULL::bytea,
                NULL::timestamptz, v_existing.verified_at, v_existing.recheck_at;
        END IF;
        RETURN;
    END IF;

    SELECT challenge.origin, challenge.proof_sha256, challenge.expires_at
      INTO v_challenge
      FROM app.site_origin_challenges AS challenge
     WHERE challenge.tenant_id = v_tenant_id
       AND challenge.site_id = p_site_id
       AND challenge.id = p_challenge_id
       AND challenge.origin = p_origin;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'challenge_not_found'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::bytea,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz;
        RETURN;
    END IF;
    IF v_challenge.expires_at <= v_now THEN
        RETURN QUERY SELECT 'challenge_expired'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::bytea,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz;
        RETURN;
    END IF;

    SELECT verification.verified_at, verification.recheck_at
      INTO v_existing
      FROM app.site_origin_verifications AS verification
     WHERE verification.tenant_id = v_tenant_id
       AND verification.site_id = p_site_id
       AND verification.challenge_id = p_challenge_id;
    IF FOUND THEN
        RETURN QUERY SELECT 'verified'::text, v_tenant_id, v_user_id,
            p_site_id, p_challenge_id, p_origin, NULL::bytea,
            v_challenge.expires_at, v_existing.verified_at, v_existing.recheck_at;
        RETURN;
    END IF;

    IF (
        SELECT count(*)
          FROM app.site_origin_verification_attempts AS attempt
         WHERE attempt.tenant_id = v_tenant_id
           AND attempt.site_id = p_site_id
           AND attempt.challenge_id = p_challenge_id
    ) >= p_attempt_limit THEN
        RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::bytea,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'prepared'::text, v_tenant_id, v_user_id,
        p_site_id, p_challenge_id, p_origin, v_challenge.proof_sha256,
        v_challenge.expires_at, NULL::timestamptz, NULL::timestamptz;
END
$$;

CREATE FUNCTION control.record_site_origin_verification(
    p_session_hash bytea,
    p_current_recovery_generation text,
    p_site_id uuid,
    p_challenge_id uuid,
    p_attempt_id uuid,
    p_idempotency_key uuid,
    p_request_hash bytea,
    p_origin text,
    p_observation_outcome text,
    p_http_status integer,
    p_media_type text,
    p_response_sha256 bytea,
    p_observed_final_url text,
    p_resolved_address text,
    p_elapsed_ms integer,
    p_recheck_seconds integer
)
RETURNS TABLE (
    outcome text,
    verification_tenant_id uuid,
    verification_user_id uuid,
    verification_site_id uuid,
    verification_challenge_id uuid,
    verification_origin text,
    proof_method text,
    verified_at timestamptz,
    recheck_at timestamptz,
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
    v_authentication_level text;
    v_membership_epoch bigint;
    v_site_epoch bigint;
    v_existing record;
    v_challenge record;
    v_claim record;
    v_final_outcome text := p_observation_outcome;
    v_expected_hash bytea;
    v_now timestamptz := transaction_timestamp();
    v_recheck_at timestamptz;
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_site_id IS NULL OR p_site_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_challenge_id IS NULL OR p_challenge_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_attempt_id IS NULL OR p_attempt_id::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_idempotency_key IS NULL OR p_idempotency_key::text !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_origin IS NULL OR length(p_origin) NOT BETWEEN 9 AND 2048
       OR p_origin !~ '^https://[^/?#[:space:]]+$'
       OR p_observation_outcome IS NULL
       OR p_observation_outcome NOT IN (
           'matched', 'proof_not_found', 'proof_mismatch', 'policy_rejected',
           'transport_unavailable', 'invalid_response'
       )
       OR (p_http_status IS NOT NULL AND p_http_status NOT BETWEEN 100 AND 599)
       OR (p_media_type IS NOT NULL AND length(p_media_type) NOT BETWEEN 1 AND 100)
       OR (p_response_sha256 IS NOT NULL AND octet_length(p_response_sha256) <> 32)
       OR (p_observed_final_url IS NOT NULL
           AND length(p_observed_final_url) NOT BETWEEN 1 AND 2048)
       OR (p_resolved_address IS NOT NULL
           AND length(p_resolved_address) NOT BETWEEN 1 AND 64)
       OR (p_elapsed_ms IS NOT NULL AND p_elapsed_ms NOT BETWEEN 0 AND 2147483647)
       OR (p_observation_outcome IN ('policy_rejected', 'transport_unavailable')
           AND (p_http_status IS NOT NULL OR p_media_type IS NOT NULL
                OR p_response_sha256 IS NOT NULL
                OR p_observed_final_url IS NOT NULL
                OR p_resolved_address IS NOT NULL OR p_elapsed_ms IS NOT NULL))
       OR p_recheck_seconds IS DISTINCT FROM 2592000
    THEN
        RAISE EXCEPTION 'invalid_origin_observation_input' USING ERRCODE = '22023';
    END IF;
    v_expected_hash := sha256(convert_to(
        'challenge_id=' || p_challenge_id::text || E'\norigin=' || p_origin
        || E'\nsite_id=' || p_site_id::text,
        'UTF8'
    ));
    IF p_request_hash IS DISTINCT FROM v_expected_hash THEN
        RAISE EXCEPTION 'invalid_origin_verification_hash' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.id, tenant_session.user_id,
           identity_session.authentication_level
      INTO v_tenant_id, v_session_id, v_user_id, v_authentication_level
      FROM app.sessions AS tenant_session
      JOIN control.identity_sessions AS identity_session
        ON identity_session.id = tenant_session.identity_session_id
       AND identity_session.user_id = tenant_session.user_id
      JOIN control.users AS identity_user ON identity_user.id = tenant_session.user_id
     WHERE tenant_session.session_token_hash = p_session_hash
       AND tenant_session.active_site_id = p_site_id
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
            NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT membership.authorization_epoch, site_membership.authorization_epoch
      INTO v_membership_epoch, v_site_epoch
      FROM app.memberships AS membership
      JOIN app.tenants AS tenant ON tenant.tenant_id = membership.tenant_id
      JOIN app.site_memberships AS site_membership
        ON site_membership.tenant_id = membership.tenant_id
       AND site_membership.user_id = membership.user_id
       AND site_membership.site_id = p_site_id
      JOIN app.sites AS site
        ON site.tenant_id = site_membership.tenant_id
       AND site.id = site_membership.site_id
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.role_key = 'owner'
       AND membership.state = 'active'
       AND tenant.lifecycle = 'active'
       AND site_membership.state = 'active'
       AND site.state <> 'archived'
       AND site.primary_origin = p_origin
     FOR UPDATE OF membership, tenant, site_membership, site;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'denied'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;

    SELECT attempt.outcome, attempt.request_hash,
           verification.verified_at, verification.recheck_at
      INTO v_existing
      FROM app.site_origin_verification_attempts AS attempt
      LEFT JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = attempt.tenant_id
       AND verification.site_id = attempt.site_id
       AND verification.id = attempt.id
     WHERE attempt.tenant_id = v_tenant_id
       AND attempt.user_id = v_user_id
       AND attempt.idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.request_hash IS DISTINCT FROM p_request_hash THEN
            RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                NULL::timestamptz, NULL::timestamptz, NULL::boolean;
        ELSIF v_existing.outcome = 'verified' THEN
            RETURN QUERY SELECT 'verified'::text, v_tenant_id, v_user_id,
                p_site_id, p_challenge_id, p_origin, 'http_well_known'::text,
                v_existing.verified_at, v_existing.recheck_at, true;
        ELSE
            RETURN QUERY SELECT v_existing.outcome, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                NULL::timestamptz, NULL::timestamptz, true;
        END IF;
        RETURN;
    END IF;

    SELECT challenge.proof_sha256, challenge.expires_at
      INTO v_challenge
      FROM app.site_origin_challenges AS challenge
     WHERE challenge.tenant_id = v_tenant_id
       AND challenge.site_id = p_site_id
       AND challenge.id = p_challenge_id
       AND challenge.origin = p_origin;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'challenge_not_found'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;
    IF v_challenge.expires_at <= v_now THEN
        RETURN QUERY SELECT 'challenge_expired'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;

    SELECT verification.verified_at, verification.recheck_at
      INTO v_existing
      FROM app.site_origin_verifications AS verification
     WHERE verification.tenant_id = v_tenant_id
       AND verification.site_id = p_site_id
       AND verification.challenge_id = p_challenge_id;
    IF FOUND THEN
        RETURN QUERY SELECT 'verified'::text, v_tenant_id, v_user_id,
            p_site_id, p_challenge_id, p_origin, 'http_well_known'::text,
            v_existing.verified_at, v_existing.recheck_at, true;
        RETURN;
    END IF;

    -- Preparation deliberately releases its transaction before network I/O.
    -- Recheck the budget under the locked site row so concurrent prepared
    -- requests cannot commit more attempts than the challenge permits.
    IF (
        SELECT count(*)
          FROM app.site_origin_verification_attempts AS attempt
         WHERE attempt.tenant_id = v_tenant_id
           AND attempt.site_id = p_site_id
           AND attempt.challenge_id = p_challenge_id
    ) >= 10 THEN
        RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean;
        RETURN;
    END IF;

    IF p_observation_outcome = 'matched' AND (
        p_http_status IS DISTINCT FROM 200
        OR p_media_type IS DISTINCT FROM 'text/plain'
        OR p_response_sha256 IS DISTINCT FROM v_challenge.proof_sha256
        OR p_observed_final_url IS DISTINCT FROM p_origin
           || '/.well-known/signal-site-verification.txt'
        OR p_resolved_address IS NULL OR p_elapsed_ms IS NULL
    ) THEN
        RAISE EXCEPTION 'invalid_matched_origin_proof' USING ERRCODE = '22023';
    END IF;

    IF p_observation_outcome = 'matched' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(p_origin, 1397312851));
        SELECT claim.tenant_id, claim.site_id, claim.claim_generation
          INTO v_claim
          FROM control.public_origin_claims AS claim
         WHERE claim.origin = p_origin
         FOR UPDATE;
        IF FOUND AND (v_claim.tenant_id, v_claim.site_id)
            IS DISTINCT FROM (v_tenant_id, p_site_id)
        THEN
            v_final_outcome := 'claim_conflict';
        ELSE
            v_final_outcome := 'verified';
        END IF;
    END IF;

    INSERT INTO app.site_origin_verification_attempts (
        tenant_id, site_id, id, challenge_id, user_id, session_id,
        idempotency_key, request_hash, origin, proof_url, outcome, http_status,
        media_type, response_sha256, observed_final_url, resolved_address,
        elapsed_ms, role_key, authentication_level,
        membership_authorization_epoch, site_authorization_epoch,
        recovery_generation, attempted_at
    ) VALUES (
        v_tenant_id, p_site_id, p_attempt_id, p_challenge_id, v_user_id,
        v_session_id, p_idempotency_key, p_request_hash, p_origin,
        p_origin || '/.well-known/signal-site-verification.txt',
        v_final_outcome, p_http_status, p_media_type, p_response_sha256,
        p_observed_final_url, p_resolved_address, p_elapsed_ms, 'owner',
        v_authentication_level, v_membership_epoch, v_site_epoch,
        p_current_recovery_generation, v_now
    );

    IF v_final_outcome = 'verified' THEN
        v_recheck_at := v_now + make_interval(secs => p_recheck_seconds);
        INSERT INTO app.site_origin_verifications (
            tenant_id, site_id, id, challenge_id, origin, proof_method,
            resource_identity, permitted_origins, revocation_conditions,
            verified_at, recheck_at
        ) VALUES (
            v_tenant_id, p_site_id, p_attempt_id, p_challenge_id, p_origin,
            'http_well_known', p_origin, jsonb_build_array(p_origin),
            '["origin_changed","ownership_changed","claim_revoked","recheck_expired"]'::jsonb,
            v_now, v_recheck_at
        );
        INSERT INTO control.public_origin_claims (
            origin, tenant_id, site_id, verification_id, proof_method,
            verified_at, recheck_at, claim_generation
        ) VALUES (
            p_origin, v_tenant_id, p_site_id, p_attempt_id,
            'http_well_known', v_now, v_recheck_at, 1
        )
        ON CONFLICT (origin) DO UPDATE
            SET verification_id = EXCLUDED.verification_id,
                proof_method = EXCLUDED.proof_method,
                verified_at = EXCLUDED.verified_at,
                recheck_at = EXCLUDED.recheck_at,
                claim_generation = control.public_origin_claims.claim_generation + 1
        WHERE control.public_origin_claims.tenant_id = EXCLUDED.tenant_id
          AND control.public_origin_claims.site_id = EXCLUDED.site_id;
        UPDATE app.sites AS site
           SET state = 'active',
               ownership_status = 'verified',
               row_version = site.row_version + 1
         WHERE site.tenant_id = v_tenant_id
           AND site.id = p_site_id;
        RETURN QUERY SELECT 'verified'::text, v_tenant_id, v_user_id,
            p_site_id, p_challenge_id, p_origin, 'http_well_known'::text,
            v_now, v_recheck_at, false;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_final_outcome, NULL::uuid, NULL::uuid,
        NULL::uuid, NULL::uuid, NULL::text, NULL::text,
        NULL::timestamptz, NULL::timestamptz, false;
END
$$;

REVOKE ALL ON FUNCTION control.issue_site_origin_challenge(
    bytea, text, uuid, uuid, uuid, bytea, text, integer, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.prepare_site_origin_verification(
    bytea, text, uuid, uuid, uuid, bytea, text, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_site_origin_verification(
    bytea, text, uuid, uuid, uuid, uuid, bytea, text, text, integer, text,
    bytea, text, text, integer, integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.issue_site_origin_challenge(
    bytea, text, uuid, uuid, uuid, bytea, text, integer, integer
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.prepare_site_origin_verification(
    bytea, text, uuid, uuid, uuid, bytea, text, integer
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.record_site_origin_verification(
    bytea, text, uuid, uuid, uuid, uuid, bytea, text, text, integer, text,
    bytea, text, text, integer, integer
) TO signal_identity;
