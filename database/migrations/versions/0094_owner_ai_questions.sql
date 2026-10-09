CREATE TABLE app.ai_visibility_question_approvals (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, question_set_id uuid NOT NULL,
    owner_id uuid NOT NULL, request_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,question_set_id),
    FOREIGN KEY (tenant_id,site_id,question_set_id)
        REFERENCES app.ai_visibility_question_sets(tenant_id,site_id,id)
);
ALTER TABLE app.ai_visibility_question_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ai_visibility_question_approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY question_approval_scope ON app.ai_visibility_question_approvals
USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE TRIGGER question_approval_immutable BEFORE UPDATE OR DELETE
ON app.ai_visibility_question_approvals FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
REVOKE ALL ON app.ai_visibility_question_approvals FROM PUBLIC,signal_api,signal_identity,
    signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_ingest,signal_crawl_admission;

CREATE FUNCTION control.ai_visibility_owner_context(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; m uuid; q uuid;
BEGIN
    SELECT * INTO a FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF a.tenant_id IS NULL THEN RETURN NULL; END IF;
    IF NOT control.current_github_site_proof(a.tenant_id,p_site) THEN
        RETURN jsonb_build_object('state','origin_unavailable'); END IF;
    SELECT id INTO m FROM app.crawl_manifests WHERE tenant_id=a.tenant_id AND site_id=p_site
        ORDER BY completed_at DESC,id DESC LIMIT 1;
    SELECT s.id INTO q FROM app.ai_visibility_question_sets s WHERE s.tenant_id=a.tenant_id AND s.site_id=p_site
        AND NOT EXISTS(SELECT 1 FROM app.ai_visibility_question_sets n WHERE n.tenant_id=s.tenant_id
            AND n.site_id=s.site_id AND n.supersedes_id=s.id) ORDER BY s.created_at DESC,s.id DESC LIMIT 1;
    RETURN jsonb_build_object('state',CASE WHEN m IS NULL THEN 'crawl_unavailable' ELSE 'available' END,
        'tenant_id',a.tenant_id,'crawl_manifest_id',m,'supersedes_id',q);
END; $$;

CREATE FUNCTION control.ai_visibility_owner_sources(p_hash bytea,p_generation text,p_site uuid,p_manifest uuid)
RETURNS TABLE(page_evidence_id uuid,fetch_url text,title text,headings jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c jsonb;
BEGIN
    c:=control.ai_visibility_owner_context(p_hash,p_generation,p_site);
    IF c->>'state' IS DISTINCT FROM 'available' OR c->>'crawl_manifest_id' IS DISTINCT FROM p_manifest::text
    THEN RETURN; END IF;
    RETURN QUERY SELECT s.* FROM control.load_ai_visibility_crawl_sources((c->>'tenant_id')::uuid,p_site,p_manifest) s
        JOIN app.crawl_page_records p ON p.tenant_id=(c->>'tenant_id')::uuid AND p.site_id=p_site AND p.id=s.page_evidence_id
        WHERE p.http_status BETWEEN 200 AND 299 AND NOT p.output_truncated;
END; $$;

CREATE FUNCTION control.ai_visibility_owner_approve_questions(
    p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_manifest uuid,p_previous uuid,p_questions jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; c jsonb; prior app.ai_visibility_question_approvals%%ROWTYPE; item jsonb; body jsonb; result text;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
       OR a.authentication_level IS DISTINCT FROM 'mfa' THEN RETURN 'denied'; END IF;
    IF NOT EXISTS(SELECT 1 FROM app.sessions WHERE session_token_hash=p_hash
        AND auth_time BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp())
    THEN RETURN 'denied'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,75));
    body:=jsonb_build_object('manifest_id',p_manifest,'supersedes_id',p_previous,'questions',p_questions);
    SELECT * INTO prior FROM app.ai_visibility_question_approvals
        WHERE tenant_id=a.tenant_id AND site_id=p_site AND question_set_id=p_id;
    IF FOUND THEN RETURN CASE WHEN prior.owner_id=a.user_id AND prior.request_payload=body
        THEN 'replayed' ELSE 'conflict' END; END IF;
    c:=control.ai_visibility_owner_context(p_hash,p_generation,p_site);
    IF c->>'state' IS DISTINCT FROM 'available' OR c->>'crawl_manifest_id' IS DISTINCT FROM p_manifest::text
       OR c->>'supersedes_id' IS DISTINCT FROM p_previous::text THEN RETURN 'stale'; END IF;
    IF p_id IS NULL OR jsonb_typeof(p_questions) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_questions) NOT BETWEEN 1 AND 25 THEN RETURN 'invalid'; END IF;
    IF (SELECT count(*) FROM jsonb_array_elements(p_questions) x WHERE x->>'source_kind'='owner')>10
    THEN RETURN 'invalid'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(p_questions) LOOP
        IF jsonb_typeof(item->'question') IS DISTINCT FROM 'string'
           OR length(item->>'question') NOT BETWEEN 8 AND 512
           OR item->>'source_kind' IS NULL OR item->>'source_kind' NOT IN ('owner','crawl')
           OR (item->>'source_kind'='owner' AND item->>'source_evidence_id' IS NOT NULL)
           OR (item->>'source_kind'='crawl' AND NOT EXISTS(
               SELECT 1 FROM control.ai_visibility_owner_sources(p_hash,p_generation,p_site,p_manifest) s
               WHERE s.page_evidence_id::text=item->>'source_evidence_id'
                 AND item->>'question'=trim(regexp_replace('What is ' || coalesce(nullif(s.title,''),s.headings->0->>'text') || '?','\s+',' ','g'))))
        THEN RETURN 'invalid'; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_questions) x GROUP BY lower(x->>'question') HAVING count(*)>1)
    THEN RETURN 'invalid'; END IF;
    result:=control.record_ai_visibility_question_set(a.tenant_id,p_site,p_id,p_manifest,p_questions);
    IF result<>'recorded' THEN RETURN result; END IF;
    INSERT INTO app.ai_visibility_question_approvals VALUES(a.tenant_id,p_site,p_id,a.user_id,body,transaction_timestamp());
    RETURN 'recorded';
