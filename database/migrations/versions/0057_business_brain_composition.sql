-- 0056 is preserved. Bind page provenance to actual 0066 evidence, not fixture metadata.
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='app.business_brain_facts'::regclass AND confrelid='app.evidence_records'::regclass LOOP
  EXECUTE format('ALTER TABLE app.business_brain_facts DROP CONSTRAINT %%I', c.conname);
 END LOOP;
END; $$;
ALTER TABLE app.business_brain_facts ADD FOREIGN KEY (tenant_id,site_id,page_evidence_id) REFERENCES app.crawl_page_records(tenant_id,site_id,id);
ALTER TABLE app.business_brain_facts DROP CONSTRAINT business_brain_facts_category_check;
ALTER TABLE app.business_brain_facts ADD CONSTRAINT business_brain_facts_category_check CHECK (category IN ('product','pricing','audience','positioning','proof_point','competitor','claim','legal','medical','financial','product_claim'));
ALTER TABLE app.business_brain_facts DROP CONSTRAINT business_brain_facts_check2;
ALTER TABLE app.business_brain_facts ADD CHECK (NOT sensitive OR category IN ('product','pricing','claim','legal','medical','financial','product_claim'));

CREATE TABLE app.business_brain_extractions (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 source_kind text NOT NULL CHECK(source_kind IN ('page_evidence','brand_document')),
 source_id uuid NOT NULL, range_start integer NOT NULL, range_end integer NOT NULL,
 extraction_version text NOT NULL CHECK(extraction_version='business-brain-v1'),
 input_sha256 bytea NOT NULL CHECK(octet_length(input_sha256)=32),
 decision_id uuid NOT NULL, model_operation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),
 UNIQUE(tenant_id,site_id,source_kind,source_id,range_start,range_end,extraction_version),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 CHECK((source_kind='page_evidence' AND range_start=0 AND range_end=0) OR
       (source_kind='brand_document' AND range_start>=0 AND range_end>range_start AND range_end-range_start<=24000))
);
CREATE TABLE app.business_brain_extraction_results (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, extraction_id uuid NOT NULL,
 state text NOT NULL CHECK(state IN ('completed','failed')),
 page_type text CHECK(page_type IN ('product','pricing','blog','docs','legal','other')),
 provider_response_id text, model_reported text, output jsonb,
 output_sha256 bytea, usage jsonb, reason text,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,extraction_id),
 FOREIGN KEY(tenant_id,site_id,extraction_id) REFERENCES app.business_brain_extractions(tenant_id,site_id,id),
 CHECK((state='completed' AND page_type IS NOT NULL AND provider_response_id IS NOT NULL AND model_reported IS NOT NULL AND output IS NOT NULL AND usage IS NOT NULL AND octet_length(output_sha256)=32 AND reason IS NULL)
 OR (state='failed' AND reason IS NOT NULL AND output IS NULL))
);
ALTER TABLE app.business_brain_facts ADD COLUMN extraction_id uuid;
ALTER TABLE app.business_brain_facts ADD COLUMN decision_id uuid;
ALTER TABLE app.business_brain_facts ADD FOREIGN KEY(tenant_id,site_id,extraction_id) REFERENCES app.business_brain_extractions(tenant_id,site_id,id);
ALTER TABLE app.business_brain_facts ADD FOREIGN KEY(tenant_id,site_id,decision_id) REFERENCES app.decision_records(tenant_id,site_id,id);
CREATE TRIGGER business_brain_extractions_immutable BEFORE UPDATE OR DELETE ON app.business_brain_extractions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER business_brain_extraction_results_immutable BEFORE UPDATE OR DELETE ON app.business_brain_extraction_results FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.business_brain_extractions ENABLE ROW LEVEL SECURITY; ALTER TABLE app.business_brain_extractions FORCE ROW LEVEL SECURITY;
ALTER TABLE app.business_brain_extraction_results ENABLE ROW LEVEL SECURITY; ALTER TABLE app.business_brain_extraction_results FORCE ROW LEVEL SECURITY;
CREATE POLICY brain_extractions_scope ON app.business_brain_extractions USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE POLICY brain_extraction_results_scope ON app.business_brain_extraction_results USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
REVOKE ALL ON app.business_brain_extractions,app.business_brain_extraction_results FROM PUBLIC,signal_identity,signal_bootstrap,signal_api,signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.business_brain_owner(p_session_hash bytea,p_generation text,p_site_id uuid)
RETURNS TABLE(tenant_id uuid,user_id uuid,membership_id uuid) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
 IF v.outcome='authorized' AND v.role_key='owner' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(v.tenant_id::text||p_site_id::text,73));
  RETURN QUERY SELECT v.tenant_id,v.user_id,m.id FROM app.memberships m WHERE m.tenant_id=v.tenant_id AND m.user_id=v.user_id;
 END IF;
