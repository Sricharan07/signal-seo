ALTER TABLE app.invitations DROP CONSTRAINT invitations_revoked_at_check,
    ADD COLUMN revoker_user_id uuid,
    ADD FOREIGN KEY (tenant_id,revoker_user_id) REFERENCES app.memberships(tenant_id,user_id),
    ADD CONSTRAINT invitations_revocation_shape CHECK (
        (revoked_at IS NULL AND revoker_user_id IS NULL) OR
        (revoked_at IS NOT NULL AND revoker_user_id IS NOT NULL AND consumed_at IS NULL
            AND accepted_user_id IS NULL AND revoked_at>=created_at AND revoked_at<expires_at)
    );

CREATE OR REPLACE FUNCTION app.guard_invitation_transition() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='UPDATE' AND current_user='signal_migrator'
        AND OLD.consumed_at IS NULL AND OLD.accepted_user_id IS NULL
        AND OLD.revoked_at IS NULL AND OLD.revoker_user_id IS NULL
        AND OLD.expires_at>transaction_timestamp() THEN
        IF NEW.consumed_at IS NOT DISTINCT FROM transaction_timestamp()
            AND NEW.accepted_user_id IS NOT NULL
            AND (to_jsonb(NEW)-'consumed_at'-'accepted_user_id') IS NOT DISTINCT FROM
                (to_jsonb(OLD)-'consumed_at'-'accepted_user_id') THEN RETURN NEW; END IF;
        IF NEW.revoked_at IS NOT DISTINCT FROM transaction_timestamp()
            AND NEW.revoker_user_id IS NOT DISTINCT FROM app.current_actor_user_id()
            AND NEW.revoker_user_id IS NOT NULL
            AND (to_jsonb(NEW)-'revoked_at'-'revoker_user_id') IS NOT DISTINCT FROM
                (to_jsonb(OLD)-'revoked_at'-'revoker_user_id') THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'invitation transition denied' USING ERRCODE='55000';
END $$;
REVOKE ALL ON FUNCTION app.guard_invitation_transition() FROM PUBLIC;

ALTER TABLE app.audit_events DROP CONSTRAINT audit_events_event_type_check,
    ADD CONSTRAINT audit_events_event_type_check CHECK (
        (aggregate_sequence=1 AND event_type='invitation.created') OR
        (aggregate_sequence=2 AND event_type IN ('invitation.accepted','invitation.revoked'))
    );
CREATE OR REPLACE FUNCTION app.validate_invitation_audit_event() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.actor_identifier !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'audit event reference is invalid' USING ERRCODE='23503'; END IF;
    IF NEW.event_type='invitation.created' AND EXISTS (
        SELECT 1 FROM app.invitations i WHERE i.tenant_id=NEW.tenant_id AND i.site_id=NEW.site_id
            AND i.id=NEW.aggregate_id AND i.inviter_user_id=NEW.actor_identifier::uuid
            AND i.role_key=NEW.facts->>'role_key'
    ) THEN RETURN NEW; END IF;
    IF NEW.event_type IN ('invitation.accepted','invitation.revoked') AND EXISTS (
        SELECT 1 FROM app.invitations i JOIN app.audit_events e
            ON e.tenant_id=i.tenant_id AND e.site_id=i.site_id AND e.aggregate_id=i.id
            AND e.aggregate_kind='invitation' AND e.aggregate_sequence=1
        WHERE i.tenant_id=NEW.tenant_id AND i.site_id=NEW.site_id AND i.id=NEW.aggregate_id
            AND i.role_key=NEW.facts->>'role_key' AND e.event_hash=NEW.previous_hash
            AND ((NEW.event_type='invitation.accepted' AND i.accepted_user_id=NEW.actor_identifier::uuid
                AND i.consumed_at IS NOT DISTINCT FROM NEW.occurred_at AND i.revoked_at IS NULL)
                OR (NEW.event_type='invitation.revoked' AND i.revoker_user_id=NEW.actor_identifier::uuid
                AND i.revoked_at IS NOT DISTINCT FROM NEW.occurred_at AND i.consumed_at IS NULL))
    ) THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'audit event reference is invalid' USING ERRCODE='23503';
END $$;
REVOKE ALL ON FUNCTION app.validate_invitation_audit_event() FROM PUBLIC;

