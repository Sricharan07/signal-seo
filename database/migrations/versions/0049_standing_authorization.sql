CREATE TABLE app.standing_authorizations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    membership_epoch bigint NOT NULL CHECK (membership_epoch > 0),
    site_epoch bigint NOT NULL CHECK (site_epoch > 0),
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    recipe_release_ids uuid[] NOT NULL CHECK (cardinality(recipe_release_ids) BETWEEN 1 AND 64),
    work_types text[] NOT NULL CHECK (cardinality(work_types) BETWEEN 1 AND 16),
    thresholds jsonb NOT NULL CHECK (jsonb_typeof(thresholds) = 'object'),
    weekly_volume_caps jsonb NOT NULL CHECK (jsonb_typeof(weekly_volume_caps) = 'object'),
    weekly_total_cap integer NOT NULL CHECK (weekly_total_cap BETWEEN 1 AND 1000),
    weekly_spend_cents bigint NOT NULL CHECK (weekly_spend_cents BETWEEN 0 AND 100000000),
    excluded_paths text[] NOT NULL DEFAULT '{}',
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    recovery_window_hours integer NOT NULL CHECK (recovery_window_hours BETWEEN 1 AND 720),
    granted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, owner_user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK (ends_at > starts_at AND ends_at <= starts_at + interval '366 days')
);
CREATE INDEX standing_authorizations_site ON app.standing_authorizations
    (tenant_id, site_id, ends_at DESC);
CREATE TRIGGER standing_authorizations_immutable BEFORE UPDATE OR DELETE
ON app.standing_authorizations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.standing_authorizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.standing_authorizations FORCE ROW LEVEL SECURITY;
CREATE POLICY standing_authorizations_scope ON app.standing_authorizations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE TABLE app.standing_authorization_revocations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    grant_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    revoked_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, grant_id),
    FOREIGN KEY (tenant_id, site_id, grant_id)
        REFERENCES app.standing_authorizations (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE TRIGGER standing_revocations_immutable BEFORE UPDATE OR DELETE
ON app.standing_authorization_revocations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.standing_authorization_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.standing_authorization_revocations FORCE ROW LEVEL SECURITY;
CREATE POLICY standing_revocations_scope ON app.standing_authorization_revocations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

-- One site/week counter survives grant replacement and serializes every reservation.
CREATE TABLE app.autonomy_weekly_usage (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    week_start date NOT NULL,
    total_count integer NOT NULL DEFAULT 0 CHECK (total_count >= 0),
    per_type_counts jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(per_type_counts) = 'object'),
    spend_cents bigint NOT NULL DEFAULT 0 CHECK (spend_cents >= 0),
    PRIMARY KEY (tenant_id, site_id, week_start),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id)
);
ALTER TABLE app.autonomy_weekly_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomy_weekly_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY autonomy_usage_scope ON app.autonomy_weekly_usage
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE TABLE app.autonomy_reservations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    recipe_release_id uuid NOT NULL,
    sealed_revision_sha256 bytea NOT NULL CHECK (octet_length(sealed_revision_sha256) = 32),
    work_type text NOT NULL,
    resource_path text NOT NULL,
    cost_cents bigint NOT NULL CHECK (cost_cents >= 0),
    week_start date NOT NULL,
    reserved_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, operation_id),
    FOREIGN KEY (tenant_id, site_id, grant_id)
        REFERENCES app.standing_authorizations (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, week_start)
        REFERENCES app.autonomy_weekly_usage (tenant_id, site_id, week_start)
);
CREATE TRIGGER autonomy_reservations_immutable BEFORE UPDATE OR DELETE
ON app.autonomy_reservations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.autonomy_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomy_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY autonomy_reservations_scope ON app.autonomy_reservations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

REVOKE ALL ON app.standing_authorizations, app.standing_authorization_revocations,
    app.autonomy_weekly_usage, app.autonomy_reservations
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.valid_standing_work_type(p_type text) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$ SELECT p_type IN ('research_audit','draft_patch','metadata_pr',
    'content_refresh_pr','new_article_pr') $$;
REVOKE ALL ON FUNCTION control.valid_standing_work_type(text) FROM PUBLIC;