END; $$;

CREATE OR REPLACE FUNCTION control.business_brain_propose(p_session_hash bytea,p_generation text,p_site_id uuid,p_fact_id uuid,p_category text,p_statement text,p_source_kind text,p_source_id uuid,p_range jsonb,p_sensitive boolean)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id);
 IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 -- Only owner statements enter this port. Extracted facts use the atomic receipt port below.
 IF p_source_kind IS DISTINCT FROM 'owner_statement' OR p_source_id IS NOT NULL OR p_range IS NOT NULL OR p_category IS NULL OR p_category NOT IN ('product','pricing','audience','positioning','proof_point','competitor','claim','legal','medical','financial','product_claim') OR p_statement IS NULL OR length(btrim(p_statement)) NOT BETWEEN 1 AND 4000 OR p_statement<>btrim(p_statement) THEN RETURN 'invalid'; END IF;
 INSERT INTO app.business_brain_facts(tenant_id,site_id,id,category,statement,initial_status,source_kind,owner_membership_id,sensitive,created_by_user_id)
 VALUES(v.tenant_id,p_site_id,p_fact_id,p_category,p_statement,'proposed','owner_statement',v.membership_id,p_category IN ('product','pricing','claim','legal','medical','financial','product_claim'),v.user_id);
 INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'proposed',p_fact_id,v.user_id,transaction_timestamp()); RETURN 'proposed';
END; $$;

CREATE OR REPLACE FUNCTION control.business_brain_correct(p_session_hash bytea,p_generation text,p_site_id uuid,p_fact_id uuid,p_replacement_id uuid,p_statement text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; f app.business_brain_facts%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 SELECT * INTO f FROM app.business_brain_facts WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_fact_id;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM app.business_brain_facts WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND supersedes_id=p_fact_id) OR EXISTS(SELECT 1 FROM app.business_brain_fact_events WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND fact_id=p_fact_id AND event_type='removed') THEN RETURN 'unavailable'; END IF;
 IF p_replacement_id IS NULL OR p_statement IS NULL OR length(btrim(p_statement)) NOT BETWEEN 1 AND 4000 OR p_statement<>btrim(p_statement) THEN RETURN 'invalid'; END IF;
 INSERT INTO app.business_brain_facts(tenant_id,site_id,id,category,statement,initial_status,source_kind,owner_membership_id,sensitive,supersedes_id,created_by_user_id)
 VALUES(v.tenant_id,p_site_id,p_replacement_id,f.category,p_statement,'approved','owner_statement',v.membership_id,f.sensitive,p_fact_id,v.user_id);
 INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'corrected',p_replacement_id,v.user_id,transaction_timestamp()); RETURN 'corrected';
END; $$;

