CREATE TABLE app.site_weekly_control (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    paused boolean NOT NULL DEFAULT false,
    epoch bigint NOT NULL DEFAULT 0 CHECK (epoch >= 0),
    changed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id)
);
ALTER TABLE app.site_weekly_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.site_weekly_control FORCE ROW LEVEL SECURITY;
CREATE POLICY site_weekly_control_scope ON app.site_weekly_control
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.site_weekly_control FROM PUBLIC, signal_api, signal_workflow,
    signal_scheduler, signal_identity, signal_bootstrap, signal_crawl_admission,
    signal_crawl_ingest;

-- Pause revokes every current grant in the same transaction. Existing 0102
-- revocation triggers make the restriction independently durable and replayable.
CREATE FUNCTION control.set_weekly_site_pause(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_paused boolean
) RETURNS TABLE (outcome text, epoch bigint, draining_count bigint, durability text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_grant record; v_epoch bigint; v_count bigint;
        v_event_id uuid; v_durability text := 'ACKNOWLEDGED';
BEGIN
    IF session_user != 'signal_api' OR p_site_id IS NULL OR p_paused IS NULL THEN
        RETURN QUERY SELECT 'invalid_request'::text, NULL::bigint, NULL::bigint,
            NULL::text; RETURN;
    END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome != 'authorized' OR v_authority.role_key != 'owner' THEN
        RETURN QUERY SELECT 'permission_denied'::text, NULL::bigint, NULL::bigint,
            NULL::text; RETURN;
    END IF;
    PERFORM set_config('signal.tenant_id', v_authority.tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    INSERT INTO app.site_weekly_control (tenant_id, site_id)
    VALUES (v_authority.tenant_id, p_site_id) ON CONFLICT DO NOTHING;
    PERFORM 1 FROM app.site_weekly_control
    WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id FOR UPDATE;
    IF p_paused THEN
        FOR v_grant IN SELECT g.id FROM app.standing_authorizations g
            WHERE g.tenant_id = v_authority.tenant_id AND g.site_id = p_site_id
              AND NOT EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
                  WHERE r.tenant_id = g.tenant_id AND r.site_id = g.site_id
                    AND r.grant_id = g.id)
            ORDER BY g.id FOR UPDATE LOOP
            v_event_id := gen_random_uuid();
            INSERT INTO app.standing_authorization_revocations
                (tenant_id, site_id, id, grant_id, actor_user_id)
            VALUES (v_authority.tenant_id, p_site_id, v_event_id,
                    v_grant.id, v_authority.user_id);
            IF control.authority_durability_status(v_event_id)
                = 'AUTHORITY_DURABILITY_PENDING' THEN
                v_durability := 'AUTHORITY_DURABILITY_PENDING';
            END IF;
        END LOOP;
    END IF;
    IF EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
        WHERE r.tenant_id=v_authority.tenant_id AND r.site_id=p_site_id
          AND control.authority_durability_status(r.id)='AUTHORITY_DURABILITY_PENDING')
    THEN v_durability := 'AUTHORITY_DURABILITY_PENDING'; END IF;
    UPDATE app.site_weekly_control c SET paused = p_paused,
        epoch = c.epoch + CASE WHEN c.paused IS DISTINCT FROM p_paused THEN 1 ELSE 0 END,
        changed_at = transaction_timestamp()
    WHERE c.tenant_id = v_authority.tenant_id AND c.site_id = p_site_id
    RETURNING c.epoch INTO v_epoch;
    SELECT count(*) INTO v_count FROM app.weekly_cycles c
    JOIN app.commands command ON command.tenant_id=c.tenant_id
        AND command.site_id=c.site_id AND command.id=c.observe_command_id
    WHERE c.tenant_id=v_authority.tenant_id AND c.site_id=p_site_id
      AND command.status NOT IN ('succeeded','failed','cancelled');
    RETURN QUERY SELECT CASE WHEN p_paused THEN 'paused' ELSE 'pause_cleared' END::text,
        v_epoch, v_count, v_durability;
