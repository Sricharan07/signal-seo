GRANT USAGE ON SCHEMA control TO signal_api;

CREATE TABLE control.recipe_signing_keys (
    key_id text PRIMARY KEY CHECK (key_id ~ '^[a-z][a-z0-9_.-]{0,63}$'),
    public_key bytea NOT NULL CHECK (octet_length(public_key) = 32),
    registered_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER recipe_signing_keys_immutable BEFORE UPDATE OR DELETE
ON control.recipe_signing_keys FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.recipe_signing_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.recipe_signing_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY recipe_signing_key_owner ON control.recipe_signing_keys
TO signal_migrator USING (true) WITH CHECK (true);

CREATE TABLE control.recipe_releases (
    id uuid PRIMARY KEY,
    recipe_key text NOT NULL CHECK (recipe_key ~ '^[a-z][a-z0-9_]{0,63}$'),
    version_major integer NOT NULL CHECK (version_major >= 0),
    version_minor integer NOT NULL CHECK (version_minor >= 0),
    version_patch integer NOT NULL CHECK (version_patch >= 0),
    contract_version integer NOT NULL CHECK (contract_version > 0),
    canonical_body bytea NOT NULL CHECK (octet_length(canonical_body) BETWEEN 1 AND 16384),
    content_hash bytea GENERATED ALWAYS AS (sha256(canonical_body)) STORED,
    signing_key_id text NOT NULL REFERENCES control.recipe_signing_keys (key_id),
    writer_signature bytea NOT NULL CHECK (octet_length(writer_signature) = 64),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recipe_key, version_major, version_minor, version_patch),
    UNIQUE (id, recipe_key)
);
CREATE INDEX recipe_releases_family
ON control.recipe_releases (recipe_key, version_major, version_minor, version_patch);
CREATE TRIGGER recipe_releases_immutable BEFORE UPDATE OR DELETE
ON control.recipe_releases FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.recipe_releases ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.recipe_releases FORCE ROW LEVEL SECURITY;
CREATE POLICY recipe_release_owner ON control.recipe_releases
TO signal_migrator USING (true) WITH CHECK (true);

CREATE TABLE control.recipe_release_events (
    id uuid PRIMARY KEY,
    release_id uuid NOT NULL REFERENCES control.recipe_releases (id),
    sequence_number bigint NOT NULL CHECK (sequence_number > 0),
    status text NOT NULL CHECK (status IN ('DRAFT','TESTED','REVIEWED','REVOKED')),
    actor_user_id uuid REFERENCES control.users (id),
    actor_reference text NOT NULL CHECK (length(actor_reference) BETWEEN 1 AND 128),
    source text NOT NULL CHECK (source IN ('migration','operator','journal_replay')),
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (release_id, sequence_number),
    CHECK (
        (source = 'operator' AND actor_user_id IS NOT NULL
         AND actor_reference = 'user:' || actor_user_id::text)
        OR (source = 'migration' AND actor_user_id IS NULL
            AND actor_reference = 'migration:0103')
        OR (source = 'journal_replay' AND actor_user_id IS NULL
            AND actor_reference = 'workload:authority_replay' AND status = 'REVOKED')
    )
);
CREATE INDEX recipe_release_events_history
ON control.recipe_release_events (release_id, sequence_number DESC);
CREATE TRIGGER recipe_release_events_immutable BEFORE UPDATE OR DELETE
ON control.recipe_release_events FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.recipe_release_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.recipe_release_events FORCE ROW LEVEL SECURITY;
CREATE POLICY recipe_release_event_owner ON control.recipe_release_events
TO signal_migrator USING (true) WITH CHECK (true);

