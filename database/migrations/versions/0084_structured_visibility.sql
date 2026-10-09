CREATE FUNCTION control.seal_structured_data_revision(
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
       OR v_release.recipe_key <> 'structured_data_grounded'
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
       OR COALESCE(v_manifest->>'source_path','') !~ '^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*\.html$'
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
           JOIN app.urls url ON url.tenant_id=page.tenant_id AND url.site_id=page.site_id AND url.id=page.url_id
           WHERE page.tenant_id = v_authority.tenant_id AND page.site_id = p_site_id
             AND page.crawl_run_id = v_report.crawl_run_id
             AND page.id::text = v_manifest #>> '{evidence,page_id}'
             AND url.fetch_url = v_manifest #>> '{evidence,page_url}'
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
REVOKE ALL ON FUNCTION control.seal_structured_data_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seal_structured_data_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) TO signal_identity;

CREATE FUNCTION control.check_grounded_structured_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE m jsonb; d jsonb; b app.candidate_build_receipts%%ROWTYPE;
    item record; f app.business_brain_facts%%ROWTYPE; kind text; allowed text[];
    origin text; page_url text; fragment text; node jsonb;
BEGIN
    IF (SELECT recipe_key FROM control.recipe_releases WHERE id=NEW.recipe_release_id)
       IS DISTINCT FROM 'structured_data_grounded' THEN RETURN NEW; END IF;
    m := convert_from(NEW.canonical_manifest,'UTF8')::jsonb;
    d := m #> '{structured_data,json_ld}'; kind := d->>'@type';
    SELECT primary_origin INTO origin FROM app.sites WHERE tenant_id=NEW.tenant_id AND id=NEW.site_id;
    page_url := origin || '/' || CASE
        WHEN m->>'source_path'='index.html' OR m->>'source_path' LIKE '%%/index.html'
            THEN left(m->>'source_path',length(m->>'source_path')-10)
        ELSE m->>'source_path' END;
    fragment := m #>> '{patch,after}';
    IF m #>> '{evidence,site_origin}' IS DISTINCT FROM origin
       OR m #>> '{evidence,page_url}' IS DISTINCT FROM page_url
       OR fragment NOT LIKE '<script type="application/ld+json">%%</script>'
       OR substring(fragment FROM 36 FOR length(fragment)-44)::jsonb IS DISTINCT FROM d
       OR (d ? 'url' AND (jsonb_typeof(d->'url') IS DISTINCT FROM 'string'
           OR left(d->>'url',length(origin)+1) IS DISTINCT FROM origin || '/'
           OR d->>'url' ~ '[?#[:space:]\\]'))
       THEN RAISE EXCEPTION 'structured_url_or_block_mismatch' USING ERRCODE='23514'; END IF;
    allowed := CASE kind
       WHEN 'FAQPage' THEN ARRAY['@context','@type','url','mainEntity']
       WHEN 'Article' THEN ARRAY['@context','@type','url','headline','datePublished','dateModified']
       WHEN 'BlogPosting' THEN ARRAY['@context','@type','url','headline','datePublished','dateModified']
       WHEN 'Organization' THEN ARRAY['@context','@type','name','description','url']
       WHEN 'Product' THEN ARRAY['@context','@type','name','description','url']
       WHEN 'BreadcrumbList' THEN ARRAY['@context','@type','itemListElement'] END;
    IF allowed IS NULL OR d->>'@context' IS DISTINCT FROM 'https://schema.org'
       OR jsonb_typeof(d) IS DISTINCT FROM 'object' OR d - allowed <> '{}'::jsonb
       OR m #>> '{structured_data,recipe_key}' IS DISTINCT FROM 'structured_data_grounded'
       OR m #> '{structured_data,autonomy_eligible}' IS DISTINCT FROM 'false'::jsonb
       OR m #> '{structured_data,owner_required}' IS DISTINCT FROM to_jsonb(kind IN ('Organization','Product'))
       OR m->'claim_review_required' IS DISTINCT FROM to_jsonb(kind IN ('Organization','Product'))
       OR m #>> '{structured_data,output_path}' IS DISTINCT FROM '_site/' || (m->>'source_path')
       OR jsonb_typeof(m #> '{structured_data,fact_refs}') IS DISTINCT FROM 'object'
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(m #> '{build_receipt,artifacts}') a
           WHERE a->>'path'=m #>> '{structured_data,output_path}'
             AND a->>'sha256'=m->>'result_sha256') THEN
        RAISE EXCEPTION 'invalid_grounded_structured_revision' USING ERRCODE='23514';
    END IF;
    IF kind='FAQPage' THEN
        IF jsonb_typeof(d->'mainEntity') IS DISTINCT FROM 'array' OR jsonb_array_length(d->'mainEntity') NOT BETWEEN 1 AND 8 THEN
            RAISE EXCEPTION 'structured_shape_invalid' USING ERRCODE='23514'; END IF;
        FOR node IN SELECT value FROM jsonb_array_elements(d->'mainEntity') LOOP
            IF node - ARRAY['@type','name','acceptedAnswer'] <> '{}'::jsonb OR node->>'@type' IS DISTINCT FROM 'Question'
               OR jsonb_typeof(node->'name') IS DISTINCT FROM 'string' OR length(node->>'name') NOT BETWEEN 1 AND 1000
               OR node->'acceptedAnswer' - ARRAY['@type','text'] <> '{}'::jsonb
               OR node #>> '{acceptedAnswer,@type}' IS DISTINCT FROM 'Answer'
               OR jsonb_typeof(node #> '{acceptedAnswer,text}') IS DISTINCT FROM 'string'
               OR length(node #>> '{acceptedAnswer,text}') NOT BETWEEN 1 AND 1000 THEN
                RAISE EXCEPTION 'structured_shape_invalid' USING ERRCODE='23514'; END IF;
        END LOOP;
    ELSIF kind IN ('Article','BlogPosting') THEN
        IF jsonb_typeof(d->'headline') IS DISTINCT FROM 'string' OR length(d->>'headline') NOT BETWEEN 1 AND 1000 THEN
            RAISE EXCEPTION 'structured_shape_invalid' USING ERRCODE='23514'; END IF;
    ELSIF kind='BreadcrumbList' THEN
        IF jsonb_typeof(d->'itemListElement') IS DISTINCT FROM 'array' OR jsonb_array_length(d->'itemListElement') NOT BETWEEN 1 AND 8 THEN
            RAISE EXCEPTION 'structured_shape_invalid' USING ERRCODE='23514'; END IF;
        FOR item IN SELECT value,ordinality FROM jsonb_array_elements(d->'itemListElement') WITH ORDINALITY LOOP
            node := item.value;
            IF node - ARRAY['@type','position','name','item'] <> '{}'::jsonb OR node->>'@type' IS DISTINCT FROM 'ListItem'
               OR node->>'position' IS DISTINCT FROM item.ordinality::text
               OR jsonb_typeof(node->'name') IS DISTINCT FROM 'string' OR length(node->>'name') NOT BETWEEN 1 AND 1000
               OR jsonb_typeof(node->'item') IS DISTINCT FROM 'string'
               OR left(node->>'item',length(origin)+1) IS DISTINCT FROM origin || '/'
               OR node->>'item' ~ '[?#[:space:]\\]' THEN
                RAISE EXCEPTION 'structured_shape_invalid' USING ERRCODE='23514'; END IF;
        END LOOP;
    END IF;
    SELECT * INTO b FROM app.candidate_build_receipts WHERE tenant_id=NEW.tenant_id
        AND site_id=NEW.site_id AND build_id=(m #>> '{structured_data,baseline_build_id}')::uuid
        AND exit_class='passed' AND base_sha=NEW.base_sha;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(b.artifacts) a
          WHERE a->>'path'=m #>> '{structured_data,output_path}' AND a->>'sha256'=m->>'source_sha256')
       OR (SELECT jsonb_agg(a ORDER BY a->>'path') FROM jsonb_array_elements(b.artifacts) a
           WHERE a->>'path' <> m #>> '{structured_data,output_path}') IS DISTINCT FROM
          (SELECT jsonb_agg(a ORDER BY a->>'path') FROM jsonb_array_elements(m #> '{build_receipt,artifacts}') a
           WHERE a->>'path' <> m #>> '{structured_data,output_path}') THEN
        RAISE EXCEPTION 'structured_build_output_mismatch' USING ERRCODE='23514';
    END IF;
    FOR item IN SELECT key,value FROM jsonb_each_text(m #> '{structured_data,fact_refs}') LOOP
        SELECT * INTO f FROM app.business_brain_facts WHERE tenant_id=NEW.tenant_id
            AND site_id=NEW.site_id AND id=item.value::uuid FOR SHARE;
        IF NOT FOUND OR item.key NOT IN ('name','description','url')
           OR d->>item.key IS DISTINCT FROM f.statement
           OR EXISTS (SELECT 1 FROM app.business_brain_facts n WHERE n.tenant_id=NEW.tenant_id
               AND n.site_id=NEW.site_id AND n.supersedes_id=f.id)
           OR EXISTS (SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=NEW.tenant_id
               AND e.site_id=NEW.site_id AND e.fact_id=f.id AND e.event_type='removed')
           OR NOT (f.initial_status='approved' OR EXISTS (SELECT 1 FROM app.business_brain_fact_events e
               WHERE e.tenant_id=NEW.tenant_id AND e.site_id=NEW.site_id AND e.fact_id=f.id AND e.event_type='approved')) THEN
            RAISE EXCEPTION 'structured_fact_not_current_approved' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF kind IN ('Organization','Product') AND (
        NOT (m #> '{structured_data,fact_refs}') ? 'name'
        OR (d - ARRAY['@context','@type']) - ARRAY(SELECT jsonb_object_keys(m #> '{structured_data,fact_refs}')) <> '{}'::jsonb
    ) THEN RAISE EXCEPTION 'structured_fact_required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION control.check_grounded_structured_revision() FROM PUBLIC;
CREATE TRIGGER grounded_structured_revision BEFORE INSERT ON app.candidate_recipe_revisions
FOR EACH ROW EXECUTE FUNCTION control.check_grounded_structured_revision();

CREATE TABLE app.ai_visibility_schedule_settings (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    version bigint NOT NULL CHECK (version > 0), cadence_days integer NOT NULL CHECK (cadence_days BETWEEN 1 AND 30),
    monthly_cap_micros bigint NOT NULL CHECK (monthly_cap_micros BETWEEN 0 AND 100000000),
    enabled boolean NOT NULL, owner_id uuid NOT NULL, membership_epoch bigint NOT NULL,
    site_epoch bigint NOT NULL, recovery_generation text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,id), UNIQUE (tenant_id,site_id,version),
    FOREIGN KEY (tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY (tenant_id,owner_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TABLE app.ai_visibility_scheduled_runs (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, settings_id uuid NOT NULL,
    question_set_id uuid, unavailable_reason text, observed_changes jsonb NOT NULL,
    started_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,settings_id) REFERENCES app.ai_visibility_schedule_settings(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,question_set_id) REFERENCES app.ai_visibility_question_sets(tenant_id,site_id,id),
    CHECK ((question_set_id IS NULL) = (unavailable_reason IS NOT NULL)),
    CHECK (jsonb_typeof(observed_changes)='array')
);
CREATE TABLE app.ai_visibility_call_reservations (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    run_id uuid NOT NULL, question_id uuid NOT NULL, provider text NOT NULL CHECK (provider IN ('openai','perplexity','gemini')),
    month_start date NOT NULL, reserved_micros bigint NOT NULL CHECK (reserved_micros BETWEEN 0 AND 100000000),
    reason text, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,id), UNIQUE (tenant_id,site_id,run_id,question_id,provider),
    FOREIGN KEY (tenant_id,site_id,run_id) REFERENCES app.ai_visibility_scheduled_runs(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,question_id) REFERENCES app.ai_visibility_questions(tenant_id,site_id,id),
    CHECK ((reserved_micros=0) = (reason IS NOT NULL))
);
CREATE TABLE app.ai_visibility_call_results (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, reservation_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('complete','incomplete','unavailable','outcome_unknown')),
    reason text, observation_id uuid, recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,reservation_id),
    FOREIGN KEY (tenant_id,site_id,reservation_id) REFERENCES app.ai_visibility_call_reservations(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,observation_id) REFERENCES app.ai_visibility_observations(tenant_id,site_id,id),
    CHECK ((status='complete' AND observation_id IS NOT NULL AND reason IS NULL) OR status<>'complete')
);

DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['ai_visibility_schedule_settings','ai_visibility_scheduled_runs',
        'ai_visibility_call_reservations','ai_visibility_call_results'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY visibility_scope ON app.%%I USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',name);
        EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',name);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_identity,signal_api,signal_bootstrap,signal_workflow,signal_scheduler,signal_crawl_ingest,signal_crawl_admission',name);
    END LOOP;
END; $$;

CREATE FUNCTION control.set_ai_visibility_schedule(p_hash bytea,p_site uuid,p_generation text,
    p_id uuid,p_days integer,p_cap bigint,p_enabled boolean) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; n bigint; old app.ai_visibility_schedule_settings%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN RETURN 'denied'; END IF;
    IF p_days IS NULL OR p_days NOT BETWEEN 1 AND 30 OR p_cap IS NULL OR p_cap NOT BETWEEN 0 AND 100000000
       OR p_enabled IS NULL OR p_id IS NULL THEN RETURN 'invalid'; END IF;
    IF NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'site_unverified'; END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site FOR UPDATE;
    SELECT * INTO old FROM app.ai_visibility_schedule_settings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id;
    IF FOUND THEN
        IF old.owner_id=a.user_id AND old.cadence_days=p_days AND old.monthly_cap_micros=p_cap
           AND old.enabled=p_enabled AND old.recovery_generation=p_generation THEN RETURN 'replayed'; END IF;
        RETURN 'conflict';
    END IF;
    SELECT coalesce(max(version),0)+1 INTO n FROM app.ai_visibility_schedule_settings WHERE tenant_id=a.tenant_id AND site_id=p_site;
    INSERT INTO app.ai_visibility_schedule_settings VALUES(a.tenant_id,p_site,p_id,n,p_days,p_cap,p_enabled,
        a.user_id,a.membership_epoch,a.site_authorization_epoch,p_generation,transaction_timestamp());
    RETURN 'updated';
END; $$;

CREATE FUNCTION control.ai_visibility_schedule_current(p_tenant uuid,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s app.ai_visibility_schedule_settings%%ROWTYPE;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO s FROM app.ai_visibility_schedule_settings WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY version DESC LIMIT 1;
    IF NOT FOUND OR NOT s.enabled OR s.recovery_generation IS DISTINCT FROM p_generation
       OR NOT control.current_github_site_proof(p_tenant,p_site)
       OR NOT EXISTS (SELECT 1 FROM app.memberships m WHERE m.tenant_id=p_tenant AND m.user_id=s.owner_id
            AND m.state='active' AND m.role_key='owner' AND m.authorization_epoch=s.membership_epoch)
       OR NOT EXISTS (SELECT 1 FROM app.site_memberships m WHERE m.tenant_id=p_tenant AND m.site_id=p_site
            AND m.user_id=s.owner_id AND m.state='active' AND m.authorization_epoch=s.site_epoch)
       OR NOT EXISTS (SELECT 1 FROM app.sites WHERE tenant_id=p_tenant AND id=p_site AND state='active')
       OR NOT EXISTS (SELECT 1 FROM app.tenants WHERE tenant_id=p_tenant AND lifecycle='active')
       OR EXISTS (SELECT 1 FROM app.site_weekly_control WHERE tenant_id=p_tenant AND site_id=p_site AND paused)
       THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('settings_id',s.id,'cadence_days',s.cadence_days);
END; $$;

CREATE FUNCTION control.open_ai_visibility_run(p_tenant uuid,p_site uuid,p_id uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s app.ai_visibility_schedule_settings%%ROWTYPE; q uuid; reason text; changes jsonb; r app.ai_visibility_scheduled_runs%%ROWTYPE;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
    PERFORM 1 FROM app.sites WHERE tenant_id=p_tenant AND id=p_site FOR UPDATE;
    SELECT * INTO r FROM app.ai_visibility_scheduled_runs WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id;
    IF NOT FOUND THEN
        SELECT * INTO s FROM app.ai_visibility_schedule_settings WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY version DESC LIMIT 1;
        IF NOT FOUND THEN RETURN jsonb_build_object('reason','SCHEDULE_UNCONFIGURED'); END IF;
        IF control.ai_visibility_schedule_current(p_tenant,p_site,p_generation) IS NULL THEN reason:='AUTHORITY_UNAVAILABLE';
        ELSIF EXISTS (SELECT 1 FROM app.ai_visibility_scheduled_runs WHERE tenant_id=p_tenant AND site_id=p_site
            AND question_set_id IS NOT NULL AND started_at > transaction_timestamp()-make_interval(days=>s.cadence_days)) THEN reason:='CADENCE_NOT_DUE';
        ELSE SELECT id INTO q FROM app.ai_visibility_question_sets WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY created_at DESC,id DESC LIMIT 1;
            IF q IS NULL THEN reason:='TARGET_QUESTIONS_UNAVAILABLE'; END IF;
        END IF;
        SELECT coalesce(jsonb_agg(DISTINCT d.operation_id),'[]'::jsonb) INTO changes
        FROM app.github_delivery_receipts d WHERE d.tenant_id=p_tenant AND d.site_id=p_site
          AND convert_from(d.canonical_receipt,'UTF8')::jsonb->>'outcome'='verified'
          AND NOT EXISTS (SELECT 1 FROM app.ai_visibility_scheduled_runs prior WHERE prior.tenant_id=p_tenant
              AND prior.site_id=p_site AND prior.question_set_id IS NOT NULL AND prior.observed_changes ? d.operation_id::text);
        INSERT INTO app.ai_visibility_scheduled_runs VALUES(p_tenant,p_site,p_id,s.id,q,reason,changes,transaction_timestamp()) RETURNING * INTO r;
    END IF;
    RETURN jsonb_build_object('run_id',r.id,'reason',r.unavailable_reason,'observed_changes',r.observed_changes,
        'site_origin',(SELECT primary_origin FROM app.sites WHERE tenant_id=p_tenant AND id=p_site),
        'questions',coalesce((SELECT jsonb_agg(jsonb_build_object('id',id,'question',question) ORDER BY id)
            FROM app.ai_visibility_questions WHERE tenant_id=p_tenant AND site_id=p_site AND question_set_id=r.question_set_id),'[]'::jsonb));
END; $$;

CREATE FUNCTION control.reserve_ai_visibility_call(p_tenant uuid,p_site uuid,p_run uuid,p_question uuid,
    p_provider text,p_generation text,p_configured boolean,p_ceiling bigint)
RETURNS TABLE (operation_id uuid,outcome text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.ai_visibility_scheduled_runs%%ROWTYPE; s app.ai_visibility_schedule_settings%%ROWTYPE;
    c app.ai_visibility_call_reservations%%ROWTYPE; used bigint; reason text;
BEGIN
    IF p_provider IS NULL OR p_provider NOT IN ('openai','perplexity','gemini') OR p_configured IS NULL
       OR p_ceiling IS NULL OR p_ceiling NOT BETWEEN 25000 AND 100000000 THEN
        RAISE EXCEPTION 'invalid_visibility_reservation' USING ERRCODE='22023'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
    PERFORM 1 FROM app.sites WHERE tenant_id=p_tenant AND id=p_site FOR UPDATE;
    SELECT * INTO c FROM app.ai_visibility_call_reservations WHERE tenant_id=p_tenant AND site_id=p_site
        AND run_id=p_run AND question_id=p_question AND provider=p_provider;
    IF FOUND THEN RETURN QUERY SELECT c.id,coalesce((SELECT status FROM app.ai_visibility_call_results
        WHERE tenant_id=p_tenant AND site_id=p_site AND reservation_id=c.id),'outcome_unknown'); RETURN; END IF;
    SELECT * INTO r FROM app.ai_visibility_scheduled_runs WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_run;
    IF NOT FOUND OR r.question_set_id IS NULL OR NOT EXISTS (SELECT 1 FROM app.ai_visibility_questions
        WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_question AND question_set_id=r.question_set_id)
        THEN RETURN QUERY SELECT NULL::uuid,'RUN_UNAVAILABLE'::text; RETURN; END IF;
    SELECT * INTO s FROM app.ai_visibility_schedule_settings WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY version DESC LIMIT 1;
    IF s.id<>r.settings_id OR control.ai_visibility_schedule_current(p_tenant,p_site,p_generation) IS NULL
        OR r.started_at < transaction_timestamp()-interval '1 hour' THEN reason:='AUTHORITY_UNAVAILABLE';
    ELSIF NOT p_configured THEN reason:='PROVIDER_UNCONFIGURED';
    ELSE
        -- Assistant APIs have no qualified billing-cost field. Every dispatched hold,
        -- including observed/unpriced successes and previous-month uncertainty, remains.
        SELECT coalesce(sum(reserved_micros),0) INTO used FROM app.ai_visibility_call_reservations
            WHERE tenant_id=p_tenant AND site_id=p_site;
        IF used+p_ceiling>s.monthly_cap_micros THEN reason:='COST_CAP_REACHED'; END IF;
    END IF;
    INSERT INTO app.ai_visibility_call_reservations VALUES(p_tenant,p_site,gen_random_uuid(),p_run,p_question,
        p_provider,date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date,
        CASE WHEN reason IS NULL THEN p_ceiling ELSE 0 END,reason,transaction_timestamp()) RETURNING * INTO c;
    IF reason IS NOT NULL THEN INSERT INTO app.ai_visibility_call_results VALUES(p_tenant,p_site,c.id,'unavailable',reason,NULL,transaction_timestamp()); END IF;
    RETURN QUERY SELECT c.id,coalesce(reason,'reserved');
END; $$;

CREATE FUNCTION control.finish_ai_visibility_call(p_tenant uuid,p_site uuid,p_operation uuid,
    p_status text,p_reason text,p_observation uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c app.ai_visibility_call_reservations%%ROWTYPE; existing app.ai_visibility_call_results%%ROWTYPE;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO c FROM app.ai_visibility_call_reservations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_operation FOR UPDATE;
    IF NOT FOUND OR c.reserved_micros=0 THEN RETURN 'unavailable'; END IF;
    SELECT * INTO existing FROM app.ai_visibility_call_results WHERE tenant_id=p_tenant AND site_id=p_site AND reservation_id=p_operation;
    IF FOUND THEN
        IF (existing.status,existing.reason,existing.observation_id) IS NOT DISTINCT FROM (p_status,p_reason,p_observation) THEN RETURN 'recorded'; END IF;
        RETURN 'conflict';
    END IF;
    IF p_status IS NULL OR p_status NOT IN ('complete','incomplete','outcome_unknown') OR length(p_reason)>128
       OR (p_status='complete' AND (p_reason IS NOT NULL OR p_observation IS NULL))
       OR (p_observation IS NOT NULL AND NOT EXISTS (SELECT 1 FROM app.ai_visibility_observations o
           JOIN app.assistant_provider_evidence e ON e.tenant_id=o.tenant_id AND e.site_id=o.site_id AND e.id=o.provider_evidence_id
           WHERE o.tenant_id=p_tenant AND o.site_id=p_site AND o.id=p_observation AND o.question_id=c.question_id
             AND o.provider=c.provider AND o.status=p_status AND e.egress_operation_id=c.id)) THEN RETURN 'invalid'; END IF;
    INSERT INTO app.ai_visibility_call_results VALUES(p_tenant,p_site,p_operation,p_status,p_reason,p_observation,transaction_timestamp());
    RETURN 'recorded';
END; $$;

CREATE FUNCTION control.read_ai_visibility_schedule(p_hash bytea,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; s app.ai_visibility_schedule_settings%%ROWTYPE; used bigint; history jsonb;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN RETURN NULL; END IF;
    SELECT * INTO s FROM app.ai_visibility_schedule_settings WHERE tenant_id=a.tenant_id AND site_id=p_site ORDER BY version DESC LIMIT 1;
    SELECT coalesce(sum(reserved_micros),0) INTO used FROM app.ai_visibility_call_reservations WHERE tenant_id=a.tenant_id AND site_id=p_site;
    SELECT coalesce(jsonb_agg(item ORDER BY item->>'started_at' DESC),'[]'::jsonb) INTO history FROM (
        SELECT jsonb_build_object('run_id',r.id,'started_at',r.started_at,'question_set_id',r.question_set_id,
            'reason',r.unavailable_reason,'observed_changes',r.observed_changes,
            'calls',coalesce((SELECT jsonb_agg(jsonb_build_object('operation_id',c.id,'provider',c.provider,'reserved_micros',c.reserved_micros,
                'status',coalesce(result.status,'outcome_unknown'),'reason',coalesce(result.reason,c.reason),
                'observation_id',result.observation_id) ORDER BY c.created_at,c.id)
                FROM app.ai_visibility_call_reservations c LEFT JOIN app.ai_visibility_call_results result
                  ON result.tenant_id=c.tenant_id AND result.site_id=c.site_id AND result.reservation_id=c.id
                WHERE c.tenant_id=r.tenant_id AND c.site_id=r.site_id AND c.run_id=r.id),'[]'::jsonb)) item
        FROM app.ai_visibility_scheduled_runs r WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site ORDER BY r.started_at DESC,r.id DESC LIMIT 30
    ) h;
    RETURN jsonb_build_object('schema_version',1,'site_id',p_site,'settings_id',s.id,
        'cadence_days',coalesce(s.cadence_days,7),'monthly_cap_micros',coalesce(s.monthly_cap_micros,1000000),
        'enabled',coalesce(s.enabled,false),'held_micros',used,'spent_micros',NULL,'currency','USD',
        'month_start',date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date,
        'cap_reached',used>=coalesce(s.monthly_cap_micros,1000000),
        'authority_current',control.ai_visibility_schedule_current(a.tenant_id,p_site,p_generation) IS NOT NULL,'runs',history);
END; $$;

REVOKE ALL ON FUNCTION control.set_ai_visibility_schedule(bytea,uuid,text,uuid,integer,bigint,boolean),
    control.read_ai_visibility_schedule(bytea,uuid,text),control.ai_visibility_schedule_current(uuid,uuid,text),
    control.open_ai_visibility_run(uuid,uuid,uuid,text),control.reserve_ai_visibility_call(uuid,uuid,uuid,uuid,text,text,boolean,bigint),
    control.finish_ai_visibility_call(uuid,uuid,uuid,text,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.set_ai_visibility_schedule(bytea,uuid,text,uuid,integer,bigint,boolean),
    control.read_ai_visibility_schedule(bytea,uuid,text) TO signal_api;
GRANT EXECUTE ON FUNCTION control.ai_visibility_schedule_current(uuid,uuid,text) TO signal_scheduler,signal_workflow;
GRANT EXECUTE ON FUNCTION control.open_ai_visibility_run(uuid,uuid,uuid,text),
    control.reserve_ai_visibility_call(uuid,uuid,uuid,uuid,text,text,boolean,bigint),
    control.finish_ai_visibility_call(uuid,uuid,uuid,text,text,uuid) TO signal_workflow;

CREATE FUNCTION control.ai_visibility_call_permit(p_tenant uuid,p_site uuid,p_operation uuid,p_generation text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c app.ai_visibility_call_reservations%%ROWTYPE; s app.ai_visibility_schedule_settings%%ROWTYPE;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO c FROM app.ai_visibility_call_reservations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_operation;
    IF NOT FOUND OR c.reason IS NOT NULL OR c.created_at < transaction_timestamp()-interval '1 minute'
       OR c.month_start<>date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date
       OR EXISTS (SELECT 1 FROM app.ai_visibility_call_results WHERE tenant_id=p_tenant AND site_id=p_site AND reservation_id=c.id)
       OR control.ai_visibility_schedule_current(p_tenant,p_site,p_generation) IS NULL THEN RETURN 'unavailable'; END IF;
    SELECT * INTO s FROM app.ai_visibility_schedule_settings WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY version DESC LIMIT 1;
    IF NOT EXISTS (SELECT 1 FROM app.ai_visibility_scheduled_runs WHERE tenant_id=p_tenant AND site_id=p_site AND id=c.run_id AND settings_id=s.id)
       OR (SELECT sum(reserved_micros) FROM app.ai_visibility_call_reservations WHERE tenant_id=p_tenant AND site_id=p_site)>s.monthly_cap_micros
       THEN RETURN 'unavailable'; END IF;
    RETURN 'permitted';
END; $$;
REVOKE ALL ON FUNCTION control.ai_visibility_call_permit(uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.ai_visibility_call_permit(uuid,uuid,uuid,text) TO signal_workflow;
