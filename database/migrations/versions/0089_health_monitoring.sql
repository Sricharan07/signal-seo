CREATE TABLE app.health_observations (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id bigint GENERATED ALWAYS AS IDENTITY,
 check_key text NOT NULL CHECK(check_key IN ('temporal','weekly_schedule','measurement_schedule',
 'outbox_backlog','outbox_age','binding_gsc','binding_bing','binding_ga4','binding_github',
 'binding_slack','binding_telegram','binding_email','egress_pins','openbao','disk','database_size',
 'write_intents','budget_model','budget_dataforseo','budget_assistants')),
 state text NOT NULL CHECK(state IN ('ok','warning','critical','unknown')),
 reason text NOT NULL CHECK(reason IN ('within_threshold','approaching_threshold','threshold_exceeded',
 'probe_unavailable','not_configured','revoked','expired','import_failing','stale_evidence',
 'reachable','unreachable','sealed','unsealed','paused','stuck','healthy','budget_exhausted',
 'pin_expired','pin_expiring','no_pin_evidence')),
 CHECK((state='ok' AND reason IN ('within_threshold','reachable','unsealed','healthy'))
 OR (state='warning' AND reason IN ('approaching_threshold','import_failing','paused','pin_expiring'))
 OR (state='critical' AND reason IN ('threshold_exceeded','revoked','expired','unreachable','sealed','stuck','budget_exhausted','pin_expired'))
 OR (state='unknown' AND reason IN ('probe_unavailable','not_configured','stale_evidence','no_pin_evidence'))),
 checked_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
CREATE INDEX health_latest ON app.health_observations(tenant_id,site_id,check_key,id DESC);
CREATE TABLE app.health_alerts (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),
 observation_id bigint NOT NULL,created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),UNIQUE(tenant_id,site_id,observation_id),
 FOREIGN KEY(tenant_id,site_id,observation_id) REFERENCES app.health_observations(tenant_id,site_id,id)
);
CREATE INDEX health_alert_rate ON app.health_alerts(tenant_id,site_id,created_at);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['health_observations','health_alerts'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY health_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_scheduler,signal_workflow,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',t);
  EXECUTE format('CREATE TRIGGER health_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
 END LOOP;
END $$;
ALTER TABLE app.email_outbox DROP CONSTRAINT email_outbox_category_check;
ALTER TABLE app.email_outbox ADD CONSTRAINT email_outbox_category_check CHECK(category IN
 ('weekly_report','pause','revocation','failed_delivery','stale_binding','health_alert'));

CREATE FUNCTION control.health_facts(p_tenant uuid,p_site uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb:='{}'; p text; binding jsonb; imported timestamptz; failures bigint;
BEGIN
 PERFORM set_config('signal.tenant_id',p_tenant::text,true);
 PERFORM set_config('signal.site_id',p_site::text,true);
 IF NOT EXISTS(SELECT 1 FROM app.sites s JOIN app.tenants t ON t.tenant_id=s.tenant_id
 WHERE s.tenant_id=p_tenant AND s.id=p_site AND t.lifecycle='active' AND s.state<>'archived') THEN RETURN NULL; END IF;
 SELECT jsonb_build_object('outbox_backlog',jsonb_build_object('value',count(*),'warning',100,'critical',1000),
 'outbox_age',jsonb_build_object('value',greatest(coalesce(extract(epoch FROM transaction_timestamp()-min(available_at)),0),0),'warning',300,'critical',1800))
 INTO result FROM app.outbox WHERE tenant_id=p_tenant AND site_id=p_site AND delivered_at IS NULL;
 result:=result||jsonb_build_object('database_size',jsonb_build_object('value',pg_database_size(current_database()),'warning',10737418240,'critical',21474836480),
 'write_intents',jsonb_build_object('value',(SELECT count(*) FROM app.github_pr_operations WHERE tenant_id=p_tenant AND site_id=p_site AND state IN ('blocked','outcome_unknown')),'warning',1,'critical',5));
 FOREACH p IN ARRAY ARRAY['gsc','bing'] LOOP
  EXECUTE format('SELECT jsonb_build_object(''condition'',CASE event_kind WHEN ''revoked'' THEN ''revoked'' WHEN ''reauth_required'' THEN ''expired'' ELSE ''healthy'' END),recorded_at FROM app.%%I WHERE tenant_id=$1 AND site_id=$2 ORDER BY recorded_at DESC,id DESC LIMIT 1',p||'_binding_events') INTO binding,imported USING p_tenant,p_site;
  IF binding IS NULL THEN binding:=jsonb_build_object('condition','not_configured');
  ELSIF binding->>'condition'='healthy' THEN
   EXECUTE format('SELECT max(imported_at) FROM app.%%I WHERE tenant_id=$1 AND site_id=$2',p||'_import_generations') INTO imported USING p_tenant,p_site;
   IF imported IS NULL OR imported<transaction_timestamp()-interval '7 days' THEN binding:=jsonb_build_object('condition','stale_evidence'); END IF;
   SELECT count(*) INTO failures FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site
    AND egress_profile LIKE p||'%%' AND (state='failed' OR http_status>=400) AND dispatched_at>transaction_timestamp()-interval '1 day';
   IF failures>0 THEN binding:=jsonb_build_object('condition','import_failing'); END IF;
  END IF;
  result:=result||jsonb_build_object('binding_'||p,binding);
 END LOOP;
 SELECT jsonb_build_object('condition',CASE WHEN EXISTS(SELECT 1 FROM control.ga4_binding_restrictions r WHERE r.target_id=b.id) OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='ga4_binding' AND d.target_id=b.id) THEN 'revoked'
 WHEN NOT EXISTS(SELECT 1 FROM app.ga4_import_generations i WHERE i.tenant_id=p_tenant AND i.site_id=p_site AND i.binding_id=b.id AND i.imported_at>transaction_timestamp()-interval '7 days') THEN 'stale_evidence' ELSE 'healthy' END)
 INTO binding FROM app.ga4_bindings b WHERE b.tenant_id=p_tenant AND b.site_id=p_site ORDER BY bound_at DESC,id DESC LIMIT 1;
 result:=result||jsonb_build_object('binding_ga4',coalesce(binding,jsonb_build_object('condition','not_configured')));
 SELECT jsonb_build_object('condition',CASE status WHEN 'active' THEN 'healthy' WHEN 'revoked' THEN 'revoked' WHEN 'failed' THEN CASE WHEN failure_code IN ('GITHUB_AUTHORIZATION_REJECTED','GITHUB_SCOPE_REJECTED','GITHUB_CREDENTIALS_REJECTED') THEN 'expired' ELSE 'import_failing' END ELSE 'stale_evidence' END)
 INTO binding FROM app.github_read_bindings WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY prepared_at DESC,id DESC LIMIT 1;
 result:=result||jsonb_build_object('binding_github',coalesce(binding,jsonb_build_object('condition','not_configured')));
 FOREACH p IN ARRAY ARRAY['slack','telegram'] LOOP
  EXECUTE format('SELECT jsonb_build_object(''condition'',CASE WHEN revoked_at IS NOT NULL THEN ''revoked'' ELSE ''healthy'' END) FROM app.%%I WHERE tenant_id=$1 AND site_id=$2 ORDER BY created_at DESC,id DESC LIMIT 1',p||'_bindings') INTO binding USING p_tenant,p_site;
  result:=result||jsonb_build_object('binding_'||p,coalesce(binding,jsonb_build_object('condition','not_configured')));
 END LOOP;
 SELECT jsonb_build_object('condition',CASE WHEN state IN ('unknown','failed','suppressed') THEN 'import_failing' WHEN state='accepted' AND created_at>transaction_timestamp()-interval '15 minutes' THEN 'healthy' ELSE 'stale_evidence' END)
 INTO binding FROM app.email_outbox WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY created_at DESC,id DESC LIMIT 1;
 result:=result||jsonb_build_object('binding_email',coalesce(binding,jsonb_build_object('condition','not_configured')));
 SELECT jsonb_build_object('condition',CASE WHEN NOT e.enabled THEN 'revoked' WHEN NOT EXISTS
 (SELECT 1 FROM control.email_claim_heads h WHERE h.user_id=e.user_id AND h.claim_epoch=e.claim_epoch AND h.address_sha256=e.address_sha256)
 THEN 'expired' ELSE result->'binding_email'->>'condition' END) INTO binding
 FROM app.email_preferences e JOIN app.memberships m ON m.tenant_id=e.tenant_id AND m.id=e.membership_id
 WHERE e.tenant_id=p_tenant AND m.role_key='owner' AND m.state='active'
 ORDER BY e.preference_order DESC LIMIT 1;
 IF binding IS NOT NULL THEN result:=result||jsonb_build_object('binding_email',binding); END IF;
 IF NOT EXISTS(SELECT 1 FROM control.email_smtp_configuration) THEN
 result:=result||jsonb_build_object('binding_email',jsonb_build_object('condition','not_configured')); END IF;
 FOREACH p IN ARRAY ARRAY['gsc','bing','ga4','github','slack','telegram'] LOOP
  IF result->('binding_'||p)->>'condition' IN ('healthy','stale_evidence','import_failing') THEN
   IF EXISTS(SELECT 1 FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site
    AND egress_profile LIKE p||'%%' AND http_status IN (401,403) AND dispatched_at>transaction_timestamp()-interval '1 day') THEN
    result:=result||jsonb_build_object('binding_'||p,jsonb_build_object('condition','expired'));
   ELSIF EXISTS(SELECT 1 FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site
    AND egress_profile LIKE p||'%%' AND (state='failed' OR http_status>=400) AND dispatched_at>transaction_timestamp()-interval '1 day') THEN
    result:=result||jsonb_build_object('binding_'||p,jsonb_build_object('condition','import_failing'));
   ELSIF result->('binding_'||p)->>'condition'='healthy' AND NOT EXISTS(SELECT 1 FROM app.egress_operations
    WHERE tenant_id=p_tenant AND site_id=p_site AND egress_profile LIKE p||'%%' AND state='observed' AND http_status=200
    AND dispatched_at>transaction_timestamp()-interval '15 minutes') THEN
    result:=result||jsonb_build_object('binding_'||p,jsonb_build_object('condition','stale_evidence'));
   END IF;
  END IF;
 END LOOP;
 SELECT jsonb_build_object('condition',CASE state WHEN 'active' THEN 'stale_evidence' WHEN 'failed' THEN 'expired' ELSE 'not_configured' END)
 INTO binding FROM app.telegram_bindings WHERE tenant_id=p_tenant AND site_id=p_site AND revoked_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 1;
 IF binding IS NOT NULL AND result->'binding_telegram'->>'condition'='healthy' THEN result:=result||jsonb_build_object('binding_telegram',binding); END IF;
 SELECT CASE WHEN cap_micros=0 THEN jsonb_build_object('condition','budget_exhausted') ELSE jsonb_build_object('value',control.dataforseo_usage(p_tenant,p_site),'warning',cap_micros*0.8,'critical',cap_micros) END INTO binding
 FROM app.dataforseo_settings WHERE tenant_id=p_tenant AND site_id=p_site AND enabled;
 result:=result||jsonb_build_object('budget_dataforseo',coalesce(binding,jsonb_build_object('condition','not_configured')));
 result:=result||jsonb_build_object('budget_model',jsonb_build_object('condition','not_configured'),
 'budget_assistants',jsonb_build_object('condition','not_configured'));
 RETURN result;
END $$;

CREATE FUNCTION control.record_health_checks(p_tenant uuid,p_site uuid,p_generation text,p_checks jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c jsonb; emitted app.health_observations%%ROWTYPE; obs bigint; alert uuid; pref record; cfg record; n integer:=0; email_id uuid;
BEGIN
 IF p_generation IS NULL OR p_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 OR jsonb_typeof(p_checks) IS DISTINCT FROM 'array' OR jsonb_array_length(p_checks)<>20
 OR (SELECT count(DISTINCT x->>'check') FROM jsonb_array_elements(p_checks) x)<>20 THEN
 RAISE EXCEPTION 'invalid_health_snapshot' USING ERRCODE='22023'; END IF;
 PERFORM set_config('signal.tenant_id',p_tenant::text,true);
 PERFORM set_config('signal.site_id',p_site::text,true);
 PERFORM 1 FROM app.sites s JOIN app.tenants t ON t.tenant_id=s.tenant_id
 WHERE s.tenant_id=p_tenant AND s.id=p_site AND s.state<>'archived' AND t.lifecycle='active' FOR UPDATE OF s;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO cfg FROM control.email_smtp_configuration;
 FOR c IN SELECT value FROM jsonb_array_elements(p_checks) ORDER BY CASE value->>'state' WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 WHEN 'unknown' THEN 2 ELSE 3 END,value->>'check' LOOP
  INSERT INTO app.health_observations(tenant_id,site_id,check_key,state,reason)
  VALUES(p_tenant,p_site,c->>'check',c->>'state',c->>'reason') RETURNING id INTO obs;
  SELECT o.* INTO emitted FROM app.health_alerts a JOIN app.health_observations o
   ON o.tenant_id=a.tenant_id AND o.site_id=a.site_id AND o.id=a.observation_id
   WHERE a.tenant_id=p_tenant AND a.site_id=p_site AND o.check_key=c->>'check'
   ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
  IF c->>'state'='ok' OR (emitted.state=c->>'state' AND emitted.reason=c->>'reason'
   AND NOT EXISTS(SELECT 1 FROM app.health_observations recovered WHERE recovered.tenant_id=p_tenant
    AND recovered.site_id=p_site AND recovered.check_key=c->>'check' AND recovered.state='ok'
    AND recovered.id>emitted.id)) THEN CONTINUE; END IF;
  IF EXISTS(SELECT 1 FROM app.health_alerts a JOIN app.health_observations o ON o.tenant_id=a.tenant_id AND o.site_id=a.site_id AND o.id=a.observation_id WHERE a.tenant_id=p_tenant AND a.site_id=p_site AND o.check_key=c->>'check' AND a.created_at>transaction_timestamp()-interval '1 hour')
  OR (SELECT count(*) FROM app.health_alerts WHERE tenant_id=p_tenant AND site_id=p_site AND created_at>transaction_timestamp()-interval '1 hour')>=6 THEN CONTINUE; END IF;
  INSERT INTO app.health_alerts(tenant_id,site_id,observation_id) VALUES(p_tenant,p_site,obs) RETURNING id INTO alert;
  n:=n+1;
  IF cfg.host IS NULL THEN CONTINUE; END IF;
  FOR pref IN SELECT e.* FROM app.email_preferences e JOIN app.memberships m ON m.tenant_id=e.tenant_id AND m.id=e.membership_id
   JOIN control.email_claim_heads h ON h.user_id=e.user_id AND h.claim_epoch=e.claim_epoch AND h.address_sha256=e.address_sha256
   WHERE e.tenant_id=p_tenant AND e.enabled AND e.recovery_generation=p_generation
   AND m.state='active' AND m.role_key='owner' AND m.authorization_epoch=e.membership_epoch
   AND e.membership_change_count=(SELECT count(*) FROM control.email_membership_changes WHERE tenant_id=p_tenant AND membership_id=m.id)
   AND NOT EXISTS(SELECT 1 FROM app.email_preferences newer WHERE newer.tenant_id=p_tenant AND newer.membership_id=e.membership_id AND newer.preference_order>e.preference_order)
   AND EXISTS(SELECT 1 FROM control.resolve_member_site_authority(p_tenant,e.user_id,p_site,'primary') a WHERE a.outcome='authorized') LOOP
   email_id:=gen_random_uuid();
   INSERT INTO app.email_outbox(tenant_id,site_id,id,membership_id,preference_id,event_id,category,projection,configuration_sha256)
   VALUES(p_tenant,p_site,email_id,pref.membership_id,pref.id,alert,'health_alert','{}',cfg.configuration_sha256);
   INSERT INTO control.email_outbox_routes(id,tenant_id,site_id) VALUES(email_id,p_tenant,p_site);
  END LOOP;
 END LOOP;
 RETURN jsonb_build_object('alerts',n);
END $$;

CREATE FUNCTION control.read_health_checks(p_session bytea,p_site uuid,p_generation text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; result jsonb;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
 PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
 PERFORM set_config('signal.site_id',p_site::text,true);
 SELECT coalesce(jsonb_agg(jsonb_build_object('check',check_key,'state',CASE WHEN checked_at<transaction_timestamp()-interval '5 minutes' THEN 'unknown' ELSE state END,
 'reason',CASE WHEN checked_at<transaction_timestamp()-interval '5 minutes' THEN 'stale_evidence' ELSE reason END,'checked_at',checked_at) ORDER BY check_key),'[]') INTO result
 FROM (SELECT DISTINCT ON(check_key) * FROM app.health_observations WHERE tenant_id=a.tenant_id AND site_id=p_site ORDER BY check_key,id DESC) latest;
 RETURN jsonb_build_object('checks',result);
END $$;
REVOKE ALL ON FUNCTION control.health_facts(uuid,uuid),control.record_health_checks(uuid,uuid,text,jsonb),control.read_health_checks(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.health_facts(uuid,uuid),control.record_health_checks(uuid,uuid,text,jsonb) TO signal_scheduler;
GRANT EXECUTE ON FUNCTION control.read_health_checks(bytea,uuid,text) TO signal_api;

-- Registration point for 0133: event identity is the downstream chat-outbox dedup key.
-- The existing channel dispatcher must still resolve current, verified owner bindings.
CREATE FUNCTION control.health_alert_events(p_tenant uuid,p_site uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; BEGIN
 PERFORM set_config('signal.tenant_id',p_tenant::text,true);
 PERFORM set_config('signal.site_id',p_site::text,true);
 SELECT coalesce(jsonb_agg(jsonb_build_object('event_id',id,'check',check_key,'state',state,'reason',reason)),'[]') INTO result
 FROM (SELECT a.id,o.check_key,o.state,o.reason FROM app.health_alerts a JOIN app.health_observations o
 ON o.tenant_id=a.tenant_id AND o.site_id=a.site_id AND o.id=a.observation_id
 WHERE a.tenant_id=p_tenant AND a.site_id=p_site AND a.created_at>transaction_timestamp()-interval '1 day'
 ORDER BY a.created_at,a.id LIMIT 100) events;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION control.health_alert_events(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.health_alert_events(uuid,uuid) TO signal_identity;
