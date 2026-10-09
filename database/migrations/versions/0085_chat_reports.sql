CREATE TABLE control.chat_report_configuration (
    provider text PRIMARY KEY CHECK(provider IN ('slack','telegram')),
    dashboard_origin text NOT NULL CHECK(dashboard_origin ~ '^https://[a-z0-9.-]+(:[0-9]+)?$'),
    daily_cap integer NOT NULL CHECK(daily_cap BETWEEN 1 AND 100),
    configuration_sha256 bytea NOT NULL CHECK(octet_length(configuration_sha256)=32)
);
CREATE FUNCTION control.configure_chat_reports(p_provider text,p_origin text,p_cap integer)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_bootstrap' THEN RAISE EXCEPTION 'chat_configuration_denied'; END IF;
    INSERT INTO control.chat_report_configuration VALUES(p_provider,p_origin,p_cap,
        sha256(convert_to(jsonb_build_array(p_provider,p_origin,p_cap)::text,'UTF8')))
    ON CONFLICT(provider) DO UPDATE SET dashboard_origin=EXCLUDED.dashboard_origin,
        daily_cap=EXCLUDED.daily_cap,configuration_sha256=EXCLUDED.configuration_sha256;
END $$;
REVOKE ALL ON FUNCTION control.configure_chat_reports(text,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.configure_chat_reports(text,text,integer) TO signal_bootstrap;

CREATE TABLE app.chat_report_preferences (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,user_id uuid NOT NULL,
    channel text NOT NULL CHECK(channel IN ('slack_channel','slack_dm','telegram')),
    enabled boolean NOT NULL,binding_id uuid,link_id uuid,
    membership_epoch bigint NOT NULL,site_epoch bigint NOT NULL,membership_change_count bigint NOT NULL,
    recovery_generation text NOT NULL,preference_order bigint GENERATED ALWAYS AS IDENTITY,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id),
    CHECK(NOT enabled OR (binding_id IS NOT NULL AND (channel='slack_channel' OR link_id IS NOT NULL)))
);
CREATE INDEX chat_preference_latest ON app.chat_report_preferences(tenant_id,site_id,user_id,channel,preference_order DESC);
CREATE TABLE app.chat_report_outbox (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL UNIQUE,
    event_id uuid NOT NULL,category text NOT NULL CHECK(category IN
        ('weekly_report','pause','revocation','failed_delivery','stale_binding')),
    preference_id uuid NOT NULL,user_id uuid NOT NULL,channel text NOT NULL,
    binding_id uuid NOT NULL,link_id uuid,destination text NOT NULL,
    projection jsonb NOT NULL CHECK(jsonb_typeof(projection)='object' AND octet_length(projection::text)<=131072),
    configuration_sha256 bytea NOT NULL CHECK(octet_length(configuration_sha256)=32),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','retry','dispatching','accepted','unknown','suppressed','failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
    ready_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    UNIQUE(tenant_id,site_id,event_id,category,channel,binding_id,destination),
    FOREIGN KEY(tenant_id,site_id,preference_id) REFERENCES app.chat_report_preferences(tenant_id,site_id,id),
    CHECK((channel='slack_channel' AND destination ~ '^[CG][A-Z0-9]{7,63}$' AND link_id IS NULL)
        OR (channel='slack_dm' AND destination ~ '^[UW][A-Z0-9]{7,63}$' AND link_id IS NOT NULL)
        OR (channel='telegram' AND destination ~ '^[1-9][0-9]{0,15}$' AND link_id IS NOT NULL))
);
CREATE TABLE control.chat_report_routes (
    id uuid PRIMARY KEY,tenant_id uuid NOT NULL,site_id uuid NOT NULL,
    current_state text NOT NULL DEFAULT 'queued',ready_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY(tenant_id,site_id,id) REFERENCES app.chat_report_outbox(tenant_id,site_id,id)
);
CREATE TABLE app.chat_report_receipts (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),
    outbox_id uuid NOT NULL,attempt_number integer NOT NULL CHECK(attempt_number BETWEEN 0 AND 3),
    phase text NOT NULL CHECK(phase IN ('dispatch','completion','suppression')),
    outcome text NOT NULL CHECK(outcome IN ('dispatching','accepted','unknown','deferred','retry_exhausted',
        'opted_out','authority_denied','stale_binding','destination_unavailable','provider_unavailable',
        'cap_reached','render_rejected')),
    message_sha256 bytea CHECK(message_sha256 IS NULL OR octet_length(message_sha256)=32),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),UNIQUE(tenant_id,site_id,outbox_id,attempt_number,phase),
    FOREIGN KEY(tenant_id,site_id,outbox_id) REFERENCES app.chat_report_outbox(tenant_id,site_id,id)
);
CREATE INDEX chat_report_site_cap ON app.chat_report_receipts(tenant_id,site_id,created_at) WHERE phase='dispatch';
CREATE TABLE control.chat_report_queue_failures (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL,site_id uuid NOT NULL,event_id uuid NOT NULL,
    category text NOT NULL,error_class text NOT NULL CHECK(error_class IN ('bounded_projection_rejected','queue_constraint_rejected')),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['chat_report_preferences','chat_report_outbox','chat_report_receipts'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY chat_report_scope ON app.%%I USING '
            '(tenant_id=app.current_tenant_id()) WITH CHECK (tenant_id=app.current_tenant_id())',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,'
            'signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END $$;
REVOKE ALL ON control.chat_report_configuration,control.chat_report_routes,control.chat_report_queue_failures
    FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest;
CREATE TRIGGER chat_preferences_immutable BEFORE UPDATE OR DELETE ON app.chat_report_preferences
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER chat_receipts_immutable BEFORE UPDATE OR DELETE ON app.chat_report_receipts
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER chat_queue_failure_immutable BEFORE UPDATE OR DELETE ON control.chat_report_queue_failures
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE FUNCTION app.guard_chat_report_outbox() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['state','attempt_count','ready_at']) IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['state','attempt_count','ready_at']) THEN RAISE EXCEPTION 'immutable_chat_report'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app.guard_chat_report_outbox() FROM PUBLIC;
CREATE TRIGGER chat_report_guard BEFORE UPDATE OR DELETE ON app.chat_report_outbox
    FOR EACH ROW EXECUTE FUNCTION app.guard_chat_report_outbox();