CREATE FUNCTION control.register_recipe_signing_key(
    p_key_id text, p_public_key bytea
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF session_user != 'signal_release_manager' OR p_key_id IS NULL
       OR p_key_id !~ '^[a-z][a-z0-9_.-]{0,63}$'
       OR octet_length(p_public_key) != 32 THEN
        RAISE EXCEPTION 'recipe signing key registration denied' USING ERRCODE = '42501';
    END IF;
    INSERT INTO control.recipe_signing_keys (key_id, public_key)
    VALUES (p_key_id, p_public_key);
END
$$;
REVOKE ALL ON FUNCTION control.register_recipe_signing_key(text, bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.register_recipe_signing_key(text, bytea)
TO signal_release_manager;

CREATE FUNCTION control.recipe_signing_key_bytes(p_key_id text) RETURNS bytea
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $$ SELECT public_key FROM control.recipe_signing_keys WHERE key_id = p_key_id $$;
REVOKE ALL ON FUNCTION control.recipe_signing_key_bytes(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.recipe_signing_key_bytes(text) TO signal_release_manager;

CREATE FUNCTION control.register_recipe_release(
    p_id uuid, p_recipe_key text, p_major integer, p_minor integer, p_patch integer,
    p_contract_version integer, p_canonical_body bytea, p_signing_key_id text,
    p_signature bytea, p_event_id uuid, p_actor_user_id uuid
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_body jsonb;
BEGIN
    IF session_user != 'signal_release_manager' OR p_id IS NULL OR p_event_id IS NULL
       OR p_recipe_key IS NULL OR p_recipe_key !~ '^[a-z][a-z0-9_]{0,63}$'
       OR p_major < 0 OR p_minor < 0 OR p_patch < 0
       OR p_contract_version < 1 OR octet_length(p_canonical_body) NOT BETWEEN 1 AND 16384
       OR octet_length(p_signature) != 64 OR p_actor_user_id IS NULL
       OR NOT EXISTS (SELECT 1 FROM control.users u
                      WHERE u.id = p_actor_user_id AND u.disabled_at IS NULL) THEN
        RAISE EXCEPTION 'recipe release registration denied' USING ERRCODE = '42501';
    END IF;
    v_body := convert_from(p_canonical_body, 'UTF8')::jsonb;
    IF jsonb_typeof(v_body) != 'object' OR v_body -> 'schema_version' != '1'::jsonb
       OR v_body ->> 'kind' != 'recipe' OR v_body ->> 'release_id' != p_id::text
       OR v_body ->> 'recipe_key' != p_recipe_key
       OR v_body ->> 'version' !=
          (p_major::text || '.' || p_minor::text || '.' || p_patch::text)
       OR v_body ->> 'contract_version' != p_contract_version::text THEN
        RAISE EXCEPTION 'recipe release body identity mismatch' USING ERRCODE = '22023';
    END IF;
    INSERT INTO control.recipe_releases
        (id, recipe_key, version_major, version_minor, version_patch,
         contract_version, canonical_body, signing_key_id, writer_signature)
    VALUES (p_id, p_recipe_key, p_major, p_minor, p_patch,
            p_contract_version, p_canonical_body, p_signing_key_id, p_signature);
    INSERT INTO control.recipe_release_events
        (id, release_id, sequence_number, status, actor_user_id,
         actor_reference, source, reason)
    VALUES (p_event_id, p_id, 1, 'DRAFT', p_actor_user_id,
            'user:' || p_actor_user_id::text, 'operator', 'Initial immutable draft');
END
$$;
REVOKE ALL ON FUNCTION control.register_recipe_release(
    uuid,text,integer,integer,integer,integer,bytea,text,bytea,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.register_recipe_release(
    uuid,text,integer,integer,integer,integer,bytea,text,bytea,uuid,uuid)
TO signal_release_manager;

CREATE FUNCTION control.transition_recipe_release(
    p_release_id uuid, p_expected_status text, p_new_status text,
    p_event_id uuid, p_actor_user_id uuid, p_reason text
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_status text;
        v_sequence bigint;
BEGIN
    IF session_user != 'signal_release_manager' OR p_release_id IS NULL
       OR p_event_id IS NULL OR p_actor_user_id IS NULL OR p_reason IS NULL
       OR length(p_reason) NOT BETWEEN 1 AND 500
       OR NOT EXISTS (SELECT 1 FROM control.users u
                      WHERE u.id = p_actor_user_id AND u.disabled_at IS NULL) THEN
        RAISE EXCEPTION 'recipe release transition denied' USING ERRCODE = '42501';
    END IF;
    PERFORM 1 FROM control.recipe_releases WHERE id = p_release_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'recipe release is unavailable' USING ERRCODE = '23503';
    END IF;
    SELECT status, sequence_number INTO v_status, v_sequence
    FROM control.recipe_release_events WHERE release_id = p_release_id
    ORDER BY sequence_number DESC LIMIT 1;
    IF v_status IS DISTINCT FROM p_expected_status OR v_status = 'REVOKED'
       OR NOT (
           (v_status = 'DRAFT' AND p_new_status = 'TESTED')
           OR (v_status = 'TESTED' AND p_new_status = 'REVIEWED')
           OR (v_status = 'REVIEWED' AND p_new_status = 'REVOKED')
       ) THEN
        RAISE EXCEPTION 'recipe release transition conflict' USING ERRCODE = '23514';
    END IF;
    INSERT INTO control.recipe_release_events
        (id, release_id, sequence_number, status, actor_user_id,
         actor_reference, source, reason)
    VALUES (p_event_id, p_release_id, v_sequence + 1, p_new_status, p_actor_user_id,
            'user:' || p_actor_user_id::text, 'operator', p_reason);
    RETURN v_sequence + 1;
END
$$;
REVOKE ALL ON FUNCTION control.transition_recipe_release(uuid,text,text,uuid,uuid,text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.transition_recipe_release(uuid,text,text,uuid,uuid,text)
TO signal_release_manager;

CREATE FUNCTION control.recipe_release_candidates(p_recipe_key text)
RETURNS TABLE (
    release_id uuid, recipe_key text, version_major integer, version_minor integer,
    version_patch integer, contract_version integer, canonical_body bytea,
    content_hash bytea, signing_key_id text, writer_signature bytea,
    public_key bytea, current_status text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF session_user NOT IN ('signal_api','signal_release_manager','signal_workflow')
       OR p_recipe_key IS NULL OR p_recipe_key !~ '^[a-z][a-z0-9_]{0,63}$' THEN
        RAISE EXCEPTION 'recipe resolution denied' USING ERRCODE = '42501';
    END IF;
    IF (SELECT count(*) FROM control.recipe_releases r
        WHERE r.recipe_key = p_recipe_key) > 500 THEN
        RAISE EXCEPTION 'recipe family resolution limit' USING ERRCODE = '54000';
    END IF;
    PERFORM 1 FROM control.recipe_releases r
    WHERE r.recipe_key = p_recipe_key FOR SHARE;
    RETURN QUERY
    SELECT r.id, r.recipe_key, r.version_major, r.version_minor, r.version_patch,
           r.contract_version, r.canonical_body, r.content_hash, r.signing_key_id,
           r.writer_signature, k.public_key, e.status
    FROM control.recipe_releases r
    JOIN control.recipe_signing_keys k ON k.key_id = r.signing_key_id
    JOIN LATERAL (
        SELECT e0.status FROM control.recipe_release_events e0
        WHERE e0.release_id = r.id ORDER BY e0.sequence_number DESC LIMIT 1
    ) e ON true
    WHERE r.recipe_key = p_recipe_key
    ORDER BY r.version_major, r.version_minor, r.version_patch, r.id;
END
$$;
REVOKE ALL ON FUNCTION control.recipe_release_candidates(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.recipe_release_candidates(text)
TO signal_api, signal_release_manager, signal_workflow;

CREATE FUNCTION control.recipe_release_dispatch_eligible(p_release_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_status text;
BEGIN
    IF session_user NOT IN ('signal_api','signal_workflow','signal_release_manager')
       OR p_release_id IS NULL THEN
        RAISE EXCEPTION 'recipe dispatch status denied' USING ERRCODE = '42501';
    END IF;
    SELECT e.status INTO v_status FROM control.recipe_releases r
    JOIN LATERAL (
        SELECT status FROM control.recipe_release_events
        WHERE release_id = r.id ORDER BY sequence_number DESC LIMIT 1
    ) e ON true WHERE r.id = p_release_id;
    RETURN COALESCE(v_status = 'REVIEWED', false)
           AND EXISTS (SELECT 1 FROM control.recipe_releases r
                       WHERE r.id = p_release_id
                         AND convert_from(r.canonical_body, 'UTF8')::jsonb
                             ->> 'delivery_mode' = 'pull_request')
           AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                           WHERE target_kind = 'recipe_release' AND target_id = p_release_id);
END
$$;
REVOKE ALL ON FUNCTION control.recipe_release_dispatch_eligible(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.recipe_release_dispatch_eligible(uuid)
TO signal_api, signal_workflow, signal_release_manager;

-- The first release is a signed proposal-only contract for existing verified-homepage work.
INSERT INTO control.recipe_signing_keys (key_id, public_key)
VALUES ('verified_homepage_seed_v1',
        decode('2012bd8389c60ddd2c7f9b59f0b00d8590516f3b92962fc419d194c606a60fdd','hex'));
INSERT INTO control.recipe_releases
    (id, recipe_key, version_major, version_minor, version_patch, contract_version,
     canonical_body, signing_key_id, writer_signature)
VALUES (
    '9f2c4164-64a6-4e19-a2d0-3d9b16c8e701', 'title_description_improvement',
    1, 0, 0, 1,
    convert_to($seed${"allowed_fields":["meta_description"],"allowed_resource_types":["verified_homepage"],"approval_class":"A1","contract_version":1,"delivery_mode":"proposal_only","kind":"recipe","legacy_manifest_version":"verified-homepage-model-1.0.0","max_resources_per_revision":1,"purpose":"Draft one meta description for owner review from verified-homepage evidence.","recipe_key":"title_description_improvement","recovery_mode":"discard_draft","release_id":"9f2c4164-64a6-4e19-a2d0-3d9b16c8e701","required_evidence":["owner_verified_origin","homepage_metadata","metadata.meta_description.missing"],"schema_version":1,"steps":["validate_evidence","draft_metadata","validate_claims","request_owner_decision"],"verification_assertions":["exact_verified_homepage","no_external_write"],"version":"1.0.0"}$seed$, 'UTF8'),
    'verified_homepage_seed_v1',
    decode('2b50041862378ecee3ea38950c2198db36e7efe5f2e1498f70f4ebefcbe8ce358cbbea3f41c7d9ebd4b30813ad83466d5c0a13106d0f4f389e2f3ca588e82d0c','hex')
);
INSERT INTO control.recipe_release_events
    (id, release_id, sequence_number, status, actor_reference, source, reason)
VALUES
    ('2a61c2b6-e1d3-4cb7-81c3-e66299190101',
     '9f2c4164-64a6-4e19-a2d0-3d9b16c8e701', 1, 'DRAFT',
     'migration:0103', 'migration', 'Existing bounded proposal contract recorded'),
    ('2a61c2b6-e1d3-4cb7-81c3-e66299190102',
     '9f2c4164-64a6-4e19-a2d0-3d9b16c8e701', 2, 'TESTED',
     'migration:0103', 'migration', 'Existing verified-homepage proposal tests reviewed'),
    ('2a61c2b6-e1d3-4cb7-81c3-e66299190103',
     '9f2c4164-64a6-4e19-a2d0-3d9b16c8e701', 3, 'REVIEWED',
     'migration:0103', 'migration', 'Proposal-only; no external dispatch authority');

-- A release revocation is a platform restriction and uses the independent stream.
ALTER TABLE control.authority_restriction_outbox
    DROP CONSTRAINT authority_restriction_outbox_target_kind_check,
    DROP CONSTRAINT authority_restriction_outbox_restriction_kind_check;
ALTER TABLE control.authority_restriction_outbox
    ADD CONSTRAINT authority_restriction_outbox_target_kind_check
        CHECK (target_kind IN ('identity_session','recipe_release')),
    ADD CONSTRAINT authority_restriction_outbox_restriction_kind_check
        CHECK ((target_kind = 'identity_session' AND restriction_kind = 'session_revoked')
            OR (target_kind = 'recipe_release'
                AND restriction_kind = 'recipe_release_revoked'));
ALTER TABLE control.authority_denial_tombstones
    DROP CONSTRAINT authority_denial_tombstones_target_kind_check,
    DROP CONSTRAINT authority_denial_tombstones_restriction_kind_check;
ALTER TABLE control.authority_denial_tombstones
    ADD CONSTRAINT authority_denial_tombstones_target_kind_check
        CHECK (target_kind IN ('identity_session','recipe_release')),
    ADD CONSTRAINT authority_denial_tombstones_restriction_kind_check
        CHECK ((target_kind = 'identity_session' AND restriction_kind = 'session_revoked')
            OR (target_kind = 'recipe_release'
                AND restriction_kind = 'recipe_release_revoked'));

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
);

CREATE OR REPLACE FUNCTION control.validate_platform_event_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
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
    ELSE
        RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END
$$;
CREATE POLICY platform_recipe_revocation_insert ON control.platform_events
FOR INSERT TO signal_migrator WITH CHECK (
    event_type = 'recipe.release.revoked'
    AND EXISTS (SELECT 1 FROM control.recipe_release_events e
                WHERE e.id = platform_events.id
                  AND e.release_id = platform_events.object_id
                  AND e.actor_user_id = platform_events.actor_user_id
                  AND e.status = 'REVOKED' AND e.source = 'operator')
);

CREATE OR REPLACE FUNCTION control.enqueue_session_restriction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_epoch bigint;
        v_kind text;
        v_restriction text;
BEGIN
    IF NEW.event_type = 'identity.session.revoked' THEN
        SELECT session_version INTO v_epoch FROM control.identity_sessions
        WHERE id = NEW.object_id AND revoked_at IS NOT NULL;
        v_kind := 'identity_session';
        v_restriction := 'session_revoked';
    ELSIF NEW.event_type = 'recipe.release.revoked' THEN
        SELECT sequence_number INTO v_epoch FROM control.recipe_release_events
        WHERE id = NEW.id AND release_id = NEW.object_id AND status = 'REVOKED';
        v_kind := 'recipe_release';
        v_restriction := 'recipe_release_revoked';
    ELSE
        RETURN NEW;
    END IF;
    IF v_epoch IS NULL THEN
        RAISE EXCEPTION 'authority restriction target is invalid' USING ERRCODE = '23503';
    END IF;
    INSERT INTO control.authority_restriction_outbox
        (event_id, scope_kind, actor_user_id, target_kind, target_id,
         restriction_kind, effective_epoch, event_time, original_facts)
    VALUES (NEW.id, 'platform', NEW.actor_user_id, v_kind, NEW.object_id,
            v_restriction, v_epoch, NEW.created_at, NEW.facts);
    RETURN NEW;
END
$$;

CREATE FUNCTION control.journal_recipe_revocation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.status = 'REVOKED' AND NEW.source = 'operator' THEN
        INSERT INTO control.platform_events
            (id, event_type, actor_user_id, object_kind, object_id, facts, reason)
        VALUES (NEW.id, 'recipe.release.revoked', NEW.actor_user_id,
                'recipe_release', NEW.release_id,
                jsonb_build_object('schema_version', 1, 'status_event_id', NEW.id,
                                   'restriction_kind', 'recipe_release_revoked'),
                'operator_revocation');
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.journal_recipe_revocation() FROM PUBLIC;
CREATE TRIGGER journal_recipe_revocation AFTER INSERT ON control.recipe_release_events
FOR EACH ROW EXECUTE FUNCTION control.journal_recipe_revocation();

CREATE FUNCTION control.block_tombstoned_recipe_release() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM control.authority_denial_tombstones
               WHERE target_kind = 'recipe_release' AND target_id = NEW.id) THEN
        RAISE EXCEPTION 'recipe release denied by journal' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.block_tombstoned_recipe_release() FROM PUBLIC;
CREATE TRIGGER block_tombstoned_recipe_release BEFORE INSERT ON control.recipe_releases
FOR EACH ROW EXECUTE FUNCTION control.block_tombstoned_recipe_release();

CREATE FUNCTION control.apply_recipe_release_denial(
    p_event_id uuid, p_target_id uuid, p_effective_epoch bigint,
    p_stream_generation uuid, p_stream_position bigint, p_payload_hash text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_status text;
        v_sequence bigint;
BEGIN
    IF session_user != 'signal_authority_dispatcher' OR p_event_id IS NULL OR p_target_id IS NULL
       OR p_effective_epoch < 1 OR p_stream_generation IS NULL
       OR p_stream_position < 1 OR p_payload_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'recipe replay denied' USING ERRCODE = '42501';
    END IF;
    INSERT INTO control.authority_denial_tombstones
        (event_id, target_kind, target_id, restriction_kind, effective_epoch,
         stream_generation, stream_position, payload_hash)
    VALUES (p_event_id, 'recipe_release', p_target_id, 'recipe_release_revoked',
            p_effective_epoch, p_stream_generation, p_stream_position, p_payload_hash)
    ON CONFLICT (event_id) DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                   WHERE event_id = p_event_id AND target_kind = 'recipe_release'
                     AND target_id = p_target_id AND effective_epoch = p_effective_epoch
                     AND stream_generation = p_stream_generation
                     AND stream_position = p_stream_position
                     AND payload_hash = p_payload_hash) THEN
        RAISE EXCEPTION 'recipe replay identity conflict' USING ERRCODE = '23505';
    END IF;
    PERFORM 1 FROM control.recipe_releases WHERE id = p_target_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    SELECT status, sequence_number INTO v_status, v_sequence
    FROM control.recipe_release_events WHERE release_id = p_target_id
    ORDER BY sequence_number DESC LIMIT 1;
    IF v_status != 'REVOKED' THEN
        INSERT INTO control.recipe_release_events
            (id, release_id, sequence_number, status, actor_reference, source, reason)
        VALUES (p_event_id, p_target_id, v_sequence + 1, 'REVOKED',
                'workload:authority_replay', 'journal_replay',
                'Independent authority restriction replay');
    END IF;
END
$$;
REVOKE ALL ON FUNCTION control.apply_recipe_release_denial(
    uuid,uuid,bigint,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_recipe_release_denial(
    uuid,uuid,bigint,uuid,bigint,text) TO signal_authority_dispatcher;