END $$;
REVOKE ALL ON FUNCTION control.set_weekly_site_pause(bytea,uuid,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.set_weekly_site_pause(bytea,uuid,text,boolean) TO signal_api;

-- Rename rather than replace the 0088 eligibility body. Every existing gate
-- caller now passes through the pause check, including the atomic 0089 finalizer.
ALTER FUNCTION control.standing_grant_eligibility(uuid,uuid,uuid,text,uuid,text,text)
    RENAME TO standing_grant_eligibility_unpaused;
REVOKE ALL ON FUNCTION control.standing_grant_eligibility_unpaused(
    uuid,uuid,uuid,text,uuid,text,text) FROM signal_api, signal_workflow;
CREATE FUNCTION control.standing_grant_eligibility(
    p_tenant_id uuid, p_site_id uuid, p_grant_id uuid, p_generation text,
    p_recipe_release_id uuid, p_work_type text, p_resource_path text
) RETURNS TABLE (eligible boolean, reason text, threshold numeric)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_paused boolean;
BEGIN
    IF session_user NOT IN ('signal_api','signal_workflow') THEN
        RETURN QUERY SELECT false, 'invalid_scope'::text, NULL::numeric; RETURN;
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT paused INTO v_paused FROM app.site_weekly_control
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id FOR SHARE;
    IF COALESCE(v_paused, false) THEN
        RETURN QUERY SELECT false, 'site_paused'::text, NULL::numeric; RETURN;
    END IF;
    RETURN QUERY SELECT * FROM control.standing_grant_eligibility_unpaused(
        p_tenant_id,p_site_id,p_grant_id,p_generation,p_recipe_release_id,
        p_work_type,p_resource_path);
END $$;
REVOKE ALL ON FUNCTION control.standing_grant_eligibility(
    uuid,uuid,uuid,text,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.standing_grant_eligibility(
    uuid,uuid,uuid,text,uuid,text,text) TO signal_api, signal_workflow;

CREATE TABLE app.weekly_cycles (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    week_start date NOT NULL,
    id uuid NOT NULL,
    first_run_id text NOT NULL,
    grant_id uuid NOT NULL,
    observe_command_id uuid,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','stopped','failed')),
    stop_reason text,
    started_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    closed_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, week_start),
    UNIQUE (id),
    FOREIGN KEY (tenant_id, site_id, grant_id)
        REFERENCES app.standing_authorizations (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, observe_command_id)
        REFERENCES app.commands (tenant_id, site_id, id),
    CHECK ((status = 'running') = (closed_at IS NULL))
);
CREATE TABLE app.weekly_stage_results (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    week_start date NOT NULL,
    stage text NOT NULL CHECK (stage IN
        ('observe','analyze','plan','prepare','gate','handoff','verify','measure','report')),
    outcome text NOT NULL CHECK (outcome IN
        ('completed','unavailable','deferred','waiting_owner','stopped','failed')),
    evidence_refs text[] NOT NULL DEFAULT '{}',
    detail_code text NOT NULL CHECK (detail_code ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, week_start, stage),
    FOREIGN KEY (tenant_id, site_id, week_start)
        REFERENCES app.weekly_cycles (tenant_id, site_id, week_start)
);
CREATE TRIGGER weekly_stage_immutable BEFORE UPDATE OR DELETE ON app.weekly_stage_results
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TABLE app.weekly_handoffs (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    week_start date NOT NULL,
    decision_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    sealed_revision_sha256 bytea NOT NULL CHECK (octet_length(sealed_revision_sha256) = 32),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, decision_id),
    UNIQUE (tenant_id, site_id, operation_id),
    FOREIGN KEY (tenant_id, site_id, week_start)
        REFERENCES app.weekly_cycles (tenant_id, site_id, week_start),
    FOREIGN KEY (tenant_id, site_id, decision_id)
        REFERENCES app.autonomy_gate_records (tenant_id, site_id, id)
);
CREATE TRIGGER weekly_handoff_immutable BEFORE UPDATE OR DELETE ON app.weekly_handoffs
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TABLE app.weekly_deferred_revisions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    source_week date NOT NULL,
    sealed_revision_sha256 bytea NOT NULL CHECK (octet_length(sealed_revision_sha256)=32),
    next_week date NOT NULL,
    reason text NOT NULL CHECK (reason='WEEKLY_CAP_REACHED'),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,source_week,sealed_revision_sha256),
    FOREIGN KEY (tenant_id,site_id,source_week)
        REFERENCES app.weekly_cycles (tenant_id,site_id,week_start),
    CHECK (next_week=source_week+7)
);
CREATE TRIGGER weekly_deferred_immutable BEFORE UPDATE OR DELETE
ON app.weekly_deferred_revisions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TABLE app.weekly_deferral_resolutions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    source_week date NOT NULL,
    sealed_revision_sha256 bytea NOT NULL,
    resolved_week date NOT NULL,
    decision_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,source_week,sealed_revision_sha256),
    FOREIGN KEY (tenant_id,site_id,source_week,sealed_revision_sha256)
        REFERENCES app.weekly_deferred_revisions
        (tenant_id,site_id,source_week,sealed_revision_sha256),
    FOREIGN KEY (tenant_id,site_id,decision_id)
        REFERENCES app.autonomy_gate_records (tenant_id,site_id,id),
    CHECK (resolved_week>source_week)
);
CREATE TRIGGER weekly_resolution_immutable BEFORE UPDATE OR DELETE
ON app.weekly_deferral_resolutions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['weekly_cycles','weekly_stage_results',
        'weekly_handoffs','weekly_deferred_revisions','weekly_deferral_resolutions'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY', name);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY', name);
        EXECUTE format('CREATE POLICY %%I_scope ON app.%%I USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id()) WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())', name, name);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC, signal_api, signal_workflow, signal_scheduler, signal_identity, signal_bootstrap, signal_crawl_admission, signal_crawl_ingest', name);
    END LOOP;
END $$;