-- Add one closed deny-only kind, preserving every existing journal contract.
DO $$ DECLARE t text;c text;expr text;extra text; BEGIN
    FOREACH t IN ARRAY ARRAY['authority_restriction_outbox','authority_denial_tombstones'] LOOP
        FOREACH c IN ARRAY ARRAY['target_kind','restriction_kind'] LOOP
            SELECT pg_get_expr(conbin,conrelid) INTO STRICT expr FROM pg_constraint
            WHERE conrelid=('control.'||t)::regclass AND conname=t||'_'||c||'_check';
            extra:=CASE WHEN c='target_kind' THEN 'target_kind=''invitation'''
                ELSE 'target_kind=''invitation'' AND restriction_kind=''invitation_revoked''' END;
            EXECUTE format('ALTER TABLE control.%%I DROP CONSTRAINT %%I',t,t||'_'||c||'_check');
            EXECUTE format('ALTER TABLE control.%%I ADD CONSTRAINT %%I CHECK((%%s) OR (%%s))',t,t||'_'||c||'_check',expr,extra);
        END LOOP;
    END LOOP;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expr FROM pg_constraint
    WHERE conrelid='control.platform_events'::regclass AND conname='platform_events_contract_check';
    ALTER TABLE control.platform_events DROP CONSTRAINT platform_events_contract_check;
    EXECUTE 'ALTER TABLE control.platform_events ADD CONSTRAINT platform_events_contract_check CHECK(('||expr||') OR
        (event_type=''invitation.revoked'' AND actor_user_id IS NOT NULL AND object_kind=''invitation''
        AND reason=''owner_revocation'' AND facts=jsonb_build_object(''schema_version'',1,
            ''restriction_kind'',''invitation_revoked'',''target_id'',object_id)))';
END $$;
DROP TRIGGER platform_event_reference_guard ON control.platform_events;
CREATE TRIGGER platform_event_reference_guard BEFORE INSERT ON control.platform_events
-- Keep every exemption added by earlier migrations (Webflow 0075, GitHub PR 0093).
FOR EACH ROW WHEN (NEW.event_type NOT IN ('webflow.binding.revoked','github.pr.revoked','invitation.revoked'))
EXECUTE FUNCTION control.validate_platform_event_reference();
CREATE FUNCTION control.journal_invitation_revocation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM control.invitation_routes r JOIN app.invitations i
        ON i.tenant_id=r.tenant_id AND i.site_id=r.site_id AND i.id=r.invitation_id
        JOIN app.audit_events e ON e.tenant_id=i.tenant_id AND e.site_id=i.site_id AND e.aggregate_id=i.id
        WHERE r.invitation_id=NEW.object_id AND e.id=NEW.id AND e.event_type='invitation.revoked'
            AND e.actor_identifier=NEW.actor_user_id::text AND i.revoker_user_id=NEW.actor_user_id
            AND i.revoked_at IS NOT NULL AND e.occurred_at=i.revoked_at
    ) THEN RAISE EXCEPTION 'invitation restriction invalid' USING ERRCODE='23503'; END IF;
    INSERT INTO control.authority_restriction_outbox(event_id,scope_kind,actor_user_id,target_kind,
        target_id,restriction_kind,effective_epoch,event_time,original_facts)
    VALUES(NEW.id,'platform',NEW.actor_user_id,'invitation',NEW.object_id,'invitation_revoked',1,NEW.created_at,NEW.facts);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.journal_invitation_revocation() FROM PUBLIC;
CREATE TRIGGER journal_invitation_revocation AFTER INSERT ON control.platform_events
FOR EACH ROW WHEN (NEW.event_type='invitation.revoked') EXECUTE FUNCTION control.journal_invitation_revocation();
CREATE POLICY platform_invitation_revocation_insert ON control.platform_events FOR INSERT TO signal_migrator
WITH CHECK (event_type='invitation.revoked' AND EXISTS (
    SELECT 1 FROM control.invitation_routes r JOIN app.audit_events e ON e.tenant_id=r.tenant_id
        AND e.site_id=r.site_id AND e.aggregate_id=r.invitation_id
    WHERE r.invitation_id=platform_events.object_id AND e.id=platform_events.id
        AND e.event_type='invitation.revoked' AND e.actor_identifier=platform_events.actor_user_id::text
));

