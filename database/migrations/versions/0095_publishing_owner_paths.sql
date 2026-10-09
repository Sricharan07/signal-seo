CREATE FUNCTION control.owner_publishing_options(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o record; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
 SELECT * INTO o FROM control.verified_site_origin(a.tenant_id,p_site);
 IF o.outcome IS DISTINCT FROM 'verified' THEN RETURN NULL; END IF;
 RETURN jsonb_build_object(
 'drafts',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (
   SELECT r.draft_id,r.payload->'article'->'title'->>'text' AS title
   FROM app.content_draft_results r
   JOIN app.content_draft_intents d ON d.tenant_id=r.tenant_id AND d.site_id=r.site_id AND d.id=r.draft_id
   JOIN app.content_briefs b ON b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.id=d.brief_id
   WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND b.payload->>'kind'='new_article'
     AND r.payload->>'state' IN ('grounded','owner_required')
     AND r.payload->'originality'->>'state'='original'
     AND EXISTS(SELECT 1 FROM app.content_brief_acceptances accepted
       WHERE accepted.tenant_id=b.tenant_id AND accepted.site_id=b.site_id AND accepted.brief_id=b.id)
     AND NOT EXISTS(SELECT 1 FROM app.content_briefs newer
       WHERE newer.tenant_id=b.tenant_id AND newer.site_id=b.site_id AND newer.supersedes_id=b.id)
   ORDER BY r.created_at DESC,r.draft_id LIMIT 100
 ) x),'[]'::jsonb),
 'articles',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (
   SELECT c.id AS candidate_id,encode(c.revision_sha256,'hex') AS source_sha256,
     r.payload->'article'->'title'->>'text' AS title
   FROM app.content_candidates c
   JOIN app.content_draft_results r ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.draft_id=c.draft_id
   JOIN app.content_delivery_decisions approved ON approved.tenant_id=c.tenant_id AND approved.site_id=c.site_id AND approved.candidate_id=c.id
   WHERE c.tenant_id=a.tenant_id AND c.site_id=p_site AND c.manifest->>'work_type'='new_article'
     AND approved.owner_user_id=a.user_id AND approved.revision_sha256=c.revision_sha256
     AND approved.recovery_generation=p_generation AND approved.membership_epoch=a.membership_epoch
     AND approved.site_epoch=a.site_authorization_epoch AND approved.created_at>transaction_timestamp()-interval '24 hours'
     AND NOT EXISTS(SELECT 1 FROM app.content_candidate_reviews review WHERE review.tenant_id=c.tenant_id
       AND review.site_id=c.site_id AND review.candidate_id=c.id AND review.decision<>'approved')
     AND NOT EXISTS(SELECT 1 FROM app.content_draft_intents d JOIN app.content_briefs newer
       ON newer.tenant_id=d.tenant_id AND newer.site_id=d.site_id AND newer.supersedes_id=d.brief_id
       WHERE d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.id=c.draft_id)
   ORDER BY c.created_at DESC,c.id LIMIT 100
 ) x),'[]'::jsonb));
