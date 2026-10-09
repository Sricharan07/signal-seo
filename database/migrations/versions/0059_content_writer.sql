CREATE TABLE app.content_briefs (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,payload jsonb NOT NULL,
 origin text NOT NULL CHECK(origin IN ('owner','evidence_proposal')),supersedes_id uuid,
 created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 FOREIGN KEY(tenant_id,site_id,supersedes_id) REFERENCES app.content_briefs(tenant_id,site_id,id),
 UNIQUE(tenant_id,site_id,supersedes_id),CHECK(jsonb_typeof(payload)='object')
);
CREATE TABLE app.content_brief_acceptances (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,brief_id uuid NOT NULL,actor_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,brief_id),
 FOREIGN KEY(tenant_id,site_id,brief_id) REFERENCES app.content_briefs(tenant_id,site_id,id)
);
CREATE TABLE app.content_writer_caps (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,cap integer NOT NULL CHECK(cap BETWEEN 1 AND 5),
 actor_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
CREATE TABLE app.content_draft_intents (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,brief_id uuid NOT NULL,
 writer_version text NOT NULL CHECK(writer_version='content-writer-v1'),input_sha256 bytea NOT NULL CHECK(octet_length(input_sha256)=32),
 fact_snapshot jsonb NOT NULL,voice_snapshot jsonb,created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),UNIQUE(tenant_id,site_id,brief_id,writer_version),
 FOREIGN KEY(tenant_id,site_id,brief_id) REFERENCES app.content_briefs(tenant_id,site_id,id)
);
CREATE TABLE app.content_draft_results (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,draft_id uuid NOT NULL,payload jsonb NOT NULL,
 canonical bytea NOT NULL,sha256 bytea NOT NULL CHECK(octet_length(sha256)=32),decision_id uuid,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,draft_id),
 FOREIGN KEY(tenant_id,site_id,draft_id) REFERENCES app.content_draft_intents(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,decision_id) REFERENCES app.decision_records(tenant_id,site_id,id)
);
CREATE TABLE app.content_candidates (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,draft_id uuid NOT NULL,
 extension_id uuid NOT NULL,build_id uuid NOT NULL,manifest jsonb NOT NULL,canonical bytea NOT NULL,
 revision_sha256 bytea NOT NULL CHECK(octet_length(revision_sha256)=32),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,id),
 UNIQUE(tenant_id,site_id,draft_id),FOREIGN KEY(tenant_id,site_id,draft_id) REFERENCES app.content_draft_results(tenant_id,site_id,draft_id),
 FOREIGN KEY(tenant_id,site_id,extension_id) REFERENCES app.github_pr_extensions(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,build_id) REFERENCES app.candidate_build_intents(tenant_id,site_id,id)
);
CREATE TABLE app.content_candidate_reviews (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,candidate_id uuid NOT NULL,decision text NOT NULL CHECK(decision IN ('approved','rejected','changes_requested')),
 actor_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,candidate_id),
 FOREIGN KEY(tenant_id,site_id,candidate_id) REFERENCES app.content_candidates(tenant_id,site_id,id)
);
CREATE TABLE app.content_writer_audits (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,kind text NOT NULL,subject_id uuid NOT NULL,actor_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['content_briefs','content_brief_acceptances','content_writer_caps','content_draft_intents','content_draft_results','content_candidates','content_candidate_reviews','content_writer_audits'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY writer_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('CREATE TRIGGER writer_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',t);
 END LOOP;
END; $$;

CREATE FUNCTION control.content_writer_inventory(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('source_id',p.id,'url',o.final_url,'title',coalesce(p.title,'')))
 FROM app.crawl_page_records p JOIN app.fetch_observations o ON o.tenant_id=p.tenant_id AND o.site_id=p.site_id AND o.id=p.observation_id
 JOIN app.crawl_frontier_settlements s ON s.tenant_id=p.tenant_id AND s.site_id=p.site_id AND s.page_record_id=p.id AND s.terminal_state='fetched'
 JOIN app.crawl_manifests m ON m.tenant_id=p.tenant_id AND m.site_id=p.site_id AND m.crawl_run_id=p.crawl_run_id
 WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.http_status BETWEEN 200 AND 299),'[]'::jsonb);
END; $$;

