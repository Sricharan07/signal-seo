CREATE TABLE app.github_pr_extensions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    binding_id uuid NOT NULL,
    requested_by_user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    membership_epoch bigint NOT NULL CHECK (membership_epoch > 0),
    site_epoch bigint NOT NULL CHECK (site_epoch > 0),
    recovery_generation text NOT NULL CHECK (length(recovery_generation) BETWEEN 1 AND 128),
    status text NOT NULL CHECK (status IN ('prepared', 'observed', 'failed', 'revoked')),
    repository_id bigint CHECK (repository_id > 0),
    base_sha text CHECK (base_sha ~ '^[0-9a-f]{40}$'),
    tree_sha text CHECK (tree_sha ~ '^[0-9a-f]{40}$'),
    framework text CHECK (framework IN ('nextjs', 'astro', 'hugo', 'eleventy', 'unknown', 'ambiguous')),
    content_format text CHECK (content_format IN (
        'tsx', 'jsx', 'markdown', 'mdx', 'astro', 'html', 'liquid', 'nunjucks', 'unknown'
    )),
    coverage text CHECK (coverage IN ('complete', 'partial')),
    content_sha text CHECK (content_sha ~ '^[0-9a-f]{40}$'),
    marker_evidence jsonb CHECK (
        jsonb_typeof(marker_evidence) = 'array'
        AND jsonb_array_length(marker_evidence) <= 16
        AND octet_length(marker_evidence::text) <= 8192
    ),
    failure_code text CHECK (failure_code IN (
        'GITHUB_AUTHORIZATION_REJECTED', 'GITHUB_REPOSITORY_STATE_REJECTED',
        'GITHUB_SCOPE_REJECTED', 'GITHUB_PROVIDER_RESPONSE_REJECTED',
        'GITHUB_PROVIDER_UNAVAILABLE', 'GITHUB_CREDENTIALS_REJECTED',
        'GITHUB_CREDENTIAL_UNAVAILABLE', 'GITHUB_BINDING_CHANGED'
    )),
    prepared_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    observed_at timestamptz,
    revoked_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT github_pr_extension_request_key UNIQUE (tenant_id, requested_by_user_id, idempotency_key),
    FOREIGN KEY (tenant_id, site_id, binding_id)
        REFERENCES app.github_read_bindings (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, requested_by_user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK (
        (status = 'prepared' AND repository_id IS NULL AND failure_code IS NULL
         AND observed_at IS NULL AND revoked_at IS NULL)
        OR
        (status = 'observed' AND repository_id IS NOT NULL AND base_sha IS NOT NULL
         AND tree_sha IS NOT NULL AND framework IS NOT NULL AND content_format IS NOT NULL
         AND coverage IS NOT NULL AND marker_evidence IS NOT NULL AND observed_at IS NOT NULL
         AND failure_code IS NULL AND revoked_at IS NULL
         AND (coverage = 'partial' OR content_sha IS NOT NULL))
        OR
        (status = 'failed' AND failure_code IS NOT NULL AND repository_id IS NULL
         AND observed_at IS NULL AND revoked_at IS NULL)
        OR
        (status = 'revoked' AND revoked_at IS NOT NULL)
    )
);
CREATE UNIQUE INDEX github_pr_extension_open_binding
ON app.github_pr_extensions (tenant_id, site_id, binding_id)
WHERE status IN ('prepared', 'observed');