CREATE FUNCTION control.route_chat_report_state() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    UPDATE control.chat_report_routes SET current_state=NEW.state,ready_at=NEW.ready_at WHERE id=NEW.id;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.route_chat_report_state() FROM PUBLIC;
CREATE TRIGGER chat_report_route AFTER UPDATE ON app.chat_report_outbox
    FOR EACH ROW EXECUTE FUNCTION control.route_chat_report_state();
CREATE INDEX chat_report_due ON control.chat_report_routes(ready_at,id) WHERE current_state IN ('queued','retry','dispatching');

-- A single closed projection of the existing bindings; no arbitrary destinations.
CREATE FUNCTION control.chat_report_destination(p_tenant uuid,p_site uuid,p_user uuid,p_channel text,p_generation text)
RETURNS TABLE(binding_id uuid,link_id uuid,destination text,secret_reference text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_member_site_authority(p_tenant,p_user,p_site,'primary');
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key<>'owner' THEN RETURN; END IF;
    IF p_channel='slack_channel' THEN
        RETURN QUERY SELECT b.id,NULL::uuid,b.channel_id,b.secret_reference FROM app.slack_bindings b
            WHERE b.tenant_id=p_tenant AND b.site_id=p_site AND b.owner_user_id=p_user
            AND b.revoked_at IS NULL AND b.recovery_generation=p_generation
            AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d
                WHERE d.target_kind='slack_binding' AND d.target_id=b.id);
    ELSIF p_channel='slack_dm' THEN
        RETURN QUERY SELECT b.id,l.id,l.slack_user_id,b.secret_reference FROM app.slack_bindings b
            JOIN app.slack_links l ON l.tenant_id=b.tenant_id AND l.site_id=b.site_id AND l.binding_id=b.id
            CROSS JOIN LATERAL control.resolve_slack_link_authority(l.id,p_generation) r
            WHERE b.tenant_id=p_tenant AND b.site_id=p_site AND l.user_id=p_user
            AND r.outcome='authorized' AND r.role_key='owner';
    ELSIF p_channel='telegram' THEN
        RETURN QUERY SELECT b.id,l.id,l.chat_id,b.secret_reference FROM app.telegram_bindings b
            JOIN app.telegram_links l ON l.tenant_id=b.tenant_id AND l.site_id=b.site_id AND l.binding_id=b.id
            CROSS JOIN LATERAL control.resolve_telegram_link_authority(l.id,p_generation) r
            WHERE b.tenant_id=p_tenant AND b.site_id=p_site AND l.user_id=p_user
            AND l.chat_id=l.telegram_user_id AND l.chat_id ~ '^[1-9][0-9]{0,15}$'
            AND r.outcome='authorized' AND r.role_key='owner';
    END IF;