CREATE FUNCTION control.defer_weekly_revisions(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_cycle_id uuid,p_hashes bytea[]
) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_hash bytea; v_count integer := 0;
BEGIN
    IF session_user != 'signal_workflow' OR p_hashes IS NULL
       OR cardinality(p_hashes) NOT BETWEEN 1 AND 32 THEN RETURN -1; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    PERFORM 1 FROM app.weekly_cycles WHERE tenant_id=p_tenant_id AND site_id=p_site_id
        AND week_start=p_week AND id=p_cycle_id AND status='running' FOR UPDATE;
    IF NOT FOUND THEN RETURN -1; END IF;
    FOREACH v_hash IN ARRAY p_hashes LOOP
        IF octet_length(v_hash) IS DISTINCT FROM 32 THEN RETURN -1; END IF;
        INSERT INTO app.weekly_deferred_revisions
            (tenant_id,site_id,source_week,sealed_revision_sha256,next_week,reason)
        VALUES (p_tenant_id,p_site_id,p_week,v_hash,p_week+7,'WEEKLY_CAP_REACHED')
        ON CONFLICT DO NOTHING;
        v_count := v_count+1;
    END LOOP;
    RETURN v_count;
END $$;
REVOKE ALL ON FUNCTION control.defer_weekly_revisions(uuid,uuid,date,uuid,bytea[])
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.defer_weekly_revisions(uuid,uuid,date,uuid,bytea[])
TO signal_workflow;

