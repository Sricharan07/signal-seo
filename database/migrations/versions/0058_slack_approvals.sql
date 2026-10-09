ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check
CHECK (egress_profile IN (
    'legacy_unqualified', 'crawl_page', 'crawl_robots', 'browser_read',
    'github_rest', 'slack_bot', 'slack_oauth', 'github_repository_write', 'google_oauth_token', 'google_oauth_revoke',
    'gsc_api', 'bing_oauth_token', 'bing_api', 'jev', 'model_json', 'openai_model',
    'openai_assistant', 'perplexity_assistant', 'gemini_assistant'
));

ALTER FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text)
RENAME TO bind_before_slack_egress_profile;
REVOKE ALL ON FUNCTION control.bind_before_slack_egress_profile(uuid,uuid,uuid,bytea,text)
FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(
    p_tenant_id uuid,p_site_id uuid,p_operation_id uuid,p_request_sha256 bytea,p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE;
BEGIN
    IF p_profile NOT IN ('slack_bot','slack_oauth') THEN
        RETURN control.bind_before_slack_egress_profile(p_tenant_id,p_site_id,p_operation_id,
            p_request_sha256,p_profile);
    END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant_id AND site_id=p_site_id
        AND id=p_operation_id FOR UPDATE;
    IF NOT FOUND OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_request_sha256
        OR o.purpose<>'connector' OR o.origin<>'https://slack.com' OR o.method<>'POST'
        OR NOT o.credentialed OR o.request_bytes>16384 OR o.max_response_bytes>16384
        OR (p_profile='slack_bot' AND o.request_url NOT IN
            ('https://slack.com/api/chat.postMessage','https://slack.com/api/auth.revoke'))
        OR (p_profile='slack_oauth' AND o.request_url<>'https://slack.com/api/oauth.v2.access')
        OR o.egress_profile NOT IN ('legacy_unqualified',p_profile) THEN
        RAISE EXCEPTION 'slack_profile_denied' USING ERRCODE='22023'; END IF;
    IF o.egress_profile='legacy_unqualified' THEN
        UPDATE app.egress_operations SET egress_profile=p_profile
        WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id;
    END IF;
    RETURN 'bound';
END $$;
REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;

CREATE TABLE app.slack_oauth_attempts (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    user_id uuid NOT NULL, state_sha256 bytea NOT NULL UNIQUE CHECK (octet_length(state_sha256)=32),
    workspace_id text NOT NULL CHECK (workspace_id ~ '^T[A-Z0-9]{7,63}$'),
    channel_id text NOT NULL CHECK (channel_id ~ '^[CG][A-Z0-9]{7,63}$'),
    redirect_uri text NOT NULL CHECK (redirect_uri ~ '^https://[^[:space:]?#]+/[^[:space:]?#]*$'),
    max_risk integer NOT NULL CHECK (max_risk BETWEEN 0 AND 2),
    recovery_generation text NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '10 minutes',
    consumed_at timestamptz,
    PRIMARY KEY (tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY (tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TABLE app.slack_bindings (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL UNIQUE,
    workspace_id text NOT NULL CHECK (workspace_id ~ '^T[A-Z0-9]{7,63}$'),
    channel_id text NOT NULL CHECK (channel_id ~ '^[CG][A-Z0-9]{7,63}$'),
    owner_user_id uuid NOT NULL, secret_reference text NOT NULL,
    max_risk integer NOT NULL CHECK (max_risk BETWEEN 0 AND 2),
    recovery_generation text NOT NULL, revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY (tenant_id,owner_user_id) REFERENCES app.memberships(tenant_id,user_id),
    CHECK (secret_reference='secret://slack/'||id::text)
);
CREATE UNIQUE INDEX slack_one_site_binding ON app.slack_bindings(tenant_id,site_id)
WHERE revoked_at IS NULL;
CREATE TABLE control.slack_binding_routes (
    id uuid PRIMARY KEY, tenant_id uuid NOT NULL, site_id uuid NOT NULL,
    FOREIGN KEY(tenant_id,site_id,id) REFERENCES app.slack_bindings(tenant_id,site_id,id)
);
REVOKE ALL ON control.slack_binding_routes FROM PUBLIC;
CREATE TABLE app.slack_links (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL UNIQUE,
    binding_id uuid NOT NULL, user_id uuid NOT NULL,
    slack_user_id text NOT NULL CHECK (slack_user_id ~ '^[UW][A-Z0-9]{7,63}$'),
    membership_epoch bigint NOT NULL, recovery_generation text NOT NULL,
    revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.slack_bindings(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE UNIQUE INDEX slack_one_user_link ON app.slack_links(binding_id,slack_user_id)
WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX slack_one_membership_link ON app.slack_links(binding_id,user_id)
WHERE revoked_at IS NULL;
CREATE TABLE app.slack_link_codes (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    binding_id uuid NOT NULL, user_id uuid NOT NULL, slack_user_id text NOT NULL,
    membership_epoch bigint NOT NULL, recovery_generation text NOT NULL,
    code_sha256 bytea NOT NULL UNIQUE CHECK (octet_length(code_sha256)=32),
    expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '5 minutes',
    consumed_at timestamptz,
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.slack_bindings(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TABLE app.slack_outbox (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    binding_id uuid NOT NULL, intended_user_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('link','approval','reply')),
    revision_id uuid, revision_sha256 bytea,
    link_code_id uuid, approve_sha256 bytea, reject_sha256 bytea,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','dispatching','accepted','unknown')),
    expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '1 hour',
    channel_id text, message_ts text,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.slack_bindings(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,revision_id,revision_sha256)
        REFERENCES app.candidate_recipe_revisions(tenant_id,site_id,id,revision_sha256),
    FOREIGN KEY(tenant_id,site_id,link_code_id) REFERENCES app.slack_link_codes(tenant_id,site_id,id),
    CHECK ((kind='link' AND link_code_id IS NOT NULL AND revision_id IS NULL)
        OR (kind='approval' AND revision_id IS NOT NULL AND octet_length(approve_sha256)=32
            AND octet_length(reject_sha256)=32 AND approve_sha256<>reject_sha256)
        OR kind='reply'),
    CHECK ((state='accepted' AND channel_id IS NOT NULL AND message_ts IS NOT NULL)
        OR (state<>'accepted' AND channel_id IS NULL AND message_ts IS NULL))
);
CREATE UNIQUE INDEX slack_request_dedup ON app.slack_outbox(binding_id,intended_user_id,revision_id)
WHERE kind='approval';
CREATE TABLE app.slack_ingress_audit (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    binding_id uuid NOT NULL, body_sha256 bytea NOT NULL CHECK (octet_length(body_sha256)=32),
    signature_sha256 bytea, outcome text NOT NULL CHECK (outcome IN (
        'signature_rejected','payload_rejected','replay','wrong_binding','unlinked',
        'authority_denied','stale_revision','step_up','linked','decided','decision_conflict')),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.slack_bindings(tenant_id,site_id,id)
);
CREATE UNIQUE INDEX slack_signature_once ON app.slack_ingress_audit(binding_id,signature_sha256)
WHERE signature_sha256 IS NOT NULL AND outcome<>'replay';
CREATE TRIGGER slack_ingress_immutable BEFORE UPDATE OR DELETE ON app.slack_ingress_audit
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['slack_oauth_attempts','slack_bindings','slack_links',
        'slack_link_codes','slack_outbox','slack_ingress_audit'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY slack_scope ON app.%%I USING '
            '(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK '
            '(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_identity,signal_api,'
            'signal_bootstrap,signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END $$;

CREATE FUNCTION control.slack_scope(p_binding_id uuid) RETURNS app.slack_bindings
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; b app.slack_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO r FROM control.slack_binding_routes WHERE id=p_binding_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',r.tenant_id::text,true);
    PERFORM set_config('signal.site_id',r.site_id::text,true);
    SELECT * INTO b FROM app.slack_bindings WHERE id=p_binding_id;
    RETURN b;
END $$;
REVOKE ALL ON FUNCTION control.slack_scope(uuid) FROM PUBLIC;

CREATE FUNCTION control.begin_slack_oauth(
    p_session bytea,p_site uuid,p_generation text,p_id uuid,p_state bytea,
    p_workspace text,p_channel text,p_redirect text,p_max_risk integer
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    INSERT INTO app.slack_oauth_attempts(tenant_id,site_id,id,user_id,state_sha256,
        workspace_id,channel_id,redirect_uri,max_risk,recovery_generation)
    VALUES(a.tenant_id,p_site,p_id,a.user_id,p_state,p_workspace,p_channel,p_redirect,p_max_risk,p_generation);
    RETURN 'created';
END $$;
CREATE FUNCTION control.consume_slack_oauth(
    p_session bytea,p_site uuid,p_generation text,p_id uuid,p_state bytea,p_redirect text
) RETURNS TABLE(workspace_id text,channel_id text) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN; END IF;
    RETURN QUERY UPDATE app.slack_oauth_attempts o SET consumed_at=transaction_timestamp()
      WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site AND o.id=p_id AND o.user_id=a.user_id
        AND o.state_sha256=p_state AND o.redirect_uri=p_redirect AND o.recovery_generation=p_generation
        AND o.expires_at>transaction_timestamp() AND o.consumed_at IS NULL
      RETURNING o.workspace_id,o.channel_id;
END $$;
CREATE FUNCTION control.confirm_slack_oauth(
    p_session bytea,p_site uuid,p_generation text,p_id uuid,p_binding uuid,p_workspace text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o app.slack_oauth_attempts%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    SELECT * INTO o FROM app.slack_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site
        AND id=p_id AND user_id=a.user_id AND consumed_at IS NOT NULL
        AND expires_at>transaction_timestamp() AND recovery_generation=p_generation FOR UPDATE;
    IF NOT FOUND OR o.workspace_id<>p_workspace THEN RETURN 'wrong_workspace'; END IF;
    INSERT INTO app.slack_bindings(tenant_id,site_id,id,workspace_id,channel_id,owner_user_id,
        secret_reference,max_risk,recovery_generation)
    VALUES(a.tenant_id,p_site,p_binding,o.workspace_id,o.channel_id,a.user_id,
        'secret://slack/'||p_binding::text,o.max_risk,p_generation);
    INSERT INTO control.slack_binding_routes VALUES(p_binding,a.tenant_id,p_site);
    DELETE FROM app.slack_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id;
    RETURN 'bound';
END $$;

CREATE FUNCTION control.resolve_member_site_authority(
    p_tenant_id uuid, p_user_id uuid, p_site_id uuid, p_authentication_level text
) RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,role_key text,
    authentication_level text,membership_epoch bigint,site_authorization_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_role_key text; v_membership_epoch bigint; v_site_authorization_epoch bigint;
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT membership.role_key, membership.authorization_epoch,
           site_membership.authorization_epoch
      INTO v_role_key, v_membership_epoch, v_site_authorization_epoch
      FROM app.memberships AS membership
      JOIN app.site_memberships AS site_membership
        ON site_membership.tenant_id = membership.tenant_id
       AND site_membership.user_id = membership.user_id
      JOIN app.tenants AS tenant
        ON tenant.tenant_id = membership.tenant_id
      JOIN app.sites AS site
        ON site.tenant_id = site_membership.tenant_id
       AND site.id = site_membership.site_id
     WHERE membership.tenant_id = p_tenant_id
       AND membership.user_id = p_user_id
       AND membership.state = 'active'
       AND EXISTS(SELECT 1 FROM control.users u WHERE u.id=p_user_id AND u.disabled_at IS NULL)
       AND site_membership.site_id = p_site_id
       AND site_membership.state = 'active'
       AND site_membership.permission_set =
           '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb
       AND tenant.lifecycle = 'active'
       AND site.state != 'archived'
     FOR KEY SHARE OF membership, site_membership, tenant, site;

    IF NOT FOUND THEN
        RETURN QUERY SELECT 'authorization_denied'::text, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;

    RETURN QUERY SELECT 'authorized'::text, p_tenant_id, p_user_id, v_role_key,
                        p_authentication_level, v_membership_epoch,
                        v_site_authorization_epoch;
END; $$;
REVOKE ALL ON FUNCTION control.resolve_member_site_authority(uuid,uuid,uuid,text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.resolve_snapshot_authority(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text
)
RETURNS TABLE (
    outcome text,
    tenant_id uuid,
    user_id uuid,
    role_key text,
    authentication_level text,
    membership_epoch bigint,
    site_authorization_epoch bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_tenant_id uuid;
    v_user_id uuid;
    v_active_site_id uuid;
    v_authentication_level text;
    v_role_key text;
    v_membership_epoch bigint;
    v_site_authorization_epoch bigint;
BEGIN
    IF p_session_hash IS NULL OR octet_length(p_session_hash) <> 32
       OR p_requested_site_id IS NULL
       OR p_current_recovery_generation IS NULL
       OR p_current_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    THEN
        RAISE EXCEPTION 'invalid_snapshot_authority_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.session_hash', encode(p_session_hash, 'hex'), true);
    SELECT tenant_session.tenant_id, tenant_session.user_id,
           tenant_session.active_site_id, identity_session.authentication_level
      INTO v_tenant_id, v_user_id, v_active_site_id, v_authentication_level
      FROM app.sessions AS tenant_session
      JOIN control.identity_sessions AS identity_session
        ON identity_session.id = tenant_session.identity_session_id
       AND identity_session.user_id = tenant_session.user_id
      JOIN control.users AS identity_user
        ON identity_user.id = tenant_session.user_id
     WHERE tenant_session.session_token_hash = p_session_hash
       AND tenant_session.revoked_at IS NULL
       AND tenant_session.expires_at > transaction_timestamp()
       AND identity_session.revoked_at IS NULL
       AND identity_session.expires_at > transaction_timestamp()
       AND identity_session.recovery_generation = p_current_recovery_generation
       AND tenant_session.auth_time = identity_session.auth_time
       AND tenant_session.mfa_level = identity_session.authentication_level
       AND identity_user.disabled_at IS NULL
     FOR KEY SHARE OF tenant_session, identity_session, identity_user;

    IF NOT FOUND THEN
        RETURN QUERY SELECT 'invalid_session'::text, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;

    IF v_active_site_id IS DISTINCT FROM p_requested_site_id THEN
        RETURN QUERY SELECT 'authorization_denied'::text, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;

    RETURN QUERY SELECT * FROM control.resolve_member_site_authority(
        v_tenant_id,v_user_id,p_requested_site_id,v_authentication_level);
END; $$;

CREATE TABLE control.slack_revocations(
    event_id uuid PRIMARY KEY,target_kind text NOT NULL CHECK(target_kind IN ('slack_binding','slack_link')),
    target_id uuid NOT NULL,actor_user_id uuid NOT NULL,
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE(target_kind,target_id)
);
REVOKE ALL ON control.slack_revocations FROM PUBLIC;
CREATE TRIGGER slack_revocations_immutable BEFORE UPDATE OR DELETE ON control.slack_revocations
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE control.authority_restriction_outbox DROP CONSTRAINT authority_restriction_outbox_target_kind_check,
    DROP CONSTRAINT authority_restriction_outbox_restriction_kind_check;
ALTER TABLE control.authority_restriction_outbox ADD CONSTRAINT authority_restriction_outbox_target_kind_check
    CHECK(target_kind IN ('identity_session','recipe_release','standing_grant','slack_binding','slack_link')),
    ADD CONSTRAINT authority_restriction_outbox_restriction_kind_check CHECK(
       (target_kind='identity_session' AND restriction_kind='session_revoked')
       OR (target_kind IN ('recipe_release','standing_grant','slack_binding','slack_link')
           AND restriction_kind=target_kind||'_revoked'));

ALTER TABLE control.authority_denial_tombstones DROP CONSTRAINT authority_denial_tombstones_target_kind_check,
    DROP CONSTRAINT authority_denial_tombstones_restriction_kind_check;
ALTER TABLE control.authority_denial_tombstones ADD CONSTRAINT authority_denial_tombstones_target_kind_check
    CHECK(target_kind IN ('identity_session','recipe_release','standing_grant','slack_binding','slack_link')),
    ADD CONSTRAINT authority_denial_tombstones_restriction_kind_check CHECK(
       (target_kind='identity_session' AND restriction_kind='session_revoked')
       OR (target_kind IN ('recipe_release','standing_grant','slack_binding','slack_link')
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
    OR (event_type IN ('slack.binding.revoked','slack.link.revoked')
     AND actor_user_id IS NOT NULL AND object_kind IN ('slack_binding','slack_link')
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

CREATE POLICY platform_slack_revocation_insert ON control.platform_events
FOR INSERT TO signal_migrator WITH CHECK(event_type IN ('slack.binding.revoked','slack.link.revoked')
    AND EXISTS(SELECT 1 FROM control.slack_revocations r WHERE r.event_id=platform_events.id
        AND r.target_id=platform_events.object_id AND r.actor_user_id=platform_events.actor_user_id));
CREATE FUNCTION control.revoke_slack_authority(
    p_session bytea,p_site uuid,p_generation text,p_binding uuid,p_link uuid,p_event uuid
) RETURNS TABLE(outcome text,secret_reference text,restriction_event_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.slack_bindings%%ROWTYPE;l app.slack_links%%ROWTYPE;v_kind text;v_target uuid;v_event uuid;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' THEN RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
    SELECT * INTO b FROM control.slack_scope(p_binding);
    IF b.id IS NULL OR b.tenant_id<>a.tenant_id OR b.site_id<>p_site THEN
        RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
    IF p_link IS NULL THEN
        IF a.role_key<>'owner' THEN RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
        v_kind:='slack_binding';v_target:=b.id;
        UPDATE app.slack_bindings SET revoked_at=COALESCE(revoked_at,transaction_timestamp()) WHERE id=b.id;
    ELSE
        SELECT * INTO l FROM app.slack_links WHERE id=p_link AND binding_id=b.id;
        IF NOT FOUND OR (l.user_id<>a.user_id AND a.role_key<>'owner') THEN
            RETURN QUERY SELECT 'denied'::text,NULL::text,NULL::uuid; RETURN; END IF;
        v_kind:='slack_link';v_target:=l.id;
        UPDATE app.slack_links SET revoked_at=COALESCE(revoked_at,transaction_timestamp()) WHERE id=l.id;
    END IF;
    INSERT INTO control.slack_revocations(event_id,target_kind,target_id,actor_user_id,tenant_id,site_id)
    VALUES(p_event,v_kind,v_target,a.user_id,a.tenant_id,p_site) ON CONFLICT DO NOTHING RETURNING event_id INTO v_event;
    IF v_event IS NULL THEN SELECT event_id INTO v_event FROM control.slack_revocations
        WHERE target_kind=v_kind AND target_id=v_target;
    ELSE
        INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason)
        VALUES(v_event,CASE WHEN p_link IS NULL THEN 'slack.binding.revoked' ELSE 'slack.link.revoked' END,
            a.user_id,v_kind,v_target,jsonb_build_object('schema_version',1,
                'restriction_kind',v_kind||'_revoked','target_id',v_target),'owner_revocation');
    END IF;
    RETURN QUERY SELECT CASE WHEN EXISTS(SELECT 1 FROM control.platform_events e
        WHERE e.event_type='authority.restriction.acknowledged' AND e.object_id=v_event)
        THEN 'revoked' ELSE 'AUTHORITY_DURABILITY_PENDING' END,
        CASE WHEN p_link IS NULL THEN b.secret_reference ELSE NULL END,v_event;
END $$;
REVOKE ALL ON FUNCTION control.revoke_slack_authority(bytea,uuid,text,uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.revoke_slack_authority(bytea,uuid,text,uuid,uuid,uuid) TO signal_identity;
CREATE FUNCTION control.apply_slack_authority_denial(
    p_event uuid,p_target uuid,p_epoch bigint,p_stream uuid,p_position bigint,p_hash text,p_kind text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_authority_dispatcher' OR p_event IS NULL OR p_target IS NULL
        OR p_epoch<>1 OR p_stream IS NULL OR p_position<1 OR p_hash !~ '^[0-9a-f]{64}$'
        OR p_kind NOT IN ('slack_link','slack_binding') THEN
        RAISE EXCEPTION 'slack_replay_denied' USING ERRCODE='42501'; END IF;
    INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,
        effective_epoch,stream_generation,stream_position,payload_hash)
    VALUES(p_event,p_kind,p_target,p_kind||'_revoked',1,p_stream,p_position,p_hash)
    ON CONFLICT(event_id) DO NOTHING;
    IF NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event
        AND target_kind=p_kind AND target_id=p_target AND effective_epoch=1
        AND stream_generation=p_stream AND stream_position=p_position AND payload_hash=p_hash) THEN
        RAISE EXCEPTION 'slack_replay_conflict' USING ERRCODE='23505'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.apply_slack_authority_denial(uuid,uuid,bigint,uuid,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_slack_authority_denial(uuid,uuid,bigint,uuid,bigint,text,text)
TO signal_authority_dispatcher;

CREATE FUNCTION control.candidate_review_risk(p_revision uuid) RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT GREATEST(
        CASE release.recipe_key
            WHEN 'technical_title' THEN 2 WHEN 'technical_description' THEN 2
            WHEN 'technical_alt' THEN 2 WHEN 'technical_structured_data' THEN 2
            WHEN 'technical_broken_link' THEN 2 WHEN 'technical_canonical' THEN 4 ELSE 6 END,
        CASE convert_from(release.canonical_body,'UTF8')::jsonb->>'approval_class'
            WHEN 'A0' THEN 0 WHEN 'A1' THEN 1 WHEN 'A2' THEN 2 WHEN 'A3' THEN 3
            WHEN 'A4' THEN 4 WHEN 'A5' THEN 5 WHEN 'owner_review' THEN 0 ELSE 6 END
    ) FROM app.candidate_recipe_revisions r JOIN control.recipe_releases release
      ON release.id=r.recipe_release_id AND release.content_hash=r.release_content_hash
    WHERE r.id=p_revision;
$$;
REVOKE ALL ON FUNCTION control.candidate_review_risk(uuid) FROM PUBLIC;

CREATE FUNCTION control.slack_revision_current(p_revision uuid,p_hash bytea) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS(SELECT 1 FROM app.candidate_recipe_revisions r
      JOIN app.github_pr_extensions e ON e.tenant_id=r.tenant_id AND e.site_id=r.site_id AND e.id=r.extension_id
      JOIN app.github_read_bindings b ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id
      WHERE r.id=p_revision AND r.revision_sha256=p_hash AND e.base_sha=r.base_sha
        AND e.status='observed' AND b.status='active'
        AND NOT EXISTS(SELECT 1 FROM app.candidate_recipe_revisions newer
          WHERE newer.tenant_id=r.tenant_id AND newer.site_id=r.site_id AND newer.audit_report_id=r.audit_report_id
            AND newer.finding_id=r.finding_id AND (newer.sealed_at,newer.id)>(r.sealed_at,r.id))
        AND (SELECT event.status FROM control.recipe_release_events event WHERE event.release_id=r.recipe_release_id
          ORDER BY event.sequence_number DESC LIMIT 1)='REVIEWED'
        AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='recipe_release'
          AND d.target_id=r.recipe_release_id));
$$;
REVOKE ALL ON FUNCTION control.slack_revision_current(uuid,bytea) FROM PUBLIC;

-- Both dashboard and chat use the same exact-revision decision implementation.
ALTER TABLE app.candidate_recipe_review_decisions
    DROP CONSTRAINT candidate_recipe_review_decisions_decision_channel_check;
ALTER TABLE app.candidate_recipe_review_decisions
    ADD CONSTRAINT candidate_recipe_review_decisions_decision_channel_check
    CHECK (decision_channel IN ('dashboard', 'slack'));

CREATE FUNCTION control.decide_candidate_recipe_revision_for_channel(
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
    IF p_channel NOT IN ('dashboard', 'slack') OR p_revision_id IS NULL OR p_decision_id IS NULL OR p_revision_id = p_decision_id
       OR p_expected_revision_sha256 IS NULL OR octet_length(p_expected_revision_sha256) <> 32
       OR p_decision NOT IN ('approved', 'rejected', 'changes_requested') THEN
        RAISE EXCEPTION 'invalid_candidate_recipe_decision_input' USING ERRCODE = '22023';
    END IF;
    IF p_channel='slack' THEN
        SELECT * INTO v_authority FROM control.resolve_slack_link_authority(p_link_id,p_generation);
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
                WHERE d.target_kind='slack_binding' AND d.target_id=b.id)))
        AND (v_authority.authentication_level<>'mfa' OR NOT EXISTS(
            SELECT 1 FROM app.sessions s WHERE s.session_token_hash=p_session_hash
              AND s.auth_time>v_now-interval '5 minutes')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'approval_permission_denied';
        RETURN;
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
            CASE WHEN p_channel = 'slack' THEN 'primary' ELSE v_authority.authentication_level END,
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

CREATE OR REPLACE FUNCTION control.decide_authenticated_candidate_recipe_revision(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_revision_id uuid, p_expected_revision_sha256 bytea,
    p_decision_id uuid, p_decision text
) RETURNS TABLE (
    revision_id uuid, revision_sha256 text, canonical_manifest jsonb,
    sealed_at timestamptz, recipe_release_id uuid, release_content_hash text,
    base_sha text, patch_sha256 text, review_status text, decision_id uuid,
    decision text, decided_by_user_id uuid, decision_channel text,
    decided_at timestamptz, reused boolean, outcome text
)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT * FROM control.decide_candidate_recipe_revision_for_channel(
        p_session_hash, p_site_id, p_generation, p_revision_id,
        p_expected_revision_sha256, p_decision_id, p_decision, 'dashboard', NULL);
$$;
REVOKE ALL ON FUNCTION control.decide_candidate_recipe_revision_for_channel(
    bytea, uuid, text, uuid, bytea, uuid, text, text, uuid) FROM PUBLIC;

CREATE FUNCTION control.resolve_slack_link_authority(p_link uuid,p_generation text)
RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,role_key text,
    authentication_level text,membership_epoch bigint,site_authorization_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE l app.slack_links%%ROWTYPE; a record; b app.slack_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO l FROM app.slack_links WHERE id=p_link FOR SHARE;
    IF NOT FOUND OR l.revoked_at IS NOT NULL OR l.recovery_generation IS DISTINCT FROM p_generation
       OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='slack_link'
           AND target_id=p_link) THEN
        RETURN QUERY SELECT 'authorization_denied'::text,NULL::uuid,NULL::uuid,NULL::text,
            NULL::text,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    SELECT * INTO b FROM control.slack_scope(l.binding_id);
    IF b.id IS NULL OR b.revoked_at IS NOT NULL OR b.recovery_generation IS DISTINCT FROM p_generation
       OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='slack_binding'
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
REVOKE ALL ON FUNCTION control.resolve_slack_link_authority(uuid,text) FROM PUBLIC;

CREATE FUNCTION control.begin_slack_link(
    p_session bytea,p_site uuid,p_generation text,p_binding uuid,p_id uuid,
    p_user text,p_hash bytea,p_payload jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.slack_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' THEN RETURN 'denied'; END IF;
    SELECT * INTO b FROM control.slack_scope(p_binding);
    IF b.id IS NULL OR b.tenant_id<>a.tenant_id OR b.site_id<>p_site OR b.revoked_at IS NOT NULL
       OR b.recovery_generation<>p_generation OR p_user !~ '^[UW][A-Z0-9]{7,63}$'
       OR octet_length(p_hash) IS DISTINCT FROM 32 OR p_payload->>'channel' IS DISTINCT FROM p_user
       OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='slack_binding'
          AND target_id=p_binding) THEN RETURN 'denied'; END IF;
    INSERT INTO app.slack_link_codes(tenant_id,site_id,id,binding_id,user_id,slack_user_id,
        membership_epoch,recovery_generation,code_sha256)
    VALUES(a.tenant_id,p_site,p_id,p_binding,a.user_id,p_user,a.membership_epoch,p_generation,p_hash);
    INSERT INTO app.slack_outbox(tenant_id,site_id,id,binding_id,intended_user_id,kind,link_code_id,
        payload,expires_at)
    VALUES(a.tenant_id,p_site,p_id,p_binding,p_user,'link',p_id,p_payload,
        transaction_timestamp()+interval '5 minutes');
    RETURN 'queued';
END $$;

CREATE FUNCTION control.read_slack_binding(p_session bytea,p_site uuid,p_generation text)
RETURNS TABLE(binding_id uuid,workspace_id text,channel_id text,max_risk integer,link_id uuid,
    slack_user_id text,availability text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,NULL::text,NULL::integer,NULL::uuid,
            NULL::text,'denied'::text; RETURN;
    END IF;
    RETURN QUERY SELECT b.id,b.workspace_id,b.channel_id,b.max_risk,l.id,l.slack_user_id,'bound'::text
        FROM app.slack_bindings b LEFT JOIN app.slack_links l ON l.binding_id=b.id AND l.user_id=a.user_id
          AND l.revoked_at IS NULL AND l.membership_epoch=a.membership_epoch
          AND l.recovery_generation=p_generation AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d
            WHERE d.target_kind='slack_link' AND d.target_id=l.id)
        WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.revoked_at IS NULL
          AND b.recovery_generation=p_generation AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d
            WHERE d.target_kind='slack_binding' AND d.target_id=b.id);
END $$;
REVOKE ALL ON FUNCTION control.read_slack_binding(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_slack_binding(bytea,uuid,text) TO signal_identity;

CREATE FUNCTION control.enqueue_slack_approval(
    p_session bytea,p_site uuid,p_generation text,p_binding uuid,p_id uuid,
    p_revision uuid,p_hash bytea,p_approve bytea,p_reject bytea,p_payload jsonb
) RETURNS TABLE(outbox_id uuid,outcome text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.slack_bindings%%ROWTYPE; l app.slack_links%%ROWTYPE; r record; existing uuid;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN
        RETURN QUERY SELECT NULL::uuid,'denied'::text; RETURN; END IF;
    SELECT * INTO b FROM control.slack_scope(p_binding);
    IF b.id IS NULL OR b.tenant_id<>a.tenant_id OR b.site_id<>p_site OR b.revoked_at IS NOT NULL
       OR b.recovery_generation<>p_generation THEN
        RETURN QUERY SELECT NULL::uuid,'denied'::text; RETURN; END IF;
    SELECT * INTO l FROM app.slack_links WHERE binding_id=p_binding AND user_id=a.user_id
        AND revoked_at IS NULL AND recovery_generation=p_generation;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid,'unlinked'::text; RETURN; END IF;
    SELECT * INTO r FROM control.resolve_slack_link_authority(l.id,p_generation);
    IF r.outcome<>'authorized' THEN RETURN QUERY SELECT NULL::uuid,'denied'::text; RETURN; END IF;
    SELECT * INTO r FROM control.read_authenticated_candidate_recipe_inbox(p_session,p_site,p_generation)
        WHERE revision_id=p_revision AND revision_sha256=encode(p_hash,'hex') AND review_status='pending';
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid,'stale_revision'::text; RETURN; END IF;
    IF p_payload->>'channel' NOT IN (b.channel_id,l.slack_user_id) THEN
        RETURN QUERY SELECT NULL::uuid,'denied'::text; RETURN; END IF;
    INSERT INTO app.slack_outbox(tenant_id,site_id,id,binding_id,intended_user_id,kind,
        revision_id,revision_sha256,approve_sha256,reject_sha256,payload)
    VALUES(a.tenant_id,p_site,p_id,p_binding,l.slack_user_id,'approval',p_revision,p_hash,
        p_approve,p_reject,p_payload) ON CONFLICT DO NOTHING RETURNING id INTO existing;
    IF existing IS NULL THEN SELECT id INTO existing FROM app.slack_outbox
        WHERE binding_id=p_binding AND intended_user_id=l.slack_user_id AND revision_id=p_revision;
    END IF;
    RETURN QUERY SELECT existing,'queued'::text;
END $$;

CREATE FUNCTION control.slack_outbox_item(p_binding uuid,p_id uuid,p_generation text,p_claim boolean)
RETURNS TABLE(payload jsonb,secret_reference text,state text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.slack_bindings%%ROWTYPE; o app.slack_outbox%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.slack_scope(p_binding);
    IF b.id IS NULL OR b.revoked_at IS NOT NULL OR b.recovery_generation IS DISTINCT FROM p_generation
       OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='slack_binding'
         AND target_id=p_binding) THEN RETURN; END IF;
    SELECT * INTO o FROM app.slack_outbox WHERE binding_id=p_binding AND id=p_id FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    IF o.state='queued' AND o.expires_at<=transaction_timestamp() THEN RETURN; END IF;
    IF o.state='queued' AND o.kind='approval' AND (NOT control.slack_revision_current(o.revision_id,o.revision_sha256)
        OR NOT EXISTS(SELECT 1 FROM app.slack_links l CROSS JOIN LATERAL
            control.resolve_slack_link_authority(l.id,p_generation) a
            WHERE l.binding_id=p_binding AND l.slack_user_id=o.intended_user_id AND a.outcome='authorized'
              AND a.role_key='owner')) THEN RETURN; END IF;
    IF p_claim AND o.state='queued' THEN
        UPDATE app.slack_outbox SET state='dispatching' WHERE id=p_id AND binding_id=p_binding;
        RETURN QUERY SELECT o.payload,b.secret_reference,'claimed'::text; RETURN;
    END IF;
    RETURN QUERY SELECT o.payload,b.secret_reference,o.state;
END $$;
CREATE FUNCTION control.defer_slack_outbox(p_binding uuid,p_id uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.slack_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.slack_scope(p_binding);
    IF b.id IS NULL THEN RETURN 'unavailable'; END IF;
    IF EXISTS(SELECT 1 FROM app.egress_operations WHERE id=p_id) THEN RETURN 'unknown'; END IF;
    UPDATE app.slack_outbox SET state='queued' WHERE binding_id=p_binding AND id=p_id AND state='dispatching';
    RETURN 'queued';
END $$;
REVOKE ALL ON FUNCTION control.defer_slack_outbox(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.defer_slack_outbox(uuid,uuid) TO signal_identity;
CREATE FUNCTION control.finish_slack_outbox(p_binding uuid,p_id uuid,p_channel text,p_ts text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.slack_bindings%%ROWTYPE; o app.slack_outbox%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.slack_scope(p_binding);
    SELECT * INTO o FROM app.slack_outbox WHERE binding_id=p_binding AND id=p_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable'; END IF;
    IF o.state<>'dispatching' THEN RETURN o.state; END IF;
    IF p_channel IS NULL AND p_ts IS NULL THEN
        UPDATE app.slack_outbox SET state='unknown' WHERE id=p_id AND binding_id=p_binding;
        RETURN 'unknown';
    END IF;
    IF p_channel !~ '^[CDG][A-Z0-9]{7,63}$' OR p_ts !~ '^[0-9]{1,16}\.[0-9]{1,8}$'
       OR (o.payload->>'channel'=b.channel_id AND p_channel<>b.channel_id)
       OR (o.payload->>'channel'<>b.channel_id AND p_channel NOT LIKE 'D%%') THEN RETURN 'unknown'; END IF;
    UPDATE app.slack_outbox SET state='accepted',channel_id=p_channel,message_ts=p_ts
        WHERE id=p_id AND binding_id=p_binding;
    RETURN 'accepted';
END $$;
CREATE FUNCTION control.audit_slack_rejection(p_binding uuid,p_body bytea,p_reason text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.slack_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO b FROM control.slack_scope(p_binding);
    IF b.id IS NULL OR p_reason NOT IN ('signature_rejected','payload_rejected') THEN RETURN; END IF;
    INSERT INTO app.slack_ingress_audit(tenant_id,site_id,id,binding_id,body_sha256,outcome)
    VALUES(b.tenant_id,b.site_id,gen_random_uuid(),b.id,p_body,p_reason);
END $$;

CREATE FUNCTION control.handle_slack_action(
    p_binding uuid,p_generation text,p_signature bytea,p_body bytea,p_workspace text,
    p_channel text,p_user text,p_callback bytea,p_action text,p_ts text,p_decision uuid
) RETURNS TABLE(outcome text,decision_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app.slack_bindings%%ROWTYPE; o app.slack_outbox%%ROWTYPE; l app.slack_links%%ROWTYPE;
    c app.slack_link_codes%%ROWTYPE; a record; result record; v_risk integer;
    v_outcome text:='wrong_binding'; v_decision uuid;
BEGIN
    SELECT * INTO b FROM control.slack_scope(p_binding);
    IF b.id IS NULL THEN RETURN QUERY SELECT 'wrong_binding'::text,NULL::uuid; RETURN; END IF;
    IF octet_length(p_signature) IS DISTINCT FROM 32 OR octet_length(p_body) IS DISTINCT FROM 32 THEN
        RAISE EXCEPTION 'invalid_slack_signature_evidence' USING ERRCODE='22023'; END IF;
    -- Serialize callbacks with the same authenticated signature, including concurrent deliveries.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_binding::text||encode(p_signature,'hex'),0));
    IF EXISTS(SELECT 1 FROM app.slack_ingress_audit WHERE binding_id=p_binding AND signature_sha256=p_signature) THEN
        INSERT INTO app.slack_ingress_audit(tenant_id,site_id,id,binding_id,body_sha256,signature_sha256,outcome)
        VALUES(b.tenant_id,b.site_id,gen_random_uuid(),b.id,p_body,p_signature,'replay');
        RETURN QUERY SELECT 'replay'::text,NULL::uuid; RETURN;
    END IF;
    IF b.workspace_id=p_workspace AND b.revoked_at IS NULL AND b.recovery_generation=p_generation
       AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='slack_binding'
          AND target_id=p_binding) THEN
        SELECT * INTO o FROM app.slack_outbox WHERE binding_id=p_binding AND state='accepted'
            AND channel_id=p_channel AND message_ts=p_ts AND intended_user_id=p_user
            AND expires_at>transaction_timestamp()
            AND ((p_action='signal_approve' AND approve_sha256=p_callback)
                OR (p_action='signal_reject' AND reject_sha256=p_callback)
                OR (p_action='signal_link' AND kind='link' AND EXISTS(
                    SELECT 1 FROM app.slack_link_codes code WHERE code.id=link_code_id
                      AND code.binding_id=p_binding AND code.code_sha256=p_callback))) FOR UPDATE;
        IF NOT FOUND THEN
            IF p_action<>'signal_link' AND NOT EXISTS(SELECT 1 FROM app.slack_links
                WHERE binding_id=p_binding AND slack_user_id=p_user AND revoked_at IS NULL) THEN
                v_outcome:='unlinked';
            ELSIF p_channel<>b.channel_id AND NOT EXISTS(SELECT 1 FROM app.slack_outbox
                WHERE binding_id=p_binding AND channel_id=p_channel AND intended_user_id=p_user) THEN
                v_outcome:='wrong_binding';
            ELSE v_outcome:='stale_revision'; END IF;
        ELSIF p_action='signal_link' THEN
            SELECT * INTO c FROM app.slack_link_codes WHERE binding_id=p_binding AND id=o.link_code_id
                AND code_sha256=p_callback AND slack_user_id=p_user AND consumed_at IS NULL
                AND expires_at>transaction_timestamp() FOR UPDATE;
            IF NOT FOUND THEN v_outcome:='unlinked';
            ELSE
                SELECT * INTO a FROM control.resolve_member_site_authority(c.tenant_id,c.user_id,c.site_id,'primary');
                IF a.outcome<>'authorized' OR a.membership_epoch<>c.membership_epoch THEN v_outcome:='authority_denied';
                ELSE
                    INSERT INTO app.slack_links(tenant_id,site_id,id,binding_id,user_id,slack_user_id,
                        membership_epoch,recovery_generation)
                    VALUES(c.tenant_id,c.site_id,gen_random_uuid(),p_binding,c.user_id,p_user,
                        c.membership_epoch,c.recovery_generation) ON CONFLICT DO NOTHING;
                    IF NOT FOUND THEN v_outcome:='unlinked'; ELSE
                        UPDATE app.slack_link_codes SET consumed_at=transaction_timestamp()
                        WHERE tenant_id=c.tenant_id AND site_id=c.site_id AND id=c.id;
                        v_outcome:='linked';
                    END IF;
                END IF;
            END IF;
        ELSE
            SELECT * INTO l FROM app.slack_links WHERE binding_id=p_binding AND slack_user_id=p_user
                AND revoked_at IS NULL;
            IF NOT FOUND THEN v_outcome:='unlinked'; ELSE
                SELECT * INTO a FROM control.resolve_slack_link_authority(l.id,p_generation);
                IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN v_outcome:='authority_denied'; ELSE
                    v_risk:=control.candidate_review_risk(o.revision_id);
                    IF NOT control.slack_revision_current(o.revision_id,o.revision_sha256) THEN
                        v_outcome:='stale_revision';
                    ELSIF v_risk IS NULL OR v_risk>=3 OR v_risk>b.max_risk THEN v_outcome:='step_up'; ELSE
                        SELECT * INTO result FROM control.decide_candidate_recipe_revision_for_channel(
                            NULL,b.site_id,p_generation,o.revision_id,o.revision_sha256,p_decision,
                            CASE WHEN p_action='signal_approve' THEN 'approved' ELSE 'rejected' END,'slack',l.id);
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
    INSERT INTO app.slack_ingress_audit(tenant_id,site_id,id,binding_id,body_sha256,signature_sha256,outcome)
    VALUES(b.tenant_id,b.site_id,gen_random_uuid(),b.id,p_body,p_signature,v_outcome);
    RETURN QUERY SELECT v_outcome,v_decision;
END $$;

REVOKE ALL ON FUNCTION control.begin_slack_oauth(bytea,uuid,text,uuid,bytea,text,text,text,integer),
    control.consume_slack_oauth(bytea,uuid,text,uuid,bytea,text),
    control.confirm_slack_oauth(bytea,uuid,text,uuid,uuid,text),
    control.begin_slack_link(bytea,uuid,text,uuid,uuid,text,bytea,jsonb),
    control.enqueue_slack_approval(bytea,uuid,text,uuid,uuid,uuid,bytea,bytea,bytea,jsonb),
    control.slack_outbox_item(uuid,uuid,text,boolean),control.finish_slack_outbox(uuid,uuid,text,text),
    control.audit_slack_rejection(uuid,bytea,text),
    control.handle_slack_action(uuid,text,bytea,bytea,text,text,text,bytea,text,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.begin_slack_oauth(bytea,uuid,text,uuid,bytea,text,text,text,integer),
    control.consume_slack_oauth(bytea,uuid,text,uuid,bytea,text),
    control.confirm_slack_oauth(bytea,uuid,text,uuid,uuid,text),
    control.begin_slack_link(bytea,uuid,text,uuid,uuid,text,bytea,jsonb),
    control.enqueue_slack_approval(bytea,uuid,text,uuid,uuid,uuid,bytea,bytea,bytea,jsonb),
    control.slack_outbox_item(uuid,uuid,text,boolean),control.finish_slack_outbox(uuid,uuid,text,text),
    control.audit_slack_rejection(uuid,bytea,text),
    control.handle_slack_action(uuid,text,bytea,bytea,text,text,text,bytea,text,text,uuid) TO signal_identity;
