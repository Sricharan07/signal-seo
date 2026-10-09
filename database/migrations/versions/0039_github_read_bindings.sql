CREATE TABLE app.github_read_bindings (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    requested_by_user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    installation_id bigint NOT NULL CHECK (installation_id > 0),
    repository_owner text NOT NULL CHECK (length(repository_owner) BETWEEN 1 AND 39),
    repository_name text NOT NULL CHECK (length(repository_name) BETWEEN 1 AND 100),
    base_branch text NOT NULL CHECK (length(base_branch) BETWEEN 1 AND 255),
    content_path text NOT NULL CHECK (length(content_path) BETWEEN 1 AND 1024),
    membership_epoch bigint NOT NULL CHECK (membership_epoch > 0),
    site_epoch bigint NOT NULL CHECK (site_epoch > 0),
    recovery_generation text NOT NULL CHECK (length(recovery_generation) BETWEEN 1 AND 128),
    status text NOT NULL CHECK (status IN ('prepared', 'active', 'failed', 'revoked')),
    repository_id bigint CHECK (repository_id > 0),
    observed_full_name text CHECK (length(observed_full_name) BETWEEN 3 AND 200),
    default_branch text CHECK (length(default_branch) BETWEEN 1 AND 255),
    base_sha text CHECK (base_sha ~ '^[0-9a-f]{40}$'),
    private boolean,
    protected boolean,
    observed_at timestamptz,
    failure_code text CHECK (failure_code IN (
        'GITHUB_AUTHORIZATION_REJECTED', 'GITHUB_REPOSITORY_STATE_REJECTED',
        'GITHUB_SCOPE_REJECTED', 'GITHUB_PROVIDER_RESPONSE_REJECTED',
        'GITHUB_PROVIDER_UNAVAILABLE', 'GITHUB_CREDENTIALS_REJECTED',
        'GITHUB_CREDENTIAL_UNAVAILABLE'
    )),
    prepared_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    revoked_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT github_read_binding_request_key UNIQUE (tenant_id, requested_by_user_id, idempotency_key),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, requested_by_user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK ((status = 'active' AND repository_id IS NOT NULL AND observed_full_name IS NOT NULL
            AND default_branch IS NOT NULL AND base_sha IS NOT NULL AND private IS NOT NULL
            AND protected IS NOT NULL AND observed_at IS NOT NULL AND failure_code IS NULL)
        OR (status = 'prepared' AND repository_id IS NULL AND failure_code IS NULL AND revoked_at IS NULL)
        OR (status = 'failed' AND repository_id IS NULL AND failure_code IS NOT NULL AND revoked_at IS NULL)
        OR (status = 'revoked' AND revoked_at IS NOT NULL))
);
CREATE UNIQUE INDEX github_read_binding_open_site
ON app.github_read_bindings (tenant_id, site_id)
WHERE status IN ('prepared', 'active');

