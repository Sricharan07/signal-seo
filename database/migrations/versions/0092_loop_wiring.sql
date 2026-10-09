ALTER TABLE app.weekly_skill_intents DROP CONSTRAINT weekly_skill_intents_stage_check;
ALTER TABLE app.weekly_skill_results DROP CONSTRAINT weekly_skill_results_stage_check;
ALTER TABLE app.weekly_skill_intents ADD CONSTRAINT weekly_skill_intents_stage_check CHECK(stage IN
 ('import_gsc','import_bing','import_ga4','pagespeed_refresh','visibility_reobserve','brain_refresh','strategy_rebuild','internal_link_proposals','brief_proposals','report_delivery','chat_report_delivery'));
ALTER TABLE app.weekly_skill_results ADD CONSTRAINT weekly_skill_results_stage_check CHECK(stage IN
 ('import_gsc','import_bing','import_ga4','pagespeed_refresh','visibility_reobserve','brain_refresh','strategy_rebuild','internal_link_proposals','brief_proposals','report_delivery','chat_report_delivery'));

-- The legacy column stores an opaque authority hash, not a session foreign key.
-- Mark workload-owned operations explicitly; no identity/session row is created.
ALTER TABLE app.owner_connector_egress_operations ADD COLUMN weekly_skill_handle_hash bytea
 REFERENCES control.weekly_skill_directory(handle_hash);
ALTER TABLE control.admission_leases DROP CONSTRAINT admission_leases_workload_id_check;
ALTER TABLE control.admission_leases ADD CONSTRAINT admission_leases_workload_id_check CHECK(
 workload_id ~ '^(crawl|egress|owner|psi):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');

CREATE FUNCTION control.weekly_skill_pagespeed_context(p_hash bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid,user_id uuid,origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; v record;
BEGIN
 PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['pagespeed_refresh']);
 SELECT * INTO a FROM control.weekly_skill_context(p_hash,p_generation,p_site);
 SELECT * INTO v FROM control.verified_site_origin(a.tenant_id,p_site);
 IF v.outcome IS DISTINCT FROM 'verified' THEN RAISE EXCEPTION 'site_unverified' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT a.tenant_id,a.user_id,v.origin;
