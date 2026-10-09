CREATE TABLE app.ai_visibility_proposals (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    observation_id uuid NOT NULL, page_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('content', 'structured_data', 'internal_link')),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    payload_sha256 bytea NOT NULL CHECK (octet_length(payload_sha256)=32),
    brief_id uuid, created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, observation_id) REFERENCES app.ai_visibility_observations(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id, site_id, page_id) REFERENCES app.crawl_page_records(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id, site_id, brief_id) REFERENCES app.content_briefs(tenant_id,site_id,id),
    CHECK ((kind='content')=(brief_id IS NOT NULL))
);
CREATE TABLE app.ai_visibility_proposal_decisions (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, proposal_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('accepted','dismissed')),
    actor_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,proposal_id),
    FOREIGN KEY (tenant_id,site_id,proposal_id) REFERENCES app.ai_visibility_proposals(tenant_id,site_id,id)
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['ai_visibility_proposals','ai_visibility_proposal_decisions'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY visibility_agent_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
        EXECUTE format('CREATE TRIGGER visibility_agent_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END; $$;

CREATE FUNCTION control.ai_visibility_agent_read(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; origin record; BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO origin FROM control.verified_site_origin(v.tenant_id,p_site);
    IF origin.outcome IS DISTINCT FROM 'verified' THEN RETURN jsonb_build_object('state','origin_unavailable'); END IF;
    IF (SELECT count(*) FROM app.ai_visibility_observations o WHERE o.tenant_id=v.tenant_id AND o.site_id=p_site)>500
       OR (SELECT count(*) FROM app.ai_visibility_questions q WHERE q.tenant_id=v.tenant_id AND q.site_id=p_site)>500
       OR (SELECT count(*) FROM app.ai_visibility_proposals p WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site)>1500
    THEN RETURN jsonb_build_object('state','coverage_limit'); END IF;
    RETURN jsonb_build_object('state','available','origin',origin.origin,
        'questions',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.id) FROM (
            SELECT q.id,q.question,q.question_set_id,s.crawl_manifest_id,s.created_at
            FROM app.ai_visibility_questions q JOIN app.ai_visibility_question_sets s
              ON s.tenant_id=q.tenant_id AND s.site_id=q.site_id AND s.id=q.question_set_id
            WHERE q.tenant_id=v.tenant_id AND q.site_id=p_site) x),'[]'::jsonb),
        'observations',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.observed_at,x.id) FROM (
            SELECT o.id,o.question_id,o.provider,o.model,o.observed_at,o.status,o.provider_evidence_id,
                o.usage,e.response
            FROM app.ai_visibility_observations o LEFT JOIN app.assistant_provider_evidence e
              ON e.tenant_id=o.tenant_id AND e.site_id=o.site_id AND e.id=o.provider_evidence_id
            WHERE o.tenant_id=v.tenant_id AND o.site_id=p_site) x),'[]'::jsonb),
        'pages',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.id) FROM (
            SELECT DISTINCT p.id,u.fetch_url AS url,p.title,p.headings,p.internal_links,p.structured_data_types,
                m.id AS manifest_id,p.output_truncated
            FROM app.ai_visibility_question_sets s JOIN app.crawl_manifests m
              ON m.tenant_id=s.tenant_id AND m.site_id=s.site_id AND m.id=s.crawl_manifest_id
            JOIN app.crawl_page_records p ON p.tenant_id=m.tenant_id AND p.site_id=m.site_id AND p.crawl_run_id=m.crawl_run_id
            JOIN app.urls u ON u.tenant_id=p.tenant_id AND u.site_id=p.site_id AND u.id=p.url_id
            WHERE s.tenant_id=v.tenant_id AND s.site_id=p_site AND p.http_status BETWEEN 200 AND 299) x),'[]'::jsonb),
        'facts',coalesce((SELECT jsonb_agg(jsonb_build_object('fact_id',f.fact_id,'category',f.category,'statement',f.statement))
            FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f),'[]'::jsonb),
        'broken_link_inputs',coalesce((SELECT jsonb_agg(jsonb_build_object('report_id',r.id,
            'manifest_id',r.manifest_id,'finding_id',f.value->>'id','target_url',f.value->>'resource_locator'))
            FROM app.crawl_audit_reports r CROSS JOIN LATERAL jsonb_array_elements(r.findings) f(value)
            WHERE r.tenant_id=v.tenant_id AND r.site_id=p_site
              AND f.value->>'key'='links.internal.not_found'),'[]'::jsonb),
        'proposals',coalesce((SELECT jsonb_agg(jsonb_build_object('id',p.id,'observation_id',p.observation_id,
            'page_id',p.page_id,'kind',p.kind,'payload',p.payload,'digest',encode(p.payload_sha256,'hex'),
            'brief_id',p.brief_id,'created_at',p.created_at,'decision',coalesce(d.decision,'proposed')) ORDER BY p.created_at,p.id)
            FROM app.ai_visibility_proposals p LEFT JOIN app.ai_visibility_proposal_decisions d
              ON d.tenant_id=p.tenant_id AND d.site_id=p.site_id AND d.proposal_id=p.id
            WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site),'[]'::jsonb));
