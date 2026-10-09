CREATE TABLE app.seo_strategy_snapshots (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    payload jsonb NOT NULL, canonical bytea NOT NULL,
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id), UNIQUE (tenant_id, site_id, version),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites(tenant_id, id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES app.memberships(tenant_id, user_id),
    CHECK (jsonb_typeof(payload) = 'object' AND sha256 = pg_catalog.sha256(canonical))
);
CREATE TABLE app.seo_strategy_item_decisions (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, snapshot_id uuid NOT NULL,
    item_id uuid NOT NULL, decision text NOT NULL CHECK (decision IN ('accepted','dismissed')),
    target_id uuid, target_kind text CHECK (target_kind IN ('brief','inbox')),
    actor_id uuid NOT NULL, decided_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, snapshot_id, item_id),
    UNIQUE (tenant_id, site_id, item_id),
    FOREIGN KEY (tenant_id, site_id, snapshot_id) REFERENCES app.seo_strategy_snapshots(tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES app.memberships(tenant_id, user_id),
    CHECK ((decision = 'dismissed' AND target_id IS NULL AND target_kind IS NULL)
       OR (decision = 'accepted' AND target_id IS NOT NULL AND target_kind IS NOT NULL))
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['seo_strategy_snapshots','seo_strategy_item_decisions'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY seo_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
        EXECUTE format('CREATE TRIGGER seo_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END; $$;

CREATE FUNCTION control.seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; m record; a record; g record; b record; q uuid; facts jsonb; pages jsonb;
    gs jsonb; bs jsonb; observations jsonb; recipes jsonb; inbox jsonb;
BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO m FROM app.crawl_manifests x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.completed_at DESC,x.id DESC LIMIT 1;
    SELECT * INTO a FROM app.crawl_audit_reports x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND x.manifest_id=m.id ORDER BY x.analyzed_at DESC,x.id DESC LIMIT 1;
    SELECT binding_id,event_kind INTO g FROM app.gsc_binding_events x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1;
    SELECT binding_id,event_kind INTO b FROM app.bing_binding_events x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1;
    SELECT x.id INTO q FROM app.ai_visibility_question_sets x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND NOT EXISTS(SELECT 1 FROM app.ai_visibility_question_sets n WHERE n.tenant_id=x.tenant_id AND n.site_id=x.site_id AND n.supersedes_id=x.id)
        ORDER BY x.created_at DESC,x.id DESC LIMIT 1;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.fact_id,'category',x.category,'statement',x.statement,
        'source_kind',x.source_kind,'page_evidence_id',x.page_evidence_id,'document_id',x.document_id,
        'owner_membership_id',x.owner_membership_id,'created_at',x.created_at,'sensitive',f.sensitive) ORDER BY x.fact_id),'[]'::jsonb)
        INTO facts FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) x
        JOIN app.business_brain_facts f ON f.tenant_id=v.tenant_id AND f.site_id=p_site AND f.id=x.fact_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',p.id,'url',u.fetch_url,'title',coalesce(p.title,'')) ORDER BY p.id),'[]'::jsonb)
        INTO pages FROM app.crawl_page_records p JOIN app.urls u ON u.tenant_id=p.tenant_id AND u.site_id=p.site_id AND u.id=p.url_id
        WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.crawl_run_id=m.crawl_run_id AND p.http_status BETWEEN 200 AND 299;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'dimensions',x.dimensions,'rows',x.rows,'coverage',x.coverage,
        'window',jsonb_build_object('start',x.start_date,'end',x.end_date),'imported_at',x.imported_at,
        'aggregation_type',x.aggregation_type) ORDER BY x.dimensions),'[]'::jsonb) INTO gs
        FROM (SELECT DISTINCT ON (dimensions) * FROM app.gsc_import_generations
            WHERE tenant_id=v.tenant_id AND site_id=p_site AND binding_id=g.binding_id AND g.event_kind='bound'
              AND search_type='web' AND data_state='final'
            ORDER BY dimensions,imported_at DESC,id DESC) x;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'rows',x.rows,'coverage',x.coverage,
        'window',NULL,'imported_at',x.imported_at)),'[]'::jsonb) INTO bs
        FROM (SELECT * FROM app.bing_import_generations WHERE tenant_id=v.tenant_id AND site_id=p_site
            AND binding_id=b.binding_id AND b.event_kind='bound' AND kind='performance'
            ORDER BY imported_at DESC,id DESC LIMIT 1) x;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',o.id,'question_id',x.id,'question',x.question,
        'provider',o.provider,'model',o.model,'status',o.status,'provider_evidence_id',o.provider_evidence_id,
        'cited_pages',o.cited_pages,'other_domains',o.other_domains,'observed_at',o.observed_at,'usage',o.usage)
        ORDER BY o.question_id,o.provider),'[]'::jsonb) INTO observations
        FROM (SELECT DISTINCT ON (z.question_id,z.provider) z.* FROM app.ai_visibility_observations z
            JOIN app.ai_visibility_questions y ON y.tenant_id=z.tenant_id AND y.site_id=z.site_id AND y.id=z.question_id
            WHERE z.tenant_id=v.tenant_id AND z.site_id=p_site AND y.question_set_id=q
            ORDER BY z.question_id,z.provider,z.observed_at DESC,z.id DESC) o
        JOIN app.ai_visibility_questions x ON x.tenant_id=o.tenant_id AND x.site_id=o.site_id AND x.id=o.question_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',r.id,'recipe_key',r.recipe_key,
        'autonomy_eligible',control.recipe_autonomy_eligible(r.id)) ORDER BY r.recipe_key),'[]'::jsonb) INTO recipes
        FROM (SELECT DISTINCT ON (x.recipe_key) x.* FROM control.recipe_releases x
            WHERE (SELECT e.status FROM control.recipe_release_events e WHERE e.release_id=x.id ORDER BY e.sequence_number DESC LIMIT 1)='REVIEWED'
            ORDER BY x.recipe_key,x.version_major DESC,x.version_minor DESC,x.version_patch DESC) r;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.revision_id,'finding_id',x.canonical_manifest->>'finding_id') ORDER BY x.revision_id),'[]'::jsonb)
        INTO inbox FROM control.read_authenticated_candidate_recipe_inbox(p_hash,p_site,p_generation) x WHERE x.review_status='pending';
    RETURN jsonb_build_object(
        'crawl',jsonb_build_object('reason',CASE WHEN m.id IS NULL THEN 'No completed crawl evidence.' END,'records',CASE WHEN m.id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object('id',m.id,'coverage',m.coverage,'discovered_count',m.discovered_count,'terminal_count',m.terminal_count,'state_counts',m.state_counts,'completed_at',m.completed_at)) END),
        'technical',jsonb_build_object('reason',CASE WHEN a.id IS NULL THEN 'No technical report for the pinned crawl.' END,'records',CASE WHEN a.id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object('id',a.id,'manifest_id',a.manifest_id,'findings',a.findings,'coverage',a.coverage,'analyzed_at',a.analyzed_at)) END),
        'gsc',jsonb_build_object('reason',CASE WHEN g.event_kind IS DISTINCT FROM 'bound' THEN 'GSC unbound, revoked or reauthorization required.' WHEN gs='[]'::jsonb THEN 'No final web-performance import.' END,'records',gs),
        'bing',jsonb_build_object('reason',CASE WHEN b.event_kind IS DISTINCT FROM 'bound' THEN 'Bing unbound, revoked or reauthorization required.' WHEN bs='[]'::jsonb THEN 'No performance import.' END,'records',bs),
        'ai_visibility',jsonb_build_object('reason',CASE WHEN observations='[]'::jsonb THEN 'No AI-visibility observations.' WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(observations) o WHERE o->>'status'='incomplete') THEN 'Incomplete provider observations; missing citations are not zero.' END,'records',observations),
        'business_brain',jsonb_build_object('reason',CASE WHEN facts='[]'::jsonb THEN 'No current approved business facts.' END,'records',facts),
        'content',jsonb_build_object('reason',CASE WHEN pages='[]'::jsonb THEN 'No successful pages in the pinned crawl.' END,'records',pages),
        'recipes',recipes,'inbox',inbox,
        'page_inventory',coalesce((SELECT jsonb_agg(jsonb_build_object('id',coalesce(p.id,s.id),'url',u.fetch_url,'title',coalesce(p.title,''),'state',s.terminal_state) ORDER BY u.fetch_url)
            FROM app.crawl_frontier f JOIN app.urls u ON u.tenant_id=f.tenant_id AND u.site_id=f.site_id AND u.id=f.url_id
            JOIN app.crawl_frontier_settlements s ON s.tenant_id=f.tenant_id AND s.site_id=f.site_id AND s.frontier_id=f.id
            LEFT JOIN app.crawl_page_records p ON p.tenant_id=f.tenant_id AND p.site_id=f.site_id AND p.frontier_id=f.id
            WHERE f.tenant_id=v.tenant_id AND f.site_id=p_site AND f.crawl_run_id=m.crawl_run_id),'[]'::jsonb),
        'writer_inventory',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.brief_id,'status',x.status,'kind',x.payload->>'kind','created_at',x.created_at) ORDER BY x.brief_id)
            FROM jsonb_to_recordset(control.content_writer_read(p_hash,p_generation,p_site)->'briefs') x(brief_id uuid,status text,payload jsonb,created_at timestamptz)),'[]'::jsonb)
    );