CREATE FUNCTION control.content_writer_create_brief(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_payload jsonb,p_origin text,p_supersedes uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; inventory jsonb; ref text; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 inventory:=control.content_writer_inventory(p_hash,p_generation,p_site);
 IF p_origin IS NULL OR p_payload IS NULL OR p_origin NOT IN ('owner','evidence_proposal') OR jsonb_typeof(p_payload)<>'object' OR coalesce(p_payload->>'kind','') NOT IN ('new_article','content_refresh') OR coalesce(jsonb_array_length(p_payload->'source_ids'),0) NOT BETWEEN 1 AND 8 OR coalesce(jsonb_array_length(p_payload->'fact_ids'),0) NOT BETWEEN 1 AND 20 OR coalesce(jsonb_array_length(p_payload->'internal_links'),9)>8 THEN RETURN 'invalid'; END IF;
 FOR ref IN SELECT jsonb_array_elements_text(p_payload->'source_ids') LOOP
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(inventory) x WHERE x->>'source_id'=ref) THEN RETURN 'source_unavailable'; END IF;
 END LOOP;
 FOR ref IN SELECT jsonb_array_elements_text(p_payload->'fact_ids') LOOP
  IF NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f WHERE f.fact_id::text=ref) THEN RETURN 'fact_unavailable'; END IF;
 END LOOP;
 FOR ref IN SELECT jsonb_array_elements_text(p_payload->'internal_links') LOOP
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(inventory) x WHERE x->>'url'=ref) THEN RETURN 'link_unavailable'; END IF;
 END LOOP;
 IF p_supersedes IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.id=p_supersedes) OR EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.supersedes_id=p_supersedes)) THEN RETURN 'conflict'; END IF;
 INSERT INTO app.content_briefs VALUES(v.tenant_id,p_site,p_id,p_payload,p_origin,p_supersedes,v.user_id,transaction_timestamp());
 IF p_origin='owner' THEN INSERT INTO app.content_brief_acceptances VALUES(v.tenant_id,p_site,p_id,v.user_id,transaction_timestamp()); END IF;
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'brief_created',p_id,v.user_id,transaction_timestamp()); RETURN 'created';
END; $$;

CREATE FUNCTION control.content_writer_accept_brief(p_hash bytea,p_generation text,p_site uuid,p_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.id=p_id) OR EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.supersedes_id=p_id) THEN RETURN 'unavailable'; END IF;
 IF EXISTS(SELECT 1 FROM app.content_brief_acceptances a WHERE a.tenant_id=v.tenant_id AND a.site_id=p_site AND a.brief_id=p_id) THEN RETURN 'replayed'; END IF;
 INSERT INTO app.content_brief_acceptances VALUES(v.tenant_id,p_site,p_id,v.user_id,transaction_timestamp());
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'brief_accepted',p_id,v.user_id,transaction_timestamp()); RETURN 'accepted';
END; $$;

CREATE FUNCTION control.content_writer_set_cap(p_hash bytea,p_generation text,p_site uuid,p_cap integer)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; n uuid:=gen_random_uuid(); BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_cap IS NULL OR p_cap NOT BETWEEN 1 AND 5 THEN RETURN 'invalid'; END IF;
 INSERT INTO app.content_writer_caps VALUES(v.tenant_id,p_site,n,p_cap,v.user_id,transaction_timestamp());
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'cap_updated',n,v.user_id,transaction_timestamp()); RETURN 'updated';
END; $$;

CREATE FUNCTION control.content_writer_read(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; cap integer; used integer; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 SELECT c.cap INTO cap FROM app.content_writer_caps c WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site ORDER BY c.created_at DESC,c.id DESC LIMIT 1; cap:=coalesce(cap,2);
 SELECT count(*) INTO used FROM app.content_draft_intents d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND d.created_at>transaction_timestamp()-interval '7 days';
 RETURN jsonb_build_object('cap',cap,'used',used,'cap_reached',used>=cap,'platform_maximum',5,
 'briefs',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT b.id AS brief_id,b.payload,b.origin,b.supersedes_id,b.created_at,CASE WHEN EXISTS(SELECT 1 FROM app.content_briefs n WHERE n.tenant_id=b.tenant_id AND n.site_id=b.site_id AND n.supersedes_id=b.id) THEN 'superseded' WHEN EXISTS(SELECT 1 FROM app.content_brief_acceptances a WHERE a.tenant_id=b.tenant_id AND a.site_id=b.site_id AND a.brief_id=b.id) THEN 'accepted' ELSE 'proposed' END AS status FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site ORDER BY b.created_at DESC LIMIT 100) x),'[]'::jsonb),
 'drafts',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT d.id AS draft_id,d.brief_id,d.fact_snapshot,d.voice_snapshot,d.created_at,coalesce(r.payload,jsonb_build_object('state','outcome_unknown')) AS result FROM app.content_draft_intents d LEFT JOIN app.content_draft_results r ON r.tenant_id=d.tenant_id AND r.site_id=d.site_id AND r.draft_id=d.id WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site ORDER BY d.created_at DESC LIMIT 100) x),'[]'::jsonb),
 'candidates',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT c.id AS candidate_id,c.draft_id,c.manifest,encode(c.revision_sha256,'hex') AS revision_sha256,c.created_at,coalesce(r.decision,'pending') AS review_status FROM app.content_candidates c LEFT JOIN app.content_candidate_reviews r ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.candidate_id=c.id WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site ORDER BY c.created_at DESC LIMIT 100) x),'[]'::jsonb));
