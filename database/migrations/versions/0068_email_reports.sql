CREATE TABLE control.email_identity_claims (
    session_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES control.users(id),
    address text, address_sha256 bytea, claim_epoch bigint NOT NULL,
    issued_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CHECK ((address IS NULL AND address_sha256 IS NULL) OR
        (address=lower(address) AND address ~ '^[a-z0-9.!#$%%&''*+/=?^_`{|}~-]+@[a-z0-9.-]+$'
         AND length(address) BETWEEN 3 AND 320 AND octet_length(address_sha256)=32))
);
CREATE TABLE control.email_claim_heads (
    user_id uuid PRIMARY KEY REFERENCES control.users(id),
    address_sha256 bytea, claim_epoch bigint NOT NULL CHECK(claim_epoch>0),
    issued_at timestamptz NOT NULL
);
CREATE FUNCTION control.record_email_identity_claim(
    p_session uuid,p_issuer text,p_subject text,p_address text,p_issued timestamptz
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s control.identity_sessions%%ROWTYPE; h control.email_claim_heads%%ROWTYPE;
    v_digest bytea; v_epoch bigint;
BEGIN
    IF session_user<>'signal_identity' THEN RAISE EXCEPTION 'email_claim_denied'; END IF;
    SELECT * INTO s FROM control.identity_sessions WHERE id=p_session
        AND token_hash=control.current_identity_session_hash() AND revoked_at IS NULL;
    IF NOT FOUND OR p_issued IS NULL OR p_issued>transaction_timestamp()+interval '30 seconds'
        OR p_issued<transaction_timestamp()-interval '11 minutes' THEN
        RAISE EXCEPTION 'email_claim_denied'; END IF;
    PERFORM 1 FROM control.users WHERE id=s.user_id AND oidc_issuer=p_issuer
        AND oidc_subject=p_subject AND disabled_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'email_claim_denied'; END IF;
    v_digest:=CASE WHEN p_address IS NULL THEN NULL
        ELSE sha256(convert_to(p_address,'UTF8')) END;
    SELECT * INTO h FROM control.email_claim_heads WHERE user_id=s.user_id FOR UPDATE;
    IF FOUND AND p_issued<h.issued_at THEN RAISE EXCEPTION 'stale_email_claim'; END IF;
    v_epoch:=COALESCE(h.claim_epoch,0)+CASE WHEN h.user_id IS NULL
        OR h.address_sha256 IS DISTINCT FROM v_digest THEN 1 ELSE 0 END;
    INSERT INTO control.email_claim_heads VALUES(s.user_id,v_digest,v_epoch,p_issued)
        ON CONFLICT(user_id) DO UPDATE SET address_sha256=EXCLUDED.address_sha256,
            claim_epoch=EXCLUDED.claim_epoch,issued_at=EXCLUDED.issued_at;
    INSERT INTO control.email_identity_claims(session_id,user_id,address,address_sha256,
        claim_epoch,issued_at) VALUES(s.id,s.user_id,p_address,v_digest,v_epoch,p_issued);
END $$;
REVOKE ALL ON FUNCTION control.record_email_identity_claim(uuid,text,text,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_email_identity_claim(uuid,text,text,text,timestamptz)
    TO signal_identity;

CREATE TABLE control.email_smtp_configuration (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    host text NOT NULL CHECK(host ~ '^[a-z0-9][a-z0-9.-]{0,252}$'),
    port integer NOT NULL CHECK(port BETWEEN 1 AND 65535),
    tls_mode text NOT NULL CHECK(tls_mode IN ('starttls','implicit')),
    sender text NOT NULL, dashboard_origin text NOT NULL,
    daily_cap integer NOT NULL CHECK(daily_cap BETWEEN 1 AND 100),
    configuration_sha256 bytea NOT NULL CHECK(octet_length(configuration_sha256)=32),
    secret_reference text NOT NULL CHECK(secret_reference='secret://email/smtp/default')
);

CREATE FUNCTION control.valid_smtp_bucket(p_origin text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS(SELECT 1 FROM control.email_smtp_configuration c
        WHERE p_origin='smtp+tls://'||c.host||':'||c.port::text)
$$;
REVOKE ALL ON FUNCTION control.valid_smtp_bucket(text) FROM PUBLIC;
ALTER TABLE control.origin_buckets DROP CONSTRAINT origin_buckets_origin_check;
ALTER TABLE control.origin_buckets ADD CONSTRAINT origin_buckets_origin_check CHECK(
    control.valid_crawl_url_identity(origin||'/',origin||'/',origin||'/',origin,1)
    OR control.valid_smtp_bucket(origin));

CREATE TABLE control.email_membership_changes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL,membership_id uuid NOT NULL
);
REVOKE ALL ON control.email_membership_changes FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,
    signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest;
CREATE FUNCTION control.invalidate_member_email() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW IS DISTINCT FROM OLD THEN
        INSERT INTO control.email_membership_changes(tenant_id,membership_id) VALUES(NEW.tenant_id,NEW.id);
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.invalidate_member_email() FROM PUBLIC;
CREATE TRIGGER email_membership_changed AFTER UPDATE ON app.memberships
    FOR EACH ROW EXECUTE FUNCTION control.invalidate_member_email();

CREATE TABLE app.email_preferences (
    tenant_id uuid NOT NULL, id uuid NOT NULL, membership_id uuid NOT NULL,
    user_id uuid NOT NULL, enabled boolean NOT NULL,
    address_sha256 bytea, identity_session_id uuid REFERENCES control.email_identity_claims(session_id),
    membership_epoch bigint NOT NULL, claim_epoch bigint, recovery_generation text NOT NULL,
    preference_order bigint GENERATED ALWAYS AS IDENTITY,
    membership_change_count bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,membership_id,id),
    FOREIGN KEY(tenant_id,membership_id) REFERENCES app.memberships(tenant_id,id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id),
    CHECK (NOT enabled OR (octet_length(address_sha256)=32 AND identity_session_id IS NOT NULL
        AND claim_epoch IS NOT NULL))
);
CREATE INDEX email_preference_latest ON app.email_preferences(tenant_id,membership_id,preference_order DESC);
CREATE TABLE app.email_outbox (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,
    membership_id uuid NOT NULL, preference_id uuid NOT NULL,
    event_id uuid NOT NULL,category text NOT NULL CHECK(category IN
        ('weekly_report','pause','revocation','failed_delivery','stale_binding')),
    projection jsonb NOT NULL CHECK(octet_length(projection::text)<=131072),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN
        ('queued','retry','dispatching','accepted','failed','unknown','suppressed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
    next_attempt_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    configuration_sha256 bytea NOT NULL CHECK(octet_length(configuration_sha256)=32),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id), UNIQUE(tenant_id,site_id,event_id,membership_id,category),
    FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY(tenant_id,membership_id,preference_id)
        REFERENCES app.email_preferences(tenant_id,membership_id,id)
);
CREATE TABLE control.email_outbox_routes (
    id uuid PRIMARY KEY,tenant_id uuid NOT NULL,site_id uuid NOT NULL,
    current_state text NOT NULL DEFAULT 'queued',
    ready_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY(tenant_id,site_id,id) REFERENCES app.email_outbox(tenant_id,site_id,id)
);
CREATE TABLE app.email_send_receipts (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),
    outbox_id uuid NOT NULL,attempt_number integer NOT NULL CHECK(attempt_number BETWEEN 0 AND 3),
    phase text NOT NULL CHECK(phase IN ('dispatch','completion','suppression')),
    recipient_sha256 bytea NOT NULL CHECK(octet_length(recipient_sha256)=32),
    message_sha256 bytea CHECK(message_sha256 IS NULL OR octet_length(message_sha256)=32),
    provider_response_class text NOT NULL CHECK(provider_response_class IN
        ('dispatching','accepted','transient','permanent','bounced','unknown',
         'recipient_unavailable','cap_reached','provider_unavailable','stale_binding')),
    egress_profile text NOT NULL DEFAULT 'smtp_submit' CHECK(egress_profile='smtp_submit'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id), UNIQUE(tenant_id,site_id,outbox_id,attempt_number,phase),
    FOREIGN KEY(tenant_id,site_id,outbox_id) REFERENCES app.email_outbox(tenant_id,site_id,id)
);
CREATE INDEX email_receipts_cap ON app.email_send_receipts(tenant_id,site_id,created_at)
    WHERE phase='dispatch';

DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['email_preferences','email_outbox','email_send_receipts'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY email_scope ON app.%%I USING '
            '(tenant_id=app.current_tenant_id()) WITH CHECK (tenant_id=app.current_tenant_id())',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,'
            'signal_scheduler,signal_workflow,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END $$;
REVOKE ALL ON control.email_identity_claims,control.email_claim_heads,
    control.email_smtp_configuration,control.email_outbox_routes FROM PUBLIC,
    signal_api,signal_identity,signal_scheduler,signal_workflow,signal_bootstrap,
    signal_crawl_admission,signal_crawl_ingest;
CREATE TRIGGER email_claim_immutable BEFORE UPDATE OR DELETE ON control.email_identity_claims
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER email_membership_history_immutable BEFORE UPDATE OR DELETE ON control.email_membership_changes
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER email_preference_immutable BEFORE UPDATE OR DELETE ON app.email_preferences
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER email_receipt_immutable BEFORE UPDATE OR DELETE ON app.email_send_receipts
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.set_email_preference(
    p_session bytea,p_site uuid,p_generation text,p_enabled boolean,p_address text,p_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; m app.memberships%%ROWTYPE; c control.email_identity_claims%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR p_enabled IS NULL THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO m FROM app.memberships WHERE tenant_id=a.tenant_id AND user_id=a.user_id FOR UPDATE;
    SELECT c0.* INTO c FROM app.sessions s JOIN control.email_identity_claims c0
        ON c0.session_id=s.identity_session_id AND c0.user_id=s.user_id
        JOIN control.email_claim_heads h ON h.user_id=c0.user_id AND h.claim_epoch=c0.claim_epoch
        AND h.address_sha256=c0.address_sha256
        WHERE s.session_token_hash=p_session AND s.tenant_id=a.tenant_id
        AND s.user_id=a.user_id AND c0.issued_at>=transaction_timestamp()-interval '10 minutes'
        AND s.auth_time>=transaction_timestamp()-interval '10 minutes';
    IF p_enabled AND (c.address IS NULL OR c.address IS DISTINCT FROM p_address) THEN
        RETURN 'identity_unverified'; END IF;
    INSERT INTO app.email_preferences(tenant_id,id,membership_id,user_id,enabled,address_sha256,
        identity_session_id,membership_epoch,claim_epoch,recovery_generation,membership_change_count)
    VALUES(a.tenant_id,p_id,m.id,a.user_id,p_enabled,CASE WHEN p_enabled THEN c.address_sha256 END,
        CASE WHEN p_enabled THEN c.session_id END,m.authorization_epoch,c.claim_epoch,p_generation,
        (SELECT count(*) FROM control.email_membership_changes WHERE tenant_id=a.tenant_id AND membership_id=m.id));
    RETURN CASE WHEN p_enabled THEN 'enabled' ELSE 'disabled' END;
END $$;

CREATE FUNCTION control.read_email_preference(p_session bytea,p_site uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; m app.memberships%%ROWTYPE; v app.email_preferences%%ROWTYPE; c record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
    SELECT * INTO m FROM app.memberships WHERE tenant_id=a.tenant_id AND user_id=a.user_id;
    SELECT * INTO v FROM app.email_preferences WHERE tenant_id=a.tenant_id AND membership_id=m.id
        ORDER BY preference_order DESC LIMIT 1;
    SELECT claim.*,s.auth_time AS session_auth_time INTO c FROM app.sessions s JOIN control.email_identity_claims claim
        ON claim.session_id=s.identity_session_id JOIN control.email_claim_heads h
        ON h.user_id=claim.user_id AND h.claim_epoch=claim.claim_epoch
        AND h.address_sha256=claim.address_sha256 WHERE s.session_token_hash=p_session;
    RETURN jsonb_build_object('availability',CASE WHEN NOT EXISTS
        (SELECT 1 FROM control.email_smtp_configuration) THEN 'unavailable'
        WHEN c.address IS NULL THEN 'identity_unverified'
        WHEN c.issued_at<transaction_timestamp()-interval '10 minutes'
            OR c.session_auth_time<transaction_timestamp()-interval '10 minutes' THEN 'stale_session'
        ELSE 'available' END,
        'address',c.address,'can_enable',COALESCE(c.issued_at>=transaction_timestamp()-interval '10 minutes'
            AND c.session_auth_time>=transaction_timestamp()-interval '10 minutes',false),
        'enabled',COALESCE(v.enabled AND v.membership_epoch=m.authorization_epoch
          AND v.claim_epoch=c.claim_epoch AND v.recovery_generation=p_generation
          AND v.membership_change_count=(SELECT count(*) FROM control.email_membership_changes
              WHERE tenant_id=a.tenant_id AND membership_id=m.id),false),
        'last_delivery_state',(SELECT state FROM app.email_outbox o WHERE o.tenant_id=a.tenant_id
            AND o.site_id=p_site AND o.membership_id=m.id
            ORDER BY CASE WHEN o.state IN ('unknown','dispatching') THEN 0 ELSE 1 END,
                o.created_at DESC,o.id DESC LIMIT 1));
END $$;

CREATE FUNCTION control.enqueue_email_report(
    p_session bytea,p_site uuid,p_generation text,p_week date,p_membership uuid,p_id uuid
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE report jsonb; v_tenant uuid; pref app.email_preferences%%ROWTYPE; v_id uuid;
    config control.email_smtp_configuration%%ROWTYPE;
BEGIN
    report:=control.read_weekly_delivery_report(p_session,p_site,p_generation,p_week);
    IF report IS NULL OR report->>'status'='running' THEN RETURN NULL; END IF;
    v_tenant:=current_setting('signal.tenant_id')::uuid;
    SELECT * INTO config FROM control.email_smtp_configuration;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT * INTO pref FROM app.email_preferences WHERE tenant_id=v_tenant AND membership_id=p_membership
        ORDER BY preference_order DESC LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    INSERT INTO app.email_outbox(tenant_id,site_id,id,membership_id,preference_id,event_id,category,
        projection,configuration_sha256)
    VALUES(v_tenant,p_site,p_id,p_membership,pref.id,(report->>'cycle_id')::uuid,'weekly_report',
        report,config.configuration_sha256)
    ON CONFLICT(tenant_id,site_id,event_id,membership_id,category) DO NOTHING;
    SELECT id INTO v_id FROM app.email_outbox WHERE tenant_id=v_tenant AND site_id=p_site
        AND event_id=(report->>'cycle_id')::uuid AND membership_id=p_membership AND category='weekly_report';
    INSERT INTO control.email_outbox_routes(id,tenant_id,site_id)
        VALUES(v_id,v_tenant,p_site) ON CONFLICT DO NOTHING;
    RETURN v_id;
END $$;

CREATE FUNCTION control.email_outbox_item(p_id uuid,p_generation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; o app.email_outbox%%ROWTYPE; p app.email_preferences%%ROWTYPE;
    c control.email_identity_claims%%ROWTYPE; cfg control.email_smtp_configuration%%ROWTYPE;
BEGIN
    IF p_generation IS NULL OR p_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN RETURN NULL; END IF;
    SELECT * INTO r FROM control.email_outbox_routes WHERE id=p_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
    PERFORM set_config('signal.site_id',r.site_id::text,true);
    SELECT * INTO o FROM app.email_outbox WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id;
    SELECT * INTO p FROM app.email_preferences WHERE tenant_id=r.tenant_id AND id=o.preference_id;
    SELECT * INTO c FROM control.email_identity_claims WHERE session_id=p.identity_session_id;
    SELECT * INTO cfg FROM control.email_smtp_configuration;
    RETURN jsonb_build_object('tenant_id',r.tenant_id,'site_id',r.site_id,'state',o.state,
        'projection',o.projection,'category',o.category,'address',c.address,
        'recipient_sha256',encode(COALESCE(p.address_sha256,sha256(convert_to('unverified','UTF8'))),'hex'),
        'host',cfg.host,'port',cfg.port,'tls_mode',cfg.tls_mode,'sender',cfg.sender,
        'dashboard_origin',cfg.dashboard_origin,'daily_cap',cfg.daily_cap,
        'configuration_sha256',encode(o.configuration_sha256,'hex'));
END $$;

CREATE FUNCTION control.begin_email_dispatch(p_id uuid,p_generation text,p_message bytea,p_size integer)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; o app.email_outbox%%ROWTYPE; p app.email_preferences%%ROWTYPE;
    m app.memberships%%ROWTYPE; cfg control.email_smtp_configuration%%ROWTYPE;
    v_class text; v_count integer; bucket control.origin_buckets%%ROWTYPE;
BEGIN
    IF p_generation IS NULL OR p_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN RETURN 'denied'; END IF;
    SELECT * INTO r FROM control.email_outbox_routes WHERE id=p_id;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
    PERFORM set_config('signal.site_id',r.site_id::text,true);
    PERFORM 1 FROM app.sites WHERE tenant_id=r.tenant_id AND id=r.site_id FOR UPDATE;
    SELECT * INTO o FROM app.email_outbox WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id FOR UPDATE;
    IF o.state NOT IN ('queued','retry') THEN RETURN o.state; END IF;
    IF o.next_attempt_at>transaction_timestamp() THEN RETURN 'deferred'; END IF;
    IF octet_length(p_message) IS DISTINCT FROM 32 OR p_size NOT BETWEEN 1 AND 32768 THEN RETURN 'denied'; END IF;
    SELECT * INTO p FROM app.email_preferences WHERE tenant_id=r.tenant_id AND id=o.preference_id;
    SELECT * INTO m FROM app.memberships WHERE tenant_id=r.tenant_id AND id=o.membership_id FOR UPDATE;
    SELECT * INTO cfg FROM control.email_smtp_configuration;
    IF cfg.host IS NULL THEN v_class:='provider_unavailable';
    ELSIF cfg.configuration_sha256<>o.configuration_sha256 THEN v_class:='stale_binding';
    ELSIF NOT p.enabled OR p.recovery_generation<>p_generation OR m.state<>'active'
        OR m.role_key<>'owner' OR m.authorization_epoch<>p.membership_epoch
        OR p.membership_change_count<>(SELECT count(*) FROM control.email_membership_changes
            WHERE tenant_id=r.tenant_id AND membership_id=m.id)
        OR NOT EXISTS(SELECT 1 FROM control.resolve_member_site_authority(
            r.tenant_id,p.user_id,r.site_id,'primary') a WHERE a.outcome='authorized')
        OR EXISTS(SELECT 1 FROM app.email_preferences newer WHERE newer.tenant_id=p.tenant_id
            AND newer.membership_id=p.membership_id AND newer.preference_order>p.preference_order)
        OR NOT EXISTS(SELECT 1 FROM control.email_claim_heads h WHERE h.user_id=p.user_id
            AND h.claim_epoch=p.claim_epoch AND h.address_sha256=p.address_sha256)
        OR EXISTS(SELECT 1 FROM control.users u WHERE u.id=p.user_id AND u.disabled_at IS NOT NULL)
        OR NOT EXISTS(SELECT 1 FROM app.tenants t WHERE t.tenant_id=r.tenant_id AND t.lifecycle='active')
        OR NOT EXISTS(SELECT 1 FROM app.sites s WHERE s.tenant_id=r.tenant_id AND s.id=r.site_id
            AND s.state='active')
        OR EXISTS(SELECT 1 FROM app.email_send_receipts receipt JOIN app.email_outbox previous
            ON previous.tenant_id=receipt.tenant_id AND previous.site_id=receipt.site_id
            AND previous.id=receipt.outbox_id WHERE receipt.tenant_id=r.tenant_id
            AND previous.membership_id=o.membership_id AND receipt.recipient_sha256=p.address_sha256
            AND receipt.provider_response_class='bounced') THEN v_class:='recipient_unavailable';
    END IF;
    IF v_class IS NULL THEN
        SELECT count(*) INTO v_count FROM app.email_send_receipts WHERE tenant_id=r.tenant_id
            AND site_id=r.site_id AND phase='dispatch'
            AND created_at>=date_trunc('day',transaction_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
        IF v_count>=cfg.daily_cap THEN v_class:='cap_reached'; END IF;
    END IF;
    IF v_class IS NOT NULL THEN
        INSERT INTO app.email_send_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,
            recipient_sha256,message_sha256,provider_response_class)
        VALUES(r.tenant_id,r.site_id,p_id,o.attempt_count,'suppression',
            COALESCE(p.address_sha256,sha256(convert_to('unverified','UTF8'))),p_message,v_class);
        UPDATE app.email_outbox SET state='suppressed' WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id;
        RETURN 'suppressed';
    END IF;
    INSERT INTO control.origin_buckets(origin,profile_version)
        VALUES('smtp+tls://'||cfg.host||':'||cfg.port::text,1) ON CONFLICT DO NOTHING;
    SELECT * INTO bucket FROM control.origin_buckets
        WHERE origin='smtp+tls://'||cfg.host||':'||cfg.port::text AND profile_version=1 FOR UPDATE;
    IF bucket.in_flight_count>0 OR bucket.next_allowed_at>clock_timestamp()
        OR bucket.degraded_until>clock_timestamp() THEN RETURN 'deferred'; END IF;
    UPDATE control.origin_buckets SET next_allowed_at=clock_timestamp()+interval '30 seconds',
        updated_at=clock_timestamp() WHERE id=bucket.id;
    INSERT INTO app.email_send_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,
        recipient_sha256,message_sha256,provider_response_class)
    VALUES(r.tenant_id,r.site_id,p_id,o.attempt_count+1,'dispatch',p.address_sha256,p_message,'dispatching');
    UPDATE app.email_outbox SET state='dispatching',attempt_count=attempt_count+1
        WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id;
    RETURN 'claimed';
END $$;

CREATE FUNCTION control.finish_email_dispatch(p_id uuid,p_message bytea,p_class text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; o app.email_outbox%%ROWTYPE; receipt app.email_send_receipts%%ROWTYPE; v_state text;
BEGIN
    IF p_class NOT IN ('accepted','transient','permanent','bounced','unknown') THEN RETURN 'denied'; END IF;
    SELECT * INTO r FROM control.email_outbox_routes WHERE id=p_id;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
    PERFORM set_config('signal.site_id',r.site_id::text,true);
    SELECT * INTO o FROM app.email_outbox WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id FOR UPDATE;
    SELECT * INTO receipt FROM app.email_send_receipts WHERE tenant_id=r.tenant_id AND site_id=r.site_id
        AND outbox_id=p_id AND attempt_number=o.attempt_count AND phase='dispatch';
    IF receipt.message_sha256 IS DISTINCT FROM p_message THEN RETURN 'denied'; END IF;
    IF o.state<>'dispatching' THEN RETURN o.state; END IF;
    v_state:=CASE WHEN p_class='accepted' THEN 'accepted' WHEN p_class='unknown' THEN 'unknown'
        WHEN p_class='transient' AND o.attempt_count<3 THEN 'retry' ELSE 'failed' END;
    INSERT INTO app.email_send_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,
        recipient_sha256,message_sha256,provider_response_class)
    VALUES(r.tenant_id,r.site_id,p_id,o.attempt_count,'completion',receipt.recipient_sha256,p_message,p_class);
    UPDATE app.email_outbox SET state=v_state,next_attempt_at=transaction_timestamp()+interval '5 minutes'
        WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id;
    RETURN v_state;
END $$;

REVOKE ALL ON FUNCTION control.set_email_preference(bytea,uuid,text,boolean,text,uuid),
    control.read_email_preference(bytea,uuid,text),control.enqueue_email_report(bytea,uuid,text,date,uuid,uuid),
    control.email_outbox_item(uuid,text),control.begin_email_dispatch(uuid,text,bytea,integer),
    control.finish_email_dispatch(uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.set_email_preference(bytea,uuid,text,boolean,text,uuid),
    control.read_email_preference(bytea,uuid,text),control.enqueue_email_report(bytea,uuid,text,date,uuid,uuid)
    TO signal_api;
GRANT EXECUTE ON FUNCTION control.email_outbox_item(uuid,text),
    control.begin_email_dispatch(uuid,text,bytea,integer),control.finish_email_dispatch(uuid,bytea,text)
    TO signal_identity;

CREATE FUNCTION control.suppress_email_outbox(p_id uuid,p_class text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; o app.email_outbox%%ROWTYPE; v_digest bytea;
BEGIN
    IF p_class NOT IN ('recipient_unavailable','provider_unavailable','stale_binding') THEN RETURN 'denied'; END IF;
    SELECT * INTO r FROM control.email_outbox_routes WHERE id=p_id;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
    PERFORM set_config('signal.site_id',r.site_id::text,true);
    SELECT * INTO o FROM app.email_outbox WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id FOR UPDATE;
    IF o.state NOT IN ('queued','retry') THEN RETURN o.state; END IF;
    SELECT address_sha256 INTO v_digest FROM app.email_preferences WHERE tenant_id=r.tenant_id AND id=o.preference_id;
    INSERT INTO app.email_send_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,
        recipient_sha256,provider_response_class)
    VALUES(r.tenant_id,r.site_id,p_id,o.attempt_count,'suppression',
        COALESCE(v_digest,sha256(convert_to('unverified','UTF8'))),p_class);
    UPDATE app.email_outbox SET state='suppressed' WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=p_id;
    RETURN 'suppressed';
END $$;
REVOKE ALL ON FUNCTION control.suppress_email_outbox(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.suppress_email_outbox(uuid,text) TO signal_identity;

CREATE FUNCTION control.configure_email_smtp(p_host text,p_port integer,p_tls text,p_sender text,
    p_dashboard text,p_cap integer,p_digest bytea)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_bootstrap' OR p_dashboard !~ '^https://[a-z0-9.-]+(:[0-9]+)?$'
        OR p_sender !~ '^[a-z0-9.!#$%%&''*+/=?^_`{|}~-]+@[a-z0-9.-]+$' THEN
        RAISE EXCEPTION 'email_configuration_denied'; END IF;
    INSERT INTO control.email_smtp_configuration VALUES(true,p_host,p_port,p_tls,p_sender,
        p_dashboard,p_cap,p_digest,'secret://email/smtp/default') ON CONFLICT(singleton)
    DO UPDATE SET host=EXCLUDED.host,port=EXCLUDED.port,tls_mode=EXCLUDED.tls_mode,
        sender=EXCLUDED.sender,dashboard_origin=EXCLUDED.dashboard_origin,
        daily_cap=EXCLUDED.daily_cap,configuration_sha256=EXCLUDED.configuration_sha256;
END $$;
REVOKE ALL ON FUNCTION control.configure_email_smtp(text,integer,text,text,text,integer,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.configure_email_smtp(text,integer,text,text,text,integer,bytea) TO signal_bootstrap;

CREATE FUNCTION app.guard_email_outbox_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='DELETE' OR
        (to_jsonb(NEW)-ARRAY['state','attempt_count','next_attempt_at']) IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['state','attempt_count','next_attempt_at']) THEN
        RAISE EXCEPTION 'immutable_email_intent'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app.guard_email_outbox_mutation() FROM PUBLIC;
CREATE TRIGGER email_outbox_guard BEFORE UPDATE OR DELETE ON app.email_outbox
    FOR EACH ROW EXECUTE FUNCTION app.guard_email_outbox_mutation();

CREATE FUNCTION control.route_email_state() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    UPDATE control.email_outbox_routes SET current_state=NEW.state,
        ready_at=CASE WHEN NEW.state='dispatching' THEN transaction_timestamp()+interval '30 seconds'
            ELSE NEW.next_attempt_at END WHERE id=NEW.id;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.route_email_state() FROM PUBLIC;
CREATE TRIGGER email_route_state AFTER UPDATE ON app.email_outbox
    FOR EACH ROW EXECUTE FUNCTION control.route_email_state();
CREATE INDEX email_routes_due ON control.email_outbox_routes(ready_at,id)
    WHERE current_state IN ('queued','retry','dispatching');

CREATE FUNCTION control.email_due_outbox(p_limit integer) RETURNS SETOF uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; o app.email_outbox%%ROWTYPE; receipt app.email_send_receipts%%ROWTYPE;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RETURN; END IF;
    FOR r IN SELECT * FROM control.email_outbox_routes
        WHERE current_state IN ('queued','retry','dispatching') AND ready_at<=transaction_timestamp()
        ORDER BY ready_at,id LIMIT p_limit LOOP
        PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
        PERFORM set_config('signal.site_id',r.site_id::text,true);
        SELECT * INTO o FROM app.email_outbox WHERE tenant_id=r.tenant_id AND site_id=r.site_id AND id=r.id;
        IF o.state='dispatching' THEN
            SELECT * INTO receipt FROM app.email_send_receipts WHERE tenant_id=r.tenant_id
                AND site_id=r.site_id AND outbox_id=r.id AND phase='dispatch' AND attempt_number=o.attempt_count;
            IF receipt.created_at<transaction_timestamp()-interval '30 seconds' THEN
                PERFORM control.finish_email_dispatch(r.id,receipt.message_sha256,'unknown');
            END IF;
        ELSIF o.state IN ('queued','retry') AND o.next_attempt_at<=transaction_timestamp() THEN
            RETURN NEXT r.id;
            p_limit:=p_limit-1;
            IF p_limit=0 THEN RETURN; END IF;
        END IF;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION control.email_due_outbox(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.email_due_outbox(integer) TO signal_identity;