CREATE TABLE app.github_read_binding_events (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    binding_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    event_kind text NOT NULL CHECK (event_kind IN ('prepared', 'activated', 'failed', 'revoked')),
    detail_code text,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, binding_id)
        REFERENCES app.github_read_bindings (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE TRIGGER github_read_binding_events_immutable BEFORE UPDATE OR DELETE
ON app.github_read_binding_events FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.github_read_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_read_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY github_read_binding_scope ON app.github_read_bindings
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
ALTER TABLE app.github_read_binding_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_read_binding_events FORCE ROW LEVEL SECURITY;
CREATE POLICY github_read_binding_events_scope ON app.github_read_binding_events
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.github_read_bindings, app.github_read_binding_events
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.prepare_github_read_binding(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid,
    p_event_id uuid, p_request_id uuid, p_request_hash bytea, p_installation_id bigint,
    p_owner text, p_repository text, p_branch text, p_content_path text
) RETURNS TABLE (binding_id uuid, binding_status text, replayed boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_existing app.github_read_bindings%%ROWTYPE;
BEGIN
    IF p_binding_id IS NULL OR p_event_id IS NULL OR p_request_id IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_installation_id IS NULL OR p_installation_id <= 0
       OR p_owner IS NULL OR p_owner !~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}$'
       OR p_repository IS NULL OR p_repository !~ '^[A-Za-z0-9_.-]{1,100}$'
       OR p_branch IS NULL OR length(p_branch) NOT BETWEEN 1 AND 255
       OR p_content_path IS NULL OR length(p_content_path) NOT BETWEEN 1 AND 1024
    THEN RAISE EXCEPTION 'invalid_github_binding_input' USING ERRCODE = '22023'; END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'permission_denied'::text; RETURN;
    END IF;
    PERFORM 1 FROM app.sites AS site WHERE site.tenant_id = v_authority.tenant_id
        AND site.id = p_site_id AND site.ownership_status = 'verified' FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'site_not_verified'::text; RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.site_origin_verifications AS verification
        JOIN control.public_origin_claims AS claim
          ON claim.origin = verification.origin
         AND claim.tenant_id = verification.tenant_id
         AND claim.site_id = verification.site_id
         AND claim.verification_id = verification.id
        JOIN app.sites AS site ON site.tenant_id = verification.tenant_id
         AND site.id = verification.site_id AND site.primary_origin = verification.origin
        WHERE verification.tenant_id = v_authority.tenant_id
          AND verification.site_id = p_site_id
          AND verification.recheck_at > transaction_timestamp()
          AND claim.recheck_at > transaction_timestamp()
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO v_existing FROM app.github_read_bindings AS binding
     WHERE binding.tenant_id = v_authority.tenant_id
       AND binding.requested_by_user_id = v_authority.user_id
       AND binding.idempotency_key = p_request_id;
    IF FOUND THEN
        IF v_existing.site_id <> p_site_id OR v_existing.request_hash <> p_request_hash
           OR v_existing.installation_id <> p_installation_id
           OR v_existing.repository_owner <> p_owner
           OR v_existing.repository_name <> p_repository
           OR v_existing.base_branch <> p_branch
           OR v_existing.content_path <> p_content_path THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'request_conflict'::text; RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.status, true, 'prepared'::text; RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_read_bindings AS binding
        WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
          AND binding.status IN ('prepared', 'active')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'binding_exists'::text; RETURN;
    END IF;
    INSERT INTO app.github_read_bindings (
        tenant_id, site_id, id, requested_by_user_id, idempotency_key, request_hash,
        installation_id, repository_owner, repository_name, base_branch, content_path,
        membership_epoch, site_epoch, recovery_generation, status
    ) VALUES (v_authority.tenant_id, p_site_id, p_binding_id, v_authority.user_id,
        p_request_id, p_request_hash, p_installation_id, p_owner, p_repository,
        p_branch, p_content_path, v_authority.membership_epoch,
        v_authority.site_authorization_epoch, p_generation, 'prepared');
    INSERT INTO app.github_read_binding_events
        (tenant_id, site_id, id, binding_id, actor_user_id, event_kind)
    VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_binding_id,
            v_authority.user_id, 'prepared');
    RETURN QUERY SELECT p_binding_id, 'prepared'::text, false, 'prepared'::text;
END; $$;

CREATE FUNCTION control.finish_github_read_binding(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid,
    p_event_id uuid, p_outcome text, p_repository_id bigint,
    p_full_name text, p_default_branch text, p_base_sha text,
    p_private boolean, p_protected boolean
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_binding app.github_read_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_binding FROM app.github_read_bindings AS binding
      WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
        AND binding.id = p_binding_id FOR UPDATE;
    IF NOT FOUND OR v_binding.requested_by_user_id <> v_authority.user_id
       OR v_binding.membership_epoch <> v_authority.membership_epoch
       OR v_binding.site_epoch <> v_authority.site_authorization_epoch
       OR v_binding.recovery_generation <> p_generation THEN
        RETURN 'binding_not_authorized';
    END IF;
    IF v_binding.status = 'active' AND p_outcome = 'observed'
       AND v_binding.repository_id = p_repository_id
       AND v_binding.observed_full_name = p_full_name
       AND v_binding.default_branch = p_default_branch
       AND v_binding.base_sha = p_base_sha
       AND v_binding.private = p_private
       AND v_binding.protected = p_protected THEN
        RETURN 'active';
    END IF;
    IF v_binding.status <> 'prepared' THEN RETURN 'binding_not_prepared'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.site_origin_verifications AS verification
        JOIN control.public_origin_claims AS claim
          ON claim.origin = verification.origin
         AND claim.tenant_id = verification.tenant_id
         AND claim.site_id = verification.site_id
         AND claim.verification_id = verification.id
        JOIN app.sites AS site ON site.tenant_id = verification.tenant_id
         AND site.id = verification.site_id AND site.primary_origin = verification.origin
        WHERE verification.tenant_id = v_authority.tenant_id
          AND verification.site_id = p_site_id AND site.ownership_status = 'verified'
          AND verification.recheck_at > transaction_timestamp()
          AND claim.recheck_at > transaction_timestamp()
    ) THEN RETURN 'site_not_verified'; END IF;
    IF p_outcome = 'observed' THEN
        IF p_repository_id IS NULL OR p_repository_id <= 0 OR p_full_name IS NULL
           OR lower(p_full_name) <> lower(v_binding.repository_owner || '/' || v_binding.repository_name)
           OR p_default_branch IS NULL OR length(p_default_branch) NOT BETWEEN 1 AND 255
           OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
           OR p_private IS NULL OR p_protected IS DISTINCT FROM true THEN
            RETURN 'invalid_observation';
        END IF;
        UPDATE app.github_read_bindings SET status = 'active', repository_id = p_repository_id,
            observed_full_name = p_full_name, default_branch = p_default_branch,
            base_sha = p_base_sha, private = p_private, protected = p_protected,
            observed_at = transaction_timestamp()
         WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_binding_id;
        INSERT INTO app.github_read_binding_events
            (tenant_id, site_id, id, binding_id, actor_user_id, event_kind)
        VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_binding_id,
                v_authority.user_id, 'activated');
        RETURN 'active';
    END IF;
    IF p_outcome NOT IN ('GITHUB_AUTHORIZATION_REJECTED', 'GITHUB_REPOSITORY_STATE_REJECTED',
        'GITHUB_SCOPE_REJECTED', 'GITHUB_PROVIDER_RESPONSE_REJECTED',
        'GITHUB_PROVIDER_UNAVAILABLE', 'GITHUB_CREDENTIALS_REJECTED',
        'GITHUB_CREDENTIAL_UNAVAILABLE') OR p_repository_id IS NOT NULL OR p_full_name IS NOT NULL
       OR p_default_branch IS NOT NULL OR p_base_sha IS NOT NULL OR p_private IS NOT NULL
       OR p_protected IS NOT NULL THEN RETURN 'invalid_observation'; END IF;
    UPDATE app.github_read_bindings SET status = 'failed', failure_code = p_outcome
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_binding_id;
    INSERT INTO app.github_read_binding_events
        (tenant_id, site_id, id, binding_id, actor_user_id, event_kind, detail_code)
    VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_binding_id,
            v_authority.user_id, 'failed', p_outcome);
    RETURN 'failed';