CREATE FUNCTION control.revoke_owner_team_invitation(
    p_hash bytea,p_site uuid,p_generation text,p_invitation uuid,p_event uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a jsonb; i app.invitations%%ROWTYPE; prev bytea; event_hash bytea; v_now timestamptz:=transaction_timestamp();
BEGIN
    IF p_invitation IS NULL OR p_event IS NULL
        OR p_event::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN RETURN NULL; END IF;
    a:=control.team_owner(p_hash,p_site,p_generation,true);
    IF a IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO i FROM app.invitations WHERE tenant_id=(a->>'tenant_id')::uuid
        AND site_id=p_site AND id=p_invitation FOR UPDATE;
    IF NOT FOUND OR i.consumed_at IS NOT NULL OR i.revoked_at IS NOT NULL
        OR i.expires_at<=clock_timestamp() OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones
            WHERE target_kind='invitation' AND target_id=p_invitation) THEN RETURN NULL; END IF;
    SELECT e.event_hash INTO prev FROM app.audit_events e WHERE e.tenant_id=i.tenant_id AND e.site_id=p_site
        AND e.aggregate_kind='invitation' AND e.aggregate_id=i.id AND e.aggregate_sequence=1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE app.invitations SET revoked_at=v_now,revoker_user_id=(a->>'user_id')::uuid
        WHERE tenant_id=i.tenant_id AND site_id=p_site AND id=i.id;
    event_hash:=sha256(convert_to(
        'actor_identifier='||(a->>'user_id')||E'\nactor_kind=user\naggregate_id='||i.id::text
        ||E'\naggregate_kind=invitation\naggregate_sequence=2\nevent_id='||p_event::text
        ||E'\nevent_type=invitation.revoked\nfacts.role_key='||i.role_key||E'\nfacts.schema_version=1\noccurred_at='
        ||to_char(v_now AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
        ||E'\nprevious_hash='||encode(prev,'hex')||E'\nsite_id='||p_site::text||E'\ntenant_id='||i.tenant_id::text,'UTF8'));
    INSERT INTO app.audit_events(tenant_id,id,site_id,aggregate_kind,aggregate_id,aggregate_sequence,
        actor_kind,actor_identifier,event_type,facts,previous_hash,event_hash,occurred_at)
    VALUES(i.tenant_id,p_event,p_site,'invitation',i.id,2,'user',a->>'user_id','invitation.revoked',
        jsonb_build_object('schema_version',1,'role_key',i.role_key),prev,event_hash,v_now);
    INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason)
    VALUES(p_event,'invitation.revoked',(a->>'user_id')::uuid,'invitation',i.id,
        jsonb_build_object('schema_version',1,'restriction_kind','invitation_revoked','target_id',i.id),'owner_revocation');
    RETURN jsonb_build_object('schema_version',1,'invitation_id',i.id,'site_id',p_site,'revoked_at',v_now,
        'durability','AUTHORITY_DURABILITY_PENDING');