END $$;
REVOKE ALL ON FUNCTION control.chat_report_destination(uuid,uuid,uuid,text,text) FROM PUBLIC;

CREATE FUNCTION control.set_chat_report_preference(p_session bytea,p_site uuid,p_generation text,
    p_channel text,p_enabled boolean,p_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; d record;
BEGIN
    IF session_user<>'signal_api' OR p_channel NOT IN ('slack_channel','slack_dm','telegram')
        OR p_channel IS NULL OR p_enabled IS NULL THEN RETURN 'denied'; END IF;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key<>'owner' THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
    SELECT * INTO d FROM control.chat_report_destination(a.tenant_id,p_site,a.user_id,p_channel,p_generation);
    IF p_enabled AND NOT EXISTS(SELECT 1 FROM control.chat_report_configuration
        WHERE provider=CASE WHEN p_channel='telegram' THEN 'telegram' ELSE 'slack' END) THEN RETURN 'unavailable'; END IF;
    IF p_enabled AND d.binding_id IS NULL THEN RETURN 'destination_unavailable'; END IF;
    INSERT INTO app.chat_report_preferences(tenant_id,site_id,id,user_id,channel,enabled,binding_id,link_id,
        membership_epoch,site_epoch,membership_change_count,recovery_generation)
    VALUES(a.tenant_id,p_site,p_id,a.user_id,p_channel,p_enabled,d.binding_id,d.link_id,a.membership_epoch,
        a.site_authorization_epoch,(SELECT count(*) FROM control.email_membership_changes
            WHERE tenant_id=a.tenant_id AND membership_id=(SELECT id FROM app.memberships
                WHERE tenant_id=a.tenant_id AND user_id=a.user_id)),p_generation);
    RETURN CASE WHEN p_enabled THEN 'enabled' ELSE 'disabled' END;
END $$;
REVOKE ALL ON FUNCTION control.set_chat_report_preference(bytea,uuid,text,text,boolean,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.set_chat_report_preference(bytea,uuid,text,text,boolean,uuid) TO signal_api;

CREATE FUNCTION control.chat_report_suppression(p_id uuid,p_generation text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.chat_report_outbox%%ROWTYPE;p app.chat_report_preferences%%ROWTYPE;a record;d record;cfg record;
BEGIN
    SELECT * INTO o FROM app.chat_report_outbox WHERE id=p_id;
    IF NOT FOUND THEN RETURN 'authority_denied'; END IF;
    SELECT * INTO p FROM app.chat_report_preferences WHERE tenant_id=o.tenant_id AND site_id=o.site_id
        AND user_id=o.user_id AND channel=o.channel ORDER BY preference_order DESC LIMIT 1;
    IF NOT p.enabled OR p.id IS DISTINCT FROM o.preference_id THEN RETURN 'opted_out'; END IF;
    SELECT * INTO a FROM control.resolve_member_site_authority(o.tenant_id,o.user_id,o.site_id,'primary');
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key<>'owner'
        OR a.membership_epoch IS DISTINCT FROM p.membership_epoch
        OR a.site_authorization_epoch IS DISTINCT FROM p.site_epoch
        OR p.recovery_generation IS DISTINCT FROM p_generation
        OR p.membership_change_count<>(SELECT count(*) FROM control.email_membership_changes c
            JOIN app.memberships m ON m.tenant_id=c.tenant_id AND m.id=c.membership_id
            WHERE m.tenant_id=o.tenant_id AND m.user_id=o.user_id) THEN RETURN 'authority_denied'; END IF;
    SELECT * INTO d FROM control.chat_report_destination(o.tenant_id,o.site_id,o.user_id,o.channel,p_generation);
    IF d.binding_id IS DISTINCT FROM o.binding_id OR d.link_id IS DISTINCT FROM o.link_id
        OR d.destination IS DISTINCT FROM o.destination THEN RETURN 'destination_unavailable'; END IF;
    SELECT * INTO cfg FROM control.chat_report_configuration WHERE provider=CASE WHEN o.channel='telegram' THEN 'telegram' ELSE 'slack' END;
    IF cfg.provider IS NULL THEN RETURN 'provider_unavailable'; END IF;
    IF cfg.configuration_sha256 IS DISTINCT FROM o.configuration_sha256 THEN RETURN 'stale_binding'; END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION control.chat_report_suppression(uuid,text) FROM PUBLIC;

CREATE FUNCTION control.queue_chat_report_event(p_tenant uuid,p_site uuid,p_event uuid,p_category text,p_projection jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p app.chat_report_preferences%%ROWTYPE;cfg record;d record;v_id uuid;v_dest text;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    IF p_projection IS NULL OR octet_length(p_projection::text)>131072 THEN
        INSERT INTO control.chat_report_queue_failures(tenant_id,site_id,event_id,category,error_class)
            VALUES(p_tenant,p_site,p_event,p_category,'bounded_projection_rejected'); RETURN;
    END IF;
    FOR p IN SELECT DISTINCT ON (user_id,channel) * FROM app.chat_report_preferences
        WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY user_id,channel,preference_order DESC LOOP
        IF p.binding_id IS NULL THEN CONTINUE; END IF;
        SELECT * INTO cfg FROM control.chat_report_configuration WHERE provider=CASE WHEN p.channel='telegram' THEN 'telegram' ELSE 'slack' END;
        IF NOT FOUND THEN CONTINUE; END IF;
        -- Retain disabled/revoked intent as an audited suppression, not a revived recipient.
        IF p.channel='slack_channel' THEN SELECT channel_id INTO v_dest FROM app.slack_bindings WHERE id=p.binding_id;
        ELSIF p.channel='slack_dm' THEN SELECT slack_user_id INTO v_dest FROM app.slack_links WHERE id=p.link_id;
        ELSE SELECT chat_id INTO v_dest FROM app.telegram_links WHERE id=p.link_id; END IF;
        IF v_dest IS NULL THEN CONTINUE; END IF;
        v_id:=gen_random_uuid();
        INSERT INTO app.chat_report_outbox(tenant_id,site_id,id,event_id,category,preference_id,user_id,
            channel,binding_id,link_id,destination,projection,configuration_sha256)
        VALUES(p_tenant,p_site,v_id,p_event,p_category,p.id,p.user_id,p.channel,p.binding_id,p.link_id,v_dest,p_projection,cfg.configuration_sha256)
        ON CONFLICT(tenant_id,site_id,event_id,category,channel,binding_id,destination) DO NOTHING;
        IF FOUND THEN INSERT INTO control.chat_report_routes(id,tenant_id,site_id) VALUES(v_id,p_tenant,p_site); END IF;
    END LOOP;
EXCEPTION WHEN integrity_constraint_violation THEN
    INSERT INTO control.chat_report_queue_failures(tenant_id,site_id,event_id,category,error_class)
        VALUES(p_tenant,p_site,p_event,p_category,'queue_constraint_rejected');
END $$;
REVOKE ALL ON FUNCTION control.queue_chat_report_event(uuid,uuid,uuid,text,jsonb) FROM PUBLIC;

CREATE FUNCTION control.chat_report_item(p_id uuid,p_generation text,p_message bytea DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE route record;o app.chat_report_outbox%%ROWTYPE;cfg record;d record;reason text;v_count integer;
BEGIN
    IF session_user<>'signal_identity' THEN RETURN NULL; END IF;
    SELECT * INTO route FROM control.chat_report_routes WHERE id=p_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',route.tenant_id::text,true);
    PERFORM set_config('signal.site_id',route.site_id::text,true);
    -- Serialize site-wide cap reservation across both Slack destination types.
    PERFORM pg_advisory_xact_lock(hashtextextended('chat-report:'||route.site_id::text,0));
    SELECT * INTO o FROM app.chat_report_outbox WHERE id=p_id FOR UPDATE;
    IF o.state='dispatching' AND o.ready_at<=transaction_timestamp() THEN
        INSERT INTO app.chat_report_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,outcome,message_sha256)
            SELECT o.tenant_id,o.site_id,o.id,o.attempt_count,'completion','unknown',message_sha256
            FROM app.chat_report_receipts WHERE outbox_id=o.id AND phase='dispatch' AND attempt_number=o.attempt_count;
        UPDATE app.chat_report_outbox SET state='unknown' WHERE id=o.id; o.state:='unknown';
    END IF;
    IF o.state NOT IN ('queued','retry') THEN RETURN jsonb_build_object('state',o.state); END IF;
    reason:=control.chat_report_suppression(p_id,p_generation);
    IF reason IS NULL AND p_message IS NOT NULL THEN
        IF octet_length(p_message)<>32 THEN RETURN NULL; END IF;
        IF o.ready_at>transaction_timestamp() THEN RETURN jsonb_build_object('state',o.state); END IF;
        SELECT * INTO cfg FROM control.chat_report_configuration WHERE provider=CASE WHEN o.channel='telegram' THEN 'telegram' ELSE 'slack' END;
        SELECT count(*) INTO v_count FROM app.chat_report_receipts r JOIN app.chat_report_outbox x ON x.id=r.outbox_id
            WHERE r.tenant_id=o.tenant_id AND r.site_id=o.site_id AND r.phase='dispatch'
            AND (r.created_at AT TIME ZONE 'UTC')::date=(transaction_timestamp() AT TIME ZONE 'UTC')::date
            AND (x.channel='telegram')=(o.channel='telegram');
        IF v_count>=cfg.daily_cap THEN reason:='cap_reached'; END IF;
    END IF;
    IF reason IS NOT NULL THEN
        INSERT INTO app.chat_report_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,outcome)
            VALUES(o.tenant_id,o.site_id,o.id,o.attempt_count,'suppression',reason);
        UPDATE app.chat_report_outbox SET state='suppressed' WHERE id=o.id;
        RETURN jsonb_build_object('state','suppressed');
    END IF;
    SELECT * INTO cfg FROM control.chat_report_configuration WHERE provider=CASE WHEN o.channel='telegram' THEN 'telegram' ELSE 'slack' END;
    SELECT * INTO d FROM control.chat_report_destination(o.tenant_id,o.site_id,o.user_id,o.channel,p_generation);
    IF p_message IS NOT NULL THEN
        INSERT INTO app.chat_report_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,outcome,message_sha256)
            VALUES(o.tenant_id,o.site_id,o.id,o.attempt_count+1,'dispatch','dispatching',p_message);
        UPDATE app.chat_report_outbox SET state='dispatching',attempt_count=attempt_count+1,
            ready_at=transaction_timestamp()+interval '30 seconds' WHERE id=o.id;
    END IF;
    RETURN jsonb_build_object('state',CASE WHEN p_message IS NULL THEN o.state ELSE 'claimed' END,
        'channel',o.channel,'binding_id',o.binding_id,'destination',o.destination,'site_id',o.site_id,
        'secret_reference',d.secret_reference,'category',o.category,'projection',o.projection,'origin',cfg.dashboard_origin);
END $$;
REVOKE ALL ON FUNCTION control.chat_report_item(uuid,text,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.chat_report_item(uuid,text,bytea) TO signal_identity;

CREATE FUNCTION control.finish_chat_report(p_id uuid,p_message bytea,p_outcome text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE route record;o app.chat_report_outbox%%ROWTYPE;v_state text;v_outcome text;
BEGIN
    IF session_user<>'signal_identity' OR p_outcome NOT IN ('accepted','unknown','deferred','provider_unavailable','render_rejected')
        OR p_outcome IS NULL THEN RETURN 'denied'; END IF;
    SELECT * INTO route FROM control.chat_report_routes WHERE id=p_id;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',route.tenant_id::text,true);
    PERFORM set_config('signal.site_id',route.site_id::text,true);
    SELECT * INTO o FROM app.chat_report_outbox WHERE id=p_id FOR UPDATE;
    IF o.state IN ('queued','retry') AND p_outcome IN ('provider_unavailable','render_rejected') THEN
        INSERT INTO app.chat_report_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,outcome)
            VALUES(o.tenant_id,o.site_id,o.id,o.attempt_count,'suppression',p_outcome);
        UPDATE app.chat_report_outbox SET state='suppressed' WHERE id=o.id; RETURN 'suppressed';
    END IF;
    IF p_outcome IN ('provider_unavailable','render_rejected') THEN RETURN o.state; END IF;
    IF o.state<>'dispatching' THEN RETURN o.state; END IF;
    IF NOT EXISTS(SELECT 1 FROM app.chat_report_receipts WHERE outbox_id=o.id AND attempt_number=o.attempt_count
        AND phase='dispatch' AND message_sha256=p_message) THEN RETURN 'denied'; END IF;
    v_outcome:=CASE WHEN p_outcome='deferred' AND o.attempt_count=3 THEN 'retry_exhausted' ELSE p_outcome END;
    v_state:=CASE WHEN v_outcome='deferred' THEN 'retry' WHEN v_outcome='retry_exhausted' THEN 'failed' ELSE v_outcome END;
    INSERT INTO app.chat_report_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,outcome,message_sha256)
        VALUES(o.tenant_id,o.site_id,o.id,o.attempt_count,'completion',v_outcome,p_message);
    UPDATE app.chat_report_outbox SET state=v_state,ready_at=transaction_timestamp()+interval '5 minutes' WHERE id=o.id;
    RETURN v_state;
END $$;
REVOKE ALL ON FUNCTION control.finish_chat_report(uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.finish_chat_report(uuid,bytea,text) TO signal_identity;

CREATE FUNCTION control.due_chat_reports(p_limit integer) RETURNS SETOF uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;
BEGIN
    IF session_user<>'signal_identity' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RETURN; END IF;
    -- Route-only iteration; per-item functions establish the tenant before reading data.
    FOR r IN SELECT * FROM control.chat_report_routes WHERE current_state IN ('queued','retry','dispatching')
        AND ready_at<=transaction_timestamp() ORDER BY ready_at,id LIMIT p_limit LOOP
        RETURN NEXT r.id;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION control.due_chat_reports(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.due_chat_reports(integer) TO signal_identity;

CREATE FUNCTION control.read_chat_reports(p_session bytea,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;v_channels jsonb:='[]'::jsonb;v_channel text;p app.chat_report_preferences%%ROWTYPE;d record;v_available boolean;
BEGIN
    IF session_user<>'signal_api' THEN RETURN NULL; END IF;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key<>'owner' THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
    FOREACH v_channel IN ARRAY ARRAY['slack_channel','slack_dm','telegram'] LOOP
        SELECT * INTO p FROM app.chat_report_preferences WHERE tenant_id=a.tenant_id AND site_id=p_site
            AND user_id=a.user_id AND channel=v_channel ORDER BY preference_order DESC LIMIT 1;
        SELECT * INTO d FROM control.chat_report_destination(a.tenant_id,p_site,a.user_id,v_channel,p_generation);
        v_available:=d.binding_id IS NOT NULL AND EXISTS(SELECT 1 FROM control.chat_report_configuration
            WHERE provider=CASE WHEN v_channel='telegram' THEN 'telegram' ELSE 'slack' END);
        v_channels:=v_channels||jsonb_build_array(jsonb_build_object('channel',v_channel,
            'binding_id',d.binding_id,
            'availability',CASE WHEN v_available THEN 'available' ELSE 'unavailable' END,
            'enabled',COALESCE(p.enabled AND p.binding_id=d.binding_id AND p.link_id IS NOT DISTINCT FROM d.link_id
                AND p.recovery_generation=p_generation AND p.membership_epoch=a.membership_epoch
                AND p.site_epoch=a.site_authorization_epoch AND p.membership_change_count=(SELECT count(*)
                    FROM control.email_membership_changes c JOIN app.memberships m ON m.tenant_id=c.tenant_id AND m.id=c.membership_id
                    WHERE m.tenant_id=a.tenant_id AND m.user_id=a.user_id),false)));
    END LOOP;
    RETURN jsonb_build_object('channels',v_channels,'history',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (
        SELECT o.id,o.channel,o.category,o.state,o.attempt_count,o.created_at,
            (SELECT r.outcome FROM app.chat_report_receipts r WHERE r.outbox_id=o.id
                ORDER BY r.created_at DESC,r.attempt_number DESC,r.phase DESC LIMIT 1) AS outcome
        FROM app.chat_report_outbox o WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site AND o.user_id=a.user_id
        ORDER BY o.created_at DESC,o.id DESC LIMIT 20) x),'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION control.read_chat_reports(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_chat_reports(bytea,uuid,text) TO signal_api;

CREATE FUNCTION control.queue_weekly_chat_report() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF OLD.status='running' AND NEW.status IN ('completed','stopped','failed') THEN
        PERFORM control.queue_chat_report_event(NEW.tenant_id,NEW.site_id,NEW.id,'weekly_report',
            control.weekly_report_projection(NEW.tenant_id,NEW.site_id,NEW.week_start)||jsonb_build_object(
                'measurements',control.change_measurement_projection(NEW.tenant_id,NEW.site_id,NEW.week_start)));
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.queue_weekly_chat_report() FROM PUBLIC;
CREATE TRIGGER weekly_chat_report AFTER UPDATE ON app.weekly_cycles
    FOR EACH ROW EXECUTE FUNCTION control.queue_weekly_chat_report();

CREATE FUNCTION control.queue_chat_report_alert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE category text;event_id uuid;
BEGIN
    IF TG_TABLE_NAME='site_weekly_control' THEN
        IF NOT NEW.paused OR (TG_OP='UPDATE' AND OLD.paused) THEN RETURN NEW; END IF;
        category:='pause';event_id:=gen_random_uuid();
    ELSIF TG_TABLE_NAME='standing_authorization_revocations' THEN category:='revocation';event_id:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('slack_revocations','telegram_revocations') THEN
        category:='revocation';event_id:=NEW.event_id;
    ELSIF TG_TABLE_NAME IN ('slack_outbox','telegram_outbox') THEN
        IF NEW.state<>'unknown' OR OLD.state='unknown' THEN RETURN NEW;END IF;
        category:='failed_delivery';event_id:=NEW.id;
    ELSIF TG_TABLE_NAME='telegram_bindings' THEN
        IF NEW.state<>'failed' OR OLD.state='failed' THEN RETURN NEW;END IF;
        category:='stale_binding';event_id:=NEW.id;
    ELSIF TG_TABLE_NAME='email_send_receipts' THEN
        IF NEW.provider_response_class IN ('unknown','permanent','bounced')
            OR (NEW.provider_response_class='transient' AND NEW.attempt_number=3) THEN category:='failed_delivery';
        ELSIF NEW.provider_response_class='stale_binding' THEN category:='stale_binding';ELSE RETURN NEW;END IF;
        IF EXISTS(SELECT 1 FROM app.email_outbox o WHERE o.tenant_id=NEW.tenant_id AND o.site_id=NEW.site_id
            AND o.id=NEW.outbox_id AND o.category<>'weekly_report') THEN RETURN NEW;END IF;
        event_id:=NEW.id;
    ELSIF TG_TABLE_NAME='github_pr_operation_events' THEN
        IF NEW.event_kind NOT IN ('blocked','outcome_unknown') THEN RETURN NEW; END IF;
        category:='failed_delivery';event_id:=NEW.id;
    ELSIF TG_TABLE_NAME='github_read_binding_events' THEN
        IF NEW.event_kind='revoked' THEN category:='revocation';
        ELSIF NEW.event_kind='failed' THEN category:='stale_binding';ELSE RETURN NEW;END IF;event_id:=NEW.id;
    ELSIF TG_TABLE_NAME='github_delivery_receipts' THEN
        IF convert_from(NEW.canonical_receipt,'UTF8')::jsonb->>'outcome'<>'regressed' THEN RETURN NEW;END IF;
        category:='failed_delivery';event_id:=NEW.attempt_id;
    ELSIF TG_TABLE_NAME='chat_report_receipts' THEN
        IF NEW.outcome IN ('unknown','retry_exhausted') THEN category:='failed_delivery';
        ELSIF NEW.outcome IN ('stale_binding','destination_unavailable') THEN category:='stale_binding';
        ELSE RETURN NEW;END IF;
        IF EXISTS(SELECT 1 FROM app.chat_report_outbox o WHERE o.id=NEW.outbox_id AND o.category<>'weekly_report') THEN RETURN NEW;END IF;
        event_id:=NEW.id;
    END IF;
    PERFORM control.queue_chat_report_event(NEW.tenant_id,NEW.site_id,event_id,category,'{}'::jsonb);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.queue_chat_report_alert() FROM PUBLIC;
CREATE TRIGGER chat_pause_alert AFTER INSERT OR UPDATE ON app.site_weekly_control FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_revocation_alert AFTER INSERT ON app.standing_authorization_revocations FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_pr_failure_alert AFTER INSERT ON app.github_pr_operation_events FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_binding_alert AFTER INSERT ON app.github_read_binding_events FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_live_alert AFTER INSERT ON app.github_delivery_receipts FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_delivery_alert AFTER INSERT ON app.chat_report_receipts FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_slack_revocation_alert AFTER INSERT ON control.slack_revocations FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_telegram_revocation_alert AFTER INSERT ON control.telegram_revocations FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_slack_failure_alert AFTER UPDATE ON app.slack_outbox FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_telegram_failure_alert AFTER UPDATE ON app.telegram_outbox FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_telegram_stale_alert AFTER UPDATE ON app.telegram_bindings FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
CREATE TRIGGER chat_email_failure_alert AFTER INSERT ON app.email_send_receipts FOR EACH ROW EXECUTE FUNCTION control.queue_chat_report_alert();