END; $$;

CREATE FUNCTION control.content_writer_begin_draft(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_brief uuid,p_input_hash bytea,p_facts jsonb,p_voice jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; d app.content_draft_intents%%ROWTYPE; brief app.content_briefs%%ROWTYPE; projection jsonb; ref text; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN jsonb_build_object('state','denied'); END IF;
 SELECT * INTO d FROM app.content_draft_intents x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site AND x.brief_id=p_brief AND x.writer_version='content-writer-v1';
 IF FOUND THEN RETURN jsonb_build_object('state','replayed','draft_id',d.id); END IF;
 SELECT * INTO brief FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.id=p_brief;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.supersedes_id=p_brief) OR NOT EXISTS(SELECT 1 FROM app.content_brief_acceptances a WHERE a.tenant_id=v.tenant_id AND a.site_id=p_site AND a.brief_id=p_brief) THEN RETURN jsonb_build_object('state','brief_unavailable'); END IF;
 projection:=control.content_writer_read(p_hash,p_generation,p_site);
 IF (projection->>'cap_reached')::boolean THEN RETURN jsonb_build_object('state','cap_reached'); END IF;
 FOR ref IN SELECT jsonb_array_elements_text(brief.payload->'fact_ids') LOOP
  IF NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f WHERE f.fact_id::text=ref) OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_facts) f WHERE f->>'fact_id'=ref) THEN RETURN jsonb_build_object('state','fact_unavailable'); END IF;
 END LOOP;
 INSERT INTO app.content_draft_intents VALUES(v.tenant_id,p_site,p_id,p_brief,'content-writer-v1',p_input_hash,p_facts,p_voice,transaction_timestamp());
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'draft_started',p_id,v.user_id,transaction_timestamp()); RETURN jsonb_build_object('state','started','draft_id',p_id,'tenant_id',v.tenant_id);
END; $$;

CREATE FUNCTION control.content_writer_finish_draft(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_canonical bytea,p_digest bytea,p_decision uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; payload jsonb; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.content_draft_intents d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND d.id=p_id) THEN RETURN 'unavailable'; END IF;
 IF EXISTS(SELECT 1 FROM app.content_draft_results d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND d.draft_id=p_id) THEN RETURN 'replayed'; END IF;
 IF p_digest IS DISTINCT FROM sha256(p_canonical) OR octet_length(p_canonical)>256000 THEN RETURN 'invalid'; END IF;
 payload:=convert_from(p_canonical,'UTF8')::jsonb;
 IF coalesce(payload->>'state','') NOT IN ('grounded','owner_required','rejected','failed') OR (payload->>'state'<>'failed' AND NOT EXISTS(SELECT 1 FROM app.decision_records r WHERE r.tenant_id=v.tenant_id AND r.site_id=p_site AND r.id=p_decision AND r.purpose='content_writer.claim_grounding' AND r.outcome<>'ship')) THEN RETURN 'invalid'; END IF;
 INSERT INTO app.content_draft_results VALUES(v.tenant_id,p_site,p_id,payload,p_canonical,p_digest,p_decision,transaction_timestamp());
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'draft_completed',p_id,v.user_id,transaction_timestamp()); RETURN 'completed';
END; $$;

