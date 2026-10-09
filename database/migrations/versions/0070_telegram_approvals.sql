ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check
CHECK (egress_profile IN (
    'legacy_unqualified','crawl_page','crawl_robots','browser_read','github_rest',
    'slack_bot','slack_oauth','telegram_bot','github_repository_write','google_oauth_token',
    'google_oauth_revoke','gsc_api','bing_oauth_token','bing_api','jev','model_json',
    'openai_model','openai_assistant','perplexity_assistant','gemini_assistant',
    'dataforseo','crawl_key_file','indexnow_submit','ga4_admin','ga4_data'
));
ALTER FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text)
RENAME TO bind_before_telegram_egress_profile;
REVOKE ALL ON FUNCTION control.bind_before_telegram_egress_profile(uuid,uuid,uuid,bytea,text)
FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(
    p_tenant_id uuid,p_site_id uuid,p_operation_id uuid,p_request_sha256 bytea,p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE;
BEGIN
    IF p_profile<>'telegram_bot' THEN RETURN control.bind_before_telegram_egress_profile(
        p_tenant_id,p_site_id,p_operation_id,p_request_sha256,p_profile); END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant_id AND site_id=p_site_id
        AND id=p_operation_id FOR UPDATE;
    IF NOT FOUND OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_request_sha256
        OR o.purpose<>'connector' OR o.origin<>'https://api.telegram.org' OR o.method<>'POST'
        OR NOT o.credentialed OR o.request_bytes>16384 OR o.max_response_bytes>16384
        OR o.request_url NOT IN ('https://api.telegram.org/getMe','https://api.telegram.org/setWebhook',
            'https://api.telegram.org/deleteWebhook','https://api.telegram.org/sendMessage',
            'https://api.telegram.org/answerCallbackQuery')
        OR o.egress_profile NOT IN ('legacy_unqualified',p_profile) THEN
        RAISE EXCEPTION 'telegram_profile_denied' USING ERRCODE='22023'; END IF;
    UPDATE app.egress_operations SET egress_profile=p_profile
        WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id;
    RETURN 'bound';
END $$;
REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;

CREATE TABLE app.telegram_bindings (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL UNIQUE,
    owner_user_id uuid NOT NULL,secret_reference text NOT NULL,
    bot_id text CHECK(bot_id ~ '^[1-9][0-9]{0,15}$'),
    bot_username text CHECK(bot_username ~ '^[A-Za-z0-9_]{5,32}$'),
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','active','failed')),
    max_risk integer NOT NULL CHECK(max_risk BETWEEN 0 AND 2),
    recovery_generation text NOT NULL,revoked_at timestamptz,webhook_deleted_at timestamptz,
    webhook_delete_claimed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY(tenant_id,owner_user_id) REFERENCES app.memberships(tenant_id,user_id),
    CHECK(secret_reference='secret://telegram/'||id::text),
    CHECK(state<>'active' OR (bot_id IS NOT NULL AND bot_username IS NOT NULL))
);
CREATE UNIQUE INDEX telegram_one_site_binding ON app.telegram_bindings(tenant_id,site_id) WHERE revoked_at IS NULL;
-- Keep the bot reserved until webhook deletion is confirmed; old revocation cannot delete a new binding's webhook.
CREATE UNIQUE INDEX telegram_one_bot_binding ON app.telegram_bindings(bot_id) WHERE webhook_deleted_at IS NULL;
CREATE TABLE control.telegram_binding_routes (
    id uuid PRIMARY KEY,tenant_id uuid NOT NULL,site_id uuid NOT NULL,
    FOREIGN KEY(tenant_id,site_id,id) REFERENCES app.telegram_bindings(tenant_id,site_id,id)
);
REVOKE ALL ON control.telegram_binding_routes FROM PUBLIC;
CREATE TABLE app.telegram_links (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL UNIQUE,
    binding_id uuid NOT NULL,user_id uuid NOT NULL,
    telegram_user_id text NOT NULL CHECK(telegram_user_id ~ '^[1-9][0-9]{0,15}$'),
    chat_id text NOT NULL CHECK(chat_id=telegram_user_id),
    membership_epoch bigint NOT NULL,recovery_generation text NOT NULL,revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.telegram_bindings(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE UNIQUE INDEX telegram_one_user_link ON app.telegram_links(binding_id,telegram_user_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX telegram_one_membership_link ON app.telegram_links(binding_id,user_id) WHERE revoked_at IS NULL;
CREATE TABLE app.telegram_link_codes (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,
    binding_id uuid NOT NULL,user_id uuid NOT NULL,telegram_user_id text NOT NULL,
    membership_epoch bigint NOT NULL,recovery_generation text NOT NULL,
    code_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(code_sha256)=32),
    expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '5 minutes',consumed_at timestamptz,
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.telegram_bindings(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TABLE app.telegram_outbox (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,binding_id uuid NOT NULL,
    intended_user_id text NOT NULL,chat_id text NOT NULL CHECK(chat_id=intended_user_id),
    kind text NOT NULL CHECK(kind IN ('approval','reply')),
    method text NOT NULL CHECK(method IN ('sendMessage','answerCallbackQuery')),
    revision_id uuid,revision_sha256 bytea,approve_sha256 bytea,reject_sha256 bytea,
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','dispatching','accepted','unknown')),
    expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '1 hour',message_id text,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.telegram_bindings(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,revision_id,revision_sha256)
        REFERENCES app.candidate_recipe_revisions(tenant_id,site_id,id,revision_sha256),
    CHECK(kind='reply' OR (method='sendMessage' AND revision_id IS NOT NULL
        AND octet_length(approve_sha256)=32 AND octet_length(reject_sha256)=32 AND approve_sha256<>reject_sha256)),
    CHECK((state='accepted' AND message_id IS NOT NULL) OR (state<>'accepted' AND message_id IS NULL))
);
CREATE UNIQUE INDEX telegram_request_dedup ON app.telegram_outbox(binding_id,intended_user_id,revision_id) WHERE kind='approval';
CREATE TABLE app.telegram_ingress_audit (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,binding_id uuid NOT NULL,
    body_sha256 bytea NOT NULL CHECK(octet_length(body_sha256)=32),update_id bigint,
    outcome text NOT NULL CHECK(outcome IN ('secret_rejected','payload_rejected','replay','wrong_binding',
        'unlinked','authority_denied','stale_revision','step_up','linked','decided','decision_conflict',
        'ignored','group_ignored')),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.telegram_bindings(tenant_id,site_id,id)
);
CREATE UNIQUE INDEX telegram_update_once ON app.telegram_ingress_audit(binding_id,update_id)
WHERE update_id IS NOT NULL AND outcome<>'replay';
CREATE TRIGGER telegram_ingress_immutable BEFORE UPDATE OR DELETE ON app.telegram_ingress_audit
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['telegram_bindings','telegram_links','telegram_link_codes','telegram_outbox','telegram_ingress_audit'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY telegram_scope ON app.%%I USING '
            '(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK '
            '(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_identity,signal_api,signal_bootstrap,'
            'signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END $$;

CREATE FUNCTION control.telegram_scope(p_binding_id uuid) RETURNS app.telegram_bindings
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO r FROM control.telegram_binding_routes WHERE id=p_binding_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
    PERFORM set_config('signal.site_id',r.site_id::text,true);
    SELECT * INTO b FROM app.telegram_bindings WHERE id=p_binding_id FOR SHARE;
    RETURN b;
END $$;
REVOKE ALL ON FUNCTION control.telegram_scope(uuid) FROM PUBLIC;

CREATE FUNCTION control.prepare_telegram_binding(p_session bytea,p_site uuid,p_generation text,p_id uuid,p_risk integer)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    INSERT INTO app.telegram_bindings(tenant_id,site_id,id,owner_user_id,secret_reference,max_risk,recovery_generation)
    VALUES(a.tenant_id,p_site,p_id,a.user_id,'secret://telegram/'||p_id::text,p_risk,p_generation);
    INSERT INTO control.telegram_binding_routes VALUES(p_id,a.tenant_id,p_site);
    RETURN 'prepared';
END $$;
CREATE FUNCTION control.confirm_telegram_binding(p_session bytea,p_site uuid,p_generation text,p_id uuid,
    p_bot text,p_username text,p_activate boolean)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    UPDATE app.telegram_bindings SET bot_id=p_bot,bot_username=p_username,
        state=CASE WHEN p_activate THEN 'active' ELSE 'pending' END
    WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id AND owner_user_id=a.user_id
        AND recovery_generation=p_generation AND revoked_at IS NULL AND state='pending';
    RETURN CASE WHEN FOUND THEN 'confirmed' ELSE 'denied' END;
END $$;
CREATE FUNCTION control.fail_telegram_binding(p_binding uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    UPDATE app.telegram_bindings SET state='failed' WHERE id=p_binding AND state='pending';
END $$;
CREATE FUNCTION control.telegram_ingress_secret(p_binding uuid)
RETURNS TABLE(secret_reference text,site_id uuid,bot_id text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    RETURN QUERY SELECT b.secret_reference,b.site_id,b.bot_id;
END $$;
CREATE FUNCTION control.finish_telegram_revocation(p_binding uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    UPDATE app.telegram_bindings SET webhook_deleted_at=COALESCE(webhook_deleted_at,transaction_timestamp())
    WHERE id=p_binding AND revoked_at IS NOT NULL;
END $$;
CREATE FUNCTION control.claim_telegram_revocation(p_binding uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    UPDATE app.telegram_bindings SET webhook_delete_claimed=true
    WHERE id=p_binding AND revoked_at IS NOT NULL AND bot_id IS NOT NULL
        AND webhook_deleted_at IS NULL AND NOT webhook_delete_claimed;
    RETURN FOUND;
END $$;

CREATE FUNCTION control.begin_telegram_link(p_session bytea,p_site uuid,p_generation text,p_binding uuid,
    p_id uuid,p_user text,p_hash bytea) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' THEN RETURN 'denied'; END IF;
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    IF b.id IS NULL OR b.tenant_id<>a.tenant_id OR b.site_id<>p_site OR b.revoked_at IS NOT NULL
        OR b.state<>'active' OR b.recovery_generation<>p_generation OR p_user !~ '^[1-9][0-9]{0,15}$'
        OR octet_length(p_hash) IS DISTINCT FROM 32
        OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='telegram_binding' AND target_id=b.id)
        THEN RETURN 'denied'; END IF;
    INSERT INTO app.telegram_link_codes(tenant_id,site_id,id,binding_id,user_id,telegram_user_id,
        membership_epoch,recovery_generation,code_sha256)
    VALUES(a.tenant_id,p_site,p_id,p_binding,a.user_id,p_user,a.membership_epoch,p_generation,p_hash);
    RETURN b.bot_username;
END $$;
CREATE FUNCTION control.read_telegram_binding(p_session bytea,p_site uuid,p_generation text)
RETURNS TABLE(binding_id uuid,bot_username text,max_risk integer,link_id uuid,telegram_user_id text,availability text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,NULL::integer,NULL::uuid,NULL::text,'denied'::text;RETURN; END IF;
    RETURN QUERY SELECT b.id,b.bot_username,b.max_risk,l.id,l.telegram_user_id,
        CASE WHEN b.state='active' THEN 'bound' ELSE 'unavailable' END
    FROM app.telegram_bindings b LEFT JOIN app.telegram_links l ON l.binding_id=b.id AND l.user_id=a.user_id
        AND l.revoked_at IS NULL AND l.membership_epoch=a.membership_epoch AND l.recovery_generation=p_generation
        AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='telegram_link' AND d.target_id=l.id)
    WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.revoked_at IS NULL AND b.recovery_generation=p_generation
        AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='telegram_binding' AND d.target_id=b.id);
END $$;
CREATE TABLE control.telegram_revocations(
    event_id uuid PRIMARY KEY,target_kind text NOT NULL CHECK(target_kind IN ('telegram_binding','telegram_link')),
    target_id uuid NOT NULL,actor_user_id uuid NOT NULL,
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE(target_kind,target_id)
);
REVOKE ALL ON control.telegram_revocations FROM PUBLIC;
CREATE TRIGGER telegram_revocations_immutable BEFORE UPDATE OR DELETE ON control.telegram_revocations
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE control.authority_restriction_outbox DROP CONSTRAINT authority_restriction_outbox_target_kind_check,
    DROP CONSTRAINT authority_restriction_outbox_restriction_kind_check;
ALTER TABLE control.authority_restriction_outbox ADD CONSTRAINT authority_restriction_outbox_target_kind_check
    CHECK(target_kind IN ('identity_session','recipe_release','standing_grant','slack_binding','slack_link','telegram_binding','telegram_link','ga4_binding')),
    ADD CONSTRAINT authority_restriction_outbox_restriction_kind_check CHECK(
       (target_kind='identity_session' AND restriction_kind='session_revoked')
       OR (target_kind IN ('recipe_release','standing_grant','slack_binding','slack_link','telegram_binding','telegram_link','ga4_binding')
           AND restriction_kind=target_kind||'_revoked'));

ALTER TABLE control.authority_denial_tombstones DROP CONSTRAINT authority_denial_tombstones_target_kind_check,
    DROP CONSTRAINT authority_denial_tombstones_restriction_kind_check;
ALTER TABLE control.authority_denial_tombstones ADD CONSTRAINT authority_denial_tombstones_target_kind_check
    CHECK(target_kind IN ('identity_session','recipe_release','standing_grant','slack_binding','slack_link','telegram_binding','telegram_link','ga4_binding')),
    ADD CONSTRAINT authority_denial_tombstones_restriction_kind_check CHECK(
       (target_kind='identity_session' AND restriction_kind='session_revoked')
       OR (target_kind IN ('recipe_release','standing_grant','slack_binding','slack_link','telegram_binding','telegram_link','ga4_binding')
           AND restriction_kind=target_kind||'_revoked'));

ALTER TABLE control.platform_events DROP CONSTRAINT platform_events_contract_check;
ALTER TABLE control.platform_events ADD CONSTRAINT platform_events_contract_check CHECK (
    (event_type = 'identity.session.issued' AND actor_user_id IS NOT NULL
     AND object_kind = 'identity_session' AND reason IS NULL
     AND jsonb_typeof(facts) = 'object'
     AND facts - 'schema_version' - 'authentication_level' = '{}'::jsonb
     AND facts -> 'schema_version' = '1'::jsonb
     AND jsonb_typeof(facts -> 'authentication_level') = 'string'
     AND facts ->> 'authentication_level' IN ('primary', 'mfa'))
    OR (event_type = 'identity.login.failed' AND actor_user_id IS NULL
     AND object_kind = 'oidc_login_attempt'
     AND reason IN ('provider_configuration_failed', 'pkce_unavailable',
                    'pkce_consume_failed', 'provider_assertion_failed',
                    'identity_not_authorized', 'session_persistence_failed',
                    'invitation_identity_not_verified', 'invitation_proof_persistence_failed')
     AND facts = '{"schema_version":1}'::jsonb)
    OR (event_type = 'identity.session.revoked' AND actor_user_id IS NOT NULL
     AND object_kind = 'identity_session' AND reason = 'user_logout'
     AND jsonb_typeof(facts) = 'object'
     AND facts - 'schema_version' - 'presented_session_kind' = '{}'::jsonb
     AND facts -> 'schema_version' = '1'::jsonb
     AND jsonb_typeof(facts -> 'presented_session_kind') = 'string'
     AND facts ->> 'presented_session_kind' IN ('identity', 'tenant'))
    OR (event_type = 'authority.restriction.acknowledged'
     AND actor_user_id IS NOT NULL AND object_kind = 'authority_restriction'
     AND reason IS NULL AND jsonb_typeof(facts) = 'object'
     AND facts -> 'schema_version' = '1'::jsonb
     AND facts - 'schema_version' - 'original_event_id' - 'stream_generation'
               - 'stream_position' - 'journal_record_id' - 'payload_hash'
               - 'verified_at' = '{}'::jsonb
     AND facts ->> 'original_event_id' = object_id::text
     AND facts ->> 'journal_record_id' = object_id::text
     AND facts ->> 'stream_generation' ~ '^[0-9a-f-]{36}$'
     AND (facts ->> 'stream_position') ~ '^[1-9][0-9]*$'
     AND facts ->> 'payload_hash' ~ '^[0-9a-f]{64}$'
     AND jsonb_typeof(facts -> 'verified_at') = 'string')
    OR (event_type = 'recipe.release.revoked'
     AND actor_user_id IS NOT NULL AND object_kind = 'recipe_release'
     AND reason = 'operator_revocation' AND jsonb_typeof(facts) = 'object'
     AND facts - 'schema_version' - 'status_event_id' - 'restriction_kind' = '{}'::jsonb
     AND facts -> 'schema_version' = '1'::jsonb
     AND facts ->> 'restriction_kind' = 'recipe_release_revoked'
     AND jsonb_typeof(facts -> 'status_event_id') = 'string'
     AND facts ->> 'status_event_id' = id::text)
    OR (event_type = 'standing.grant.revoked'
     AND actor_user_id IS NOT NULL AND object_kind = 'standing_grant'
     AND reason = 'owner_revocation' AND jsonb_typeof(facts) = 'object'
     AND facts - 'schema_version' - 'restriction_kind' - 'grant_id' = '{}'::jsonb
     AND facts -> 'schema_version' = '1'::jsonb
     AND facts ->> 'restriction_kind' = 'standing_grant_revoked'
     AND facts ->> 'grant_id' = object_id::text)
    OR (event_type = 'ga4.binding.revoked' AND object_kind = 'ga4_binding'
     AND actor_user_id IS NOT NULL AND reason = 'owner_revocation'
     AND facts = jsonb_build_object('schema_version',1,'restriction_kind','ga4_binding_revoked','target_id',object_id))
    OR (event_type IN ('slack.binding.revoked','slack.link.revoked','telegram.binding.revoked','telegram.link.revoked')
     AND actor_user_id IS NOT NULL AND object_kind IN ('slack_binding','slack_link','telegram_binding','telegram_link')
     AND reason='owner_revocation' AND facts=jsonb_build_object(
        'schema_version',1,'restriction_kind',object_kind||'_revoked','target_id',object_id))
);

CREATE OR REPLACE FUNCTION control.validate_platform_event_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.event_type = 'identity.session.issued' THEN
        IF NOT EXISTS (SELECT 1 FROM control.identity_sessions s
                       WHERE s.id = NEW.object_id AND s.user_id = NEW.actor_user_id) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
        END IF;
    ELSIF NEW.event_type = 'identity.session.revoked' THEN
        IF NOT EXISTS (SELECT 1 FROM control.identity_sessions s
                       WHERE s.id = NEW.object_id AND s.user_id = NEW.actor_user_id
                         AND s.revoked_at IS NOT NULL) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
        END IF;
    ELSIF NEW.event_type = 'identity.login.failed' THEN
        IF NOT EXISTS (SELECT 1 FROM control.oidc_login_attempts a
                       WHERE a.id = NEW.object_id AND a.consumed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
        END IF;
    ELSIF NEW.event_type = 'authority.restriction.acknowledged' THEN
        IF NOT EXISTS (SELECT 1 FROM control.authority_restriction_outbox o
                       WHERE o.event_id = NEW.object_id
                         AND o.actor_user_id = NEW.actor_user_id) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
        END IF;
    ELSIF NEW.event_type = 'recipe.release.revoked' THEN
        IF NOT EXISTS (SELECT 1 FROM control.recipe_release_events e
                       WHERE e.id = NEW.id AND e.release_id = NEW.object_id
                         AND e.actor_user_id = NEW.actor_user_id
                         AND e.status = 'REVOKED' AND e.source = 'operator') THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
        END IF;
    ELSIF NEW.event_type = 'standing.grant.revoked' THEN
        IF NOT EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
                       WHERE r.id = NEW.id AND r.grant_id = NEW.object_id
                         AND r.actor_user_id = NEW.actor_user_id) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
        END IF;

    ELSIF NEW.event_type IN ('slack.binding.revoked','slack.link.revoked') THEN
        IF NOT EXISTS(SELECT 1 FROM control.slack_revocations r WHERE r.event_id=NEW.id
            AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id
            AND r.target_kind=NEW.object_kind) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503';
        END IF;
    ELSIF NEW.event_type = 'ga4.binding.revoked' THEN
        IF NOT EXISTS(SELECT 1 FROM control.ga4_binding_restrictions r WHERE r.event_id=NEW.id
            AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503';
        END IF;
    ELSIF NEW.event_type IN ('telegram.binding.revoked','telegram.link.revoked') THEN
        IF NOT EXISTS(SELECT 1 FROM control.telegram_revocations r WHERE r.event_id=NEW.id
            AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id
            AND r.target_kind=NEW.object_kind) THEN
            RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503';
        END IF;
    ELSE
        RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION control.enqueue_session_restriction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_epoch bigint; v_kind text; v_restriction text;
BEGIN
    IF NEW.event_type = 'identity.session.revoked' THEN
        SELECT session_version INTO v_epoch FROM control.identity_sessions
        WHERE id = NEW.object_id AND revoked_at IS NOT NULL;
        v_kind := 'identity_session'; v_restriction := 'session_revoked';
    ELSIF NEW.event_type = 'recipe.release.revoked' THEN
        SELECT sequence_number INTO v_epoch FROM control.recipe_release_events
        WHERE id = NEW.id AND release_id = NEW.object_id AND status = 'REVOKED';
        v_kind := 'recipe_release'; v_restriction := 'recipe_release_revoked';
    ELSIF NEW.event_type = 'standing.grant.revoked' THEN
        SELECT 1 INTO v_epoch FROM app.standing_authorization_revocations
        WHERE id = NEW.id AND grant_id = NEW.object_id;
        v_kind := 'standing_grant'; v_restriction := 'standing_grant_revoked';

    ELSIF NEW.event_type IN ('slack.binding.revoked','slack.link.revoked') THEN
        SELECT 1 INTO v_epoch FROM control.slack_revocations WHERE event_id=NEW.id
            AND target_id=NEW.object_id AND actor_user_id=NEW.actor_user_id;
        v_kind:=NEW.object_kind; v_restriction:=v_kind||'_revoked';
    ELSIF NEW.event_type IN ('telegram.binding.revoked','telegram.link.revoked') THEN
        SELECT 1 INTO v_epoch FROM control.telegram_revocations WHERE event_id=NEW.id
            AND target_id=NEW.object_id AND actor_user_id=NEW.actor_user_id;
        v_kind:=NEW.object_kind; v_restriction:=v_kind||'_revoked';
    ELSE RETURN NEW; END IF;
    IF v_epoch IS NULL THEN
        RAISE EXCEPTION 'authority restriction target is invalid' USING ERRCODE = '23503';
    END IF;
    INSERT INTO control.authority_restriction_outbox
        (event_id, scope_kind, actor_user_id, target_kind, target_id,
         restriction_kind, effective_epoch, event_time, original_facts)
    VALUES (NEW.id, 'platform', NEW.actor_user_id, v_kind, NEW.object_id,
            v_restriction, v_epoch, NEW.created_at, NEW.facts);
    RETURN NEW;
END $$;

CREATE POLICY platform_telegram_revocation_insert ON control.platform_events
FOR INSERT TO signal_migrator WITH CHECK(event_type IN ('telegram.binding.revoked','telegram.link.revoked')
    AND EXISTS(SELECT 1 FROM control.telegram_revocations r WHERE r.event_id=platform_events.id
        AND r.target_id=platform_events.object_id AND r.actor_user_id=platform_events.actor_user_id));
CREATE FUNCTION control.revoke_telegram_authority(
    p_session bytea,p_site uuid,p_generation text,p_binding uuid,p_link uuid,p_event uuid
) RETURNS TABLE(outcome text,secret_reference text,restriction_event_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.telegram_bindings%%ROWTYPE;l app.telegram_links%%ROWTYPE;v_kind text;v_target uuid;v_event uuid;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' THEN RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    IF b.id IS NULL OR b.tenant_id<>a.tenant_id OR b.site_id<>p_site THEN
        RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
    IF p_link IS NULL THEN
        IF a.role_key<>'owner' THEN RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
        v_kind:='telegram_binding';v_target:=b.id;
        UPDATE app.telegram_bindings SET revoked_at=COALESCE(revoked_at,transaction_timestamp()) WHERE id=b.id;
    ELSE
        SELECT * INTO l FROM app.telegram_links WHERE id=p_link AND binding_id=b.id;
        IF NOT FOUND OR (l.user_id<>a.user_id AND a.role_key<>'owner') THEN
            RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
        v_kind:='telegram_link';v_target:=l.id;
        UPDATE app.telegram_links SET revoked_at=COALESCE(revoked_at,transaction_timestamp()) WHERE id=l.id;
    END IF;
    INSERT INTO control.telegram_revocations(event_id,target_kind,target_id,actor_user_id,tenant_id,site_id)
    VALUES(p_event,v_kind,v_target,a.user_id,a.tenant_id,p_site) ON CONFLICT DO NOTHING RETURNING event_id INTO v_event;
    IF v_event IS NULL THEN SELECT event_id INTO v_event FROM control.telegram_revocations
        WHERE target_kind=v_kind AND target_id=v_target;
    ELSE
        INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason)
        VALUES(v_event,CASE WHEN p_link IS NULL THEN 'telegram.binding.revoked' ELSE 'telegram.link.revoked' END,
            a.user_id,v_kind,v_target,jsonb_build_object('schema_version',1,
                'restriction_kind',v_kind||'_revoked','target_id',v_target),'owner_revocation');
    END IF;
    RETURN QUERY SELECT CASE WHEN EXISTS(SELECT 1 FROM control.platform_events e
        WHERE e.event_type='authority.restriction.acknowledged' AND e.object_id=v_event)
        THEN 'revoked' ELSE 'AUTHORITY_DURABILITY_PENDING' END,
        CASE WHEN p_link IS NULL THEN b.secret_reference ELSE NULL END,v_event;
END $$;
REVOKE ALL ON FUNCTION control.revoke_telegram_authority(bytea,uuid,text,uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.revoke_telegram_authority(bytea,uuid,text,uuid,uuid,uuid) TO signal_identity;
CREATE FUNCTION control.apply_telegram_authority_denial(
    p_event uuid,p_target uuid,p_epoch bigint,p_stream uuid,p_position bigint,p_hash text,p_kind text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_authority_dispatcher' OR p_event IS NULL OR p_target IS NULL
        OR p_epoch<>1 OR p_stream IS NULL OR p_position<1 OR p_hash !~ '^[0-9a-f]{64}$'
        OR p_kind NOT IN ('telegram_link','telegram_binding') THEN
        RAISE EXCEPTION 'telegram_replay_denied' USING ERRCODE='42501'; END IF;
    INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,
        effective_epoch,stream_generation,stream_position,payload_hash)
    VALUES(p_event,p_kind,p_target,p_kind||'_revoked',1,p_stream,p_position,p_hash)
    ON CONFLICT(event_id) DO NOTHING;
    IF NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event
        AND target_kind=p_kind AND target_id=p_target AND effective_epoch=1
        AND stream_generation=p_stream AND stream_position=p_position AND payload_hash=p_hash) THEN
        RAISE EXCEPTION 'telegram_replay_conflict' USING ERRCODE='23505'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.apply_telegram_authority_denial(uuid,uuid,bigint,uuid,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_telegram_authority_denial(uuid,uuid,bigint,uuid,bigint,text,text)
TO signal_authority_dispatcher;

CREATE FUNCTION control.resolve_telegram_link_authority(p_link uuid,p_generation text)
RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,role_key text,
    authentication_level text,membership_epoch bigint,site_authorization_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE l app.telegram_links%%ROWTYPE; a record; b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO l FROM app.telegram_links WHERE id=p_link FOR SHARE;
    IF NOT FOUND OR l.revoked_at IS NOT NULL OR l.recovery_generation IS DISTINCT FROM p_generation
       OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='telegram_link'
           AND target_id=p_link) THEN
        RETURN QUERY SELECT 'authorization_denied'::text,NULL::uuid,NULL::uuid,NULL::text,
            NULL::text,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    SELECT * INTO b FROM control.telegram_scope(l.binding_id);
    IF b.id IS NULL OR b.revoked_at IS NOT NULL OR b.state<>'active' OR b.recovery_generation IS DISTINCT FROM p_generation
       OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='telegram_binding'
           AND target_id=b.id) THEN
        RETURN QUERY SELECT 'authorization_denied'::text,NULL::uuid,NULL::uuid,NULL::text,
            NULL::text,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    SELECT * INTO a FROM control.resolve_member_site_authority(l.tenant_id,l.user_id,l.site_id,'primary');
    IF a.outcome<>'authorized' OR a.membership_epoch<>l.membership_epoch THEN
        RETURN QUERY SELECT 'authorization_denied'::text,NULL::uuid,NULL::uuid,NULL::text,
            NULL::text,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    RETURN QUERY SELECT a.outcome,a.tenant_id,a.user_id,a.role_key,a.authentication_level,
        a.membership_epoch,a.site_authorization_epoch;
END $$;
REVOKE ALL ON FUNCTION control.resolve_telegram_link_authority(uuid,text) FROM PUBLIC;

ALTER TABLE app.candidate_recipe_review_decisions DROP CONSTRAINT candidate_recipe_review_decisions_decision_channel_check;
ALTER TABLE app.candidate_recipe_review_decisions ADD CONSTRAINT candidate_recipe_review_decisions_decision_channel_check CHECK(decision_channel IN ('dashboard','slack','telegram'));
-- 0104 continues to bind the channel from the exact immutable Inbox decision.
ALTER TABLE app.github_pr_operations DROP CONSTRAINT github_pr_operations_decision_channel_check;
ALTER TABLE app.github_pr_operations ADD CONSTRAINT github_pr_operations_decision_channel_check
    CHECK(decision_channel IN ('dashboard','slack','telegram'));
CREATE OR REPLACE FUNCTION control.decide_candidate_recipe_revision_for_channel(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_revision_id uuid, p_expected_revision_sha256 bytea,
    p_decision_id uuid, p_decision text, p_channel text, p_link_id uuid
) RETURNS TABLE (
    revision_id uuid, revision_sha256 text, canonical_manifest jsonb,
    sealed_at timestamptz, recipe_release_id uuid, release_content_hash text,
    base_sha text, patch_sha256 text, review_status text, decision_id uuid,
    decision text, decided_by_user_id uuid, decision_channel text,
    decided_at timestamptz, reused boolean, outcome text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_existing app.candidate_recipe_review_decisions%%ROWTYPE;
        v_now timestamptz := transaction_timestamp(); v_reused boolean := false;
BEGIN
    IF p_channel NOT IN ('dashboard', 'slack', 'telegram') OR p_revision_id IS NULL OR p_decision_id IS NULL OR p_revision_id = p_decision_id
       OR p_expected_revision_sha256 IS NULL OR octet_length(p_expected_revision_sha256) <> 32
       OR p_decision NOT IN ('approved', 'rejected', 'changes_requested') THEN
        RAISE EXCEPTION 'invalid_candidate_recipe_decision_input' USING ERRCODE = '22023';
    END IF;
    IF p_channel='slack' THEN
        SELECT * INTO v_authority FROM control.resolve_slack_link_authority(p_link_id,p_generation);
    ELSIF p_channel='telegram' THEN
        SELECT * INTO v_authority FROM control.resolve_telegram_link_authority(p_link_id,p_generation);
    ELSE
        SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
            p_session_hash, p_site_id, p_generation);
    END IF;
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, v_authority.outcome;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'approval_permission_denied';
        RETURN;
    END IF;
    SELECT * INTO v_revision FROM app.candidate_recipe_revisions AS revision
     WHERE revision.tenant_id = v_authority.tenant_id AND revision.site_id = p_site_id
       AND revision.id = p_revision_id FOR KEY SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'approval_not_found';
        RETURN;
    END IF;
    IF v_revision.revision_sha256 <> p_expected_revision_sha256 THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'stale_revision';
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_recipe_revisions AS newer
        WHERE newer.tenant_id = v_revision.tenant_id AND newer.site_id = v_revision.site_id
          AND newer.audit_report_id = v_revision.audit_report_id AND newer.finding_id = v_revision.finding_id
          AND (newer.sealed_at, newer.id) > (v_revision.sealed_at, v_revision.id)) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'stale_revision';
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
        JOIN app.github_read_bindings AS binding ON binding.tenant_id = extension.tenant_id
         AND binding.site_id = extension.site_id AND binding.id = extension.binding_id
         AND binding.status = 'active'
        WHERE extension.tenant_id = v_revision.tenant_id AND extension.site_id = v_revision.site_id
          AND extension.id = v_revision.extension_id AND extension.status = 'observed'
          AND extension.base_sha = v_revision.base_sha) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'stale_revision';
        RETURN;
    END IF;
    IF p_channel='dashboard' AND (COALESCE(control.candidate_review_risk(p_revision_id),6)>=3
        OR EXISTS(SELECT 1 FROM app.slack_bindings b WHERE b.tenant_id=v_authority.tenant_id
            AND b.site_id=p_site_id AND b.revoked_at IS NULL AND b.recovery_generation=p_generation
            AND b.max_risk<COALESCE(control.candidate_review_risk(p_revision_id),6)
            AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d
                WHERE d.target_kind='slack_binding' AND d.target_id=b.id))
        OR EXISTS(SELECT 1 FROM app.telegram_bindings b WHERE b.tenant_id=v_authority.tenant_id
            AND b.site_id=p_site_id AND b.state='active' AND b.revoked_at IS NULL AND b.recovery_generation=p_generation
            AND b.max_risk<COALESCE(control.candidate_review_risk(p_revision_id),6)
            AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d
                WHERE d.target_kind='telegram_binding' AND d.target_id=b.id)))
        AND (v_authority.authentication_level<>'mfa' OR NOT EXISTS(
            SELECT 1 FROM app.sessions s WHERE s.session_token_hash=p_session_hash
              AND s.auth_time>v_now-interval '5 minutes')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'approval_permission_denied';
        RETURN;
    END IF;
    IF p_channel IN ('slack','telegram') AND COALESCE(control.candidate_review_risk(p_revision_id),6)>=3 THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,NULL::jsonb,NULL::timestamptz,NULL::uuid,NULL::text,
            NULL::text,NULL::text,NULL::text,NULL::uuid,NULL::text,NULL::uuid,NULL::text,NULL::timestamptz,
            false,'approval_permission_denied'; RETURN;
    END IF;
    SELECT * INTO v_existing FROM app.candidate_recipe_review_decisions AS decision
     WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
       AND decision.candidate_revision_id = p_revision_id;
    IF FOUND THEN
        IF v_existing.decision <> p_decision OR v_existing.decided_by_user_id <> v_authority.user_id THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
                NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
                NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'decision_conflict';
            RETURN;
        END IF;
        v_reused := true;
    ELSE
        INSERT INTO app.candidate_recipe_review_decisions (
            tenant_id, site_id, id, candidate_revision_id, revision_sha256, decided_by_user_id,
            authentication_level, actor_role, decision_channel, decision, recovery_generation,
            membership_epoch, site_authorization_epoch, decided_at
        ) VALUES (v_authority.tenant_id, p_site_id, p_decision_id, p_revision_id,
            p_expected_revision_sha256, v_authority.user_id,
            CASE WHEN p_channel IN ('slack','telegram') THEN 'primary' ELSE v_authority.authentication_level END,
            v_authority.role_key, p_channel, p_decision, p_generation,
            v_authority.membership_epoch, v_authority.site_authorization_epoch, v_now)
        ON CONFLICT ON CONSTRAINT candidate_recipe_review_decisions_revision_key DO NOTHING;
        IF NOT FOUND THEN
            SELECT * INTO v_existing FROM app.candidate_recipe_review_decisions AS decision
             WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
               AND decision.candidate_revision_id = p_revision_id;
            IF NOT FOUND OR v_existing.decision <> p_decision
               OR v_existing.decided_by_user_id <> v_authority.user_id THEN
                RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
                    NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'decision_conflict';
                RETURN;
            END IF;
            v_reused := true;
        ELSE
            SELECT * INTO v_existing FROM app.candidate_recipe_review_decisions AS decision
             WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
               AND decision.candidate_revision_id = p_revision_id;
        END IF;
    END IF;
    RETURN QUERY SELECT v_revision.id, encode(v_revision.revision_sha256, 'hex'),
        convert_from(v_revision.canonical_manifest, 'UTF8')::jsonb, v_revision.sealed_at,
        v_revision.recipe_release_id, encode(v_revision.release_content_hash, 'hex'),
        v_revision.base_sha, v_revision.patch_sha256, v_existing.decision, v_existing.id,
        v_existing.decision, v_existing.decided_by_user_id, v_existing.decision_channel,
        v_existing.decided_at, v_reused, 'decided';
END; $$;

CREATE FUNCTION control.enqueue_telegram_approval(p_session bytea,p_site uuid,p_generation text,p_binding uuid,
    p_id uuid,p_revision uuid,p_hash bytea,p_approve bytea,p_reject bytea,p_payload jsonb)
RETURNS TABLE(outbox_id uuid,outcome text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.telegram_bindings%%ROWTYPE;l app.telegram_links%%ROWTYPE;r record;existing uuid;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN RETURN QUERY SELECT NULL::uuid,'denied'::text;RETURN;END IF;
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    IF b.id IS NULL OR b.tenant_id<>a.tenant_id OR b.site_id<>p_site OR b.revoked_at IS NOT NULL
        OR b.state<>'active' OR b.recovery_generation<>p_generation THEN RETURN QUERY SELECT NULL::uuid,'denied'::text;RETURN;END IF;
    SELECT * INTO l FROM app.telegram_links WHERE binding_id=p_binding AND user_id=a.user_id
        AND revoked_at IS NULL AND recovery_generation=p_generation;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid,'unlinked'::text;RETURN;END IF;
    SELECT * INTO r FROM control.resolve_telegram_link_authority(l.id,p_generation);
    IF r.outcome<>'authorized' OR p_payload->>'chat_id' IS DISTINCT FROM l.chat_id THEN
        RETURN QUERY SELECT NULL::uuid,'denied'::text;RETURN;END IF;
    SELECT * INTO r FROM control.read_authenticated_candidate_recipe_inbox(p_session,p_site,p_generation)
        WHERE revision_id=p_revision AND revision_sha256=encode(p_hash,'hex') AND review_status='pending';
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid,'stale_revision'::text;RETURN;END IF;
    INSERT INTO app.telegram_outbox(tenant_id,site_id,id,binding_id,intended_user_id,chat_id,kind,method,
        revision_id,revision_sha256,approve_sha256,reject_sha256,payload)
    VALUES(a.tenant_id,p_site,p_id,p_binding,l.telegram_user_id,l.chat_id,'approval','sendMessage',
        p_revision,p_hash,p_approve,p_reject,p_payload) ON CONFLICT DO NOTHING RETURNING id INTO existing;
    IF existing IS NULL THEN SELECT id INTO existing FROM app.telegram_outbox
        WHERE binding_id=p_binding AND intended_user_id=l.telegram_user_id AND revision_id=p_revision;END IF;
    RETURN QUERY SELECT existing,'queued'::text;
END $$;

CREATE FUNCTION control.telegram_outbox_item(p_binding uuid,p_id uuid,p_generation text,p_claim boolean)
RETURNS TABLE(payload jsonb,secret_reference text,state text,method text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;o app.telegram_outbox%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    IF b.id IS NULL OR b.revoked_at IS NOT NULL OR b.state<>'active' OR b.recovery_generation IS DISTINCT FROM p_generation
        OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='telegram_binding' AND target_id=b.id)
        THEN RETURN;END IF;
    SELECT * INTO o FROM app.telegram_outbox WHERE binding_id=p_binding AND id=p_id FOR UPDATE;
    IF NOT FOUND THEN RETURN;END IF;
    IF o.state='queued' AND (o.expires_at<=transaction_timestamp()
        OR NOT EXISTS(SELECT 1 FROM app.telegram_links l CROSS JOIN LATERAL control.resolve_telegram_link_authority(l.id,p_generation) a
            WHERE l.binding_id=p_binding AND l.telegram_user_id=o.intended_user_id AND l.chat_id=o.chat_id
            AND a.outcome='authorized' AND (o.kind='reply' OR a.role_key='owner'))
        OR (o.kind='approval' AND NOT control.slack_revision_current(o.revision_id,o.revision_sha256))) THEN RETURN;END IF;
    IF p_claim AND o.state='queued' THEN
        UPDATE app.telegram_outbox SET state='dispatching' WHERE id=p_id AND binding_id=p_binding;
        RETURN QUERY SELECT o.payload,b.secret_reference,'claimed'::text,o.method;RETURN;END IF;
    RETURN QUERY SELECT o.payload,b.secret_reference,o.state,o.method;
END $$;
CREATE FUNCTION control.defer_telegram_outbox(p_binding uuid,p_id uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    IF b.id IS NULL THEN RETURN 'unavailable';END IF;
    IF EXISTS(SELECT 1 FROM app.egress_operations WHERE id=p_id) THEN RETURN 'unknown';END IF;
    UPDATE app.telegram_outbox SET state='queued' WHERE binding_id=p_binding AND id=p_id AND state='dispatching';
    RETURN 'queued';
END $$;
CREATE FUNCTION control.finish_telegram_outbox(p_binding uuid,p_id uuid,p_chat text,p_message text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;o app.telegram_outbox%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    SELECT * INTO o FROM app.telegram_outbox WHERE binding_id=p_binding AND id=p_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable';END IF;
    IF o.state<>'dispatching' THEN RETURN o.state;END IF;
    IF NOT COALESCE(o.method='answerCallbackQuery' AND p_message='ack',false) AND
        (p_chat IS NULL OR p_message IS NULL OR p_chat<>o.chat_id OR p_message !~ '^[1-9][0-9]{0,15}$') THEN
        UPDATE app.telegram_outbox SET state='unknown' WHERE id=p_id AND binding_id=p_binding;RETURN 'unknown';END IF;
    UPDATE app.telegram_outbox SET state='accepted',message_id=p_message WHERE id=p_id AND binding_id=p_binding;
    RETURN 'accepted';
END $$;
CREATE FUNCTION control.audit_telegram_rejection(p_binding uuid,p_body bytea,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    IF b.id IS NULL OR p_reason NOT IN ('secret_rejected','payload_rejected') THEN RETURN;END IF;
    INSERT INTO app.telegram_ingress_audit(tenant_id,site_id,id,binding_id,body_sha256,outcome)
    VALUES(b.tenant_id,b.site_id,gen_random_uuid(),b.id,p_body,p_reason);
END $$;

CREATE FUNCTION control.handle_telegram_update(p_binding uuid,p_generation text,p_update bigint,p_body bytea,
    p_kind text,p_user text,p_chat text,p_code bytea,p_message text,p_callback text,p_decision uuid,p_dashboard text)
RETURNS TABLE(outcome text,decision_id uuid,reply_outbox_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.telegram_bindings%%ROWTYPE;o app.telegram_outbox%%ROWTYPE;l app.telegram_links%%ROWTYPE;
    c app.telegram_link_codes%%ROWTYPE;a record;result record;v_risk integer;
    v_outcome text:='wrong_binding';v_decision uuid;v_reply uuid;v_payload jsonb;v_method text;
BEGIN
    SELECT * INTO b FROM control.telegram_scope(p_binding);
    IF b.id IS NULL THEN RETURN QUERY SELECT 'wrong_binding'::text,NULL::uuid,NULL::uuid;RETURN;END IF;
    IF p_update IS NULL OR p_update<0 OR p_update>4503599627370495 OR octet_length(p_body) IS DISTINCT FROM 32 THEN
        RAISE EXCEPTION 'invalid_telegram_update_evidence' USING ERRCODE='22023';END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_binding::text||p_update::text,0));
    IF EXISTS(SELECT 1 FROM app.telegram_ingress_audit WHERE binding_id=p_binding AND update_id=p_update) THEN
        INSERT INTO app.telegram_ingress_audit(tenant_id,site_id,id,binding_id,body_sha256,update_id,outcome)
        VALUES(b.tenant_id,b.site_id,gen_random_uuid(),b.id,p_body,p_update,'replay');
        RETURN QUERY SELECT 'replay'::text,NULL::uuid,NULL::uuid;RETURN;END IF;
    IF b.state='active' AND b.revoked_at IS NULL AND b.recovery_generation=p_generation
        AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='telegram_binding' AND target_id=b.id) THEN
        IF p_kind='group_ignored' THEN v_outcome:='group_ignored';
        ELSIF p_kind IN ('ignored','wrong_binding') THEN
            v_outcome:=p_kind;
            IF p_kind='ignored' AND p_user IS NOT NULL AND NOT EXISTS(SELECT 1 FROM app.telegram_links WHERE binding_id=b.id
                AND telegram_user_id=p_user AND revoked_at IS NULL) THEN v_outcome:='unlinked';END IF;
        ELSIF p_chat IS DISTINCT FROM p_user OR p_user IS NULL THEN v_outcome:='wrong_binding';
        ELSIF p_kind='pair' THEN
            SELECT * INTO c FROM app.telegram_link_codes WHERE binding_id=p_binding AND code_sha256=p_code
                AND telegram_user_id=p_user AND consumed_at IS NULL AND expires_at>transaction_timestamp()
                AND recovery_generation=p_generation FOR UPDATE;
            IF NOT FOUND THEN v_outcome:='unlinked';ELSE
                SELECT * INTO a FROM control.resolve_member_site_authority(c.tenant_id,c.user_id,c.site_id,'primary');
                IF a.outcome<>'authorized' OR a.membership_epoch<>c.membership_epoch THEN v_outcome:='authority_denied';ELSE
                    INSERT INTO app.telegram_links(tenant_id,site_id,id,binding_id,user_id,telegram_user_id,chat_id,
                        membership_epoch,recovery_generation)
                    VALUES(c.tenant_id,c.site_id,gen_random_uuid(),p_binding,c.user_id,p_user,p_chat,
                        c.membership_epoch,c.recovery_generation) ON CONFLICT DO NOTHING;
                    IF NOT FOUND THEN v_outcome:='unlinked';ELSE
                        UPDATE app.telegram_link_codes SET consumed_at=transaction_timestamp() WHERE id=c.id;
                        v_outcome:='linked';END IF;
                END IF;
            END IF;
        ELSIF p_kind='decision' THEN
            SELECT * INTO l FROM app.telegram_links WHERE binding_id=p_binding AND telegram_user_id=p_user
                AND chat_id=p_chat AND revoked_at IS NULL;
            IF NOT FOUND THEN v_outcome:='unlinked';ELSE
                SELECT * INTO a FROM control.resolve_telegram_link_authority(l.id,p_generation);
                IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN v_outcome:='authority_denied';ELSE
                    SELECT * INTO o FROM app.telegram_outbox WHERE binding_id=p_binding AND kind='approval' AND state='accepted'
                        AND chat_id=p_chat AND intended_user_id=p_user AND message_id=p_message
                        AND expires_at>transaction_timestamp() AND (approve_sha256=p_code OR reject_sha256=p_code) FOR UPDATE;
                    IF NOT FOUND THEN v_outcome:='stale_revision';ELSE
                        v_risk:=control.candidate_review_risk(o.revision_id);
                        IF NOT control.slack_revision_current(o.revision_id,o.revision_sha256) THEN v_outcome:='stale_revision';
                        ELSIF v_risk IS NULL OR v_risk>=3 OR v_risk>b.max_risk THEN v_outcome:='step_up';ELSE
                            SELECT * INTO result FROM control.decide_candidate_recipe_revision_for_channel(
                                NULL,b.site_id,p_generation,o.revision_id,o.revision_sha256,p_decision,
                                CASE WHEN o.approve_sha256=p_code THEN 'approved' ELSE 'rejected' END,'telegram',l.id);
                            v_outcome:=CASE WHEN result.outcome='decided' THEN 'decided'
                                WHEN result.outcome='decision_conflict' THEN 'decision_conflict'
                                WHEN result.outcome IN ('approval_not_found','stale_revision') THEN 'stale_revision'
                                ELSE 'authority_denied' END;
                            v_decision:=result.decision_id;
                        END IF;
                    END IF;
                END IF;
            END IF;
        END IF;
    END IF;
    INSERT INTO app.telegram_ingress_audit(tenant_id,site_id,id,binding_id,body_sha256,update_id,outcome)
    VALUES(b.tenant_id,b.site_id,gen_random_uuid(),b.id,p_body,p_update,v_outcome);
    IF v_outcome IN ('decided','step_up') AND p_callback ~ '^[A-Za-z0-9_-]{1,128}$' THEN
        v_reply:=gen_random_uuid();v_method:='answerCallbackQuery';
        v_payload:=jsonb_build_object('callback_query_id',p_callback,'text','Signal: '||v_outcome);
        IF v_outcome='step_up' THEN
            IF p_dashboard !~ '^https://[A-Za-z0-9.:-]+/approvals$' THEN
                RAISE EXCEPTION 'invalid_telegram_dashboard' USING ERRCODE='22023';END IF;
            v_method:='sendMessage';
            v_payload:=jsonb_build_object('chat_id',p_chat,'text','Continue in the dashboard for step-up authentication.',
                'reply_markup',jsonb_build_object('inline_keyboard',jsonb_build_array(jsonb_build_array(
                    jsonb_build_object('text','Dashboard','url',p_dashboard||'?revision='||o.revision_id::text)))));
        END IF;
        INSERT INTO app.telegram_outbox(tenant_id,site_id,id,binding_id,intended_user_id,chat_id,kind,method,payload)
        VALUES(b.tenant_id,b.site_id,v_reply,p_binding,p_user,p_chat,'reply',v_method,v_payload);
    END IF;
    RETURN QUERY SELECT v_outcome,v_decision,v_reply;
END $$;

REVOKE ALL ON FUNCTION control.prepare_telegram_binding(bytea,uuid,text,uuid,integer),
    control.confirm_telegram_binding(bytea,uuid,text,uuid,text,text,boolean),control.fail_telegram_binding(uuid),
    control.telegram_ingress_secret(uuid),control.finish_telegram_revocation(uuid),control.claim_telegram_revocation(uuid),control.begin_telegram_link(bytea,uuid,text,uuid,uuid,text,bytea),
    control.read_telegram_binding(bytea,uuid,text),
    control.enqueue_telegram_approval(bytea,uuid,text,uuid,uuid,uuid,bytea,bytea,bytea,jsonb),
    control.telegram_outbox_item(uuid,uuid,text,boolean),control.defer_telegram_outbox(uuid,uuid),
    control.finish_telegram_outbox(uuid,uuid,text,text),control.audit_telegram_rejection(uuid,bytea,text),
    control.handle_telegram_update(uuid,text,bigint,bytea,text,text,text,bytea,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_telegram_binding(bytea,uuid,text,uuid,integer),
    control.confirm_telegram_binding(bytea,uuid,text,uuid,text,text,boolean),control.fail_telegram_binding(uuid),
    control.telegram_ingress_secret(uuid),control.finish_telegram_revocation(uuid),control.claim_telegram_revocation(uuid),control.begin_telegram_link(bytea,uuid,text,uuid,uuid,text,bytea),
    control.read_telegram_binding(bytea,uuid,text),
    control.enqueue_telegram_approval(bytea,uuid,text,uuid,uuid,uuid,bytea,bytea,bytea,jsonb),
    control.telegram_outbox_item(uuid,uuid,text,boolean),control.defer_telegram_outbox(uuid,uuid),
    control.finish_telegram_outbox(uuid,uuid,text,text),control.audit_telegram_rejection(uuid,bytea,text),
    control.handle_telegram_update(uuid,text,bigint,bytea,text,text,text,bytea,text,text,uuid,text) TO signal_identity;
