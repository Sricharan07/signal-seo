CREATE TABLE app.crawl_page_image_evidence (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    page_record_id uuid NOT NULL,
    body_sha256 bytea NOT NULL CHECK (octet_length(body_sha256) = 32),
    missing_alt_images jsonb NOT NULL CHECK (
        jsonb_typeof(missing_alt_images) = 'array'
        AND jsonb_array_length(missing_alt_images) <= 128
        AND octet_length(missing_alt_images::text) <= 262144
    ),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, page_record_id),
    FOREIGN KEY (tenant_id, site_id, page_record_id)
        REFERENCES app.crawl_page_records (tenant_id, site_id, id)
);
CREATE TRIGGER crawl_page_image_evidence_immutable BEFORE UPDATE OR DELETE
ON app.crawl_page_image_evidence FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.crawl_page_image_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_page_image_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_page_image_evidence_scope ON app.crawl_page_image_evidence
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.crawl_page_image_evidence FROM PUBLIC, signal_identity,
    signal_bootstrap, signal_api, signal_scheduler, signal_workflow,
    signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.record_crawl_image_evidence(
    p_tenant_id uuid, p_site_id uuid, p_page_id uuid, p_body_sha256 bytea,
    p_missing_alt_images jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_page app.crawl_page_records%%ROWTYPE;
        v_existing app.crawl_page_image_evidence%%ROWTYPE;
        v_item jsonb;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_page_id IS NULL
       OR p_body_sha256 IS NULL OR octet_length(p_body_sha256) <> 32
       OR p_missing_alt_images IS NULL OR jsonb_typeof(p_missing_alt_images) <> 'array'
       OR jsonb_array_length(p_missing_alt_images) > 128
       OR octet_length(p_missing_alt_images::text) > 262144 THEN
        RETURN 'invalid_image_evidence';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_missing_alt_images) LOOP
        IF jsonb_typeof(v_item) <> 'string'
           OR length(v_item #>> '{}') NOT BETWEEN 1 AND 2048 THEN
            RETURN 'invalid_image_evidence';
        END IF;
    END LOOP;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_page FROM app.crawl_page_records AS page
     WHERE page.tenant_id = p_tenant_id AND page.site_id = p_site_id
       AND page.id = p_page_id FOR SHARE;
    IF NOT FOUND OR v_page.body_sha256 <> p_body_sha256 THEN
        RETURN 'page_unavailable';
    END IF;
    SELECT * INTO v_existing FROM app.crawl_page_image_evidence AS evidence
     WHERE evidence.tenant_id = p_tenant_id AND evidence.site_id = p_site_id
       AND evidence.page_record_id = p_page_id FOR SHARE;
    IF FOUND THEN
        IF v_existing.body_sha256 = p_body_sha256
           AND v_existing.missing_alt_images = p_missing_alt_images THEN
            RETURN 'recorded';
        END IF;
        RETURN 'image_evidence_conflict';
    END IF;
    INSERT INTO app.crawl_page_image_evidence (
        tenant_id, site_id, page_record_id, body_sha256, missing_alt_images
    ) VALUES (p_tenant_id, p_site_id, p_page_id, p_body_sha256, p_missing_alt_images);
    RETURN 'recorded';
END; $$;

CREATE FUNCTION control.load_crawl_image_evidence(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid
) RETURNS TABLE (page_record_id uuid, missing_alt_images jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY SELECT evidence.page_record_id, evidence.missing_alt_images
      FROM app.crawl_page_image_evidence AS evidence
      JOIN app.crawl_page_records AS page
        ON page.tenant_id = evidence.tenant_id AND page.site_id = evidence.site_id
       AND page.id = evidence.page_record_id
     WHERE evidence.tenant_id = p_tenant_id AND evidence.site_id = p_site_id
       AND page.crawl_run_id = p_crawl_run_id AND page.body_sha256 = evidence.body_sha256
     ORDER BY evidence.page_record_id;
END; $$;
REVOKE ALL ON FUNCTION control.record_crawl_image_evidence(
    uuid, uuid, uuid, bytea, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.load_crawl_image_evidence(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_crawl_image_evidence(
    uuid, uuid, uuid, bytea, jsonb) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.load_crawl_image_evidence(uuid, uuid, uuid)
TO signal_crawl_ingest;

CREATE TABLE app.candidate_recipe_revisions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    extension_id uuid NOT NULL,
    build_id uuid NOT NULL,
    audit_report_id uuid NOT NULL,
    finding_id uuid NOT NULL,
    recipe_release_id uuid NOT NULL,
    release_content_hash bytea NOT NULL CHECK (octet_length(release_content_hash) = 32),
    base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
    patch_sha256 text NOT NULL CHECK (patch_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_manifest bytea NOT NULL CHECK (octet_length(canonical_manifest) BETWEEN 1 AND 32768),
    revision_sha256 bytea GENERATED ALWAYS AS (sha256(canonical_manifest)) STORED,
    created_by_user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    membership_epoch bigint NOT NULL CHECK (membership_epoch > 0),
    site_epoch bigint NOT NULL CHECK (site_epoch > 0),
    recovery_generation text NOT NULL CHECK (length(recovery_generation) BETWEEN 1 AND 128),
    sealed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, created_by_user_id, idempotency_key),
    UNIQUE (tenant_id, site_id, build_id),
    FOREIGN KEY (tenant_id, site_id, extension_id)
        REFERENCES app.github_pr_extensions (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, build_id)
        REFERENCES app.candidate_build_receipts (tenant_id, site_id, build_id),
    FOREIGN KEY (tenant_id, site_id, audit_report_id)
        REFERENCES app.crawl_audit_reports (tenant_id, site_id, id),
    FOREIGN KEY (recipe_release_id) REFERENCES control.recipe_releases (id),
    FOREIGN KEY (tenant_id, created_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);
CREATE TRIGGER candidate_recipe_revisions_immutable BEFORE UPDATE OR DELETE
ON app.candidate_recipe_revisions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.candidate_recipe_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.candidate_recipe_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY candidate_recipe_revision_scope ON app.candidate_recipe_revisions
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.candidate_recipe_revisions FROM PUBLIC, signal_identity,
    signal_bootstrap, signal_api, signal_scheduler, signal_workflow,
    signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.load_candidate_recipe_evidence(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_report_id uuid, p_finding_id uuid
) RETURNS TABLE (evidence jsonb, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_report app.crawl_audit_reports%%ROWTYPE;
        v_finding jsonb; v_page app.crawl_page_records%%ROWTYPE;
        v_source_id uuid; v_parent_url_id uuid; v_page_url text;
        v_origin text; v_images jsonb;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
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

CREATE FUNCTION control.seal_candidate_recipe_revision(
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
    IF p_revision_id IS NULL OR p_extension_id IS NULL OR p_build_id IS NULL
       OR p_report_id IS NULL OR p_finding_id IS NULL OR p_release_id IS NULL
       OR p_idempotency_key IS NULL OR p_release_hash IS NULL
       OR octet_length(p_release_hash) <> 32 OR p_canonical IS NULL
       OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768
       OR p_revision_hash IS NULL OR p_revision_hash <> sha256(p_canonical) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'invalid_revision'::text; RETURN;
    END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
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

REVOKE ALL ON FUNCTION control.load_candidate_recipe_evidence(
    bytea, uuid, text, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.seal_candidate_recipe_revision(
    bytea, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, bytea, uuid, bytea, bytea)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.load_candidate_recipe_evidence(
    bytea, uuid, text, uuid, uuid) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.seal_candidate_recipe_revision(
    bytea, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, bytea, uuid, bytea, bytea)
TO signal_identity;
