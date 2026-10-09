ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check CHECK
(egress_profile IN ('legacy_unqualified','crawl_page','crawl_robots','crawl_key_file','indexnow_submit',
 'browser_read','github_rest','slack_bot','slack_oauth','github_repository_write','google_oauth_token',
 'google_oauth_revoke','gsc_api','bing_oauth_token','bing_api','jev','model_json','openai_model',
 'openai_assistant','perplexity_assistant','gemini_assistant','dataforseo'));

ALTER FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text)
RENAME TO bind_before_indexnow_egress_profile;
REVOKE ALL ON FUNCTION control.bind_before_indexnow_egress_profile(uuid,uuid,uuid,bytea,text)
FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(
 p_tenant_id uuid,p_site_id uuid,p_operation_id uuid,p_request_sha256 bytea,p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE;
BEGIN
 IF p_profile NOT IN ('crawl_key_file','indexnow_submit') THEN
  RETURN control.bind_before_indexnow_egress_profile(p_tenant_id,p_site_id,p_operation_id,p_request_sha256,p_profile);
 END IF;
 PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
 PERFORM set_config('signal.site_id',p_site_id::text,true);
 SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id FOR UPDATE;
 IF NOT FOUND OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_request_sha256
  OR o.egress_profile NOT IN ('legacy_unqualified',p_profile)
  OR (p_profile='indexnow_submit' AND (o.purpose<>'connector' OR o.method<>'POST' OR NOT o.credentialed
    OR o.request_url<>'https://api.indexnow.org/indexnow' OR o.request_bytes>65536 OR o.max_response_bytes>4096))
  OR (p_profile='crawl_key_file' AND (o.purpose<>'crawl' OR o.method<>'GET' OR o.credentialed
    OR o.request_url !~ '^https://[^/?#]+/[A-Za-z0-9-]{8,128}\.txt$' OR o.request_bytes<>0 OR o.max_response_bytes>1024))
 THEN RAISE EXCEPTION 'indexnow_profile_denied' USING ERRCODE='22023'; END IF;
 UPDATE app.egress_operations SET egress_profile=p_profile WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id;
 RETURN 'bound';
END $$;
REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;

CREATE TABLE app.indexnow_key_intents (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 previous_key_id uuid, created_by_user_id uuid NOT NULL, recovery_generation text NOT NULL,
 membership_epoch bigint NOT NULL, site_epoch bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by_user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TABLE app.indexnow_keys (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 key_sha256 bytea NOT NULL CHECK(octet_length(key_sha256)=32),
 stored_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,id) REFERENCES app.indexnow_key_intents(tenant_id,site_id,id)
);
ALTER TABLE app.indexnow_key_intents ADD FOREIGN KEY(tenant_id,site_id,previous_key_id)
 REFERENCES app.indexnow_keys(tenant_id,site_id,id);
CREATE TABLE app.indexnow_key_retirements (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, old_key_id uuid NOT NULL, new_key_id uuid NOT NULL,
 retired_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,old_key_id),
 FOREIGN KEY(tenant_id,site_id,old_key_id) REFERENCES app.indexnow_keys(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,new_key_id) REFERENCES app.indexnow_keys(tenant_id,site_id,id),
 CHECK(old_key_id<>new_key_id)
);
ALTER TABLE app.candidate_recipe_revisions ALTER COLUMN audit_report_id DROP NOT NULL;
ALTER TABLE app.candidate_recipe_revisions ADD CONSTRAINT candidate_recipe_evidence_kind CHECK (
 audit_report_id IS NOT NULL OR
 coalesce((convert_from(canonical_manifest,'UTF8')::jsonb #>> '{evidence,finding,key}'='indexnow.key.required'
  AND convert_from(canonical_manifest,'UTF8')::jsonb ->>'approval_class'='owner_review'),false)
);
CREATE TABLE app.indexnow_key_revisions (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, key_id uuid NOT NULL, revision_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,site_id,key_id), UNIQUE(tenant_id,site_id,revision_id),
 FOREIGN KEY(tenant_id,site_id,key_id) REFERENCES app.indexnow_keys(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,revision_id) REFERENCES app.candidate_recipe_revisions(tenant_id,site_id,id)
);
CREATE TABLE app.indexnow_outbox (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 change_id uuid NOT NULL, delivery_attempt_id uuid NOT NULL, url text NOT NULL CHECK(length(url) BETWEEN 1 AND 2048),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','checking','dispatching','retry','accepted','rejected','skipped','outcome_unknown','exhausted')),
 reason text NOT NULL DEFAULT 'QUEUED', attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 4),
 available_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 worker_id uuid, attempt_id uuid, lease_until timestamptz, key_id uuid,
 generation text, membership_epoch bigint, site_epoch bigint,
 journal_generation uuid,journal_position bigint,journal_hash bytea,body_hash bytea,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id), UNIQUE(tenant_id,site_id,change_id,url), UNIQUE(tenant_id,site_id,id,url),
 FOREIGN KEY(tenant_id,site_id,change_id) REFERENCES app.github_pr_operations(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,delivery_attempt_id) REFERENCES app.github_delivery_receipts(tenant_id,site_id,attempt_id),
 FOREIGN KEY(tenant_id,site_id,key_id) REFERENCES app.indexnow_keys(tenant_id,site_id,id)
);
CREATE TABLE app.indexnow_receipts (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 outbox_id uuid NOT NULL, key_id uuid, key_egress_id uuid, submit_egress_id uuid,
 provider_status integer CHECK(provider_status BETWEEN 100 AND 599),
 outcome text NOT NULL, reason text NOT NULL, url text NOT NULL,
 journal_generation uuid,journal_position bigint,journal_hash bytea,
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,outbox_id) REFERENCES app.indexnow_outbox(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,outbox_id,url) REFERENCES app.indexnow_outbox(tenant_id,site_id,id,url),
 FOREIGN KEY(tenant_id,site_id,key_id) REFERENCES app.indexnow_keys(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,key_egress_id) REFERENCES app.egress_operations(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,submit_egress_id) REFERENCES app.egress_operations(tenant_id,site_id,id)
);
CREATE INDEX indexnow_outbox_available ON app.indexnow_outbox(tenant_id,site_id,available_at);
CREATE INDEX indexnow_receipts_recent ON app.indexnow_receipts(tenant_id,site_id,recorded_at DESC);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['indexnow_key_intents','indexnow_keys','indexnow_key_retirements','indexnow_key_revisions','indexnow_outbox','indexnow_receipts'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY indexnow_scope ON app.%%I USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_identity,signal_api,signal_bootstrap,signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest',t);
  IF t<>'indexnow_outbox' THEN
   EXECUTE format('CREATE TRIGGER indexnow_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
  END IF;
 END LOOP;
END $$;

CREATE FUNCTION control.prepare_indexnow_key(p_session bytea,p_site uuid,p_generation text,p_key uuid,p_previous uuid)
RETURNS TABLE(tenant_id uuid,key_id uuid,origin text,outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; k app.indexnow_key_intents%%ROWTYPE;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN
  RETURN QUERY SELECT NULL::uuid,NULL::uuid,NULL::text,'denied'::text; RETURN; END IF;
 PERFORM 1 FROM app.sites WHERE app.sites.tenant_id=a.tenant_id AND id=p_site FOR UPDATE;
 SELECT * INTO k FROM app.indexnow_key_intents WHERE app.indexnow_key_intents.tenant_id=a.tenant_id AND site_id=p_site ORDER BY created_at DESC,id DESC LIMIT 1;
 IF FOUND AND k.id=p_key THEN
  IF k.previous_key_id IS DISTINCT FROM p_previous OR k.recovery_generation<>p_generation
   OR k.membership_epoch<>a.membership_epoch OR k.site_epoch<>a.site_authorization_epoch THEN
   RETURN QUERY SELECT NULL::uuid,NULL::uuid,NULL::text,'conflict'::text; RETURN; END IF;
 ELSE
  IF k.id IS NOT NULL AND (p_previous IS DISTINCT FROM k.id OR NOT EXISTS
    (SELECT 1 FROM app.indexnow_key_revisions WHERE app.indexnow_key_revisions.tenant_id=a.tenant_id AND site_id=p_site AND app.indexnow_key_revisions.key_id=k.id)) THEN
   RETURN QUERY SELECT NULL::uuid,NULL::uuid,NULL::text,'key_exists'::text; RETURN; END IF;
  IF k.id IS NULL AND p_previous IS NOT NULL THEN RETURN QUERY SELECT NULL::uuid,NULL::uuid,NULL::text,'conflict'::text; RETURN; END IF;
  INSERT INTO app.indexnow_key_intents VALUES(a.tenant_id,p_site,p_key,p_previous,a.user_id,p_generation,a.membership_epoch,a.site_authorization_epoch,transaction_timestamp());
 END IF;
 RETURN QUERY SELECT a.tenant_id,p_key,s.primary_origin,'prepared'::text FROM app.sites s WHERE s.tenant_id=a.tenant_id AND s.id=p_site;
END $$;

CREATE FUNCTION control.store_indexnow_key(p_session bytea,p_site uuid,p_generation text,p_key uuid,p_hash bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; i app.indexnow_key_intents%%ROWTYPE; k app.indexnow_keys%%ROWTYPE;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'denied'; END IF;
 SELECT * INTO i FROM app.indexnow_key_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_key;
 IF NOT FOUND OR i.created_by_user_id<>a.user_id OR i.recovery_generation<>p_generation
  OR i.membership_epoch<>a.membership_epoch OR i.site_epoch<>a.site_authorization_epoch
  OR p_hash IS NULL OR octet_length(p_hash)<>32 THEN RETURN 'denied'; END IF;
 SELECT * INTO k FROM app.indexnow_keys WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_key;
 IF FOUND THEN RETURN CASE WHEN k.key_sha256=p_hash THEN 'stored' ELSE 'conflict' END; END IF;
 INSERT INTO app.indexnow_keys VALUES(a.tenant_id,p_site,p_key,p_hash,transaction_timestamp()); RETURN 'stored';
END $$;

CREATE FUNCTION control.seal_indexnow_key_revision(p_session bytea,p_site uuid,p_generation text,p_key uuid,
 p_revision uuid,p_canonical bytea,p_hash bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; k app.indexnow_keys%%ROWTYPE; i app.indexnow_key_intents%%ROWTYPE; m jsonb;
 b app.candidate_build_intents%%ROWTYPE; r app.candidate_build_receipts%%ROWTYPE; release control.recipe_releases%%ROWTYPE;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site FOR UPDATE;
 SELECT * INTO k FROM app.indexnow_keys WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_key;
 SELECT * INTO i FROM app.indexnow_key_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_key;
 IF k.id IS NULL OR i.created_by_user_id<>a.user_id OR i.recovery_generation<>p_generation
  OR i.membership_epoch<>a.membership_epoch OR i.site_epoch<>a.site_authorization_epoch
  OR p_canonical IS NULL OR octet_length(p_canonical) NOT BETWEEN 2 AND 32768 OR p_hash IS DISTINCT FROM sha256(p_canonical)
  OR EXISTS(SELECT 1 FROM app.indexnow_key_retirements WHERE tenant_id=a.tenant_id AND site_id=p_site AND old_key_id=p_key) THEN RETURN 'denied'; END IF;
 IF EXISTS(SELECT 1 FROM app.indexnow_key_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site AND key_id=p_key) THEN
  RETURN CASE WHEN EXISTS(SELECT 1 FROM app.candidate_recipe_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_revision AND canonical_manifest=p_canonical) THEN 'sealed' ELSE 'conflict' END;
 END IF;
 m:=convert_from(p_canonical,'UTF8')::jsonb;
 SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=(m->>'build_id')::uuid;
 SELECT * INTO r FROM app.candidate_build_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=b.id;
 SELECT * INTO release FROM control.recipe_releases WHERE id=(m->>'recipe_release_id')::uuid;
 IF b.id IS NULL OR r.build_id IS NULL OR b.status<>'completed' OR r.exit_class<>'passed'
  OR b.requested_by_user_id<>a.user_id OR b.recovery_generation<>p_generation OR b.membership_epoch<>a.membership_epoch OR b.site_epoch<>a.site_authorization_epoch
  OR release.recipe_key<>'technical_indexnow_key' OR release.content_hash IS DISTINCT FROM decode(m->>'release_content_hash','hex')
  OR (SELECT status FROM control.recipe_release_events WHERE release_id=release.id ORDER BY sequence_number DESC LIMIT 1) IS DISTINCT FROM 'REVIEWED'
  OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=release.id)
  OR m->>'schema_version' IS DISTINCT FROM '1' OR m->>'site_id' IS DISTINCT FROM p_site::text
  OR m->>'extension_id' IS DISTINCT FROM b.extension_id::text OR m->>'base_sha' IS DISTINCT FROM b.base_sha
  OR m->>'patch_sha256' IS DISTINCT FROM r.patch_sha256 OR r.patch_sha256<>b.patch_sha256
  OR m->'audit_report_id' IS DISTINCT FROM 'null'::jsonb OR m->>'finding_id' IS DISTINCT FROM p_key::text
  OR m#>>'{evidence,key_id}' IS DISTINCT FROM p_key::text OR m#>>'{evidence,key_sha256}' IS DISTINCT FROM encode(k.key_sha256,'hex')
  OR m#>>'{evidence,finding,key}' IS DISTINCT FROM 'indexnow.key.required'
  OR m#>>'{evidence,finding,id}' IS DISTINCT FROM p_key::text
  OR m#>>'{evidence,site_origin}' IS DISTINCT FROM (SELECT primary_origin FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site)
  OR m#>>'{evidence,page_url}' IS DISTINCT FROM (m#>>'{evidence,site_origin}')||'/'||(m->>'source_path')
  OR m#>>'{evidence,finding,resource_locator}' IS DISTINCT FROM (m#>>'{evidence,page_url}')
  OR m->>'source_path' IS DISTINCT FROM (m#>>'{patch,after}')||'.txt'
  OR m#>>'{patch,after}' !~ '^[A-Za-z0-9-]{8,128}$'
  OR sha256(convert_to(m#>>'{patch,after}','UTF8')) IS DISTINCT FROM k.key_sha256
  OR m#>>'{patch,before}' IS DISTINCT FROM '' OR m#>>'{patch,offset}' IS DISTINCT FROM '0'
  OR m->>'source_sha256' IS DISTINCT FROM encode(sha256(''::bytea),'hex')
  OR m->>'result_sha256' IS DISTINCT FROM encode(k.key_sha256,'hex')
  OR m->>'approval_class' IS DISTINCT FROM 'owner_review' OR m->>'claim_review_required' IS DISTINCT FROM 'false' OR m->'model_draft' IS DISTINCT FROM 'null'::jsonb
  OR m#>>'{build_receipt,toolchain}' IS DISTINCT FROM r.toolchain OR m#>>'{build_receipt,command}' IS DISTINCT FROM r.build_command
  OR m#>>'{build_receipt,logs_sha256}' IS DISTINCT FROM r.logs_sha256 OR m#>'{build_receipt,artifacts}' IS DISTINCT FROM r.artifacts
  OR m#>>'{build_receipt,exit_class}' IS DISTINCT FROM 'passed'
  OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r.artifacts) artifact WHERE artifact->>'path'='_site/'||(m->>'source_path') AND artifact->>'sha256'=encode(k.key_sha256,'hex') AND (artifact->>'size')::integer=length(m#>>'{patch,after}'))
  OR NOT EXISTS(SELECT 1 FROM app.github_pr_extensions ext JOIN app.github_read_bindings binding ON binding.tenant_id=ext.tenant_id AND binding.site_id=ext.site_id AND binding.id=ext.binding_id
   WHERE ext.tenant_id=a.tenant_id AND ext.site_id=p_site AND ext.id=b.extension_id AND ext.status='observed' AND binding.status='active' AND ext.framework='eleventy' AND ext.content_format='html' AND ext.base_sha=b.base_sha AND ext.tree_sha=b.tree_sha)
 THEN RETURN 'invalid_revision'; END IF;
 INSERT INTO app.candidate_recipe_revisions(tenant_id,site_id,id,extension_id,build_id,audit_report_id,finding_id,recipe_release_id,release_content_hash,base_sha,patch_sha256,canonical_manifest,created_by_user_id,idempotency_key,membership_epoch,site_epoch,recovery_generation)
 VALUES(a.tenant_id,p_site,p_revision,b.extension_id,b.id,NULL,p_key,release.id,release.content_hash,b.base_sha,b.patch_sha256,p_canonical,a.user_id,p_key,a.membership_epoch,a.site_authorization_epoch,p_generation);
 INSERT INTO app.indexnow_key_revisions VALUES(a.tenant_id,p_site,p_key,p_revision);
 IF i.previous_key_id IS NOT NULL THEN INSERT INTO app.indexnow_key_retirements VALUES(a.tenant_id,p_site,i.previous_key_id,p_key,transaction_timestamp()); END IF;
 RETURN 'sealed';
END $$;
REVOKE ALL ON FUNCTION control.seal_indexnow_key_revision(bytea,uuid,text,uuid,uuid,bytea,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seal_indexnow_key_revision(bytea,uuid,text,uuid,uuid,bytea,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.candidate_review_risk(p_revision uuid) RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT GREATEST(
  CASE release.recipe_key
   WHEN 'technical_title' THEN 2 WHEN 'technical_description' THEN 2
   WHEN 'technical_alt' THEN 2 WHEN 'technical_structured_data' THEN 2
   WHEN 'technical_broken_link' THEN 2 WHEN 'technical_canonical' THEN 4
   WHEN 'technical_indexnow_key' THEN 4 ELSE 6 END,
  CASE convert_from(release.canonical_body,'UTF8')::jsonb->>'approval_class'
   WHEN 'A0' THEN 0 WHEN 'A1' THEN 1 WHEN 'A2' THEN 2 WHEN 'A3' THEN 3
   WHEN 'A4' THEN 4 WHEN 'A5' THEN 5 WHEN 'owner_review' THEN 0 ELSE 6 END
 ) FROM app.candidate_recipe_revisions r JOIN control.recipe_releases release
 ON release.id=r.recipe_release_id AND release.content_hash=r.release_content_hash WHERE r.id=p_revision;
$$;

ALTER FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) RENAME TO github_pr_eligible_before_indexnow;
REVOKE ALL ON FUNCTION control.github_pr_eligible_before_indexnow(bytea,uuid,text,uuid) FROM PUBLIC;
CREATE FUNCTION control.github_pr_operation_eligible(p_session bytea,p_site uuid,p_generation text,p_revision uuid)
RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,membership_epoch bigint,site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
 SELECT * INTO a FROM control.github_pr_eligible_before_indexnow(p_session,p_site,p_generation,p_revision);
 IF a.outcome='eligible' AND EXISTS(SELECT 1 FROM app.indexnow_key_revisions kr JOIN app.indexnow_key_retirements r
  ON r.tenant_id=kr.tenant_id AND r.site_id=kr.site_id AND r.old_key_id=kr.key_id
  WHERE kr.tenant_id=a.tenant_id AND kr.site_id=p_site AND kr.revision_id=p_revision) THEN
  RETURN QUERY SELECT 'key_retired'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
 END IF;
 RETURN QUERY SELECT a.outcome,a.tenant_id,a.user_id,a.membership_epoch,a.site_epoch;
END $$;
REVOKE ALL ON FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.queue_indexnow_verified_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE receipt jsonb; m jsonb;
BEGIN
 receipt:=convert_from(NEW.canonical_receipt,'UTF8')::jsonb;
 IF receipt->>'outcome'<>'verified' THEN RETURN NEW; END IF;
 SELECT convert_from(r.canonical_manifest,'UTF8')::jsonb INTO m FROM app.github_pr_operations o
 JOIN app.candidate_recipe_revisions r ON r.tenant_id=o.tenant_id AND r.site_id=o.site_id AND r.id=o.candidate_revision_id
 WHERE o.tenant_id=NEW.tenant_id AND o.site_id=NEW.site_id AND o.id=NEW.operation_id;
 IF m#>>'{evidence,finding,key}'='indexnow.key.required' THEN RETURN NEW; END IF;
 IF m#>>'{evidence,page_url}' IS NOT NULL THEN
  INSERT INTO app.indexnow_outbox(tenant_id,site_id,id,change_id,delivery_attempt_id,url)
  VALUES(NEW.tenant_id,NEW.site_id,gen_random_uuid(),NEW.operation_id,NEW.attempt_id,m#>>'{evidence,page_url}')
  ON CONFLICT(tenant_id,site_id,change_id,url) DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER indexnow_verified_change AFTER INSERT ON app.github_delivery_receipts
FOR EACH ROW EXECUTE FUNCTION control.queue_indexnow_verified_change();

CREATE FUNCTION control.claim_indexnow(p_session bytea,p_site uuid,p_generation text,p_worker uuid,p_attempt uuid)
RETURNS TABLE(tenant_id uuid,outbox_id uuid,change_id uuid,url text,origin text,key_id uuid,key_hash bytea,delivery_hash bytea,outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o app.indexnow_outbox%%ROWTYPE; k app.indexnow_keys%%ROWTYPE; site_origin text;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN
  RETURN QUERY SELECT NULL::uuid,NULL::uuid,NULL::uuid,NULL::text,NULL::text,NULL::uuid,NULL::bytea,NULL::bytea,'denied'::text; RETURN; END IF;
 INSERT INTO app.indexnow_receipts(tenant_id,site_id,id,outbox_id,key_id,outcome,reason,url,journal_generation,journal_position,journal_hash)
 SELECT q.tenant_id,q.site_id,q.attempt_id,q.id,q.key_id,'outcome_unknown','POST_OUTCOME_UNKNOWN',q.url,q.journal_generation,q.journal_position,q.journal_hash
 FROM app.indexnow_outbox q WHERE q.tenant_id=a.tenant_id AND q.site_id=p_site AND q.state='dispatching' AND q.lease_until<=transaction_timestamp()
 ON CONFLICT ON CONSTRAINT indexnow_receipts_pkey DO NOTHING;
 UPDATE app.indexnow_outbox q SET state='outcome_unknown',reason='POST_OUTCOME_UNKNOWN'
 WHERE q.tenant_id=a.tenant_id AND q.site_id=p_site AND q.state='dispatching' AND q.lease_until<=transaction_timestamp();
 SELECT * INTO o FROM app.indexnow_outbox q WHERE q.tenant_id=a.tenant_id AND q.site_id=p_site
  AND (q.state IN ('pending','retry') OR (q.state='checking' AND q.lease_until<=transaction_timestamp()))
  AND q.available_at<=transaction_timestamp() ORDER BY q.available_at,q.id LIMIT 1 FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT s.primary_origin INTO site_origin FROM app.sites s WHERE s.tenant_id=a.tenant_id AND s.id=p_site;
 SELECT key.* INTO k FROM app.indexnow_keys key JOIN app.indexnow_key_intents i ON i.tenant_id=key.tenant_id AND i.site_id=key.site_id AND i.id=key.id
 WHERE key.tenant_id=a.tenant_id AND key.site_id=p_site AND i.recovery_generation=p_generation
  AND (o.attempt_count=0 OR key.id=o.key_id)
  AND NOT EXISTS(SELECT 1 FROM app.indexnow_key_retirements r WHERE r.tenant_id=key.tenant_id AND r.site_id=key.site_id AND r.old_key_id=key.id)
  AND EXISTS(SELECT 1 FROM app.indexnow_key_revisions kr JOIN app.github_pr_operations pr ON pr.tenant_id=kr.tenant_id AND pr.site_id=kr.site_id AND pr.candidate_revision_id=kr.revision_id
   WHERE kr.tenant_id=key.tenant_id AND kr.site_id=key.site_id AND kr.key_id=key.id AND pr.state='opened')
 ORDER BY i.created_at DESC,i.id DESC LIMIT 1;
 UPDATE app.indexnow_outbox q SET state='checking',worker_id=p_worker,attempt_id=p_attempt,lease_until=transaction_timestamp()+interval '60 seconds',
  key_id=k.id,generation=p_generation,membership_epoch=a.membership_epoch,site_epoch=a.site_authorization_epoch
 WHERE q.tenant_id=a.tenant_id AND q.site_id=p_site AND q.id=o.id;
 RETURN QUERY SELECT a.tenant_id,o.id,o.change_id,o.url,site_origin,k.id,k.key_sha256,r.receipt_sha256,
 CASE WHEN NOT control.current_github_site_proof(a.tenant_id,p_site) THEN 'EC_142_SITE_UNVERIFIED'
  WHEN k.id IS NULL THEN CASE WHEN EXISTS(SELECT 1 FROM app.indexnow_keys known WHERE known.tenant_id=a.tenant_id AND known.site_id=p_site)
    THEN 'EC_142_KEY_PR_REQUIRED' ELSE 'EC_142_KEY_NOT_CREATED' END ELSE 'claimed' END
 FROM app.github_delivery_receipts r WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND r.attempt_id=o.delivery_attempt_id;
END $$;

CREATE FUNCTION control.begin_indexnow_submit(p_session bytea,p_site uuid,p_generation text,p_outbox uuid,p_worker uuid,p_attempt uuid,p_key_egress uuid,
 p_journal_generation uuid,p_journal_position bigint,p_journal_hash bytea,p_body_hash bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o app.indexnow_outbox%%ROWTYPE; k app.indexnow_keys%%ROWTYPE; e app.egress_operations%%ROWTYPE; origin text;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF p_journal_generation IS NULL OR p_journal_position IS NULL OR p_journal_position<1
  OR p_journal_hash IS NULL OR octet_length(p_journal_hash)<>32 OR p_body_hash IS NULL OR octet_length(p_body_hash)<>32 THEN RETURN 'denied'; END IF;
 IF a.outcome<>'authorized' OR a.role_key<>'owner' OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'denied'; END IF;
 SELECT * INTO o FROM app.indexnow_outbox WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_outbox FOR UPDATE;
 IF NOT FOUND OR o.state<>'checking' OR o.worker_id<>p_worker OR o.attempt_id<>p_attempt OR o.lease_until<=transaction_timestamp()
  OR o.generation<>p_generation OR o.membership_epoch<>a.membership_epoch OR o.site_epoch<>a.site_authorization_epoch OR o.attempt_count>=4 THEN RETURN 'denied'; END IF;
 SELECT * INTO k FROM app.indexnow_keys WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=o.key_id;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM app.indexnow_key_retirements WHERE tenant_id=a.tenant_id AND site_id=p_site AND old_key_id=k.id) THEN RETURN 'denied'; END IF;
 SELECT primary_origin INTO origin FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site;
 SELECT * INTO e FROM app.egress_operations WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_key_egress;
 IF NOT FOUND OR e.egress_profile<>'crawl_key_file' OR e.state<>'observed' OR e.network_outcome<>'fetched'
  OR e.http_status<>200 OR e.media_type<>'text/plain' OR e.response_sha256<>k.key_sha256
  OR e.origin<>origin OR sha256(convert_to(regexp_replace(substring(e.request_url FROM length(origin)+2),'\.txt$',''),'UTF8'))<>k.key_sha256
  OR e.dispatched_at<transaction_timestamp()-interval '30 seconds'
  OR o.url NOT LIKE origin||'/%%' THEN RETURN 'key_unavailable'; END IF;
 UPDATE app.indexnow_outbox SET state='dispatching',attempt_count=attempt_count+1,
  journal_generation=p_journal_generation,journal_position=p_journal_position,journal_hash=p_journal_hash,body_hash=p_body_hash
 WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_outbox;
 RETURN 'permitted';
END $$;

CREATE FUNCTION control.finish_indexnow(p_session bytea,p_site uuid,p_generation text,p_outbox uuid,p_worker uuid,p_attempt uuid,
 p_key_egress uuid,p_submit_egress uuid,p_status integer,p_reason text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o app.indexnow_outbox%%ROWTYPE; e app.egress_operations%%ROWTYPE; result text;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN RETURN 'denied'; END IF;
 SELECT * INTO o FROM app.indexnow_outbox WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_outbox FOR UPDATE;
 IF NOT FOUND OR o.worker_id IS DISTINCT FROM p_worker OR o.attempt_id IS DISTINCT FROM p_attempt THEN RETURN 'denied'; END IF;
 IF EXISTS(SELECT 1 FROM app.indexnow_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_attempt) THEN RETURN o.state; END IF;
 IF o.state NOT IN ('checking','dispatching') OR p_reason NOT IN (
  ('KEY_DEPLOYED'),('EC_142_SITE_UNVERIFIED'),('EC_142_KEY_NOT_CREATED'),
  ('EC_142_KEY_UNREACHABLE'),('EC_142_KEY_REDIRECT'),('EC_142_KEY_MISSING'),
  ('EC_142_KEY_CONTENT_TYPE'),('EC_142_KEY_MISMATCH'),('EC_142_KEY_PR_REQUIRED'),
  ('INDEXNOW_SECRET_UNAVAILABLE'),('POST_OUTCOME_UNKNOWN'),('SUBMISSION_SCOPE_REJECTED')) THEN RETURN 'denied'; END IF;
 IF p_status IS NOT NULL THEN
  SELECT * INTO e FROM app.egress_operations WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_submit_egress;
  IF NOT FOUND OR o.state<>'dispatching' OR e.egress_profile<>'indexnow_submit' OR e.request_url<>'https://api.indexnow.org/indexnow'
   OR e.state<>'observed' OR e.network_outcome<>'fetched' OR e.http_status IS DISTINCT FROM p_status
   OR e.request_body_sha256 IS DISTINCT FROM o.body_hash THEN RETURN 'denied'; END IF;
  result:=CASE WHEN p_status IN (200,202) THEN 'accepted'
   WHEN p_status=429 OR p_status>=500 THEN CASE WHEN o.attempt_count<4 THEN 'retry' ELSE 'exhausted' END
   ELSE 'rejected' END;
 ELSE result:=CASE WHEN o.state='dispatching' OR p_reason='POST_OUTCOME_UNKNOWN' THEN 'outcome_unknown' ELSE 'skipped' END; END IF;
 INSERT INTO app.indexnow_receipts VALUES(a.tenant_id,p_site,p_attempt,p_outbox,o.key_id,p_key_egress,p_submit_egress,p_status,result,p_reason,o.url,o.journal_generation,o.journal_position,o.journal_hash,transaction_timestamp());
 UPDATE app.indexnow_outbox SET state=result,reason=p_reason,available_at=transaction_timestamp()+make_interval(secs=>least(3600,30*power(2,attempt_count)::integer))
 WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_outbox;
 RETURN result;
END $$;

CREATE FUNCTION control.indexnow_known_effect(p_session bytea,p_site uuid,p_generation text,p_operation uuid,p_journal_generation uuid,p_position bigint,p_hash bytea)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN RETURN false; END IF;
 RETURN EXISTS(SELECT 1 FROM app.indexnow_receipts r WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site
  AND r.submit_egress_id=p_operation AND r.journal_generation=p_journal_generation AND r.journal_position=p_position
  AND r.journal_hash=p_hash AND r.provider_status IS NOT NULL);
END $$;
REVOKE ALL ON FUNCTION control.indexnow_known_effect(bytea,uuid,text,uuid,uuid,bigint,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.indexnow_known_effect(bytea,uuid,text,uuid,uuid,bigint,bytea) TO signal_identity;

CREATE FUNCTION control.read_indexnow(p_session bytea,p_site uuid,p_generation text)
RETURNS TABLE(document jsonb,outcome text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; k app.indexnow_keys%%ROWTYPE; status text; reason text;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN RETURN QUERY SELECT NULL::jsonb,'denied'::text; RETURN; END IF;
 SELECT key.* INTO k FROM app.indexnow_keys key JOIN app.indexnow_key_intents i ON i.tenant_id=key.tenant_id AND i.site_id=key.site_id AND i.id=key.id
 WHERE key.tenant_id=a.tenant_id AND key.site_id=p_site AND NOT EXISTS(SELECT 1 FROM app.indexnow_key_retirements r WHERE r.tenant_id=key.tenant_id AND r.site_id=key.site_id AND r.old_key_id=key.id)
 ORDER BY i.created_at DESC,i.id DESC LIMIT 1;
 status:='not_created'; reason:='EC_142_KEY_NOT_CREATED';
 IF k.id IS NOT NULL THEN
  reason:='EC_142_KEY_PR_REQUIRED';
  IF EXISTS(SELECT 1 FROM app.indexnow_key_revisions kr JOIN app.github_pr_operations pr
   ON pr.tenant_id=kr.tenant_id AND pr.site_id=kr.site_id AND pr.candidate_revision_id=kr.revision_id
   WHERE kr.tenant_id=a.tenant_id AND kr.site_id=p_site AND kr.key_id=k.id AND pr.state='opened') THEN status:='pr_open'; END IF;
  SELECT r.reason INTO reason FROM app.indexnow_receipts r WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND r.key_id=k.id ORDER BY r.recorded_at DESC,r.id DESC LIMIT 1;
  reason:=coalesce(reason,'EC_142_KEY_PR_REQUIRED');
  IF reason='KEY_DEPLOYED' THEN status:='deployed';
  ELSIF reason IN ('EC_142_KEY_MISMATCH','EC_142_KEY_CONTENT_TYPE','EC_142_KEY_REDIRECT') THEN status:='mismatch'; END IF;
 END IF;
 RETURN QUERY SELECT jsonb_build_object('key_status',status,'key_id',k.id,'reason',reason,
 'submissions',coalesce((SELECT jsonb_agg(value ORDER BY recorded_at DESC) FROM
  (SELECT jsonb_build_object('change_id',o.change_id,'urls',jsonb_build_array(r.url),'state',r.outcome,'provider_status',r.provider_status,'reason',r.reason,'recorded_at',r.recorded_at) value,r.recorded_at
   FROM app.indexnow_receipts r JOIN app.indexnow_outbox o ON o.tenant_id=r.tenant_id AND o.site_id=r.site_id AND o.id=r.outbox_id
   WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site ORDER BY r.recorded_at DESC LIMIT 20) recent),'[]'::jsonb)), 'found'::text;
END $$;

REVOKE ALL ON FUNCTION control.prepare_indexnow_key(bytea,uuid,text,uuid,uuid),control.store_indexnow_key(bytea,uuid,text,uuid,bytea),
 control.claim_indexnow(bytea,uuid,text,uuid,uuid),control.begin_indexnow_submit(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,bigint,bytea,bytea),
 control.finish_indexnow(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,integer,text),control.read_indexnow(bytea,uuid,text),control.queue_indexnow_verified_change() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_indexnow_key(bytea,uuid,text,uuid,uuid),control.store_indexnow_key(bytea,uuid,text,uuid,bytea),
 control.claim_indexnow(bytea,uuid,text,uuid,uuid),control.begin_indexnow_submit(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,bigint,bytea,bytea),
 control.finish_indexnow(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,integer,text),control.read_indexnow(bytea,uuid,text) TO signal_identity;
