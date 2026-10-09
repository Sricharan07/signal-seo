-- F2 reuses one F1/F2 receipt validator and existing immutable impact/MFA records.
CREATE FUNCTION control.seal_built_metadata_recipe_revision(
    p_adapter text,p_token bytea,p_site uuid,p_generation text,p_revision uuid,p_extension uuid,p_build uuid,
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
    IF p_adapter IS NULL OR p_adapter NOT IN ('front_matter','nextjs_metadata') OR p_canonical IS NULL OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768
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
      AND id=p_extension AND framework IN ('astro','eleventy','nextjs') AND status='observed' AND coverage='complete';
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
    IF NOT FOUND OR NOT ((p_adapter='front_matter' AND release.recipe_key IN ('front_matter_title','front_matter_description'))
      OR (p_adapter='nextjs_metadata' AND release.recipe_key IN ('nextjs_title','nextjs_description')))
      OR (SELECT status FROM control.recipe_release_events WHERE release_id=p_release ORDER BY sequence_number DESC LIMIT 1) IS DISTINCT FROM 'REVIEWED'
      OR convert_from(release.canonical_body,'UTF8')::jsonb->>'autonomy_eligible' IS DISTINCT FROM 'false'
      OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=p_release) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'release_unavailable'::text; RETURN;
    END IF;
    SELECT * INTO report FROM app.crawl_audit_reports WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_report;
    SELECT evidence INTO loaded FROM control.load_candidate_recipe_evidence(p_token,p_site,p_generation,p_report,p_finding);
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(report.findings) f
      WHERE f->>'id'=p_finding::text AND f=m #> '{evidence,finding}' AND f->>'key'=ANY(
        CASE release.recipe_key WHEN 'nextjs_title' THEN ARRAY['metadata.title.missing','metadata.title.duplicate']
        WHEN 'nextjs_description' THEN ARRAY['metadata.meta_description.missing','metadata.meta_description.duplicate']
        WHEN 'front_matter_title' THEN ARRAY['metadata.title.missing','metadata.title.duplicate']
        WHEN 'front_matter_description' THEN ARRAY['metadata.meta_description.missing','metadata.meta_description.duplicate']
        WHEN 'astro_alt' THEN ARRAY['images.alt.missing']
        WHEN 'astro_json_ld' THEN ARRAY['structured_data.invalid_json_ld'] END)) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'finding_unavailable'::text; RETURN;
    END IF;
    IF m->>'site_id' IS DISTINCT FROM p_site::text OR m->>'extension_id' IS DISTINCT FROM p_extension::text
      OR m->>'build_id' IS DISTINCT FROM p_build::text OR m->>'audit_report_id' IS DISTINCT FROM p_report::text
      OR m->>'finding_id' IS DISTINCT FROM p_finding::text OR m->>'recipe_release_id' IS DISTINCT FROM p_release::text
      OR m->>'release_content_hash' IS DISTINCT FROM encode(p_release_hash,'hex') OR m->>'recipe_key' IS DISTINCT FROM release.recipe_key
      OR m->>'base_sha' IS DISTINCT FROM b.base_sha OR m->>'patch_sha256' IS DISTINCT FROM b.patch_sha256
      OR m->>'framework' IS DISTINCT FROM e.framework OR m->>'content_adapter' IS DISTINCT FROM p_adapter
      OR (p_adapter='nextjs_metadata' AND (e.framework<>'nextjs' OR b.artifact_root<>'out' OR base.artifact_root<>'out')) OR m->>'autonomy_eligible' IS DISTINCT FROM 'false'
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
      OR (p_adapter='front_matter' AND (m->>'source_path' ~* '(^|/)(layouts?|components|config|_layouts|_includes|includes|_?data)(/|$)'
        OR m->>'source_path' ~* 'config[^/]*$' OR m->>'source_path' !~ '\.(md|mdx|njk)$'))
      OR (p_adapter='nextjs_metadata' AND (m->>'source_path' !~ '^(src/)?(app/([A-Za-z0-9_\[\]-]+/)*(page|layout)|pages/([A-Za-z0-9_\[\]-]+/)*[A-Za-z0-9\[\]-]+)\.(tsx|jsx)$'
        OR m->>'source_path' ~ '(^|/)(_.*|api)(/|\.)'))
      OR m->>'claim_review_required' IS DISTINCT FROM 'true'
      OR m->'model_draft' IS DISTINCT FROM 'null'::jsonb
      OR NOT EXISTS(SELECT 1 FROM app.candidate_dependency_receipts d
        WHERE d.tenant_id=a.tenant_id AND d.site_id=p_site AND d.build_id=p_build)
      OR NOT EXISTS(SELECT 1 FROM app.candidate_dependency_receipts d
        WHERE d.tenant_id=a.tenant_id AND d.site_id=p_site AND d.build_id=p_baseline)
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
        AND rtrim(loaded->>'site_origin','/')||'/'||CASE WHEN p_adapter='nextjs_metadata' THEN regexp_replace(regexp_replace(substring(h->>'path' FROM length(base.artifact_root)+2),'index\.html$',''),'\.html$','')
          ELSE regexp_replace(substring(h->>'path' FROM length(base.artifact_root)+2),'index\.html$','') END=loaded->>'page_url') THEN
        RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'crawl_build_drift'::text; RETURN;
    END IF;
    v_class:=CASE WHEN v_count=1 AND m->>'source_path' !~ '[\[\]]' THEN 'A2' ELSE 'A4' END;
    IF p_adapter='nextjs_metadata' AND m->>'source_path' ~ '/layout\.(tsx|jsx)$' THEN
        v_class:='A4';
        IF jsonb_array_length(bh)>128 OR (m->>'source_path' !~ '^(src/)?app/layout\.(tsx|jsx)$' AND v_count<>1) THEN
            RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'invalid_impact'::text; RETURN;
        END IF;
    END IF;
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
        IF (release.recipe_key IN ('front_matter_title','nextjs_title') AND NOT (
            (field ~ '<title>$' AND (item->>'after') !~ '[<>"'']')
            OR (field ~ '<head[^>]*>$' AND item->>'before'='' AND item->>'after' ~ '^<title>[^<>]*</title>$')))
          OR (release.recipe_key IN ('front_matter_description','nextjs_description') AND NOT (
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
REVOKE ALL ON FUNCTION control.seal_built_metadata_recipe_revision(text,bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea,bytea,bytea) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.seal_front_matter_recipe_revision(p_token bytea,p_site uuid,p_generation text,p_revision uuid,p_extension uuid,p_build uuid,p_report uuid,p_finding uuid,p_release uuid,p_release_hash bytea,p_key uuid,p_canonical bytea,p_hash bytea,p_baseline uuid,p_scope bytea,p_baseline_html bytea,p_candidate_html bytea)
RETURNS TABLE(revision_id uuid,revision_sha256 bytea,replayed boolean,outcome text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT * FROM control.seal_built_metadata_recipe_revision('front_matter',p_token,p_site,p_generation,p_revision,p_extension,p_build,p_report,p_finding,p_release,p_release_hash,p_key,p_canonical,p_hash,p_baseline,p_scope,p_baseline_html,p_candidate_html);
$$;
REVOKE ALL ON FUNCTION control.seal_front_matter_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seal_front_matter_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea,bytea,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.seal_nextjs_recipe_revision(p_token bytea,p_site uuid,p_generation text,p_revision uuid,p_extension uuid,p_build uuid,p_report uuid,p_finding uuid,p_release uuid,p_release_hash bytea,p_key uuid,p_canonical bytea,p_hash bytea,p_baseline uuid,p_scope bytea,p_baseline_html bytea,p_candidate_html bytea)
RETURNS TABLE(revision_id uuid,revision_sha256 bytea,replayed boolean,outcome text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT * FROM control.seal_built_metadata_recipe_revision('nextjs_metadata',p_token,p_site,p_generation,p_revision,p_extension,p_build,p_report,p_finding,p_release,p_release_hash,p_key,p_canonical,p_hash,p_baseline,p_scope,p_baseline_html,p_candidate_html);
$$;
REVOKE ALL ON FUNCTION control.seal_nextjs_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seal_nextjs_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea,bytea,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.github_pr_operation_eligible(p_token bytea,p_site uuid,p_generation text,p_revision uuid)
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
        WHERE e.tenant_id=v.tenant_id AND e.site_id=v.site_id AND e.id=v.extension_id AND (e.framework='astro' OR (((e.framework IN ('eleventy','nextjs') AND convert_from(v.canonical_manifest,'UTF8')::jsonb->>'content_adapter'='front_matter') OR (e.framework='nextjs' AND convert_from(v.canonical_manifest,'UTF8')::jsonb->>'content_adapter'='nextjs_metadata'))))
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

ALTER TABLE app.candidate_dependency_receipts DROP CONSTRAINT candidate_dependency_receipts_unavailable_reason_check;
ALTER TABLE app.candidate_dependency_receipts ADD CONSTRAINT candidate_dependency_receipts_unavailable_reason_check CHECK (unavailable_reason IN ('NPM_OFFLINE_INSTALL_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_LIMIT_EXCEEDED','ASTRO_BUILT_OUTPUT_REJECTED','NEXT_BUILD_NETWORK_UNAVAILABLE','NEXT_OFFLINE_BUILD_UNAVAILABLE_NETWORK_DISABLED','NEXT_OFFLINE_BUILD_LIMIT_EXCEEDED','NEXT_BUILT_OUTPUT_REJECTED'));

CREATE OR REPLACE FUNCTION control.finish_candidate_dependency_build(p_session bytea,p_site uuid,p_generation text,p_build uuid,p_exit text,p_code integer,p_logs text,p_bytes integer,p_artifacts jsonb,p_lock text,p_pages jsonb,p_reason text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.candidate_build_intents%%ROWTYPE;d app.candidate_dependency_receipts%%ROWTYPE;item jsonb;v_outcome text;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_build FOR UPDATE;
    IF NOT FOUND OR b.requested_by_user_id<>a.user_id OR b.membership_epoch<>a.membership_epoch OR b.site_epoch<>a.site_authorization_epoch OR b.recovery_generation<>p_generation
       OR NOT EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id AND i.lockfile_sha256=p_lock)
    THEN RETURN 'build_not_authorized'; END IF;
    IF p_pages IS NULL OR jsonb_typeof(p_pages)<>'array' OR jsonb_array_length(p_pages)>1000 OR octet_length(p_pages::text)>131072 OR p_artifacts IS NULL OR jsonb_typeof(p_artifacts)<>'array'
       OR (p_exit='passed' AND (jsonb_array_length(p_pages)=0 OR p_reason IS NOT NULL))
       OR (p_exit<>'passed' AND jsonb_array_length(p_pages)<>0)
       OR (p_reason IS NOT NULL AND p_reason NOT IN ('NPM_OFFLINE_INSTALL_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_LIMIT_EXCEEDED','ASTRO_BUILT_OUTPUT_REJECTED','NEXT_BUILD_NETWORK_UNAVAILABLE','NEXT_OFFLINE_BUILD_UNAVAILABLE_NETWORK_DISABLED','NEXT_OFFLINE_BUILD_LIMIT_EXCEEDED','NEXT_BUILT_OUTPUT_REJECTED'))
    THEN RETURN 'invalid_receipt'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(p_pages) LOOP
        IF jsonb_typeof(item)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(item))<>4 OR NOT(item ?& ARRAY['path','sha256','title','description'])
           OR jsonb_typeof(item->'path')<>'string' OR jsonb_typeof(item->'sha256')<>'string'
           OR jsonb_typeof(item->'title') NOT IN ('null','string') OR jsonb_typeof(item->'description') NOT IN ('null','string')
           OR octet_length(coalesce(item->>'title',''))>4096 OR octet_length(coalesce(item->>'description',''))>4096
           OR right(item->>'path',5)<>'.html'
           OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_artifacts) f WHERE f->>'path'=item->>'path' AND f->>'sha256'=item->>'sha256')
        THEN RETURN 'invalid_receipt'; END IF;
    END LOOP;
    IF p_exit='passed' AND ((SELECT count(DISTINCT page.value->>'path') FROM jsonb_array_elements(p_pages) AS page(value))<>jsonb_array_length(p_pages)
       OR (SELECT count(*) FROM jsonb_array_elements(p_artifacts) f WHERE right(f->>'path',5)='.html')<>jsonb_array_length(p_pages))
    THEN RETURN 'invalid_receipt'; END IF;
    SELECT * INTO d FROM app.candidate_dependency_receipts WHERE tenant_id=b.tenant_id AND site_id=b.site_id AND build_id=b.id;
    IF FOUND AND (d.lockfile_sha256<>p_lock OR d.built_pages<>p_pages OR d.unavailable_reason IS DISTINCT FROM p_reason) THEN RETURN 'receipt_conflict'; END IF;
    v_outcome := control.finish_before_dependencies(p_session,p_site,p_generation,p_build,p_exit,p_code,p_logs,p_bytes,p_artifacts);
    IF v_outcome<>'completed' THEN RETURN v_outcome; END IF;
    IF d.build_id IS NULL THEN INSERT INTO app.candidate_dependency_receipts(tenant_id,site_id,build_id,lockfile_sha256,built_pages,unavailable_reason) VALUES(b.tenant_id,b.site_id,b.id,p_lock,p_pages,p_reason); END IF;
    RETURN 'completed';
END $$;
REVOKE ALL ON FUNCTION control.finish_candidate_dependency_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.finish_candidate_dependency_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb,text,jsonb,text) TO signal_identity;