END $$;
REVOKE ALL ON FUNCTION control.weekly_skill_pagespeed_context(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_skill_pagespeed_context(bytea,text,uuid) TO signal_workflow;

CREATE FUNCTION control.verified_chat_preferences(p_tenant uuid,p_site uuid,p_generation text)
RETURNS SETOF app.chat_report_preferences LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p.* FROM (SELECT DISTINCT ON(user_id,channel) * FROM app.chat_report_preferences
  WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY user_id,channel,preference_order DESC) p
 CROSS JOIN LATERAL control.resolve_member_site_authority(p_tenant,p.user_id,p_site,'primary') a
 CROSS JOIN LATERAL control.chat_report_destination(p_tenant,p_site,p.user_id,p.channel,p_generation) d
 WHERE p.enabled AND p.recovery_generation=p_generation AND a.outcome='authorized' AND a.role_key='owner'
 AND a.membership_epoch=p.membership_epoch AND a.site_authorization_epoch=p.site_epoch
 AND d.binding_id=p.binding_id AND d.link_id IS NOT DISTINCT FROM p.link_id
 AND p.membership_change_count=(SELECT count(*) FROM control.email_membership_changes c JOIN app.memberships m ON m.tenant_id=c.tenant_id AND m.id=c.membership_id WHERE m.tenant_id=p_tenant AND m.user_id=p.user_id)
 AND EXISTS(SELECT 1 FROM control.chat_report_configuration WHERE provider=CASE WHEN p.channel='telegram' THEN 'telegram' ELSE 'slack' END)
$$;
REVOKE ALL ON FUNCTION control.verified_chat_preferences(uuid,uuid,text) FROM PUBLIC;

-- Keep 0135's closed migration-time copy mechanism. The current-owner path is
-- untouched. Only sampling, exact PSI dispatch/completion and observation ports
-- receive the separately qualified standing resolver.
DO $$ DECLARE definition text; args text; f text; old text; BEGIN
 SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid) INTO STRICT definition,args
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname='schedule_pagespeed_sample';
 definition:=replace(definition,'FUNCTION control.schedule_pagespeed_sample('||args||')','FUNCTION control.weekly_pagespeed_plan(p_tenant uuid,p_site uuid)');
 definition:=replace(definition,'SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);',
  'SELECT p_tenant AS tenant_id,v.origin INTO a FROM control.verified_site_origin(p_tenant,p_site) v WHERE v.outcome=''verified'';');
 EXECUTE definition;
 REVOKE ALL ON FUNCTION control.weekly_pagespeed_plan(uuid,uuid) FROM PUBLIC;
 FOREACH f IN ARRAY ARRAY['finish_pagespeed','begin_owner_connector_egress','finish_owner_connector_egress'] LOOP
  SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid) INTO STRICT definition,args
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname=f;
  definition:=replace(definition,'FUNCTION control.'||f||'(', 'FUNCTION control.weekly_skill_'||CASE f WHEN 'begin_owner_connector_egress' THEN 'begin_pagespeed_egress' WHEN 'finish_owner_connector_egress' THEN 'finish_pagespeed_egress' ELSE f END||'(');
  IF f='finish_pagespeed' THEN
   definition:=replace(definition,'control.gsc_owner_context(', 'control.weekly_skill_pagespeed_context(');
   definition:=regexp_replace(definition,'BEGIN','BEGIN IF NOT EXISTS(SELECT 1 FROM control.weekly_skill_directory WHERE handle_hash=p_session AND site_id=p_site AND recovery_generation=p_generation) THEN RETURN ''denied''; END IF;');
   definition:=replace(definition,'AND id=p_sample AND verified_origin=a.origin FOR SHARE;',
    'AND id=p_sample AND verified_origin=a.origin AND EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session AND u->>''sample_id''=p_sample::text) FOR SHARE;');
  ELSIF f='begin_owner_connector_egress' THEN
   definition:=regexp_replace(definition,'BEGIN','BEGIN IF p_profile IS DISTINCT FROM ''pagespeed'' THEN RAISE EXCEPTION ''skill_profile_denied'' USING ERRCODE=''42501''; END IF;');
   old:='SELECT * INTO v_owner FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);';
   IF strpos(definition,old)=0 THEN RAISE EXCEPTION 'psi_authority_copy_drift'; END IF;
   definition:=replace(definition,old,'SELECT * INTO v_owner FROM control.weekly_skill_pagespeed_context(p_session_hash,p_generation,p_site_id);');
   definition:=replace(definition,'IF v_owner.outcome IS DISTINCT FROM ''authorized'' OR v_owner.role_key IS DISTINCT FROM ''owner''
       OR v_owner.authentication_level IS DISTINCT FROM ''mfa''','IF v_owner.tenant_id IS NULL');
   old:='SELECT LEAST(session.expires_at,identity.expires_at) INTO v_session_expires FROM app.sessions AS session
     JOIN control.identity_sessions AS identity ON identity.id=session.identity_session_id
     WHERE session.session_token_hash=p_session_hash AND session.expires_at>v_now
       AND identity.expires_at>v_now AND session.revoked_at IS NULL AND identity.revoked_at IS NULL;';
   IF strpos(definition,old)=0 THEN RAISE EXCEPTION 'psi_expiry_copy_drift'; END IF;
   definition:=replace(definition,old,'SELECT g.ends_at INTO v_session_expires FROM app.weekly_skill_intents i JOIN app.standing_authorizations g ON g.tenant_id=i.tenant_id AND g.site_id=i.site_id AND g.id=i.grant_id WHERE i.handle_hash=p_session_hash AND g.ends_at>v_now;');
   definition:=replace(definition,'v_workload:=''owner:''','v_workload:=''psi:''');
   definition:=replace(definition,'robots_operation_id,bucket_id,issued_at,expires_at)', 'robots_operation_id,bucket_id,issued_at,expires_at,weekly_skill_handle_hash)');
   definition:=replace(definition,'p_robots_operation_id,v_bucket.id,v_now,v_expires);','p_robots_operation_id,v_bucket.id,v_now,v_expires,p_session_hash);');
   definition:=replace(definition,'IF p_profile=''pagespeed'' AND control.pagespeed_admission_allowed(',
    'IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session_hash AND control.pagespeed_request_url(u->>''url'',u->>''strategy'')=p_target_url AND (p_kind=''robots'' OR u->>''sample_id''=p_operation_id::text)) THEN RAISE EXCEPTION ''skill_resource_denied'' USING ERRCODE=''42501''; END IF; IF p_profile=''pagespeed'' AND control.pagespeed_admission_allowed(');
  ELSE
   old:='PERFORM set_config(''signal.session_hash'',encode(p_session_hash,''hex''),true);
    SELECT tenant_id INTO v_tenant FROM app.sessions WHERE session_token_hash=p_session_hash;';
   IF strpos(definition,old)=0 THEN RAISE EXCEPTION 'psi_completion_copy_drift'; END IF;
   definition:=replace(definition,old,'SELECT tenant_id INTO v_tenant FROM control.weekly_skill_directory WHERE handle_hash=p_session_hash AND site_id=p_site_id AND recovery_generation=p_generation;');
   definition:=replace(definition,'OR v_op.recovery_generation IS DISTINCT FROM p_generation THEN', 'OR v_op.recovery_generation IS DISTINCT FROM p_generation OR v_op.weekly_skill_handle_hash IS DISTINCT FROM p_session_hash OR v_op.profile<>''pagespeed'' THEN');
  END IF;
  IF definition LIKE '%%FROM app.sessions%%' OR definition LIKE '%%control.resolve_snapshot_authority(%%' THEN RAISE EXCEPTION 'psi_identity_copy_denied'; END IF;
  EXECUTE definition;
  EXECUTE format('REVOKE ALL ON FUNCTION control.weekly_skill_%%I(%%s) FROM PUBLIC',CASE f WHEN 'begin_owner_connector_egress' THEN 'begin_pagespeed_egress' WHEN 'finish_owner_connector_egress' THEN 'finish_pagespeed_egress' ELSE f END,args);
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.weekly_skill_%%I(%%s) TO signal_workflow',CASE f WHEN 'begin_owner_connector_egress' THEN 'begin_pagespeed_egress' WHEN 'finish_owner_connector_egress' THEN 'finish_pagespeed_egress' ELSE f END,args);
 END LOOP;
