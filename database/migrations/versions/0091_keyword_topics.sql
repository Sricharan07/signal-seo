-- No new authority: preserve the existing exact-session owner resolver and snapshots.
ALTER TABLE app.model_budget_calls DROP CONSTRAINT model_budget_calls_role_check;
ALTER TABLE app.model_budget_calls ADD CONSTRAINT model_budget_calls_role_check CHECK(role IN (
    'page_type','fact_extraction','metadata_draft','claim_check','article_outline','article_draft',
    'article_critique','article_revise','report_text','topic_ideas'));

CREATE TABLE app.keyword_idea_sets (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    input jsonb NOT NULL CHECK(jsonb_typeof(input)='object' AND octet_length(input::text)<=32000),
    output jsonb NOT NULL, canonical bytea NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,id) REFERENCES app.model_budget_receipts(tenant_id,site_id,call_id),
    CHECK(jsonb_typeof(output)='object' AND octet_length(canonical)<=16000)
);
ALTER TABLE app.keyword_idea_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.keyword_idea_sets FORCE ROW LEVEL SECURITY;
CREATE POLICY keyword_scope ON app.keyword_idea_sets
    USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
    WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE TRIGGER keyword_immutable BEFORE UPDATE OR DELETE ON app.keyword_idea_sets
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
REVOKE ALL ON app.keyword_idea_sets FROM PUBLIC,signal_api,signal_identity,signal_workflow,
    signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest;

-- Private projection helper; only the authenticated wrappers below can execute it.
CREATE FUNCTION control.keyword_topic_sources(p_tenant uuid,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE volumes jsonb; ideas jsonb;
BEGIN
    IF p_tenant IS DISTINCT FROM app.current_tenant_id() OR p_site IS DISTINCT FROM app.current_site_id()
    THEN RAISE EXCEPTION 'keyword_scope_denied' USING ERRCODE='42501'; END IF;
    SELECT coalesce(jsonb_agg(x ORDER BY x.id),'[]'::jsonb) INTO volumes FROM (
        SELECT c.id,r.result,c.recorded_at,encode(r.response_sha256,'hex') AS response_sha256,c.query
        FROM app.dataforseo_calls c JOIN app.dataforseo_receipts r
            ON (r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
        JOIN app.dataforseo_settings s ON (s.tenant_id,s.site_id)=(c.tenant_id,c.site_id)
            AND s.enabled AND s.credential_generation=c.credential_generation
        WHERE c.tenant_id=p_tenant AND c.site_id=p_site AND c.kind='volume' AND r.status='complete'
        ORDER BY c.recorded_at DESC,c.id DESC LIMIT 100) x;
    SELECT coalesce(jsonb_agg(x ORDER BY x.recorded_at DESC,x.id DESC),'[]'::jsonb) INTO ideas FROM (
        SELECT k.id,k.input,k.output,k.recorded_at,c.model_release,r.receipt
        FROM app.keyword_idea_sets k JOIN app.model_budget_calls c
            ON (c.tenant_id,c.site_id,c.id)=(k.tenant_id,k.site_id,k.id)
        JOIN app.model_budget_receipts r ON (r.tenant_id,r.site_id,r.call_id)=(k.tenant_id,k.site_id,k.id)
        WHERE k.tenant_id=p_tenant AND k.site_id=p_site
        ORDER BY k.recorded_at DESC,k.id DESC LIMIT 20) x;
    RETURN jsonb_build_object(
        'dataforseo',jsonb_build_object('reason',CASE WHEN volumes='[]'::jsonb THEN
            'No configured provider with completed volume receipts; volume unavailable.' END,'records',volumes),
        'topic_ideas',jsonb_build_object('reason',CASE WHEN ideas='[]'::jsonb THEN
            'No recorded model expansion; ideas unavailable.' END,'records',ideas));
END; $$;
REVOKE ALL ON FUNCTION control.keyword_topic_sources(uuid,uuid) FROM PUBLIC;

ALTER FUNCTION control.seo_strategy_sources(bytea,text,uuid) RENAME TO seo_strategy_sources_0137;
REVOKE ALL ON FUNCTION control.seo_strategy_sources_0137(bytea,text,uuid) FROM PUBLIC,signal_api;

CREATE FUNCTION control.seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE packet jsonb; v record;
BEGIN
    packet:=control.seo_strategy_sources_0137(p_hash,p_generation,p_site);
    IF packet IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    RETURN packet || control.keyword_topic_sources(v.tenant_id,p_site);
END; $$;
REVOKE ALL ON FUNCTION control.seo_strategy_sources(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seo_strategy_sources(bytea,text,uuid) TO signal_api;

-- Reuse 0135's existing registered strategy stage, not a new workload or owner session.
ALTER FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid)
    RENAME TO weekly_skill_seo_strategy_sources_0137;
REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_sources_0137(bytea,text,uuid)
    FROM PUBLIC,signal_workflow;
CREATE FUNCTION control.weekly_skill_seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE packet jsonb; v record;
BEGIN
    PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['strategy_rebuild']);
    SELECT * INTO v FROM control.weekly_skill_context(p_hash,p_generation,p_site);
    packet:=control.weekly_skill_seo_strategy_sources_0137(p_hash,p_generation,p_site);
    IF packet IS NULL THEN RETURN NULL; END IF;
    RETURN packet || control.keyword_topic_sources(v.tenant_id,p_site);
END; $$;
REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) TO signal_workflow;