CREATE FUNCTION control.business_brain_page_source(p_session_hash bytea,p_generation text,p_site_id uuid,p_page_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; result jsonb; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 SELECT jsonb_build_object('tenant_id',p.tenant_id,'url',o.final_url,'title',coalesce(p.title,''),'artifact',to_jsonb(a),'body_sha256',encode(p.body_sha256,'hex')) INTO result
 FROM app.crawl_page_records p JOIN app.fetch_observations o ON o.tenant_id=p.tenant_id AND o.site_id=p.site_id AND o.id=p.observation_id
 JOIN app.artifacts a ON a.tenant_id=o.tenant_id AND a.site_id=o.site_id AND a.id=o.raw_artifact_id
 JOIN app.crawl_frontier_settlements s ON s.tenant_id=p.tenant_id AND s.site_id=p.site_id AND s.page_record_id=p.id AND s.terminal_state='fetched'
 JOIN app.crawl_manifests m ON m.tenant_id=p.tenant_id AND m.site_id=p.site_id AND m.crawl_run_id=p.crawl_run_id
 WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site_id AND p.id=p_page_id AND p.http_status BETWEEN 200 AND 299;
 RETURN result;
END; $$;

CREATE FUNCTION control.business_brain_begin_extraction(p_session_hash bytea,p_generation text,p_site_id uuid,p_id uuid,p_source_kind text,p_source_id uuid,p_start integer,p_end integer,p_version text,p_input_hash bytea,p_decision_id uuid,p_model_id uuid)
RETURNS TABLE(extraction_id uuid,decision_id uuid,model_operation_id uuid,state text,tenant_id uuid) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; e app.business_brain_extractions%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id);
 IF v.tenant_id IS NULL THEN RAISE EXCEPTION 'owner_access_denied' USING ERRCODE='42501'; END IF;
 IF p_source_kind='page_evidence' AND control.business_brain_page_source(p_session_hash,p_generation,p_site_id,p_source_id) IS NULL THEN RAISE EXCEPTION 'source_unavailable' USING ERRCODE='22023'; END IF;
 IF p_source_kind='brand_document' AND NOT EXISTS(SELECT 1 FROM app.brand_documents d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site_id AND d.id=p_source_id AND NOT d.secret_signal AND NOT EXISTS(SELECT 1 FROM app.brand_document_events b WHERE b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.document_id=d.id AND b.event_type='deleted') AND NOT EXISTS(SELECT 1 FROM app.brand_documents n WHERE n.tenant_id=d.tenant_id AND n.site_id=d.site_id AND n.supersedes_id=d.id)) THEN RAISE EXCEPTION 'source_unavailable' USING ERRCODE='22023'; END IF;
 SELECT * INTO e FROM app.business_brain_extractions x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site_id AND x.source_kind=p_source_kind AND x.source_id=p_source_id AND x.range_start=p_start AND x.range_end=p_end AND x.extraction_version=p_version;
 IF FOUND THEN
  IF e.input_sha256<>p_input_hash THEN RAISE EXCEPTION 'input_conflict' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT e.id,e.decision_id,e.model_operation_id,coalesce((SELECT r.state FROM app.business_brain_extraction_results r WHERE r.tenant_id=e.tenant_id AND r.site_id=e.site_id AND r.extraction_id=e.id),'outcome_unknown'),v.tenant_id; RETURN;
 END IF;
 INSERT INTO app.business_brain_extractions(tenant_id,site_id,id,source_kind,source_id,range_start,range_end,extraction_version,input_sha256,decision_id,model_operation_id)
 VALUES(v.tenant_id,p_site_id,p_id,p_source_kind,p_source_id,p_start,p_end,p_version,p_input_hash,p_decision_id,p_model_id);
 RETURN QUERY SELECT p_id,p_decision_id,p_model_id,'started'::text,v.tenant_id;
END; $$;