CREATE FUNCTION control.grant_standing_authorization(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_grant_id uuid,
    p_release_ids uuid[], p_work_types text[], p_thresholds jsonb,
    p_volume_caps jsonb, p_total_cap integer, p_spend_cents bigint,
    p_excluded_paths text[], p_starts_at timestamptz, p_ends_at timestamptz,
    p_recovery_window_hours integer
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_type text; v_path text; v_release uuid;
BEGIN
    IF session_user != 'signal_api' OR p_grant_id IS NULL OR p_site_id IS NULL
       OR cardinality(p_release_ids) NOT BETWEEN 1 AND 64
       OR cardinality(p_work_types) NOT BETWEEN 1 AND 16
       OR cardinality(p_excluded_paths) > 64
       OR p_total_cap NOT BETWEEN 1 AND 1000
       OR p_spend_cents NOT BETWEEN 0 AND 100000000
       OR p_recovery_window_hours NOT BETWEEN 1 AND 720
       OR p_starts_at IS NULL OR p_ends_at IS NULL
       OR p_starts_at < transaction_timestamp() - interval '5 minutes'
       OR p_ends_at <= p_starts_at OR p_ends_at > p_starts_at + interval '366 days'
       OR jsonb_typeof(p_thresholds) != 'object'
       OR jsonb_typeof(p_volume_caps) != 'object'
       OR (SELECT count(DISTINCT id) FROM unnest(p_release_ids) id) != cardinality(p_release_ids)
       OR (SELECT count(DISTINCT kind) FROM unnest(p_work_types) kind) != cardinality(p_work_types)
    THEN RETURN 'invalid_grant'; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_thresholds)) != cardinality(p_work_types)
       OR (SELECT count(*) FROM jsonb_object_keys(p_volume_caps)) != cardinality(p_work_types)
    THEN RETURN 'invalid_grant'; END IF;
    FOREACH v_type IN ARRAY p_work_types LOOP
        IF NOT control.valid_standing_work_type(v_type)
           OR jsonb_typeof(p_thresholds -> v_type) != 'number'
           OR (p_thresholds ->> v_type)::numeric NOT BETWEEN 0 AND 1
           OR jsonb_typeof(p_volume_caps -> v_type) != 'number'
           OR (p_volume_caps ->> v_type) !~ '^[1-9][0-9]{0,3}$'
           OR (p_volume_caps ->> v_type)::integer > p_total_cap
        THEN RETURN 'invalid_grant'; END IF;
    END LOOP;
    FOREACH v_path IN ARRAY p_excluded_paths LOOP
        IF v_path IS NULL OR length(v_path) NOT BETWEEN 1 AND 1024
           OR v_path !~ '^/[A-Za-z0-9_./-]*$' OR v_path LIKE '%%..%%'
           OR v_path LIKE '%%//%%' OR position(chr(92) in v_path) > 0
        THEN RETURN 'invalid_grant'; END IF;
    END LOOP;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome != 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key != 'owner' THEN RETURN 'permission_denied'; END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id = v_authority.tenant_id
        AND id = p_site_id AND state = 'active' AND ownership_status = 'verified' FOR UPDATE;
    IF NOT FOUND THEN RETURN 'site_unavailable'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        v_authority.tenant_id::text || ':' || p_site_id::text, 43));
    IF EXISTS (SELECT 1 FROM app.standing_authorizations g WHERE
        g.tenant_id = v_authority.tenant_id AND g.site_id = p_site_id
        AND g.ends_at > transaction_timestamp()
        AND g.recovery_generation = p_generation
        AND NOT EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
            WHERE r.tenant_id = g.tenant_id AND r.site_id = g.site_id AND r.grant_id = g.id)
        AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
            WHERE t.target_kind = 'standing_grant' AND t.target_id = g.id))
    THEN RETURN 'grant_exists'; END IF;
    FOREACH v_release IN ARRAY p_release_ids LOOP
        IF NOT EXISTS (
            SELECT 1 FROM control.recipe_releases r
            JOIN LATERAL (SELECT e.status FROM control.recipe_release_events e
                WHERE e.release_id = r.id ORDER BY e.sequence_number DESC LIMIT 1) current ON true
            WHERE r.id = v_release AND current.status = 'REVIEWED'
              AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind = 'recipe_release' AND t.target_id = r.id)
        ) THEN
            RETURN 'recipe_unavailable';
        END IF;
    END LOOP;
    FOREACH v_type IN ARRAY p_work_types LOOP
        IF NOT EXISTS (
            SELECT 1 FROM control.recipe_releases r
            WHERE r.id = ANY(p_release_ids)
              AND convert_from(r.canonical_body, 'UTF8')::jsonb ->> 'approval_class' =
                  CASE WHEN v_type = 'research_audit' THEN 'A0'
                       WHEN v_type = 'draft_patch' THEN 'A1' ELSE 'A2' END
              AND (v_type IN ('research_audit','draft_patch')
                   OR convert_from(r.canonical_body, 'UTF8')::jsonb
                      ->> 'delivery_mode' = 'pull_request')
              AND (v_type IN ('research_audit','draft_patch')
                   OR convert_from(r.canonical_body, 'UTF8')::jsonb
                      ->> 'standing_work_type' = v_type)
        ) THEN RETURN 'recipe_unavailable'; END IF;
    END LOOP;
    INSERT INTO app.standing_authorizations (
        tenant_id, site_id, id, owner_user_id, membership_epoch, site_epoch,
        recovery_generation, recipe_release_ids, work_types, thresholds,
        weekly_volume_caps, weekly_total_cap, weekly_spend_cents, excluded_paths,
        starts_at, ends_at, recovery_window_hours)
    VALUES (v_authority.tenant_id, p_site_id, p_grant_id, v_authority.user_id,
        v_authority.membership_epoch, v_authority.site_authorization_epoch,
        p_generation, p_release_ids, p_work_types, p_thresholds, p_volume_caps,
        p_total_cap, p_spend_cents, p_excluded_paths, p_starts_at, p_ends_at,
        p_recovery_window_hours);
    RETURN 'granted';
