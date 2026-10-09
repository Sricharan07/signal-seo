-- Paid calls retain both ledgers, including process death before transmission.
CREATE TABLE app.strategy_provider_requests (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, call_id uuid NOT NULL,
 authority_hash bytea NOT NULL CHECK(octet_length(authority_hash)=32),
 recovery_generation text NOT NULL, grant_id uuid NOT NULL, recipe_release_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,site_id,call_id),
 FOREIGN KEY(tenant_id,site_id,call_id) REFERENCES app.dataforseo_calls(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,grant_id) REFERENCES app.standing_authorizations(tenant_id,site_id,id)
);
ALTER TABLE app.strategy_provider_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.strategy_provider_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY provider_scope ON app.strategy_provider_requests
 USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
 WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE TRIGGER provider_immutable BEFORE UPDATE OR DELETE ON app.strategy_provider_requests
 FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
REVOKE ALL ON app.strategy_provider_requests FROM PUBLIC,signal_api,signal_identity,signal_workflow,
 signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest;

CREATE FUNCTION control.strategy_provider_context(p_hash bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid,user_id uuid,grant_id uuid,recipe_release_id uuid,origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; i record; g record; r uuid; v record; e record;
BEGIN
 IF EXISTS(SELECT 1 FROM control.weekly_skill_directory WHERE handle_hash=p_hash) THEN
  PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['strategy_rebuild']);
  SELECT * INTO a FROM control.weekly_skill_context(p_hash,p_generation,p_site);
  SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
  SELECT x.* INTO g FROM app.standing_authorizations x WHERE x.id=i.grant_id AND x.tenant_id=a.tenant_id AND x.site_id=p_site;
  r:=i.recipe_release_id;
 ELSE
  SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
  IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
   OR a.authentication_level IS DISTINCT FROM 'mfa' OR NOT EXISTS(SELECT 1 FROM app.sessions WHERE session_token_hash=p_hash AND auth_time BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp())
   THEN RETURN; END IF;
  PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
  PERFORM set_config('signal.site_id',p_site::text,true);
  SELECT x.* INTO g FROM app.standing_authorizations x WHERE x.tenant_id=a.tenant_id AND x.site_id=p_site
   AND x.owner_user_id=a.user_id AND x.recovery_generation=p_generation
   ORDER BY x.granted_at DESC,x.id DESC LIMIT 1;
  SELECT x.id INTO r FROM control.recipe_releases x WHERE x.id=ANY(g.recipe_release_ids)
   AND convert_from(x.canonical_body,'UTF8')::jsonb->>'approval_class'='A0'
   ORDER BY x.id LIMIT 1;
 END IF;
 SELECT * INTO v FROM control.verified_site_origin(a.tenant_id,p_site);
 SELECT * INTO e FROM control.standing_grant_eligibility(a.tenant_id,p_site,g.id,p_generation,r,'research_audit','/strategy');
 IF v.outcome IS DISTINCT FROM 'verified' OR e.eligible IS DISTINCT FROM true THEN RETURN; END IF;
 RETURN QUERY SELECT a.tenant_id,a.user_id,g.id,r,v.origin;