END; $$;
REVOKE ALL ON FUNCTION control.owner_publishing_options(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.owner_publishing_options(bytea,text,uuid) TO signal_api;

ALTER FUNCTION control.webflow_command(bytea,text,uuid,text,jsonb) RENAME TO webflow_command_before_owner_paths;
REVOKE ALL ON FUNCTION control.webflow_command_before_owner_paths(bytea,text,uuid,text,jsonb) FROM signal_api;
CREATE FUNCTION control.webflow_command(p_hash bytea,p_generation text,p_site uuid,p_action text,p jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; auth_at timestamptz; options jsonb; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
   OR a.authentication_level IS DISTINCT FROM 'mfa' THEN RETURN NULL; END IF;
 IF p_action='options' THEN RETURN control.owner_publishing_options(p_hash,p_generation,p_site); END IF;
 IF p_action IN ('preflight','begin_oauth','consume_oauth','bind','source','seal','review','revoke') THEN
   SELECT s.auth_time INTO auth_at FROM app.sessions s WHERE s.session_token_hash=p_hash;
   IF auth_at IS NULL OR auth_at<=transaction_timestamp()-interval '5 minutes'
     OR auth_at>transaction_timestamp() THEN RETURN jsonb_build_object('state','step_up_required'); END IF;
 END IF;
 IF p_action='preflight' THEN
   options:=control.owner_publishing_options(p_hash,p_generation,p_site);
   IF options IS NULL OR EXISTS(SELECT 1 FROM app.site_weekly_control WHERE tenant_id=a.tenant_id AND site_id=p_site AND paused)
     THEN RETURN NULL; END IF;
   RETURN jsonb_build_object('state','ready');
 END IF;
 IF p_action IN ('source','seal') THEN
   options:=control.owner_publishing_options(p_hash,p_generation,p_site);
   IF options IS NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(options->'articles') article
     WHERE article->>'candidate_id'=p->>'candidate_id' AND article->>'source_sha256'=p->>'source_sha256')
     THEN RETURN NULL; END IF;
 END IF;
 RETURN control.webflow_command_before_owner_paths(p_hash,p_generation,p_site,p_action,p);
END; $$;
REVOKE ALL ON FUNCTION control.webflow_command(bytea,text,uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.webflow_command(bytea,text,uuid,text,jsonb) TO signal_api;

-- Owner onboarding permits only OAuth, revocation and the four validation reads.
-- The pre-existing draft-write profile and its lab gate are not changed.
ALTER FUNCTION control.owner_connector_request_allowed(text,text,text) RENAME TO owner_request_before_publishing;
CREATE FUNCTION control.owner_connector_request_allowed(p_profile text,p_method text,p_url text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT control.owner_request_before_publishing(p_profile,p_method,p_url) OR coalesce(CASE p_profile
   WHEN 'webflow_oauth' THEN p_method='POST' AND p_url='https://api.webflow.com/oauth/access_token'
   WHEN 'webflow_revoke' THEN p_method='POST' AND p_url='https://webflow.com/oauth/revoke_authorization'
   WHEN 'webflow' THEN p_method='GET' AND (
     p_url='https://api.webflow.com/v2/token/introspect'
     OR p_url ~ '^https://api[.]webflow[.]com/v2/sites/[0-9a-f]{24}/(custom_domains|collections)$'
     OR p_url ~ '^https://api[.]webflow[.]com/v2/collections/[0-9a-f]{24}$')
   ELSE false END,false);
$$;
REVOKE ALL ON FUNCTION control.owner_connector_request_allowed(text,text,text) FROM PUBLIC;
DO $$ DECLARE definition text; BEGIN
 SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
 WHERE conrelid='app.owner_connector_egress_operations'::regclass AND conname='owner_connector_egress_operations_profile_check';
 ALTER TABLE app.owner_connector_egress_operations DROP CONSTRAINT owner_connector_egress_operations_profile_check;
 EXECUTE 'ALTER TABLE app.owner_connector_egress_operations ADD CONSTRAINT owner_connector_egress_operations_profile_check CHECK ('
   ||substring(definition FROM 8 FOR length(definition)-8)||' OR profile IN (''webflow'',''webflow_oauth'',''webflow_revoke''))';
END; $$;

CREATE FUNCTION control.webflow_owner_egress_allowed(p_hash bytea,p_generation text,p_site uuid,p_profile text,p_url text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; auth_at timestamptz; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 SELECT auth_time INTO auth_at FROM app.sessions WHERE session_token_hash=p_hash;
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
   OR a.authentication_level IS DISTINCT FROM 'mfa' OR auth_at IS NULL
   OR auth_at<=clock_timestamp()-interval '5 minutes' OR auth_at>clock_timestamp() THEN RETURN false; END IF;
 IF p_profile='webflow_revoke' THEN
   RETURN EXISTS(SELECT 1 FROM app.webflow_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND revoked_at IS NOT NULL);
 END IF;
 RETURN EXISTS(SELECT 1 FROM app.webflow_oauth_attempts attempt
   WHERE attempt.tenant_id=a.tenant_id AND attempt.site_id=p_site AND attempt.actor_id=a.user_id
     AND attempt.recovery_generation=p_generation AND attempt.created_at>clock_timestamp()-interval '10 minutes'
     AND attempt.consumed_at IS NOT NULL
     AND NOT EXISTS(SELECT 1 FROM app.webflow_bindings binding WHERE binding.tenant_id=attempt.tenant_id
       AND binding.site_id=attempt.site_id AND binding.attempt_id=attempt.id)
     AND (p_profile='webflow_oauth' OR p_url IN (
       'https://api.webflow.com/v2/token/introspect',
       'https://api.webflow.com/v2/sites/'||attempt.provider_site||'/custom_domains',
       'https://api.webflow.com/v2/sites/'||attempt.provider_site||'/collections',
       'https://api.webflow.com/v2/collections/'||attempt.collection_id)));
END; $$;
REVOKE ALL ON FUNCTION control.webflow_owner_egress_allowed(bytea,text,uuid,text,text) FROM PUBLIC;
DO $$ DECLARE definition text; marker text; BEGIN
 SELECT pg_get_functiondef('control.begin_owner_connector_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer)'::regprocedure) INTO definition;
 marker:='IF p_profile=''pagespeed'' AND control.pagespeed_admission_allowed(';
 IF position(marker IN definition)=0 THEN RAISE EXCEPTION 'owner_egress_contract_changed'; END IF;
 definition:=replace(definition,'''github_rest'',''gsc_api'',''pagespeed''','''github_rest'',''gsc_api'',''pagespeed'',''webflow''');
 definition:=replace(definition,marker,
   'IF p_profile IN (''webflow'',''webflow_oauth'',''webflow_revoke'') AND control.webflow_owner_egress_allowed(p_session_hash,p_generation,p_site_id,p_profile,p_target_url) IS NOT TRUE THEN RETURN QUERY SELECT ''denied''::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF; '||marker);
 EXECUTE definition;
END; $$;