END $$;
REVOKE ALL ON FUNCTION control.grant_standing_authorization(
    bytea,uuid,text,uuid,uuid[],text[],jsonb,jsonb,integer,bigint,text[],
    timestamptz,timestamptz,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.grant_standing_authorization(
    bytea,uuid,text,uuid,uuid[],text[],jsonb,jsonb,integer,bigint,text[],
    timestamptz,timestamptz,integer) TO signal_api;

ALTER TABLE control.authority_restriction_outbox
    DROP CONSTRAINT authority_restriction_outbox_target_kind_check,
    DROP CONSTRAINT authority_restriction_outbox_restriction_kind_check;
ALTER TABLE control.authority_restriction_outbox
    ADD CONSTRAINT authority_restriction_outbox_target_kind_check
        CHECK (target_kind IN ('identity_session','recipe_release','standing_grant')),
    ADD CONSTRAINT authority_restriction_outbox_restriction_kind_check
        CHECK ((target_kind = 'identity_session' AND restriction_kind = 'session_revoked')
            OR (target_kind = 'recipe_release' AND restriction_kind = 'recipe_release_revoked')
            OR (target_kind = 'standing_grant' AND restriction_kind = 'standing_grant_revoked'));
ALTER TABLE control.authority_denial_tombstones
    DROP CONSTRAINT authority_denial_tombstones_target_kind_check,
    DROP CONSTRAINT authority_denial_tombstones_restriction_kind_check;
ALTER TABLE control.authority_denial_tombstones
    ADD CONSTRAINT authority_denial_tombstones_target_kind_check
        CHECK (target_kind IN ('identity_session','recipe_release','standing_grant')),
    ADD CONSTRAINT authority_denial_tombstones_restriction_kind_check
        CHECK ((target_kind = 'identity_session' AND restriction_kind = 'session_revoked')
            OR (target_kind = 'recipe_release' AND restriction_kind = 'recipe_release_revoked')
            OR (target_kind = 'standing_grant' AND restriction_kind = 'standing_grant_revoked'));

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
    ELSE
        RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END $$;
CREATE POLICY platform_standing_revocation_insert ON control.platform_events
FOR INSERT TO signal_migrator WITH CHECK (
    event_type = 'standing.grant.revoked'
    AND EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
                WHERE r.id = platform_events.id
                  AND r.grant_id = platform_events.object_id
                  AND r.actor_user_id = platform_events.actor_user_id)
);

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