END $$;
REVOKE ALL ON FUNCTION control.revoke_owner_team_invitation(bytea,uuid,text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.revoke_owner_team_invitation(bytea,uuid,text,uuid,uuid) TO signal_api,signal_identity;

CREATE FUNCTION control.apply_invitation_authority_denial(
    p_event uuid,p_target uuid,p_epoch bigint,p_stream uuid,p_position bigint,p_hash text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;
BEGIN
    IF session_user<>'signal_authority_dispatcher' OR p_event IS NULL OR p_target IS NULL
        OR p_epoch IS DISTINCT FROM 1 OR p_stream IS NULL OR p_position IS NULL OR p_position<1
        OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invitation replay denied' USING ERRCODE='42501'; END IF;
    SELECT * INTO r FROM control.invitation_routes WHERE invitation_id=p_target;
    IF FOUND THEN
        PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
        PERFORM set_config('signal.site_id',r.site_id::text,true);
        PERFORM 1 FROM app.tenants WHERE tenant_id=r.tenant_id FOR UPDATE;
        PERFORM 1 FROM app.sites WHERE tenant_id=r.tenant_id AND id=r.site_id FOR UPDATE;
    END IF;
    INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,
        effective_epoch,stream_generation,stream_position,payload_hash)
    VALUES(p_event,'invitation',p_target,'invitation_revoked',1,p_stream,p_position,p_hash) ON CONFLICT(event_id) DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event
        AND target_kind='invitation' AND target_id=p_target AND effective_epoch=1
        AND stream_generation=p_stream AND stream_position=p_position AND payload_hash=p_hash) THEN
        RAISE EXCEPTION 'invitation replay conflict' USING ERRCODE='23505'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.apply_invitation_authority_denial(uuid,uuid,bigint,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_invitation_authority_denial(uuid,uuid,bigint,uuid,bigint,text) TO signal_authority_dispatcher;

-- Preserve the accepted implementation behind a private, restore-aware wrapper.
ALTER FUNCTION control.accept_site_invitation(uuid,bytea,text,text,text,text,uuid,uuid,uuid,uuid)
RENAME TO accept_site_invitation_authority_base;
REVOKE ALL ON FUNCTION control.accept_site_invitation_authority_base(uuid,bytea,text,text,text,text,uuid,uuid,uuid,uuid)
FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,signal_scheduler;
CREATE FUNCTION control.accept_site_invitation(
    p_id uuid,p_hash bytea,p_issuer text,p_subject text,p_email text,p_name text,
    p_user uuid,p_membership uuid,p_site_membership uuid,p_event uuid
) RETURNS TABLE(accepted_user_id uuid,accepted_tenant_id uuid,accepted_site_id uuid,
    accepted_role_key text,accepted_at timestamptz,accepted_event_hash bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;
BEGIN
    SELECT * INTO r FROM control.invitation_routes WHERE invitation_id=p_id;
    IF NOT FOUND THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
    PERFORM set_config('signal.site_id',r.site_id::text,true);
    PERFORM 1 FROM app.tenants WHERE tenant_id=r.tenant_id FOR UPDATE;
    PERFORM 1 FROM app.sites WHERE tenant_id=r.tenant_id AND id=r.site_id FOR UPDATE;
    IF EXISTS (SELECT 1 FROM control.authority_denial_tombstones
        WHERE target_kind='invitation' AND target_id=p_id) THEN RETURN; END IF;
    RETURN QUERY SELECT * FROM control.accept_site_invitation_authority_base(
        p_id,p_hash,p_issuer,p_subject,p_email,p_name,p_user,p_membership,p_site_membership,p_event);
END $$;
REVOKE ALL ON FUNCTION control.accept_site_invitation(uuid,bytea,text,text,text,text,uuid,uuid,uuid,uuid) FROM PUBLIC;

-- Extend the existing bounded, owner-only projection without inventing readiness.
CREATE OR REPLACE FUNCTION control.read_owner_team(p_hash bytea,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a jsonb; v_invitations jsonb; v_members jsonb; v_truncated boolean; v_can_revoke boolean;
BEGIN
    a:=control.team_owner(p_hash,p_site,p_generation,false);
    IF a IS NULL THEN RETURN NULL; END IF;
    v_can_revoke:=control.team_owner(p_hash,p_site,p_generation,true) IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.invitations WHERE tenant_id=(a->>'tenant_id')::uuid AND site_id=p_site
            AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>clock_timestamp()
            AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                WHERE target_kind='invitation' AND target_id=app.invitations.id));
    SELECT coalesce(jsonb_agg(to_jsonb(i) ORDER BY i.created_at DESC,i.id),'[]'::jsonb)
    INTO v_invitations FROM (
        SELECT id,email_normalized AS email,role_key,created_at,expires_at,
            CASE WHEN consumed_at IS NOT NULL THEN 'accepted' WHEN revoked_at IS NOT NULL OR EXISTS (
                SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='invitation'
                    AND target_id=app.invitations.id) THEN 'revoked'
                WHEN expires_at<=transaction_timestamp() THEN 'expired' ELSE 'pending' END AS state,
            CASE WHEN EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                WHERE target_kind='invitation' AND target_id=app.invitations.id) THEN 'acknowledged'
                WHEN revoked_at IS NULL THEN NULL WHEN EXISTS (
                SELECT 1 FROM app.audit_events e JOIN control.platform_events receipt ON receipt.object_id=e.id
                    AND receipt.event_type='authority.restriction.acknowledged'
                WHERE e.tenant_id=app.invitations.tenant_id AND e.site_id=p_site AND e.aggregate_id=app.invitations.id
                    AND e.event_type='invitation.revoked') THEN 'acknowledged' ELSE 'pending' END AS revocation_durability
        FROM app.invitations WHERE tenant_id=(a->>'tenant_id')::uuid AND site_id=p_site
        ORDER BY created_at DESC,id LIMIT 100
    ) i;
    SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.display_name,m.user_id),'[]'::jsonb)
    INTO v_members FROM (
        SELECT membership.user_id,u.display_name,membership.role_key FROM app.memberships membership
        JOIN app.site_memberships sm ON sm.tenant_id=membership.tenant_id AND sm.user_id=membership.user_id
        JOIN control.users u ON u.id=membership.user_id
        WHERE membership.tenant_id=(a->>'tenant_id')::uuid AND sm.site_id=p_site
            AND membership.state='active' AND sm.state='active' AND u.disabled_at IS NULL
        ORDER BY u.display_name,membership.user_id LIMIT 100
    ) m;
    SELECT (SELECT count(*)>100 FROM app.invitations WHERE tenant_id=(a->>'tenant_id')::uuid AND site_id=p_site)
        OR (SELECT count(*)>100 FROM app.memberships m JOIN app.site_memberships sm
            ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id JOIN control.users u ON u.id=m.user_id
            WHERE m.tenant_id=(a->>'tenant_id')::uuid AND sm.site_id=p_site AND m.state='active'
                AND sm.state='active' AND u.disabled_at IS NULL) INTO v_truncated;
    RETURN jsonb_build_object('schema_version',1,'site_id',p_site,'invitations',v_invitations,
        'members',v_members,'truncated',v_truncated,'can_revoke',v_can_revoke);
END $$;
REVOKE ALL ON FUNCTION control.read_owner_team(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_owner_team(bytea,uuid,text) TO signal_api,signal_identity;
