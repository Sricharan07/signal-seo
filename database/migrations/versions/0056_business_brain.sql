CREATE TABLE app.business_brain_facts (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    category text NOT NULL CHECK (category IN ('product','pricing','audience','positioning','proof_point','competitor','claim')),
    statement text NOT NULL CHECK (length(statement) BETWEEN 1 AND 4000),
    initial_status text NOT NULL CHECK (initial_status IN ('proposed','approved')),
    source_kind text NOT NULL CHECK (source_kind IN ('page_evidence','brand_document','owner_statement')),
    page_evidence_id uuid, document_id uuid, extracted_range jsonb,
    owner_membership_id uuid, sensitive boolean NOT NULL DEFAULT false,
    supersedes_id uuid, created_by_user_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, supersedes_id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, page_evidence_id) REFERENCES app.evidence_records (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, document_id) REFERENCES app.brand_documents (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, supersedes_id) REFERENCES app.business_brain_facts (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, owner_membership_id) REFERENCES app.memberships (tenant_id, id),
    FOREIGN KEY (tenant_id, created_by_user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK ((source_kind = 'page_evidence' AND page_evidence_id IS NOT NULL AND document_id IS NULL AND owner_membership_id IS NULL AND extracted_range IS NULL)
        OR (source_kind = 'brand_document' AND page_evidence_id IS NULL AND document_id IS NOT NULL AND owner_membership_id IS NULL AND extracted_range IS NOT NULL AND jsonb_typeof(extracted_range) = 'object')
        OR (source_kind = 'owner_statement' AND page_evidence_id IS NULL AND document_id IS NULL AND owner_membership_id IS NOT NULL AND extracted_range IS NULL)),
    CHECK (NOT sensitive OR category IN ('pricing','claim'))
);
CREATE TABLE app.business_brain_fact_events (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, fact_id uuid NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('approved','removed')),
    actor_user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id), UNIQUE (tenant_id, site_id, fact_id, event_type),
    FOREIGN KEY (tenant_id, site_id, fact_id) REFERENCES app.business_brain_facts (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE TABLE app.business_brain_voice_profiles (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, profile jsonb NOT NULL,
    supersedes_id uuid, actor_user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id), UNIQUE (tenant_id, site_id, supersedes_id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, supersedes_id) REFERENCES app.business_brain_voice_profiles (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK (jsonb_typeof(profile) = 'object' AND octet_length(convert_to(profile::text, 'UTF8')) BETWEEN 2 AND 16384)
);
CREATE TABLE app.business_brain_audit_records (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, action text NOT NULL CHECK (action IN ('proposed','approved','corrected','removed','voice_edited')),
    subject_id uuid NOT NULL, actor_user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id), FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX business_brain_facts_current ON app.business_brain_facts (tenant_id, site_id, created_at DESC, id);
CREATE TRIGGER business_brain_facts_immutable BEFORE UPDATE OR DELETE ON app.business_brain_facts FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER business_brain_fact_events_immutable BEFORE UPDATE OR DELETE ON app.business_brain_fact_events FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER business_brain_voice_profiles_immutable BEFORE UPDATE OR DELETE ON app.business_brain_voice_profiles FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER business_brain_audit_records_immutable BEFORE UPDATE OR DELETE ON app.business_brain_audit_records FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.business_brain_facts ENABLE ROW LEVEL SECURITY; ALTER TABLE app.business_brain_facts FORCE ROW LEVEL SECURITY;
ALTER TABLE app.business_brain_fact_events ENABLE ROW LEVEL SECURITY; ALTER TABLE app.business_brain_fact_events FORCE ROW LEVEL SECURITY;
ALTER TABLE app.business_brain_voice_profiles ENABLE ROW LEVEL SECURITY; ALTER TABLE app.business_brain_voice_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE app.business_brain_audit_records ENABLE ROW LEVEL SECURITY; ALTER TABLE app.business_brain_audit_records FORCE ROW LEVEL SECURITY;
CREATE POLICY business_brain_facts_scope ON app.business_brain_facts USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id()) WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY business_brain_fact_events_scope ON app.business_brain_fact_events USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id()) WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY business_brain_voice_scope ON app.business_brain_voice_profiles USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id()) WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY business_brain_audit_scope ON app.business_brain_audit_records USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id()) WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.business_brain_owner(p_session_hash bytea, p_generation text, p_site_id uuid)
RETURNS TABLE (tenant_id uuid, user_id uuid, membership_id uuid) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
 IF v.outcome = 'authorized' AND v.role_key = 'owner' THEN
  RETURN QUERY SELECT v.tenant_id,v.user_id,m.id FROM app.memberships m WHERE m.tenant_id=v.tenant_id AND m.user_id=v.user_id;
 END IF;
END; $$;