END; $$;

CREATE FUNCTION control.seo_strategy_record(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_canonical bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; p jsonb; current_sources jsonb; previous app.seo_strategy_snapshots%%ROWTYPE; n integer;
BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    IF p_canonical IS NULL OR octet_length(p_canonical)>16000000 THEN RETURN jsonb_build_object('state','invalid'); END IF;
    p:=convert_from(p_canonical,'UTF8')::jsonb;
    IF p->>'version' IS DISTINCT FROM 'seo-baseline-strategy-v1' THEN RETURN jsonb_build_object('state','invalid'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,77));
    current_sources:=control.seo_strategy_sources(p_hash,p_generation,p_site);
    IF p->'sources' IS DISTINCT FROM current_sources THEN RETURN jsonb_build_object('state','stale'); END IF;
    SELECT * INTO previous FROM app.seo_strategy_snapshots s WHERE s.tenant_id=v.tenant_id AND s.site_id=p_site ORDER BY s.version DESC LIMIT 1;
    IF previous.sha256=sha256(p_canonical) THEN RETURN jsonb_build_object('state','replayed','snapshot_id',previous.id); END IF;
    n:=coalesce(previous.version,0)+1;
    INSERT INTO app.seo_strategy_snapshots VALUES(v.tenant_id,p_site,p_id,n,p,p_canonical,sha256(p_canonical),v.user_id,transaction_timestamp());
    RETURN jsonb_build_object('state','recorded','snapshot_id',p_id);