CREATE TABLE app.github_pr_extension_events (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    extension_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    event_kind text NOT NULL CHECK (event_kind IN ('prepared', 'observed', 'failed', 'revoked')),
    detail_code text,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, extension_id)
        REFERENCES app.github_pr_extensions (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE TRIGGER github_pr_extension_events_immutable BEFORE UPDATE OR DELETE
ON app.github_pr_extension_events FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.github_pr_extensions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_pr_extensions FORCE ROW LEVEL SECURITY;
CREATE POLICY github_pr_extension_scope ON app.github_pr_extensions
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
ALTER TABLE app.github_pr_extension_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_pr_extension_events FORCE ROW LEVEL SECURITY;
CREATE POLICY github_pr_extension_events_scope ON app.github_pr_extension_events
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.github_pr_extensions, app.github_pr_extension_events
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.current_github_site_proof(p_tenant_id uuid, p_site_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN EXISTS (
        SELECT 1 FROM app.site_origin_verifications AS verification
        JOIN control.public_origin_claims AS claim
          ON claim.origin = verification.origin
         AND claim.tenant_id = verification.tenant_id
         AND claim.site_id = verification.site_id
         AND claim.verification_id = verification.id
        JOIN app.sites AS site ON site.tenant_id = verification.tenant_id
         AND site.id = verification.site_id AND site.primary_origin = verification.origin
        WHERE verification.tenant_id = p_tenant_id AND verification.site_id = p_site_id
          AND site.ownership_status = 'verified'
          AND verification.recheck_at > transaction_timestamp()
          AND claim.recheck_at > transaction_timestamp()
    );
END; $$;
REVOKE ALL ON FUNCTION control.current_github_site_proof(uuid, uuid) FROM PUBLIC;

CREATE FUNCTION control.prepare_github_pr_extension(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid,
    p_extension_id uuid, p_event_id uuid, p_request_id uuid, p_request_hash bytea
) RETURNS TABLE (extension_id uuid, extension_status text, replayed boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_binding app.github_read_bindings%%ROWTYPE;
        v_existing app.github_pr_extensions%%ROWTYPE;
BEGIN
    IF p_binding_id IS NULL OR p_extension_id IS NULL OR p_event_id IS NULL
       OR p_request_id IS NULL OR p_request_hash IS NULL
       OR octet_length(p_request_hash) <> 32 THEN
        RAISE EXCEPTION 'invalid_github_pr_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'permission_denied'::text; RETURN;
    END IF;
    PERFORM 1 FROM app.sites AS site WHERE site.tenant_id = v_authority.tenant_id
        AND site.id = p_site_id FOR UPDATE;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO v_existing FROM app.github_pr_extensions AS extension
      WHERE extension.tenant_id = v_authority.tenant_id
        AND extension.requested_by_user_id = v_authority.user_id
        AND extension.idempotency_key = p_request_id;
    IF FOUND THEN
        IF v_existing.site_id <> p_site_id OR v_existing.binding_id <> p_binding_id
           OR v_existing.request_hash <> p_request_hash THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'request_conflict'::text; RETURN;
        END IF;
    END IF;
    SELECT * INTO v_binding FROM app.github_read_bindings AS binding
      WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
        AND binding.id = p_binding_id AND binding.status = 'active' FOR SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'binding_inactive'::text; RETURN;
    END IF;
    IF v_existing.id IS NOT NULL THEN
        RETURN QUERY SELECT v_existing.id, v_existing.status, true, 'prepared'::text; RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
        WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
          AND extension.binding_id = p_binding_id
          AND extension.status IN ('prepared', 'observed')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'extension_exists'::text; RETURN;
    END IF;
    INSERT INTO app.github_pr_extensions (
        tenant_id, site_id, id, binding_id, requested_by_user_id, idempotency_key,
        request_hash, membership_epoch, site_epoch, recovery_generation, status
    ) VALUES (v_authority.tenant_id, p_site_id, p_extension_id, p_binding_id,
        v_authority.user_id, p_request_id, p_request_hash, v_authority.membership_epoch,
        v_authority.site_authorization_epoch, p_generation, 'prepared');
    INSERT INTO app.github_pr_extension_events
        (tenant_id, site_id, id, extension_id, actor_user_id, event_kind)
    VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_extension_id,
            v_authority.user_id, 'prepared');
    RETURN QUERY SELECT p_extension_id, 'prepared'::text, false, 'prepared'::text;
END; $$;

CREATE FUNCTION control.finish_github_pr_extension(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_extension_id uuid, p_event_id uuid, p_outcome text,
    p_repository_id bigint, p_base_sha text, p_tree_sha text,
    p_framework text, p_content_format text, p_coverage text,
    p_content_sha text, p_marker_evidence jsonb, p_protected boolean
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_extension app.github_pr_extensions%%ROWTYPE;
        v_binding app.github_read_bindings%%ROWTYPE; v_item jsonb;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_extension FROM app.github_pr_extensions AS extension
      WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
        AND extension.id = p_extension_id FOR UPDATE;
    IF NOT FOUND OR v_extension.requested_by_user_id <> v_authority.user_id
       OR v_extension.membership_epoch <> v_authority.membership_epoch
       OR v_extension.site_epoch <> v_authority.site_authorization_epoch
       OR v_extension.recovery_generation <> p_generation THEN
        RETURN 'extension_not_authorized';
    END IF;
    SELECT * INTO v_binding FROM app.github_read_bindings AS binding
      WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
        AND binding.id = v_extension.binding_id AND binding.status = 'active' FOR SHARE;
    IF NOT FOUND THEN RETURN 'binding_inactive'; END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN 'site_not_verified';
    END IF;
    IF v_extension.status <> 'prepared' THEN RETURN 'extension_not_prepared'; END IF;
    IF p_outcome = 'observed' THEN
        IF p_repository_id IS DISTINCT FROM v_binding.repository_id
           OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
           OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
           OR p_protected IS DISTINCT FROM true
           OR p_framework IS NULL OR p_content_format IS NULL OR p_coverage IS NULL
           OR p_framework NOT IN ('nextjs', 'astro', 'hugo', 'eleventy', 'unknown', 'ambiguous')
           OR p_content_format NOT IN (
               'tsx', 'jsx', 'markdown', 'mdx', 'astro', 'html', 'liquid', 'nunjucks', 'unknown'
           ) OR p_coverage NOT IN ('complete', 'partial')
           OR (p_coverage = 'complete' AND (p_content_sha IS NULL
               OR p_content_sha !~ '^[0-9a-f]{40}$'))
           OR (p_content_format <> 'unknown' AND p_content_sha IS NULL)
           OR (p_content_sha IS NOT NULL AND p_content_sha !~ '^[0-9a-f]{40}$')
           OR p_marker_evidence IS NULL OR jsonb_typeof(p_marker_evidence) <> 'array'
           OR jsonb_array_length(p_marker_evidence) > 16
           OR (p_framework IN ('nextjs', 'astro', 'hugo', 'eleventy')
               AND jsonb_array_length(p_marker_evidence) < 1)
           OR (p_framework = 'ambiguous' AND jsonb_array_length(p_marker_evidence) < 2)
           OR octet_length(p_marker_evidence::text) > 8192 THEN
            RETURN 'invalid_observation';
        END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_marker_evidence) LOOP
            IF jsonb_typeof(v_item) <> 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 2
               OR NOT (v_item ?& ARRAY['path', 'sha'])
               OR jsonb_typeof(v_item->'path') <> 'string'
               OR jsonb_typeof(v_item->'sha') <> 'string'
               OR length(v_item->>'path') NOT BETWEEN 1 AND 1024
               OR (v_item->>'sha') !~ '^[0-9a-f]{40}$' THEN
                RETURN 'invalid_observation';
            END IF;
        END LOOP;
        UPDATE app.github_pr_extensions SET status = 'observed',
            repository_id = p_repository_id, base_sha = p_base_sha, tree_sha = p_tree_sha,
            framework = p_framework, content_format = p_content_format, coverage = p_coverage,
            content_sha = p_content_sha, marker_evidence = p_marker_evidence,
            observed_at = transaction_timestamp()
         WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_extension_id;
        INSERT INTO app.github_pr_extension_events
            (tenant_id, site_id, id, extension_id, actor_user_id, event_kind)
        VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_extension_id,
                v_authority.user_id, 'observed');
        RETURN 'observed';
    END IF;
    IF p_outcome IS NULL OR p_outcome NOT IN (
        'GITHUB_AUTHORIZATION_REJECTED', 'GITHUB_REPOSITORY_STATE_REJECTED',
        'GITHUB_SCOPE_REJECTED', 'GITHUB_PROVIDER_RESPONSE_REJECTED',
        'GITHUB_PROVIDER_UNAVAILABLE', 'GITHUB_CREDENTIALS_REJECTED',
        'GITHUB_CREDENTIAL_UNAVAILABLE', 'GITHUB_BINDING_CHANGED')
       OR p_repository_id IS NOT NULL OR p_base_sha IS NOT NULL OR p_tree_sha IS NOT NULL
       OR p_framework IS NOT NULL OR p_content_format IS NOT NULL OR p_coverage IS NOT NULL
       OR p_content_sha IS NOT NULL OR p_marker_evidence IS NOT NULL OR p_protected IS NOT NULL
    THEN RETURN 'invalid_observation'; END IF;
    UPDATE app.github_pr_extensions SET status = 'failed', failure_code = p_outcome
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_extension_id;
    INSERT INTO app.github_pr_extension_events
        (tenant_id, site_id, id, extension_id, actor_user_id, event_kind, detail_code)
    VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_extension_id,
            v_authority.user_id, 'failed', p_outcome);
    RETURN 'failed';