CREATE FUNCTION control.business_brain_finish_extraction(p_session_hash bytea,p_generation text,p_site_id uuid,p_id uuid,p_page_type text,p_canonical_output bytea,p_output_hash bytea,p_response_id text,p_model text,p_usage jsonb,p_reason text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; e app.business_brain_extractions%%ROWTYPE; item jsonb; f_id uuid; p_output jsonb; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 SELECT * INTO e FROM app.business_brain_extractions WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_id;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 IF EXISTS(SELECT 1 FROM app.business_brain_extraction_results WHERE tenant_id=e.tenant_id AND site_id=e.site_id AND extraction_id=e.id) THEN RETURN 'replayed'; END IF;
 IF p_reason IS NOT NULL THEN
  INSERT INTO app.business_brain_extraction_results(tenant_id,site_id,extraction_id,state,reason) VALUES(e.tenant_id,e.site_id,e.id,'failed',p_reason); RETURN 'failed';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM app.decision_records d WHERE d.tenant_id=e.tenant_id AND d.site_id=e.site_id AND d.id=e.decision_id AND d.purpose='business_brain.page_type' AND d.answer->'page_type'->>'choice'=p_page_type AND d.outcome<>'ship') THEN RETURN 'decision_unavailable'; END IF;
 IF p_canonical_output IS NULL OR octet_length(p_canonical_output)>256000 OR p_output_hash IS DISTINCT FROM sha256(p_canonical_output) THEN RETURN 'invalid'; END IF;
 p_output:=convert_from(p_canonical_output,'UTF8')::jsonb;
 IF jsonb_typeof(p_output)<>'array' OR jsonb_array_length(p_output)>40 THEN RETURN 'invalid'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_output) LOOP
  IF jsonb_typeof(item)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(item))<>2 OR item->>'category' IS NULL OR item->>'category' NOT IN ('product','pricing','audience','positioning','proof_point','competitor','claim','legal','medical','financial','product_claim') OR jsonb_typeof(item->'statement') IS DISTINCT FROM 'string' OR length(btrim(item->>'statement')) NOT BETWEEN 1 AND 4000 THEN RETURN 'invalid'; END IF;
 END LOOP;
 INSERT INTO app.business_brain_extraction_results(tenant_id,site_id,extraction_id,state,page_type,provider_response_id,model_reported,output,output_sha256,usage)
 VALUES(e.tenant_id,e.site_id,e.id,'completed',p_page_type,p_response_id,p_model,p_output,p_output_hash,p_usage);
 FOR item IN SELECT value FROM jsonb_array_elements(p_output) LOOP
  f_id:=gen_random_uuid();
  INSERT INTO app.business_brain_facts(tenant_id,site_id,id,category,statement,initial_status,source_kind,page_evidence_id,document_id,extracted_range,sensitive,created_by_user_id,extraction_id,decision_id)
  VALUES(e.tenant_id,e.site_id,f_id,item->>'category',item->>'statement','proposed',e.source_kind,CASE WHEN e.source_kind='page_evidence' THEN e.source_id END,CASE WHEN e.source_kind='brand_document' THEN e.source_id END,CASE WHEN e.source_kind='brand_document' THEN jsonb_build_object('start',e.range_start,'end',e.range_end) END,(item->>'category') IN ('product','pricing','claim','legal','medical','financial','product_claim'),v.user_id,e.id,e.decision_id);
  INSERT INTO app.business_brain_audit_records VALUES(e.tenant_id,e.site_id,gen_random_uuid(),'proposed',f_id,v.user_id,transaction_timestamp());
 END LOOP;
 RETURN 'completed';
END; $$;

CREATE FUNCTION control.business_brain_read(p_session_hash bytea,p_generation text,p_site_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object(
 'facts',coalesce((SELECT jsonb_agg(to_jsonb(f)||jsonb_build_object('decision_id',b.decision_id,'extraction_id',b.extraction_id)) FROM control.list_business_brain_facts(p_session_hash,p_generation,p_site_id,NULL) f JOIN app.business_brain_facts b ON b.tenant_id=v.tenant_id AND b.site_id=p_site_id AND b.id=f.fact_id),'[]'::jsonb),
 'voice',(SELECT to_jsonb(x) FROM (SELECT id AS profile_id,profile,supersedes_id,created_at FROM app.business_brain_voice_profiles p WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site_id AND NOT EXISTS(SELECT 1 FROM app.business_brain_voice_profiles n WHERE n.tenant_id=p.tenant_id AND n.site_id=p.site_id AND n.supersedes_id=p.id) ORDER BY created_at DESC,id DESC LIMIT 1) x),
 'extractions',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT e.id AS extraction_id,e.source_kind,e.source_id,e.decision_id,coalesce(r.state,'outcome_unknown') AS state,r.page_type,d.fallback,d.provider,r.reason FROM app.business_brain_extractions e LEFT JOIN app.business_brain_extraction_results r ON r.tenant_id=e.tenant_id AND r.site_id=e.site_id AND r.extraction_id=e.id LEFT JOIN app.decision_records d ON d.tenant_id=e.tenant_id AND d.site_id=e.site_id AND d.id=e.decision_id WHERE e.tenant_id=v.tenant_id AND e.site_id=p_site_id ORDER BY e.created_at DESC LIMIT 100) x),'[]'::jsonb));
