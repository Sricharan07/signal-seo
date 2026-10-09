CREATE TABLE app.candidate_build_intents (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    extension_id uuid NOT NULL,
    requested_by_user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    membership_epoch bigint NOT NULL CHECK (membership_epoch > 0),
    site_epoch bigint NOT NULL CHECK (site_epoch > 0),
    recovery_generation text NOT NULL CHECK (length(recovery_generation) BETWEEN 1 AND 128),
    base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
    tree_sha text NOT NULL CHECK (tree_sha ~ '^[0-9a-f]{40}$'),
    patch_sha256 text NOT NULL CHECK (patch_sha256 ~ '^[0-9a-f]{64}$'),
    toolchain text NOT NULL CHECK (toolchain =
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'),
    build_command text NOT NULL CHECK (build_command = 'npm run build'),
    artifact_root text NOT NULL CHECK (artifact_root IN ('.next', 'dist', '_site')),
    status text NOT NULL CHECK (status IN ('prepared', 'dispatched', 'completed')),
    prepared_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    dispatched_at timestamptz,
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, requested_by_user_id, idempotency_key),
    FOREIGN KEY (tenant_id, site_id, extension_id)
        REFERENCES app.github_pr_extensions (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, requested_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id),
    CHECK ((status = 'prepared' AND dispatched_at IS NULL AND completed_at IS NULL)
        OR (status = 'dispatched' AND dispatched_at IS NOT NULL AND completed_at IS NULL)
        OR (status = 'completed' AND dispatched_at IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX candidate_build_one_open_extension
ON app.candidate_build_intents (tenant_id, site_id, extension_id)
WHERE status IN ('prepared', 'dispatched');

CREATE TABLE app.candidate_build_receipts (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    build_id uuid NOT NULL,
    base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
    patch_sha256 text NOT NULL CHECK (patch_sha256 ~ '^[0-9a-f]{64}$'),
    toolchain text NOT NULL CHECK (toolchain =
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'),
    build_command text NOT NULL CHECK (build_command = 'npm run build'),
    exit_class text NOT NULL CHECK (exit_class IN (
        'passed', 'crash', 'timeout', 'oom', 'output_limit', 'policy_rejected'
    )),
    exit_code integer CHECK (exit_code BETWEEN 0 AND 255),
    logs_sha256 text NOT NULL CHECK (logs_sha256 ~ '^[0-9a-f]{64}$'),
    log_bytes integer NOT NULL CHECK (log_bytes BETWEEN 0 AND 65536),
    artifacts jsonb NOT NULL CHECK (
        jsonb_typeof(artifacts) = 'array' AND jsonb_array_length(artifacts) <= 1000
        AND octet_length(artifacts::text) <= 131072
    ),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, build_id),
    FOREIGN KEY (tenant_id, site_id, build_id)
        REFERENCES app.candidate_build_intents (tenant_id, site_id, id),
    CHECK ((exit_class = 'passed' AND exit_code = 0 AND jsonb_array_length(artifacts) > 0)
        OR (exit_class <> 'passed' AND jsonb_array_length(artifacts) = 0))
);
CREATE TRIGGER candidate_build_receipts_immutable BEFORE UPDATE OR DELETE
ON app.candidate_build_receipts FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.candidate_build_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.candidate_build_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY candidate_build_intent_scope ON app.candidate_build_intents
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
ALTER TABLE app.candidate_build_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.candidate_build_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY candidate_build_receipt_scope ON app.candidate_build_receipts
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.candidate_build_intents, app.candidate_build_receipts
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.prepare_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid,
    p_build_id uuid, p_request_id uuid, p_request_hash bytea,
    p_base_sha text, p_tree_sha text, p_patch_sha256 text,
    p_toolchain text, p_build_command text, p_artifact_root text
) RETURNS TABLE (build_id uuid, build_status text, replayed boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_extension app.github_pr_extensions%%ROWTYPE;
        v_existing app.candidate_build_intents%%ROWTYPE;
BEGIN
    IF p_extension_id IS NULL OR p_build_id IS NULL OR p_request_id IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
       OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
       OR p_patch_sha256 IS NULL OR p_patch_sha256 !~ '^[0-9a-f]{64}$'
       OR p_toolchain IS DISTINCT FROM
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
       OR p_build_command IS DISTINCT FROM 'npm run build'
       OR p_artifact_root IS NULL OR p_artifact_root NOT IN ('.next', 'dist', '_site') THEN
        RAISE EXCEPTION 'invalid_candidate_build_input' USING ERRCODE = '22023';
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
    SELECT * INTO v_existing FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id
        AND intent.requested_by_user_id = v_authority.user_id
        AND intent.idempotency_key = p_request_id;
    IF FOUND THEN
        IF v_existing.site_id <> p_site_id OR v_existing.extension_id <> p_extension_id
           OR v_existing.request_hash <> p_request_hash THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'request_conflict'::text; RETURN;
        END IF;
    END IF;
    SELECT extension.* INTO v_extension FROM app.github_pr_extensions AS extension
      JOIN app.github_read_bindings AS binding
        ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
       AND binding.id = extension.binding_id AND binding.status = 'active'
     WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
       AND extension.id = p_extension_id AND extension.status = 'observed'
       AND extension.coverage = 'complete' AND extension.content_sha IS NOT NULL
       AND extension.framework IN ('nextjs', 'astro', 'eleventy')
       AND extension.repository_id = binding.repository_id
       AND extension.base_sha = p_base_sha AND extension.tree_sha = p_tree_sha
     FOR SHARE OF extension, binding;
    IF NOT FOUND OR NOT (
        (v_extension.framework = 'nextjs' AND p_artifact_root = '.next')
        OR (v_extension.framework = 'astro' AND p_artifact_root = 'dist')
        OR (v_extension.framework = 'eleventy' AND p_artifact_root = '_site')
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'extension_inactive'::text; RETURN;
    END IF;
    IF v_existing.id IS NOT NULL THEN
        RETURN QUERY SELECT v_existing.id, v_existing.status, true, 'prepared'::text; RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_build_intents AS intent
        WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
          AND intent.extension_id = p_extension_id
          AND intent.status IN ('prepared', 'dispatched')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'build_exists'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_build_intents (
        tenant_id, site_id, id, extension_id, requested_by_user_id, idempotency_key,
        request_hash, membership_epoch, site_epoch, recovery_generation,
        base_sha, tree_sha, patch_sha256, toolchain, build_command, artifact_root, status
    ) VALUES (v_authority.tenant_id, p_site_id, p_build_id, p_extension_id,
        v_authority.user_id, p_request_id, p_request_hash, v_authority.membership_epoch,
        v_authority.site_authorization_epoch, p_generation, p_base_sha, p_tree_sha,
        p_patch_sha256, p_toolchain, p_build_command, p_artifact_root, 'prepared');
    RETURN QUERY SELECT p_build_id, 'prepared'::text, false, 'prepared'::text;
END; $$;

CREATE FUNCTION control.dispatch_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_intent FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
        AND intent.id = p_build_id FOR UPDATE;
    IF NOT FOUND OR v_intent.requested_by_user_id <> v_authority.user_id
       OR v_intent.membership_epoch <> v_authority.membership_epoch
       OR v_intent.site_epoch <> v_authority.site_authorization_epoch
       OR v_intent.recovery_generation <> p_generation THEN
        RETURN 'build_not_authorized';
    END IF;
    IF v_intent.status <> 'prepared' THEN RETURN 'dispatch_unknown'; END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id)
       OR NOT EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
           JOIN app.github_read_bindings AS binding
             ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
            AND binding.id = extension.binding_id AND binding.status = 'active'
          WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
            AND extension.id = v_intent.extension_id AND extension.status = 'observed'
            AND extension.base_sha = v_intent.base_sha AND extension.tree_sha = v_intent.tree_sha) THEN
        RETURN 'extension_inactive';
    END IF;
    UPDATE app.candidate_build_intents SET status = 'dispatched',
        dispatched_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_build_id;
    RETURN 'dispatched';
END; $$;

CREATE FUNCTION control.finish_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid,
    p_exit_class text, p_exit_code integer, p_logs_sha256 text,
    p_log_bytes integer, p_artifacts jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
        v_item jsonb; v_total bigint := 0;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_intent FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
        AND intent.id = p_build_id FOR UPDATE;
    IF NOT FOUND OR v_intent.requested_by_user_id <> v_authority.user_id
       OR v_intent.membership_epoch <> v_authority.membership_epoch
       OR v_intent.site_epoch <> v_authority.site_authorization_epoch
       OR v_intent.recovery_generation <> p_generation THEN
        RETURN 'build_not_authorized';
    END IF;
    IF v_intent.status = 'completed' THEN
        IF EXISTS (SELECT 1 FROM app.candidate_build_receipts AS receipt
          WHERE receipt.tenant_id = v_authority.tenant_id AND receipt.site_id = p_site_id
            AND receipt.build_id = p_build_id AND receipt.exit_class = p_exit_class
            AND receipt.exit_code IS NOT DISTINCT FROM p_exit_code
            AND receipt.logs_sha256 = p_logs_sha256 AND receipt.log_bytes = p_log_bytes
            AND receipt.artifacts = p_artifacts) THEN RETURN 'completed'; END IF;
        RETURN 'receipt_conflict';
    END IF;
    IF v_intent.status <> 'dispatched' THEN RETURN 'dispatch_unknown'; END IF;
    IF p_exit_class IS NULL OR p_exit_class NOT IN (
        'passed', 'crash', 'timeout', 'oom', 'output_limit', 'policy_rejected')
       OR p_logs_sha256 IS NULL OR p_logs_sha256 !~ '^[0-9a-f]{64}$'
       OR p_log_bytes IS NULL OR p_log_bytes NOT BETWEEN 0 AND 65536
       OR p_artifacts IS NULL OR jsonb_typeof(p_artifacts) <> 'array'
       OR jsonb_array_length(p_artifacts) > 1000
       OR octet_length(p_artifacts::text) > 131072
       OR p_exit_code IS NOT NULL AND p_exit_code NOT BETWEEN 0 AND 255
       OR (p_exit_class = 'passed' AND (p_exit_code IS DISTINCT FROM 0
            OR jsonb_array_length(p_artifacts) = 0))
       OR (p_exit_class <> 'passed' AND jsonb_array_length(p_artifacts) <> 0) THEN
        RETURN 'invalid_receipt';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_artifacts) LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 3
           OR NOT (v_item ?& ARRAY['path', 'sha256', 'size'])
           OR jsonb_typeof(v_item->'path') <> 'string'
           OR jsonb_typeof(v_item->'sha256') <> 'string'
           OR jsonb_typeof(v_item->'size') <> 'number'
           OR length(v_item->>'path') NOT BETWEEN 1 AND 1024
           OR left(v_item->>'path', length(v_intent.artifact_root) + 1)
                <> v_intent.artifact_root || '/'
           OR position('..' in v_item->>'path') > 0
           OR (v_item->>'sha256') !~ '^[0-9a-f]{64}$'
           OR (v_item->>'size') !~ '^[0-9]{1,8}$' THEN
            RETURN 'invalid_receipt';
        END IF;
        v_total := v_total + (v_item->>'size')::bigint;
        IF v_total > 16777216 THEN RETURN 'invalid_receipt'; END IF;
    END LOOP;
    INSERT INTO app.candidate_build_receipts (
        tenant_id, site_id, build_id, base_sha, patch_sha256, toolchain,
        build_command, exit_class, exit_code, logs_sha256, log_bytes, artifacts
    ) VALUES (v_authority.tenant_id, p_site_id, p_build_id, v_intent.base_sha,
        v_intent.patch_sha256, v_intent.toolchain, v_intent.build_command,
        p_exit_class, p_exit_code, p_logs_sha256, p_log_bytes, p_artifacts);
    UPDATE app.candidate_build_intents SET status = 'completed',
        completed_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_build_id;
    RETURN 'completed';
END; $$;

CREATE FUNCTION control.read_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid
) RETURNS TABLE (
    build_status text, base_sha text, patch_sha256 text, toolchain text,
    build_command text, exit_class text, exit_code integer, logs_sha256 text,
    log_bytes integer, artifacts jsonb, outcome text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::jsonb, 'permission_denied'::text; RETURN;
    END IF;
    RETURN QUERY SELECT intent.status, intent.base_sha, intent.patch_sha256,
        intent.toolchain, intent.build_command, receipt.exit_class,
        receipt.exit_code, receipt.logs_sha256, receipt.log_bytes,
        receipt.artifacts, 'found'::text
      FROM app.candidate_build_intents AS intent
      LEFT JOIN app.candidate_build_receipts AS receipt
        ON receipt.tenant_id = intent.tenant_id AND receipt.site_id = intent.site_id
       AND receipt.build_id = intent.id
     WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
       AND intent.id = p_build_id AND intent.requested_by_user_id = v_authority.user_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::jsonb, 'build_not_found'::text;
    END IF;
END; $$;

REVOKE ALL ON FUNCTION control.prepare_candidate_build(
    bytea, uuid, text, uuid, uuid, uuid, bytea, text, text, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.dispatch_candidate_build(bytea, uuid, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_candidate_build(
    bytea, uuid, text, uuid, text, integer, text, integer, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_candidate_build(bytea, uuid, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_candidate_build(
    bytea, uuid, text, uuid, uuid, uuid, bytea, text, text, text, text, text, text)
TO signal_identity;
GRANT EXECUTE ON FUNCTION control.dispatch_candidate_build(bytea, uuid, text, uuid)
TO signal_identity;
GRANT EXECUTE ON FUNCTION control.finish_candidate_build(
    bytea, uuid, text, uuid, text, integer, text, integer, jsonb)
TO signal_identity;
GRANT EXECUTE ON FUNCTION control.read_candidate_build(bytea, uuid, text, uuid)
TO signal_identity;