END; $$;

CREATE FUNCTION control.ai_visibility_agent_propose(
    p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_observation uuid,p_page uuid,
    p_kind text,p_canonical bytea,p_digest bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; ref text; prior bytea; result text; brief uuid; claim jsonb; p_payload jsonb; BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
    IF p_canonical IS NULL OR octet_length(p_canonical)>32000 OR p_digest IS DISTINCT FROM sha256(p_canonical)
    THEN RETURN 'invalid'; END IF;
    p_payload:=convert_from(p_canonical,'UTF8')::jsonb;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,96));
    SELECT p.payload_sha256 INTO prior FROM app.ai_visibility_proposals p
     WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.id=p_id;
    IF FOUND THEN RETURN CASE WHEN prior=p_digest THEN 'replayed' ELSE 'conflict' END; END IF;
    IF p_id IS NULL OR p_kind IS NULL OR p_kind NOT IN ('content','structured_data','internal_link')
       OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object' OR octet_length(p_payload::text)>32000
       OR octet_length(p_digest) IS DISTINCT FROM 32
       OR jsonb_typeof(p_payload->'fact_ids') IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_payload->'fact_ids') NOT BETWEEN 1 AND 20
       OR p_payload->>'state' IS NULL OR p_payload->>'state' NOT IN ('ready','owner_required','rejected')
       OR p_payload->>'observation_id' IS DISTINCT FROM p_observation::text
       OR p_payload->>'page_id' IS DISTINCT FROM p_page::text
       OR p_payload->>'kind' IS DISTINCT FROM p_kind THEN RETURN 'invalid'; END IF;
    IF NOT EXISTS(SELECT 1 FROM app.ai_visibility_observations o
        JOIN app.ai_visibility_questions q ON q.tenant_id=o.tenant_id AND q.site_id=o.site_id AND q.id=o.question_id
        JOIN app.ai_visibility_question_sets s ON s.tenant_id=q.tenant_id AND s.site_id=q.site_id AND s.id=q.question_set_id
        JOIN app.crawl_manifests m ON m.tenant_id=s.tenant_id AND m.site_id=s.site_id AND m.id=s.crawl_manifest_id
        JOIN app.crawl_page_records page ON page.tenant_id=m.tenant_id AND page.site_id=m.site_id AND page.crawl_run_id=m.crawl_run_id
        WHERE o.tenant_id=v.tenant_id AND o.site_id=p_site AND o.id=p_observation AND o.status='complete'
          AND page.id=p_page AND NOT page.output_truncated AND page.http_status BETWEEN 200 AND 299)
    THEN RETURN 'evidence_unavailable'; END IF;
    FOR ref IN SELECT jsonb_array_elements_text(p_payload->'fact_ids') LOOP
        IF NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f WHERE f.fact_id::text=ref)
        THEN RETURN 'fact_unavailable'; END IF;
    END LOOP;
    IF jsonb_typeof(p_payload->'claims') IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_payload->'claims') NOT BETWEEN 1 AND 20
       OR p_payload->'originality'->>'state' NOT IN ('original','rejected')
    THEN RETURN 'invalid'; END IF;
    IF p_payload->>'state'='ready' THEN
        IF p_payload->'grounding'->>'state' IS DISTINCT FROM 'grounded'
           OR p_payload->'originality'->>'state' IS DISTINCT FROM 'original' THEN RETURN 'invalid'; END IF;
        FOR claim IN SELECT value FROM jsonb_array_elements(p_payload->'claims') LOOP
            IF NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f
                WHERE f.statement=claim->>'text' AND claim->'fact_ids' @> jsonb_build_array(f.fact_id::text)
                  AND p_payload->'fact_ids' @> jsonb_build_array(f.fact_id::text))
            THEN RETURN 'claim_unsupported'; END IF;
        END LOOP;
    END IF;
    IF p_kind='structured_data' AND (p_payload->>'schema_type' IS NULL OR p_payload->>'schema_type' NOT IN ('FAQPage','HowTo','Organization','Product','Article'))
    THEN RETURN 'invalid'; END IF;
    IF p_kind='content' THEN
        brief:=p_id;
        result:=control.content_writer_create_brief(p_hash,p_generation,p_site,brief,p_payload->'brief','evidence_proposal',NULL);
        IF result<>'created' THEN RETURN result; END IF;
    END IF;
    INSERT INTO app.ai_visibility_proposals VALUES(v.tenant_id,p_site,p_id,p_observation,p_page,p_kind,p_payload,p_digest,brief,v.user_id,transaction_timestamp());
    RETURN 'proposed';