CREATE FUNCTION control.due_weekly_revisions(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_cycle_id uuid
) RETURNS TABLE (revision_sha256 bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    IF NOT EXISTS (SELECT 1 FROM app.weekly_cycles WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week AND id=p_cycle_id
        AND status='running') THEN RETURN; END IF;
    RETURN QUERY SELECT d.sealed_revision_sha256
    FROM app.weekly_deferred_revisions d WHERE d.tenant_id=p_tenant_id
        AND d.site_id=p_site_id AND d.next_week<=p_week
        AND NOT EXISTS (SELECT 1 FROM app.weekly_deferral_resolutions r
            WHERE r.tenant_id=d.tenant_id AND r.site_id=d.site_id
              AND r.source_week=d.source_week
              AND r.sealed_revision_sha256=d.sealed_revision_sha256)
    GROUP BY d.sealed_revision_sha256
    ORDER BY min(d.next_week),d.sealed_revision_sha256 LIMIT 32;
END $$;
REVOKE ALL ON FUNCTION control.due_weekly_revisions(uuid,uuid,date,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.due_weekly_revisions(uuid,uuid,date,uuid)
TO signal_workflow;

CREATE FUNCTION control.resolve_weekly_deferral(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_cycle_id uuid,
    p_revision_sha256 bytea,p_decision_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_count integer;
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    IF NOT EXISTS (SELECT 1 FROM app.weekly_cycles c
        JOIN app.autonomy_gate_records g ON g.tenant_id=c.tenant_id
            AND g.site_id=c.site_id AND g.id=p_decision_id
        WHERE c.tenant_id=p_tenant_id AND c.site_id=p_site_id
          AND c.week_start=p_week AND c.id=p_cycle_id AND c.status='running'
          AND g.outcome='ship' AND g.sealed_revision_sha256=p_revision_sha256
          AND g.decided_at>=c.started_at) THEN RETURN 'gate_unavailable'; END IF;
    INSERT INTO app.weekly_deferral_resolutions
        (tenant_id,site_id,source_week,sealed_revision_sha256,resolved_week,decision_id)
    SELECT d.tenant_id,d.site_id,d.source_week,d.sealed_revision_sha256,
        p_week,p_decision_id FROM app.weekly_deferred_revisions d
    WHERE d.tenant_id=p_tenant_id AND d.site_id=p_site_id
      AND d.sealed_revision_sha256=p_revision_sha256 AND d.next_week<=p_week
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN CASE WHEN v_count>0 THEN 'resolved' ELSE 'not_deferred' END;
END $$;
REVOKE ALL ON FUNCTION control.resolve_weekly_deferral(
    uuid,uuid,date,uuid,bytea,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.resolve_weekly_deferral(
    uuid,uuid,date,uuid,bytea,uuid) TO signal_workflow;

CREATE FUNCTION control.weekly_cycle_authority(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_id uuid,p_grant_id uuid,
    p_generation text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_grant app.standing_authorizations%%ROWTYPE;
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    IF NOT EXISTS (SELECT 1 FROM app.weekly_cycles WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week AND id=p_id
        AND grant_id=p_grant_id AND status='running') THEN RETURN 'cycle_unavailable'; END IF;
    PERFORM 1 FROM app.site_weekly_control WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND paused FOR SHARE;
    IF FOUND THEN RETURN 'paused'; END IF;
    SELECT * INTO v_grant FROM app.standing_authorizations WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND id=p_grant_id FOR SHARE;
    IF NOT FOUND OR v_grant.recovery_generation IS DISTINCT FROM p_generation
       OR transaction_timestamp() NOT BETWEEN v_grant.starts_at AND v_grant.ends_at
       OR EXISTS (SELECT 1 FROM app.standing_authorization_revocations
                  WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND grant_id=p_grant_id)
       OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                  WHERE target_kind='standing_grant' AND target_id=p_grant_id)
       OR NOT EXISTS (SELECT 1 FROM app.sites s JOIN app.tenants t ON t.tenant_id=s.tenant_id
           WHERE s.tenant_id=p_tenant_id AND s.id=p_site_id AND s.state='active'
           AND s.ownership_status='verified' AND t.lifecycle='active')
       OR NOT EXISTS (SELECT 1 FROM app.memberships m JOIN app.site_memberships sm
           ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id
           WHERE m.tenant_id=p_tenant_id AND m.user_id=v_grant.owner_user_id
           AND m.state='active' AND m.role_key='owner'
           AND m.authorization_epoch=v_grant.membership_epoch
           AND sm.site_id=p_site_id AND sm.state='active'
           AND sm.authorization_epoch=v_grant.site_epoch)
       OR NOT EXISTS (SELECT 1 FROM control.recipe_releases r
            JOIN LATERAL (SELECT e.status FROM control.recipe_release_events e
                WHERE e.release_id=r.id ORDER BY e.sequence_number DESC LIMIT 1) current
                ON true
            WHERE r.id=ANY(v_grant.recipe_release_ids) AND current.status='REVIEWED'
              AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind='recipe_release' AND t.target_id=r.id))
    THEN RETURN 'authority_changed'; END IF;
    RETURN 'active';
END $$;
REVOKE ALL ON FUNCTION control.weekly_cycle_authority(uuid,uuid,date,uuid,uuid,text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_cycle_authority(uuid,uuid,date,uuid,uuid,text)
TO signal_workflow;

CREATE FUNCTION control.admit_weekly_observation(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_cycle_id uuid,p_grant_id uuid,
    p_generation text,p_command_id uuid,p_event_id uuid,p_outbox_id uuid
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_cycle app.weekly_cycles%%ROWTYPE; v_fingerprint bytea;
BEGIN
    IF session_user != 'signal_workflow' OR p_command_id IS NULL OR p_event_id IS NULL
       OR p_outbox_id IS NULL THEN RETURN NULL; END IF;
    IF control.weekly_cycle_authority(
        p_tenant_id,p_site_id,p_week,p_cycle_id,p_grant_id,p_generation) != 'active'
    THEN RETURN NULL; END IF;
    SELECT * INTO v_cycle FROM app.weekly_cycles WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week FOR UPDATE;
    IF v_cycle.observe_command_id IS NOT NULL THEN RETURN v_cycle.observe_command_id; END IF;
    v_fingerprint := sha256(convert_to(
        'weekly-observe-v1:' || p_tenant_id || ':' || p_site_id || ':' || p_week,
        'UTF8'));
    INSERT INTO app.commands (tenant_id,id,site_id,actor_service,kind,schema_version,
        principal_key,route_key,scope_kind,idempotency_key,request_fingerprint,payload)
    VALUES (p_tenant_id,p_command_id,p_site_id,'weekly_loop','site.snapshot',1,
        'service:weekly_loop','internal.site.snapshot','site',p_cycle_id::text,
        v_fingerprint,'{"schema_version":1}'::jsonb);
    INSERT INTO app.command_events
        (tenant_id,site_id,id,command_id,event_number,event_type,facts)
    VALUES (p_tenant_id,p_site_id,p_event_id,p_command_id,1,'command.accepted',
        '{"schema_version":1}'::jsonb);
    INSERT INTO app.outbox
        (tenant_id,site_id,id,event_id,aggregate_kind,aggregate_id,event_type,
         schema_version,payload)
    VALUES (p_tenant_id,p_site_id,p_outbox_id,p_event_id,'command',p_command_id,
        'command.accepted',1,'{"schema_version":1}'::jsonb);
    UPDATE app.weekly_cycles SET observe_command_id=p_command_id
    WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND week_start=p_week;
    RETURN p_command_id;
END $$;
REVOKE ALL ON FUNCTION control.admit_weekly_observation(
    uuid,uuid,date,uuid,uuid,text,uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.admit_weekly_observation(
    uuid,uuid,date,uuid,uuid,text,uuid,uuid,uuid) TO signal_workflow;

CREATE FUNCTION control.weekly_observation_status(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_cycle_id uuid
) RETURNS TABLE (command_status text, result_reference jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    RETURN QUERY SELECT command.status,command.result_reference
    FROM app.weekly_cycles cycle JOIN app.commands command
      ON command.tenant_id=cycle.tenant_id AND command.site_id=cycle.site_id
     AND command.id=cycle.observe_command_id
    WHERE cycle.tenant_id=p_tenant_id AND cycle.site_id=p_site_id
      AND cycle.week_start=p_week AND cycle.id=p_cycle_id;
END $$;
REVOKE ALL ON FUNCTION control.weekly_observation_status(uuid,uuid,date,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_observation_status(uuid,uuid,date,uuid)
TO signal_workflow;

CREATE FUNCTION control.check_weekly_crawl_dispatch(
    p_tenant_id uuid,p_site_id uuid,p_crawl_run_id uuid
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_cycle app.weekly_cycles%%ROWTYPE;
        v_grant app.standing_authorizations%%ROWTYPE;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT cycle.* INTO v_cycle FROM app.weekly_cycles cycle
    JOIN app.crawl_runs run ON run.tenant_id=cycle.tenant_id
        AND run.site_id=cycle.site_id AND run.command_id=cycle.observe_command_id
    WHERE run.tenant_id=p_tenant_id AND run.site_id=p_site_id
      AND run.id=p_crawl_run_id FOR SHARE OF cycle;
    IF NOT FOUND THEN RETURN; END IF;
    PERFORM 1 FROM app.site_weekly_control WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND paused FOR SHARE;
    IF FOUND THEN
        RAISE EXCEPTION 'weekly crawl authority changed' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_grant FROM app.standing_authorizations
    WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=v_cycle.grant_id
    FOR SHARE;
    IF NOT FOUND OR v_cycle.status!='running'
       OR transaction_timestamp() NOT BETWEEN v_grant.starts_at AND v_grant.ends_at
       OR EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
           WHERE r.tenant_id=p_tenant_id AND r.site_id=p_site_id
             AND r.grant_id=v_cycle.grant_id)
       OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
           WHERE t.target_kind='standing_grant' AND t.target_id=v_cycle.grant_id)
       OR NOT EXISTS (SELECT 1 FROM app.sites s JOIN app.tenants t
           ON t.tenant_id=s.tenant_id WHERE s.tenant_id=p_tenant_id
             AND s.id=p_site_id AND s.state='active'
             AND s.ownership_status='verified' AND t.lifecycle='active')
       OR NOT EXISTS (SELECT 1 FROM app.memberships m JOIN app.site_memberships sm
           ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id
           WHERE m.tenant_id=p_tenant_id AND m.user_id=v_grant.owner_user_id
             AND m.role_key='owner' AND m.state='active'
             AND m.authorization_epoch=v_grant.membership_epoch
             AND sm.site_id=p_site_id AND sm.state='active'
             AND sm.authorization_epoch=v_grant.site_epoch)
    THEN RAISE EXCEPTION 'weekly crawl authority changed' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.check_weekly_crawl_dispatch(uuid,uuid,uuid) FROM PUBLIC;

CREATE FUNCTION control.guard_weekly_crawl_lease() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.status = 'leased' AND NEW.lease_id IS DISTINCT FROM OLD.lease_id THEN
        PERFORM control.check_weekly_crawl_dispatch(
            NEW.tenant_id,NEW.site_id,NEW.crawl_run_id);
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.guard_weekly_crawl_lease() FROM PUBLIC;
CREATE TRIGGER guard_weekly_crawl_lease BEFORE UPDATE ON app.crawl_frontier
FOR EACH ROW EXECUTE FUNCTION control.guard_weekly_crawl_lease();

CREATE FUNCTION control.guard_weekly_egress_dispatch() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM control.check_weekly_crawl_dispatch(
        NEW.tenant_id,NEW.site_id,NEW.crawl_run_id);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.guard_weekly_egress_dispatch() FROM PUBLIC;
CREATE TRIGGER guard_weekly_egress_dispatch BEFORE INSERT ON app.egress_operations
FOR EACH ROW EXECUTE FUNCTION control.guard_weekly_egress_dispatch();

CREATE FUNCTION control.open_weekly_cycle(
    p_tenant_id uuid, p_site_id uuid, p_grant_id uuid, p_week date, p_id uuid,
    p_first_run_id text,p_generation text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_grant app.standing_authorizations%%ROWTYPE;
BEGIN
    IF session_user != 'signal_workflow' OR p_week IS NULL OR p_id IS NULL
       OR p_first_run_id IS NULL
       OR p_first_run_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR extract(isodow FROM p_week) != 1
       OR p_week > (transaction_timestamp() AT TIME ZONE 'UTC')::date
       OR p_week < (transaction_timestamp() AT TIME ZONE 'UTC')::date - 7 THEN
        RETURN 'invalid_cycle';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    PERFORM 1 FROM app.site_weekly_control
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND paused FOR SHARE;
    IF FOUND THEN RETURN 'paused'; END IF;
    SELECT * INTO v_grant FROM app.standing_authorizations
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_grant_id FOR SHARE;
    IF NOT FOUND OR v_grant.recovery_generation IS DISTINCT FROM p_generation
       OR transaction_timestamp() NOT BETWEEN v_grant.starts_at AND v_grant.ends_at
       OR EXISTS (SELECT 1 FROM app.standing_authorization_revocations
                  WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND grant_id = p_grant_id)
       OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                  WHERE target_kind = 'standing_grant' AND target_id = p_grant_id)
       OR NOT EXISTS (SELECT 1 FROM app.sites s JOIN app.tenants t ON t.tenant_id=s.tenant_id
           WHERE s.tenant_id=p_tenant_id AND s.id=p_site_id AND s.state='active'
           AND s.ownership_status='verified' AND t.lifecycle='active')
       OR NOT EXISTS (SELECT 1 FROM app.memberships m JOIN app.site_memberships sm
           ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id
           WHERE m.tenant_id=p_tenant_id AND m.user_id=v_grant.owner_user_id
           AND m.state='active' AND m.role_key='owner'
           AND m.authorization_epoch=v_grant.membership_epoch
           AND sm.site_id=p_site_id AND sm.state='active'
           AND sm.authorization_epoch=v_grant.site_epoch)
       OR NOT EXISTS (SELECT 1 FROM control.recipe_releases r
            JOIN LATERAL (SELECT e.status FROM control.recipe_release_events e
                WHERE e.release_id=r.id ORDER BY e.sequence_number DESC LIMIT 1) current
                ON true
            WHERE r.id=ANY(v_grant.recipe_release_ids) AND current.status='REVIEWED'
              AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind='recipe_release' AND t.target_id=r.id))
    THEN RETURN 'authority_unavailable'; END IF;
    INSERT INTO app.weekly_cycles (tenant_id,site_id,week_start,id,grant_id,first_run_id)
    VALUES (p_tenant_id,p_site_id,p_week,p_id,p_grant_id,p_first_run_id)
    ON CONFLICT DO NOTHING;
    IF EXISTS (SELECT 1 FROM app.weekly_cycles WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week AND id=p_id AND grant_id=p_grant_id
        AND first_run_id=p_first_run_id)
    THEN RETURN 'opened'; END IF;
    RETURN 'cycle_exists';
END $$;
REVOKE ALL ON FUNCTION control.open_weekly_cycle(uuid,uuid,uuid,date,uuid,text,text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.open_weekly_cycle(uuid,uuid,uuid,date,uuid,text,text)
TO signal_workflow;

CREATE FUNCTION control.weekly_schedule_eligibility(
    p_tenant_id uuid,p_site_id uuid,p_grant_id uuid,p_generation text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_grant app.standing_authorizations%%ROWTYPE;
BEGIN
    IF session_user != 'signal_scheduler' THEN RETURN false; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    PERFORM 1 FROM app.site_weekly_control WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND paused FOR SHARE;
    IF FOUND THEN RETURN false; END IF;
    SELECT * INTO v_grant FROM app.standing_authorizations WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND id=p_grant_id FOR SHARE;
    RETURN FOUND AND v_grant.recovery_generation=p_generation
        AND transaction_timestamp() BETWEEN v_grant.starts_at AND v_grant.ends_at
        AND NOT EXISTS (SELECT 1 FROM app.standing_authorization_revocations
            WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND grant_id=p_grant_id)
        AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones
            WHERE target_kind='standing_grant' AND target_id=p_grant_id)
        AND EXISTS (SELECT 1 FROM app.sites s JOIN app.tenants t ON t.tenant_id=s.tenant_id
            WHERE s.tenant_id=p_tenant_id AND s.id=p_site_id AND s.state='active'
            AND s.ownership_status='verified' AND t.lifecycle='active')
        AND EXISTS (SELECT 1 FROM app.memberships m JOIN app.site_memberships sm
            ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id
            WHERE m.tenant_id=p_tenant_id AND m.user_id=v_grant.owner_user_id
              AND m.role_key='owner' AND m.state='active'
              AND m.authorization_epoch=v_grant.membership_epoch
              AND sm.site_id=p_site_id AND sm.state='active'
              AND sm.authorization_epoch=v_grant.site_epoch)
        AND EXISTS (SELECT 1 FROM control.recipe_releases r
            JOIN LATERAL (SELECT e.status FROM control.recipe_release_events e
                WHERE e.release_id=r.id ORDER BY e.sequence_number DESC LIMIT 1) current
                ON true
            WHERE r.id=ANY(v_grant.recipe_release_ids) AND current.status='REVIEWED'
              AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind='recipe_release' AND t.target_id=r.id));
END $$;
REVOKE ALL ON FUNCTION control.weekly_schedule_eligibility(uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_schedule_eligibility(uuid,uuid,uuid,text)
TO signal_scheduler;

CREATE FUNCTION control.record_weekly_stage(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_id uuid,p_grant_id uuid,
    p_stage text,p_outcome text,p_refs text[],p_detail text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_cycle app.weekly_cycles%%ROWTYPE;
BEGIN
    IF session_user != 'signal_workflow' OR p_stage NOT IN
        ('observe','analyze','plan','prepare','gate','handoff','verify','measure','report')
       OR p_outcome NOT IN ('completed','unavailable','deferred','waiting_owner','stopped','failed')
       OR p_detail !~ '^[A-Z][A-Z0-9_]{0,127}$' OR p_refs IS NULL
       OR cardinality(p_refs)>64 OR EXISTS (SELECT 1 FROM unnest(p_refs) ref
           WHERE length(ref) NOT BETWEEN 1 AND 512 OR ref !~ '^[A-Za-z0-9:/._#-]+$')
    THEN RETURN 'invalid_stage'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_cycle FROM app.weekly_cycles WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week FOR UPDATE;
    IF NOT FOUND OR v_cycle.id != p_id OR v_cycle.grant_id != p_grant_id
       OR v_cycle.status != 'running' THEN RETURN 'cycle_unavailable'; END IF;
    IF EXISTS (SELECT 1 FROM app.weekly_stage_results WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week AND stage=p_stage) THEN
        IF EXISTS (SELECT 1 FROM app.weekly_stage_results WHERE tenant_id=p_tenant_id
            AND site_id=p_site_id AND week_start=p_week AND stage=p_stage
            AND outcome=p_outcome AND evidence_refs=p_refs AND detail_code=p_detail)
        THEN RETURN 'recorded'; END IF;
        RETURN 'stage_conflict';
    END IF;
    INSERT INTO app.weekly_stage_results
        (tenant_id,site_id,week_start,stage,outcome,evidence_refs,detail_code)
    VALUES (p_tenant_id,p_site_id,p_week,p_stage,p_outcome,p_refs,p_detail);
    RETURN 'recorded';
END $$;
REVOKE ALL ON FUNCTION control.record_weekly_stage(
    uuid,uuid,date,uuid,uuid,text,text,text[],text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_weekly_stage(
    uuid,uuid,date,uuid,uuid,text,text,text[],text) TO signal_workflow;

CREATE FUNCTION control.read_weekly_stage(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_id uuid,p_stage text
) RETURNS TABLE (outcome text,detail_code text,evidence_refs text[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    RETURN QUERY SELECT s.outcome,s.detail_code,s.evidence_refs
    FROM app.weekly_cycles c JOIN app.weekly_stage_results s
      ON s.tenant_id=c.tenant_id AND s.site_id=c.site_id AND s.week_start=c.week_start
    WHERE c.tenant_id=p_tenant_id AND c.site_id=p_site_id AND c.week_start=p_week
      AND c.id=p_id AND s.stage=p_stage;
END $$;
REVOKE ALL ON FUNCTION control.read_weekly_stage(uuid,uuid,date,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_weekly_stage(uuid,uuid,date,uuid,text)
TO signal_workflow;

CREATE FUNCTION control.read_weekly_gate_outcome(
    p_tenant_id uuid,p_site_id uuid,p_decision_id uuid,p_grant_id uuid,
    p_recipe_release_id uuid,p_revision_sha256 bytea,p_operation_id uuid,
    p_work_type text,p_resource_path text,p_cost_cents bigint
) RETURNS TABLE (outcome text,reason text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    RETURN QUERY SELECT g.outcome,g.reason FROM app.autonomy_gate_records g
    WHERE g.tenant_id=p_tenant_id AND g.site_id=p_site_id AND g.id=p_decision_id
      AND g.grant_id=p_grant_id AND g.recipe_release_id=p_recipe_release_id
      AND g.sealed_revision_sha256=p_revision_sha256
      AND g.operation_id=p_operation_id AND g.work_type=p_work_type
      AND g.resource_path=p_resource_path AND g.cost_cents=p_cost_cents;
END $$;
REVOKE ALL ON FUNCTION control.read_weekly_gate_outcome(
    uuid,uuid,uuid,uuid,uuid,bytea,uuid,text,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_weekly_gate_outcome(
    uuid,uuid,uuid,uuid,uuid,bytea,uuid,text,text,bigint) TO signal_workflow;

CREATE FUNCTION control.record_weekly_handoff(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_cycle_id uuid,p_decision_id uuid,
    p_generation text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_gate app.autonomy_gate_records%%ROWTYPE; v_cycle app.weekly_cycles%%ROWTYPE;
        v_scope record;
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_cycle FROM app.weekly_cycles WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week FOR UPDATE;
    SELECT * INTO v_gate FROM app.autonomy_gate_records WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND id=p_decision_id;
    IF v_cycle.id IS DISTINCT FROM p_cycle_id OR v_cycle.status != 'running'
       OR v_gate.id IS NULL OR v_gate.grant_id != v_cycle.grant_id
       OR v_gate.outcome != 'ship' OR v_gate.reserved_operation_id IS NULL
       OR v_gate.recovery_generation IS DISTINCT FROM p_generation
       OR v_gate.decided_at < v_cycle.started_at THEN RETURN 'gate_unavailable'; END IF;
    SELECT * INTO v_scope FROM control.standing_grant_eligibility(
        p_tenant_id,p_site_id,v_gate.grant_id,v_gate.recovery_generation,
        v_gate.recipe_release_id,v_gate.work_type,v_gate.resource_path);
    IF NOT v_scope.eligible THEN RETURN 'authority_changed'; END IF;
    INSERT INTO app.weekly_handoffs
        (tenant_id,site_id,week_start,decision_id,operation_id,sealed_revision_sha256)
    VALUES (p_tenant_id,p_site_id,p_week,p_decision_id,v_gate.operation_id,
        v_gate.sealed_revision_sha256) ON CONFLICT DO NOTHING;
    IF EXISTS (SELECT 1 FROM app.weekly_handoffs WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND decision_id=p_decision_id AND week_start=p_week
        AND operation_id=v_gate.operation_id
        AND sealed_revision_sha256=v_gate.sealed_revision_sha256)
    THEN
        PERFORM control.resolve_weekly_deferral(
            p_tenant_id,p_site_id,p_week,p_cycle_id,
            v_gate.sealed_revision_sha256,p_decision_id);
        RETURN 'recorded';
    END IF;
    RETURN 'handoff_conflict';
END $$;
REVOKE ALL ON FUNCTION control.record_weekly_handoff(uuid,uuid,date,uuid,uuid,text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_weekly_handoff(uuid,uuid,date,uuid,uuid,text)
TO signal_workflow;

CREATE FUNCTION control.close_weekly_cycle(
    p_tenant_id uuid,p_site_id uuid,p_week date,p_id uuid,p_status text,p_reason text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user != 'signal_workflow' OR p_status NOT IN ('completed','stopped','failed')
       OR p_reason !~ '^[A-Z][A-Z0-9_]{0,127}$' THEN RETURN 'invalid_close'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    UPDATE app.weekly_cycles SET status=p_status, stop_reason=p_reason,
        closed_at=transaction_timestamp() WHERE tenant_id=p_tenant_id AND site_id=p_site_id
        AND week_start=p_week AND id=p_id AND status='running';
    IF FOUND THEN RETURN 'closed'; END IF;
    IF EXISTS (SELECT 1 FROM app.weekly_cycles WHERE tenant_id=p_tenant_id
        AND site_id=p_site_id AND week_start=p_week AND id=p_id
        AND status=p_status AND stop_reason=p_reason) THEN RETURN 'closed'; END IF;
    RETURN 'cycle_conflict';
END $$;
REVOKE ALL ON FUNCTION control.close_weekly_cycle(uuid,uuid,date,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.close_weekly_cycle(uuid,uuid,date,uuid,text,text)
TO signal_workflow;

CREATE FUNCTION control.read_weekly_cycle_report(
    p_session_hash bytea,p_site_id uuid,p_generation text,p_week date
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_cycle app.weekly_cycles%%ROWTYPE;
BEGIN
    IF session_user != 'signal_api' OR p_week IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash,p_site_id,p_generation);
    IF v_authority.outcome != 'authorized' OR v_authority.role_key != 'owner' THEN
        RETURN NULL;
    END IF;
    PERFORM set_config('signal.tenant_id', v_authority.tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_cycle FROM app.weekly_cycles WHERE tenant_id=v_authority.tenant_id
        AND site_id=p_site_id AND week_start=p_week;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object(
        'schema_version',1,'cycle_id',v_cycle.id,'site_id',p_site_id,
        'week_start',p_week,'status',v_cycle.status,'stop_reason',v_cycle.stop_reason,
        'started_at',v_cycle.started_at,'closed_at',v_cycle.closed_at,
        'observation_command',CASE WHEN v_cycle.observe_command_id IS NULL THEN NULL
            ELSE (SELECT jsonb_build_object('command_id',command.id,
                'status',command.status,'result_reference',command.result_reference,
                'status_url','/v1/sites/' || p_site_id::text || '/weekly-cycles/' ||
                    p_week::text || '/observation/' || command.id::text)
                FROM app.commands command WHERE command.tenant_id=v_authority.tenant_id
                  AND command.site_id=p_site_id AND command.id=v_cycle.observe_command_id)
            END,
        'stages',COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'stage',s.stage,'outcome',s.outcome,'detail_code',s.detail_code,
            'evidence_refs',s.evidence_refs,'recorded_at',s.recorded_at)
            ORDER BY array_position(ARRAY['observe','analyze','plan','prepare','gate',
                'handoff','verify','measure','report'],s.stage))
            FROM app.weekly_stage_results s WHERE s.tenant_id=v_authority.tenant_id
                AND s.site_id=p_site_id AND s.week_start=p_week),'[]'::jsonb),
        'gate_decisions',COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'decision_id',g.id,'outcome',g.outcome,'reason',g.reason,
            'revision_sha256',encode(g.sealed_revision_sha256,'hex'),
            'decided_at',g.decided_at) ORDER BY g.decided_at,g.id)
            FROM app.weekly_stage_results s
            JOIN LATERAL unnest(s.evidence_refs) ref ON ref LIKE 'gate:%%'
            JOIN app.autonomy_gate_records g ON g.tenant_id=s.tenant_id
                AND g.site_id=s.site_id AND g.id=CASE WHEN ref ~
                    '^gate:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN substring(ref FROM 6)::uuid ELSE NULL END
            WHERE s.tenant_id=v_authority.tenant_id AND s.site_id=p_site_id
                AND s.week_start=p_week AND s.stage='gate'),'[]'::jsonb),
        'waiting_for_owner',COALESCE((SELECT jsonb_agg(g.id ORDER BY g.decided_at,g.id)
            FROM app.weekly_stage_results s
            JOIN LATERAL unnest(s.evidence_refs) ref ON ref LIKE 'gate:%%'
            JOIN app.autonomy_gate_records g ON g.tenant_id=s.tenant_id
                AND g.site_id=s.site_id AND g.id=CASE WHEN ref ~
                    '^gate:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN substring(ref FROM 6)::uuid ELSE NULL END
            WHERE s.tenant_id=v_authority.tenant_id AND s.site_id=p_site_id
                AND s.week_start=p_week AND s.stage='gate'
                AND g.outcome='ask_owner'),'[]'::jsonb),
        'handoffs',COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'decision_id',h.decision_id,'operation_id',h.operation_id,
            'revision_sha256',encode(h.sealed_revision_sha256,'hex'),
            'recorded_at',h.recorded_at) ORDER BY h.recorded_at)
            FROM app.weekly_handoffs h WHERE h.tenant_id=v_authority.tenant_id
                AND h.site_id=p_site_id AND h.week_start=p_week),'[]'::jsonb),
        'deferred',COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'revision_sha256',encode(d.sealed_revision_sha256,'hex'),
            'next_week',d.next_week,'reason',d.reason,
            'resolved_week',r.resolved_week) ORDER BY d.recorded_at)
            FROM app.weekly_deferred_revisions d
            LEFT JOIN app.weekly_deferral_resolutions r
                ON r.tenant_id=d.tenant_id AND r.site_id=d.site_id
                AND r.source_week=d.source_week
                AND r.sealed_revision_sha256=d.sealed_revision_sha256
            WHERE d.tenant_id=v_authority.tenant_id AND d.site_id=p_site_id
                AND d.source_week=p_week),'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION control.read_weekly_cycle_report(bytea,uuid,text,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_weekly_cycle_report(bytea,uuid,text,date)
TO signal_api;