END; $$;

CREATE FUNCTION control.seo_strategy_read(p_hash bytea,p_generation text,p_site uuid,p_snapshot uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; s app.seo_strategy_snapshots%%ROWTYPE;
BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO s FROM app.seo_strategy_snapshots x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site AND (p_snapshot IS NULL OR x.id=p_snapshot) ORDER BY x.version DESC LIMIT 1;
    RETURN jsonb_build_object('snapshot',CASE WHEN s.id IS NOT NULL THEN jsonb_build_object('id',s.id,'version',s.version,'created_at',s.created_at,'sha256',encode(s.sha256,'hex'),'payload',s.payload) END,
        'decisions',coalesce((SELECT jsonb_agg(jsonb_build_object('item_id',d.item_id,'decision',d.decision,'target_id',d.target_id,'target_kind',d.target_kind,'decided_at',d.decided_at) ORDER BY d.item_id) FROM app.seo_strategy_item_decisions d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.payload->'strategy'->'items') i WHERE i->>'id'=d.item_id::text)),'[]'::jsonb));
END; $$;

CREATE FUNCTION control.seo_strategy_decide(p_hash bytea,p_generation text,p_site uuid,p_snapshot uuid,p_item uuid,p_decision text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; s app.seo_strategy_snapshots%%ROWTYPE; prior app.seo_strategy_item_decisions%%ROWTYPE; item jsonb; target uuid; kind text; outcome text;
BEGIN
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,77));
    SELECT * INTO s FROM app.seo_strategy_snapshots x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site ORDER BY x.version DESC LIMIT 1;
    IF s.id IS DISTINCT FROM p_snapshot OR p_decision IS NULL OR p_decision NOT IN ('accepted','dismissed') THEN RETURN jsonb_build_object('state','conflict'); END IF;
    SELECT value INTO item FROM jsonb_array_elements(s.payload->'strategy'->'items') WHERE value->>'id'=p_item::text;
    IF item IS NULL THEN RETURN jsonb_build_object('state','conflict'); END IF;
    SELECT * INTO prior FROM app.seo_strategy_item_decisions d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND d.item_id=p_item;
    IF FOUND THEN RETURN jsonb_build_object('state',CASE WHEN prior.decision=p_decision THEN 'replayed' ELSE 'conflict' END,'target_id',prior.target_id,'target_kind',prior.target_kind); END IF;
    IF p_decision='accepted' THEN
        kind:=item->'action'->>'kind'; target:=gen_random_uuid();
        IF kind='brief' THEN
            outcome:=control.content_writer_create_brief(p_hash,p_generation,p_site,target,item->'action'->'payload','evidence_proposal',NULL);
            IF outcome<>'created' THEN RETURN jsonb_build_object('state','unavailable'); END IF;
        ELSIF kind='inbox' THEN
            target:=(item->'action'->>'revision_id')::uuid;
            IF NOT EXISTS(SELECT 1 FROM control.read_authenticated_candidate_recipe_inbox(p_hash,p_site,p_generation) i WHERE i.revision_id=target AND i.review_status='pending') THEN RETURN jsonb_build_object('state','unavailable'); END IF;
        ELSE RETURN jsonb_build_object('state','unavailable'); END IF;
    END IF;
    INSERT INTO app.seo_strategy_item_decisions VALUES(v.tenant_id,p_site,p_snapshot,p_item,p_decision,target,kind,v.user_id,transaction_timestamp());
    RETURN jsonb_build_object('state',p_decision,'target_id',target,'target_kind',kind);
END; $$;

REVOKE ALL ON FUNCTION control.seo_strategy_sources(bytea,text,uuid),control.seo_strategy_record(bytea,text,uuid,uuid,bytea),control.seo_strategy_read(bytea,text,uuid,uuid),control.seo_strategy_decide(bytea,text,uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seo_strategy_sources(bytea,text,uuid),control.seo_strategy_record(bytea,text,uuid,uuid,bytea),control.seo_strategy_read(bytea,text,uuid,uuid),control.seo_strategy_decide(bytea,text,uuid,uuid,uuid,text) TO signal_api;
