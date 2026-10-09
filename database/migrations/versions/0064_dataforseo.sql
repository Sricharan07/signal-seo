CREATE TABLE app.dataforseo_settings (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, credential_generation uuid,
 enabled boolean NOT NULL DEFAULT false, cap_micros bigint NOT NULL DEFAULT 5000000 CHECK(cap_micros BETWEEN 0 AND 1000000000),
 PRIMARY KEY(tenant_id,site_id), FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 CHECK(NOT enabled OR credential_generation IS NOT NULL)
);
CREATE TABLE app.dataforseo_settings_events (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, actor_id uuid NOT NULL,
 action text NOT NULL CHECK(action IN ('prepare','activate','remove','cap')),
 credential_generation uuid, cap_micros bigint NOT NULL CHECK(cap_micros BETWEEN 0 AND 1000000000),
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id), FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
CREATE TABLE app.dataforseo_calls (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, credential_generation uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('serp','volume','backlinks')), endpoint text NOT NULL,
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 query jsonb NOT NULL CHECK(jsonb_typeof(query)='object' AND octet_length(query::text)<=4096),
 reserved_micros bigint NOT NULL CHECK(reserved_micros BETWEEN 1 AND 100000000),
 month date NOT NULL DEFAULT date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date,
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id), FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 CHECK(endpoint = CASE kind WHEN 'serp' THEN 'https://api.dataforseo.com/v3/serp/google/organic/live/advanced'
 WHEN 'volume' THEN 'https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live'
 ELSE 'https://api.dataforseo.com/v3/backlinks/summary/live' END)
);
CREATE TABLE app.dataforseo_receipts (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, call_id uuid NOT NULL,
 status text NOT NULL CHECK(status IN ('complete','unavailable','unknown','rejected')),
 failure_code text CHECK(failure_code IN ('DATAFORSEO_UNAVAILABLE','DATAFORSEO_CREDENTIAL_REJECTED','DATAFORSEO_RESPONSE_REJECTED','DATAFORSEO_SECRET_UNAVAILABLE')),
 reported_cost_micros bigint CHECK(reported_cost_micros BETWEEN 0 AND 100000000),
 response_sha256 bytea CHECK(octet_length(response_sha256)=32), task_id text CHECK(task_id ~ '^[A-Za-z0-9_-]{1,128}$'),
 result jsonb CHECK(jsonb_typeof(result)='object' AND octet_length(result::text)<=32768),
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,call_id), FOREIGN KEY(tenant_id,site_id,call_id) REFERENCES app.dataforseo_calls(tenant_id,site_id,id),
 CHECK((status='complete' AND result IS NOT NULL AND task_id IS NOT NULL AND response_sha256 IS NOT NULL AND reported_cost_micros IS NOT NULL AND failure_code IS NULL)
 OR (status<>'complete' AND result IS NULL AND task_id IS NULL AND failure_code IS NOT NULL))
);
CREATE FUNCTION control.valid_dataforseo_result(p_result jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE kind text; item jsonb; ranks integer[]:=ARRAY[]::integer[]; n integer; key text; BEGIN
 IF jsonb_typeof(p_result) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
 kind:=p_result->>'kind';
 IF kind IS NULL OR kind NOT IN ('serp','volume','backlinks') OR jsonb_typeof(p_result->'subject') IS DISTINCT FROM 'string'
 OR length(p_result->>'subject') NOT BETWEEN 1 AND 253 THEN RETURN false; END IF;
 IF kind='backlinks' THEN
  IF p_result - ARRAY['kind','subject','location_code','language_code','backlinks','referring_domains','rank'] <> '{}'::jsonb
  OR p_result->'location_code' IS DISTINCT FROM 'null'::jsonb OR p_result->'language_code' IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
  FOREACH key IN ARRAY ARRAY['backlinks','referring_domains','rank'] LOOP
   IF jsonb_typeof(p_result->key) IS DISTINCT FROM 'number' OR (p_result->>key) !~ '^[0-9]{1,13}$'
   OR (p_result->>key)::bigint>1000000000000 THEN RETURN false; END IF;
  END LOOP;
  RETURN (p_result->>'rank')::integer<=1000;
 END IF;
 IF jsonb_typeof(p_result->'location_code') IS DISTINCT FROM 'number'
 OR (p_result->>'location_code') !~ '^[1-9][0-9]{0,6}$' OR (p_result->>'location_code')::integer>1000000
 OR jsonb_typeof(p_result->'language_code') IS DISTINCT FROM 'string' OR (p_result->>'language_code') !~ '^[a-z]{2}(-[A-Z]{2})?$' THEN RETURN false; END IF;
 IF kind='volume' THEN
  IF p_result - ARRAY['kind','subject','location_code','language_code','search_volume'] <> '{}'::jsonb THEN RETURN false; END IF;
  RETURN p_result->'search_volume' = 'null'::jsonb OR (jsonb_typeof(p_result->'search_volume')='number'
   AND (p_result->>'search_volume') ~ '^[0-9]{1,13}$' AND (p_result->>'search_volume')::bigint<=1000000000000);
 END IF;
 IF p_result - ARRAY['kind','subject','location_code','language_code','competitors'] <> '{}'::jsonb
 OR jsonb_typeof(p_result->'competitors') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(p_result->'competitors')>10 THEN RETURN false; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_result->'competitors') LOOP
  IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR item - ARRAY['rank','url','domain'] <> '{}'::jsonb
  OR jsonb_typeof(item->'rank') IS DISTINCT FROM 'number' OR (item->>'rank') !~ '^[1-9][0-9]?$'
  OR jsonb_typeof(item->'domain') IS DISTINCT FROM 'string' OR (item->>'domain') !~ '^[a-z0-9.-]{1,253}$'
  OR jsonb_typeof(item->'url') IS DISTINCT FROM 'string' OR length(item->>'url')>2048
  OR (item->>'url') !~ '^https?://' OR split_part(item->>'url','/',3) IS DISTINCT FROM item->>'domain' THEN RETURN false; END IF;
  n:=(item->>'rank')::integer; IF n>10 OR n=ANY(ranks) THEN RETURN false; END IF;
  ranks:=array_append(ranks,n);
 END LOOP;
 RETURN true;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION control.valid_dataforseo_result(jsonb) FROM PUBLIC;