CREATE FUNCTION control.business_brain_propose(p_session_hash bytea,p_generation text,p_site_id uuid,p_fact_id uuid,p_category text,p_statement text,p_source_kind text,p_source_id uuid,p_range jsonb,p_sensitive boolean)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v record; BEGIN SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id);
 IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_fact_id IS NULL OR p_category NOT IN ('product','pricing','audience','positioning','proof_point','competitor','claim') OR p_statement IS NULL OR length(p_statement) NOT BETWEEN 1 AND 4000 OR p_source_kind NOT IN ('page_evidence','brand_document','owner_statement') THEN RETURN 'invalid'; END IF;
 IF p_source_kind='page_evidence' AND NOT EXISTS (SELECT 1 FROM app.evidence_records WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_source_id) THEN RETURN 'provenance_unavailable'; END IF;
 IF p_source_kind='brand_document' AND (p_range IS NULL OR jsonb_typeof(p_range)<>'object' OR NOT EXISTS (SELECT 1 FROM app.brand_documents WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_source_id)) THEN RETURN 'provenance_unavailable'; END IF;
 INSERT INTO app.business_brain_facts (tenant_id,site_id,id,category,statement,initial_status,source_kind,page_evidence_id,document_id,extracted_range,owner_membership_id,sensitive,created_by_user_id)
 VALUES (v.tenant_id,p_site_id,p_fact_id,p_category,p_statement,'proposed',p_source_kind,CASE WHEN p_source_kind='page_evidence' THEN p_source_id END,CASE WHEN p_source_kind='brand_document' THEN p_source_id END,CASE WHEN p_source_kind='brand_document' THEN p_range END,CASE WHEN p_source_kind='owner_statement' THEN v.membership_id END,coalesce(p_sensitive,false),v.user_id);
 INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'proposed',p_fact_id,v.user_id,transaction_timestamp()); RETURN 'proposed';
END; $$;

CREATE FUNCTION control.business_brain_approve(p_session_hash bytea,p_generation text,p_site_id uuid,p_fact_id uuid,p_event_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v record; BEGIN SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF NOT EXISTS (SELECT 1 FROM app.business_brain_facts f WHERE f.tenant_id=v.tenant_id AND f.site_id=p_site_id AND f.id=p_fact_id AND f.initial_status='proposed' AND NOT EXISTS(SELECT 1 FROM app.business_brain_facts n WHERE n.tenant_id=f.tenant_id AND n.site_id=f.site_id AND n.supersedes_id=f.id) AND NOT EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='removed')) THEN RETURN 'unavailable'; END IF;
 INSERT INTO app.business_brain_fact_events VALUES(v.tenant_id,p_site_id,p_event_id,p_fact_id,'approved',v.user_id,transaction_timestamp()) ON CONFLICT (tenant_id,site_id,fact_id,event_type) DO NOTHING;
 INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'approved',p_fact_id,v.user_id,transaction_timestamp()); RETURN 'approved'; END; $$;

CREATE FUNCTION control.business_brain_remove(p_session_hash bytea,p_generation text,p_site_id uuid,p_fact_id uuid,p_event_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v record; BEGIN SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF NOT EXISTS (SELECT 1 FROM app.business_brain_facts WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_fact_id) THEN RETURN 'unavailable'; END IF;
 INSERT INTO app.business_brain_fact_events VALUES(v.tenant_id,p_site_id,p_event_id,p_fact_id,'removed',v.user_id,transaction_timestamp()) ON CONFLICT (tenant_id,site_id,fact_id,event_type) DO NOTHING;
 INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'removed',p_fact_id,v.user_id,transaction_timestamp()); RETURN 'removed'; END; $$;

CREATE FUNCTION control.business_brain_correct(p_session_hash bytea,p_generation text,p_site_id uuid,p_fact_id uuid,p_replacement_id uuid,p_statement text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v record; f app.business_brain_facts%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 SELECT * INTO f FROM app.business_brain_facts WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_fact_id;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM app.business_brain_facts WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND supersedes_id=p_fact_id) OR EXISTS(SELECT 1 FROM app.business_brain_fact_events WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND fact_id=p_fact_id AND event_type='removed') THEN RETURN 'unavailable'; END IF;
 IF p_replacement_id IS NULL OR p_statement IS NULL OR length(p_statement) NOT BETWEEN 1 AND 4000 THEN RETURN 'invalid'; END IF;
 INSERT INTO app.business_brain_facts (tenant_id,site_id,id,category,statement,initial_status,source_kind,page_evidence_id,document_id,extracted_range,owner_membership_id,sensitive,supersedes_id,created_by_user_id)
 VALUES(v.tenant_id,p_site_id,p_replacement_id,f.category,p_statement,'approved',f.source_kind,f.page_evidence_id,f.document_id,f.extracted_range,CASE WHEN f.source_kind='owner_statement' THEN v.membership_id END,f.sensitive,p_fact_id,v.user_id);
 INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'corrected',p_replacement_id,v.user_id,transaction_timestamp()); RETURN 'corrected'; END; $$;