END $$;

DO $$ DECLARE definition text; BEGIN
 SELECT pg_get_functiondef('control.weekly_skill_context(bytea,text,uuid)'::regprocedure) INTO definition;
 definition:=replace(definition,'i.stage=''report_delivery''','i.stage IN (''report_delivery'',''chat_report_delivery'')'); EXECUTE definition;
 SELECT pg_get_functiondef('control.admit_weekly_skill(uuid,uuid,uuid,uuid,text,text,bytea,bigint,boolean)'::regprocedure) INTO definition;
 definition:=replace(definition,'''import_ga4'',''brain_refresh''','''import_ga4'',''pagespeed_refresh'',''visibility_reobserve'',''chat_report_delivery'',''brain_refresh''');
 definition:=replace(definition,'p_stage=''report_delivery''','p_stage IN (''report_delivery'',''chat_report_delivery'')');
 definition:=replace(definition,'p_stage<>''brain_refresh'' AND p_cost<>0','p_stage NOT IN (''brain_refresh'',''visibility_reobserve'') AND p_cost<>0');
 definition:=replace(definition,'IF p_stage=''import_gsc'' THEN', $port$
 IF p_stage='pagespeed_refresh' THEN
  SELECT coalesce(jsonb_agg(u||jsonb_build_object('resource_path',coalesce(substring(u->>'url' FROM '^https?://[^/]+(/[^?#]*)'),'/'))),'[]') INTO plan
  FROM jsonb_array_elements(control.weekly_pagespeed_plan(p_tenant,p_site)->'samples') u;
  IF jsonb_array_length(plan)=0 THEN RETURN jsonb_build_object('state','unavailable','reason','PSI_NO_DUE_SAMPLES'); END IF;
 ELSIF p_stage='visibility_reobserve' THEN
  IF p_cost=0 THEN RETURN jsonb_build_object('state','unavailable','reason','COST_BOUND_UNCONFIGURED'); END IF;
  IF control.ai_visibility_schedule_current(p_tenant,p_site,p_generation) IS NULL THEN RETURN jsonb_build_object('state','unavailable','reason','VISIBILITY_SCHEDULE_UNAVAILABLE'); END IF;
  IF EXISTS(SELECT 1 FROM app.ai_visibility_scheduled_runs WHERE tenant_id=p_tenant AND site_id=p_site AND question_set_id IS NOT NULL AND started_at>transaction_timestamp()-make_interval(days=>(control.ai_visibility_schedule_current(p_tenant,p_site,p_generation)->>'cadence_days')::integer)) THEN RETURN jsonb_build_object('state','unavailable','reason','CADENCE_NOT_DUE'); END IF;
  SELECT coalesce(jsonb_agg(jsonb_build_object('question_id',q.id,'provider',provider,'resource_path','/visibility')),'[]') INTO plan FROM app.ai_visibility_questions q CROSS JOIN unnest(ARRAY['openai','perplexity','gemini']) provider WHERE q.tenant_id=p_tenant AND q.site_id=p_site AND q.question_set_id=(SELECT id FROM app.ai_visibility_question_sets WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY created_at DESC,id DESC LIMIT 1);
 ELSIF p_stage='chat_report_delivery' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('preference_id',id,'resource_path','/reports')),'[]') INTO plan FROM control.verified_chat_preferences(p_tenant,p_site,p_generation);
  IF jsonb_array_length(plan)=0 THEN RETURN jsonb_build_object('state','unavailable','reason','NO_VERIFIED_CHAT_RECIPIENT'); END IF;
 ELSIF p_stage='import_gsc' THEN
 $port$);
 EXECUTE definition;
 SELECT pg_get_functiondef('control.weekly_report_projection(uuid,uuid,date)'::regprocedure) INTO definition;
 definition:=replace(definition,'''import_ga4'',''brain_refresh''','''import_ga4'',''pagespeed_refresh'',''visibility_reobserve'',''brain_refresh''');
 definition:=replace(definition,'''brief_proposals'',''report_delivery'']','''brief_proposals'',''report_delivery'',''chat_report_delivery'']');
 definition:=replace(definition,'ELSE ''standing_authorization'' END,', 'WHEN s.stage=''pagespeed_refresh'' THEN ''standing_authorization_and_pagespeed'' WHEN s.stage=''visibility_reobserve'' THEN ''standing_authorization_and_assistants'' WHEN s.stage=''chat_report_delivery'' THEN ''standing_authorization_and_chat'' ELSE ''standing_authorization'' END,');
 EXECUTE definition;