END; $$;

CREATE FUNCTION control.revoke_github_pr_extension(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_extension_id uuid, p_event_id uuid
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_extension app.github_pr_extensions%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_extension FROM app.github_pr_extensions AS extension
      WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
        AND extension.id = p_extension_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'extension_not_found'; END IF;
    IF v_extension.status = 'revoked' THEN RETURN 'revoked'; END IF;
    UPDATE app.github_pr_extensions SET status = 'revoked', revoked_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_extension_id;
    INSERT INTO app.github_pr_extension_events
        (tenant_id, site_id, id, extension_id, actor_user_id, event_kind)
    VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_extension_id,
            v_authority.user_id, 'revoked');
    RETURN 'revoked';
END; $$;

CREATE FUNCTION control.read_github_pr_extension(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid
) RETURNS TABLE (
    extension_id uuid, extension_status text, binding_id uuid, repository_id bigint,
    base_sha text, tree_sha text, framework text, content_format text, coverage text,
    content_sha text, marker_evidence jsonb, outcome text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::bigint,
          NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
          NULL::text, NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::bigint,
          NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
          NULL::text, NULL::jsonb, 'site_not_verified'::text; RETURN;
    END IF;
    RETURN QUERY SELECT extension.id, extension.status, extension.binding_id,
        extension.repository_id, extension.base_sha, extension.tree_sha,
        extension.framework, extension.content_format, extension.coverage,
        extension.content_sha, extension.marker_evidence, 'found'::text
      FROM app.github_pr_extensions AS extension
      JOIN app.github_read_bindings AS binding
        ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
       AND binding.id = extension.binding_id AND binding.status = 'active'
     WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
       AND extension.id = p_extension_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::bigint,
          NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
          NULL::text, NULL::jsonb, 'extension_not_found'::text;
    END IF;
END; $$;

REVOKE ALL ON FUNCTION control.prepare_github_pr_extension(
    bytea, uuid, text, uuid, uuid, uuid, uuid, bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_github_pr_extension(
    bytea, uuid, text, uuid, uuid, text, bigint, text, text,
    text, text, text, text, jsonb, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.revoke_github_pr_extension(bytea, uuid, text, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_github_pr_extension(bytea, uuid, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_github_pr_extension(
    bytea, uuid, text, uuid, uuid, uuid, uuid, bytea) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.finish_github_pr_extension(
    bytea, uuid, text, uuid, uuid, text, bigint, text, text,
    text, text, text, text, jsonb, boolean) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.revoke_github_pr_extension(bytea, uuid, text, uuid, uuid)
TO signal_identity;
GRANT EXECUTE ON FUNCTION control.read_github_pr_extension(bytea, uuid, text, uuid)
TO signal_identity;
