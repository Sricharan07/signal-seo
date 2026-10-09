-- Builds are not authority. Keep the original owner/proof/epoch gate, allowing a
-- nonempty Astro patch only in the fixed offline build profile.
CREATE OR REPLACE FUNCTION control.prepare_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid,
    p_build_id uuid, p_request_id uuid, p_request_hash bytea,
    p_base_sha text, p_tree_sha text, p_patch_sha256 text,
    p_toolchain text, p_build_command text, p_artifact_root text
) RETURNS TABLE (build_id uuid, build_status text, replayed boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE a record; e app.github_pr_extensions%%ROWTYPE;
        existing app.candidate_build_intents%%ROWTYPE;
BEGIN
    IF p_extension_id IS NULL OR p_build_id IS NULL OR p_request_id IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
       OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
       OR p_patch_sha256 IS NULL OR p_patch_sha256 !~ '^[0-9a-f]{64}$'
       OR p_toolchain IS DISTINCT FROM
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
       OR p_build_command IS DISTINCT FROM 'npm run build'
       OR p_artifact_root IS NULL OR NOT (p_artifact_root IN ('.next','_site')
           OR control.valid_astro_output_directory(p_artifact_root)) THEN
        RAISE EXCEPTION 'invalid_candidate_build_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF a.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,a.outcome; RETURN;
    END IF;
    IF a.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'permission_denied'::text; RETURN;
    END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site_id FOR UPDATE;
    IF NOT control.current_github_site_proof(a.tenant_id,p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO existing FROM app.candidate_build_intents
      WHERE tenant_id=a.tenant_id AND requested_by_user_id=a.user_id AND idempotency_key=p_request_id;
    IF FOUND AND (existing.site_id<>p_site_id OR existing.extension_id<>p_extension_id
        OR existing.request_hash<>p_request_hash) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'request_conflict'::text; RETURN;
    END IF;
    SELECT ext.* INTO e FROM app.github_pr_extensions ext
      JOIN app.github_read_bindings b ON b.tenant_id=ext.tenant_id AND b.site_id=ext.site_id
        AND b.id=ext.binding_id AND b.status='active'
      WHERE ext.tenant_id=a.tenant_id AND ext.site_id=p_site_id AND ext.id=p_extension_id
        AND ext.status='observed' AND ext.coverage='complete' AND ext.content_sha IS NOT NULL
        AND ext.framework IN ('nextjs','astro','eleventy') AND ext.repository_id=b.repository_id
        AND ext.base_sha=p_base_sha AND ext.tree_sha=p_tree_sha FOR SHARE OF ext,b;
    IF NOT FOUND OR NOT (
        (e.framework='nextjs' AND p_artifact_root='.next')
        OR (e.framework='astro' AND control.valid_astro_output_directory(p_artifact_root))
        OR (e.framework='eleventy' AND p_artifact_root='_site')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'extension_inactive'::text; RETURN;
    END IF;
    IF existing.id IS NOT NULL THEN
        RETURN QUERY SELECT existing.id,existing.status,true,'prepared'::text; RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id
        AND site_id=p_site_id AND extension_id=p_extension_id AND status IN ('prepared','dispatched')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'build_exists'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_build_intents (
        tenant_id,site_id,id,extension_id,requested_by_user_id,idempotency_key,request_hash,
        membership_epoch,site_epoch,recovery_generation,base_sha,tree_sha,patch_sha256,
        toolchain,build_command,artifact_root,status)
    VALUES (a.tenant_id,p_site_id,p_build_id,p_extension_id,a.user_id,p_request_id,p_request_hash,
        a.membership_epoch,a.site_authorization_epoch,p_generation,p_base_sha,p_tree_sha,
        p_patch_sha256,p_toolchain,p_build_command,p_artifact_root,'prepared');
    RETURN QUERY SELECT p_build_id,'prepared'::text,false,'prepared'::text;
END; $$;

CREATE TABLE app.candidate_built_html (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,build_id uuid NOT NULL,
    artifact_id uuid NOT NULL,pages_sha256 bytea NOT NULL CHECK(octet_length(pages_sha256)=32),
    PRIMARY KEY(tenant_id,site_id,build_id),
    FOREIGN KEY(tenant_id,site_id,build_id) REFERENCES app.candidate_dependency_receipts(tenant_id,site_id,build_id),
    FOREIGN KEY(tenant_id,site_id,artifact_id,pages_sha256) REFERENCES app.artifacts(tenant_id,site_id,id,sha256)
);
CREATE TABLE app.astro_candidate_impacts (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,revision_id uuid NOT NULL,
    baseline_build_id uuid NOT NULL,approval_class text NOT NULL CHECK(approval_class IN ('A2','A4')),
    canonical_scope bytea NOT NULL CHECK(octet_length(canonical_scope) BETWEEN 1 AND 2097152),
    scope_sha256 bytea GENERATED ALWAYS AS (sha256(canonical_scope)) STORED,
    PRIMARY KEY(tenant_id,site_id,revision_id),
    FOREIGN KEY(tenant_id,site_id,revision_id) REFERENCES app.candidate_recipe_revisions(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,baseline_build_id) REFERENCES app.candidate_built_html(tenant_id,site_id,build_id)
);
CREATE TABLE app.astro_review_authentication (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,revision_id uuid NOT NULL,decision_id uuid NOT NULL,
    authentication_time timestamptz NOT NULL,recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,revision_id),
    FOREIGN KEY(tenant_id,site_id,revision_id) REFERENCES app.astro_candidate_impacts(tenant_id,site_id,revision_id),
    FOREIGN KEY(tenant_id,site_id,decision_id) REFERENCES app.candidate_recipe_review_decisions(tenant_id,site_id,id),
    CHECK(authentication_time BETWEEN recorded_at-interval '5 minutes' AND recorded_at)
);
CREATE TRIGGER candidate_built_html_immutable BEFORE UPDATE OR DELETE ON app.candidate_built_html
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER astro_candidate_impacts_immutable BEFORE UPDATE OR DELETE ON app.astro_candidate_impacts
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER astro_review_authentication_immutable BEFORE UPDATE OR DELETE ON app.astro_review_authentication
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.candidate_built_html ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.candidate_built_html FORCE ROW LEVEL SECURITY;
ALTER TABLE app.astro_candidate_impacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.astro_candidate_impacts FORCE ROW LEVEL SECURITY;
ALTER TABLE app.astro_review_authentication ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.astro_review_authentication FORCE ROW LEVEL SECURITY;
CREATE POLICY candidate_built_html_scope ON app.candidate_built_html
USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE POLICY astro_candidate_impacts_scope ON app.astro_candidate_impacts
USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE POLICY astro_review_authentication_scope ON app.astro_review_authentication
USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
REVOKE ALL ON app.candidate_built_html,app.astro_candidate_impacts,app.astro_review_authentication
FROM PUBLIC,signal_identity,signal_api,signal_bootstrap,signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest;

CREATE FUNCTION control.bind_candidate_built_html(p_token bytea,p_site uuid,p_generation text,p_build uuid,p_body bytea,
    p_artifact uuid,p_key_ref text,p_created timestamptz,p_retain timestamptz)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; r app.candidate_dependency_receipts%%ROWTYPE; existing bytea; item jsonb; artifact jsonb; p_pages jsonb;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_token,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT dep.* INTO r FROM app.candidate_dependency_receipts dep
      JOIN app.candidate_build_intents b ON b.tenant_id=dep.tenant_id AND b.site_id=dep.site_id AND b.id=dep.build_id
      JOIN app.candidate_build_receipts receipt ON receipt.tenant_id=b.tenant_id AND receipt.site_id=b.site_id AND receipt.build_id=b.id
      WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.id=p_build AND b.status='completed'
        AND b.requested_by_user_id=a.user_id AND b.membership_epoch=a.membership_epoch
        AND b.site_epoch=a.site_authorization_epoch AND b.recovery_generation=p_generation AND receipt.exit_class='passed';
    IF NOT FOUND OR p_body IS NULL OR octet_length(p_body) NOT BETWEEN 1 AND 20971520
      OR p_artifact IS NULL OR NOT control.valid_artifact_key_reference(p_key_ref)
      OR p_created IS NULL OR p_created>transaction_timestamp()+interval '5 minutes'
      OR p_retain IS NULL OR p_retain<=p_created THEN RETURN 'invalid_receipt'; END IF;
    BEGIN p_pages:=convert_from(p_body,'UTF8')::jsonb;
    EXCEPTION WHEN character_not_in_repertoire OR invalid_text_representation THEN RETURN 'invalid_receipt'; END;
    IF jsonb_typeof(p_pages)<>'array'
      OR jsonb_array_length(p_pages)<>jsonb_array_length(r.built_pages)
      OR jsonb_array_length(p_pages) NOT BETWEEN 1 AND 1000
      OR (SELECT count(DISTINCT value->>'path') FROM jsonb_array_elements(p_pages))<>jsonb_array_length(p_pages) THEN RETURN 'invalid_receipt'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(p_pages) LOOP
        IF jsonb_typeof(item->'html') IS DISTINCT FROM 'string' OR octet_length(item->>'html')>524288
          OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.built_pages) page
              WHERE page->>'path'=item->>'path' AND page->>'sha256'=encode(sha256(convert_to(item->>'html','UTF8')),'hex')) THEN RETURN 'invalid_receipt'; END IF;
        SELECT value INTO artifact FROM app.candidate_build_receipts receipt,
          LATERAL jsonb_array_elements(receipt.artifacts) WHERE receipt.tenant_id=a.tenant_id
          AND receipt.site_id=p_site AND receipt.build_id=p_build AND value->>'path'=item->>'path';
        IF artifact->>'size' IS DISTINCT FROM octet_length(item->>'html')::text THEN RETURN 'invalid_receipt'; END IF;
    END LOOP;
    SELECT pages_sha256 INTO existing FROM app.candidate_built_html WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=p_build;
    IF FOUND THEN RETURN CASE WHEN existing=sha256(p_body) AND EXISTS(SELECT 1 FROM app.candidate_built_html h
      JOIN app.artifacts object ON object.tenant_id=h.tenant_id AND object.site_id=h.site_id AND object.id=h.artifact_id
      WHERE h.tenant_id=a.tenant_id AND h.site_id=p_site AND h.build_id=p_build AND h.artifact_id=p_artifact
        AND object.encryption_key_ref=p_key_ref AND object.durability_state='verified') THEN 'bound' ELSE 'receipt_conflict' END; END IF;
    INSERT INTO app.artifacts(tenant_id,site_id,id,object_key,object_version,sha256,byte_length,media_type,
      encryption_key_ref,durability_state,created_at,retain_until,legal_hold)
    VALUES(a.tenant_id,p_site,p_artifact,'artifacts/v1/'||a.tenant_id::text||'/'||p_site::text||'/'||p_artifact::text||'/'||encode(sha256(p_body),'hex')||'.sig',
      encode(sha256(p_body),'hex'),sha256(p_body),octet_length(p_body),'application/json',p_key_ref,'verified',p_created,p_retain,false);
    INSERT INTO app.artifact_attestations(tenant_id,site_id,id,artifact_id,check_type,result,verified_hash,verified_at)
    VALUES(a.tenant_id,p_site,p_artifact,p_artifact,'upload_readback','verified',sha256(p_body),p_created);
    INSERT INTO app.candidate_built_html VALUES(a.tenant_id,p_site,p_build,p_artifact,sha256(p_body));
    RETURN 'bound';
