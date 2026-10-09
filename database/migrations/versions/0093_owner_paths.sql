-- Browser grants reuse the internal observer, with fresh human authority at both ends.
CREATE FUNCTION control.owner_github_pr_preflight(p_hash bytea,p_site uuid,p_generation text,p_binding uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
 IF a.role_key IS DISTINCT FROM 'owner' OR a.authentication_level IS DISTINCT FROM 'mfa' THEN RETURN 'permission_denied'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.sessions s WHERE s.session_token_hash=p_hash
   AND s.auth_time BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp())
 THEN RETURN 'step_up_required'; END IF;
 SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_binding FOR SHARE;
 IF NOT FOUND OR b.status<>'active' OR b.membership_epoch<>a.membership_epoch
   OR b.site_epoch<>a.site_authorization_epoch OR b.recovery_generation<>p_generation
   OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'binding_not_authorized'; END IF;
 RETURN 'authorized';
END $$;
REVOKE ALL ON FUNCTION control.owner_github_pr_preflight(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.owner_github_pr_preflight(bytea,uuid,text,uuid) TO signal_identity;

CREATE FUNCTION control.prepare_owner_github_pr_extension(
 p_hash bytea,p_site uuid,p_generation text,p_binding uuid,p_extension uuid,p_event uuid,p_request uuid,p_digest bytea
) RETURNS TABLE(extension_id uuid,extension_status text,replayed boolean,outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result text; BEGIN
 result:=control.owner_github_pr_preflight(p_hash,p_site,p_generation,p_binding);
 IF result<>'authorized' THEN RETURN QUERY SELECT NULL::uuid,NULL::text,false,result; RETURN; END IF;
 RETURN QUERY SELECT * FROM control.prepare_github_pr_extension(p_hash,p_site,p_generation,p_binding,p_extension,p_event,p_request,p_digest);
END $$;
REVOKE ALL ON FUNCTION control.prepare_owner_github_pr_extension(bytea,uuid,text,uuid,uuid,uuid,uuid,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_owner_github_pr_extension(bytea,uuid,text,uuid,uuid,uuid,uuid,bytea) TO signal_identity;

CREATE FUNCTION control.finish_owner_github_pr_extension(
 p_hash bytea,p_site uuid,p_generation text,p_extension uuid,p_event uuid,p_outcome text,
 p_repository bigint,p_base text,p_tree text,p_framework text,p_format text,p_coverage text,p_content text,p_markers jsonb,p_protected boolean
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; e app.github_pr_extensions%%ROWTYPE; result text; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
 SELECT * INTO e FROM app.github_pr_extensions WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_extension FOR UPDATE;
 IF NOT FOUND OR e.status<>'prepared' OR e.requested_by_user_id<>a.user_id THEN RETURN 'extension_not_prepared'; END IF;
 result:=control.owner_github_pr_preflight(p_hash,p_site,p_generation,e.binding_id);
 IF result<>'authorized' THEN RETURN result; END IF;
 RETURN control.finish_github_pr_extension(p_hash,p_site,p_generation,p_extension,p_event,p_outcome,p_repository,p_base,p_tree,p_framework,p_format,p_coverage,p_content,p_markers,p_protected);
END $$;
REVOKE ALL ON FUNCTION control.finish_owner_github_pr_extension(bytea,uuid,text,uuid,uuid,text,bigint,text,text,text,text,text,text,jsonb,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.finish_owner_github_pr_extension(bytea,uuid,text,uuid,uuid,text,bigint,text,text,text,text,text,text,jsonb,boolean) TO signal_identity;

CREATE FUNCTION control.read_owner_github_pr_extension(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE; e app.github_pr_extensions%%ROWTYPE;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
   OR a.authentication_level IS DISTINCT FROM 'mfa' OR NOT control.current_github_site_proof(a.tenant_id,p_site)
 THEN RETURN jsonb_build_object('availability','denied'); END IF;
 SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site ORDER BY prepared_at DESC,id DESC LIMIT 1;
 IF NOT FOUND OR b.status<>'active' OR b.membership_epoch<>a.membership_epoch OR b.site_epoch<>a.site_authorization_epoch
   OR b.recovery_generation<>p_generation THEN RETURN jsonb_build_object('availability','ungranted'); END IF;
 SELECT * INTO e FROM app.github_pr_extensions WHERE tenant_id=a.tenant_id AND site_id=p_site AND binding_id=b.id ORDER BY prepared_at DESC,id DESC LIMIT 1;
 IF NOT FOUND OR e.status='revoked' THEN RETURN jsonb_build_object('availability','ungranted'); END IF;
 RETURN jsonb_build_object('availability',CASE WHEN e.membership_epoch<>a.membership_epoch OR e.site_epoch<>a.site_authorization_epoch OR e.recovery_generation<>p_generation THEN 'stale' ELSE e.status END,
   'extension_id',e.id,'idempotency_key',e.idempotency_key,'binding_id',b.id,'owner',b.repository_owner,'repository',b.repository_name,'base_branch',b.base_branch,'content_path',b.content_path);
END $$;
REVOKE ALL ON FUNCTION control.read_owner_github_pr_extension(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_owner_github_pr_extension(bytea,text,uuid) TO signal_identity;

-- A PR permission restriction is separate from the read binding and never deletes it.
CREATE TABLE control.github_pr_revocations(
 event_id uuid PRIMARY KEY,target_id uuid NOT NULL UNIQUE,actor_user_id uuid NOT NULL
);
REVOKE ALL ON control.github_pr_revocations FROM PUBLIC;
CREATE TRIGGER github_pr_revocations_immutable BEFORE UPDATE OR DELETE ON control.github_pr_revocations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
DO $$ DECLARE t text;c text;expr text;extra text; BEGIN
 FOREACH t IN ARRAY ARRAY['authority_restriction_outbox','authority_denial_tombstones'] LOOP
  FOREACH c IN ARRAY ARRAY['target_kind','restriction_kind'] LOOP
   SELECT pg_get_expr(conbin,conrelid) INTO expr FROM pg_constraint WHERE conrelid=('control.'||t)::regclass AND conname=t||'_'||c||'_check';
   extra:=CASE WHEN c='target_kind' THEN 'target_kind=''github_pr_extension''' ELSE '(target_kind=''github_pr_extension'' AND restriction_kind=''github_pr_extension_revoked'')' END;
   EXECUTE format('ALTER TABLE control.%%I DROP CONSTRAINT %%I',t,t||'_'||c||'_check');
   EXECUTE format('ALTER TABLE control.%%I ADD CONSTRAINT %%I CHECK((%%s) OR %%s)',t,t||'_'||c||'_check',expr,extra);
  END LOOP;
 END LOOP;
 SELECT pg_get_expr(conbin,conrelid) INTO expr FROM pg_constraint WHERE conrelid='control.platform_events'::regclass AND conname='platform_events_contract_check';
 ALTER TABLE control.platform_events DROP CONSTRAINT platform_events_contract_check;
 EXECUTE 'ALTER TABLE control.platform_events ADD CONSTRAINT platform_events_contract_check CHECK(('||expr||') OR (event_type=''github.pr.revoked'' AND actor_user_id IS NOT NULL AND object_kind=''github_pr_extension'' AND reason=''owner_revocation'' AND facts=jsonb_build_object(''schema_version'',1,''restriction_kind'',''github_pr_extension_revoked'',''target_id'',object_id)))';
END $$;
DROP TRIGGER platform_event_reference_guard ON control.platform_events;
CREATE TRIGGER platform_event_reference_guard BEFORE INSERT ON control.platform_events
FOR EACH ROW WHEN(NEW.event_type NOT IN ('webflow.binding.revoked','github.pr.revoked')) EXECUTE FUNCTION control.validate_platform_event_reference();
CREATE FUNCTION control.enqueue_github_pr_restriction() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.event_kind<>'revoked' THEN RETURN NEW; END IF;
 INSERT INTO control.github_pr_revocations VALUES(NEW.id,NEW.extension_id,NEW.actor_user_id);
 INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason)
 VALUES(NEW.id,'github.pr.revoked',NEW.actor_user_id,'github_pr_extension',NEW.extension_id,jsonb_build_object('schema_version',1,'restriction_kind','github_pr_extension_revoked','target_id',NEW.extension_id),'owner_revocation');
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.enqueue_github_pr_restriction() FROM PUBLIC;
CREATE TRIGGER enqueue_github_pr_restriction AFTER INSERT ON app.github_pr_extension_events FOR EACH ROW EXECUTE FUNCTION control.enqueue_github_pr_restriction();
CREATE FUNCTION control.github_pr_restriction_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM control.github_pr_revocations WHERE event_id=NEW.id AND target_id=NEW.object_id AND actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'github_pr_restriction_invalid' USING ERRCODE='23503'; END IF;
 INSERT INTO control.authority_restriction_outbox(event_id,scope_kind,actor_user_id,target_kind,target_id,restriction_kind,effective_epoch,event_time,original_facts)
 VALUES(NEW.id,'platform',NEW.actor_user_id,'github_pr_extension',NEW.object_id,'github_pr_extension_revoked',1,NEW.created_at,NEW.facts);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.github_pr_restriction_event() FROM PUBLIC;
CREATE TRIGGER github_pr_restriction_event AFTER INSERT ON control.platform_events FOR EACH ROW WHEN(NEW.event_type='github.pr.revoked') EXECUTE FUNCTION control.github_pr_restriction_event();
CREATE POLICY platform_github_pr_revocation_insert ON control.platform_events FOR INSERT TO signal_migrator WITH CHECK(event_type='github.pr.revoked' AND EXISTS(SELECT 1 FROM control.github_pr_revocations WHERE event_id=platform_events.id AND target_id=platform_events.object_id AND actor_user_id=platform_events.actor_user_id));
CREATE FUNCTION control.revoke_owner_github_pr_extension(p_hash bytea,p_site uuid,p_generation text,p_extension uuid,p_event uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; e app.github_pr_extensions%%ROWTYPE; result text; event uuid; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
 SELECT * INTO e FROM app.github_pr_extensions WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_extension FOR UPDATE;
 IF NOT FOUND THEN RETURN 'extension_not_authorized'; END IF;
 result:=control.owner_github_pr_preflight(p_hash,p_site,p_generation,e.binding_id);
 IF result<>'authorized' THEN RETURN result; END IF;
 result:=control.revoke_github_pr_extension(p_hash,p_site,p_generation,p_extension,p_event);
 IF result<>'revoked' THEN RETURN result; END IF;
 SELECT event_id INTO event FROM control.github_pr_revocations WHERE target_id=p_extension;
 RETURN COALESCE(control.authority_durability_status(event),'AUTHORITY_DURABILITY_PENDING');
END $$;
REVOKE ALL ON FUNCTION control.revoke_owner_github_pr_extension(bytea,uuid,text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.revoke_owner_github_pr_extension(bytea,uuid,text,uuid,uuid) TO signal_identity;
CREATE FUNCTION control.apply_github_pr_authority_denial(p_event uuid,p_target uuid,p_epoch bigint,p_stream uuid,p_position bigint,p_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'signal_authority_dispatcher' OR p_event IS NULL OR p_target IS NULL OR p_epoch<>1 OR p_stream IS NULL OR p_position<1 OR p_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'github_pr_replay_denied' USING ERRCODE='42501'; END IF;
 INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,effective_epoch,stream_generation,stream_position,payload_hash)
 VALUES(p_event,'github_pr_extension',p_target,'github_pr_extension_revoked',1,p_stream,p_position,p_hash) ON CONFLICT(event_id) DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event AND target_kind='github_pr_extension' AND target_id=p_target AND effective_epoch=1 AND stream_generation=p_stream AND stream_position=p_position AND payload_hash=p_hash) THEN RAISE EXCEPTION 'github_pr_replay_conflict' USING ERRCODE='23505'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.apply_github_pr_authority_denial(uuid,uuid,bigint,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_github_pr_authority_denial(uuid,uuid,bigint,uuid,bigint,text) TO signal_authority_dispatcher;
-- All existing delivery/build consumers require observed extensions. Restored grants
-- denied by the independent journal must be invisible to those consumers too.
CREATE POLICY github_pr_restore_denial ON app.github_pr_extensions AS RESTRICTIVE
USING(status<>'observed' OR NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='github_pr_extension' AND d.target_id=github_pr_extensions.id));

CREATE FUNCTION control.read_owner_weekly_pause(p_hash bytea,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; paused boolean; BEGIN
 IF session_user<>'signal_api' THEN RETURN NULL; END IF;
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
 SELECT c.paused INTO paused FROM app.site_weekly_control c WHERE c.tenant_id=a.tenant_id AND c.site_id=p_site;
 RETURN jsonb_build_object('site_id',p_site,'paused',COALESCE(paused,false));
END $$;
REVOKE ALL ON FUNCTION control.read_owner_weekly_pause(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_owner_weekly_pause(bytea,uuid,text) TO signal_api;