CREATE FUNCTION control.journal_standing_revocation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO control.platform_events
        (id, event_type, actor_user_id, object_kind, object_id, facts, reason)
    VALUES (NEW.id, 'standing.grant.revoked', NEW.actor_user_id,
            'standing_grant', NEW.grant_id,
            jsonb_build_object('schema_version', 1,
                'restriction_kind', 'standing_grant_revoked', 'grant_id', NEW.grant_id),
            'owner_revocation');
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.journal_standing_revocation() FROM PUBLIC;
CREATE TRIGGER journal_standing_revocation
AFTER INSERT ON app.standing_authorization_revocations
FOR EACH ROW EXECUTE FUNCTION control.journal_standing_revocation();

CREATE FUNCTION control.block_tombstoned_standing_grant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM control.authority_denial_tombstones
               WHERE target_kind = 'standing_grant' AND target_id = NEW.id) THEN
        RAISE EXCEPTION 'standing grant denied by journal' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.block_tombstoned_standing_grant() FROM PUBLIC;
CREATE TRIGGER block_tombstoned_standing_grant BEFORE INSERT ON app.standing_authorizations
FOR EACH ROW EXECUTE FUNCTION control.block_tombstoned_standing_grant();

CREATE FUNCTION control.apply_standing_grant_denial(
    p_event_id uuid, p_target_id uuid, p_effective_epoch bigint,
    p_stream_generation uuid, p_stream_position bigint, p_payload_hash text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user != 'signal_authority_dispatcher' OR p_event_id IS NULL OR p_target_id IS NULL
       OR p_effective_epoch != 1 OR p_stream_generation IS NULL
       OR p_stream_position < 1 OR p_payload_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'standing grant replay denied' USING ERRCODE = '42501';
    END IF;
    INSERT INTO control.authority_denial_tombstones
        (event_id, target_kind, target_id, restriction_kind, effective_epoch,
         stream_generation, stream_position, payload_hash)
    VALUES (p_event_id, 'standing_grant', p_target_id, 'standing_grant_revoked',
            1, p_stream_generation, p_stream_position, p_payload_hash)
    ON CONFLICT (event_id) DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                   WHERE event_id = p_event_id AND target_kind = 'standing_grant'
                     AND target_id = p_target_id AND effective_epoch = 1
                     AND stream_generation = p_stream_generation
                     AND stream_position = p_stream_position
                     AND payload_hash = p_payload_hash) THEN
        RAISE EXCEPTION 'standing grant replay identity conflict' USING ERRCODE = '23505';
    END IF;