END $$;

ALTER TABLE app.chat_report_outbox DROP CONSTRAINT chat_report_outbox_category_check;
ALTER TABLE app.chat_report_outbox ADD CONSTRAINT chat_report_outbox_category_check CHECK(category IN
 ('weekly_report','pause','revocation','failed_delivery','stale_binding','health_alert'));

CREATE FUNCTION control.queue_verified_chat_event(p_tenant uuid,p_site uuid,p_generation text,p_event uuid,p_category text,p_projection jsonb,p_preferences uuid[] DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p record; cfg record; d record; identifier uuid; refs jsonb:='[]'; BEGIN
 PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
 IF p_category NOT IN ('weekly_report','health_alert') OR p_projection IS NULL OR octet_length(p_projection::text)>131072 THEN RAISE EXCEPTION 'bounded_chat_event_required'; END IF;
 FOR p IN SELECT * FROM control.verified_chat_preferences(p_tenant,p_site,p_generation) WHERE p_preferences IS NULL OR id=ANY(p_preferences) ORDER BY id LOOP
  SELECT * INTO cfg FROM control.chat_report_configuration WHERE provider=CASE WHEN p.channel='telegram' THEN 'telegram' ELSE 'slack' END;
  SELECT * INTO d FROM control.chat_report_destination(p_tenant,p_site,p.user_id,p.channel,p_generation);
  identifier:=gen_random_uuid();
  INSERT INTO app.chat_report_outbox(tenant_id,site_id,id,event_id,category,preference_id,user_id,channel,binding_id,link_id,destination,projection,configuration_sha256)
   VALUES(p_tenant,p_site,identifier,p_event,p_category,p.id,p.user_id,p.channel,d.binding_id,d.link_id,d.destination,p_projection,cfg.configuration_sha256)
   ON CONFLICT(tenant_id,site_id,event_id,category,channel,binding_id,destination) DO NOTHING;
  IF FOUND THEN INSERT INTO control.chat_report_routes(id,tenant_id,site_id) VALUES(identifier,p_tenant,p_site); END IF;
  SELECT id INTO identifier FROM app.chat_report_outbox WHERE tenant_id=p_tenant AND site_id=p_site AND event_id=p_event AND category=p_category AND channel=p.channel AND binding_id=d.binding_id AND destination=d.destination;
  refs:=refs||jsonb_build_array('chat_outbox:'||identifier::text);
 END LOOP;
 RETURN refs;
END $$;
REVOKE ALL ON FUNCTION control.queue_verified_chat_event(uuid,uuid,text,uuid,text,jsonb,uuid[]) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.queue_weekly_chat_report() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF NEW.skill_registry_version=0 AND OLD.status='running' AND NEW.status IN ('completed','stopped','failed') THEN
  PERFORM control.queue_chat_report_event(NEW.tenant_id,NEW.site_id,NEW.id,'weekly_report',control.weekly_report_projection(NEW.tenant_id,NEW.site_id,NEW.week_start)||jsonb_build_object('measurements',control.change_measurement_projection(NEW.tenant_id,NEW.site_id,NEW.week_start)));
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION control.weekly_skill_queue_chat_report(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i record; c record; refs jsonb; preferences uuid[]; BEGIN
 PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['chat_report_delivery']);
 SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
 SELECT * INTO c FROM app.weekly_cycles WHERE tenant_id=i.tenant_id AND site_id=p_site AND id=i.cycle_id;
 IF c.status='running' THEN RETURN jsonb_build_object('state','unavailable','reason','REPORT_NOT_CLOSED'); END IF;
 SELECT array_agg((u->>'preference_id')::uuid) INTO preferences FROM jsonb_array_elements(i.plan) u;
 refs:=control.queue_verified_chat_event(i.tenant_id,p_site,p_generation,i.cycle_id,'weekly_report',control.weekly_report_projection(i.tenant_id,p_site,c.week_start)||jsonb_build_object('measurements',control.change_measurement_projection(i.tenant_id,p_site,c.week_start)),preferences);
 RETURN jsonb_build_object('state',CASE WHEN jsonb_array_length(refs)>0 THEN 'queued' ELSE 'unavailable' END,'reason','NO_VERIFIED_CHAT_RECIPIENT','evidence_refs',refs);
END $$;
REVOKE ALL ON FUNCTION control.weekly_skill_queue_chat_report(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_skill_queue_chat_report(bytea,text,uuid) TO signal_workflow;

CREATE FUNCTION control.queue_health_chat_alerts(p_tenant uuid,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE event jsonb; refs jsonb:='[]'; BEGIN
 IF session_user<>'signal_identity' THEN RAISE EXCEPTION 'identity_worker_required' USING ERRCODE='42501'; END IF;
 FOR event IN SELECT value FROM jsonb_array_elements(control.health_alert_events(p_tenant,p_site)) LOOP
  refs:=refs||control.queue_verified_chat_event(p_tenant,p_site,p_generation,(event->>'event_id')::uuid,'health_alert','{}');
 END LOOP;
 RETURN refs;
END $$;
REVOKE ALL ON FUNCTION control.queue_health_chat_alerts(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.queue_health_chat_alerts(uuid,uuid,text) TO signal_identity;

CREATE FUNCTION control.due_site_chat_reports(p_tenant uuid,p_site uuid,p_limit integer)
RETURNS SETOF uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF session_user<>'signal_identity' OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'identity_worker_required' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT id FROM control.chat_report_routes WHERE tenant_id=p_tenant AND site_id=p_site AND current_state IN ('queued','retry','dispatching') AND ready_at<=transaction_timestamp() ORDER BY ready_at,id LIMIT p_limit;
END $$;
REVOKE ALL ON FUNCTION control.due_site_chat_reports(uuid,uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.due_site_chat_reports(uuid,uuid,integer) TO signal_identity;

ALTER FUNCTION control.measurement_source_window(uuid,uuid,text,date,date,timestamptz) RENAME TO measurement_source_window_before_bing_pages;
CREATE FUNCTION control.measurement_source_window(p_tenant uuid,p_site uuid,p_page text,p_start date,p_end date,p_cutoff timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; b record; binding uuid; count_rows integer; metrics jsonb; page jsonb; BEGIN
 result:=control.measurement_source_window_before_bing_pages(p_tenant,p_site,p_page,p_start,p_end,p_cutoff);
 SELECT binding_id INTO binding FROM control.current_bing_binding(p_tenant,p_site);
 IF binding IS NULL THEN RETURN result; END IF;
 SELECT * INTO b FROM app.bing_import_generations WHERE tenant_id=p_tenant AND site_id=p_site AND binding_id=binding AND kind='page_performance' AND imported_at<=p_cutoff ORDER BY imported_at DESC,id DESC LIMIT 1;
 IF b.id IS NULL THEN RETURN result; END IF;
 SELECT count(*),jsonb_build_object('clicks',sum((r->>'clicks')::numeric),'impressions',sum((r->>'impressions')::numeric),'ctr',NULL,'position',NULL) INTO count_rows,metrics FROM jsonb_array_elements(b.rows) r WHERE r->>'page_url'=p_page AND (to_timestamp(substring(r->>'date' FROM '^/Date\((-?[0-9]+)')::numeric/1000) AT TIME ZONE 'UTC')::date BETWEEN p_start AND p_end;
 page:=jsonb_build_object('state',CASE WHEN count_rows=0 THEN 'unavailable' ELSE 'measured_as_reported' END,'reason',CASE WHEN count_rows=0 THEN 'PAGE_ROWS_NOT_REPORTED_IN_WINDOW' ELSE 'AS_REPORTED_DATE_GRANULARITY_UNKNOWN' END,'generation_id',b.id,'coverage',b.coverage,'metrics',CASE WHEN count_rows=0 THEN NULL ELSE metrics END);
 RETURN result||jsonb_build_object('bing_page',page);
EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow THEN
 RETURN result||jsonb_build_object('bing_page',jsonb_build_object('state','failed','reason','SOURCE_RECORD_INVALID','generation_id',NULL,'coverage',NULL,'metrics',NULL));
END $$;
REVOKE ALL ON FUNCTION control.measurement_source_window(uuid,uuid,text,date,date,timestamptz),control.measurement_source_window_before_bing_pages(uuid,uuid,text,date,date,timestamptz) FROM PUBLIC;

CREATE FUNCTION control.assert_weekly_visibility_call(p_hash bytea,p_generation text,p_site uuid,p_run uuid,p_question uuid DEFAULT NULL,p_provider text DEFAULT NULL,p_ceiling bigint DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i record;
BEGIN
 PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['visibility_reobserve']);
 SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
 IF p_run IS DISTINCT FROM control.weekly_skill_identity(i.cycle_id,'visibility_reobserve') OR
 (p_question IS NOT NULL AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(i.plan) u WHERE u->>'question_id'=p_question::text AND u->>'provider'=p_provider)) OR
 (p_ceiling IS NOT NULL AND p_ceiling>(i.reserved_cents/jsonb_array_length(i.plan))*10000) THEN RAISE EXCEPTION 'visibility_scope_denied' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.assert_weekly_visibility_call(bytea,text,uuid,uuid,uuid,text,bigint) FROM PUBLIC;

DO $$ DECLARE f text; definition text; args text; guard text; BEGIN
 FOREACH f IN ARRAY ARRAY['open_ai_visibility_run','reserve_ai_visibility_call','ai_visibility_call_permit','finish_ai_visibility_call'] LOOP
  SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid) INTO STRICT definition,args FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname=f;
  definition:=replace(definition,'FUNCTION control.'||f||'(', 'FUNCTION control.weekly_skill_'||f||'(p_hash bytea,p_recovery text,');
  guard:='IF p_tenant IS DISTINCT FROM (SELECT tenant_id FROM control.weekly_skill_directory WHERE handle_hash=p_hash AND site_id=p_site AND recovery_generation=p_recovery) THEN RAISE EXCEPTION ''skill_tenant_denied'' USING ERRCODE=''42501''; END IF;';
  guard:=guard||'PERFORM set_config(''signal.tenant_id'',p_tenant::text,true); PERFORM set_config(''signal.site_id'',p_site::text,true);';
  IF f='open_ai_visibility_run' THEN guard:=guard||'PERFORM control.assert_weekly_visibility_call(p_hash,p_recovery,p_site,p_id);';
  ELSIF f='reserve_ai_visibility_call' THEN guard:=guard||'PERFORM control.assert_weekly_visibility_call(p_hash,p_recovery,p_site,p_run,p_question,p_provider,p_ceiling);';
  ELSE guard:=guard||'IF NOT EXISTS(SELECT 1 FROM app.ai_visibility_call_reservations skill_call JOIN app.weekly_skill_intents skill_intent ON skill_intent.tenant_id=skill_call.tenant_id AND skill_intent.site_id=skill_call.site_id WHERE skill_intent.handle_hash=p_hash AND skill_intent.stage=''visibility_reobserve'' AND skill_call.id=p_operation AND skill_call.run_id=control.weekly_skill_identity(skill_intent.cycle_id,''visibility_reobserve'')) THEN RAISE EXCEPTION ''skill_call_denied'' USING ERRCODE=''42501''; END IF;';
   IF f='ai_visibility_call_permit' THEN guard:=guard||'PERFORM control.assert_weekly_skill_stage(p_hash,p_recovery,p_site,ARRAY[''visibility_reobserve'']);'; END IF;
  END IF;
  IF f<>'finish_ai_visibility_call' THEN guard:=guard||'IF p_generation IS DISTINCT FROM p_recovery THEN RAISE EXCEPTION ''skill_generation_denied'' USING ERRCODE=''42501''; END IF;'; END IF;
  definition:=regexp_replace(definition,'BEGIN','BEGIN '||guard);
  EXECUTE definition;
  EXECUTE format('REVOKE ALL ON FUNCTION control.weekly_skill_%%I(bytea,text,%%s) FROM PUBLIC',f,args);
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.weekly_skill_%%I(bytea,text,%%s) TO signal_workflow',f,args);
 END LOOP;
END $$;

DO $$ DECLARE definition text; BEGIN
 SELECT pg_get_functiondef('control.measure_change_horizon(uuid,uuid,uuid,integer,timestamptz)'::regprocedure) INTO definition;
 definition:=replace(definition,'v_post->''bing_site_context''->''metrics'')),',
  'v_post->''bing_site_context''->''metrics''),''bing_page'',control.measurement_delta(p.baseline->''bing_page''->''metrics'',v_post->''bing_page''->''metrics'')),');
 EXECUTE definition;
END $$;

-- Astro key additions inherit 0127's A4 fresh-owner review and publishing gate.
-- The candidate must preserve every baseline artifact and add only the served key.
ALTER TABLE app.candidate_recipe_revisions DROP CONSTRAINT candidate_recipe_evidence_kind;
ALTER TABLE app.candidate_recipe_revisions ADD CONSTRAINT candidate_recipe_evidence_kind CHECK(
 audit_report_id IS NOT NULL OR coalesce(
 (convert_from(canonical_manifest,'UTF8')::jsonb #>> '{evidence,finding,key}' IN ('indexnow.key.required','links.internal.add')
 AND (convert_from(canonical_manifest,'UTF8')::jsonb ->>'approval_class'='owner_review' OR
 (convert_from(canonical_manifest,'UTF8')::jsonb #>> '{evidence,finding,key}'='indexnow.key.required'
 AND convert_from(canonical_manifest,'UTF8')::jsonb ->>'approval_class'='A4'
 AND convert_from(canonical_manifest,'UTF8')::jsonb ->>'autonomy_eligible'='false'
 AND convert_from(canonical_manifest,'UTF8')::jsonb ? 'static_key_placement'))),false));
DO $$ DECLARE definition text; old text; BEGIN
 SELECT pg_get_functiondef('control.seal_indexnow_key_revision(bytea,uuid,text,uuid,uuid,bytea,bytea)'::regprocedure) INTO definition;
 definition:=replace(definition,'DECLARE a record;', 'DECLARE astro boolean; base app.candidate_build_intents%%ROWTYPE; base_receipt app.candidate_build_receipts%%ROWTYPE; a record;');
 old:='SELECT * INTO release FROM control.recipe_releases WHERE id=(m->>''recipe_release_id'')::uuid;';
 IF strpos(definition,old)=0 THEN RAISE EXCEPTION 'indexnow_copy_drift'; END IF;
 definition:=replace(definition,old,old||$astro$
 SELECT framework='astro' INTO astro FROM app.github_pr_extensions WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=b.extension_id;
 IF astro THEN
  SELECT * INTO base FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=(m#>>'{static_key_placement,baseline_build_id}')::uuid;
  SELECT * INTO base_receipt FROM app.candidate_build_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=base.id;
  IF base.id IS NULL OR base.status<>'completed' OR base_receipt.exit_class IS DISTINCT FROM 'passed'
    OR base.extension_id<>b.extension_id OR base.base_sha<>b.base_sha OR base.tree_sha<>b.tree_sha OR base.artifact_root<>b.artifact_root
    OR base.patch_sha256 IS DISTINCT FROM encode(sha256(''::bytea),'hex')
    OR NOT EXISTS(SELECT 1 FROM app.candidate_built_html WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=base.id)
    OR (SELECT lockfile_sha256 FROM app.candidate_dependency_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=b.id)
      IS DISTINCT FROM (SELECT lockfile_sha256 FROM app.candidate_dependency_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=base.id)
    OR NOT EXISTS(SELECT 1 FROM app.candidate_dependency_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site AND build_id=base.id)
    OR NOT control.valid_astro_output_directory(m#>>'{static_key_placement,public_directory}')
    OR m#>>'{static_key_placement,output_path}' IS DISTINCT FROM b.artifact_root||'/'||(m#>>'{patch,after}')||'.txt'
    OR m->>'autonomy_eligible' IS DISTINCT FROM 'false'
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(base_receipt.artifacts) x WHERE x->>'path'=m#>>'{static_key_placement,output_path}')
    OR jsonb_array_length(r.artifacts)<>jsonb_array_length(base_receipt.artifacts)+1
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(base_receipt.artifacts) x WHERE NOT r.artifacts @> jsonb_build_array(x))
  THEN RETURN 'invalid_revision'; END IF;
 END IF;
 $astro$);
 definition:=replace(definition,$s$(m#>>'{evidence,site_origin}')||'/'||(m->>'source_path')$s$, $s$(m#>>'{evidence,site_origin}')||'/'||(m#>>'{patch,after}')||'.txt'$s$);
 definition:=replace(definition,$s$m->>'source_path' IS DISTINCT FROM (m#>>'{patch,after}')||'.txt'$s$, $s$m->>'source_path' IS DISTINCT FROM (CASE WHEN astro THEN (m#>>'{static_key_placement,public_directory}')||'/' ELSE '' END)||(m#>>'{patch,after}')||'.txt'$s$);
 definition:=replace(definition,$s$m->>'approval_class' IS DISTINCT FROM 'owner_review'$s$, $s$m->>'approval_class' IS DISTINCT FROM (CASE WHEN astro THEN 'A4' ELSE 'owner_review' END)$s$);
 definition:=replace(definition,$s$artifact->>'path'='_site/'||(m->>'source_path')$s$, $s$artifact->>'path'=b.artifact_root||'/'||(m#>>'{patch,after}')||'.txt'$s$);
 definition:=replace(definition,$s$ext.framework='eleventy' AND ext.content_format='html'$s$, $s$((ext.framework='eleventy' AND ext.content_format='html') OR (ext.framework='astro' AND ext.content_format='astro'))$s$);
 definition:=replace(definition,'INSERT INTO app.indexnow_key_revisions VALUES', $impact$
 IF astro THEN
  INSERT INTO app.astro_candidate_impacts(tenant_id,site_id,revision_id,baseline_build_id,approval_class,canonical_scope)
   VALUES(a.tenant_id,p_site,p_revision,base.id,'A4',convert_to((m->'static_key_placement')::text,'UTF8'));
 END IF;
 INSERT INTO app.indexnow_key_revisions VALUES
 $impact$);
 EXECUTE definition;
END $$;