END; $$;

CREATE FUNCTION control.ai_visibility_structured_context(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_digest bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; p app.ai_visibility_proposals%%ROWTYPE; result jsonb; ref text;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
    IF a.authentication_level IS DISTINCT FROM 'mfa' OR NOT EXISTS(SELECT 1 FROM app.sessions
        WHERE session_token_hash=p_hash AND auth_time BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp())
    THEN RETURN jsonb_build_object('state','step_up_required'); END IF;
    SELECT * INTO p FROM app.ai_visibility_proposals WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id;
    IF NOT FOUND OR p.kind<>'structured_data' OR p.payload_sha256 IS DISTINCT FROM p_digest
       OR p.payload->>'state' IS DISTINCT FROM 'ready'
       OR NOT EXISTS(SELECT 1 FROM app.ai_visibility_proposal_decisions WHERE tenant_id=a.tenant_id
           AND site_id=p_site AND proposal_id=p_id AND decision='accepted')
    THEN RETURN jsonb_build_object('state','proposal_unavailable'); END IF;
    FOR ref IN SELECT jsonb_array_elements_text(p.payload->'fact_ids') LOOP
        IF NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f WHERE f.fact_id::text=ref)
        THEN RETURN jsonb_build_object('state','facts_unavailable'); END IF;
    END LOOP;
    SELECT jsonb_build_object('revision_id',id,'revision_sha256',encode(revision_sha256,'hex'),
        'fact_fields',convert_from(canonical_manifest,'UTF8')::jsonb->'visibility_fact_fields') INTO result
        FROM app.candidate_recipe_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site
          AND convert_from(canonical_manifest,'UTF8')::jsonb->>'visibility_proposal_id'=p_id::text LIMIT 1;
    RETURN jsonb_build_object('state','available','payload',p.payload,'sealed',result,
        'input', (SELECT jsonb_build_object('report_id',r.id,'finding_id',f.value->>'id')
            FROM app.crawl_audit_reports r CROSS JOIN LATERAL jsonb_array_elements(r.findings) f(value)
            WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND r.manifest_id::text=p.payload->>'manifest_id'
              AND r.detector_release_id='0fc2a362-f51d-4b75-9bbf-22d456f29b40'::uuid
              AND f.value->>'source_kind'='page' AND f.value->>'source_id'=p.page_id::text ORDER BY r.id,f.value->>'id' LIMIT 1),
        'extension_id',(SELECT e.id FROM app.github_pr_extensions e JOIN app.github_read_bindings b
            ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id
            WHERE e.tenant_id=a.tenant_id AND e.site_id=p_site AND e.status='observed' AND b.status='active'
              AND e.framework='eleventy' AND e.content_format='html' ORDER BY e.observed_at DESC,e.id DESC LIMIT 1),
        'recipe_release_id',(SELECT r.id FROM control.recipe_releases r
            WHERE r.recipe_key='structured_data_grounded'
              AND (SELECT e.status FROM control.recipe_release_events e WHERE e.release_id=r.id
                   ORDER BY e.sequence_number DESC LIMIT 1)='REVIEWED'
              AND convert_from(r.canonical_body,'UTF8')::jsonb->>'delivery_mode'='pull_request'
              AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind='recipe_release' AND t.target_id=r.id)
            ORDER BY r.version_major DESC,r.version_minor DESC,r.version_patch DESC,r.id DESC LIMIT 1));
END; $$;