END; $$;
CREATE FUNCTION control.read_candidate_built_html(p_token bytea,p_site uuid,p_generation text,p_build uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_token,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
    RETURN (SELECT jsonb_build_object('tenant_id',object.tenant_id,'site_id',object.site_id,'artifact_id',object.id,
      'object_key',object.object_key,'object_version',object.object_version,'sha256',encode(object.sha256,'hex'),
      'byte_length',object.byte_length,'media_type',object.media_type,'encryption_key_ref',object.encryption_key_ref,
      'created_at',object.created_at,'retain_until',object.retain_until,'legal_hold',object.legal_hold,'durability_state',object.durability_state)
      FROM app.candidate_built_html h JOIN app.artifacts object ON object.tenant_id=h.tenant_id AND object.site_id=h.site_id AND object.id=h.artifact_id
      JOIN app.candidate_build_intents b
      ON b.tenant_id=h.tenant_id AND b.site_id=h.site_id AND b.id=h.build_id
      WHERE h.tenant_id=a.tenant_id AND h.site_id=p_site AND h.build_id=p_build
        AND b.requested_by_user_id=a.user_id AND b.membership_epoch=a.membership_epoch
        AND b.site_epoch=a.site_authorization_epoch AND b.recovery_generation=p_generation);
END; $$;
REVOKE ALL ON FUNCTION control.bind_candidate_built_html(bytea,uuid,text,uuid,bytea,uuid,text,timestamptz,timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_candidate_built_html(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_candidate_built_html(bytea,uuid,text,uuid,bytea,uuid,text,timestamptz,timestamptz),
control.read_candidate_built_html(bytea,uuid,text,uuid) TO signal_identity;

CREATE FUNCTION control.seal_astro_recipe_revision(
    p_token bytea,p_site uuid,p_generation text,p_revision uuid,p_extension uuid,p_build uuid,
    p_report uuid,p_finding uuid,p_release uuid,p_release_hash bytea,p_key uuid,
    p_canonical bytea,p_hash bytea,p_baseline uuid,p_scope bytea,p_baseline_html bytea,p_candidate_html bytea
) RETURNS TABLE(revision_id uuid,revision_sha256 bytea,replayed boolean,outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; m jsonb; scope jsonb; b app.candidate_build_intents%%ROWTYPE;
    base app.candidate_build_intents%%ROWTYPE; r app.candidate_build_receipts%%ROWTYPE;
    br app.candidate_build_receipts%%ROWTYPE; e app.github_pr_extensions%%ROWTYPE;
    release control.recipe_releases%%ROWTYPE; report app.crawl_audit_reports%%ROWTYPE;
    existing app.candidate_recipe_revisions%%ROWTYPE; bh jsonb; ch jsonb;
    item jsonb; old text; new text; off integer; v_class text; v_count integer; field text; loaded jsonb;
BEGIN
    IF p_canonical IS NULL OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768
        OR p_hash IS DISTINCT FROM sha256(p_canonical) OR p_scope IS NULL
        OR octet_length(p_scope) NOT BETWEEN 1 AND 2097152 OR p_revision IS NULL OR p_key IS NULL
        OR p_baseline_html IS NULL OR octet_length(p_baseline_html) NOT BETWEEN 1 AND 20971520
        OR p_candidate_html IS NULL OR octet_length(p_candidate_html) NOT BETWEEN 1 AND 20971520 THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_revision'::text; RETURN;
    END IF;
    BEGIN
        m:=convert_from(p_canonical,'UTF8')::jsonb; scope:=convert_from(p_scope,'UTF8')::jsonb;
        bh:=convert_from(p_baseline_html,'UTF8')::jsonb; ch:=convert_from(p_candidate_html,'UTF8')::jsonb;
    EXCEPTION WHEN character_not_in_repertoire OR invalid_text_representation THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_revision'::text; RETURN;
    END;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_token,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
      OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'permission_denied'::text; RETURN;
    END IF;
    SELECT * INTO e FROM app.github_pr_extensions WHERE tenant_id=a.tenant_id AND site_id=p_site
      AND id=p_extension AND framework='astro' AND status='observed' AND coverage='complete';
    SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_build;
    SELECT * INTO base FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_baseline;
    SELECT * INTO r FROM app.candidate_build_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=p_build;
    SELECT * INTO br FROM app.candidate_build_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=p_baseline;
    IF e.id IS NULL OR b.id IS NULL OR base.id IS NULL OR jsonb_typeof(bh) IS DISTINCT FROM 'array' OR jsonb_typeof(ch) IS DISTINCT FROM 'array'
      OR encode(sha256(p_baseline_html),'hex') IS DISTINCT FROM control.read_candidate_built_html(p_token,p_site,p_generation,p_baseline)->>'sha256'
      OR encode(sha256(p_candidate_html),'hex') IS DISTINCT FROM control.read_candidate_built_html(p_token,p_site,p_generation,p_build)->>'sha256'
      OR control.read_candidate_built_html(p_token,p_site,p_generation,p_baseline)->>'durability_state' IS DISTINCT FROM 'verified'
      OR control.read_candidate_built_html(p_token,p_site,p_generation,p_build)->>'durability_state' IS DISTINCT FROM 'verified'
      OR b.extension_id<>p_extension OR base.extension_id<>p_extension
      OR b.base_sha<>base.base_sha OR b.tree_sha<>base.tree_sha OR b.base_sha<>e.base_sha
      OR b.tree_sha<>e.tree_sha OR b.status<>'completed' OR base.status<>'completed'
      OR b.requested_by_user_id<>a.user_id OR base.requested_by_user_id<>a.user_id
      OR b.membership_epoch<>a.membership_epoch OR base.membership_epoch<>a.membership_epoch
      OR b.site_epoch<>a.site_authorization_epoch OR base.site_epoch<>a.site_authorization_epoch
      OR b.recovery_generation<>p_generation OR base.recovery_generation<>p_generation
      OR base.patch_sha256<>'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
      OR b.patch_sha256=base.patch_sha256 OR r.exit_class IS DISTINCT FROM 'passed'
      OR br.exit_class IS DISTINCT FROM 'passed'
      OR (SELECT lockfile_sha256 FROM app.candidate_dependency_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=p_build)
        IS DISTINCT FROM (SELECT lockfile_sha256 FROM app.candidate_dependency_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=p_baseline)
      OR NOT EXISTS (SELECT 1 FROM app.github_read_bindings binding WHERE tenant_id=a.tenant_id
        AND site_id=p_site AND id=e.binding_id AND status='active' AND base_sha=b.base_sha
        AND (protected OR control.github_unprotected_base_accepted(binding,p_generation))) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'build_unavailable'::text; RETURN;
    END IF;
    SELECT * INTO release FROM control.recipe_releases WHERE id=p_release AND content_hash=p_release_hash;
    IF NOT FOUND OR release.recipe_key NOT IN ('astro_title','astro_description','astro_alt','astro_json_ld')
      OR (SELECT status FROM control.recipe_release_events WHERE release_id=p_release ORDER BY sequence_number DESC LIMIT 1) IS DISTINCT FROM 'REVIEWED'
      OR convert_from(release.canonical_body,'UTF8')::jsonb->>'autonomy_eligible' IS DISTINCT FROM 'false'
      OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=p_release) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'release_unavailable'::text; RETURN;
    END IF;
    SELECT * INTO report FROM app.crawl_audit_reports WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_report;
    SELECT evidence INTO loaded FROM control.load_candidate_recipe_evidence(p_token,p_site,p_generation,p_report,p_finding);
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(report.findings) f
      WHERE f->>'id'=p_finding::text AND f=m #> '{evidence,finding}' AND f->>'key'=ANY(
        CASE release.recipe_key WHEN 'astro_title' THEN ARRAY['metadata.title.missing','metadata.title.duplicate']
        WHEN 'astro_description' THEN ARRAY['metadata.meta_description.missing','metadata.meta_description.duplicate']
        WHEN 'astro_alt' THEN ARRAY['images.alt.missing']
        WHEN 'astro_json_ld' THEN ARRAY['structured_data.invalid_json_ld'] END)) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'finding_unavailable'::text; RETURN;
    END IF;
    IF m->>'site_id' IS DISTINCT FROM p_site::text OR m->>'extension_id' IS DISTINCT FROM p_extension::text
      OR m->>'build_id' IS DISTINCT FROM p_build::text OR m->>'audit_report_id' IS DISTINCT FROM p_report::text
      OR m->>'finding_id' IS DISTINCT FROM p_finding::text OR m->>'recipe_release_id' IS DISTINCT FROM p_release::text
      OR m->>'release_content_hash' IS DISTINCT FROM encode(p_release_hash,'hex') OR m->>'recipe_key' IS DISTINCT FROM release.recipe_key
      OR m->>'base_sha' IS DISTINCT FROM b.base_sha OR m->>'patch_sha256' IS DISTINCT FROM b.patch_sha256
      OR m->>'framework' IS DISTINCT FROM 'astro' OR m->>'autonomy_eligible' IS DISTINCT FROM 'false'
      OR m #>> '{built_impact,baseline_build_id}' IS DISTINCT FROM p_baseline::text
      OR m #>> '{built_impact,scope_sha256}' IS DISTINCT FROM encode(sha256(p_scope),'hex')
      OR m #>> '{evidence,manifest_id}' IS DISTINCT FROM report.manifest_id::text
      OR m #>> '{evidence,manifest_sha256}' IS DISTINCT FROM encode(report.manifest_sha256,'hex')
      OR loaded IS NULL OR m #>> '{evidence,page_id}' IS DISTINCT FROM loaded->>'page_id'
      OR m #>> '{evidence,page_url}' IS DISTINCT FROM loaded->>'page_url'
      OR m #>> '{evidence,site_origin}' IS DISTINCT FROM loaded->>'site_origin'
      OR m #>> '{built_impact,lockfile_sha256}' IS DISTINCT FROM (SELECT lockfile_sha256
          FROM app.candidate_dependency_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=p_build)
      OR m #> '{build_receipt,artifacts}' IS DISTINCT FROM r.artifacts
      OR m #>> '{build_receipt,logs_sha256}' IS DISTINCT FROM r.logs_sha256
      OR m #>> '{build_receipt,toolchain}' IS DISTINCT FROM r.toolchain
      OR m #>> '{build_receipt,command}' IS DISTINCT FROM r.build_command
      OR m #>> '{build_receipt,exit_class}' IS DISTINCT FROM 'passed'
      OR coalesce(m->>'source_path','') !~ '^[A-Za-z0-9_\[\]./-]+$'
      OR m->>'source_path' ~ '(^|/)(\.|\.\.|auth|authentication|payments?|infra|infrastructure|deploy|deployment|secrets?)(/|$)'
      OR m->>'source_path' ~ '(^|/)\.' OR m->>'source_path' ~* '(secret|\.lock$|\.pem$|\.key$|package\.json$|package-lock\.json$|astro\.config\.)'
      OR coalesce(m->>'source_sha256','') !~ '^[0-9a-f]{64}$' OR coalesce(m->>'result_sha256','') !~ '^[0-9a-f]{64}$'
      OR jsonb_typeof(m->'patch') IS DISTINCT FROM 'object' OR coalesce(m #>> '{patch,offset}','') !~ '^(0|[1-9][0-9]{0,5})$'
      OR jsonb_typeof(m #> '{patch,before}') IS DISTINCT FROM 'string' OR jsonb_typeof(m #> '{patch,after}') IS DISTINCT FROM 'string'
      OR octet_length(m #>> '{patch,before}')>4096 OR octet_length(m #>> '{patch,after}')>4096
      OR coalesce(length(m->>'recovery_plan'),0) NOT BETWEEN 1 AND 500
      OR coalesce(length(m->>'expected_impact'),0) NOT BETWEEN 1 AND 500
      OR jsonb_typeof(scope->'assertions') IS DISTINCT FROM 'array'
      OR jsonb_array_length(scope->'assertions') NOT BETWEEN 1 AND 128 THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_revision'::text; RETURN;
    END IF;
    v_count:=jsonb_array_length(scope->'assertions');
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(bh) h JOIN jsonb_array_elements(scope->'assertions') s
      ON s->>'path'=h->>'path' WHERE encode(sha256(convert_to(h->>'html','UTF8')),'hex')=loaded->>'page_body_sha256'
        AND rtrim(loaded->>'site_origin','/')||'/'||regexp_replace(substring(h->>'path' FROM length(base.artifact_root)+2),'index\.html$','')=loaded->>'page_url') THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'crawl_build_drift'::text; RETURN;
    END IF;
    v_class:=CASE WHEN v_count=1 AND m->>'source_path' !~ '(^|/)(layouts|components|data)(/|$)' AND (m->>'source_path' ~ '/pages/[^\[\]]+\.astro$'
      OR m->>'source_path' ~ '/content/.+\.(json|md|mdx|yaml|yml)$') THEN 'A2' ELSE 'A4' END;
    IF m->>'approval_class' IS DISTINCT FROM v_class OR m #>> '{built_impact,page_count}' IS DISTINCT FROM v_count::text
      OR (release.recipe_key='astro_json_ld' AND v_class<>'A2')
      OR (SELECT count(DISTINCT x->>'path') FROM jsonb_array_elements(scope->'assertions') x)<>v_count
      OR m #> '{built_impact,pages}' IS DISTINCT FROM (SELECT jsonb_agg(x->>'path' ORDER BY x->>'path') FROM jsonb_array_elements(scope->'assertions') x)
      OR jsonb_typeof(m #> '{built_impact,samples}') IS DISTINCT FROM 'array'
      OR jsonb_array_length(m #> '{built_impact,samples}') NOT BETWEEN 1 AND 3
      OR octet_length((m #> '{built_impact,samples}')::text)>9000 THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_impact'::text; RETURN;
    END IF;
    IF (SELECT array_agg(x->>'path' ORDER BY x->>'path') FROM jsonb_array_elements(bh) x)
      IS DISTINCT FROM (SELECT array_agg(x->>'path' ORDER BY x->>'path') FROM jsonb_array_elements(ch) x)
      OR (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(br.artifacts) x WHERE NOT EXISTS
        (SELECT 1 FROM jsonb_array_elements(scope->'assertions') s WHERE s->>'path'=x->>'path'))
      IS DISTINCT FROM (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(r.artifacts) x WHERE NOT EXISTS
        (SELECT 1 FROM jsonb_array_elements(scope->'assertions') s WHERE s->>'path'=x->>'path'))
      OR EXISTS (SELECT 1 FROM jsonb_array_elements(bh) x JOIN jsonb_array_elements(ch) y ON x->>'path'=y->>'path'
        WHERE x->>'html' IS DISTINCT FROM y->>'html' AND NOT EXISTS
          (SELECT 1 FROM jsonb_array_elements(scope->'assertions') s WHERE s->>'path'=x->>'path')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'out_of_scope'::text; RETURN;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(scope->'assertions') LOOP
        SELECT x->>'html' INTO old FROM jsonb_array_elements(bh) x WHERE x->>'path'=item->>'path';
        SELECT x->>'html' INTO new FROM jsonb_array_elements(ch) x WHERE x->>'path'=item->>'path';
        IF old IS NULL OR new IS NULL OR old=new OR coalesce(item->>'offset','') !~ '^(0|[1-9][0-9]{0,5})$'
          OR jsonb_typeof(item->'before') IS DISTINCT FROM 'string' OR jsonb_typeof(item->'after') IS DISTINCT FROM 'string'
          OR octet_length(item->>'before')>4096 OR octet_length(item->>'after')>4096
          OR item->>'before_sha256' IS DISTINCT FROM encode(sha256(convert_to(old,'UTF8')),'hex')
          OR item->>'after_sha256' IS DISTINCT FROM encode(sha256(convert_to(new,'UTF8')),'hex') THEN
            RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_assertion'::text; RETURN;
        END IF;
        off:=(item->>'offset')::integer;
        IF substring(old FROM off+1 FOR length(item->>'before')) IS DISTINCT FROM item->>'before'
          OR substring(old FROM 1 FOR off)||(item->>'after')||substring(old FROM off+length(item->>'before')+1) IS DISTINCT FROM new
          THEN
            RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_assertion'::text; RETURN;
        END IF;
        field:=substring(old FROM 1 FOR off);
        IF (release.recipe_key='astro_title' AND NOT (
            (field ~ '<title>$' AND (item->>'after') !~ '[<>"'']')
            OR (field ~ '<head[^>]*>$' AND item->>'before'='' AND item->>'after' ~ '^<title>[^<>]*</title>$')))
          OR (release.recipe_key='astro_description' AND NOT (
            (field ~ '<meta[^>]*name="description"[^>]*content=["'']$' AND (item->>'after') !~ '[<>"'']')
            OR (field ~ '<head[^>]*>$' AND item->>'before'='' AND item->>'after' ~ '^<meta name="description" content="[^"<>]*">$')))
          OR (release.recipe_key='astro_alt' AND NOT (
            (field ~ '<img[^>]*alt=["'']$' AND (item->>'after') !~ '[<>"'']')
            OR (item->>'before' ~ '^<img[^<>]*/?>$' AND item->>'before' !~ '\s+alt\s*='
              AND item->>'after' ~ '^<img[^<>]* alt="[^"<>]*"/?>$'
              AND regexp_replace(item->>'after',' alt="[^"<>]*"','')=item->>'before')))
          OR (release.recipe_key='astro_json_ld' AND field !~ '<script[^>]*type="application/ld\+json"[^>]*>$') THEN
            RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_field_assertion'::text; RETURN;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(m #> '{built_impact,samples}') sample WHERE NOT EXISTS
      (SELECT 1 FROM jsonb_array_elements(scope->'assertions') s WHERE sample=jsonb_build_object('path',s->>'path','before',s->>'before','after',s->>'after'))) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_sample'::text; RETURN;
    END IF;
    SELECT * INTO existing FROM app.candidate_recipe_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site AND created_by_user_id=a.user_id AND idempotency_key=p_key;
    IF FOUND THEN
        IF existing.id=p_revision AND existing.canonical_manifest=p_canonical AND EXISTS
          (SELECT 1 FROM app.astro_candidate_impacts i WHERE i.tenant_id=a.tenant_id AND i.site_id=p_site AND i.revision_id=p_revision AND i.canonical_scope=p_scope) THEN
            RETURN QUERY SELECT existing.id,existing.revision_sha256,true,'sealed'::text; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'revision_conflict'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_recipe_revisions(tenant_id,site_id,id,extension_id,build_id,audit_report_id,finding_id,recipe_release_id,
      release_content_hash,base_sha,patch_sha256,canonical_manifest,created_by_user_id,idempotency_key,membership_epoch,site_epoch,recovery_generation)
    VALUES(a.tenant_id,p_site,p_revision,p_extension,p_build,p_report,p_finding,p_release,p_release_hash,b.base_sha,b.patch_sha256,p_canonical,a.user_id,p_key,a.membership_epoch,a.site_authorization_epoch,p_generation);
    INSERT INTO app.astro_candidate_impacts(tenant_id,site_id,revision_id,baseline_build_id,approval_class,canonical_scope)
    VALUES(a.tenant_id,p_site,p_revision,p_baseline,v_class,p_scope);
    RETURN QUERY SELECT p_revision,p_hash,false,'sealed'::text;
END; $$;
REVOKE ALL ON FUNCTION control.seal_astro_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seal_astro_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea,bytea,bytea) TO signal_identity;

ALTER FUNCTION control.decide_authenticated_candidate_recipe_revision(bytea,uuid,text,uuid,bytea,uuid,text)
RENAME TO decide_before_astro;
REVOKE ALL ON FUNCTION control.decide_before_astro(bytea,uuid,text,uuid,bytea,uuid,text) FROM signal_identity;
CREATE FUNCTION control.decide_authenticated_candidate_recipe_revision(
    p_token bytea,p_site uuid,p_generation text,p_revision uuid,p_hash bytea,p_decision uuid,p_value text
) RETURNS TABLE(revision_id uuid,revision_sha256 text,canonical_manifest jsonb,sealed_at timestamptz,
    recipe_release_id uuid,release_content_hash text,base_sha text,patch_sha256 text,review_status text,
    decision_id uuid,decision text,decided_by_user_id uuid,decision_channel text,decided_at timestamptz,reused boolean,outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; auth_time timestamptz; result record; is_astro boolean;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_token,p_site,p_generation);
    is_astro:=EXISTS(SELECT 1 FROM app.astro_candidate_impacts i WHERE i.tenant_id=a.tenant_id AND i.site_id=p_site AND i.revision_id=p_revision);
    IF is_astro AND p_value='approved' THEN
        SELECT s.auth_time INTO auth_time FROM app.sessions s WHERE s.session_token_hash=p_token;
        IF a.role_key IS DISTINCT FROM 'owner' OR a.authentication_level IS DISTINCT FROM 'mfa'
          OR auth_time IS NULL OR auth_time NOT BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp() THEN
            RETURN QUERY SELECT NULL::uuid,NULL::text,NULL::jsonb,NULL::timestamptz,NULL::uuid,NULL::text,NULL::text,NULL::text,
              NULL::text,NULL::uuid,NULL::text,NULL::uuid,NULL::text,NULL::timestamptz,false,'step_up_required'::text; RETURN;
        END IF;
    END IF;
    SELECT * INTO result FROM control.decide_before_astro(p_token,p_site,p_generation,p_revision,p_hash,p_decision,p_value);
    IF is_astro AND p_value='approved' AND result.outcome='decided' THEN
        INSERT INTO app.astro_review_authentication(tenant_id,site_id,revision_id,decision_id,authentication_time)
        VALUES(a.tenant_id,p_site,p_revision,result.decision_id,auth_time) ON CONFLICT DO NOTHING;
    END IF;
    RETURN QUERY SELECT result.revision_id,result.revision_sha256,result.canonical_manifest,result.sealed_at,
      result.recipe_release_id,result.release_content_hash,result.base_sha,result.patch_sha256,result.review_status,
      result.decision_id,result.decision,result.decided_by_user_id,result.decision_channel,result.decided_at,result.reused,result.outcome;
END; $$;
REVOKE ALL ON FUNCTION control.decide_authenticated_candidate_recipe_revision(bytea,uuid,text,uuid,bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.decide_authenticated_candidate_recipe_revision(bytea,uuid,text,uuid,bytea,uuid,text) TO signal_identity;

ALTER FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) RENAME TO github_pr_eligible_before_astro;
REVOKE ALL ON FUNCTION control.github_pr_eligible_before_astro(bytea,uuid,text,uuid) FROM signal_identity;
CREATE FUNCTION control.github_pr_operation_eligible(p_token bytea,p_site uuid,p_generation text,p_revision uuid)
RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,membership_epoch bigint,site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; v app.candidate_recipe_revisions%%ROWTYPE; d app.candidate_recipe_review_decisions%%ROWTYPE;
    auth_time timestamptz;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_token,p_site,p_generation);
    IF NOT EXISTS(SELECT 1 FROM app.astro_candidate_impacts i WHERE i.tenant_id=a.tenant_id AND i.site_id=p_site AND i.revision_id=p_revision) THEN
        RETURN QUERY SELECT * FROM control.github_pr_eligible_before_astro(p_token,p_site,p_generation,p_revision); RETURN;
    END IF;
    SELECT s.auth_time INTO auth_time FROM app.sessions s WHERE s.session_token_hash=p_token;
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
      OR a.authentication_level IS DISTINCT FROM 'mfa' OR auth_time IS NULL
      OR auth_time NOT BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp()
      OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN
        RETURN QUERY SELECT 'step_up_required'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    SELECT revision.* INTO v FROM app.candidate_recipe_revisions revision WHERE revision.tenant_id=a.tenant_id AND revision.site_id=p_site AND revision.id=p_revision;
    SELECT decision.* INTO d FROM app.candidate_recipe_review_decisions decision WHERE decision.tenant_id=a.tenant_id AND decision.site_id=p_site AND decision.candidate_revision_id=p_revision;
    IF d.id IS NULL OR d.decision<>'approved' OR d.decision_channel<>'dashboard' OR d.actor_role<>'owner'
      OR d.authentication_level<>'mfa' OR d.revision_sha256<>v.revision_sha256 OR d.recovery_generation<>p_generation
      OR d.membership_epoch<>a.membership_epoch OR d.site_authorization_epoch<>a.site_authorization_epoch
      OR d.decided_at<transaction_timestamp()-interval '1 hour'
      OR v.recovery_generation<>p_generation OR v.membership_epoch<>a.membership_epoch OR v.site_epoch<>a.site_authorization_epoch
      OR NOT EXISTS(SELECT 1 FROM app.astro_review_authentication h WHERE h.tenant_id=a.tenant_id AND h.site_id=p_site
          AND h.revision_id=p_revision AND h.decision_id=d.id)
      OR EXISTS(SELECT 1 FROM app.candidate_recipe_revisions newer WHERE newer.tenant_id=v.tenant_id AND newer.site_id=v.site_id
          AND newer.audit_report_id=v.audit_report_id AND newer.finding_id=v.finding_id AND (newer.sealed_at,newer.id)>(v.sealed_at,v.id)) THEN
        RETURN QUERY SELECT 'decision_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM app.github_pr_extensions e JOIN app.github_read_bindings b
        ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id
        WHERE e.tenant_id=v.tenant_id AND e.site_id=v.site_id AND e.id=v.extension_id AND e.framework='astro'
          AND e.status='observed' AND e.coverage='complete' AND e.base_sha=v.base_sha AND e.repository_id=b.repository_id
          AND b.status='active' AND b.base_sha=v.base_sha AND (b.protected OR control.github_unprotected_base_accepted(b,p_generation)))
      OR NOT EXISTS(SELECT 1 FROM app.candidate_build_receipts b WHERE b.tenant_id=v.tenant_id AND b.site_id=v.site_id
          AND b.build_id=v.build_id AND b.exit_class='passed' AND b.base_sha=v.base_sha AND b.patch_sha256=v.patch_sha256)
      OR NOT EXISTS(SELECT 1 FROM control.recipe_releases r WHERE r.id=v.recipe_release_id AND r.content_hash=v.release_content_hash
          AND (SELECT status FROM control.recipe_release_events WHERE release_id=r.id ORDER BY sequence_number DESC LIMIT 1)='REVIEWED'
          AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=r.id)) THEN
        RETURN QUERY SELECT 'binding_build_or_release_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    RETURN QUERY SELECT 'eligible'::text,a.tenant_id,a.user_id,a.membership_epoch,a.site_authorization_epoch;
END; $$;
REVOKE ALL ON FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) TO signal_identity;