END $$;
REVOKE ALL ON FUNCTION control.apply_standing_grant_denial(
    uuid,uuid,bigint,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_standing_grant_denial(
    uuid,uuid,bigint,uuid,bigint,text) TO signal_authority_dispatcher;

CREATE FUNCTION control.revoke_standing_authorization(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_grant_id uuid, p_event_id uuid
) RETURNS TABLE (outcome text, restriction_event_id uuid, durability text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_grant app.standing_authorizations%%ROWTYPE; v_existing uuid;
BEGIN
    IF session_user != 'signal_api' OR p_grant_id IS NULL OR p_event_id IS NULL THEN
        RETURN QUERY SELECT 'invalid_request'::text, NULL::uuid, NULL::text; RETURN;
    END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome != 'authorized' THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::text; RETURN;
    END IF;
    IF v_authority.role_key != 'owner' THEN
        RETURN QUERY SELECT 'permission_denied'::text, NULL::uuid, NULL::text; RETURN;
    END IF;
    SELECT * INTO v_grant FROM app.standing_authorizations g
    WHERE g.tenant_id = v_authority.tenant_id AND g.site_id = p_site_id AND g.id = p_grant_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'grant_unavailable'::text, NULL::uuid, NULL::text; RETURN;
    END IF;
    SELECT r.id INTO v_existing FROM app.standing_authorization_revocations r
    WHERE r.tenant_id = v_grant.tenant_id AND r.site_id = v_grant.site_id
      AND r.grant_id = v_grant.id;
    IF v_existing IS NULL THEN
        INSERT INTO app.standing_authorization_revocations
            (tenant_id, site_id, id, grant_id, actor_user_id)
        VALUES (v_grant.tenant_id, v_grant.site_id, p_event_id, p_grant_id,
                v_authority.user_id);
        v_existing := p_event_id;
    END IF;
    RETURN QUERY SELECT 'revoked'::text, v_existing,
        control.authority_durability_status(v_existing);
END $$;
REVOKE ALL ON FUNCTION control.revoke_standing_authorization(
    bytea,uuid,text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.revoke_standing_authorization(
    bytea,uuid,text,uuid,uuid) TO signal_api;

CREATE FUNCTION control.standing_grant_eligibility(
    p_tenant_id uuid, p_site_id uuid, p_grant_id uuid, p_generation text,
    p_recipe_release_id uuid, p_work_type text, p_resource_path text
) RETURNS TABLE (eligible boolean, reason text, threshold numeric)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE g app.standing_authorizations%%ROWTYPE;
BEGIN
    IF session_user NOT IN ('signal_api','signal_workflow') OR p_tenant_id IS NULL
       OR p_site_id IS NULL OR p_grant_id IS NULL OR p_recipe_release_id IS NULL
       OR p_generation IS NULL OR p_work_type IS NULL
       OR NOT control.valid_standing_work_type(p_work_type)
       OR p_resource_path IS NULL
       OR length(p_resource_path) NOT BETWEEN 1 AND 1024
       OR p_resource_path !~ '^/[A-Za-z0-9_./-]*$'
       OR p_resource_path LIKE '%%..%%'
       OR p_resource_path LIKE '%%//%%' OR position(chr(92) in p_resource_path) > 0 THEN
        RETURN QUERY SELECT false, 'invalid_scope'::text, NULL::numeric; RETURN;
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO g FROM app.standing_authorizations a
    WHERE a.tenant_id = p_tenant_id AND a.site_id = p_site_id AND a.id = p_grant_id
    FOR SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'grant_unavailable'::text, NULL::numeric; RETURN;
    END IF;
    IF g.recovery_generation != p_generation
       OR transaction_timestamp() NOT BETWEEN g.starts_at AND g.ends_at
       OR NOT p_work_type = ANY(g.work_types)
       OR NOT p_recipe_release_id = ANY(g.recipe_release_ids)
       OR EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
                  WHERE r.tenant_id = p_tenant_id AND r.site_id = p_site_id
                    AND r.grant_id = p_grant_id)
       OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind = 'standing_grant' AND t.target_id = p_grant_id)
       OR EXISTS (SELECT 1 FROM unnest(g.excluded_paths) x
                  WHERE p_resource_path = x OR p_resource_path LIKE rtrim(x, '/') || '/%%')
       OR NOT EXISTS (SELECT 1 FROM app.memberships m JOIN app.site_memberships sm
                      ON sm.tenant_id = m.tenant_id AND sm.user_id = m.user_id
                      JOIN app.tenants t ON t.tenant_id = m.tenant_id
                      JOIN app.sites s ON s.tenant_id = sm.tenant_id AND s.id = sm.site_id
                      WHERE m.tenant_id = p_tenant_id AND m.user_id = g.owner_user_id
                        AND m.role_key = 'owner' AND m.state = 'active'
                        AND m.authorization_epoch = g.membership_epoch
                        AND sm.site_id = p_site_id AND sm.state = 'active'
                        AND sm.authorization_epoch = g.site_epoch
                        AND t.lifecycle = 'active' AND s.state = 'active'
                        AND s.ownership_status = 'verified')
       OR NOT EXISTS (
            SELECT 1 FROM control.recipe_releases r
            JOIN LATERAL (SELECT e.status FROM control.recipe_release_events e
                WHERE e.release_id = r.id ORDER BY e.sequence_number DESC LIMIT 1) current ON true
            WHERE r.id = p_recipe_release_id AND current.status = 'REVIEWED'
              AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind = 'recipe_release' AND t.target_id = r.id)
              AND convert_from(r.canonical_body, 'UTF8')::jsonb ->> 'approval_class' =
                  CASE WHEN p_work_type = 'research_audit' THEN 'A0'
                       WHEN p_work_type = 'draft_patch' THEN 'A1' ELSE 'A2' END
              AND (p_work_type IN ('research_audit','draft_patch')
                   OR convert_from(r.canonical_body, 'UTF8')::jsonb
                      ->> 'delivery_mode' = 'pull_request')
              AND (p_work_type IN ('research_audit','draft_patch')
                   OR convert_from(r.canonical_body, 'UTF8')::jsonb
                      ->> 'standing_work_type' = p_work_type))
    THEN RETURN QUERY SELECT false, 'authority_unavailable'::text, NULL::numeric; RETURN;
    END IF;
    RETURN QUERY SELECT true, 'eligible'::text,
        (g.thresholds ->> p_work_type)::numeric;