END $$;
REVOKE ALL ON FUNCTION control.strategy_provider_context(bytea,text,uuid) FROM PUBLIC;
CREATE FUNCTION control.strategy_provider_egress_context(p_hash bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT c.tenant_id FROM control.strategy_provider_context(p_hash,p_generation,p_site) c
 WHERE session_user='signal_workflow'
$$;
REVOKE ALL ON FUNCTION control.strategy_provider_egress_context(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.strategy_provider_egress_context(bytea,text,uuid) TO signal_workflow;

CREATE FUNCTION control.strategy_provider_settings(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; s record;
BEGIN
 SELECT * INTO a FROM control.strategy_provider_context(p_hash,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO s FROM app.dataforseo_settings WHERE tenant_id=a.tenant_id AND site_id=p_site AND enabled;
 IF s.credential_generation IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('tenant_id',a.tenant_id,'credential_generation',s.credential_generation,
  'month',date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date);
END $$;
REVOKE ALL ON FUNCTION control.strategy_provider_settings(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.strategy_provider_settings(bytea,text,uuid) TO signal_workflow;

CREATE FUNCTION control.reserve_strategy_dataforseo(p_hash bytea,p_generation text,p_bound_site uuid,
 p_tenant uuid,p_site uuid,p_id uuid,p_credential uuid,p_kind text,p_endpoint text,p_digest bytea,p_query jsonb,p_estimate bigint)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; result text; budget text;
BEGIN
 IF session_user<>'signal_workflow' OR p_bound_site IS DISTINCT FROM p_site THEN RETURN 'authority_unavailable'; END IF;
 -- Serialize before snapshot authority takes site SHARE locks, avoiding lock upgrades.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,155));
 SELECT * INTO a FROM control.strategy_provider_context(p_hash,p_generation,p_site);
 IF a.tenant_id IS DISTINCT FROM p_tenant THEN RETURN 'authority_unavailable'; END IF;
 -- One serialized reservation decision across both caps. A denial rolls back both.
 PERFORM 1 FROM app.sites WHERE tenant_id=p_tenant AND id=p_site FOR UPDATE;
 BEGIN
  result:=control.reserve_dataforseo(p_tenant,p_site,p_id,p_credential,p_kind,p_endpoint,p_digest,p_query,p_estimate);
  IF result<>'reserved' THEN RETURN result; END IF;
  IF EXISTS(SELECT 1 FROM app.dataforseo_calls c LEFT JOIN app.dataforseo_receipts r
    ON (r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
    WHERE c.tenant_id=p_tenant AND c.site_id=p_site AND c.id<>p_id AND c.query=p_query
    AND coalesce(r.status,'unknown')<>'complete') THEN
   RAISE EXCEPTION 'held' USING ERRCODE='P0155';
  END IF;
  budget:=control.reserve_standing_budget(p_tenant,p_site,a.grant_id,p_generation,a.recipe_release_id,
   'research_audit','/strategy',p_id,p_digest,(p_estimate+9999)/10000);
  IF budget<>'reserved' THEN RAISE EXCEPTION 'standing_cap' USING ERRCODE='P0156'; END IF;
  INSERT INTO app.strategy_provider_requests VALUES(p_tenant,p_site,p_id,p_hash,p_generation,a.grant_id,a.recipe_release_id);
 EXCEPTION WHEN SQLSTATE 'P0155' THEN RETURN 'outcome_held';
  WHEN SQLSTATE 'P0156' THEN RETURN 'standing_cap_reached';
 END;
 RETURN 'reserved';
END $$;
REVOKE ALL ON FUNCTION control.reserve_strategy_dataforseo(bytea,text,uuid,uuid,uuid,uuid,uuid,text,text,bytea,jsonb,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.reserve_strategy_dataforseo(bytea,text,uuid,uuid,uuid,uuid,uuid,text,text,bytea,jsonb,bigint) TO signal_workflow;
CREATE FUNCTION control.strategy_dataforseo_call(p_hash bytea,p_generation text,p_bound_site uuid,p_tenant uuid,p_site uuid,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
 SELECT * INTO a FROM control.strategy_provider_context(p_hash,p_generation,p_site);
 IF session_user<>'signal_workflow' OR p_bound_site IS DISTINCT FROM p_site OR a.tenant_id IS DISTINCT FROM p_tenant
  OR NOT EXISTS(SELECT 1 FROM app.strategy_provider_requests WHERE tenant_id=p_tenant AND site_id=p_site AND call_id=p_id)
  THEN RAISE EXCEPTION 'provider_read_denied' USING ERRCODE='42501'; END IF;
 RETURN control.dataforseo_call(p_tenant,p_site,p_id);
END $$;
CREATE FUNCTION control.record_strategy_dataforseo(p_hash bytea,p_generation text,p_bound_site uuid,
 p_tenant uuid,p_site uuid,p_id uuid,p_status text,p_failure text,p_cost bigint,p_response bytea,p_task text,p_result jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 -- In-flight receipts must survive a pause/revocation, but cannot cross their intent.
 PERFORM set_config('signal.tenant_id',p_tenant::text,true);
 PERFORM set_config('signal.site_id',p_site::text,true);
 IF session_user<>'signal_workflow' OR p_bound_site IS DISTINCT FROM p_site OR NOT EXISTS(
  SELECT 1 FROM app.strategy_provider_requests WHERE tenant_id=p_tenant AND site_id=p_site AND call_id=p_id
   AND authority_hash=p_hash AND recovery_generation=p_generation)
  THEN RAISE EXCEPTION 'provider_receipt_denied' USING ERRCODE='42501'; END IF;
 RETURN control.record_dataforseo(p_tenant,p_site,p_id,p_status,p_failure,p_cost,p_response,p_task,p_result);
END $$;
REVOKE ALL ON FUNCTION control.strategy_dataforseo_call(bytea,text,uuid,uuid,uuid,uuid),
 control.record_strategy_dataforseo(bytea,text,uuid,uuid,uuid,uuid,text,text,bigint,bytea,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.strategy_dataforseo_call(bytea,text,uuid,uuid,uuid,uuid),
 control.record_strategy_dataforseo(bytea,text,uuid,uuid,uuid,uuid,text,text,bigint,bytea,text,jsonb) TO signal_workflow;

-- New entries, not broader provider profiles: only existing read/token/paid routes.
ALTER FUNCTION control.owner_connector_request_allowed(text,text,text) RENAME TO owner_connector_request_allowed_0144;
CREATE FUNCTION control.owner_connector_request_allowed(p_profile text,p_method text,p_url text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT control.owner_connector_request_allowed_0144(p_profile,p_method,p_url) OR coalesce(CASE p_profile
  WHEN 'bing_oauth_token' THEN p_method='POST' AND p_url='https://www.bing.com/webmasters/oauth/token'
  WHEN 'bing_api' THEN p_method='GET' AND (p_url='https://www.bing.com/webmaster/api.svc/json/GetUserSites' OR p_url ~ '^https://www[.]bing[.]com/webmaster/api[.]svc/json/(GetRankAndTrafficStats|GetPageStats)[?]siteUrl=[A-Za-z0-9%%._~-]+$')
  WHEN 'dataforseo' THEN p_method='POST' AND p_url IN (
   'https://api.dataforseo.com/v3/serp/google/organic/live/advanced',
   'https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live',
   'https://api.dataforseo.com/v3/backlinks/summary/live')
  ELSE false END,false)
$$;
REVOKE ALL ON FUNCTION control.owner_connector_request_allowed(text,text,text),control.owner_connector_request_allowed_0144(text,text,text) FROM PUBLIC;
-- Extend the live constraint, so profiles added by other migrations are preserved.
DO $$ DECLARE definition text; BEGIN
 SELECT pg_get_constraintdef(oid) INTO STRICT definition FROM pg_constraint
 WHERE conrelid='app.owner_connector_egress_operations'::regclass AND conname='owner_connector_egress_operations_profile_check';
 ALTER TABLE app.owner_connector_egress_operations DROP CONSTRAINT owner_connector_egress_operations_profile_check;
 EXECUTE 'ALTER TABLE app.owner_connector_egress_operations ADD CONSTRAINT owner_connector_egress_operations_profile_check CHECK ('
   ||substring(definition FROM 8 FOR length(definition)-8)||' OR profile IN (''bing_oauth_token'',''bing_api'',''dataforseo''))';
END; $$;

CREATE FUNCTION control.assert_strategy_provider_dispatch(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_url text,p_body bytea)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; c record;
BEGIN
 SELECT * INTO a FROM control.strategy_provider_context(p_hash,p_generation,p_site);
 SELECT x.* INTO c FROM app.dataforseo_calls x JOIN app.strategy_provider_requests s
  ON (s.tenant_id,s.site_id,s.call_id)=(x.tenant_id,x.site_id,x.id)
  WHERE x.tenant_id=a.tenant_id AND x.site_id=p_site AND x.id=p_id
   AND s.authority_hash=p_hash AND s.recovery_generation=p_generation
   AND s.grant_id=a.grant_id AND s.recipe_release_id=a.recipe_release_id;
 IF c.id IS NULL OR c.endpoint IS DISTINCT FROM p_url OR c.request_sha256 IS DISTINCT FROM p_body
  OR c.month<>date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date
  OR EXISTS(SELECT 1 FROM app.dataforseo_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND call_id=p_id)
  OR NOT EXISTS(SELECT 1 FROM app.dataforseo_settings WHERE tenant_id=a.tenant_id AND site_id=p_site AND enabled AND credential_generation=c.credential_generation)
  OR control.dataforseo_usage(a.tenant_id,p_site)>(SELECT cap_micros FROM app.dataforseo_settings WHERE tenant_id=a.tenant_id AND site_id=p_site)
  OR EXISTS(SELECT 1 FROM app.autonomy_weekly_usage u JOIN app.standing_authorizations g ON g.tenant_id=u.tenant_id AND g.site_id=u.site_id AND g.id=a.grant_id
   WHERE u.tenant_id=a.tenant_id AND u.site_id=p_site AND u.week_start=date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC')::date
    AND (u.spend_cents>g.weekly_spend_cents OR u.total_count>g.weekly_total_cap))
  THEN RAISE EXCEPTION 'provider_dispatch_denied' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.assert_strategy_provider_dispatch(bytea,text,uuid,uuid,text,bytea) FROM PUBLIC;

-- Migration-time copies keep the same origin buckets, robots and outcome holds.
DO $$ DECLARE d text; args text; f text; BEGIN
 SELECT pg_get_functiondef('control.begin_owner_connector_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer)'::regprocedure) INTO d;
 -- Other migrations may already have extended this list, so extend its stable prefix.
 IF strpos(d,'p_profile IN (''github_rest'',''gsc_api'',''pagespeed''')=0 THEN RAISE EXCEPTION 'owner_egress_contract_changed'; END IF;
 d:=replace(d,'p_profile IN (''github_rest'',''gsc_api'',''pagespeed''','p_profile IN (''github_rest'',''gsc_api'',''pagespeed'',''bing_api''');
 d:=replace(d,'SELECT * INTO v_owner FROM control.resolve_snapshot_authority',
  'IF p_profile=''dataforseo'' AND p_kind=''provider'' THEN PERFORM control.assert_strategy_provider_dispatch(p_session_hash,p_generation,p_site_id,p_operation_id,p_target_url,p_body_sha256); END IF; SELECT * INTO v_owner FROM control.resolve_snapshot_authority');
 EXECUTE d;
 FOREACH f IN ARRAY ARRAY['begin','finish'] LOOP
  SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid) INTO STRICT d,args
   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname='weekly_skill_'||f||'_pagespeed_egress';
  d:=replace(d,'weekly_skill_'||f||'_pagespeed_egress','weekly_skill_'||f||'_dataforseo_egress');
  d:=replace(d,'''pagespeed''','''dataforseo''');
  d:=replace(d,'p_profile IN (''github_rest'',''gsc_api'',''dataforseo'')',
   'p_profile IN (''github_rest'',''gsc_api'')');
  d:=replace(d,'control.weekly_skill_pagespeed_context','control.strategy_provider_context');
  IF f='begin' THEN
   d:=replace(d,'p_robots_operation_id,v_bucket.id,v_now,v_expires,p_session_hash);',
    'p_robots_operation_id,v_bucket.id,v_now,v_expires,CASE WHEN EXISTS(SELECT 1 FROM control.weekly_skill_directory WHERE handle_hash=p_session_hash) THEN p_session_hash END);');
   d:=replace(d,'SELECT g.ends_at INTO v_session_expires FROM app.weekly_skill_intents i JOIN app.standing_authorizations g ON g.tenant_id=i.tenant_id AND g.site_id=i.site_id AND g.id=i.grant_id WHERE i.handle_hash=p_session_hash AND g.ends_at>v_now;',
    'SELECT g.ends_at INTO v_session_expires FROM app.standing_authorizations g WHERE g.tenant_id=v_owner.tenant_id AND g.site_id=p_site_id AND g.id=v_owner.grant_id AND g.ends_at>v_now;');
   d:=replace(d,'IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session_hash AND control.pagespeed_request_url(u->>''url'',u->>''strategy'')=p_target_url AND (p_kind=''robots'' OR u->>''sample_id''=p_operation_id::text)) THEN RAISE EXCEPTION ''skill_resource_denied'' USING ERRCODE=''42501''; END IF;',
    'IF p_kind=''provider'' THEN PERFORM control.assert_strategy_provider_dispatch(p_session_hash,p_generation,p_site_id,p_operation_id,p_target_url,p_body_sha256); END IF;');
   d:=replace(d,'IF p_profile=''dataforseo'' AND control.pagespeed_admission_allowed('||chr(10)||'        v_owner.tenant_id,p_site_id,p_operation_id,p_kind,p_target_url,v_verified.origin) IS NOT TRUE',
    'IF p_profile IS DISTINCT FROM ''dataforseo''');
  ELSE
   d:=replace(d,'v_op.weekly_skill_handle_hash IS DISTINCT FROM p_session_hash',
    '(v_op.weekly_skill_handle_hash IS DISTINCT FROM p_session_hash AND NOT (v_op.weekly_skill_handle_hash IS NULL AND v_op.session_hash=p_session_hash))');
   d:=replace(d,'SELECT tenant_id INTO v_tenant FROM control.weekly_skill_directory WHERE handle_hash=p_session_hash AND site_id=p_site_id AND recovery_generation=p_generation;',
    'SELECT tenant_id INTO v_tenant FROM control.weekly_skill_directory WHERE handle_hash=p_session_hash AND site_id=p_site_id AND recovery_generation=p_generation; IF v_tenant IS NULL THEN PERFORM set_config(''signal.session_hash'',encode(p_session_hash,''hex''),true); SELECT tenant_id INTO v_tenant FROM app.sessions WHERE session_token_hash=p_session_hash; END IF;');
  END IF;
  IF d LIKE '%%control.pagespeed_%%' OR d LIKE '%%control.weekly_skill_pagespeed_%%' THEN RAISE EXCEPTION 'provider_egress_copy_drift'; END IF;
  EXECUTE d;
  EXECUTE format('REVOKE ALL ON FUNCTION control.weekly_skill_%%s_dataforseo_egress(%%s) FROM PUBLIC',f,args);
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.weekly_skill_%%s_dataforseo_egress(%%s) TO signal_workflow',f,args);
 END LOOP;
 -- Receipt provenance may now be the reserved owner/workload connector port.
 SELECT pg_get_functiondef('control.record_dataforseo(uuid,uuid,uuid,text,text,bigint,bytea,text,jsonb)'::regprocedure) INTO d;
 d:=replace(d,'NOT EXISTS(SELECT 1 FROM app.egress_operations',
  'NOT EXISTS(SELECT 1 FROM app.owner_connector_egress_operations o JOIN app.strategy_provider_requests s ON (s.tenant_id,s.site_id,s.call_id)=(o.tenant_id,o.site_id,o.id) WHERE o.tenant_id=p_tenant AND o.site_id=p_site AND o.id=p_id AND o.profile=''dataforseo'' AND o.kind=''provider'' AND o.state=''observed'' AND o.request_body_sha256=c.request_sha256 AND o.response_evidence->>''http_status''=''200'' AND o.response_evidence->>''body_sha256''=encode(p_response,''hex'')) AND NOT EXISTS(SELECT 1 FROM app.egress_operations');
 EXECUTE d;
 -- Bing page recorder gets its own closed, admitted binding port.
 SELECT pg_get_functiondef('control.record_bing_page_import_generation(uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid)'::regprocedure) INTO d;
 d:=replace(d,'FUNCTION control.record_bing_page_import_generation(',
  'FUNCTION control.weekly_skill_record_bing_page_import_generation(p_hash bytea,p_generation text,');
 d:=regexp_replace(d,'BEGIN','BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site_id,ARRAY[''import_bing'']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.weekly_skill_context(p_hash,p_generation,p_site_id)) THEN RAISE EXCEPTION ''skill_tenant_denied'' USING ERRCODE=''42501''; END IF; PERFORM control.assert_weekly_skill_resource(p_hash,p_generation,p_site_id,''binding_id'',p_binding_id);');
 EXECUTE d;
 REVOKE ALL ON FUNCTION control.weekly_skill_record_bing_page_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION control.weekly_skill_record_bing_page_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) TO signal_workflow;
END $$;

CREATE FUNCTION control.read_owner_bing_connector(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b record; attempt record; verified text;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
  OR a.authentication_level IS DISTINCT FROM 'mfa' OR NOT EXISTS(SELECT 1 FROM app.sessions WHERE session_token_hash=p_hash AND auth_time BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp())
  THEN RETURN jsonb_build_object('availability','denied'); END IF;
 SELECT origin INTO verified FROM control.verified_site_origin(a.tenant_id,p_site) WHERE outcome='verified';
 IF verified IS NULL THEN RETURN jsonb_build_object('availability','denied'); END IF;
 SELECT x.binding_id,x.property_resource_name,
  CASE WHEN EXISTS(SELECT 1 FROM app.bing_binding_events e WHERE e.tenant_id=x.tenant_id AND e.site_id=x.site_id AND e.binding_id=x.binding_id AND e.event_kind='reauth_required') THEN 'reauth_required' ELSE 'bound' END state
  INTO b FROM app.bing_binding_events x WHERE x.tenant_id=a.tenant_id AND x.site_id=p_site AND x.event_kind='bound'
  AND x.verified_origin=verified AND NOT EXISTS(SELECT 1 FROM app.bing_binding_events e WHERE e.tenant_id=x.tenant_id AND e.site_id=x.site_id AND e.binding_id=x.binding_id AND e.event_kind='revoked')
  ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1;
 IF b.state='bound' THEN RETURN jsonb_build_object('availability','bound','binding_id',b.binding_id,'site_url',b.property_resource_name); END IF;
 SELECT * INTO attempt FROM app.bing_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site AND user_id=a.user_id
  AND status='selecting' AND expires_at>clock_timestamp() AND verified_origin=verified ORDER BY staged_at DESC,id DESC LIMIT 1;
 IF FOUND AND attempt.candidates @> jsonb_build_array(jsonb_build_object('url',verified||'/','eligible',true)) THEN
  RETURN jsonb_build_object('availability','selecting','attempt_id',attempt.id,'sites',jsonb_build_array(jsonb_build_object('url',verified||'/')));
 END IF;
 IF b.state='reauth_required' THEN RETURN jsonb_build_object('availability','reauth_required','binding_id',b.binding_id,'site_url',b.property_resource_name); END IF;
 RETURN jsonb_build_object('availability','unbound');
END $$;
REVOKE ALL ON FUNCTION control.read_owner_bing_connector(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_owner_bing_connector(bytea,text,uuid) TO signal_identity;

-- Extend the pinned source packet, not the immutable historical snapshots.
ALTER FUNCTION control.keyword_topic_sources(uuid,uuid) RENAME TO keyword_topic_sources_0141;
CREATE FUNCTION control.keyword_topic_sources(p_tenant uuid,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE packet jsonb; records jsonb; s record; usage bigint; reason text;
BEGIN
 packet:=control.keyword_topic_sources_0141(p_tenant,p_site);
 SELECT * INTO s FROM app.dataforseo_settings WHERE tenant_id=p_tenant AND site_id=p_site;
 usage:=control.dataforseo_usage(p_tenant,p_site);
 SELECT coalesce(jsonb_agg(x ORDER BY x.id),'[]') INTO records FROM (
  SELECT c.id,c.query,c.recorded_at,r.result,encode(r.response_sha256,'hex') response_sha256
  FROM app.dataforseo_calls c JOIN app.dataforseo_receipts r ON (r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
  WHERE c.tenant_id=p_tenant AND c.site_id=p_site AND r.status='complete'
   AND s.enabled AND c.credential_generation=s.credential_generation
  ORDER BY c.recorded_at DESC,c.id DESC LIMIT 100) x;
 reason:=CASE WHEN NOT coalesce(s.enabled,false) THEN 'DataForSEO is not configured.'
  WHEN usage+90000>s.cap_micros THEN 'The monthly DataForSEO cap leaves too little for another volume call; recorded evidence remains available.'
  WHEN EXISTS(SELECT 1 FROM app.dataforseo_calls c LEFT JOIN app.dataforseo_receipts r ON (r.tenant_id,r.site_id,r.call_id)=(c.tenant_id,c.site_id,c.id)
   WHERE c.tenant_id=p_tenant AND c.site_id=p_site AND coalesce(r.status,'unknown')<>'complete')
   THEN 'DataForSEO has an unavailable or unresolved call. Its spend is held; it will not be retried automatically.'
  WHEN EXISTS(SELECT 1 FROM app.standing_authorizations g LEFT JOIN app.autonomy_weekly_usage u ON u.tenant_id=g.tenant_id AND u.site_id=g.site_id
    AND u.week_start=date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC')::date
   WHERE g.tenant_id=p_tenant AND g.site_id=p_site
   AND g.id=(SELECT x.id FROM app.standing_authorizations x WHERE x.tenant_id=p_tenant AND x.site_id=p_site ORDER BY x.granted_at DESC,x.id DESC LIMIT 1)
   AND (coalesce(u.spend_cents,0)+9>g.weekly_spend_cents OR coalesce(u.total_count,0)>=g.weekly_total_cap
    OR coalesce((u.per_type_counts->>'research_audit')::integer,0)>=(g.weekly_volume_caps->>'research_audit')::integer))
   THEN 'The standing authorization budget or weekly volume cap leaves no room for more DataForSEO research.'
  WHEN records='[]'::jsonb THEN 'No completed DataForSEO research. A configured executor and a current research standing authorization with budget are required.' END;
 RETURN packet||jsonb_build_object('dataforseo',jsonb_build_object('reason',reason,'records',records));
END $$;

-- Provider overruns remain visible and occupy the standing ledger as well.
CREATE FUNCTION control.account_strategy_provider_cost() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE reservation record; difference bigint;
BEGIN
 SELECT r.* INTO reservation FROM app.autonomy_reservations r JOIN app.strategy_provider_requests s
  ON (s.tenant_id,s.site_id,s.call_id)=(r.tenant_id,r.site_id,r.operation_id)
  WHERE s.tenant_id=NEW.tenant_id AND s.site_id=NEW.site_id AND s.call_id=NEW.call_id;
 IF FOUND AND NEW.reported_cost_micros IS NOT NULL THEN
  difference:=greatest(0,(NEW.reported_cost_micros+9999)/10000-reservation.cost_cents);
  UPDATE app.autonomy_weekly_usage SET spend_cents=spend_cents+difference
   WHERE tenant_id=NEW.tenant_id AND site_id=NEW.site_id AND week_start=reservation.week_start;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.account_strategy_provider_cost() FROM PUBLIC;
CREATE TRIGGER account_strategy_provider_cost AFTER INSERT ON app.dataforseo_receipts
 FOR EACH ROW EXECUTE FUNCTION control.account_strategy_provider_cost();
REVOKE ALL ON FUNCTION control.keyword_topic_sources(uuid,uuid),control.keyword_topic_sources_0141(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION control.strategy_bing_pages(p_tenant uuid,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rows jsonb;
BEGIN
 IF p_tenant IS DISTINCT FROM app.current_tenant_id() OR p_site IS DISTINCT FROM app.current_site_id() THEN RAISE EXCEPTION 'source_scope_denied'; END IF;
 SELECT coalesce(jsonb_agg(x),'[]') INTO rows FROM (
  SELECT g.id,g.rows,g.coverage,g.imported_at,'["page","date"]'::jsonb dimensions
  FROM app.bing_import_generations g JOIN control.current_bing_binding(p_tenant,p_site) b ON b.binding_id=g.binding_id
  WHERE g.tenant_id=p_tenant AND g.site_id=p_site AND g.kind='page_performance'
  ORDER BY g.imported_at DESC,g.id DESC LIMIT 1) x;
 RETURN jsonb_build_object('reason',CASE WHEN rows='[]'::jsonb THEN 'No current Bing page import; page metrics unavailable.' END,'records',rows);
END $$;
REVOKE ALL ON FUNCTION control.strategy_bing_pages(uuid,uuid) FROM PUBLIC;
DO $$ DECLARE f text; d text; BEGIN
 FOREACH f IN ARRAY ARRAY['seo_strategy_sources','weekly_skill_seo_strategy_sources'] LOOP
  SELECT pg_get_functiondef(p.oid) INTO STRICT d FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname=f;
  d:=replace(d,'RETURN packet || control.keyword_topic_sources(v.tenant_id,p_site);',
   'RETURN packet || control.keyword_topic_sources(v.tenant_id,p_site)||jsonb_build_object(''bing_pages'',control.strategy_bing_pages(v.tenant_id,p_site));');
  EXECUTE d;
 END LOOP;
END $$;