CREATE FUNCTION control.list_business_brain_facts(p_session_hash bytea,p_generation text,p_site_id uuid,p_status text DEFAULT NULL)
RETURNS TABLE (fact_id uuid,category text,statement text,status text,source_kind text,page_evidence_id uuid,document_id uuid,extracted_range jsonb,owner_membership_id uuid,sensitive boolean,supersedes_id uuid,created_at timestamptz) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v record; BEGIN SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN; END IF;
 RETURN QUERY WITH current AS (SELECT f.*, CASE WHEN EXISTS(SELECT 1 FROM app.business_brain_facts n WHERE n.tenant_id=f.tenant_id AND n.site_id=f.site_id AND n.supersedes_id=f.id) THEN 'superseded' WHEN EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='removed') THEN 'removed' WHEN f.initial_status='approved' OR EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='approved') THEN 'approved' ELSE 'proposed' END AS view_status FROM app.business_brain_facts f WHERE f.tenant_id=v.tenant_id AND f.site_id=p_site_id)
 SELECT id,category,statement,view_status,source_kind,page_evidence_id,document_id,extracted_range,owner_membership_id,sensitive,supersedes_id,created_at FROM current WHERE p_status IS NULL OR view_status=p_status ORDER BY created_at DESC,id DESC LIMIT 500; END; $$;

CREATE FUNCTION control.approved_business_brain_facts(p_session_hash bytea,p_generation text,p_site_id uuid)
RETURNS TABLE (fact_id uuid,category text,statement text,source_kind text,page_evidence_id uuid,document_id uuid,extracted_range jsonb,owner_membership_id uuid,created_at timestamptz) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN RETURN QUERY SELECT fact_id,category,statement,source_kind,page_evidence_id,document_id,extracted_range,owner_membership_id,created_at FROM control.list_business_brain_facts(p_session_hash,p_generation,p_site_id,'approved'); END; $$;

CREATE FUNCTION control.business_brain_set_voice(p_session_hash bytea,p_generation text,p_site_id uuid,p_profile_id uuid,p_profile jsonb,p_supersedes_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v record; BEGIN SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_profile_id IS NULL OR p_profile IS NULL OR jsonb_typeof(p_profile)<>'object' OR octet_length(convert_to(p_profile::text,'UTF8')) NOT BETWEEN 2 AND 16384 THEN RETURN 'invalid'; END IF;
 IF p_supersedes_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM app.business_brain_voice_profiles WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_supersedes_id AND NOT EXISTS(SELECT 1 FROM app.business_brain_voice_profiles n WHERE n.tenant_id=v.tenant_id AND n.site_id=p_site_id AND n.supersedes_id=p_supersedes_id)) THEN RETURN 'unavailable'; END IF;
 INSERT INTO app.business_brain_voice_profiles VALUES(v.tenant_id,p_site_id,p_profile_id,p_profile,p_supersedes_id,v.user_id,transaction_timestamp()); INSERT INTO app.business_brain_audit_records VALUES(v.tenant_id,p_site_id,gen_random_uuid(),'voice_edited',p_profile_id,v.user_id,transaction_timestamp()); RETURN 'recorded'; END; $$;
REVOKE ALL ON app.business_brain_facts,app.business_brain_fact_events,app.business_brain_voice_profiles,app.business_brain_audit_records FROM PUBLIC,signal_identity,signal_bootstrap,signal_api,signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest;
REVOKE ALL ON FUNCTION control.business_brain_owner(bytea,text,uuid),control.business_brain_propose(bytea,text,uuid,uuid,text,text,text,uuid,jsonb,boolean),control.business_brain_approve(bytea,text,uuid,uuid,uuid),control.business_brain_remove(bytea,text,uuid,uuid,uuid),control.business_brain_correct(bytea,text,uuid,uuid,uuid,text),control.list_business_brain_facts(bytea,text,uuid,text),control.approved_business_brain_facts(bytea,text,uuid),control.business_brain_set_voice(bytea,text,uuid,uuid,jsonb,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.business_brain_owner(bytea,text,uuid),control.business_brain_propose(bytea,text,uuid,uuid,text,text,text,uuid,jsonb,boolean),control.business_brain_approve(bytea,text,uuid,uuid,uuid),control.business_brain_remove(bytea,text,uuid,uuid,uuid),control.business_brain_correct(bytea,text,uuid,uuid,uuid,text),control.list_business_brain_facts(bytea,text,uuid,text),control.approved_business_brain_facts(bytea,text,uuid),control.business_brain_set_voice(bytea,text,uuid,uuid,jsonb,uuid) TO signal_api;