END; $$;

CREATE FUNCTION control.revoke_github_read_binding(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_binding_id uuid, p_event_id uuid
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_binding app.github_read_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_binding FROM app.github_read_bindings AS binding
      WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
        AND binding.id = p_binding_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'binding_not_found'; END IF;
    IF v_binding.status = 'revoked' THEN RETURN 'revoked'; END IF;
    UPDATE app.github_read_bindings SET status = 'revoked', revoked_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_binding_id;
    INSERT INTO app.github_read_binding_events
        (tenant_id, site_id, id, binding_id, actor_user_id, event_kind)
    VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_binding_id,
            v_authority.user_id, 'revoked');
    RETURN 'revoked';
END; $$;

CREATE FUNCTION control.read_github_read_binding(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid
) RETURNS TABLE (
    binding_id uuid, binding_status text, installation_id bigint, repository_owner text,
    repository_name text, base_branch text, content_path text, repository_id bigint,
    observed_full_name text, base_sha text, outcome text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::bigint, NULL::text,
          NULL::text, NULL::text, NULL::text, NULL::bigint, NULL::text, NULL::text,
          v_authority.outcome; RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.site_origin_verifications AS verification
        JOIN control.public_origin_claims AS claim
          ON claim.origin = verification.origin
         AND claim.tenant_id = verification.tenant_id
         AND claim.site_id = verification.site_id
         AND claim.verification_id = verification.id
        JOIN app.sites AS site ON site.tenant_id = verification.tenant_id
         AND site.id = verification.site_id AND site.primary_origin = verification.origin
        WHERE verification.tenant_id = v_authority.tenant_id
          AND verification.site_id = p_site_id AND site.ownership_status = 'verified'
          AND verification.recheck_at > transaction_timestamp()
          AND claim.recheck_at > transaction_timestamp()
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::bigint, NULL::text,
          NULL::text, NULL::text, NULL::text, NULL::bigint, NULL::text, NULL::text,
          'site_not_verified'::text; RETURN;
    END IF;
    RETURN QUERY SELECT binding.id, binding.status, binding.installation_id,
        binding.repository_owner, binding.repository_name, binding.base_branch,
        binding.content_path, binding.repository_id, binding.observed_full_name,
        binding.base_sha, 'found'::text
      FROM app.github_read_bindings AS binding
     WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
       AND binding.id = p_binding_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::bigint, NULL::text,
          NULL::text, NULL::text, NULL::text, NULL::bigint, NULL::text, NULL::text,
          'binding_not_found'::text;
    END IF;
END; $$;

REVOKE ALL ON FUNCTION control.prepare_github_read_binding(bytea, uuid, text, uuid, uuid,
    uuid, bytea, bigint, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_github_read_binding(bytea, uuid, text, uuid, uuid,
    text, bigint, text, text, text, boolean, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.revoke_github_read_binding(bytea, uuid, text, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_github_read_binding(bytea, uuid, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_github_read_binding(bytea, uuid, text, uuid, uuid,
    uuid, bytea, bigint, text, text, text, text) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.finish_github_read_binding(bytea, uuid, text, uuid, uuid,
    text, bigint, text, text, text, boolean, boolean) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.revoke_github_read_binding(bytea, uuid, text, uuid, uuid) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.read_github_read_binding(bytea, uuid, text, uuid) TO signal_identity;