CREATE FUNCTION control.content_writer_seal(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_draft uuid,p_extension uuid,p_build uuid,p_canonical bytea,p_digest bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; manifest jsonb; existing app.content_candidates%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN jsonb_build_object('state','denied'); END IF;
 SELECT * INTO existing FROM app.content_candidates c WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.draft_id=p_draft;
 IF FOUND THEN RETURN jsonb_build_object('state','replayed','candidate_id',existing.id,'revision_sha256',encode(existing.revision_sha256,'hex')); END IF;
 IF p_digest IS DISTINCT FROM sha256(p_canonical) OR octet_length(p_canonical)>256000 THEN RETURN jsonb_build_object('state','invalid'); END IF;
 manifest:=convert_from(p_canonical,'UTF8')::jsonb;
 IF manifest->>'approval_class' IS DISTINCT FROM 'A2' OR manifest->>'autonomy_eligible' IS DISTINCT FROM 'false' OR coalesce(jsonb_array_length(manifest->'changed_files'),0)<>1 OR NOT EXISTS(SELECT 1 FROM app.content_draft_results r JOIN app.content_draft_intents d ON d.tenant_id=r.tenant_id AND d.site_id=r.site_id AND d.id=r.draft_id WHERE r.tenant_id=v.tenant_id AND r.site_id=p_site AND r.draft_id=p_draft AND r.payload->>'state' IN ('grounded','owner_required') AND r.payload->'originality'->>'state'='original' AND NOT EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.supersedes_id=d.brief_id)) THEN RETURN jsonb_build_object('state','draft_unavailable'); END IF;
 IF NOT EXISTS(SELECT 1 FROM app.candidate_build_intents b JOIN app.candidate_build_receipts r ON r.tenant_id=b.tenant_id AND r.site_id=b.site_id AND r.build_id=b.id JOIN app.github_pr_extensions e ON e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.id=p_build AND b.extension_id=p_extension AND b.base_sha=manifest->>'base_sha' AND b.patch_sha256=manifest->>'patch_sha256' AND r.exit_class='passed' AND e.status='observed' AND e.base_sha=b.base_sha AND b.status='completed' AND b.recovery_generation=p_generation) THEN RETURN jsonb_build_object('state','build_unavailable'); END IF;
 INSERT INTO app.content_candidates VALUES(v.tenant_id,p_site,p_id,p_draft,p_extension,p_build,manifest,p_canonical,p_digest,transaction_timestamp());
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'candidate_sealed',p_id,v.user_id,transaction_timestamp()); RETURN jsonb_build_object('state','sealed','candidate_id',p_id,'revision_sha256',encode(p_digest,'hex'));
END; $$;

CREATE FUNCTION control.content_writer_review(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_digest bytea,p_decision text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; prior text; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_decision IS NULL OR p_decision NOT IN ('approved','rejected','changes_requested') OR NOT EXISTS(SELECT 1 FROM app.content_candidates c WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_id AND c.revision_sha256=p_digest) THEN RETURN 'conflict'; END IF;
 SELECT r.decision INTO prior FROM app.content_candidate_reviews r WHERE r.tenant_id=v.tenant_id AND r.site_id=p_site AND r.candidate_id=p_id;
 IF FOUND THEN RETURN CASE WHEN prior=p_decision THEN 'replayed' ELSE 'conflict' END; END IF;
 INSERT INTO app.content_candidate_reviews VALUES(v.tenant_id,p_site,p_id,p_decision,v.user_id,transaction_timestamp());
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'candidate_reviewed',p_id,v.user_id,transaction_timestamp()); RETURN 'reviewed';
END; $$;

REVOKE ALL ON FUNCTION control.content_writer_inventory(bytea,text,uuid),control.content_writer_read(bytea,text,uuid),control.content_writer_create_brief(bytea,text,uuid,uuid,jsonb,text,uuid),control.content_writer_accept_brief(bytea,text,uuid,uuid),control.content_writer_set_cap(bytea,text,uuid,integer),control.content_writer_begin_draft(bytea,text,uuid,uuid,uuid,bytea,jsonb,jsonb),control.content_writer_finish_draft(bytea,text,uuid,uuid,bytea,bytea,uuid),control.content_writer_seal(bytea,text,uuid,uuid,uuid,uuid,uuid,bytea,bytea),control.content_writer_review(bytea,text,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.content_writer_inventory(bytea,text,uuid),control.content_writer_read(bytea,text,uuid),control.content_writer_create_brief(bytea,text,uuid,uuid,jsonb,text,uuid),control.content_writer_accept_brief(bytea,text,uuid,uuid),control.content_writer_set_cap(bytea,text,uuid,integer),control.content_writer_begin_draft(bytea,text,uuid,uuid,uuid,bytea,jsonb,jsonb),control.content_writer_finish_draft(bytea,text,uuid,uuid,bytea,bytea,uuid),control.content_writer_seal(bytea,text,uuid,uuid,uuid,uuid,uuid,bytea,bytea),control.content_writer_review(bytea,text,uuid,uuid,bytea,text) TO signal_api;