CREATE FUNCTION control.seo_strategy_record_ideas(p_hash bytea,p_generation text,p_site uuid,p_id uuid,
    p_sources jsonb,p_input jsonb,p_canonical bytea,p_request bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; output jsonb; item jsonb; prior app.keyword_idea_sets%%ROWTYPE;
BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    IF p_canonical IS NULL OR octet_length(p_canonical)>16000 OR p_input IS NULL
        OR jsonb_typeof(p_input)<>'object' OR octet_length(p_input::text)>32000
        THEN RETURN jsonb_build_object('state','invalid'); END IF;
    output:=convert_from(p_canonical,'UTF8')::jsonb;
    IF output - 'ideas'<>'{}'::jsonb OR jsonb_typeof(output->'ideas') IS DISTINCT FROM 'array'
        OR jsonb_array_length(output->'ideas')>20 THEN RETURN jsonb_build_object('state','invalid'); END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(output->'ideas') LOOP
        IF item - ARRAY['cluster_id','query']<>'{}'::jsonb
            OR jsonb_typeof(item->'query') IS DISTINCT FROM 'string'
            OR length(item->>'query') NOT BETWEEN 1 AND 200
            OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_input->'clusters') c
                WHERE c->>'cluster_id'=item->>'cluster_id')
            THEN RETURN jsonb_build_object('state','invalid'); END IF;
    END LOOP;
    IF NOT EXISTS(SELECT 1 FROM app.model_budget_calls c JOIN app.model_budget_receipts r
        ON (r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
        WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_id
            AND c.generation=p_generation AND c.role='topic_ideas' AND c.request_sha256=p_request
            AND r.receipt->>'output_sha256'=encode(sha256(p_canonical),'hex'))
        THEN RETURN jsonb_build_object('state','invalid'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,77));
    SELECT * INTO prior FROM app.keyword_idea_sets WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
    IF FOUND THEN RETURN jsonb_build_object('state',CASE WHEN prior.canonical=p_canonical
        AND prior.input=p_input THEN 'replayed' ELSE 'invalid' END); END IF;
    IF p_sources IS DISTINCT FROM control.seo_strategy_sources(p_hash,p_generation,p_site)
        THEN RETURN jsonb_build_object('state','stale'); END IF;
    INSERT INTO app.keyword_idea_sets VALUES(v.tenant_id,p_site,p_id,p_input,output,p_canonical,transaction_timestamp());
    RETURN jsonb_build_object('state','recorded');
END; $$;
REVOKE ALL ON FUNCTION control.seo_strategy_record_ideas(bytea,text,uuid,uuid,jsonb,jsonb,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seo_strategy_record_ideas(bytea,text,uuid,uuid,jsonb,jsonb,bytea,bytea) TO signal_api;