END; $$;

CREATE FUNCTION control.ai_visibility_agent_decide(
    p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_digest bytea,p_decision text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; p app.ai_visibility_proposals%%ROWTYPE; prior text; ref text; result text; BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN jsonb_build_object('state','denied'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,96));
    SELECT * INTO p FROM app.ai_visibility_proposals x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site AND x.id=p_id;
    IF NOT FOUND OR p.payload_sha256 IS DISTINCT FROM p_digest OR p_decision IS NULL OR p_decision NOT IN ('accepted','dismissed')
    THEN RETURN jsonb_build_object('state','conflict'); END IF;
    SELECT d.decision INTO prior FROM app.ai_visibility_proposal_decisions d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND d.proposal_id=p_id;
    IF FOUND THEN RETURN jsonb_build_object('state',CASE WHEN prior=p_decision THEN 'replayed' ELSE 'conflict' END,'brief_id',p.brief_id); END IF;
    IF p_decision='accepted' THEN
        IF p.payload->>'state'<>'ready' THEN RETURN jsonb_build_object('state','owner_required'); END IF;
        FOR ref IN SELECT jsonb_array_elements_text(p.payload->'fact_ids') LOOP
            IF NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f WHERE f.fact_id::text=ref)
            THEN RETURN jsonb_build_object('state','fact_unavailable'); END IF;
        END LOOP;
        IF p.kind='content' THEN
            result:=control.content_writer_accept_brief(p_hash,p_generation,p_site,p.brief_id);
            IF result NOT IN ('accepted','replayed') THEN RETURN jsonb_build_object('state',result); END IF;
        END IF;
    END IF;
    INSERT INTO app.ai_visibility_proposal_decisions VALUES(v.tenant_id,p_site,p_id,p_decision,v.user_id,transaction_timestamp());
    RETURN jsonb_build_object('state',p_decision,'brief_id',p.brief_id,'handoff',CASE WHEN p.kind='content' THEN 'content_writer' ELSE 'recommendation_only' END);
END; $$;

REVOKE ALL ON FUNCTION control.ai_visibility_agent_read(bytea,text,uuid),
    control.ai_visibility_agent_propose(bytea,text,uuid,uuid,uuid,uuid,text,bytea,bytea),
    control.ai_visibility_agent_decide(bytea,text,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.ai_visibility_agent_read(bytea,text,uuid),
    control.ai_visibility_agent_propose(bytea,text,uuid,uuid,uuid,uuid,text,bytea,bytea),
    control.ai_visibility_agent_decide(bytea,text,uuid,uuid,bytea,text) TO signal_api;
