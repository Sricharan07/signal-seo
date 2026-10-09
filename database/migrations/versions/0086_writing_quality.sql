CREATE TABLE app.model_budget_caps (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 cap_micros bigint NOT NULL CHECK(cap_micros BETWEEN 0 AND 1000000000), actor_id uuid NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id), FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
CREATE TABLE app.model_budget_calls (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 generation text NOT NULL, request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 role text NOT NULL CHECK(role IN ('page_type','fact_extraction','metadata_draft','claim_check',
 'article_outline','article_draft','article_critique','article_revise','report_text')),
 model_release jsonb NOT NULL CHECK(jsonb_typeof(model_release)='object' AND octet_length(model_release::text)<=2048),
 reserved_micros bigint NOT NULL CHECK(reserved_micros BETWEEN 1 AND 1000000000),
 month date NOT NULL DEFAULT date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date,
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id), FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
CREATE TABLE app.model_budget_dispatches (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,call_id uuid NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(), PRIMARY KEY(tenant_id,site_id,call_id),
 FOREIGN KEY(tenant_id,site_id,call_id) REFERENCES app.model_budget_calls(tenant_id,site_id,id)
);
CREATE TABLE app.model_budget_receipts (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,call_id uuid NOT NULL,
 cost_micros bigint NOT NULL CHECK(cost_micros BETWEEN 0 AND 1000000000000),
 response_sha256 bytea NOT NULL CHECK(octet_length(response_sha256)=32),
 receipt jsonb NOT NULL CHECK(jsonb_typeof(receipt)='object' AND octet_length(receipt::text)<=4096),
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(), PRIMARY KEY(tenant_id,site_id,call_id),
 FOREIGN KEY(tenant_id,site_id,call_id) REFERENCES app.model_budget_dispatches(tenant_id,site_id,call_id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['model_budget_caps','model_budget_calls','model_budget_dispatches','model_budget_receipts'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY model_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,signal_workflow,signal_scheduler,signal_crawl_ingest,signal_crawl_admission',t);
 END LOOP;
END; $$;

ALTER TABLE app.agent_runs DROP CONSTRAINT agent_runs_release_key_check;
ALTER TABLE app.agent_runs ADD CONSTRAINT agent_runs_release_key_check CHECK(release_key IN (
 'local-gpt-5.6-luna-metadata-v1','verified-gpt-5.6-luna-metadata-v1',
 'local-gpt-6-luna-metadata-v2','verified-gpt-6-luna-metadata-v2'));
ALTER TABLE app.model_calls DROP CONSTRAINT model_calls_model_requested_check;
ALTER TABLE app.model_calls ADD CONSTRAINT model_calls_model_requested_check
 CHECK(model_requested ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$');
ALTER TABLE app.model_calls DROP CONSTRAINT model_calls_model_reported_check;
ALTER TABLE app.model_calls ADD CONSTRAINT model_calls_model_reported_check
 CHECK(model_reported IS NULL OR (model_reported ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
 AND (model_reported=model_requested OR left(model_reported,length(model_requested)+1)=model_requested||'-')));
DO $$ DECLARE f record; definition text; BEGIN
 FOR f IN SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='control' AND p.prokind='f' AND p.prosrc LIKE '%%gpt-5.6-luna%%'
 AND p.proname<>'record_decision_recommendation' LOOP
  definition:=pg_get_functiondef(f.oid);
  definition:=replace(definition,'local-gpt-5.6-luna-metadata-v1','local-gpt-6-luna-metadata-v2');
  definition:=replace(definition,'verified-gpt-5.6-luna-metadata-v1','verified-gpt-6-luna-metadata-v2');
  definition:=replace(definition,'gpt-5.6-luna','gpt-6-luna');
  definition:=replace(definition,'gpt-5[.]6-luna','gpt-6-luna');
  definition:=replace(definition,'6c50fef0b4d7c0f096b34a2cd7f52de346e205bae3d788a7f25a7ddce73746e0',
  'ce56187c86a67106e9ad047204b829920a1a153b49385ca43a000738548e8005');
  definition:=replace(definition,'f963221dfbac7cc218bead459e6f11ca61d07e41a98ae8fa46d784f5d06b9347',
  'fa4796b4dde74981d02a4009f5bf28a6b486dacee63e105692f5fbb88a59d8c6');
  EXECUTE definition;
 END LOOP;
END; $$;

-- Operator-selected metadata aliases are bound at intent creation, not learned
-- from generated text. Keep the original ten-argument default ports intact.
DO $$ DECLARE name text; definition text; signature text; BEGIN
 FOREACH name IN ARRAY ARRAY['begin_authenticated_fixture_model_run',
 'begin_authenticated_verified_homepage_model_run'] LOOP
  signature:='control.'||name||'(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,bytea,bytea)';
  SELECT pg_get_functiondef(signature::regprocedure) INTO definition;
  definition:=replace(definition,'p_input_sha256 bytea)', 'p_input_sha256 bytea, p_model_requested text)');
  definition:=replace(definition,'IF p_agent_run_id IS NULL',
  'IF p_model_requested IS NULL OR p_model_requested !~ ''^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'' OR p_agent_run_id IS NULL');
  definition:=replace(definition,'run.input_sha256' || chr(10) || '      INTO v_existing',
  'run.input_sha256, call.model_requested' || chr(10) || '      INTO v_existing');
  definition:=replace(definition,'IF v_existing.input_sha256 IS DISTINCT FROM p_input_sha256 THEN',
  'IF v_existing.input_sha256 IS DISTINCT FROM p_input_sha256 OR v_existing.model_requested IS DISTINCT FROM p_model_requested THEN');
  definition:=replace(definition,'''openai'', ''gpt-6-luna'', ''requested''',
  '''openai'', p_model_requested, ''requested''');
  EXECUTE definition;
  EXECUTE 'REVOKE ALL ON FUNCTION control.'||name||'(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,bytea,bytea,text) FROM PUBLIC';
  EXECUTE 'GRANT EXECUTE ON FUNCTION control.'||name||'(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,bytea,bytea,text) TO signal_identity';
 END LOOP;
 FOREACH name IN ARRAY ARRAY['complete_authenticated_fixture_model_proposal',
 'complete_authenticated_verified_homepage_model_proposal'] LOOP
  SELECT pg_get_functiondef(p.oid) INTO definition FROM pg_proc p JOIN pg_namespace n
  ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname=name;
  definition:=replace(definition,'''^gpt-6-luna[A-Za-z0-9._:-]{0,96}$''',
  '''^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$''');
  definition:=replace(definition,'SELECT run.source_finding_id, run.source_evidence_id, run.input_sha256,',
  'SELECT call.model_requested, run.source_finding_id, run.source_evidence_id, run.input_sha256,');
  definition:=replace(definition,'v_expected_manifest := jsonb_build_object(',
  'IF NOT (p_model_reported=v_run.model_requested OR left(p_model_reported,length(v_run.model_requested)+1)=v_run.model_requested||''-'') THEN RAISE EXCEPTION ''model_release_mismatch'' USING ERRCODE=''22023''; END IF; v_expected_manifest := jsonb_build_object(');
  definition:=replace(definition,'''model_requested'', ''gpt-6-luna''',
  '''model_requested'', v_run.model_requested');
  EXECUTE definition;
 END LOOP;
END; $$;

CREATE FUNCTION control.model_budget_read(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; cap bigint; used bigint; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 SELECT c.cap_micros INTO cap FROM app.model_budget_caps c WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site
 ORDER BY c.recorded_at DESC,c.id DESC LIMIT 1; cap:=coalesce(cap,25000000);
 SELECT coalesce(sum(coalesce(r.cost_micros,c.reserved_micros)),0) INTO used
 FROM app.model_budget_calls c LEFT JOIN app.model_budget_receipts r
 ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.call_id=c.id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.month=date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date;
 RETURN jsonb_build_object('tenant_id',v.tenant_id,'site_id',p_site,'cap_micros',cap,'used_micros',used,'warning',used*5>=cap*4,
 'state',CASE WHEN used>=cap THEN 'unavailable' ELSE 'available' END,
 'month',date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date);
END; $$;

CREATE FUNCTION control.model_budget_set_cap(p_hash bytea,p_generation text,p_site uuid,p_cap bigint)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_cap IS NULL OR p_cap NOT BETWEEN 0 AND 1000000000 THEN RETURN 'invalid'; END IF;
 -- Compatible with the authorizer's existing KEY SHARE lock; still serializes settings.
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 INSERT INTO app.model_budget_caps VALUES(v.tenant_id,p_site,gen_random_uuid(),p_cap,v.user_id,transaction_timestamp());
 RETURN 'updated';
END; $$;

CREATE FUNCTION control.model_budget_reserve(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_digest bytea,p_role text,p_release jsonb,p_amount bigint)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; account jsonb; prior app.model_budget_calls%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_id IS NULL OR p_digest IS NULL OR octet_length(p_digest)<>32 OR p_amount IS NULL
 OR p_amount NOT BETWEEN 1 AND 1000000000 OR p_role IS NULL OR p_release IS NULL THEN RETURN 'invalid'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO prior FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
 IF FOUND THEN
  IF prior.request_sha256 IS DISTINCT FROM p_digest OR prior.role IS DISTINCT FROM p_role
  OR prior.model_release IS DISTINCT FROM p_release OR prior.reserved_micros IS DISTINCT FROM p_amount
  OR prior.generation IS DISTINCT FROM p_generation THEN RETURN 'conflict'; END IF;
  RETURN 'replayed';
 END IF;
 account:=control.model_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint+p_amount>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_calls(tenant_id,site_id,id,generation,request_sha256,role,model_release,reserved_micros)
 VALUES(v.tenant_id,p_site,p_id,p_generation,p_digest,p_role,p_release,p_amount);
 RETURN 'reserved';
END; $$;

CREATE FUNCTION control.model_budget_dispatch(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_digest bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; c app.model_budget_calls%%ROWTYPE; account jsonb; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO c FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
 IF NOT FOUND OR c.generation IS DISTINCT FROM p_generation OR c.request_sha256 IS DISTINCT FROM p_digest
 OR c.month<>date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date
 OR EXISTS(SELECT 1 FROM app.model_budget_dispatches WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id)
 THEN RETURN 'unavailable'; END IF;
 account:=control.model_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_dispatches VALUES(v.tenant_id,p_site,p_id,transaction_timestamp());
 RETURN 'admitted';
END; $$;

CREATE FUNCTION control.model_budget_finish(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_cost bigint,p_digest bytea,p_receipt jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; prior app.model_budget_receipts%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 IF NOT EXISTS(SELECT 1 FROM app.model_budget_calls c JOIN app.model_budget_dispatches d
 ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.call_id=c.id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_id AND c.generation=p_generation)
 THEN RETURN 'unavailable'; END IF;
 SELECT * INTO prior FROM app.model_budget_receipts WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id;
 IF FOUND THEN RETURN CASE WHEN prior.cost_micros=p_cost AND prior.response_sha256=p_digest
 AND prior.receipt=p_receipt THEN 'replayed' ELSE 'conflict' END; END IF;
 INSERT INTO app.model_budget_receipts VALUES(v.tenant_id,p_site,p_id,p_cost,p_digest,p_receipt,transaction_timestamp());
 RETURN 'completed';
END; $$;

REVOKE ALL ON FUNCTION control.model_budget_read(bytea,text,uuid),control.model_budget_set_cap(bytea,text,uuid,bigint),control.model_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint),control.model_budget_dispatch(bytea,text,uuid,uuid,bytea),control.model_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.model_budget_read(bytea,text,uuid),control.model_budget_set_cap(bytea,text,uuid,bigint),control.model_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint),control.model_budget_dispatch(bytea,text,uuid,uuid,bytea),control.model_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) TO signal_api,signal_identity;

CREATE FUNCTION control.content_writer_voice_rank(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; binding record; generation app.gsc_import_generations%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 SELECT binding_id,event_kind INTO binding FROM app.gsc_binding_events
 WHERE tenant_id=v.tenant_id AND site_id=p_site ORDER BY recorded_at DESC,id DESC LIMIT 1;
 IF binding.event_kind IS DISTINCT FROM 'bound' THEN RETURN '[]'::jsonb; END IF;
 SELECT * INTO generation FROM app.gsc_import_generations
 WHERE tenant_id=v.tenant_id AND site_id=p_site AND binding_id=binding.binding_id
 AND dimensions='["page"]'::jsonb AND search_type='web' AND data_state='final'
 ORDER BY imported_at DESC,id DESC LIMIT 1;
 IF NOT FOUND THEN RETURN '[]'::jsonb; END IF;
 RETURN coalesce((SELECT jsonb_agg(source_id ORDER BY clicks DESC,source_id) FROM (
 SELECT x->>'source_id' AS source_id, sum((r->>'clicks')::numeric) AS clicks
 FROM jsonb_array_elements(control.content_writer_inventory(p_hash,p_generation,p_site)) x
 JOIN jsonb_array_elements(generation.rows) r ON r->'keys'->>0=x->>'url'
 GROUP BY x->>'source_id' ORDER BY clicks DESC,source_id LIMIT 3) ranked),'[]'::jsonb);
END; $$;
REVOKE ALL ON FUNCTION control.content_writer_voice_rank(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.content_writer_voice_rank(bytea,text,uuid) TO signal_api;

-- Historical sealed evidence stays unchanged. Only new writer intents use v2.
ALTER TABLE app.content_draft_intents DROP CONSTRAINT content_draft_intents_writer_version_check;
ALTER TABLE app.content_draft_intents ADD CONSTRAINT content_draft_intents_writer_version_check
 CHECK(writer_version IN ('content-writer-v1','content-writer-0136-v2'));
DO $$ DECLARE definition text; BEGIN
 SELECT pg_get_functiondef('control.content_writer_begin_draft(bytea,text,uuid,uuid,uuid,bytea,jsonb,jsonb)'::regprocedure) INTO definition;
 definition:=replace(definition,'content-writer-v1','content-writer-0136-v2');
 -- Serialize the existing weekly-cap admission with other site reservations.
 definition:=replace(definition,'SELECT * INTO d FROM app.content_draft_intents',
 'PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE; SELECT * INTO d FROM app.content_draft_intents');
 EXECUTE definition;
END; $$;

ALTER FUNCTION control.content_writer_read(bytea,text,uuid) RENAME TO content_writer_read_0082;
REVOKE ALL ON FUNCTION control.content_writer_read_0082(bytea,text,uuid) FROM signal_api;
CREATE FUNCTION control.content_writer_read(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; BEGIN
 result:=control.content_writer_read_0082(p_hash,p_generation,p_site);
 IF result IS NULL THEN RETURN NULL; END IF;
 RETURN result || jsonb_build_object('monthly_model_budget',control.model_budget_read(p_hash,p_generation,p_site));
END; $$;
REVOKE ALL ON FUNCTION control.content_writer_read(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.content_writer_read(bytea,text,uuid) TO signal_api;

-- Admit new fallback aliases without rewriting old decision evidence.
DO $$ DECLARE c record; definition text; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='app.decision_records'::regclass
 AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%model_requested%%' LOOP
  EXECUTE format('ALTER TABLE app.decision_records DROP CONSTRAINT %%I',c.conname);
 END LOOP;
 ALTER TABLE app.decision_records ADD CONSTRAINT decision_models_0136 CHECK(
 (provider='typesafe' AND model_requested='jev-latest' AND model_reported ~ '^jev-[0-9]+[.][0-9]+[.][0-9]+')
 OR (provider='openai_fallback' AND model_requested ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
 AND (model_reported=model_requested OR left(model_reported,length(model_requested)+1)=model_requested||'-'))
 OR (provider='deterministic_fallback' AND model_requested='deterministic-v1' AND model_reported='deterministic-v1'));
 SELECT pg_get_functiondef('control.record_decision_recommendation(uuid,uuid,uuid,text,text,text,text,bytea,bytea,jsonb,jsonb,numeric,numeric,text,text,boolean,text)'::regprocedure) INTO definition;
 definition:=replace(definition,'p_model_requested <> ''gpt-5.6-luna''',
 'p_model_requested !~ ''^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$''');
 definition:=replace(definition,'p_model_reported !~ ''^gpt-5[.]6-luna''',
 'NOT (p_model_reported=p_model_requested OR left(p_model_reported,length(p_model_requested)+1)=p_model_requested||''-'')');
 EXECUTE definition;
END; $$;
