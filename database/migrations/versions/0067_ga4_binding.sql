CREATE TABLE app.ga4_oauth_attempts (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
 user_id uuid NOT NULL, verified_origin text NOT NULL,
 state_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(state_sha256)=32),
 redirect_uri text NOT NULL CHECK(length(redirect_uri) BETWEEN 12 AND 2048),
 code_challenge text NOT NULL CHECK(code_challenge ~ '^[A-Za-z0-9_-]{43}$'),
 recovery_generation text NOT NULL,
 status text NOT NULL DEFAULT 'authorizing' CHECK(status IN ('authorizing','exchanging','selecting','confirmed')),
 candidates jsonb, consumed_at timestamptz,
 expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '10 minutes',
 PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TABLE app.ga4_bindings (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL UNIQUE,
 attempt_id uuid NOT NULL, actor_user_id uuid NOT NULL, verified_origin text NOT NULL,
 property_resource_name text NOT NULL CHECK(property_resource_name ~ '^properties/[1-9][0-9]{0,19}$'),
 secret_reference text NOT NULL,
 granted_scope text NOT NULL DEFAULT 'https://www.googleapis.com/auth/analytics.readonly'
 CHECK(granted_scope='https://www.googleapis.com/auth/analytics.readonly'),
 recovery_generation text NOT NULL, stream_operation_id uuid NOT NULL,
 stream_response_sha256 bytea NOT NULL CHECK(octet_length(stream_response_sha256)=32),
 bound_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,attempt_id) REFERENCES app.ga4_oauth_attempts(tenant_id,site_id,id),
 CHECK(secret_reference='secret://ga4/'||attempt_id::text)
);
CREATE TABLE control.ga4_binding_restrictions (
 event_id uuid PRIMARY KEY,target_id uuid NOT NULL UNIQUE REFERENCES app.ga4_bindings(id),
 actor_user_id uuid NOT NULL,reason text NOT NULL CHECK(reason IN ('owner_disconnect','reauth_required')),
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TABLE app.ga4_import_generations (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,
 binding_id uuid NOT NULL,property_resource_name text NOT NULL,
 start_date date NOT NULL,end_date date NOT NULL,
 rows jsonb NOT NULL CHECK(jsonb_typeof(rows)='array' AND jsonb_array_length(rows)<=5000),
 coverage jsonb NOT NULL CHECK(jsonb_typeof(coverage)='object' AND coverage->'complete'='false'::jsonb
 AND coverage->>'missing_data'='unknown_not_zero'),
 receipts jsonb NOT NULL CHECK(jsonb_typeof(receipts)='array' AND jsonb_array_length(receipts) BETWEEN 1 AND 5),
 imported_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.ga4_bindings(tenant_id,site_id,id),
 CHECK(start_date<=end_date AND end_date-start_date<=92)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['ga4_oauth_attempts','ga4_bindings','ga4_import_generations'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY ga4_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_identity,signal_api,signal_crawl_admission,signal_crawl_ingest',t);
 END LOOP;
END $$;
REVOKE ALL ON control.ga4_binding_restrictions FROM PUBLIC;
CREATE TRIGGER ga4_bindings_immutable BEFORE UPDATE OR DELETE ON app.ga4_bindings FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER ga4_imports_immutable BEFORE UPDATE OR DELETE ON app.ga4_import_generations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER ga4_restrictions_immutable BEFORE UPDATE OR DELETE ON control.ga4_binding_restrictions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.ga4_owner_context(p_session bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid,user_id uuid,origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;o record; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' OR a.authentication_level<>'mfa' THEN RETURN; END IF;
 SELECT * INTO o FROM control.verified_site_origin(a.tenant_id,p_site);
 IF o.outcome<>'verified' THEN RETURN; END IF;
 RETURN QUERY SELECT a.tenant_id,a.user_id,o.origin;
END $$;
CREATE FUNCTION control.begin_ga4_oauth_attempt(p_session bytea,p_generation text,p_site uuid,p_id uuid,p_state bytea,p_redirect text,p_challenge text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
 IF p_id IS NULL OR substr(p_id::text,15,1)<>'4' OR octet_length(p_state) IS DISTINCT FROM 32
 OR p_redirect IS NULL OR p_redirect !~ '^https://[^[:space:]?#]+/[^[:space:]?#]*$'
 OR length(p_redirect)>2048 OR p_challenge IS NULL OR p_challenge !~ '^[A-Za-z0-9_-]{43}$' THEN RETURN 'invalid'; END IF;
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 INSERT INTO app.ga4_oauth_attempts(tenant_id,site_id,id,user_id,verified_origin,state_sha256,redirect_uri,code_challenge,recovery_generation)
 VALUES(a.tenant_id,p_site,p_id,a.user_id,a.origin,p_state,p_redirect,p_challenge,p_generation);
 RETURN 'created';
END $$;
CREATE FUNCTION control.consume_ga4_oauth_attempt(p_session bytea,p_generation text,p_site uuid,p_id uuid,p_state bytea,p_redirect text)
RETURNS TABLE(outcome text,origin text,code_challenge text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;t app.ga4_oauth_attempts%%ROWTYPE; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN; END IF;
 SELECT * INTO t FROM app.ga4_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR t.user_id<>a.user_id OR t.verified_origin<>a.origin OR t.recovery_generation<>p_generation
 OR t.state_sha256 IS DISTINCT FROM p_state OR t.redirect_uri IS DISTINCT FROM p_redirect
 OR t.status<>'authorizing' OR t.expires_at<=transaction_timestamp() THEN RETURN; END IF;
 UPDATE app.ga4_oauth_attempts SET status='exchanging',consumed_at=transaction_timestamp() WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id;
 RETURN QUERY SELECT 'consumed'::text,t.verified_origin,t.code_challenge;
END $$;
CREATE FUNCTION control.stage_ga4_oauth_attempt(p_session bytea,p_generation text,p_site uuid,p_id uuid,p_candidates jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;t app.ga4_oauth_attempts%%ROWTYPE;c jsonb; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF jsonb_typeof(p_candidates) IS DISTINCT FROM 'array' OR jsonb_array_length(p_candidates)>100 THEN RETURN 'invalid'; END IF;
 FOR c IN SELECT value FROM jsonb_array_elements(p_candidates) LOOP
  IF jsonb_typeof(c) IS DISTINCT FROM 'object' OR c - 'resource_name' - 'display_name'<>'{}'::jsonb
  OR c->>'resource_name' IS NULL OR c->>'resource_name' !~ '^properties/[1-9][0-9]{0,19}$'
  OR jsonb_typeof(c->'display_name') IS DISTINCT FROM 'string' OR length(c->>'display_name')>256 THEN RETURN 'invalid'; END IF;
 END LOOP;
 SELECT * INTO t FROM app.ga4_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR t.user_id<>a.user_id OR t.verified_origin<>a.origin OR t.recovery_generation<>p_generation
 OR t.status<>'exchanging' OR t.expires_at<=transaction_timestamp() THEN RETURN 'unavailable'; END IF;
 UPDATE app.ga4_oauth_attempts SET status='selecting',candidates=p_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id;
 RETURN 'staged';
END $$;
CREATE FUNCTION control.ga4_selection(p_session bytea,p_generation text,p_site uuid,p_attempt uuid,p_property text)
RETURNS TABLE(tenant_id uuid,origin text,secret_reference text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN; END IF;
 RETURN QUERY SELECT a.tenant_id,a.origin,'secret://ga4/'||t.id::text FROM app.ga4_oauth_attempts t
 WHERE t.tenant_id=a.tenant_id AND t.site_id=p_site AND t.id=p_attempt AND t.user_id=a.user_id
 AND t.verified_origin=a.origin AND t.status='selecting' AND t.expires_at>transaction_timestamp()
 AND t.recovery_generation=p_generation AND EXISTS(SELECT 1 FROM jsonb_array_elements(t.candidates) c WHERE c->>'resource_name'=p_property);
END $$;
CREATE FUNCTION control.confirm_ga4_binding(p_session bytea,p_generation text,p_site uuid,p_attempt uuid,p_binding uuid,p_property text,p_origin text,p_stream_op uuid,p_stream_hash bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;s record; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site FOR UPDATE;
 SELECT * INTO s FROM control.ga4_selection(p_session,p_generation,p_site,p_attempt,p_property);
 IF s.tenant_id IS NULL OR s.origin IS DISTINCT FROM p_origin THEN RETURN 'wrong_property'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.egress_operations e WHERE e.tenant_id=a.tenant_id AND e.site_id=p_site
 AND e.id=p_stream_op AND e.egress_profile='ga4_admin' AND e.method='GET' AND e.state='observed' AND e.http_status=200
 AND e.response_sha256=p_stream_hash AND split_part(e.request_url,'?',1)='https://analyticsadmin.googleapis.com/v1beta/'||p_property||'/dataStreams') THEN RETURN 'evidence_unavailable'; END IF;
 IF EXISTS(SELECT 1 FROM app.ga4_bindings b WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site
 AND NOT EXISTS(SELECT 1 FROM control.ga4_binding_restrictions r WHERE r.target_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='ga4_binding' AND d.target_id=b.id)) THEN RETURN 'already_bound'; END IF;
 INSERT INTO app.ga4_bindings(tenant_id,site_id,id,attempt_id,actor_user_id,verified_origin,property_resource_name,secret_reference,recovery_generation,stream_operation_id,stream_response_sha256)
 VALUES(a.tenant_id,p_site,p_binding,p_attempt,a.user_id,a.origin,p_property,s.secret_reference,p_generation,p_stream_op,p_stream_hash);
 UPDATE app.ga4_oauth_attempts SET status='confirmed' WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_attempt;
 RETURN 'bound';
END $$;
CREATE FUNCTION control.current_ga4_binding(p_tenant uuid,p_site uuid,p_generation text)
RETURNS TABLE(binding_id uuid,property_resource_name text,secret_reference text,origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o record; BEGIN
 SELECT * INTO o FROM control.verified_site_origin(p_tenant,p_site);
 IF o.outcome<>'verified' THEN RETURN; END IF;
 PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
 RETURN QUERY SELECT b.id,b.property_resource_name,b.secret_reference,b.verified_origin FROM app.ga4_bindings b
 WHERE b.tenant_id=p_tenant AND b.site_id=p_site AND b.verified_origin=o.origin AND b.recovery_generation=p_generation
 AND NOT EXISTS(SELECT 1 FROM control.ga4_binding_restrictions r WHERE r.target_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='ga4_binding' AND d.target_id=b.id);
END $$;

-- Extend existing restrictive vocabularies without replacing their accepted cases.
DO $$ DECLARE t text;n text;e text; BEGIN
 FOREACH t IN ARRAY ARRAY['authority_restriction_outbox','authority_denial_tombstones'] LOOP
  FOREACH n IN ARRAY ARRAY[t||'_target_kind_check',t||'_restriction_kind_check'] LOOP
   SELECT pg_get_expr(conbin,conrelid) INTO e FROM pg_constraint WHERE conrelid=('control.'||t)::regclass AND conname=n;
   IF e IS NULL THEN RAISE EXCEPTION 'missing_authority_constraint'; END IF;
   EXECUTE format('ALTER TABLE control.%%I DROP CONSTRAINT %%I',t,n);
   EXECUTE format('ALTER TABLE control.%%I ADD CONSTRAINT %%I CHECK((%%s) OR (target_kind=''ga4_binding'' AND restriction_kind=''ga4_binding_revoked''))',t,n,e);
  END LOOP;
 END LOOP;
 SELECT pg_get_expr(conbin,conrelid) INTO e FROM pg_constraint WHERE conrelid='control.platform_events'::regclass AND conname='platform_events_contract_check';
 ALTER TABLE control.platform_events DROP CONSTRAINT platform_events_contract_check;
 EXECUTE 'ALTER TABLE control.platform_events ADD CONSTRAINT platform_events_contract_check CHECK(('||e||') OR (event_type=''ga4.binding.revoked'' AND object_kind=''ga4_binding'' AND actor_user_id IS NOT NULL AND reason=''owner_revocation'' AND facts=jsonb_build_object(''schema_version'',1,''restriction_kind'',''ga4_binding_revoked'',''target_id'',object_id)))';
END $$;
CREATE OR REPLACE FUNCTION control.validate_platform_event_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.event_type='identity.session.issued' THEN
  IF NOT EXISTS(SELECT 1 FROM control.identity_sessions s WHERE s.id=NEW.object_id AND s.user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='identity.session.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM control.identity_sessions s WHERE s.id=NEW.object_id AND s.user_id=NEW.actor_user_id AND s.revoked_at IS NOT NULL) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='identity.login.failed' THEN
  IF NOT EXISTS(SELECT 1 FROM control.oidc_login_attempts a WHERE a.id=NEW.object_id AND a.consumed_at IS NOT NULL) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='authority.restriction.acknowledged' THEN
  IF NOT EXISTS(SELECT 1 FROM control.authority_restriction_outbox o WHERE o.event_id=NEW.object_id AND o.actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='recipe.release.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM control.recipe_release_events e WHERE e.id=NEW.id AND e.release_id=NEW.object_id AND e.actor_user_id=NEW.actor_user_id AND e.status='REVOKED' AND e.source='operator') THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='standing.grant.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM app.standing_authorization_revocations r WHERE r.id=NEW.id AND r.grant_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type IN ('slack.binding.revoked','slack.link.revoked') THEN
  IF NOT EXISTS(SELECT 1 FROM control.slack_revocations r WHERE r.event_id=NEW.id AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id AND r.target_kind=NEW.object_kind) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='ga4.binding.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM control.ga4_binding_restrictions r WHERE r.event_id=NEW.id AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSE RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 RETURN NEW;
END $$;
CREATE POLICY platform_ga4_restriction_insert ON control.platform_events FOR INSERT TO signal_migrator
WITH CHECK(event_type='ga4.binding.revoked' AND EXISTS(SELECT 1 FROM control.ga4_binding_restrictions r WHERE r.event_id=platform_events.id AND r.target_id=platform_events.object_id AND r.actor_user_id=platform_events.actor_user_id));
CREATE FUNCTION control.enqueue_ga4_restriction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF NEW.event_type='ga4.binding.revoked' THEN
  INSERT INTO control.authority_restriction_outbox(event_id,scope_kind,actor_user_id,target_kind,target_id,restriction_kind,effective_epoch,event_time,original_facts)
  VALUES(NEW.id,'platform',NEW.actor_user_id,'ga4_binding',NEW.object_id,'ga4_binding_revoked',1,NEW.created_at,NEW.facts);
 END IF; RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.enqueue_ga4_restriction() FROM PUBLIC;
CREATE TRIGGER enqueue_ga4_restriction AFTER INSERT ON control.platform_events FOR EACH ROW EXECUTE FUNCTION control.enqueue_ga4_restriction();
CREATE FUNCTION control.restrict_ga4_binding(p_binding uuid,p_event uuid,p_reason text,p_actor uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.ga4_bindings%%ROWTYPE;r uuid; BEGIN
 SELECT * INTO b FROM app.ga4_bindings WHERE id=p_binding FOR UPDATE;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 INSERT INTO control.ga4_binding_restrictions(event_id,target_id,actor_user_id,reason)
 VALUES(p_event,p_binding,p_actor,p_reason) ON CONFLICT(target_id) DO NOTHING RETURNING event_id INTO r;
 IF r IS NOT NULL THEN
  INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason)
  VALUES(r,'ga4.binding.revoked',p_actor,'ga4_binding',b.id,jsonb_build_object('schema_version',1,'restriction_kind','ga4_binding_revoked','target_id',b.id),'owner_revocation');
 ELSE SELECT event_id INTO r FROM control.ga4_binding_restrictions WHERE target_id=p_binding; END IF;
 RETURN CASE WHEN EXISTS(SELECT 1 FROM control.platform_events WHERE event_type='authority.restriction.acknowledged' AND object_id=r) THEN 'revoked' ELSE 'AUTHORITY_DURABILITY_PENDING' END;
END $$;
CREATE FUNCTION control.revoke_ga4_binding(p_session bytea,p_generation text,p_site uuid,p_binding uuid,p_event uuid)
RETURNS TABLE(outcome text,secret_reference text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.ga4_bindings%%ROWTYPE; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF a.outcome<>'authorized' OR a.role_key<>'owner' OR a.authentication_level<>'mfa' THEN RETURN; END IF;
 SELECT * INTO b FROM app.ga4_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_binding;
 IF NOT FOUND THEN RETURN; END IF;
 RETURN QUERY SELECT control.restrict_ga4_binding(b.id,p_event,'owner_disconnect',a.user_id),b.secret_reference;
END $$;
CREATE FUNCTION control.apply_ga4_binding_denial(p_event uuid,p_target uuid,p_epoch bigint,p_stream uuid,p_position bigint,p_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF session_user<>'signal_authority_dispatcher' OR p_event IS NULL OR p_target IS NULL OR p_epoch IS DISTINCT FROM 1
 OR p_stream IS NULL OR p_position IS NULL OR p_position<1 OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'ga4_replay_denied' USING ERRCODE='42501'; END IF;
 INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,effective_epoch,stream_generation,stream_position,payload_hash)
 VALUES(p_event,'ga4_binding',p_target,'ga4_binding_revoked',1,p_stream,p_position,p_hash) ON CONFLICT(event_id) DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event AND target_kind='ga4_binding' AND target_id=p_target AND effective_epoch=1 AND stream_generation=p_stream AND stream_position=p_position AND payload_hash=p_hash) THEN RAISE EXCEPTION 'ga4_replay_conflict' USING ERRCODE='23505'; END IF;
END $$;

ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check CHECK(egress_profile IN (
'legacy_unqualified','crawl_page','crawl_robots','browser_read','github_rest','github_repository_write','google_oauth_token','google_oauth_revoke','gsc_api','bing_oauth_token','bing_api','jev','model_json','openai_model','openai_assistant','perplexity_assistant','gemini_assistant','slack_bot','slack_oauth','ga4_admin','ga4_data','dataforseo','crawl_key_file','indexnow_submit'));
ALTER FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) RENAME TO bind_before_ga4_egress_profile;
REVOKE ALL ON FUNCTION control.bind_before_ga4_egress_profile(uuid,uuid,uuid,bytea,text) FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(p_tenant uuid,p_site uuid,p_operation uuid,p_hash bytea,p_profile text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE e app.egress_operations%%ROWTYPE; BEGIN
 IF p_profile NOT IN ('ga4_admin','ga4_data') THEN RETURN control.bind_before_ga4_egress_profile(p_tenant,p_site,p_operation,p_hash,p_profile); END IF;
 PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
 SELECT * INTO e FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_operation FOR UPDATE;
 IF NOT FOUND OR e.state<>'dispatched' OR e.request_sha256 IS DISTINCT FROM p_hash OR e.purpose<>'connector' OR NOT e.credentialed
 OR e.max_response_bytes>131072 OR e.request_bytes>4096 OR e.egress_profile NOT IN ('legacy_unqualified',p_profile)
 OR (p_profile='ga4_admin' AND (e.origin<>'https://analyticsadmin.googleapis.com' OR e.method<>'GET'
 OR length(replace(split_part(e.request_url,'&pageToken=',2),'%%3D','='))>512
 OR e.request_url !~ '^https://analyticsadmin[.]googleapis[.]com/v1beta/(accountSummaries|properties/[1-9][0-9]{0,19}/dataStreams)[?]pageSize=50(&pageToken=([A-Za-z0-9_=.-]|%%3D)+)?$'))
 OR (p_profile='ga4_data' AND (e.origin<>'https://analyticsdata.googleapis.com' OR e.method<>'POST'
 OR e.request_url !~ '^https://analyticsdata[.]googleapis[.]com/v1beta/properties/[1-9][0-9]{0,19}:runReport$')) THEN RAISE EXCEPTION 'ga4_profile_denied' USING ERRCODE='22023'; END IF;
 IF e.egress_profile='legacy_unqualified' THEN UPDATE app.egress_operations SET egress_profile=p_profile WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_operation; END IF;
 RETURN 'bound';
END $$;
CREATE FUNCTION control.record_ga4_import_generation(p_tenant uuid,p_site uuid,p_generation text,p_id uuid,p_binding uuid,p_property text,p_start date,p_end date,p_rows jsonb,p_coverage jsonb,p_receipts jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b record;r jsonb; BEGIN
 SELECT * INTO b FROM control.current_ga4_binding(p_tenant,p_site,p_generation);
 IF b.binding_id IS DISTINCT FROM p_binding OR b.property_resource_name IS DISTINCT FROM p_property THEN RETURN 'binding_unavailable'; END IF;
 IF jsonb_typeof(p_receipts) IS DISTINCT FROM 'array' OR jsonb_array_length(p_receipts) NOT BETWEEN 1 AND 5
 OR p_coverage->'complete' IS DISTINCT FROM 'false'::jsonb OR p_coverage->>'missing_data' IS DISTINCT FROM 'unknown_not_zero'
 OR p_coverage->>'requested_start_date' IS DISTINCT FROM p_start::text OR p_coverage->>'requested_end_date' IS DISTINCT FROM p_end::text THEN RETURN 'invalid'; END IF;
 FOR r IN SELECT value FROM jsonb_array_elements(p_receipts) LOOP
  IF NOT EXISTS(SELECT 1 FROM app.egress_operations e WHERE e.tenant_id=p_tenant AND e.site_id=p_site AND e.id=(r->>'operation_id')::uuid
  AND e.egress_profile='ga4_data' AND e.state='observed' AND e.http_status=200 AND e.method='POST'
  AND encode(e.response_sha256,'hex')=r->>'response_sha256' AND e.request_url='https://analyticsdata.googleapis.com/v1beta/'||p_property||':runReport') THEN RETURN 'evidence_unavailable'; END IF;
 END LOOP;
 INSERT INTO app.ga4_import_generations(tenant_id,site_id,id,binding_id,property_resource_name,start_date,end_date,rows,coverage,receipts)
 VALUES(p_tenant,p_site,p_id,p_binding,p_property,p_start,p_end,p_rows,p_coverage,p_receipts);
 RETURN 'recorded';
END $$;
CREATE FUNCTION control.ga4_status(p_session bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b record;g record; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO b FROM control.current_ga4_binding(a.tenant_id,p_site,p_generation);
 SELECT id,coverage,imported_at INTO g FROM app.ga4_import_generations WHERE tenant_id=a.tenant_id AND site_id=p_site AND binding_id=b.binding_id ORDER BY imported_at DESC LIMIT 1;
 RETURN jsonb_build_object('state',CASE WHEN b.binding_id IS NULL THEN 'disconnected' ELSE 'read_only' END,'binding_id',b.binding_id,'property_resource_name',b.property_resource_name,'scope','https://www.googleapis.com/auth/analytics.readonly','generation_id',g.id,'coverage',g.coverage,'imported_at',g.imported_at,
 'selection',(SELECT jsonb_build_object('attempt_id',t.id,'properties',t.candidates) FROM app.ga4_oauth_attempts t WHERE t.tenant_id=a.tenant_id AND t.site_id=p_site AND t.user_id=a.user_id AND t.status='selecting' AND t.verified_origin=a.origin AND t.recovery_generation=p_generation AND t.expires_at>transaction_timestamp() ORDER BY t.expires_at DESC LIMIT 1));
END $$;
CREATE FUNCTION control.ga4_owner_binding(p_session bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid,binding_id uuid,property_resource_name text,secret_reference text,origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN; END IF;
 RETURN QUERY SELECT a.tenant_id,b.binding_id,b.property_resource_name,b.secret_reference,b.origin
 FROM control.current_ga4_binding(a.tenant_id,p_site,p_generation) b;
END $$;
CREATE FUNCTION control.record_ga4_owner_import(p_session bytea,p_generation text,p_site uuid,p_id uuid,p_binding uuid,p_property text,p_start date,p_end date,p_rows jsonb,p_coverage jsonb,p_receipts jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.ga4_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_binding FOR UPDATE;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 RETURN control.record_ga4_import_generation(a.tenant_id,p_site,p_generation,p_id,p_binding,p_property,p_start,p_end,p_rows,p_coverage,p_receipts);
END $$;
CREATE FUNCTION control.ga4_owner_reauth(p_session bytea,p_generation text,p_site uuid,p_binding uuid,p_event uuid,p_egress uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b record;a record; BEGIN
 SELECT * INTO a FROM control.ga4_owner_context(p_session,p_generation,p_site);
 SELECT * INTO b FROM control.ga4_owner_binding(p_session,p_generation,p_site);
 IF b.binding_id IS DISTINCT FROM p_binding THEN RETURN 'denied'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.egress_operations e WHERE e.tenant_id=b.tenant_id AND e.site_id=p_site AND e.id=p_egress
 AND e.state='observed' AND ((e.request_url='https://oauth2.googleapis.com/token' AND e.egress_profile='google_oauth_token' AND e.http_status IN (200,400,401,403))
 OR (e.request_url='https://analyticsdata.googleapis.com/v1beta/'||b.property_resource_name||':runReport' AND e.egress_profile='ga4_data' AND e.http_status IN (401,403)))) THEN RETURN 'evidence_unavailable'; END IF;
 RETURN control.restrict_ga4_binding(p_binding,p_event,'reauth_required',a.user_id);
END $$;
REVOKE ALL ON FUNCTION control.ga4_owner_binding(bytea,text,uuid),control.record_ga4_owner_import(bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb),control.ga4_owner_reauth(bytea,text,uuid,uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.ga4_owner_binding(bytea,text,uuid),control.record_ga4_owner_import(bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb),control.ga4_owner_reauth(bytea,text,uuid,uuid,uuid,uuid) TO signal_identity;
REVOKE ALL ON FUNCTION control.ga4_owner_context(bytea,text,uuid),control.begin_ga4_oauth_attempt(bytea,text,uuid,uuid,bytea,text,text),control.consume_ga4_oauth_attempt(bytea,text,uuid,uuid,bytea,text),control.stage_ga4_oauth_attempt(bytea,text,uuid,uuid,jsonb),control.ga4_selection(bytea,text,uuid,uuid,text),control.confirm_ga4_binding(bytea,text,uuid,uuid,uuid,text,text,uuid,bytea),control.current_ga4_binding(uuid,uuid,text),control.restrict_ga4_binding(uuid,uuid,text,uuid),control.revoke_ga4_binding(bytea,text,uuid,uuid,uuid),control.apply_ga4_binding_denial(uuid,uuid,bigint,uuid,bigint,text),control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text),control.record_ga4_import_generation(uuid,uuid,text,uuid,uuid,text,date,date,jsonb,jsonb,jsonb),control.ga4_status(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.begin_ga4_oauth_attempt(bytea,text,uuid,uuid,bytea,text,text),control.consume_ga4_oauth_attempt(bytea,text,uuid,uuid,bytea,text),control.stage_ga4_oauth_attempt(bytea,text,uuid,uuid,jsonb),control.ga4_selection(bytea,text,uuid,uuid,text),control.confirm_ga4_binding(bytea,text,uuid,uuid,uuid,text,text,uuid,bytea),control.revoke_ga4_binding(bytea,text,uuid,uuid,uuid),control.ga4_status(bytea,text,uuid) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.current_ga4_binding(uuid,uuid,text),control.record_ga4_import_generation(uuid,uuid,text,uuid,uuid,text,date,date,jsonb,jsonb,jsonb) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.apply_ga4_binding_denial(uuid,uuid,bigint,uuid,bigint,text) TO signal_authority_dispatcher;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;