END; $$;

CREATE OR REPLACE FUNCTION control.list_business_brain_facts(p_session_hash bytea,p_generation text,p_site_id uuid,p_status text DEFAULT NULL)
RETURNS TABLE(fact_id uuid,category text,statement text,status text,source_kind text,page_evidence_id uuid,document_id uuid,extracted_range jsonb,owner_membership_id uuid,sensitive boolean,supersedes_id uuid,created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id);
 IF v.tenant_id IS NULL THEN RETURN; END IF;
 RETURN QUERY
 WITH current AS (
 SELECT f.*,CASE
 WHEN EXISTS(SELECT 1 FROM app.business_brain_facts n WHERE n.tenant_id=f.tenant_id AND n.site_id=f.site_id AND n.supersedes_id=f.id) THEN 'superseded'
 WHEN EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='removed') THEN 'removed'
 WHEN f.initial_status='approved' OR EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='approved') THEN 'approved' ELSE 'proposed' END AS view_status
 FROM app.business_brain_facts f WHERE f.tenant_id=v.tenant_id AND f.site_id=p_site_id)
 SELECT c.id,c.category,c.statement,c.view_status,c.source_kind,c.page_evidence_id,c.document_id,c.extracted_range,c.owner_membership_id,c.sensitive,c.supersedes_id,c.created_at FROM current c WHERE p_status IS NULL OR c.view_status=p_status ORDER BY c.created_at DESC,c.id DESC;
END;
$$;
CREATE OR REPLACE FUNCTION control.approved_business_brain_facts(p_session_hash bytea,p_generation text,p_site_id uuid)
RETURNS TABLE(fact_id uuid,category text,statement text,source_kind text,page_evidence_id uuid,document_id uuid,extracted_range jsonb,owner_membership_id uuid,created_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT f.fact_id,f.category,f.statement,f.source_kind,f.page_evidence_id,f.document_id,f.extracted_range,f.owner_membership_id,f.created_at FROM control.list_business_brain_facts(p_session_hash,p_generation,p_site_id,'approved') f;
$$;

CREATE OR REPLACE FUNCTION control.business_brain_set_voice(p_session_hash bytea,p_generation text,p_site_id uuid,p_profile_id uuid,p_profile jsonb,p_supersedes_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; latest uuid; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_profile_id IS NULL OR p_profile IS NULL OR jsonb_typeof(p_profile)<>'object' OR octet_length(convert_to(p_profile::text,'UTF8')) NOT BETWEEN 2 AND 16384 THEN RETURN 'invalid'; END IF;
 SELECT p.id INTO latest FROM app.business_brain_voice_profiles p WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site_id AND NOT EXISTS(SELECT 1 FROM app.business_brain_voice_profiles n WHERE n.tenant_id=p.tenant_id AND n.site_id=p.site_id AND n.supersedes_id=p.id) ORDER BY created_at DESC,id DESC LIMIT 1;
 IF p_supersedes_id IS DISTINCT FROM latest THEN RETURN 'conflict'; END IF;
 INSERT INTO app.business_brain_voice_profiles VALUES(v.tenant_id,p_site_id,p_profile_id,p_profile,p_supersedes_id,v.user_id,transaction_timestamp()); INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'voice_edited',p_profile_id,v.user_id,transaction_timestamp()); RETURN 'recorded';
END; $$;

REVOKE ALL ON FUNCTION control.business_brain_page_source(bytea,text,uuid,uuid),control.business_brain_begin_extraction(bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid),control.business_brain_finish_extraction(bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text),control.business_brain_read(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.business_brain_page_source(bytea,text,uuid,uuid),control.business_brain_begin_extraction(bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid),control.business_brain_finish_extraction(bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text),control.business_brain_read(bytea,text,uuid) TO signal_api;
