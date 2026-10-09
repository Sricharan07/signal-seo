CREATE FUNCTION control.weekly_read_github_read_binding(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid
) RETURNS TABLE (
    binding_id uuid, binding_status text, installation_id bigint, repository_owner text,
    repository_name text, base_branch text, content_path text, repository_id bigint,
    observed_full_name text, base_sha text, outcome text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('binding_id',p_binding_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
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

CREATE FUNCTION control.weekly_read_github_pr_extension(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid
) RETURNS TABLE (
    extension_id uuid, extension_status text, binding_id uuid, repository_id bigint,
    base_sha text, tree_sha text, framework text, content_format text, coverage text,
    content_sha text, marker_evidence jsonb, outcome text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('extension_id',p_extension_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
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

CREATE FUNCTION control.weekly_prepare_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid,
    p_build_id uuid, p_request_id uuid, p_request_hash bytea,
    p_base_sha text, p_tree_sha text, p_patch_sha256 text,
    p_toolchain text, p_build_command text, p_artifact_root text
) RETURNS TABLE (build_id uuid, build_status text, replayed boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_extension app.github_pr_extensions%%ROWTYPE;
        v_existing app.candidate_build_intents%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('extension_id',p_extension_id::text,'build_key',p_request_id::text));
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
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'workload' THEN
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

CREATE FUNCTION control.weekly_dispatch_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('build_id',p_build_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'workload' THEN RETURN 'permission_denied'; END IF;
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

CREATE FUNCTION control.weekly_finish_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid,
    p_exit_class text, p_exit_code integer, p_logs_sha256 text,
    p_log_bytes integer, p_artifacts jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
        v_item jsonb; v_total bigint := 0;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('build_id',p_build_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'workload' THEN RETURN 'permission_denied'; END IF;
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

CREATE FUNCTION control.weekly_read_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid
) RETURNS TABLE (
    build_status text, base_sha text, patch_sha256 text, toolchain text,
    build_command text, exit_class text, exit_code integer, logs_sha256 text,
    log_bytes integer, artifacts jsonb, outcome text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('build_id',p_build_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'workload' THEN
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

CREATE FUNCTION control.weekly_load_candidate_recipe_evidence(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_report_id uuid, p_finding_id uuid
) RETURNS TABLE (evidence jsonb, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_report app.crawl_audit_reports%%ROWTYPE;
        v_finding jsonb; v_page app.crawl_page_records%%ROWTYPE;
        v_source_id uuid; v_parent_url_id uuid; v_page_url text;
        v_origin text; v_images jsonb;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('report_id',p_report_id::text,'finding_id',p_finding_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'workload' THEN
        RETURN QUERY SELECT NULL::jsonb, 'permission_denied'::text; RETURN;
    END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::jsonb, 'site_not_verified'::text; RETURN;
    END IF;
    SELECT report.* INTO v_report FROM app.crawl_audit_reports AS report
      JOIN app.crawl_manifests AS manifest
        ON manifest.tenant_id = report.tenant_id AND manifest.site_id = report.site_id
       AND manifest.id = report.manifest_id
     WHERE report.tenant_id = v_authority.tenant_id AND report.site_id = p_site_id
       AND report.id = p_report_id AND report.detector_release_id =
           '0fc2a362-f51d-4b75-9bbf-22d456f29b40'::uuid
       AND report.manifest_sha256 = manifest.manifest_sha256
     FOR SHARE OF report, manifest;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::jsonb, 'report_unavailable'::text; RETURN;
    END IF;
    SELECT item.value INTO v_finding
      FROM jsonb_array_elements(v_report.findings) AS item(value)
     WHERE item.value->>'id' = p_finding_id::text;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::jsonb, 'finding_unavailable'::text; RETURN;
    END IF;
    v_source_id := (v_finding->>'source_id')::uuid;
    IF v_finding->>'source_kind' = 'page' THEN
        SELECT page.* INTO v_page FROM app.crawl_page_records AS page
         WHERE page.tenant_id = v_authority.tenant_id AND page.site_id = p_site_id
           AND page.crawl_run_id = v_report.crawl_run_id AND page.id = v_source_id;
    ELSIF v_finding->>'source_kind' = 'settlement'
          AND v_finding->>'key' = 'links.internal.not_found' THEN
        SELECT frontier.discovered_from_url_id INTO v_parent_url_id
          FROM app.crawl_frontier_settlements AS settlement
          JOIN app.crawl_frontier AS frontier
            ON frontier.tenant_id = settlement.tenant_id
           AND frontier.site_id = settlement.site_id
           AND frontier.id = settlement.frontier_id
         WHERE settlement.tenant_id = v_authority.tenant_id
           AND settlement.site_id = p_site_id
           AND settlement.crawl_run_id = v_report.crawl_run_id
           AND settlement.id = v_source_id AND settlement.terminal_state = 'http_error';
        SELECT page.* INTO v_page FROM app.crawl_page_records AS page
         WHERE page.tenant_id = v_authority.tenant_id AND page.site_id = p_site_id
           AND page.crawl_run_id = v_report.crawl_run_id
           AND page.url_id = v_parent_url_id
           AND page.internal_links ? (v_finding->>'resource_locator');
    END IF;
    IF v_page.id IS NULL THEN
        RETURN QUERY SELECT NULL::jsonb, 'source_unavailable'::text; RETURN;
    END IF;
    SELECT url.fetch_url INTO v_page_url FROM app.urls AS url
     WHERE url.tenant_id = v_authority.tenant_id AND url.site_id = p_site_id
       AND url.id = v_page.url_id;
    SELECT site.primary_origin INTO v_origin FROM app.sites AS site
     WHERE site.tenant_id = v_authority.tenant_id AND site.id = p_site_id;
    SELECT images.missing_alt_images INTO v_images
      FROM app.crawl_page_image_evidence AS images
     WHERE images.tenant_id = v_authority.tenant_id AND images.site_id = p_site_id
       AND images.page_record_id = v_page.id
       AND images.body_sha256 = v_page.body_sha256;
    IF v_page_url IS NULL OR v_origin IS NULL THEN
        RETURN QUERY SELECT NULL::jsonb, 'source_unavailable'::text; RETURN;
    END IF;
    evidence := jsonb_build_object(
        'report_id', v_report.id::text,
        'manifest_id', v_report.manifest_id::text,
        'manifest_sha256', encode(v_report.manifest_sha256, 'hex'),
        'detector_release_id', v_report.detector_release_id::text,
        'finding', v_finding,
        'site_origin', v_origin,
        'page_id', v_page.id::text,
        'page_url', v_page_url,
        'page_body_sha256', encode(v_page.body_sha256, 'hex'),
        'page_title', v_page.title,
        'page_description', v_page.meta_description,
        'page_canonical', v_page.canonical_url,
        'page_headings', v_page.headings,
        'page_parse_error_count', v_page.parse_error_count,
        'page_output_truncated', v_page.output_truncated,
        'page_internal_links', v_page.internal_links,
        'missing_alt_images', v_images
    );
    IF octet_length(evidence::text) > 131072 THEN
        RETURN QUERY SELECT NULL::jsonb, 'source_oversized'::text; RETURN;
    END IF;
    outcome := 'found';
    RETURN NEXT;
END; $$;

CREATE FUNCTION control.weekly_seal_candidate_recipe_revision(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_revision_id uuid, p_extension_id uuid, p_build_id uuid,
    p_report_id uuid, p_finding_id uuid, p_release_id uuid,
    p_release_hash bytea, p_idempotency_key uuid, p_canonical bytea,
    p_revision_hash bytea
) RETURNS TABLE (revision_id uuid, revision_sha256 bytea,
                 replayed boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_build app.candidate_build_intents%%ROWTYPE;
        v_receipt app.candidate_build_receipts%%ROWTYPE;
        v_release control.recipe_releases%%ROWTYPE;
        v_existing app.candidate_recipe_revisions%%ROWTYPE;
        v_report app.crawl_audit_reports%%ROWTYPE; v_manifest jsonb;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('revision_id',p_revision_id::text,'extension_id',p_extension_id::text,'build_id',p_build_id::text,'report_id',p_report_id::text,'finding_id',p_finding_id::text,'release_id',p_release_id::text,'revision_key',p_idempotency_key::text));
    IF p_revision_id IS NULL OR p_extension_id IS NULL OR p_build_id IS NULL
       OR p_report_id IS NULL OR p_finding_id IS NULL OR p_release_id IS NULL
       OR p_idempotency_key IS NULL OR p_release_hash IS NULL
       OR octet_length(p_release_hash) <> 32 OR p_canonical IS NULL
       OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768
       OR p_revision_hash IS NULL OR p_revision_hash <> sha256(p_canonical) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'invalid_revision'::text; RETURN;
    END IF;
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'workload' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'permission_denied'::text; RETURN;
    END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO v_existing FROM app.candidate_recipe_revisions AS revision
     WHERE revision.tenant_id = v_authority.tenant_id AND revision.site_id = p_site_id
       AND revision.created_by_user_id = v_authority.user_id
       AND revision.idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.id = p_revision_id AND v_existing.extension_id = p_extension_id
           AND v_existing.build_id = p_build_id AND v_existing.audit_report_id = p_report_id
           AND v_existing.finding_id = p_finding_id AND v_existing.recipe_release_id = p_release_id
           AND v_existing.release_content_hash = p_release_hash
           AND v_existing.canonical_manifest = p_canonical THEN
            RETURN QUERY SELECT v_existing.id, v_existing.revision_sha256,
                true, 'sealed'::text; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'revision_conflict'::text; RETURN;
    END IF;
    SELECT * INTO v_release FROM control.recipe_releases AS release
     WHERE release.id = p_release_id AND release.content_hash = p_release_hash FOR SHARE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM control.recipe_release_events AS event
         WHERE event.release_id = p_release_id
         ORDER BY event.sequence_number DESC LIMIT 1
    ) OR (SELECT event.status FROM control.recipe_release_events AS event
         WHERE event.release_id = p_release_id
         ORDER BY event.sequence_number DESC LIMIT 1) <> 'REVIEWED'
       OR v_release.canonical_body IS NULL
       OR convert_from(v_release.canonical_body, 'UTF8')::jsonb->>'delivery_mode'
            <> 'pull_request'
       OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones AS tombstone
           WHERE tombstone.target_kind = 'recipe_release'
             AND tombstone.target_id = p_release_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'release_unavailable'::text; RETURN;
    END IF;
    SELECT * INTO v_build FROM app.candidate_build_intents AS build
     WHERE build.tenant_id = v_authority.tenant_id AND build.site_id = p_site_id
       AND build.id = p_build_id AND build.extension_id = p_extension_id
       AND build.status = 'completed' AND build.requested_by_user_id = v_authority.user_id
       AND build.membership_epoch = v_authority.membership_epoch
       AND build.site_epoch = v_authority.site_authorization_epoch
       AND build.recovery_generation = p_generation;
    SELECT * INTO v_receipt FROM app.candidate_build_receipts AS receipt
     WHERE receipt.tenant_id = v_authority.tenant_id AND receipt.site_id = p_site_id
       AND receipt.build_id = p_build_id AND receipt.exit_class = 'passed';
    IF v_build.id IS NULL OR v_receipt.build_id IS NULL
       OR v_receipt.patch_sha256 <> v_build.patch_sha256
       OR v_build.patch_sha256 =
           'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
       OR NOT EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
           JOIN app.github_read_bindings AS binding
             ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
            AND binding.id = extension.binding_id AND binding.status = 'active'
          WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
            AND extension.id = p_extension_id AND extension.status = 'observed'
            AND extension.base_sha = v_build.base_sha
            AND extension.tree_sha = v_build.tree_sha) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'build_unavailable'::text; RETURN;
    END IF;
    SELECT * INTO v_report FROM app.crawl_audit_reports AS report
     WHERE report.tenant_id = v_authority.tenant_id AND report.site_id = p_site_id
       AND report.id = p_report_id AND report.detector_release_id =
           '0fc2a362-f51d-4b75-9bbf-22d456f29b40'::uuid;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_report.findings) AS item(value)
        WHERE item.value->>'id' = p_finding_id::text) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'finding_unavailable'::text; RETURN;
    END IF;
    BEGIN
        v_manifest := convert_from(p_canonical, 'UTF8')::jsonb;
    EXCEPTION WHEN character_not_in_repertoire OR invalid_text_representation THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'invalid_revision'::text; RETURN;
    END;
    IF jsonb_typeof(v_manifest) IS DISTINCT FROM 'object'
       OR v_manifest->>'site_id' IS DISTINCT FROM p_site_id::text
       OR v_manifest->>'extension_id' IS DISTINCT FROM p_extension_id::text
       OR v_manifest->>'build_id' IS DISTINCT FROM p_build_id::text
       OR v_manifest->>'audit_report_id' IS DISTINCT FROM p_report_id::text
       OR v_manifest->>'finding_id' IS DISTINCT FROM p_finding_id::text
       OR v_manifest->>'recipe_release_id' IS DISTINCT FROM p_release_id::text
       OR v_manifest->>'release_content_hash' IS DISTINCT FROM encode(p_release_hash, 'hex')
       OR v_manifest->>'base_sha' IS DISTINCT FROM v_build.base_sha
       OR v_manifest->>'patch_sha256' IS DISTINCT FROM v_build.patch_sha256
       OR v_manifest->>'source_path' IS DISTINCT FROM 'index.html'
       OR COALESCE(v_manifest->>'source_sha256', '') !~ '^[0-9a-f]{64}$'
       OR COALESCE(v_manifest->>'result_sha256', '') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(v_manifest->'patch') IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_manifest #> '{patch,offset}') IS DISTINCT FROM 'number'
       OR COALESCE(v_manifest #>> '{patch,offset}', '') !~ '^(0|[1-9][0-9]*)$'
       OR jsonb_typeof(v_manifest #> '{patch,before}') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_manifest #> '{patch,after}') IS DISTINCT FROM 'string'
       OR v_manifest #>> '{evidence,manifest_id}' IS DISTINCT FROM v_report.manifest_id::text
       OR v_manifest #>> '{evidence,manifest_sha256}' IS DISTINCT FROM
            encode(v_report.manifest_sha256, 'hex')
       OR v_manifest #>> '{evidence,finding,id}' IS DISTINCT FROM p_finding_id::text
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_report.findings) AS item(value)
           WHERE item.value->>'id' = p_finding_id::text
             AND item.value = v_manifest #> '{evidence,finding}')
       OR v_manifest #>> '{build_receipt,toolchain}' IS DISTINCT FROM v_receipt.toolchain
       OR v_manifest #>> '{build_receipt,command}' IS DISTINCT FROM v_receipt.build_command
       OR v_manifest #>> '{build_receipt,exit_class}' IS DISTINCT FROM 'passed'
       OR v_manifest #>> '{build_receipt,logs_sha256}' IS DISTINCT FROM v_receipt.logs_sha256
       OR v_manifest #> '{build_receipt,artifacts}' IS DISTINCT FROM v_receipt.artifacts
       OR v_manifest->>'approval_class' IS DISTINCT FROM 'owner_review'
       OR COALESCE(length(v_manifest->>'expected_impact'), 0) NOT BETWEEN 1 AND 500
       OR COALESCE(length(v_manifest->>'recovery_plan'), 0) NOT BETWEEN 1 AND 500
       OR NOT EXISTS (SELECT 1 FROM app.crawl_page_records AS page
           WHERE page.tenant_id = v_authority.tenant_id AND page.site_id = p_site_id
             AND page.crawl_run_id = v_report.crawl_run_id
             AND page.id::text = v_manifest #>> '{evidence,page_id}'
             AND encode(page.body_sha256, 'hex') = v_manifest->>'source_sha256') THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'invalid_revision'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_recipe_revisions (
        tenant_id, site_id, id, extension_id, build_id, audit_report_id, finding_id,
        recipe_release_id, release_content_hash, base_sha, patch_sha256,
        canonical_manifest, created_by_user_id, idempotency_key,
        membership_epoch, site_epoch, recovery_generation
    ) VALUES (v_authority.tenant_id, p_site_id, p_revision_id, p_extension_id,
        p_build_id, p_report_id, p_finding_id, p_release_id, p_release_hash,
        v_build.base_sha, v_build.patch_sha256, p_canonical, v_authority.user_id,
        p_idempotency_key, v_authority.membership_epoch,
        v_authority.site_authorization_epoch, p_generation);
    RETURN QUERY SELECT p_revision_id, p_revision_hash, false, 'sealed'::text;
END; $$;

CREATE FUNCTION control.weekly_read_authenticated_candidate_recipe_inbox(
    p_session_hash bytea, p_site_id uuid, p_generation text
) RETURNS TABLE (
    revision_id uuid, revision_sha256 text, canonical_manifest jsonb,
    sealed_at timestamptz, recipe_release_id uuid, release_content_hash text,
    base_sha text, patch_sha256 text, review_status text, decision_id uuid,
    decision text, decided_by_user_id uuid, decision_channel text,
    decided_at timestamptz, outcome text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object());
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;
    RETURN QUERY
    SELECT revision.id, encode(revision.revision_sha256, 'hex'),
           convert_from(revision.canonical_manifest, 'UTF8')::jsonb,
           revision.sealed_at, revision.recipe_release_id,
           encode(revision.release_content_hash, 'hex'), revision.base_sha,
           revision.patch_sha256,
           CASE
             WHEN decision.id IS NOT NULL THEN decision.decision
             WHEN EXISTS (
               SELECT 1 FROM app.candidate_recipe_revisions AS newer
                WHERE newer.tenant_id = revision.tenant_id AND newer.site_id = revision.site_id
                  AND newer.audit_report_id = revision.audit_report_id
                  AND newer.finding_id = revision.finding_id
                  AND (newer.sealed_at, newer.id) > (revision.sealed_at, revision.id)
             ) THEN 'superseded'
             WHEN NOT EXISTS (
               SELECT 1 FROM app.github_pr_extensions AS extension
                JOIN app.github_read_bindings AS binding
                  ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
                 AND binding.id = extension.binding_id AND binding.status = 'active'
                WHERE extension.tenant_id = revision.tenant_id AND extension.site_id = revision.site_id
                  AND extension.id = revision.extension_id AND extension.status = 'observed'
                  AND extension.base_sha = revision.base_sha
             ) THEN 'stale_base'
             ELSE 'pending'
           END,
           decision.id, decision.decision, decision.decided_by_user_id,
           decision.decision_channel, decision.decided_at, 'found'::text
      FROM app.candidate_recipe_revisions AS revision
      LEFT JOIN app.candidate_recipe_review_decisions AS decision
        ON decision.tenant_id = revision.tenant_id AND decision.site_id = revision.site_id
       AND decision.candidate_revision_id = revision.id
     WHERE revision.tenant_id = v_authority.tenant_id AND revision.site_id = p_site_id
       AND revision.id=(SELECT job.revision_id FROM app.weekly_delivery_workloads job WHERE job.handle_hash=p_session_hash)
     ORDER BY revision.sealed_at DESC, revision.id DESC
     LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, 'not_found'::text;
    END IF;
END; $$;

CREATE FUNCTION control.weekly_github_pr_operation_eligible(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid
) RETURNS TABLE (outcome text, tenant_id uuid, user_id uuid,
    membership_epoch bigint, site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('revision_id',p_revision_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'workload' OR NOT control.current_github_site_proof(
        v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT 'authority_denied'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT revision.* INTO v_revision FROM app.candidate_recipe_revisions revision
     WHERE revision.tenant_id = v_authority.tenant_id
       AND revision.site_id = p_site_id AND revision.id = p_revision_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'revision_unavailable'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT decision.* INTO v_decision FROM app.candidate_recipe_review_decisions decision
     WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
       AND decision.candidate_revision_id = p_revision_id;
    IF (v_decision.id IS NULL OR v_decision.decision <> 'approved'
       OR v_decision.revision_sha256 <> v_revision.revision_sha256
       OR v_decision.recovery_generation <> p_generation
       OR v_decision.membership_epoch <> v_authority.membership_epoch
       OR v_decision.site_authorization_epoch <> v_authority.site_authorization_epoch)
       AND NOT control.standing_dispatch_current(p_session_hash,p_site_id,p_generation) THEN
        RETURN QUERY SELECT 'decision_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_pr_operations operation
        WHERE operation.tenant_id=v_revision.tenant_id AND operation.site_id=p_site_id
          AND operation.candidate_revision_id=p_revision_id
          AND (operation.requested_by_user_id IS DISTINCT FROM v_authority.user_id
            OR (operation.authority_kind='owner_inbox'
                AND operation.decision_id IS DISTINCT FROM v_decision.id)
            OR (operation.authority_kind='standing_grant'
                AND NOT control.standing_dispatch_current(p_session_hash,p_site_id,p_generation)))) THEN
        RETURN QUERY SELECT 'operation_authority_changed'::text,NULL::uuid,NULL::uuid,
            NULL::bigint,NULL::bigint;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_recipe_revisions newer
       WHERE newer.tenant_id = v_revision.tenant_id AND newer.site_id = v_revision.site_id
         AND newer.audit_report_id = v_revision.audit_report_id
         AND newer.finding_id = v_revision.finding_id
         AND (newer.sealed_at, newer.id) > (v_revision.sealed_at, v_revision.id)) THEN
        RETURN QUERY SELECT 'revision_superseded'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.github_pr_extensions ext
       JOIN app.github_read_bindings binding ON binding.tenant_id = ext.tenant_id
          AND binding.site_id = ext.site_id AND binding.id = ext.binding_id
       WHERE ext.tenant_id = v_revision.tenant_id AND ext.site_id = v_revision.site_id
         AND ext.id = v_revision.extension_id AND ext.status = 'observed'
         AND ext.base_sha = v_revision.base_sha AND ext.coverage = 'complete'
         AND ext.repository_id = binding.repository_id AND ext.framework = 'eleventy'
         AND ext.content_format = 'html' AND binding.status = 'active'
         AND binding.protected AND binding.base_sha = v_revision.base_sha) THEN
        RETURN QUERY SELECT 'binding_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.candidate_build_receipts build
       WHERE build.tenant_id = v_revision.tenant_id AND build.site_id = v_revision.site_id
         AND build.build_id = v_revision.build_id AND build.exit_class = 'passed'
         AND build.base_sha = v_revision.base_sha
         AND build.patch_sha256 = v_revision.patch_sha256) THEN
        RETURN QUERY SELECT 'build_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM control.recipe_releases release
       WHERE release.id = v_revision.recipe_release_id
         AND release.content_hash = v_revision.release_content_hash
         AND (SELECT event.status FROM control.recipe_release_events event
              WHERE event.release_id = release.id
              ORDER BY event.sequence_number DESC LIMIT 1) = 'REVIEWED'
         AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones denial
              WHERE denial.target_kind = 'recipe_release'
                AND denial.target_id = release.id)) THEN
        RETURN QUERY SELECT 'release_inactive'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'eligible'::text, v_authority.tenant_id, v_authority.user_id,
        v_authority.membership_epoch, v_authority.site_authorization_epoch;
END; $$;

CREATE FUNCTION control.weekly_prepare_github_pr_operation(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_revision_id uuid, p_revision_sha256 bytea, p_operation_id uuid,
    p_intent_sha256 bytea
) RETURNS TABLE (operation_id uuid, operation_state text, operation_step text,
    canonical_manifest bytea, operation_created_at timestamptz, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
        v_extension app.github_pr_extensions%%ROWTYPE;
        v_existing app.github_pr_operations%%ROWTYPE;
        v_branch text;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('revision_id',p_revision_id::text,'operation_id',p_operation_id::text));
    IF p_operation_id IS NULL OR p_revision_id IS NULL
       OR octet_length(p_revision_sha256) <> 32 OR octet_length(p_intent_sha256) <> 32 THEN
        RAISE EXCEPTION 'invalid_pr_operation_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, p_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, v_eligible.outcome;
        RETURN;
    END IF;
    SELECT * INTO v_revision FROM app.candidate_recipe_revisions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id AND id = p_revision_id;
    IF v_revision.revision_sha256 <> p_revision_sha256 THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, 'revision_stale'::text;
        RETURN;
    END IF;
    SELECT * INTO v_decision FROM app.candidate_recipe_review_decisions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id
       AND candidate_revision_id = p_revision_id;
    SELECT * INTO v_extension FROM app.github_pr_extensions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id
       AND id = v_revision.extension_id;
    v_branch := 'signal/' || replace(p_operation_id::text, '-', '');
    SELECT * INTO v_existing FROM app.github_pr_operations
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id
       AND (id = p_operation_id OR candidate_revision_id = p_revision_id);
    IF FOUND THEN
        IF v_existing.id <> p_operation_id OR v_existing.candidate_revision_id <> p_revision_id
           OR v_existing.revision_sha256 <> p_revision_sha256
           OR v_existing.intent_sha256 <> p_intent_sha256
           OR v_existing.branch_name <> v_branch THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
                NULL::timestamptz, 'operation_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.state, v_existing.step,
            v_revision.canonical_manifest, v_existing.created_at, 'prepared'::text;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_pr_operations other
       WHERE other.tenant_id = v_eligible.tenant_id AND other.site_id = p_site_id
         AND other.state NOT IN ('opened', 'blocked')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, 'resource_conflict'::text;
        RETURN;
    END IF;
    INSERT INTO app.github_pr_operations (
        tenant_id, site_id, id, candidate_revision_id, revision_sha256, intent_sha256,
        decision_id, extension_id, binding_id, recipe_release_id, requested_by_user_id,
        recovery_generation, membership_epoch, site_epoch, base_sha,
        branch_name, step, state, authority_kind, standing_dispatch_id
    ) VALUES (v_eligible.tenant_id, p_site_id, p_operation_id, p_revision_id,
        p_revision_sha256, p_intent_sha256, v_decision.id, v_extension.id,
        v_extension.binding_id, v_revision.recipe_release_id, v_eligible.user_id,
        p_generation, v_eligible.membership_epoch, v_eligible.site_epoch,
        v_revision.base_sha, v_branch, 'tree', 'planned',
        CASE WHEN v_decision.id IS NULL THEN 'standing_grant' ELSE 'owner_inbox' END,
        CASE WHEN v_decision.id IS NULL THEN (SELECT a.id FROM app.standing_dispatch_authorizations a
            JOIN app.weekly_delivery_workloads w ON w.tenant_id=a.tenant_id AND w.site_id=a.site_id AND w.id=a.workload_id
            WHERE w.handle_hash=p_session_hash AND w.site_id=p_site_id) ELSE NULL END);
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind)
    VALUES (v_eligible.tenant_id, p_site_id, gen_random_uuid(), p_operation_id, 'tree', 'planned');
    RETURN QUERY SELECT p_operation_id, 'planned'::text, 'tree'::text,
        v_revision.canonical_manifest, transaction_timestamp(), 'prepared'::text;
END; $$;

CREATE FUNCTION control.weekly_acknowledge_github_pr_intent(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid,
    p_journal_generation uuid, p_position bigint, p_body_hash bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_operation.tenant_id <> v_eligible.tenant_id OR p_journal_generation IS NULL
       OR p_position < 1 OR octet_length(p_body_hash) <> 32 THEN
        RETURN 'journal_invalid';
    END IF;
    IF v_operation.journal_generation IS NOT NULL THEN
        IF (v_operation.journal_generation, v_operation.journal_position,
            v_operation.journal_body_hash) = (p_journal_generation, p_position, p_body_hash) THEN
            RETURN 'acknowledged';
        END IF;
        RETURN 'journal_conflict';
    END IF;
    UPDATE app.github_pr_operations SET journal_generation = p_journal_generation,
        journal_position = p_position, journal_body_hash = p_body_hash,
        updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        v_operation.step, 'journal_acknowledged', p_body_hash);
    RETURN 'acknowledged';
END; $$;

CREATE FUNCTION control.weekly_claim_github_pr_operation(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid
) RETURNS TABLE (fence bigint, operation_state text, operation_step text, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'operation_unavailable'::text;
        RETURN;
    END IF;
    IF v_operation.state IN ('dispatching','outcome_unknown') THEN
        SELECT * INTO v_eligible FROM control.weekly_github_pr_reconciliation_eligible(
            p_session_hash,p_site_id,p_generation,v_operation.candidate_revision_id);
    ELSE
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    END IF;
    IF v_eligible.outcome <> 'eligible' OR v_eligible.tenant_id <> v_operation.tenant_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, v_eligible.outcome;
        RETURN;
    END IF;
    IF v_operation.journal_generation IS NULL OR p_worker_id IS NULL THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'journal_unavailable'::text;
        RETURN;
    END IF;
    IF v_operation.lease_until > transaction_timestamp()
       AND v_operation.lease_holder <> p_worker_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'lease_held'::text;
        RETURN;
    END IF;
    IF v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_until <= transaction_timestamp() THEN
        UPDATE app.github_pr_operations SET lease_holder = p_worker_id,
            lease_fence = lease_fence + 1,
            lease_until = transaction_timestamp() + interval '60 seconds',
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id
         RETURNING lease_fence INTO v_operation.lease_fence;
        INSERT INTO app.github_pr_operation_events
            (tenant_id, site_id, id, operation_id, step, event_kind)
        VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
            v_operation.step, 'lease_claimed');
    END IF;
    RETURN QUERY SELECT v_operation.lease_fence, v_operation.state,
        v_operation.step, 'claimed'::text;
END; $$;

CREATE FUNCTION control.weekly_bind_github_pr_operation_tree(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid,
    p_tree_sha text, p_commit_sha text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.journal_generation IS NULL
       OR p_tree_sha !~ '^[0-9a-f]{40}$' OR p_commit_sha !~ '^[0-9a-f]{40}$' THEN
        RETURN 'tree_binding_denied';
    END IF;
    IF v_operation.expected_tree_sha IS NOT NULL THEN
        IF v_operation.expected_tree_sha = p_tree_sha
           AND v_operation.expected_commit_sha = p_commit_sha THEN RETURN 'bound'; END IF;
        RETURN 'tree_binding_conflict';
    END IF;
    IF v_operation.state <> 'planned' OR v_operation.step <> 'tree' THEN
        RETURN 'tree_binding_denied';
    END IF;
    UPDATE app.github_pr_operations SET expected_tree_sha = p_tree_sha,
        expected_commit_sha = p_commit_sha, updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    RETURN 'bound';
END; $$;

CREATE FUNCTION control.weekly_github_pr_dispatch_permit(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.journal_generation IS NULL
       OR v_operation.expected_tree_sha IS NULL
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR v_operation.step <> p_step OR v_operation.state <> 'dispatching' THEN
        RETURN 'permit_denied';
    END IF;
    RETURN 'permitted';
END; $$;

CREATE FUNCTION control.weekly_begin_github_pr_step(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.journal_generation IS NULL
       OR v_operation.expected_tree_sha IS NULL
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR v_operation.step <> p_step THEN RETURN 'permit_denied'; END IF;
    IF v_operation.state IN ('dispatching', 'outcome_unknown') THEN RETURN 'reconcile_required'; END IF;
    IF v_operation.state NOT IN ('planned', 'ready') THEN RETURN 'operation_closed'; END IF;
    UPDATE app.github_pr_operations SET state = 'dispatching', updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, 'dispatching');
    RETURN 'dispatching';
END; $$;

CREATE FUNCTION control.weekly_finish_github_pr_step(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text,
    p_result text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL,
    p_pr_url text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.github_pr_operations%%ROWTYPE; v_next text;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    IF control.weekly_github_pr_dispatch_permit(p_session_hash, p_site_id, p_generation,
        p_operation_id, p_worker_id, p_fence, p_step) <> 'permitted'
       OR octet_length(p_evidence_sha256) <> 32
       OR p_result NOT IN ('completed', 'outcome_unknown', 'blocked') THEN
        RETURN 'permit_denied';
    END IF;
    IF p_result = 'completed' THEN
        v_next := CASE p_step WHEN 'tree' THEN 'commit' WHEN 'commit' THEN 'branch'
            WHEN 'branch' THEN 'pr' WHEN 'pr' THEN 'done' END;
        IF v_next IS NULL OR (p_step = 'pr' AND (p_pr_number < 1 OR p_pr_url !~
            '^https://github[.]com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'))
           OR (p_step <> 'pr' AND (p_pr_number IS NOT NULL OR p_pr_url IS NOT NULL)) THEN
            RETURN 'result_invalid';
        END IF;
        UPDATE app.github_pr_operations SET step = v_next,
            state = CASE WHEN v_next = 'done' THEN 'opened' ELSE 'ready' END,
            pr_number = p_pr_number, pr_url = p_pr_url,
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    ELSE
        UPDATE app.github_pr_operations SET state = p_result,
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    END IF;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, p_result, p_evidence_sha256);
    RETURN p_result;
END; $$;

CREATE FUNCTION control.weekly_reconcile_github_pr_step(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text,
    p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL, p_pr_url text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.github_pr_operations%%ROWTYPE; v_eligible record; v_next text;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_reconciliation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id OR v_operation.step <> p_step
       OR v_operation.state NOT IN ('dispatching', 'outcome_unknown')
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR octet_length(p_evidence_sha256) <> 32 THEN RETURN 'permit_denied'; END IF;
    v_next := CASE p_step WHEN 'tree' THEN 'commit' WHEN 'commit' THEN 'branch'
        WHEN 'branch' THEN 'pr' WHEN 'pr' THEN 'done' END;
    IF v_next IS NULL OR (p_step = 'pr' AND (p_pr_number < 1 OR p_pr_url !~
        '^https://github[.]com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'))
       OR (p_step <> 'pr' AND (p_pr_number IS NOT NULL OR p_pr_url IS NOT NULL)) THEN
        RETURN 'result_invalid';
    END IF;
    UPDATE app.github_pr_operations SET step = v_next,
        state = CASE WHEN v_next = 'done' THEN 'opened' ELSE 'ready' END,
        pr_number = p_pr_number, pr_url = p_pr_url, updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, 'reconciled', p_evidence_sha256);
    RETURN 'reconciled';
END; $$;

CREATE FUNCTION control.weekly_read_authenticated_github_pr_operations(
    p_session_hash bytea, p_site_id uuid, p_generation text
) RETURNS TABLE (operation_id uuid, candidate_revision_id uuid, revision_sha256 text,
    branch_name text, base_sha text, head_sha text, expected_tree_sha text,
    state text, step text, pr_number bigint, pr_url text,
    journal_generation uuid, journal_position bigint, journal_body_hash text,
    created_at timestamptz, updated_at timestamptz, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object());
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;
    RETURN QUERY SELECT op.id, op.candidate_revision_id, encode(op.revision_sha256, 'hex'),
        op.branch_name, op.base_sha, op.expected_commit_sha, op.expected_tree_sha,
        op.state, op.step, op.pr_number, op.pr_url, op.journal_generation,
        op.journal_position, encode(op.journal_body_hash, 'hex'),
        op.created_at, op.updated_at, 'found'::text
      FROM app.github_pr_operations op
     WHERE op.tenant_id = v_authority.tenant_id AND op.site_id = p_site_id
       AND op.id=(SELECT job.operation_id FROM app.weekly_delivery_workloads job WHERE job.handle_hash=p_session_hash)
     ORDER BY op.created_at DESC, op.id DESC LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, 'not_found'::text;
    END IF;
END; $$;

CREATE FUNCTION control.weekly_github_delivery_read_authority(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid
) RETURNS TABLE (outcome text, tenant_id uuid, membership_epoch bigint, site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'workload' OR NOT control.current_github_site_proof(
        v_authority.tenant_id, p_site_id) OR NOT EXISTS (
        SELECT 1 FROM app.github_pr_operations operation
        JOIN app.github_read_bindings binding ON binding.tenant_id = operation.tenant_id
          AND binding.site_id = operation.site_id AND binding.id = operation.binding_id
        JOIN app.github_pr_extensions extension ON extension.tenant_id = operation.tenant_id
          AND extension.site_id = operation.site_id AND extension.id = operation.extension_id
        WHERE operation.tenant_id = v_authority.tenant_id AND operation.site_id = p_site_id
          AND operation.id = p_operation_id AND operation.state = 'opened'
          AND binding.status = 'active' AND binding.repository_id = extension.repository_id
          AND extension.binding_id = binding.id) THEN
        RETURN QUERY SELECT 'observation_unavailable'::text, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'authorized'::text, v_authority.tenant_id,
        v_authority.membership_epoch, v_authority.site_authorization_epoch;
END; $$;

CREATE FUNCTION control.weekly_begin_github_delivery_observation(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid,
    p_attempt_id uuid, p_environment text, p_actor_id bigint
) RETURNS TABLE (canonical_manifest bytea, canonical_receipt bytea, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_previous app.github_delivery_attempts%%ROWTYPE; v_sequence integer;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    IF p_attempt_id IS NULL OR p_environment IS NULL OR p_environment !~
       '^[A-Za-z0-9][A-Za-z0-9_. /-]{0,99}$' OR p_actor_id IS NULL OR p_actor_id < 1 THEN
        RAISE EXCEPTION 'invalid_delivery_observation' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.weekly_github_delivery_read_authority(
        p_session_hash, p_site_id, p_generation, p_operation_id);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::bytea, NULL::bytea, v_authority.outcome; RETURN;
    END IF;
    PERFORM 1 FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    IF FOUND THEN
        IF v_attempt.operation_id <> p_operation_id OR v_attempt.environment <> p_environment
           OR v_attempt.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::bytea, receipt.canonical_receipt, 'completed'::text
          FROM app.github_delivery_receipts receipt WHERE receipt.tenant_id = v_authority.tenant_id
           AND receipt.site_id = p_site_id AND receipt.attempt_id = p_attempt_id;
        IF NOT FOUND THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'outcome_unknown'::text;
        END IF;
        RETURN;
    END IF;
    SELECT * INTO v_previous FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND operation_id = p_operation_id ORDER BY sequence_number DESC LIMIT 1;
    IF FOUND THEN
        IF v_previous.environment <> p_environment OR v_previous.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        IF v_previous.sequence_number >= 24 THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_budget_exhausted'::text; RETURN;
        END IF;
        IF v_previous.next_observe_at > transaction_timestamp()
           OR (v_previous.state = 'dispatching' AND v_previous.expires_at > transaction_timestamp()) THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_backoff'::text; RETURN;
        END IF;
        UPDATE app.github_delivery_attempts SET state = 'outcome_unknown'
         WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id
           AND id = v_previous.id AND state = 'dispatching';
    END IF;
    v_sequence := coalesce(v_previous.sequence_number, 0) + 1;
    INSERT INTO app.github_delivery_attempts
      (tenant_id, site_id, id, operation_id, sequence_number, environment, deployment_actor_id,
       recovery_generation, membership_epoch, site_epoch, state, expires_at, next_observe_at)
    VALUES (v_authority.tenant_id, p_site_id, p_attempt_id, p_operation_id, v_sequence,
      p_environment, p_actor_id, p_generation, v_authority.membership_epoch, v_authority.site_epoch,
      'dispatching', transaction_timestamp() + interval '300 seconds',
      transaction_timestamp() + make_interval(secs => least(3600, 30 * power(2, least(v_sequence - 1, 7)))::integer));
    RETURN QUERY SELECT revision.canonical_manifest, NULL::bytea, 'dispatching'::text
      FROM app.github_pr_operations operation JOIN app.candidate_recipe_revisions revision
      ON revision.tenant_id = operation.tenant_id AND revision.site_id = operation.site_id
        AND revision.id = operation.candidate_revision_id
     WHERE operation.tenant_id = v_authority.tenant_id AND operation.site_id = p_site_id
       AND operation.id = p_operation_id;
END; $$;

CREATE FUNCTION control.weekly_github_delivery_read_permit(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('attempt_id',p_attempt_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    IF NOT FOUND THEN RETURN 'observation_unavailable'; END IF;
    SELECT * INTO v_authority FROM control.weekly_github_delivery_read_authority(
        p_session_hash, p_site_id, p_generation, v_attempt.operation_id);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_attempt.state <> 'dispatching' OR v_attempt.expires_at <= transaction_timestamp()
      OR v_attempt.recovery_generation <> p_generation
      OR v_attempt.membership_epoch <> v_authority.membership_epoch
      OR v_attempt.site_epoch <> v_authority.site_epoch THEN RETURN 'observation_stale'; END IF;
    RETURN 'permitted';
END; $$;

CREATE FUNCTION control.weekly_finish_github_delivery_observation(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid,
    p_canonical_receipt bytea, p_receipt_sha256 bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_operation app.github_pr_operations%%ROWTYPE; v_receipt jsonb; v_manifest jsonb;
        v_existing app.github_delivery_receipts%%ROWTYPE; v_item jsonb; v_egress app.egress_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('attempt_id',p_attempt_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'observation_unavailable'; END IF;
    SELECT * INTO v_existing FROM app.github_delivery_receipts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND attempt_id = p_attempt_id;
    IF FOUND THEN
        IF v_existing.canonical_receipt = p_canonical_receipt AND v_existing.receipt_sha256 = p_receipt_sha256
          THEN RETURN 'completed'; END IF;
        RETURN 'receipt_conflict';
    END IF;
    IF control.weekly_github_delivery_read_permit(p_session_hash, p_site_id, p_generation, p_attempt_id)
        <> 'permitted' THEN RETURN 'observation_stale'; END IF;
    IF p_canonical_receipt IS NULL OR octet_length(p_canonical_receipt) NOT BETWEEN 2 AND 65536
       OR p_receipt_sha256 IS DISTINCT FROM sha256(p_canonical_receipt) THEN
        RETURN 'receipt_invalid';
    END IF;
    SELECT * INTO v_operation FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = v_attempt.operation_id;
    SELECT convert_from(canonical_manifest, 'UTF8')::jsonb INTO v_manifest
      FROM app.candidate_recipe_revisions WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_site_id AND id = v_operation.candidate_revision_id;
    v_receipt := convert_from(p_canonical_receipt, 'UTF8')::jsonb;
    IF v_receipt->>'schema_version' IS DISTINCT FROM '1'
      OR v_receipt->>'site_id' IS DISTINCT FROM p_site_id::text
      OR v_receipt->>'attempt_id' IS DISTINCT FROM p_attempt_id::text
      OR v_receipt->>'operation_id' IS DISTINCT FROM v_operation.id::text
      OR v_receipt->>'revision_sha256' IS DISTINCT FROM encode(v_operation.revision_sha256, 'hex')
      OR v_receipt->>'environment' IS DISTINCT FROM v_attempt.environment
      OR v_receipt->>'deployment_actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
      OR coalesce(v_receipt->>'outcome', '') NOT IN ('verified', 'not_yet_deployed', 'inconclusive', 'regressed')
      OR coalesce(v_receipt->'provider'->>'stage', '') NOT IN ('pr_opened', 'checks', 'merged', 'deployed')
      OR v_receipt->>'recovery_plan' IS DISTINCT FROM v_manifest->>'recovery_plan'
      OR v_receipt->>'delivery_certified' IS DISTINCT FROM 'false'
      OR jsonb_typeof(v_receipt->'provider_evidence') IS DISTINCT FROM 'array'
      OR jsonb_array_length(v_receipt->'provider_evidence') > 20 THEN RETURN 'receipt_invalid'; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_receipt->'provider_evidence') LOOP
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_item->>'egress_operation_id')::uuid;
        IF NOT FOUND OR v_egress.purpose <> 'connector' OR v_egress.method <> 'GET'
          OR v_egress.egress_profile <> 'github_rest'
          OR v_egress.origin <> 'https://api.github.com' OR v_egress.network_outcome <> 'fetched'
          OR v_egress.request_url IS DISTINCT FROM v_item->>'url'
          OR v_egress.http_status::text IS DISTINCT FROM v_item->>'http_status'
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_item->>'body_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'provider_evidence_invalid'; END IF;
    END LOOP;
    IF v_receipt->'provider'->>'stage' <> 'pr_opened'
       AND jsonb_array_length(v_receipt->'provider_evidence') < 4 THEN RETURN 'provider_evidence_invalid'; END IF;
    IF v_receipt->'provider'->>'stage' = 'deployed' THEN
        IF v_receipt->'provider'->>'merged_tree_sha' IS DISTINCT FROM v_operation.expected_tree_sha
          OR v_receipt->'provider'->'deployment'->>'sha' IS DISTINCT FROM v_receipt->'provider'->>'merged_sha'
          OR v_receipt->'provider'->'deployment'->>'environment' IS DISTINCT FROM v_attempt.environment
          OR v_receipt->'provider'->'deployment'->>'actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
          OR v_receipt->'provider'->'deployment'->>'environment_url' IS NULL
          OR v_receipt->'provider'->'deployment'->>'environment_url' NOT IN
            (v_manifest->'evidence'->>'site_origin', v_manifest->'evidence'->>'page_url')
          OR jsonb_array_length(v_receipt->'provider_evidence') < 7 THEN RETURN 'deployment_identity_invalid'; END IF;
    END IF;
    IF v_receipt->>'outcome' = 'verified' THEN
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_receipt->>'live_egress_operation_id')::uuid;
        IF NOT FOUND OR v_receipt->'provider'->>'stage' IS DISTINCT FROM 'deployed'
          OR v_receipt->'live'->>'outcome' IS DISTINCT FROM 'verified'
          OR v_receipt->'live'->>'matched' IS DISTINCT FROM 'true'
          OR v_receipt->'live'->>'fetched_sha256' IS DISTINCT FROM v_manifest->>'result_sha256'
          OR v_egress.purpose <> 'crawl' OR v_egress.method <> 'GET' OR v_egress.credentialed
          OR v_egress.egress_profile <> 'crawl_page'
          OR v_egress.request_url IS DISTINCT FROM v_manifest->'evidence'->>'page_url'
          OR v_egress.state <> 'observed' OR v_egress.network_outcome <> 'fetched' OR v_egress.http_status <> 200
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_receipt->'live'->>'fetched_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'live_evidence_invalid'; END IF;
    END IF;
    INSERT INTO app.github_delivery_receipts (tenant_id, site_id, attempt_id, operation_id,
      canonical_receipt, receipt_sha256) VALUES (v_authority.tenant_id, p_site_id, p_attempt_id,
      v_operation.id, p_canonical_receipt, p_receipt_sha256);
    UPDATE app.github_delivery_attempts SET state = 'completed' WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    RETURN 'completed';
END; $$;

CREATE FUNCTION control.weekly_read_authenticated_github_delivery_observations(
    p_session_hash bytea, p_site_id uuid, p_generation text
) RETURNS TABLE (attempt_id uuid, operation_id uuid, canonical_receipt bytea,
    receipt_sha256 text, attempt_state text, observed_at timestamptz,
    next_observe_at timestamptz, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object());
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::bytea, NULL::text, NULL::text,
          NULL::timestamptz, NULL::timestamptz, v_authority.outcome; RETURN;
    END IF;
    RETURN QUERY SELECT DISTINCT ON (attempt.operation_id) attempt.id, attempt.operation_id,
      receipt.canonical_receipt, encode(receipt.receipt_sha256, 'hex'),
      CASE WHEN attempt.state = 'dispatching' AND attempt.expires_at <= transaction_timestamp()
        THEN 'outcome_unknown' ELSE attempt.state END,
      coalesce(receipt.recorded_at, attempt.created_at), attempt.next_observe_at, 'found'::text
      FROM app.github_delivery_attempts attempt LEFT JOIN app.github_delivery_receipts receipt
        ON receipt.tenant_id = attempt.tenant_id AND receipt.site_id = attempt.site_id
          AND receipt.attempt_id = attempt.id
     WHERE attempt.tenant_id = v_authority.tenant_id AND attempt.site_id = p_site_id AND attempt.operation_id=(SELECT job.operation_id FROM app.weekly_delivery_workloads job WHERE job.handle_hash=p_session_hash)
     ORDER BY attempt.operation_id, attempt.sequence_number DESC LIMIT 50;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::bytea, NULL::text,
       NULL::text, NULL::timestamptz, NULL::timestamptz, 'not_found'::text; END IF;
END; $$;

REVOKE ALL ON FUNCTION control.weekly_read_github_read_binding(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_github_read_binding(bytea,uuid,text,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_read_github_pr_extension(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_github_pr_extension(bytea,uuid,text,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_dispatch_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_dispatch_candidate_build(bytea,uuid,text,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_read_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_candidate_build(bytea,uuid,text,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_load_candidate_recipe_evidence(bytea,uuid,text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_load_candidate_recipe_evidence(bytea,uuid,text,uuid,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_seal_candidate_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_seal_candidate_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_read_authenticated_candidate_recipe_inbox(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_authenticated_candidate_recipe_inbox(bytea,uuid,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_github_pr_operation_eligible(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_github_pr_operation_eligible(bytea,uuid,text,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_acknowledge_github_pr_intent(bytea,uuid,text,uuid,uuid,bigint,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_acknowledge_github_pr_intent(bytea,uuid,text,uuid,uuid,bigint,bytea) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_claim_github_pr_operation(bytea,uuid,text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_claim_github_pr_operation(bytea,uuid,text,uuid,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_bind_github_pr_operation_tree(bytea,uuid,text,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_bind_github_pr_operation_tree(bytea,uuid,text,uuid,text,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_github_pr_dispatch_permit(bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_github_pr_dispatch_permit(bytea,uuid,text,uuid,uuid,bigint,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_begin_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_begin_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_finish_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_finish_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_reconcile_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_reconcile_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_read_authenticated_github_pr_operations(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_authenticated_github_pr_operations(bytea,uuid,text) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_github_delivery_read_authority(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_github_delivery_read_authority(bytea,uuid,text,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_begin_github_delivery_observation(bytea,uuid,text,uuid,uuid,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_begin_github_delivery_observation(bytea,uuid,text,uuid,uuid,text,bigint) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_github_delivery_read_permit(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_github_delivery_read_permit(bytea,uuid,text,uuid) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_finish_github_delivery_observation(bytea,uuid,text,uuid,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_finish_github_delivery_observation(bytea,uuid,text,uuid,bytea,bytea) TO signal_workflow;
REVOKE ALL ON FUNCTION control.weekly_read_authenticated_github_delivery_observations(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_authenticated_github_delivery_observations(bytea,uuid,text) TO signal_workflow;
