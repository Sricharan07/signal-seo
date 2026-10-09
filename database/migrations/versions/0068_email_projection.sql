-- One committed owner-visible projection feeds both the dashboard and email.
CREATE FUNCTION control.weekly_report_projection(p_tenant uuid,p_site uuid,p_week date)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c app.weekly_cycles%%ROWTYPE; report jsonb;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO c FROM app.weekly_cycles WHERE tenant_id=p_tenant AND site_id=p_site AND week_start=p_week;
    IF NOT FOUND THEN RETURN NULL; END IF;
    report:=jsonb_build_object('schema_version',1,'cycle_id',c.id,'site_id',p_site,
        'week_start',p_week,'status',c.status,'stop_reason',c.stop_reason,
        'started_at',c.started_at,'closed_at',c.closed_at,
        'observation_command',CASE WHEN c.observe_command_id IS NULL THEN NULL ELSE
            (SELECT jsonb_build_object('command_id',command.id,'status',command.status,
                'result_reference',command.result_reference,'status_url','/v1/sites/'||p_site::text||
                '/weekly-cycles/'||p_week::text||'/observation/'||command.id::text)
             FROM app.commands command WHERE command.tenant_id=p_tenant AND command.site_id=p_site
                AND command.id=c.observe_command_id) END,
        'stages',COALESCE((SELECT jsonb_agg(jsonb_build_object('stage',s.stage,'outcome',s.outcome,
            'detail_code',s.detail_code,'evidence_refs',s.evidence_refs,'recorded_at',s.recorded_at)
            ORDER BY array_position(ARRAY['observe','analyze','plan','prepare','gate','handoff',
                'verify','measure','report'],s.stage)) FROM app.weekly_stage_results s
            WHERE s.tenant_id=p_tenant AND s.site_id=p_site AND s.week_start=p_week),'[]'::jsonb),
        'gate_decisions',COALESCE((SELECT jsonb_agg(jsonb_build_object('decision_id',g.id,
            'outcome',g.outcome,'reason',g.reason,'revision_sha256',encode(g.sealed_revision_sha256,'hex'),
            'decided_at',g.decided_at) ORDER BY g.decided_at,g.id)
            FROM app.weekly_stage_results s JOIN LATERAL unnest(s.evidence_refs) ref ON ref LIKE 'gate:%%'
            JOIN app.autonomy_gate_records g ON g.tenant_id=s.tenant_id AND g.site_id=s.site_id
            AND g.id=CASE WHEN ref ~ '^gate:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN substring(ref FROM 6)::uuid ELSE NULL END
            WHERE s.tenant_id=p_tenant AND s.site_id=p_site AND s.week_start=p_week AND s.stage='gate'),'[]'::jsonb),
        'waiting_for_owner',COALESCE((SELECT jsonb_agg(g.id ORDER BY g.decided_at,g.id)
            FROM app.weekly_stage_results s JOIN LATERAL unnest(s.evidence_refs) ref ON ref LIKE 'gate:%%'
            JOIN app.autonomy_gate_records g ON g.tenant_id=s.tenant_id AND g.site_id=s.site_id
            AND g.id=CASE WHEN ref ~ '^gate:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN substring(ref FROM 6)::uuid ELSE NULL END
            WHERE s.tenant_id=p_tenant AND s.site_id=p_site AND s.week_start=p_week
                AND s.stage='gate' AND g.outcome='ask_owner'),'[]'::jsonb),
        'handoffs',COALESCE((SELECT jsonb_agg(jsonb_build_object('decision_id',h.decision_id,
            'operation_id',h.operation_id,'revision_sha256',encode(h.sealed_revision_sha256,'hex'),
            'recorded_at',h.recorded_at) ORDER BY h.recorded_at) FROM app.weekly_handoffs h
            WHERE h.tenant_id=p_tenant AND h.site_id=p_site AND h.week_start=p_week),'[]'::jsonb),
        'deferred',COALESCE((SELECT jsonb_agg(jsonb_build_object('revision_sha256',
            encode(d.sealed_revision_sha256,'hex'),'next_week',d.next_week,'reason',d.reason,
            'resolved_week',r.resolved_week) ORDER BY d.recorded_at) FROM app.weekly_deferred_revisions d
            LEFT JOIN app.weekly_deferral_resolutions r ON r.tenant_id=d.tenant_id AND r.site_id=d.site_id
                AND r.source_week=d.source_week AND r.sealed_revision_sha256=d.sealed_revision_sha256
            WHERE d.tenant_id=p_tenant AND d.site_id=p_site AND d.source_week=p_week),'[]'::jsonb));
    RETURN report||jsonb_build_object('delivery',COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'workload_id',j.id,'finding_id',j.finding_id,'recipe_release_id',j.recipe_release_id,
            'revision_id',j.revision_id,'revision_sha256',encode(r.revision_sha256,'hex'),
            'operation_id',o.id,'operation_state',o.state,'pr_url',o.pr_url,'authority_kind',o.authority_kind,
            'authority_id',COALESCE(o.decision_id,o.standing_dispatch_id),'decision_channel',o.decision_channel,
            'authorization_owner_id',o.requested_by_user_id,'review_status',d.decision,
            'observation_id',receipt.attempt_id,'observation_sha256',encode(receipt.receipt_sha256,'hex'),
            'delivery_outcome',receipt.document->>'outcome','delivery_reason',receipt.document->>'reason',
            'delivery_stage',receipt.document->'provider'->>'stage') ORDER BY j.created_at,j.id)
        FROM app.weekly_delivery_workloads j LEFT JOIN app.candidate_recipe_revisions r
            ON r.tenant_id=j.tenant_id AND r.site_id=j.site_id AND r.id=j.revision_id
        LEFT JOIN app.github_pr_operations o ON o.tenant_id=j.tenant_id AND o.site_id=j.site_id AND o.id=j.operation_id
        LEFT JOIN app.candidate_recipe_review_decisions d ON d.tenant_id=j.tenant_id AND d.site_id=j.site_id
            AND d.candidate_revision_id=j.revision_id
        LEFT JOIN LATERAL (SELECT x.attempt_id,x.receipt_sha256,
            convert_from(x.canonical_receipt,'UTF8')::jsonb AS document FROM app.github_delivery_receipts x
            WHERE x.tenant_id=j.tenant_id AND x.site_id=j.site_id AND x.operation_id=j.operation_id
            ORDER BY x.recorded_at DESC,x.attempt_id DESC LIMIT 1) receipt ON true
        WHERE j.tenant_id=p_tenant AND j.site_id=p_site AND (j.cycle_id=c.id OR EXISTS(
            SELECT 1 FROM app.weekly_stage_results stage WHERE stage.tenant_id=j.tenant_id
            AND stage.site_id=j.site_id AND stage.week_start=p_week
            AND ('operation:'||j.operation_id::text)=ANY(stage.evidence_refs)))),'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION control.weekly_report_projection(uuid,uuid,date) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.read_weekly_delivery_report(
    p_session_hash bytea,p_site_id uuid,p_generation text,p_week date
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    IF session_user<>'signal_api' OR p_week IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key<>'owner' THEN RETURN NULL; END IF;
    RETURN control.weekly_report_projection(a.tenant_id,p_site_id,p_week);