END $$;
REVOKE ALL ON FUNCTION control.standing_grant_eligibility(
    uuid,uuid,uuid,text,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.standing_grant_eligibility(
    uuid,uuid,uuid,text,uuid,text,text) TO signal_api, signal_workflow;

CREATE FUNCTION control.reserve_standing_budget(
    p_tenant_id uuid, p_site_id uuid, p_grant_id uuid, p_generation text,
    p_recipe_release_id uuid, p_work_type text, p_resource_path text,
    p_operation_id uuid, p_sealed_revision_sha256 bytea, p_cost_cents bigint
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE g app.standing_authorizations%%ROWTYPE; v_eligible record;
        v_week date; v_usage app.autonomy_weekly_usage%%ROWTYPE;
        v_existing app.autonomy_reservations%%ROWTYPE; v_count integer;
BEGIN
    IF session_user != 'signal_workflow' OR p_operation_id IS NULL
       OR octet_length(p_sealed_revision_sha256) != 32
       OR p_cost_cents NOT BETWEEN 0 AND 100000000 THEN RETURN 'invalid_reservation'; END IF;
    SELECT * INTO v_eligible FROM control.standing_grant_eligibility(
        p_tenant_id, p_site_id, p_grant_id, p_generation,
        p_recipe_release_id, p_work_type, p_resource_path);
    IF NOT v_eligible.eligible THEN RETURN v_eligible.reason; END IF;
    SELECT * INTO g FROM app.standing_authorizations WHERE tenant_id = p_tenant_id
        AND site_id = p_site_id AND id = p_grant_id FOR SHARE;
    SELECT * INTO v_existing FROM app.autonomy_reservations
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND operation_id = p_operation_id;
    IF FOUND THEN
        IF v_existing.grant_id = p_grant_id
           AND v_existing.recipe_release_id = p_recipe_release_id
           AND v_existing.sealed_revision_sha256 = p_sealed_revision_sha256
           AND v_existing.work_type = p_work_type
           AND v_existing.resource_path = p_resource_path
           AND v_existing.cost_cents = p_cost_cents
        THEN RETURN 'reserved'; END IF;
        RETURN 'reservation_conflict';
    END IF;
    v_week := date_trunc('week', transaction_timestamp() AT TIME ZONE 'UTC')::date;
    INSERT INTO app.autonomy_weekly_usage (tenant_id, site_id, week_start)
    VALUES (p_tenant_id, p_site_id, v_week) ON CONFLICT DO NOTHING;
    SELECT * INTO v_usage FROM app.autonomy_weekly_usage
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND week_start = v_week
    FOR UPDATE;
    v_count := COALESCE((v_usage.per_type_counts ->> p_work_type)::integer, 0);
    IF v_usage.total_count >= g.weekly_total_cap
       OR v_count >= (g.weekly_volume_caps ->> p_work_type)::integer
       OR v_usage.spend_cents + p_cost_cents > g.weekly_spend_cents
    THEN RETURN 'weekly_cap_reached'; END IF;
    UPDATE app.autonomy_weekly_usage
    SET total_count = total_count + 1,
        per_type_counts = jsonb_set(per_type_counts, ARRAY[p_work_type],
            to_jsonb(v_count + 1), true),
        spend_cents = spend_cents + p_cost_cents
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND week_start = v_week;
    INSERT INTO app.autonomy_reservations
        (tenant_id, site_id, operation_id, grant_id, recipe_release_id,
         sealed_revision_sha256, work_type, resource_path, cost_cents, week_start)
    VALUES (p_tenant_id, p_site_id, p_operation_id, p_grant_id,
        p_recipe_release_id, p_sealed_revision_sha256, p_work_type,
        p_resource_path, p_cost_cents, v_week);
    RETURN 'reserved';
END $$;
REVOKE ALL ON FUNCTION control.reserve_standing_budget(
    uuid,uuid,uuid,text,uuid,text,text,uuid,bytea,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.reserve_standing_budget(
    uuid,uuid,uuid,text,uuid,text,text,uuid,bytea,bigint) TO signal_workflow;

CREATE FUNCTION control.read_standing_authorization(
    p_session_hash bytea, p_site_id uuid, p_generation text
) RETURNS TABLE (outcome text, grant_id uuid, release_ids uuid[], work_types text[],
                 thresholds jsonb, volume_caps jsonb, total_cap integer,
                 spend_cents bigint, excluded_paths text[], starts_at timestamptz,
                 ends_at timestamptz, recovery_window_hours integer,
                 restriction_event_id uuid, durability text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; g app.standing_authorizations%%ROWTYPE; v_event uuid;
BEGIN
    IF session_user != 'signal_api' THEN
        RAISE EXCEPTION 'standing grant read denied' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome != 'authorized' THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::uuid[], NULL::text[],
            NULL::jsonb, NULL::jsonb, NULL::integer, NULL::bigint, NULL::text[],
            NULL::timestamptz, NULL::timestamptz, NULL::integer, NULL::uuid,
            NULL::text; RETURN;
    END IF;
    IF v_authority.role_key != 'owner' THEN
        RETURN QUERY SELECT 'permission_denied'::text, NULL::uuid, NULL::uuid[],
            NULL::text[], NULL::jsonb, NULL::jsonb, NULL::integer, NULL::bigint,
            NULL::text[], NULL::timestamptz, NULL::timestamptz, NULL::integer,
            NULL::uuid, NULL::text; RETURN;
    END IF;
    SELECT * INTO g FROM app.standing_authorizations a
    WHERE a.tenant_id = v_authority.tenant_id AND a.site_id = p_site_id
    ORDER BY a.granted_at DESC, a.id DESC LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'no_grant'::text, NULL::uuid, NULL::uuid[], NULL::text[],
            NULL::jsonb, NULL::jsonb, NULL::integer, NULL::bigint, NULL::text[],
            NULL::timestamptz, NULL::timestamptz, NULL::integer, NULL::uuid,
            NULL::text; RETURN;
    END IF;
    SELECT r.id INTO v_event FROM app.standing_authorization_revocations r
    WHERE r.tenant_id = g.tenant_id AND r.site_id = g.site_id AND r.grant_id = g.id;
    RETURN QUERY SELECT CASE WHEN v_event IS NOT NULL THEN 'revoked'::text
        WHEN g.recovery_generation != p_generation THEN 'recovery_stale'::text
        WHEN g.starts_at > transaction_timestamp() THEN 'not_started'::text
        WHEN g.ends_at <= transaction_timestamp() THEN 'expired'::text
        ELSE 'active'::text END,
        g.id, g.recipe_release_ids, g.work_types, g.thresholds,
        g.weekly_volume_caps, g.weekly_total_cap, g.weekly_spend_cents,
        g.excluded_paths, g.starts_at, g.ends_at, g.recovery_window_hours,
        v_event, CASE WHEN v_event IS NULL THEN NULL::text
                      ELSE control.authority_durability_status(v_event) END;
END $$;
REVOKE ALL ON FUNCTION control.read_standing_authorization(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_standing_authorization(bytea,uuid,text) TO signal_api;
