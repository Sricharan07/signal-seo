CREATE FUNCTION control.team_owner(
    p_hash bytea, p_site uuid, p_generation text, p_mutation boolean
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE a record; v_auth_time timestamptz;
BEGIN
    IF p_mutation IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
        THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    PERFORM set_config('signal.actor_user_id',a.user_id::text,true);
    PERFORM set_config('signal.membership_epoch',a.membership_epoch::text,true);
    PERFORM set_config('signal.site_authorization_epoch',a.site_authorization_epoch::text,true);
    IF NOT app.lock_invitation_authority() THEN RETURN NULL; END IF;
    PERFORM 1 FROM app.sessions s JOIN control.identity_sessions i
        ON i.id=s.identity_session_id AND i.user_id=s.user_id
        JOIN control.users u ON u.id=s.user_id
        WHERE s.session_token_hash=p_hash FOR UPDATE OF s,i,u;
    IF NOT FOUND THEN RETURN NULL; END IF;
    -- Recheck after the same locks used by issuance and acceptance have been acquired.
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
        THEN RETURN NULL; END IF;
    IF p_mutation THEN
        SELECT auth_time INTO v_auth_time FROM app.sessions WHERE session_token_hash=p_hash;
        IF a.authentication_level IS DISTINCT FROM 'mfa'
            OR v_auth_time IS NULL
            OR v_auth_time NOT BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp()
            OR NOT EXISTS (SELECT 1 FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site
                AND ownership_status='verified' AND state!='archived') THEN RETURN NULL; END IF;
    END IF;
    RETURN to_jsonb(a);
END; $$;
REVOKE ALL ON FUNCTION control.team_owner(bytea,uuid,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.team_owner(bytea,uuid,text,boolean) TO signal_api,signal_identity;

CREATE FUNCTION control.read_owner_team(p_hash bytea,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE a jsonb; v_invitations jsonb; v_members jsonb; v_truncated boolean;
BEGIN
    a := control.team_owner(p_hash,p_site,p_generation,false);
    IF a IS NULL THEN RETURN NULL; END IF;
    SELECT coalesce(jsonb_agg(to_jsonb(i) ORDER BY i.created_at DESC,i.id),'[]'::jsonb)
    INTO v_invitations FROM (
        SELECT id,email_normalized AS email,role_key,created_at,expires_at,
            CASE WHEN consumed_at IS NOT NULL THEN 'accepted' WHEN revoked_at IS NOT NULL THEN 'revoked'
                WHEN expires_at<=transaction_timestamp() THEN 'expired' ELSE 'pending' END AS state
        FROM app.invitations WHERE tenant_id=(a->>'tenant_id')::uuid AND site_id=p_site
        ORDER BY created_at DESC,id LIMIT 100
    ) i;
    SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.display_name,m.user_id),'[]'::jsonb)
    INTO v_members FROM (
        SELECT membership.user_id,u.display_name,membership.role_key
        FROM app.memberships membership JOIN app.site_memberships sm
            ON sm.tenant_id=membership.tenant_id AND sm.user_id=membership.user_id
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
        'members',v_members,'truncated',v_truncated,'can_revoke',false);
END; $$;
REVOKE ALL ON FUNCTION control.read_owner_team(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_owner_team(bytea,uuid,text) TO signal_api,signal_identity;

-- The composed browser gateway uses signal_identity. Give it a guarded function,
-- not direct invitation/audit table privileges or a second database credential.
CREATE FUNCTION control.insert_owner_team_invitation(
    p_hash bytea,p_site uuid,p_generation text,p_expected jsonb,
    p_id uuid,p_email text,p_role text,p_token_hash bytea,p_expires timestamptz,
    p_event uuid,p_event_hash bytea,p_created timestamptz
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE a jsonb;
BEGIN
    a := control.team_owner(p_hash,p_site,p_generation,true);
    IF a IS NULL OR p_expected IS NULL OR a IS DISTINCT FROM p_expected
        OR p_role IS NULL OR p_role NOT IN ('viewer','analyst','editor','approver','admin')
        OR p_created IS DISTINCT FROM transaction_timestamp()
        OR p_expires IS NULL OR p_expires NOT BETWEEN p_created+interval '15 minutes' AND p_created+interval '7 days'
        THEN RETURN false; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended((a->>'tenant_id')||':'||p_site::text||':'||p_email,0));
    IF EXISTS (SELECT 1 FROM app.invitations WHERE tenant_id=(a->>'tenant_id')::uuid AND site_id=p_site
        AND email_normalized=p_email AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>p_created) THEN
        RAISE EXCEPTION 'invitation already pending' USING ERRCODE='23505',CONSTRAINT='team_invitation_pending';
    END IF;
    INSERT INTO app.invitations(tenant_id,id,site_id,email_normalized,role_key,token_hash,
        inviter_user_id,inviter_membership_epoch,inviter_site_authorization_epoch,expires_at,created_at)
    VALUES((a->>'tenant_id')::uuid,p_id,p_site,p_email,p_role,p_token_hash,(a->>'user_id')::uuid,
        (a->>'membership_epoch')::bigint,(a->>'site_authorization_epoch')::bigint,p_expires,p_created);
    INSERT INTO app.audit_events(tenant_id,id,site_id,aggregate_kind,aggregate_id,aggregate_sequence,
        actor_kind,actor_identifier,event_type,facts,previous_hash,event_hash,occurred_at)
    VALUES((a->>'tenant_id')::uuid,p_event,p_site,'invitation',p_id,1,'user',a->>'user_id',
        'invitation.created',jsonb_build_object('schema_version',1,'role_key',p_role),NULL,p_event_hash,p_created);
    RETURN true;
END; $$;
REVOKE ALL ON FUNCTION control.insert_owner_team_invitation(bytea,uuid,text,jsonb,uuid,text,text,bytea,timestamptz,uuid,bytea,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.insert_owner_team_invitation(bytea,uuid,text,jsonb,uuid,text,text,bytea,timestamptz,uuid,bytea,timestamptz) TO signal_api,signal_identity;