END $$;

CREATE TABLE control.email_queue_failures (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL,site_id uuid NOT NULL,
    event_id uuid NOT NULL,category text NOT NULL,error_class text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    CHECK(error_class IN ('bounded_projection_rejected','queue_constraint_rejected'))
);
REVOKE ALL ON control.email_queue_failures FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,
    signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest;
CREATE TRIGGER email_queue_failure_immutable BEFORE UPDATE OR DELETE ON control.email_queue_failures
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.queue_email_event(p_tenant uuid,p_site uuid,p_event uuid,p_category text,p_projection jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE cfg control.email_smtp_configuration%%ROWTYPE; p app.email_preferences%%ROWTYPE;
    member record; v_id uuid;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO cfg FROM control.email_smtp_configuration;
    IF NOT FOUND THEN RETURN; END IF;
    IF p_projection IS NULL OR octet_length(p_projection::text)>131072 THEN
        INSERT INTO control.email_queue_failures(tenant_id,site_id,event_id,category,error_class)
            VALUES(p_tenant,p_site,p_event,p_category,'bounded_projection_rejected');
        RETURN;
    END IF;
    FOR member IN SELECT * FROM app.memberships WHERE tenant_id=p_tenant AND role_key='owner' LOOP
        SELECT * INTO p FROM app.email_preferences WHERE tenant_id=p_tenant AND membership_id=member.id
            ORDER BY preference_order DESC LIMIT 1;
        IF NOT FOUND THEN
            INSERT INTO app.email_preferences(tenant_id,id,membership_id,user_id,enabled,
                membership_epoch,recovery_generation)
            VALUES(p_tenant,gen_random_uuid(),member.id,member.user_id,false,member.authorization_epoch,'unverified')
            RETURNING * INTO p;
        END IF;
        v_id:=gen_random_uuid();
        INSERT INTO app.email_outbox(tenant_id,site_id,id,membership_id,preference_id,event_id,
            category,projection,configuration_sha256)
        VALUES(p_tenant,p_site,v_id,p.membership_id,p.id,p_event,p_category,p_projection,cfg.configuration_sha256)
        ON CONFLICT(tenant_id,site_id,event_id,membership_id,category) DO NOTHING;
        IF FOUND THEN INSERT INTO control.email_outbox_routes(id,tenant_id,site_id)
            VALUES(v_id,p_tenant,p_site); END IF;
    END LOOP;
EXCEPTION WHEN integrity_constraint_violation THEN
    -- Notification constraints must not roll back a committed authority reduction.
    INSERT INTO control.email_queue_failures(tenant_id,site_id,event_id,category,error_class)
        VALUES(p_tenant,p_site,p_event,p_category,'queue_constraint_rejected');
END $$;
REVOKE ALL ON FUNCTION control.queue_email_event(uuid,uuid,uuid,text,jsonb) FROM PUBLIC;

CREATE FUNCTION control.queue_weekly_email() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF OLD.status='running' AND NEW.status IN ('completed','stopped','failed') THEN
        PERFORM control.queue_email_event(NEW.tenant_id,NEW.site_id,NEW.id,'weekly_report',
            control.weekly_report_projection(NEW.tenant_id,NEW.site_id,NEW.week_start));
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.queue_weekly_email() FROM PUBLIC;
CREATE TRIGGER weekly_email_after_close AFTER UPDATE ON app.weekly_cycles
    FOR EACH ROW EXECUTE FUNCTION control.queue_weekly_email();

CREATE FUNCTION control.queue_email_alert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_category text; v_id uuid;
BEGIN
    IF TG_TABLE_NAME='site_weekly_control' THEN
        IF NOT NEW.paused OR (TG_OP='UPDATE' AND OLD.paused) THEN RETURN NEW; END IF;
        v_category:='pause';v_id:=gen_random_uuid();
    ELSIF TG_TABLE_NAME='standing_authorization_revocations' THEN
        v_category:='revocation';v_id:=NEW.id;
    ELSIF TG_TABLE_NAME='email_send_receipts' THEN
        IF NEW.provider_response_class IN ('unknown','permanent','bounced')
            OR (NEW.provider_response_class='transient' AND NEW.attempt_number=3) THEN v_category:='failed_delivery';
        ELSIF NEW.provider_response_class='stale_binding' THEN v_category:='stale_binding';
        ELSE RETURN NEW; END IF;
        IF EXISTS(SELECT 1 FROM app.email_outbox o WHERE o.tenant_id=NEW.tenant_id AND o.site_id=NEW.site_id
            AND o.id=NEW.outbox_id AND o.category<>'weekly_report') THEN RETURN NEW; END IF;
        v_id:=NEW.id;
    ELSIF TG_TABLE_NAME='github_pr_operation_events' THEN
        IF NEW.event_kind NOT IN ('blocked','outcome_unknown') THEN RETURN NEW; END IF;
        v_category:='failed_delivery';v_id:=NEW.id;
    ELSIF TG_TABLE_NAME='github_read_binding_events' THEN
        IF NEW.event_kind='revoked' THEN v_category:='revocation';
        ELSIF NEW.event_kind='failed' THEN v_category:='stale_binding';
        ELSE RETURN NEW; END IF;
        v_id:=NEW.id;
    ELSIF TG_TABLE_NAME='github_delivery_receipts' THEN
        IF convert_from(NEW.canonical_receipt,'UTF8')::jsonb->>'outcome'<>'regressed' THEN RETURN NEW; END IF;
        v_category:='failed_delivery';v_id:=NEW.attempt_id;
    END IF;
    PERFORM control.queue_email_event(NEW.tenant_id,NEW.site_id,v_id,v_category,'{}'::jsonb);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.queue_email_alert() FROM PUBLIC;
CREATE TRIGGER email_pause_alert AFTER INSERT OR UPDATE ON app.site_weekly_control
    FOR EACH ROW EXECUTE FUNCTION control.queue_email_alert();
CREATE TRIGGER email_revocation_alert AFTER INSERT ON app.standing_authorization_revocations
    FOR EACH ROW EXECUTE FUNCTION control.queue_email_alert();
CREATE TRIGGER email_delivery_alert AFTER INSERT ON app.email_send_receipts
    FOR EACH ROW EXECUTE FUNCTION control.queue_email_alert();
CREATE TRIGGER email_pr_failure_alert AFTER INSERT ON app.github_pr_operation_events
    FOR EACH ROW EXECUTE FUNCTION control.queue_email_alert();
CREATE TRIGGER email_github_binding_alert AFTER INSERT ON app.github_read_binding_events
    FOR EACH ROW EXECUTE FUNCTION control.queue_email_alert();
CREATE TRIGGER email_live_regression_alert AFTER INSERT ON app.github_delivery_receipts
    FOR EACH ROW EXECUTE FUNCTION control.queue_email_alert();