CREATE FUNCTION control.seal_ai_visibility_structured_revision(
    p_hash bytea,p_site uuid,p_generation text,p_revision uuid,p_extension uuid,p_build uuid,
    p_report uuid,p_finding uuid,p_release uuid,p_release_hash bytea,p_key uuid,p_canonical bytea,p_sha bytea,
    p_proposal uuid,p_digest bytea
) RETURNS TABLE(revision_id uuid,revision_sha256 bytea,replayed boolean,outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; c jsonb; m jsonb; claim jsonb; field text; ref text; statement text; actual text; kind text;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
       OR a.authentication_level IS DISTINCT FROM 'mfa' OR NOT EXISTS(SELECT 1 FROM app.sessions
           WHERE session_token_hash=p_hash AND auth_time BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp())
    THEN RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'permission_denied'::text; RETURN; END IF;
    c:=control.ai_visibility_structured_context(p_hash,p_generation,p_site,p_proposal,p_digest);
    m:=convert_from(p_canonical,'UTF8')::jsonb;
    IF c->>'state' IS DISTINCT FROM 'available' OR m->>'visibility_proposal_id' IS DISTINCT FROM p_proposal::text
       OR m->>'visibility_proposal_digest' IS DISTINCT FROM encode(p_digest,'hex')
       OR m#>>'{evidence,page_id}' IS DISTINCT FROM c#>>'{payload,page_id}'
       OR m#>>'{evidence,manifest_id}' IS DISTINCT FROM c#>>'{payload,manifest_id}'
       OR m#>>'{structured_data,json_ld,@type}' IS DISTINCT FROM c#>>'{payload,schema_type}'
    THEN RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'proposal_unavailable'::text; RETURN; END IF;
    FOR claim IN SELECT value FROM jsonb_array_elements(c#>'{payload,claims}') LOOP
        IF NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f
            WHERE f.statement=claim->>'text' AND claim->'fact_ids' @> jsonb_build_array(f.fact_id::text))
        THEN RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'facts_unavailable'::text; RETURN; END IF;
    END LOOP;
    kind:=m#>>'{structured_data,json_ld,@type}';
    IF jsonb_typeof(m->'visibility_fact_fields') IS DISTINCT FROM 'object'
       OR kind NOT IN ('Article','FAQPage','Organization','Product')
       OR (kind='Article' AND NOT m->'visibility_fact_fields' ? 'headline')
       OR (kind IN ('Organization','Product') AND NOT m->'visibility_fact_fields' ? 'name')
       OR (kind='FAQPage' AND (NOT m->'visibility_fact_fields' ? 'question' OR NOT m->'visibility_fact_fields' ? 'answer'))
    THEN RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'facts_unavailable'::text; RETURN; END IF;
    FOR field,ref IN SELECT * FROM jsonb_each_text(m->'visibility_fact_fields') LOOP
        SELECT f.statement INTO statement FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f
            WHERE f.fact_id::text=ref AND c#>'{payload,fact_ids}' @> jsonb_build_array(ref);
        actual:=CASE
            WHEN kind='Article' AND field='headline' THEN m#>>'{structured_data,json_ld,headline}'
            WHEN kind IN ('Organization','Product') AND field IN ('name','description') THEN m#>'{structured_data,json_ld}'->>field
            WHEN kind='FAQPage' AND field='question' THEN m#>>'{structured_data,json_ld,mainEntity,0,name}'
            WHEN kind='FAQPage' AND field='answer' THEN m#>>'{structured_data,json_ld,mainEntity,0,acceptedAnswer,text}' END;
        IF statement IS NULL OR actual IS DISTINCT FROM statement
        THEN RETURN QUERY SELECT NULL::uuid,NULL::bytea,false,'facts_unavailable'::text; RETURN; END IF;
    END LOOP;
    RETURN QUERY SELECT * FROM control.seal_structured_data_revision(p_hash,p_site,p_generation,
        p_revision,p_extension,p_build,p_report,p_finding,p_release,p_release_hash,p_key,p_canonical,p_sha);
END; $$;

REVOKE ALL ON FUNCTION control.ai_visibility_owner_context(bytea,text,uuid),
    control.ai_visibility_owner_sources(bytea,text,uuid,uuid),
    control.ai_visibility_owner_approve_questions(bytea,text,uuid,uuid,uuid,uuid,jsonb),
    control.ai_visibility_structured_context(bytea,text,uuid,uuid,bytea),
    control.seal_ai_visibility_structured_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.ai_visibility_owner_context(bytea,text,uuid),
    control.ai_visibility_owner_sources(bytea,text,uuid,uuid),
    control.ai_visibility_owner_approve_questions(bytea,text,uuid,uuid,uuid,uuid,jsonb),
    control.ai_visibility_structured_context(bytea,text,uuid,uuid,bytea)
TO signal_api;
GRANT EXECUTE ON FUNCTION control.ai_visibility_structured_context(bytea,text,uuid,uuid,bytea)
TO signal_identity;
GRANT EXECUTE ON FUNCTION control.seal_ai_visibility_structured_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea,uuid,bytea)
TO signal_identity;
