ALTER TABLE app.weekly_cycles ADD COLUMN skill_registry_version integer NOT NULL DEFAULT 0
    CHECK (skill_registry_version IN (0,1));
ALTER TABLE app.weekly_cycles ADD CONSTRAINT weekly_cycle_skill_identity UNIQUE(tenant_id,site_id,id);

CREATE TABLE app.weekly_skill_intents (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, cycle_id uuid NOT NULL,
    stage text NOT NULL CHECK (stage IN ('import_gsc','import_bing','import_ga4',
        'brain_refresh','strategy_rebuild','brief_proposals','report_delivery')),
    handle_hash bytea NOT NULL UNIQUE CHECK (octet_length(handle_hash)=32),
    grant_id uuid NOT NULL, recovery_generation text NOT NULL, recipe_release_id uuid NOT NULL,
    work_type text NOT NULL CHECK (work_type IN ('research_audit','draft_patch')),
    plan jsonb NOT NULL CHECK (jsonb_typeof(plan)='array' AND jsonb_array_length(plan) BETWEEN 1 AND 64),
    reserved_cents bigint NOT NULL CHECK (reserved_cents BETWEEN 0 AND 100000000),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,cycle_id,stage),
    FOREIGN KEY (tenant_id,site_id,cycle_id) REFERENCES app.weekly_cycles(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,grant_id) REFERENCES app.standing_authorizations(tenant_id,site_id,id)
);
CREATE FUNCTION control.weekly_skill_refs_valid(p_refs jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT CASE WHEN jsonb_typeof(p_refs)='array' AND jsonb_array_length(p_refs)<=64
        THEN NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_refs) r
            WHERE jsonb_typeof(r)<>'string' OR length(r #>> '{}') NOT BETWEEN 1 AND 512
                OR NOT (r #>> '{}') ~ '^[A-Za-z0-9:/._#-]+$')
        ELSE false END
$$;
REVOKE ALL ON FUNCTION control.weekly_skill_refs_valid(jsonb) FROM PUBLIC;
CREATE TABLE app.weekly_skill_results (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, cycle_id uuid NOT NULL,
    stage text NOT NULL CHECK (stage IN ('import_gsc','import_bing','import_ga4',
        'brain_refresh','strategy_rebuild','brief_proposals','report_delivery')),
    outcome text NOT NULL CHECK (outcome IN ('completed','unavailable','failed')),
    detail_code text NOT NULL CHECK (detail_code ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    evidence_refs jsonb NOT NULL CHECK (control.weekly_skill_refs_valid(evidence_refs)),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,cycle_id,stage),
    FOREIGN KEY (tenant_id,site_id,cycle_id) REFERENCES app.weekly_cycles(tenant_id,site_id,id)
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['weekly_skill_intents','weekly_skill_results'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY weekly_skill_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
        EXECUTE format('CREATE TRIGGER weekly_skill_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END $$;

CREATE TABLE control.weekly_skill_directory (
    handle_hash bytea PRIMARY KEY CHECK(octet_length(handle_hash)=32),
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, recovery_generation text NOT NULL,
    FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
ALTER TABLE control.weekly_skill_directory ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.weekly_skill_directory FORCE ROW LEVEL SECURITY;
CREATE POLICY weekly_skill_directory_owner ON control.weekly_skill_directory
    TO signal_migrator USING(true) WITH CHECK(true);
REVOKE ALL ON control.weekly_skill_directory FROM PUBLIC,signal_workflow,signal_api,
    signal_identity,signal_scheduler,signal_bootstrap,signal_crawl_ingest,signal_crawl_admission;
CREATE TRIGGER weekly_skill_directory_immutable BEFORE UPDATE OR DELETE
    ON control.weekly_skill_directory FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE FUNCTION control.register_weekly_skill_directory() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    INSERT INTO control.weekly_skill_directory VALUES(NEW.handle_hash,NEW.tenant_id,NEW.site_id,NEW.recovery_generation);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.register_weekly_skill_directory() FROM PUBLIC;
CREATE TRIGGER register_weekly_skill_directory AFTER INSERT ON app.weekly_skill_intents
    FOR EACH ROW EXECUTE FUNCTION control.register_weekly_skill_directory();

CREATE FUNCTION control.weekly_skill_identity(p_cycle uuid,p_label text)
RETURNS uuid LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT (substr(md5(p_cycle::text||p_label),1,8)||'-'||substr(md5(p_cycle::text||p_label),9,4)||'-4'||substr(md5(p_cycle::text||p_label),14,3)||'-8'||substr(md5(p_cycle::text||p_label),18,3)||'-'||substr(md5(p_cycle::text||p_label),21,12))::uuid
$$;
REVOKE ALL ON FUNCTION control.weekly_skill_identity(uuid,text) FROM PUBLIC;

CREATE FUNCTION control.enable_weekly_skills(p_tenant uuid,p_site uuid,p_cycle uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_workflow' THEN RETURN 'permission_denied'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    UPDATE app.weekly_cycles SET skill_registry_version=1
        WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_cycle AND status='running';
    RETURN CASE WHEN FOUND THEN 'enabled' ELSE 'cycle_unavailable' END;
END $$;

CREATE FUNCTION control.weekly_skill_result(p_tenant uuid,p_site uuid,p_cycle uuid,p_stage text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; i record;
BEGIN
    IF session_user<>'signal_workflow' THEN RAISE EXCEPTION 'worker_required' USING ERRCODE='42501'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO r FROM app.weekly_skill_results WHERE tenant_id=p_tenant AND site_id=p_site AND cycle_id=p_cycle AND stage=p_stage;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT * INTO i FROM app.weekly_skill_intents WHERE tenant_id=p_tenant AND site_id=p_site AND cycle_id=p_cycle AND stage=p_stage;
    RETURN jsonb_build_object('outcome',r.outcome,'detail_code',r.detail_code,'evidence_refs',r.evidence_refs,
        'reserved_cents',coalesce(i.reserved_cents,0),'units',coalesce(jsonb_array_length(i.plan),0));
END $$;

CREATE FUNCTION control.record_weekly_skill(p_tenant uuid,p_site uuid,p_cycle uuid,p_stage text,p_outcome text,p_detail text,p_refs jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;
BEGIN
    IF session_user<>'signal_workflow' THEN RAISE EXCEPTION 'worker_required' USING ERRCODE='42501'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    IF NOT EXISTS(SELECT 1 FROM app.weekly_cycles WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_cycle AND skill_registry_version=1) THEN RETURN 'cycle_unavailable'; END IF;
    IF p_outcome='completed' AND NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents WHERE tenant_id=p_tenant AND site_id=p_site AND cycle_id=p_cycle AND stage=p_stage) THEN RETURN 'intent_required'; END IF;
    INSERT INTO app.weekly_skill_results(tenant_id,site_id,cycle_id,stage,outcome,detail_code,evidence_refs)
        VALUES(p_tenant,p_site,p_cycle,p_stage,p_outcome,p_detail,p_refs) ON CONFLICT DO NOTHING;
    SELECT * INTO r FROM app.weekly_skill_results WHERE tenant_id=p_tenant AND site_id=p_site AND cycle_id=p_cycle AND stage=p_stage;
    RETURN CASE WHEN r.outcome=p_outcome AND r.detail_code=p_detail AND r.evidence_refs=p_refs THEN 'recorded' ELSE 'conflict' END;
END $$;

-- Scope is a workload handle, never a human identity session. Existing standing
-- eligibility rechecks pause, revocation, expiry, owner epochs and release status.
CREATE FUNCTION control.weekly_skill_context(p_hash bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid,user_id uuid,membership_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i record; e record; tenant uuid; unit jsonb;
BEGIN
    IF session_user<>'signal_workflow' OR p_generation IS NULL THEN RAISE EXCEPTION 'worker_required' USING ERRCODE='42501'; END IF;
    SELECT d.tenant_id INTO tenant FROM control.weekly_skill_directory d
        WHERE d.handle_hash=p_hash AND d.site_id=p_site AND d.recovery_generation=p_generation;
    IF tenant IS NULL THEN RAISE EXCEPTION 'skill_scope_denied' USING ERRCODE='42501'; END IF;
    PERFORM set_config('signal.tenant_id',tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash AND site_id=p_site AND recovery_generation=p_generation;
    IF NOT FOUND THEN RAISE EXCEPTION 'skill_scope_denied' USING ERRCODE='42501'; END IF;
    SELECT * INTO e FROM control.standing_grant_eligibility(i.tenant_id,p_site,i.grant_id,p_generation,i.recipe_release_id,i.work_type,'/');
    IF NOT e.eligible OR EXISTS(SELECT 1 FROM app.weekly_skill_results r WHERE r.tenant_id=i.tenant_id AND r.site_id=p_site AND r.cycle_id=i.cycle_id AND r.stage=i.stage)
       OR NOT EXISTS(SELECT 1 FROM app.weekly_cycles c WHERE c.tenant_id=i.tenant_id AND c.site_id=p_site AND c.id=i.cycle_id
            AND (c.status='running' OR i.stage='report_delivery')) THEN
        RAISE EXCEPTION 'skill_authority_changed' USING ERRCODE='42501';
    END IF;
    FOR unit IN SELECT value FROM jsonb_array_elements(i.plan) LOOP
        SELECT * INTO e FROM control.standing_grant_eligibility(i.tenant_id,p_site,i.grant_id,p_generation,i.recipe_release_id,i.work_type,coalesce(unit->>'resource_path','/'));
        IF NOT e.eligible THEN RAISE EXCEPTION 'skill_authority_changed' USING ERRCODE='42501'; END IF;
    END LOOP;
    PERFORM pg_advisory_xact_lock(hashtextextended(i.tenant_id::text||p_site::text,73));
    RETURN QUERY SELECT i.tenant_id,g.owner_user_id,m.id FROM app.standing_authorizations g
        JOIN app.memberships m ON m.tenant_id=g.tenant_id AND m.user_id=g.owner_user_id
        WHERE g.tenant_id=i.tenant_id AND g.site_id=p_site AND g.id=i.grant_id;
END $$;

CREATE FUNCTION control.assert_weekly_skill_stage(p_hash bytea,p_generation text,p_site uuid,p_stages text[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    PERFORM control.weekly_skill_context(p_hash,p_generation,p_site);
    IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents WHERE handle_hash=p_hash AND stage=ANY(p_stages)) THEN
        RAISE EXCEPTION 'skill_port_denied' USING ERRCODE='42501';
    END IF;
END $$;

CREATE FUNCTION control.admit_weekly_skill(p_tenant uuid,p_site uuid,p_cycle uuid,p_grant uuid,p_generation text,p_stage text,p_hash bytea,p_cost bigint,p_configured boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c record; g record; e record; r uuid; kind text; plan jsonb:='[]'; unit jsonb;
    cap integer; used integer; result text; operation uuid; digest bytea; existing jsonb;
BEGIN
    IF session_user<>'signal_workflow' OR p_hash IS NULL OR octet_length(p_hash)<>32
        OR p_tenant IS NULL OR p_site IS NULL OR p_cycle IS NULL OR p_grant IS NULL OR p_generation IS NULL
        OR p_stage IS NULL OR p_stage NOT IN ('import_gsc','import_bing','import_ga4','brain_refresh','strategy_rebuild','brief_proposals','report_delivery')
        OR p_cost IS NULL OR p_cost NOT BETWEEN 0 AND 100000000 OR p_configured IS NULL THEN
        RAISE EXCEPTION 'invalid_skill_admission' USING ERRCODE='42501';
    END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO c FROM app.weekly_cycles WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_cycle AND grant_id=p_grant
        AND skill_registry_version=1 AND (status='running' OR p_stage='report_delivery') FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('state','unavailable','reason','CYCLE_UNAVAILABLE'); END IF;
    existing:=control.weekly_skill_result(p_tenant,p_site,p_cycle,p_stage);
    IF existing IS NOT NULL THEN RETURN jsonb_build_object('state','replayed','result',existing); END IF;
    IF EXISTS(SELECT 1 FROM app.weekly_skill_intents WHERE tenant_id=p_tenant AND site_id=p_site AND cycle_id=p_cycle AND stage=p_stage) THEN
        RETURN jsonb_build_object('state','unknown','reason','OUTCOME_UNKNOWN');
    END IF;
    kind:=CASE WHEN p_stage='brief_proposals' THEN 'draft_patch' ELSE 'research_audit' END;
    SELECT * INTO g FROM app.standing_authorizations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_grant;
    IF NOT FOUND OR NOT kind=ANY(g.work_types) THEN RETURN jsonb_build_object('state','unavailable','reason','WORK_TYPE_NOT_GRANTED'); END IF;
    SELECT x.id INTO r FROM control.recipe_releases x WHERE x.id=ANY(g.recipe_release_ids)
        AND convert_from(x.canonical_body,'UTF8')::jsonb->>'approval_class'=CASE WHEN kind='research_audit' THEN 'A0' ELSE 'A1' END
        AND (SELECT status FROM control.recipe_release_events WHERE release_id=x.id ORDER BY sequence_number DESC LIMIT 1)='REVIEWED'
        AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=x.id)
        ORDER BY x.id LIMIT 1;
    IF r IS NULL THEN RETURN jsonb_build_object('state','unavailable','reason','REVIEWED_RELEASE_UNAVAILABLE'); END IF;
    SELECT * INTO e FROM control.standing_grant_eligibility(p_tenant,p_site,p_grant,p_generation,r,kind,'/');
    IF NOT e.eligible THEN RETURN jsonb_build_object('state','unavailable','reason','AUTHORITY_CHANGED'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_tenant::text||p_site::text,73));
    IF NOT p_configured THEN RETURN jsonb_build_object('state','unavailable','reason','PORT_UNCONFIGURED'); END IF;
    IF p_stage='brain_refresh' AND p_cost=0 THEN RETURN jsonb_build_object('state','unavailable','reason','COST_BOUND_UNCONFIGURED'); END IF;
    IF p_stage<>'brain_refresh' AND p_cost<>0 THEN RETURN jsonb_build_object('state','unavailable','reason','COST_BOUND_INVALID'); END IF;
    IF p_stage='import_gsc' THEN
        SELECT coalesce(jsonb_agg(jsonb_build_object('binding_id',binding_id,'resource_path','/connectors/gsc')),'[]') INTO plan FROM control.current_gsc_binding(p_tenant,p_site);
    ELSIF p_stage='import_bing' THEN
        SELECT coalesce(jsonb_agg(jsonb_build_object('binding_id',binding_id,'resource_path','/connectors/bing')),'[]') INTO plan FROM control.current_bing_binding(p_tenant,p_site);
    ELSIF p_stage='import_ga4' THEN
        SELECT coalesce(jsonb_agg(jsonb_build_object('binding_id',binding_id,'resource_path','/connectors/ga4')),'[]') INTO plan FROM control.current_ga4_binding(p_tenant,p_site,p_generation);
    ELSIF p_stage='brain_refresh' THEN
        SELECT coalesce(jsonb_agg(x.item ORDER BY x.id),'[]') INTO plan FROM (
            SELECT p.id,jsonb_build_object('source_id',p.id,'source_kind','page_evidence','resource_path',
                coalesce(substring(u.fetch_url FROM '^https?://[^/]+(/[^?#]*)'),'/')) item
            FROM app.crawl_page_records p JOIN app.crawl_manifests m ON m.tenant_id=p.tenant_id AND m.site_id=p.site_id AND m.crawl_run_id=p.crawl_run_id
                JOIN app.urls u ON u.tenant_id=p.tenant_id AND u.site_id=p.site_id AND u.id=p.url_id
            WHERE p.tenant_id=p_tenant AND p.site_id=p_site AND p.http_status BETWEEN 200 AND 299
                AND m.id=(SELECT id FROM app.crawl_manifests WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY completed_at DESC,id DESC LIMIT 1)
                AND NOT EXISTS(SELECT 1 FROM app.business_brain_extractions b JOIN app.crawl_page_records old ON old.tenant_id=b.tenant_id AND old.site_id=b.site_id AND old.id=b.source_id
                    WHERE b.tenant_id=p_tenant AND b.site_id=p_site AND b.source_kind='page_evidence' AND old.url_id=p.url_id AND old.body_sha256=p.body_sha256 AND b.extraction_version='business-brain-v1')
                AND NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) planned(value)
                    JOIN app.crawl_page_records old ON old.tenant_id=i.tenant_id AND old.site_id=i.site_id AND old.id=(planned.value->>'source_id')::uuid
                    WHERE i.tenant_id=p_tenant AND i.site_id=p_site AND i.stage='brain_refresh' AND planned.value->>'source_kind'='page_evidence'
                    AND old.url_id=p.url_id AND old.body_sha256=p.body_sha256)
            UNION ALL
            SELECT d.id,jsonb_build_object('source_id',d.id,'source_kind','brand_document','resource_path','/documents/'||d.id::text) FROM app.brand_documents d
            WHERE d.tenant_id=p_tenant AND d.site_id=p_site AND NOT d.secret_signal
                AND NOT EXISTS(SELECT 1 FROM app.brand_document_events b WHERE b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.document_id=d.id AND b.event_type='deleted')
                AND NOT EXISTS(SELECT 1 FROM app.brand_documents n WHERE n.tenant_id=d.tenant_id AND n.site_id=d.site_id AND n.supersedes_id=d.id)
                AND NOT EXISTS(SELECT 1 FROM app.business_brain_extractions b JOIN app.brand_documents old ON old.tenant_id=b.tenant_id AND old.site_id=b.site_id AND old.id=b.source_id
                    WHERE b.tenant_id=p_tenant AND b.site_id=p_site AND b.source_kind='brand_document' AND old.text_sha256=d.text_sha256 AND b.extraction_version='business-brain-v1')
                AND NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) planned(value)
                    JOIN app.brand_documents old ON old.tenant_id=i.tenant_id AND old.site_id=i.site_id AND old.id=(planned.value->>'source_id')::uuid
                    WHERE i.tenant_id=p_tenant AND i.site_id=p_site AND i.stage='brain_refresh' AND planned.value->>'source_kind'='brand_document' AND old.text_sha256=d.text_sha256)
            ORDER BY id LIMIT 65
        ) x;
    ELSIF p_stage='brief_proposals' THEN
        IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_results WHERE tenant_id=p_tenant AND site_id=p_site AND cycle_id=p_cycle AND stage='strategy_rebuild' AND outcome='completed') THEN
            RETURN jsonb_build_object('state','unavailable','reason','DEPENDENCY_UNAVAILABLE'); END IF;
        SELECT coalesce((SELECT x.cap FROM app.content_writer_caps x WHERE x.tenant_id=p_tenant AND x.site_id=p_site ORDER BY x.created_at DESC,x.id DESC LIMIT 1),2) INTO cap;
        SELECT count(*) INTO used FROM (
            SELECT b.id FROM app.content_briefs b WHERE b.tenant_id=p_tenant AND b.site_id=p_site AND b.origin='evidence_proposal' AND b.created_at>transaction_timestamp()-interval '7 days'
            UNION SELECT d.brief_id FROM app.content_draft_intents d WHERE d.tenant_id=p_tenant AND d.site_id=p_site AND d.created_at>transaction_timestamp()-interval '7 days'
            UNION SELECT (u->>'brief_id')::uuid FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u
                WHERE i.tenant_id=p_tenant AND i.site_id=p_site AND i.stage='brief_proposals' AND i.created_at>transaction_timestamp()-interval '7 days'
        ) occupied;
        IF used>=cap THEN RETURN jsonb_build_object('state','unavailable','reason','WRITER_CAP_REACHED'); END IF;
        SELECT coalesce(jsonb_agg(jsonb_build_object('item_id',x.item->>'id','payload',x.item->'action'->'payload',
            'brief_id',control.weekly_skill_identity(p_cycle,x.item->>'id'),'resource_path','/briefs')),'[]') INTO plan
        FROM (SELECT item FROM app.seo_strategy_snapshots s CROSS JOIN LATERAL jsonb_array_elements(s.payload->'strategy'->'items') item
            WHERE s.tenant_id=p_tenant AND s.site_id=p_site AND s.id=(SELECT id FROM app.seo_strategy_snapshots WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY version DESC LIMIT 1)
                AND item->'action'->>'kind'='brief'
                AND NOT EXISTS(SELECT 1 FROM app.seo_strategy_item_decisions d WHERE d.tenant_id=p_tenant AND d.site_id=p_site AND d.item_id=(item->>'id')::uuid)
            ORDER BY item->>'id' LIMIT greatest(0,cap-used)) x;
    ELSIF p_stage='report_delivery' THEN
        IF c.status='running' THEN RETURN jsonb_build_object('state','unavailable','reason','REPORT_NOT_CLOSED'); END IF;
        IF NOT EXISTS(SELECT 1 FROM control.email_smtp_configuration) THEN RETURN jsonb_build_object('state','unavailable','reason','SMTP_UNCONFIGURED'); END IF;
        SELECT coalesce(jsonb_agg(jsonb_build_object('preference_id',id,'membership_id',membership_id,'resource_path','/reports')),'[]') INTO plan
            FROM control.weekly_skill_email_recipients(p_tenant,p_site,p_generation);
        IF plan='[]'::jsonb THEN RETURN jsonb_build_object('state','unavailable','reason','NO_VERIFIED_RECIPIENT'); END IF;
    ELSE plan:=jsonb_build_array(jsonb_build_object('cycle_id',p_cycle,'resource_path','/strategy'));
    END IF;
    IF plan='[]'::jsonb THEN RETURN jsonb_build_object('state','unavailable','reason',CASE
        WHEN p_stage='brain_refresh' THEN 'NO_CHANGED_SOURCES' WHEN p_stage='brief_proposals' THEN 'NO_ELIGIBLE_BRIEFS' ELSE 'NO_ACTIVE_BINDING' END); END IF;
    IF jsonb_array_length(plan)>64 THEN RETURN jsonb_build_object('state','unavailable','reason','BATCH_CAP_REACHED'); END IF;
    -- Subtransaction makes aggregate reservations all-or-none; no partial cap consumption.
    BEGIN
        FOR unit IN SELECT value FROM jsonb_array_elements(plan) LOOP
            digest:=sha256(convert_to(p_cycle::text||p_stage||unit::text,'UTF8'));
            operation:=(substr(encode(digest,'hex'),1,8)||'-'||substr(encode(digest,'hex'),9,4)||'-4'||substr(encode(digest,'hex'),14,3)||'-8'||substr(encode(digest,'hex'),18,3)||'-'||substr(encode(digest,'hex'),21,12))::uuid;
            result:=control.reserve_standing_budget(p_tenant,p_site,p_grant,p_generation,r,kind,unit->>'resource_path',operation,digest,p_cost);
            IF result<>'reserved' THEN RAISE EXCEPTION 'skill_budget_denied' USING ERRCODE='P0135'; END IF;
        END LOOP;
    EXCEPTION WHEN SQLSTATE 'P0135' THEN RETURN jsonb_build_object('state','unavailable','reason',upper(result)); END;
    INSERT INTO app.weekly_skill_intents(tenant_id,site_id,cycle_id,stage,handle_hash,grant_id,recovery_generation,recipe_release_id,work_type,plan,reserved_cents)
        VALUES(p_tenant,p_site,p_cycle,p_stage,p_hash,p_grant,p_generation,r,kind,plan,p_cost*jsonb_array_length(plan));
    RETURN jsonb_build_object('state','started','plan',plan,'reserved_cents',p_cost*jsonb_array_length(plan),'units',jsonb_array_length(plan));
END $$;

CREATE FUNCTION control.weekly_skill_email_recipients(p_tenant uuid,p_site uuid,p_generation text)
RETURNS SETOF app.email_preferences LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT p.* FROM app.email_preferences p JOIN app.memberships m ON m.tenant_id=p.tenant_id AND m.id=p.membership_id
        JOIN control.email_claim_heads h ON h.user_id=p.user_id AND h.claim_epoch=p.claim_epoch AND h.address_sha256=p.address_sha256
    WHERE p.tenant_id=p_tenant AND p.enabled AND p.recovery_generation=p_generation
        AND m.state='active' AND m.role_key='owner' AND m.authorization_epoch=p.membership_epoch
        AND p.membership_change_count=(SELECT count(*) FROM control.email_membership_changes WHERE tenant_id=p_tenant AND membership_id=m.id)
        AND NOT EXISTS(SELECT 1 FROM app.email_preferences n WHERE n.tenant_id=p_tenant AND n.membership_id=p.membership_id AND n.preference_order>p.preference_order)
        AND NOT EXISTS(SELECT 1 FROM control.users u WHERE u.id=p.user_id AND u.disabled_at IS NOT NULL)
        AND EXISTS(SELECT 1 FROM control.resolve_member_site_authority(p_tenant,p.user_id,p_site,'primary') a WHERE a.outcome='authorized')
        AND NOT EXISTS(SELECT 1 FROM app.email_send_receipts r JOIN app.email_outbox o ON o.tenant_id=r.tenant_id AND o.site_id=r.site_id AND o.id=r.outbox_id
            WHERE r.tenant_id=p_tenant AND o.membership_id=p.membership_id AND r.recipient_sha256=p.address_sha256 AND r.provider_response_class='bounced')
$$;
REVOKE ALL ON FUNCTION control.weekly_skill_email_recipients(uuid,uuid,text) FROM PUBLIC;

REVOKE ALL ON FUNCTION control.enable_weekly_skills(uuid,uuid,uuid),control.weekly_skill_result(uuid,uuid,uuid,text),
    control.record_weekly_skill(uuid,uuid,uuid,text,text,text,jsonb),control.weekly_skill_context(bytea,text,uuid),
    control.assert_weekly_skill_stage(bytea,text,uuid,text[]),control.admit_weekly_skill(uuid,uuid,uuid,uuid,text,text,bytea,bigint,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.enable_weekly_skills(uuid,uuid,uuid),control.weekly_skill_result(uuid,uuid,uuid,text),
    control.record_weekly_skill(uuid,uuid,uuid,text,text,text,jsonb),control.weekly_skill_context(bytea,text,uuid),
    control.admit_weekly_skill(uuid,uuid,uuid,uuid,text,text,bytea,bigint,boolean) TO signal_workflow;

CREATE FUNCTION control.assert_weekly_skill_resource(p_hash bytea,p_generation text,p_site uuid,p_key text,p_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i record; source uuid;
BEGIN
    PERFORM control.weekly_skill_context(p_hash,p_generation,p_site);
    SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
    source:=p_id;
    IF p_key='extraction_id' THEN
        SELECT source_id INTO source FROM app.business_brain_extractions WHERE tenant_id=i.tenant_id AND site_id=p_site AND id=p_id;
        p_key:='source_id';
    END IF;
    IF p_key NOT IN ('source_id','binding_id','brief_id') OR source IS NULL
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(i.plan) u WHERE u->>p_key=source::text) THEN
        RAISE EXCEPTION 'skill_resource_denied' USING ERRCODE='42501';
    END IF;
END $$;
REVOKE ALL ON FUNCTION control.assert_weekly_skill_resource(bytea,text,uuid,text,uuid) FROM PUBLIC;

ALTER TABLE app.model_budget_calls ADD COLUMN weekly_skill_handle_hash bytea
    REFERENCES control.weekly_skill_directory(handle_hash);
CREATE FUNCTION control.weekly_skill_page_type_model_id(p_decision uuid)
RETURNS uuid LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    WITH digest AS (SELECT substring(sha256(uuid_send(p_decision)||convert_to('brain-page-type','UTF8')) FROM 1 FOR 16) AS b)
    SELECT encode(set_byte(set_byte(b,6,(get_byte(b,6)&15)|64),8,(get_byte(b,8)&63)|128),'hex')::uuid FROM digest
$$;
REVOKE ALL ON FUNCTION control.weekly_skill_page_type_model_id(uuid) FROM PUBLIC;

CREATE FUNCTION control.assert_weekly_skill_model_reservation(p_hash bytea,p_generation text,p_site uuid,
    p_id uuid,p_role text,p_amount bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i app.weekly_skill_intents%%ROWTYPE; e app.business_brain_extractions%%ROWTYPE; used bigint;
BEGIN
    PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['brain_refresh']);
    SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
    SELECT x.* INTO e FROM app.business_brain_extractions x CROSS JOIN LATERAL jsonb_array_elements(i.plan) u
        WHERE x.tenant_id=i.tenant_id AND x.site_id=p_site AND x.source_id::text=u->>'source_id'
        AND x.source_kind=u->>'source_kind' AND x.id=control.weekly_skill_identity(i.cycle_id,x.source_id::text)
        AND p_id=CASE WHEN p_role='page_type' THEN control.weekly_skill_page_type_model_id(x.decision_id)
            ELSE x.model_operation_id END;
    IF NOT FOUND OR p_role IS NULL OR p_role NOT IN ('page_type','fact_extraction') THEN
        RAISE EXCEPTION 'skill_model_resource_denied' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id=i.tenant_id AND id=p_site FOR NO KEY UPDATE;
    SELECT coalesce(sum(greatest(c.reserved_micros,coalesce(r.cost_micros,c.reserved_micros))),0) INTO used
        FROM app.model_budget_calls c LEFT JOIN app.model_budget_receipts r
        ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.call_id=c.id
        WHERE c.weekly_skill_handle_hash=p_hash AND c.id<>p_id
        AND c.id IN (e.model_operation_id,control.weekly_skill_page_type_model_id(e.decision_id));
    IF p_amount IS NULL OR p_amount<1 OR used+p_amount>(i.reserved_cents/jsonb_array_length(i.plan))*10000 THEN
        RAISE EXCEPTION 'skill_model_cost_bound_exhausted' USING ERRCODE='42501';
    END IF;
END $$;
REVOKE ALL ON FUNCTION control.assert_weekly_skill_model_reservation(bytea,text,uuid,uuid,text,bigint) FROM PUBLIC;

CREATE FUNCTION control.assert_weekly_skill_model_call(p_hash bytea,p_generation text,p_site uuid,p_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['brain_refresh']);
    IF NOT EXISTS(SELECT 1 FROM app.model_budget_calls WHERE id=p_id AND weekly_skill_handle_hash=p_hash) THEN
        RAISE EXCEPTION 'skill_model_call_denied' USING ERRCODE='42501';
    END IF;
END $$;
REVOKE ALL ON FUNCTION control.assert_weekly_skill_model_call(bytea,text,uuid,uuid) FROM PUBLIC;

-- Generate additive ports from the already qualified local implementation, with
-- a closed list, replaced authority resolver and stage/resource checks. No owner
-- approvals, acceptances, grant changes, uploads or publishing ports are copied.
DO $$ DECLARE f record; definition text; other record; args text; prefix text; guard text;
BEGIN
    FOR f IN SELECT * FROM (VALUES
        ('business_brain_read','brain_refresh,strategy_rebuild,brief_proposals',''),
        ('list_business_brain_facts','brain_refresh,strategy_rebuild,brief_proposals',''),
        ('approved_business_brain_facts','brain_refresh,strategy_rebuild,brief_proposals',''),
        ('business_brain_page_source','brain_refresh','source_id:p_page_id'),
        ('business_brain_begin_extraction','brain_refresh','source_id:p_source_id'),
        ('business_brain_finish_extraction','brain_refresh','extraction_id:p_id'),
        ('read_brand_document_artifact','brain_refresh','source_id:p_document_id'),
        ('content_writer_inventory','strategy_rebuild,brief_proposals',''),
        ('content_writer_read','strategy_rebuild,brief_proposals',''),
        ('content_writer_read_0082','strategy_rebuild,brief_proposals',''),
        ('model_budget_read','brain_refresh,strategy_rebuild,brief_proposals',''),
        ('model_budget_reserve','brain_refresh',''),
        ('model_budget_dispatch','brain_refresh',''),
        ('model_budget_finish','brain_refresh',''),
        ('content_writer_create_brief','brief_proposals','brief_id:p_id'),
        ('seo_strategy_sources','strategy_rebuild',''),
        ('seo_strategy_record','strategy_rebuild',''),
        ('seo_strategy_read','brief_proposals',''),
        ('ga4_owner_binding','import_ga4',''),
        ('record_ga4_owner_import','import_ga4','binding_id:p_binding'),
        ('ga4_owner_reauth','import_ga4','binding_id:p_binding')
    ) ports(name,stages,resource) LOOP
        SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid)
            INTO STRICT definition,args FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='control' AND p.proname=f.name;
        prefix:=CASE WHEN f.name LIKE 'business_brain_%%' OR f.name IN ('list_business_brain_facts','approved_business_brain_facts','read_brand_document_artifact') THEN 'p_session_hash'
            WHEN f.name LIKE '%%ga4%%' THEN 'p_session' ELSE 'p_hash' END;
        guard:=format('PERFORM control.assert_weekly_skill_stage(%%s,p_generation,%%s,%%L::text[]);',prefix,
            CASE WHEN prefix='p_session_hash' THEN 'p_site_id' ELSE 'p_site' END,
            '{'||f.stages||'}');
        IF f.resource<>'' THEN
            guard:=guard||format('PERFORM control.assert_weekly_skill_resource(%%s,p_generation,%%s,%%L,%%s);',prefix,
                CASE WHEN prefix='p_session_hash' THEN 'p_site_id' ELSE 'p_site' END,split_part(f.resource,':',1),split_part(f.resource,':',2));
        END IF;
        IF f.name='content_writer_create_brief' THEN
            guard:=guard||'IF p_origin IS DISTINCT FROM ''evidence_proposal'' OR p_supersedes IS NOT NULL OR NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_hash AND u->>''brief_id''=p_id::text AND u->''payload''=p_payload) THEN RETURN ''denied''; END IF; IF EXISTS(SELECT 1 FROM app.content_briefs b JOIN app.weekly_skill_intents i ON i.tenant_id=b.tenant_id AND i.site_id=b.site_id WHERE i.handle_hash=p_hash AND b.id=p_id AND b.payload=p_payload AND b.origin=''evidence_proposal'') THEN RETURN ''created''; END IF;';
        END IF;
        IF f.name='business_brain_begin_extraction' THEN
            guard:=guard||'IF p_version IS DISTINCT FROM ''business-brain-v1'' OR p_id IS DISTINCT FROM (SELECT control.weekly_skill_identity(cycle_id,p_source_id::text) FROM app.weekly_skill_intents WHERE handle_hash=p_session_hash) OR NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session_hash AND u->>''source_id''=p_source_id::text AND u->>''source_kind''=p_source_kind) OR NOT ((p_source_kind=''page_evidence'' AND p_start=0 AND p_end=0) OR (p_source_kind=''brand_document'' AND p_start=0 AND p_end BETWEEN 1 AND 24000)) THEN RAISE EXCEPTION ''skill_extraction_scope_denied'' USING ERRCODE=''42501''; END IF;';
        END IF;
        IF f.name='seo_strategy_record' THEN
            guard:=guard||'IF p_id IS DISTINCT FROM (SELECT control.weekly_skill_identity(cycle_id,''strategy_rebuild'') FROM app.weekly_skill_intents WHERE handle_hash=p_hash) THEN RAISE EXCEPTION ''skill_snapshot_scope_denied'' USING ERRCODE=''42501''; END IF; IF EXISTS(SELECT 1 FROM app.seo_strategy_snapshots s JOIN app.weekly_skill_intents i ON i.tenant_id=s.tenant_id AND i.site_id=s.site_id WHERE i.handle_hash=p_hash AND s.id=p_id) THEN IF EXISTS(SELECT 1 FROM app.seo_strategy_snapshots s JOIN app.weekly_skill_intents i ON i.tenant_id=s.tenant_id AND i.site_id=s.site_id WHERE i.handle_hash=p_hash AND s.id=p_id AND s.sha256=sha256(p_canonical)) THEN RETURN jsonb_build_object(''state'',''replayed'',''snapshot_id'',p_id); ELSE RETURN jsonb_build_object(''state'',''stale''); END IF; END IF;';
            definition:=replace(definition,'IF previous.sha256=sha256(p_canonical) THEN RETURN jsonb_build_object(''state'',''replayed'',''snapshot_id'',previous.id); END IF;','');
        END IF;
        IF f.name='model_budget_reserve' THEN
            guard:=guard||'PERFORM control.assert_weekly_skill_model_reservation(p_hash,p_generation,p_site,p_id,p_role,p_amount);';
            definition:=replace(definition,'model_release,reserved_micros)',
                'model_release,reserved_micros,weekly_skill_handle_hash)');
            definition:=replace(definition,'p_role,p_release,p_amount);','p_role,p_release,p_amount,p_hash);');
        ELSIF f.name IN ('model_budget_dispatch','model_budget_finish') THEN
            guard:=guard||'PERFORM control.assert_weekly_skill_model_call(p_hash,p_generation,p_site,p_id);';
        END IF;
        IF definition LIKE '%%LANGUAGE plpgsql%%' THEN
            definition:=regexp_replace(definition,'BEGIN','BEGIN '||guard);
        END IF;
        definition:=replace(definition,'control.business_brain_owner(', 'control.weekly_skill_context(');
        definition:=replace(definition,'control.brand_document_owner(', 'control.weekly_skill_context(');
        definition:=replace(definition,'control.ga4_owner_context(', 'control.weekly_skill_context(');
        IF f.name='ga4_owner_binding' THEN
            definition:=replace(definition,'p_generation) b;', 'p_generation) b WHERE EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session AND u->>''binding_id''=b.binding_id::text);');
        END IF;
        FOR other IN SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='control' AND p.proname=ANY(ARRAY['business_brain_read','list_business_brain_facts','approved_business_brain_facts',
                'business_brain_page_source','business_brain_begin_extraction','business_brain_finish_extraction','read_brand_document_artifact',
                'content_writer_inventory','content_writer_read','content_writer_read_0082','content_writer_create_brief','seo_strategy_sources','seo_strategy_record','seo_strategy_read',
                'model_budget_read','model_budget_reserve','model_budget_dispatch','model_budget_finish',
                'ga4_owner_binding','record_ga4_owner_import','ga4_owner_reauth']) LOOP
            definition:=replace(definition,'control.'||other.proname||'(', 'control.weekly_skill_'||other.proname||'(');
        END LOOP;
        definition:=replace(definition,'control.read_authenticated_candidate_recipe_inbox(', 'control.weekly_skill_candidate_inbox(');
        IF definition LIKE '%%control.resolve_snapshot_authority(%%' OR definition LIKE '%%control.business_brain_owner(%%' THEN
            RAISE EXCEPTION 'unreviewed_skill_port_authority'; END IF;
        EXECUTE definition;
        EXECUTE format('REVOKE ALL ON FUNCTION control.weekly_skill_%%I(%%s) FROM PUBLIC',f.name,args);
        EXECUTE format('GRANT EXECUTE ON FUNCTION control.weekly_skill_%%I(%%s) TO signal_workflow',f.name,args);
    END LOOP;
END $$;

ALTER FUNCTION control.weekly_report_projection(uuid,uuid,date) RENAME TO weekly_report_projection_without_skills;
CREATE FUNCTION control.weekly_report_projection(p_tenant uuid,p_site uuid,p_week date)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE report jsonb; c record;
BEGIN
    report:=control.weekly_report_projection_without_skills(p_tenant,p_site,p_week);
    SELECT * INTO c FROM app.weekly_cycles WHERE tenant_id=p_tenant AND site_id=p_site AND week_start=p_week;
    IF report IS NULL OR c.skill_registry_version=0 THEN RETURN report; END IF;
    RETURN report||jsonb_build_object('skill_stages',coalesce((SELECT jsonb_agg(jsonb_build_object(
        'stage',s.stage,'outcome',coalesce(r.outcome,CASE WHEN i.stage IS NULL THEN 'unavailable' ELSE 'failed' END),
        'detail_code',coalesce(r.detail_code,CASE WHEN i.stage IS NULL THEN 'NOT_STARTED' ELSE 'OUTCOME_UNKNOWN' END),
        'work_type',CASE WHEN s.stage='brief_proposals' THEN 'draft_patch' ELSE 'research_audit' END,
        'budget_source','standing_authorization','cap_source',CASE WHEN s.stage='brief_proposals' THEN 'standing_authorization_and_content_writer' WHEN s.stage='report_delivery' THEN 'standing_authorization_and_email' ELSE 'standing_authorization' END,
        'reserved_cents',coalesce(i.reserved_cents,0),'units',coalesce(jsonb_array_length(i.plan),0),
        'spend_status',CASE WHEN coalesce(i.reserved_cents,0)>0 THEN 'upper_bound_reserved' ELSE 'no_paid_io' END,
        'evidence_refs',coalesce(r.evidence_refs,'[]'::jsonb),'recorded_at',r.recorded_at) ORDER BY s.position)
        FROM unnest(ARRAY['import_gsc','import_bing','import_ga4','brain_refresh','strategy_rebuild','brief_proposals','report_delivery']) WITH ORDINALITY s(stage,position)
        LEFT JOIN app.weekly_skill_intents i ON i.tenant_id=p_tenant AND i.site_id=p_site AND i.cycle_id=c.id AND i.stage=s.stage
        LEFT JOIN app.weekly_skill_results r ON r.tenant_id=p_tenant AND r.site_id=p_site AND r.cycle_id=c.id AND r.stage=s.stage),'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION control.weekly_report_projection(uuid,uuid,date) FROM PUBLIC;

CREATE FUNCTION control.read_latest_weekly_skill_report(p_hash bytea,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; week date;
BEGIN
    IF session_user<>'signal_api' THEN RETURN NULL; END IF;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
    SELECT max(week_start) INTO week FROM app.weekly_cycles WHERE tenant_id=a.tenant_id AND site_id=p_site;
    IF week IS NULL THEN RETURN NULL; END IF;
    RETURN control.read_change_measurement_report(p_hash,p_site,p_generation,week);
END $$;
REVOKE ALL ON FUNCTION control.read_latest_weekly_skill_report(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_latest_weekly_skill_report(bytea,uuid,text) TO signal_api;

-- Legacy histories retain their existing notification behavior. Version-one
-- cycles queue only through the admitted report skill, after the report closes.
CREATE OR REPLACE FUNCTION control.queue_weekly_email() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.skill_registry_version=0 AND OLD.status='running' AND NEW.status IN ('completed','stopped','failed') THEN
        PERFORM control.queue_email_event(NEW.tenant_id,NEW.site_id,NEW.id,'weekly_report',
            control.weekly_report_projection(NEW.tenant_id,NEW.site_id,NEW.week_start));
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION control.weekly_skill_queue_report(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i record; c record; cfg record; p record; refs jsonb:='[]'; identifier uuid; projection jsonb; used integer; recipients integer;
BEGIN
    PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['report_delivery']);
    SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
    SELECT * INTO c FROM app.weekly_cycles WHERE tenant_id=i.tenant_id AND site_id=p_site AND id=i.cycle_id;
    SELECT * INTO cfg FROM control.email_smtp_configuration;
    IF c.status='running' OR cfg.host IS NULL THEN RETURN jsonb_build_object('state','unavailable','reason','SMTP_OR_REPORT_UNAVAILABLE'); END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id=i.tenant_id AND id=p_site FOR UPDATE;
    SELECT count(*) INTO used FROM app.email_send_receipts WHERE tenant_id=i.tenant_id AND site_id=p_site AND phase='dispatch'
        AND created_at>=date_trunc('day',transaction_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
    SELECT used+count(*) INTO used FROM app.email_outbox WHERE tenant_id=i.tenant_id AND site_id=p_site AND state IN ('queued','retry','dispatching');
    SELECT count(*) INTO recipients FROM control.weekly_skill_email_recipients(i.tenant_id,p_site,p_generation) v
        WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(i.plan) u WHERE u->>'preference_id'=v.id::text);
    IF recipients=0 THEN RETURN jsonb_build_object('state','unavailable','reason','NO_VERIFIED_RECIPIENT'); END IF;
    IF used+recipients>cfg.daily_cap THEN RETURN jsonb_build_object('state','unavailable','reason','EMAIL_CAP_REACHED'); END IF;
    FOR p IN SELECT * FROM control.weekly_skill_email_recipients(i.tenant_id,p_site,p_generation) v
        WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(i.plan) u WHERE u->>'preference_id'=v.id::text) ORDER BY v.id LOOP
        identifier:=gen_random_uuid();
        refs:=refs||jsonb_build_array('email_outbox:'||identifier::text);
    END LOOP;
    PERFORM control.record_weekly_skill(i.tenant_id,p_site,i.cycle_id,'report_delivery','completed','REPORT_QUEUED',refs);
    projection:=control.weekly_report_projection(i.tenant_id,p_site,c.week_start);
    IF octet_length(projection::text)>131072 THEN RAISE EXCEPTION 'bounded_projection_rejected'; END IF;
    recipients:=0;
    FOR p IN SELECT * FROM control.weekly_skill_email_recipients(i.tenant_id,p_site,p_generation) v
        WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(i.plan) u WHERE u->>'preference_id'=v.id::text) ORDER BY v.id LOOP
        identifier:=substr(refs->>recipients,14)::uuid; recipients:=recipients+1;
        INSERT INTO app.email_outbox(tenant_id,site_id,id,membership_id,preference_id,event_id,category,projection,configuration_sha256)
            VALUES(i.tenant_id,p_site,identifier,p.membership_id,p.id,i.cycle_id,'weekly_report',projection,cfg.configuration_sha256);
    END LOOP;
    RETURN jsonb_build_object('state','completed','evidence_refs',refs);
END $$;
REVOKE ALL ON FUNCTION control.weekly_skill_queue_report(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_skill_queue_report(bytea,text,uuid) TO signal_workflow;

CREATE FUNCTION control.weekly_skill_candidate_inbox(p_hash bytea,p_site uuid,p_generation text)
RETURNS TABLE(revision_id uuid,canonical_manifest jsonb,review_status text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c record;
BEGIN
    PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['strategy_rebuild']);
    SELECT * INTO c FROM control.weekly_skill_context(p_hash,p_generation,p_site);
    RETURN QUERY SELECT r.id,convert_from(r.canonical_manifest,'UTF8')::jsonb,'pending'::text
        FROM app.candidate_recipe_revisions r WHERE r.tenant_id=c.tenant_id AND r.site_id=p_site
        AND NOT EXISTS(SELECT 1 FROM app.candidate_recipe_review_decisions d WHERE d.tenant_id=r.tenant_id AND d.site_id=r.site_id AND d.candidate_revision_id=r.id)
        AND NOT EXISTS(SELECT 1 FROM app.candidate_recipe_revisions n WHERE n.tenant_id=r.tenant_id AND n.site_id=r.site_id AND n.audit_report_id=r.audit_report_id
            AND n.finding_id=r.finding_id AND (n.sealed_at,n.id)>(r.sealed_at,r.id))
        AND EXISTS(SELECT 1 FROM app.github_pr_extensions e JOIN app.github_read_bindings b
            ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id AND b.status='active'
            WHERE e.tenant_id=r.tenant_id AND e.site_id=r.site_id AND e.id=r.extension_id
                AND e.status='observed' AND e.base_sha=r.base_sha)
        ORDER BY r.sealed_at DESC,r.id DESC LIMIT 50;
END $$;
REVOKE ALL ON FUNCTION control.weekly_skill_candidate_inbox(bytea,uuid,text) FROM PUBLIC;

-- Import recording and reauthorization retain the original evidence checks.
-- Only the handle-bound site's admitted binding can reach them.
DO $$ DECLARE f record; definition text; args text; guard text;
BEGIN
    FOR f IN SELECT * FROM (VALUES
        ('current_gsc_binding','import_gsc',false),('current_bing_binding','import_bing',false),
        ('record_gsc_import_generation','import_gsc',true),('mark_gsc_reauth_required','import_gsc',true),
        ('record_bing_import_generation','import_bing',true),('mark_bing_reauth_required','import_bing',true)
    ) ports(name,stage,has_binding) LOOP
        SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid)
            INTO STRICT definition,args FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='control' AND p.proname=f.name;
        definition:=replace(definition,'FUNCTION control.'||f.name||'(', 'FUNCTION control.weekly_skill_'||f.name||'(p_hash bytea, p_generation text, ');
        guard:=format('PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site_id,ARRAY[%%L]); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.weekly_skill_context(p_hash,p_generation,p_site_id)) THEN RAISE EXCEPTION ''skill_tenant_denied'' USING ERRCODE=''42501''; END IF;',f.stage);
        IF f.has_binding THEN guard:=guard||'PERFORM control.assert_weekly_skill_resource(p_hash,p_generation,p_site_id,''binding_id'',p_binding_id);'; END IF;
        IF NOT f.has_binding THEN
            definition:=replace(definition,'AND event.event_kind = ''bound''',
                'AND EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_hash AND u->>''binding_id''=event.binding_id::text) AND event.event_kind = ''bound''');
        END IF;
        definition:=regexp_replace(definition,'BEGIN','BEGIN '||guard);
        EXECUTE definition;
        EXECUTE format('REVOKE ALL ON FUNCTION control.weekly_skill_%%I(bytea,text,%%s) FROM PUBLIC',f.name,args);
        EXECUTE format('GRANT EXECUTE ON FUNCTION control.weekly_skill_%%I(bytea,text,%%s) TO signal_workflow',f.name,args);
    END LOOP;
END $$;