ALTER TABLE app.dataforseo_receipts ADD CONSTRAINT dataforseo_result_valid
CHECK(result IS NULL OR control.valid_dataforseo_result(result) IS TRUE);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['dataforseo_settings','dataforseo_settings_events','dataforseo_calls','dataforseo_receipts'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY scope ON app.%%I USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,signal_scheduler,signal_workflow,signal_crawl_ingest,signal_crawl_admission',t);
  IF t <> 'dataforseo_settings' THEN
   EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
  END IF;
 END LOOP;
END; $$;

CREATE FUNCTION control.dataforseo_owner(p_hash bytea,p_generation text,p_site uuid)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome <> 'authorized' OR a.role_key <> 'owner' THEN RETURN NULL; END IF;
 PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
 PERFORM set_config('signal.site_id',p_site::text,true);
 RETURN a.tenant_id;
END; $$;

CREATE FUNCTION control.configure_dataforseo(p_hash bytea,p_generation text,p_site uuid,p_action text,p_credential uuid,p_cap bigint,p_event uuid)
RETURNS TABLE(tenant_id uuid,old_credential uuid,outcome text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t uuid; old app.dataforseo_settings%%ROWTYPE; a record; BEGIN
 t:=control.dataforseo_owner(p_hash,p_generation,p_site);
 IF t IS NULL THEN RETURN QUERY SELECT NULL::uuid,NULL::uuid,'denied'; RETURN; END IF;
 IF p_action IS NULL OR p_action NOT IN ('prepare','activate','remove','cap') OR p_event IS NULL
 OR p_cap IS NULL OR p_cap NOT BETWEEN 0 AND 1000000000 OR (p_action IN ('prepare','activate') AND p_credential IS NULL) THEN
  RAISE EXCEPTION 'invalid_dataforseo_settings' USING ERRCODE='22023'; END IF;
 PERFORM 1 FROM app.sites WHERE app.sites.tenant_id=t AND id=p_site FOR UPDATE;
 INSERT INTO app.dataforseo_settings(tenant_id,site_id) VALUES(t,p_site) ON CONFLICT DO NOTHING;
 SELECT * INTO old FROM app.dataforseo_settings s WHERE s.tenant_id=t AND s.site_id=p_site FOR UPDATE;
 IF p_action='activate' AND old.credential_generation IS DISTINCT FROM p_credential THEN
  RETURN QUERY SELECT t,old.credential_generation,'conflict'; RETURN; END IF;
 IF p_action IN ('prepare','remove') THEN
  UPDATE app.dataforseo_settings s SET enabled=false,credential_generation=CASE WHEN p_action='prepare' THEN p_credential END
  WHERE s.tenant_id=t AND s.site_id=p_site;
 ELSIF p_action='activate' THEN
  UPDATE app.dataforseo_settings s SET enabled=true WHERE s.tenant_id=t AND s.site_id=p_site;
 ELSE
  UPDATE app.dataforseo_settings s SET cap_micros=p_cap WHERE s.tenant_id=t AND s.site_id=p_site;
 END IF;
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 INSERT INTO app.dataforseo_settings_events(tenant_id,site_id,id,actor_id,action,credential_generation,cap_micros)
 SELECT t,p_site,p_event,a.user_id,p_action,s.credential_generation,s.cap_micros FROM app.dataforseo_settings s WHERE s.tenant_id=t AND s.site_id=p_site;
 RETURN QUERY SELECT t,old.credential_generation,'configured';
END; $$;

CREATE FUNCTION control.dataforseo_usage(p_tenant uuid,p_site uuid)
RETURNS bigint LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT coalesce(sum(CASE WHEN r.status='complete' THEN r.reported_cost_micros ELSE greatest(c.reserved_micros,coalesce(r.reported_cost_micros,0)) END),0)::bigint
 FROM app.dataforseo_calls c LEFT JOIN app.dataforseo_receipts r ON (r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
 WHERE c.tenant_id=p_tenant AND c.site_id=p_site AND c.month=date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date;
$$;

CREATE FUNCTION control.read_dataforseo(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t uuid; s app.dataforseo_settings%%ROWTYPE; usage bigint; BEGIN
 t:=control.dataforseo_owner(p_hash,p_generation,p_site); IF t IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO s FROM app.dataforseo_settings WHERE tenant_id=t AND site_id=p_site;
 usage:=control.dataforseo_usage(t,p_site);
 RETURN jsonb_build_object('tenant_id',t,'credential_generation',s.credential_generation,
 'availability',CASE WHEN NOT coalesce(s.enabled,false) THEN 'unconfigured' WHEN usage>=coalesce(s.cap_micros,5000000) THEN 'cap_exhausted' ELSE 'configured' END,
 'cap_micros',coalesce(s.cap_micros,5000000),'usage_micros',usage,'month',date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date,
 'records',coalesce((SELECT jsonb_agg(x) FROM (SELECT c.id AS evidence_id,c.kind,c.endpoint,encode(c.request_sha256,'hex') AS request_sha256,c.query,c.reserved_micros,
 coalesce(r.status,'unknown') AS status,r.failure_code,r.reported_cost_micros,encode(r.response_sha256,'hex') AS response_sha256,r.task_id,r.result,c.recorded_at
 FROM app.dataforseo_calls c LEFT JOIN app.dataforseo_receipts r ON(r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
 WHERE c.tenant_id=t AND c.site_id=p_site ORDER BY c.recorded_at DESC,c.id DESC LIMIT 100) x),'[]'::jsonb));
END; $$;

CREATE FUNCTION control.reserve_dataforseo(p_tenant uuid,p_site uuid,p_id uuid,p_credential uuid,p_kind text,p_endpoint text,p_digest bytea,p_query jsonb,p_estimate bigint)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s app.dataforseo_settings%%ROWTYPE; c app.dataforseo_calls%%ROWTYPE; v record; minimum bigint; BEGIN
 IF p_tenant IS NULL OR p_site IS NULL OR p_id IS NULL OR p_credential IS NULL OR p_kind IS NULL
 OR octet_length(p_digest) IS DISTINCT FROM 32 OR jsonb_typeof(p_query) IS DISTINCT FROM 'object' OR octet_length(p_query::text)>4096 THEN
 RAISE EXCEPTION 'invalid_dataforseo_reservation' USING ERRCODE='22023'; END IF;
 minimum:=CASE p_kind WHEN 'serp' THEN 2000 WHEN 'volume' THEN 90000 WHEN 'backlinks' THEN 24036 END;
 IF minimum IS NULL OR p_estimate IS NULL OR p_estimate NOT BETWEEN minimum AND 100000000 THEN
 RAISE EXCEPTION 'invalid_dataforseo_estimate' USING ERRCODE='22023'; END IF;
 IF p_query - ARRAY['kind','subject','location_code','language_code'] <> '{}'::jsonb
 OR p_query->>'kind' IS DISTINCT FROM p_kind OR jsonb_typeof(p_query->'subject') IS DISTINCT FROM 'string'
 OR length(p_query->>'subject') NOT BETWEEN 1 AND 253 THEN
 RAISE EXCEPTION 'invalid_dataforseo_query' USING ERRCODE='22023'; END IF;
 PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
 PERFORM 1 FROM app.sites WHERE tenant_id=p_tenant AND id=p_site AND state='active' FOR UPDATE;
 IF NOT FOUND THEN RETURN 'site_unavailable'; END IF;
 SELECT * INTO c FROM app.dataforseo_calls WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id;
 IF FOUND THEN
  IF (c.credential_generation,c.kind,c.endpoint,c.request_sha256,c.query,c.reserved_micros) IS DISTINCT FROM (p_credential,p_kind,p_endpoint,p_digest,p_query,p_estimate) THEN RETURN 'conflict'; END IF;
  RETURN 'replay';
 END IF;
 SELECT * INTO v FROM control.verified_site_origin(p_tenant,p_site); IF v.outcome<>'verified' THEN RETURN 'origin_unavailable'; END IF;
 SELECT * INTO s FROM app.dataforseo_settings WHERE tenant_id=p_tenant AND site_id=p_site FOR UPDATE;
 IF NOT FOUND OR NOT s.enabled OR s.credential_generation IS DISTINCT FROM p_credential THEN RETURN 'unconfigured'; END IF;
 IF control.dataforseo_usage(p_tenant,p_site)+p_estimate>s.cap_micros THEN RETURN 'cap_exhausted'; END IF;
 INSERT INTO app.dataforseo_calls(tenant_id,site_id,id,credential_generation,kind,endpoint,request_sha256,query,reserved_micros)
 VALUES(p_tenant,p_site,p_id,p_credential,p_kind,p_endpoint,p_digest,p_query,p_estimate);
 RETURN 'reserved';
END; $$;

ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check CHECK(egress_profile IN (
 'legacy_unqualified','crawl_page','crawl_robots','browser_read','github_rest','github_repository_write',
 'google_oauth_token','google_oauth_revoke','gsc_api','bing_oauth_token','bing_api','jev','model_json','openai_model',
 'openai_assistant','perplexity_assistant','gemini_assistant','slack_bot','slack_oauth','dataforseo'));

CREATE FUNCTION control.bind_dataforseo_egress_profile(p_tenant uuid,p_site uuid,p_id uuid,p_digest bytea,p_credential uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c app.dataforseo_calls%%ROWTYPE; o app.egress_operations%%ROWTYPE; BEGIN
 PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
 PERFORM 1 FROM app.sites WHERE tenant_id=p_tenant AND id=p_site AND state='active' FOR UPDATE;
 SELECT * INTO c FROM app.dataforseo_calls WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id;
 IF NOT FOUND OR c.credential_generation IS DISTINCT FROM p_credential OR EXISTS(SELECT 1 FROM app.dataforseo_receipts WHERE tenant_id=p_tenant AND site_id=p_site AND call_id=p_id)
 OR c.month<>date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date
 OR NOT EXISTS(SELECT 1 FROM app.dataforseo_settings WHERE tenant_id=p_tenant AND site_id=p_site AND enabled AND credential_generation=p_credential) THEN
 RAISE EXCEPTION 'dataforseo_reservation_unavailable' USING ERRCODE='22023'; END IF;
 IF control.dataforseo_usage(p_tenant,p_site)>(SELECT cap_micros FROM app.dataforseo_settings WHERE tenant_id=p_tenant AND site_id=p_site) THEN
 RAISE EXCEPTION 'dataforseo_cap_exhausted' USING ERRCODE='22023'; END IF;
 SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR o.state<>'dispatched' OR o.purpose<>'connector' OR o.method<>'POST' OR NOT o.credentialed
 OR o.request_url IS DISTINCT FROM c.endpoint OR o.request_body_sha256 IS DISTINCT FROM c.request_sha256
 OR o.request_sha256 IS DISTINCT FROM p_digest OR o.max_response_bytes>131072
 OR o.egress_profile NOT IN ('legacy_unqualified','dataforseo') THEN
 RAISE EXCEPTION 'invalid_dataforseo_egress' USING ERRCODE='22023'; END IF;
 IF o.egress_profile='legacy_unqualified' THEN UPDATE app.egress_operations SET egress_profile='dataforseo' WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id; END IF;
 RETURN 'bound';
END; $$;

CREATE FUNCTION control.record_dataforseo(p_tenant uuid,p_site uuid,p_id uuid,p_status text,p_code text,p_cost bigint,p_response bytea,p_task text,p_result jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c app.dataforseo_calls%%ROWTYPE; r app.dataforseo_receipts%%ROWTYPE; BEGIN
 PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
 SELECT * INTO c FROM app.dataforseo_calls WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 SELECT * INTO r FROM app.dataforseo_receipts WHERE tenant_id=p_tenant AND site_id=p_site AND call_id=p_id;
 IF FOUND THEN
 IF (r.status,r.failure_code,r.reported_cost_micros,r.response_sha256,r.task_id,r.result) IS DISTINCT FROM (p_status,p_code,p_cost,p_response,p_task,p_result) THEN RETURN 'conflict'; END IF;
 RETURN 'replay'; END IF;
 IF p_status='complete' AND (NOT EXISTS(SELECT 1 FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id AND egress_profile='dataforseo' AND state='observed' AND http_status=200 AND response_sha256=p_response AND request_body_sha256=c.request_sha256)
 OR p_result->>'kind' IS DISTINCT FROM c.kind OR p_result->>'subject' IS DISTINCT FROM c.query->>'subject'
 OR p_result->'location_code' IS DISTINCT FROM c.query->'location_code' OR p_result->'language_code' IS DISTINCT FROM c.query->'language_code') THEN RETURN 'evidence_unavailable'; END IF;
 INSERT INTO app.dataforseo_receipts(tenant_id,site_id,call_id,status,failure_code,reported_cost_micros,response_sha256,task_id,result)
 VALUES(p_tenant,p_site,p_id,p_status,p_code,p_cost,p_response,p_task,p_result);
 RETURN 'recorded';
END; $$;

CREATE FUNCTION control.dataforseo_call(p_tenant uuid,p_site uuid,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
 RETURN (SELECT jsonb_build_object('evidence_id',c.id,'status',coalesce(r.status,'unknown'),'result',r.result,'failure_code',r.failure_code,'reported_cost_micros',r.reported_cost_micros)
 FROM app.dataforseo_calls c LEFT JOIN app.dataforseo_receipts r ON(r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
 WHERE c.tenant_id=p_tenant AND c.site_id=p_site AND c.id=p_id);
END; $$;

REVOKE ALL ON FUNCTION control.dataforseo_owner(bytea,text,uuid),control.configure_dataforseo(bytea,text,uuid,text,uuid,bigint,uuid),
 control.read_dataforseo(bytea,text,uuid),control.dataforseo_usage(uuid,uuid),
 control.reserve_dataforseo(uuid,uuid,uuid,uuid,text,text,bytea,jsonb,bigint),control.bind_dataforseo_egress_profile(uuid,uuid,uuid,bytea,uuid),
 control.record_dataforseo(uuid,uuid,uuid,text,text,bigint,bytea,text,jsonb),control.dataforseo_call(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.dataforseo_owner(bytea,text,uuid),control.configure_dataforseo(bytea,text,uuid,text,uuid,bigint,uuid),control.read_dataforseo(bytea,text,uuid) TO signal_api;
GRANT EXECUTE ON FUNCTION control.reserve_dataforseo(uuid,uuid,uuid,uuid,text,text,bytea,jsonb,bigint),control.record_dataforseo(uuid,uuid,uuid,text,text,bigint,bytea,text,jsonb),control.dataforseo_call(uuid,uuid,uuid) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.bind_dataforseo_egress_profile(uuid,uuid,uuid,bytea,uuid) TO signal_crawl_admission;
