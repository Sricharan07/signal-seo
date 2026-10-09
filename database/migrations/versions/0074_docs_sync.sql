CREATE TABLE app.docs_oauth_attempts (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    user_id uuid NOT NULL, recovery_generation text NOT NULL, verified_origin text NOT NULL,
    state_sha256 bytea NOT NULL CHECK(octet_length(state_sha256)=32),
    code_challenge text NOT NULL CHECK(code_challenge ~ '^[A-Za-z0-9_-]{43}$'),
    redirect_uri text NOT NULL CHECK(length(redirect_uri) BETWEEN 12 AND 2048),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '10 minutes',
    consumed_at timestamptz, bound_at timestamptz,
    PRIMARY KEY(tenant_id,site_id,id), UNIQUE(state_sha256),
    FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id),
    CHECK(expires_at=created_at+interval '10 minutes')
);
CREATE TABLE app.docs_bindings (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    attempt_id uuid NOT NULL, user_id uuid NOT NULL, recovery_generation text NOT NULL,
    verified_origin text NOT NULL, secret_reference text NOT NULL,
    granted_scope text NOT NULL CHECK(granted_scope='https://www.googleapis.com/auth/drive.file'),
    state text NOT NULL DEFAULT 'ready' CHECK(state IN ('ready','degraded','revoked')),
    reason text CHECK(reason IN ('owner_disconnect','provider_reauthorization','scope_changed','refresh_failed')),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id), UNIQUE(id),
    FOREIGN KEY(tenant_id,site_id,attempt_id) REFERENCES app.docs_oauth_attempts(tenant_id,site_id,id),
    CHECK(secret_reference='secret://google-docs/'||attempt_id::text)
);
CREATE UNIQUE INDEX docs_one_current_binding ON app.docs_bindings(tenant_id,site_id)
WHERE state IN ('ready','degraded');
CREATE TABLE app.docs_sources (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    binding_id uuid NOT NULL, file_id text NOT NULL CHECK(file_id ~ '^[A-Za-z0-9_-]+$' AND length(file_id) BETWEEN 10 AND 200),
    selected_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id), UNIQUE(tenant_id,site_id,binding_id,file_id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.docs_bindings(tenant_id,site_id,id)
);
CREATE TABLE app.docs_source_versions (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    source_id uuid NOT NULL, sequence_number bigint NOT NULL CHECK(sequence_number>0),
    event_kind text NOT NULL CHECK(event_kind IN ('version','withdrawn')),
    provider_version text CHECK(provider_version ~ '^[0-9]+$' AND length(provider_version)<=100),
    modified_time timestamptz, document_id uuid,
    reason text CHECK(reason IN ('provider_removed','provider_unshared')),
    observed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id), UNIQUE(tenant_id,site_id,source_id,sequence_number),
    UNIQUE(tenant_id,site_id,source_id,provider_version,modified_time),
    FOREIGN KEY(tenant_id,site_id,source_id) REFERENCES app.docs_sources(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,document_id) REFERENCES app.brand_documents(tenant_id,site_id,id),
    CHECK((event_kind='version' AND document_id IS NOT NULL AND provider_version IS NOT NULL
        AND modified_time IS NOT NULL AND reason IS NULL)
      OR(event_kind='withdrawn' AND document_id IS NULL AND provider_version IS NULL
        AND modified_time IS NULL AND reason IS NOT NULL))
);
CREATE TABLE control.docs_revocations (
    event_id uuid PRIMARY KEY, binding_id uuid NOT NULL, actor_user_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TRIGGER docs_revocations_immutable BEFORE UPDATE OR DELETE ON control.docs_revocations
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.docs_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.docs_revocations FORCE ROW LEVEL SECURITY;
CREATE POLICY docs_revocations_owner ON control.docs_revocations TO signal_migrator USING(true) WITH CHECK(true);

DO $$ DECLARE n text; BEGIN
    FOREACH n IN ARRAY ARRAY['docs_oauth_attempts','docs_bindings','docs_sources','docs_source_versions'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',n);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',n);
        EXECUTE format('CREATE POLICY docs_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',n);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_crawl_ingest,signal_crawl_admission',n);
    END LOOP;
END $$;
CREATE TRIGGER docs_sources_immutable BEFORE UPDATE OR DELETE ON app.docs_sources FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER docs_versions_immutable BEFORE UPDATE OR DELETE ON app.docs_source_versions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.docs_owner(p_session bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid,user_id uuid,origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o record; BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' OR a.authentication_level<>'mfa' THEN RETURN; END IF;
    SELECT * INTO o FROM control.verified_site_origin(a.tenant_id,p_site);
    IF o.outcome<>'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT a.tenant_id,a.user_id,o.origin;
END $$;
CREATE FUNCTION control.begin_docs_oauth(p_session bytea,p_generation text,p_site uuid,p_id uuid,
    p_state bytea,p_challenge text,p_redirect text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
    SELECT * INTO a FROM control.docs_owner(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    INSERT INTO app.docs_oauth_attempts(tenant_id,site_id,id,user_id,recovery_generation,
        verified_origin,state_sha256,code_challenge,redirect_uri)
    VALUES(a.tenant_id,p_site,p_id,a.user_id,p_generation,a.origin,p_state,p_challenge,p_redirect);
    RETURN 'created';
END $$;
CREATE FUNCTION control.consume_docs_oauth(p_session bytea,p_generation text,p_site uuid,p_id uuid,
    p_state bytea,p_redirect text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; challenge text; BEGIN
    SELECT * INTO a FROM control.docs_owner(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE app.docs_oauth_attempts SET consumed_at=transaction_timestamp()
      WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id AND user_id=a.user_id
        AND recovery_generation=p_generation AND verified_origin=a.origin AND state_sha256=p_state
        AND redirect_uri=p_redirect AND consumed_at IS NULL AND expires_at>transaction_timestamp()
      RETURNING code_challenge INTO challenge;
    RETURN challenge;
END $$;
CREATE FUNCTION control.docs_oauth_current(p_session bytea,p_generation text,p_site uuid,p_attempt uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
    SELECT * INTO a FROM control.docs_owner(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN false; END IF;
    RETURN EXISTS(SELECT 1 FROM app.docs_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site
      AND id=p_attempt AND user_id=a.user_id AND verified_origin=a.origin AND recovery_generation=p_generation
      AND consumed_at IS NOT NULL AND bound_at IS NULL AND expires_at>transaction_timestamp());
END $$;
CREATE FUNCTION control.bind_docs_oauth(p_session bytea,p_generation text,p_site uuid,p_attempt uuid,
    p_binding uuid,p_files jsonb,p_scope text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o record; f text; BEGIN
    SELECT * INTO a FROM control.docs_owner(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(a.tenant_id::text||p_site::text,100));
    SELECT * INTO o FROM app.docs_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site
        AND id=p_attempt AND user_id=a.user_id AND verified_origin=a.origin AND recovery_generation=p_generation
        AND consumed_at IS NOT NULL AND bound_at IS NULL AND expires_at>transaction_timestamp() FOR UPDATE;
    IF NOT FOUND OR p_scope IS DISTINCT FROM 'https://www.googleapis.com/auth/drive.file'
       OR jsonb_typeof(p_files) IS DISTINCT FROM 'array' THEN RETURN 'rejected'; END IF;
    IF jsonb_array_length(p_files) NOT BETWEEN 1 AND 20 OR EXISTS(
        SELECT 1 FROM jsonb_array_elements(p_files) x WHERE jsonb_typeof(x)<>'string'
          OR x#>>'{}' !~ '^[A-Za-z0-9_-]+$' OR length(x#>>'{}') NOT BETWEEN 10 AND 200)
       OR (SELECT count(DISTINCT x) FROM jsonb_array_elements(p_files) x)<>jsonb_array_length(p_files)
       OR EXISTS(SELECT 1 FROM app.docs_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND state IN ('ready','degraded'))
       OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='docs_binding' AND target_id=p_binding)
    THEN RETURN 'rejected'; END IF;
    INSERT INTO app.docs_bindings(tenant_id,site_id,id,attempt_id,user_id,recovery_generation,
        verified_origin,secret_reference,granted_scope)
    VALUES(a.tenant_id,p_site,p_binding,p_attempt,a.user_id,p_generation,a.origin,
        'secret://google-docs/'||p_attempt::text,p_scope);
    FOR f IN SELECT jsonb_array_elements_text(p_files) LOOP
        INSERT INTO app.docs_sources VALUES(a.tenant_id,p_site,gen_random_uuid(),p_binding,f,transaction_timestamp());
    END LOOP;
    UPDATE app.docs_oauth_attempts SET bound_at=transaction_timestamp() WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_attempt;
    RETURN 'bound';
END $$;
CREATE FUNCTION control.docs_binding_packet(p_session bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b record; o record; BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' OR a.authentication_level<>'mfa' THEN
      RAISE EXCEPTION 'docs_owner_denied' USING ERRCODE='42501'; END IF;
    SELECT * INTO o FROM control.verified_site_origin(a.tenant_id,p_site);
    SELECT * INTO b FROM app.docs_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site
        ORDER BY created_at DESC LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('availability','unbound','sources','[]'::jsonb); END IF;
    RETURN jsonb_build_object('binding_id',b.id,'availability',
      CASE WHEN b.recovery_generation<>p_generation OR o.outcome IS DISTINCT FROM 'verified'
        OR b.verified_origin IS DISTINCT FROM o.origin OR EXISTS(
        SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='docs_binding' AND target_id=b.id)
        THEN 'revoked' ELSE b.state END,'reason',b.reason,'secret_reference',b.secret_reference,
      'tenant_id',a.tenant_id,'sources',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (
        SELECT s.id AS source_id,s.file_id,v.document_id,v.provider_version,v.modified_time,v.observed_at,
          coalesce(v.event_kind,'pending') AS state,coalesce((SELECT jsonb_agg(f.id) FROM app.business_brain_facts f
            JOIN app.docs_source_versions d ON d.tenant_id=f.tenant_id AND d.site_id=f.site_id AND d.document_id=f.document_id
            WHERE d.source_id=s.id AND v.event_kind='withdrawn'),'[]'::jsonb) AS review_fact_ids
        FROM app.docs_sources s LEFT JOIN LATERAL(SELECT * FROM app.docs_source_versions v
          WHERE v.tenant_id=s.tenant_id AND v.site_id=s.site_id AND v.source_id=s.id ORDER BY sequence_number DESC LIMIT 1) v ON true
        WHERE s.tenant_id=a.tenant_id AND s.site_id=p_site AND s.binding_id=b.id) x),'[]'::jsonb));
END $$;
CREATE FUNCTION control.degrade_docs_binding(p_session bytea,p_generation text,p_site uuid,p_binding uuid,p_reason text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
    IF p_reason NOT IN ('provider_reauthorization','scope_changed','refresh_failed') THEN RETURN 'rejected'; END IF;
    SELECT * INTO a FROM control.docs_owner(p_session,p_generation,p_site); IF NOT FOUND THEN RETURN 'denied'; END IF;
    UPDATE app.docs_bindings SET state='degraded',reason=p_reason WHERE tenant_id=a.tenant_id AND site_id=p_site
        AND id=p_binding AND recovery_generation=p_generation AND state='ready';
    RETURN 'degraded';
END $$;
CREATE FUNCTION control.record_docs_source_version(p_session bytea,p_generation text,p_site uuid,p_binding uuid,
    p_source uuid,p_version text,p_modified timestamptz,p_document uuid,p_reason text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; n bigint; BEGIN
    SELECT * INTO a FROM control.docs_owner(p_session,p_generation,p_site); IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM 1 FROM app.docs_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_binding
      AND recovery_generation=p_generation AND verified_origin=a.origin AND state='ready' FOR UPDATE;
    IF NOT FOUND OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='docs_binding' AND target_id=p_binding)
      OR NOT EXISTS(SELECT 1 FROM app.docs_sources WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_source AND binding_id=p_binding)
      OR EXISTS(SELECT 1 FROM app.docs_source_versions WHERE tenant_id=a.tenant_id AND site_id=p_site AND source_id=p_source AND event_kind='withdrawn')
    THEN RETURN 'unavailable'; END IF;
    IF p_version IS NOT NULL AND EXISTS(SELECT 1 FROM app.docs_source_versions WHERE tenant_id=a.tenant_id AND site_id=p_site
        AND source_id=p_source AND provider_version=p_version AND modified_time=p_modified) THEN RETURN 'unchanged'; END IF;
    SELECT coalesce(max(sequence_number),0)+1 INTO n FROM app.docs_source_versions WHERE tenant_id=a.tenant_id AND site_id=p_site AND source_id=p_source;
    INSERT INTO app.docs_source_versions VALUES(a.tenant_id,p_site,gen_random_uuid(),p_source,n,
        CASE WHEN p_reason IS NULL THEN 'version' ELSE 'withdrawn' END,p_version,p_modified,p_document,p_reason,transaction_timestamp());
    RETURN CASE WHEN p_reason IS NULL THEN 'recorded' ELSE 'withdrawn' END;
END $$;

-- Only broaden existing enumerations by the new closed profiles and restriction pair.
DO $$ DECLARE t text; c record; expr text; BEGIN
    FOREACH t IN ARRAY ARRAY['app.egress_operations','control.authority_restriction_outbox','control.authority_denial_tombstones','control.platform_events'] LOOP
      FOR c IN SELECT conname,pg_get_expr(conbin,conrelid) AS expression FROM pg_constraint
        WHERE conrelid=t::regclass AND contype='c' AND conname IN (
          'egress_operations_egress_profile_check','authority_restriction_outbox_target_kind_check',
          'authority_restriction_outbox_restriction_kind_check','authority_denial_tombstones_target_kind_check',
          'authority_denial_tombstones_restriction_kind_check','platform_events_contract_check') LOOP
        expr:=CASE WHEN t='app.egress_operations' THEN 'egress_profile IN (''drive_metadata'',''drive_export'')'
          WHEN t='control.platform_events' THEN 'event_type=''docs.binding.revoked'' AND object_kind=''docs_binding'' AND actor_user_id IS NOT NULL AND reason=''owner_revocation'' AND facts=jsonb_build_object(''schema_version'',1,''restriction_kind'',''docs_binding_revoked'',''target_id'',object_id)'
          ELSE 'target_kind=''docs_binding'' AND restriction_kind=''docs_binding_revoked''' END;
        EXECUTE format('ALTER TABLE %%s DROP CONSTRAINT %%I',t,c.conname);
        EXECUTE format('ALTER TABLE %%s ADD CONSTRAINT %%I CHECK((%%s) OR (%%s))',t,c.conname,c.expression,expr);
      END LOOP;
    END LOOP;
END $$;
CREATE FUNCTION control.bind_docs_egress_profile(p_tenant uuid,p_site uuid,p_operation uuid,p_digest bytea,p_profile text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF p_profile NOT IN ('drive_metadata','drive_export') THEN RAISE EXCEPTION 'docs_profile_denied' USING ERRCODE='22023'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true); PERFORM set_config('signal.site_id',p_site::text,true);
    UPDATE app.egress_operations SET egress_profile=p_profile WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_operation
      AND request_sha256=p_digest AND state='dispatched' AND purpose='connector' AND method='GET'
      AND credentialed AND origin='https://www.googleapis.com' AND egress_profile='legacy_unqualified';
    IF NOT FOUND AND NOT EXISTS(SELECT 1 FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_operation
      AND request_sha256=p_digest AND egress_profile=p_profile AND state='dispatched') THEN
        RAISE EXCEPTION 'docs_profile_conflict' USING ERRCODE='22023'; END IF;
    RETURN 'bound';
END $$;

-- Extend the existing journal triggers without replacing any accepted branches.
DO $$ DECLARE body text; BEGIN
    SELECT pg_get_functiondef('control.validate_platform_event_reference()'::regprocedure) INTO body;
    -- Preserve the accepted WordPress guard while normalizing its exact sentinel.
    IF position(' ELSE RAISE EXCEPTION ''platform event object reference is invalid'' USING ERRCODE=''23503''; END IF;' IN body)>0 THEN
      body:=replace(body,' ELSE RAISE EXCEPTION ''platform event object reference is invalid'' USING ERRCODE=''23503''; END IF;',
        '    ELSE'||chr(10)||'        RAISE EXCEPTION ''platform event object reference is invalid'' USING ERRCODE=''23503'';'||chr(10)||'    END IF;');
    END IF;
    IF position('    ELSE'||chr(10)||'        RAISE EXCEPTION ''platform event object reference is invalid''' IN body)=0 THEN
      RAISE EXCEPTION 'docs_journal_upgrade_conflict'; END IF;
    body:=replace(body,'    ELSE'||chr(10)||'        RAISE EXCEPTION ''platform event object reference is invalid''',
      '    ELSIF NEW.event_type = ''docs.binding.revoked'' THEN'||chr(10)||
      '        IF NOT EXISTS(SELECT 1 FROM control.docs_revocations WHERE event_id=NEW.id AND binding_id=NEW.object_id AND actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION ''invalid_docs_revocation'' USING ERRCODE=''23503''; END IF;'||chr(10)||
      '    ELSE'||chr(10)||'        RAISE EXCEPTION ''platform event object reference is invalid''');
    EXECUTE body;
    SELECT pg_get_functiondef('control.enqueue_session_restriction()'::regprocedure) INTO body;
    IF position('    ELSE RETURN NEW; END IF;' IN body)=0 THEN RAISE EXCEPTION 'docs_outbox_upgrade_conflict'; END IF;
    body:=replace(body,'    ELSE RETURN NEW; END IF;',
      '    ELSIF NEW.event_type=''docs.binding.revoked'' THEN SELECT 1 INTO v_epoch FROM control.docs_revocations WHERE event_id=NEW.id AND binding_id=NEW.object_id AND actor_user_id=NEW.actor_user_id; v_kind:=''docs_binding''; v_restriction:=''docs_binding_revoked'';'||chr(10)||'    ELSE RETURN NEW; END IF;');
    EXECUTE body;
END $$;
CREATE POLICY platform_docs_revocation_insert ON control.platform_events FOR INSERT TO signal_migrator
WITH CHECK(event_type='docs.binding.revoked' AND EXISTS(SELECT 1 FROM control.docs_revocations r
    WHERE r.event_id=platform_events.id AND r.binding_id=platform_events.object_id AND r.actor_user_id=platform_events.actor_user_id));
CREATE FUNCTION control.revoke_docs_binding(p_session bytea,p_generation text,p_site uuid,p_binding uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; e uuid; BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' OR a.authentication_level<>'mfa' THEN RETURN 'denied'; END IF;
    PERFORM 1 FROM app.docs_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_binding FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable'; END IF;
    UPDATE app.docs_bindings SET state='revoked',reason='owner_disconnect' WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_binding;
    SELECT event_id INTO e FROM control.docs_revocations WHERE binding_id=p_binding LIMIT 1;
    IF e IS NULL THEN
      e:=gen_random_uuid(); INSERT INTO control.docs_revocations VALUES(e,p_binding,a.user_id,transaction_timestamp());
      INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason)
      VALUES(e,'docs.binding.revoked',a.user_id,'docs_binding',p_binding,jsonb_build_object(
        'schema_version',1,'restriction_kind','docs_binding_revoked','target_id',p_binding),'owner_revocation');
    END IF;
    RETURN CASE WHEN EXISTS(SELECT 1 FROM control.platform_events WHERE event_type='authority.restriction.acknowledged' AND object_id=e)
      THEN 'revoked_durable' ELSE 'revoked_pending' END;
END $$;
CREATE FUNCTION control.apply_docs_binding_denial(p_event uuid,p_binding uuid,p_epoch bigint,p_generation uuid,p_position bigint,p_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_authority_dispatcher' OR p_epoch<>1 OR p_event IS NULL OR p_binding IS NULL
      OR p_generation IS NULL OR p_position<1 OR p_hash !~ '^[0-9a-f]{64}$'
    THEN RAISE EXCEPTION 'docs_replay_denied' USING ERRCODE='42501'; END IF;
    INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,effective_epoch,
      stream_generation,stream_position,payload_hash) VALUES(p_event,'docs_binding',p_binding,'docs_binding_revoked',1,p_generation,p_position,p_hash)
      ON CONFLICT(event_id) DO NOTHING;
    IF NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event AND target_kind='docs_binding'
      AND target_id=p_binding AND effective_epoch=p_epoch AND stream_generation=p_generation AND stream_position=p_position AND payload_hash=p_hash)
    THEN RAISE EXCEPTION 'docs_replay_conflict' USING ERRCODE='23505'; END IF;
END $$;

REVOKE ALL ON control.docs_revocations FROM PUBLIC,signal_api,signal_identity;
CREATE FUNCTION control.docs_document_withdrawn(p_tenant uuid,p_site uuid,p_document uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS(SELECT 1 FROM app.docs_source_versions d JOIN app.docs_source_versions w
      ON w.tenant_id=d.tenant_id AND w.site_id=d.site_id AND w.source_id=d.source_id AND w.event_kind='withdrawn'
      WHERE d.tenant_id=p_tenant AND d.site_id=p_site AND d.document_id=p_document)
$$;
REVOKE ALL ON FUNCTION control.docs_document_withdrawn(uuid,uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.business_brain_read(p_session_hash bytea,p_generation text,p_site_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object(
 'facts',coalesce((SELECT jsonb_agg(to_jsonb(f)||jsonb_build_object('decision_id',b.decision_id,'extraction_id',b.extraction_id,
   'source_review_required',control.docs_document_withdrawn(v.tenant_id,p_site_id,b.document_id)))
   FROM control.list_business_brain_facts(p_session_hash,p_generation,p_site_id,NULL) f JOIN app.business_brain_facts b
   ON b.tenant_id=v.tenant_id AND b.site_id=p_site_id AND b.id=f.fact_id),'[]'::jsonb),
 'voice',(SELECT to_jsonb(x) FROM (SELECT id AS profile_id,profile,supersedes_id,created_at FROM app.business_brain_voice_profiles p WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site_id AND NOT EXISTS(SELECT 1 FROM app.business_brain_voice_profiles n WHERE n.tenant_id=p.tenant_id AND n.site_id=p.site_id AND n.supersedes_id=p.id) ORDER BY created_at DESC,id DESC LIMIT 1) x),
 'extractions',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT e.id AS extraction_id,e.source_kind,e.source_id,e.decision_id,coalesce(r.state,'outcome_unknown') AS state,r.page_type,d.fallback,d.provider,r.reason FROM app.business_brain_extractions e LEFT JOIN app.business_brain_extraction_results r ON r.tenant_id=e.tenant_id AND r.site_id=e.site_id AND r.extraction_id=e.id LEFT JOIN app.decision_records d ON d.tenant_id=e.tenant_id AND d.site_id=e.site_id AND d.id=e.decision_id WHERE e.tenant_id=v.tenant_id AND e.site_id=p_site_id ORDER BY e.created_at DESC LIMIT 100) x),'[]'::jsonb));
END $$;

CREATE OR REPLACE FUNCTION control.approved_business_brain_facts(p_session_hash bytea,p_generation text,p_site_id uuid)
RETURNS TABLE(fact_id uuid,category text,statement text,source_kind text,page_evidence_id uuid,document_id uuid,extracted_range jsonb,owner_membership_id uuid,created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_session_hash,p_generation,p_site_id); IF v.tenant_id IS NULL THEN RETURN; END IF;
 RETURN QUERY SELECT f.fact_id,f.category,f.statement,f.source_kind,f.page_evidence_id,f.document_id,f.extracted_range,f.owner_membership_id,f.created_at
 FROM control.list_business_brain_facts(p_session_hash,p_generation,p_site_id,'approved') f
 WHERE NOT control.docs_document_withdrawn(v.tenant_id,p_site_id,f.document_id);
END $$;

-- Add withdrawal denial at the existing source and completion ports. No facts are deleted.
DO $$ DECLARE signature text; body text; old text; replacement text; BEGIN
  FOR signature,old,replacement IN SELECT * FROM (VALUES
    ('control.business_brain_approve(bytea,text,uuid,uuid,uuid)',
     'AND f.initial_status=''proposed''',
     'AND f.initial_status=''proposed'' AND NOT control.docs_document_withdrawn(f.tenant_id,f.site_id,f.document_id)'),
    ('control.read_brand_document_artifact(bytea,text,uuid,uuid)',
     'AND NOT EXISTS (SELECT 1 FROM app.brand_documents next',
     'AND NOT control.docs_document_withdrawn(d.tenant_id,d.site_id,d.id) AND NOT EXISTS (SELECT 1 FROM app.brand_documents next'),
    ('control.business_brain_begin_extraction(bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid)',
     'AND NOT d.secret_signal', 'AND NOT d.secret_signal AND NOT control.docs_document_withdrawn(d.tenant_id,d.site_id,d.id)'),
    ('control.business_brain_finish_extraction(bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text)',
     ' IF p_reason IS NOT NULL THEN',
     ' IF e.source_kind=''brand_document'' AND control.docs_document_withdrawn(e.tenant_id,e.site_id,e.source_id) THEN RETURN ''source_unavailable''; END IF; IF p_reason IS NOT NULL THEN')
  ) changes LOOP
    SELECT pg_get_functiondef(signature::regprocedure) INTO body;
    IF position(old IN body)=0 THEN RAISE EXCEPTION 'docs_source_boundary_upgrade_conflict'; END IF;
    EXECUTE replace(body,old,replacement);
  END LOOP;
END $$;
DO $$ DECLARE f record; BEGIN
  FOR f IN SELECT oid::regprocedure AS signature,proname FROM pg_proc WHERE pronamespace='control'::regnamespace
    AND proname IN ('docs_owner','begin_docs_oauth','consume_docs_oauth','docs_oauth_current','bind_docs_oauth','docs_binding_packet',
      'degrade_docs_binding','record_docs_source_version','revoke_docs_binding','apply_docs_binding_denial','bind_docs_egress_profile') LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %%s FROM PUBLIC',f.signature);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %%s TO %%I',f.signature,
      CASE WHEN f.proname='apply_docs_binding_denial' THEN 'signal_authority_dispatcher'
           WHEN f.proname='bind_docs_egress_profile' THEN 'signal_crawl_admission' ELSE 'signal_api' END);
  END LOOP;
END $$;
