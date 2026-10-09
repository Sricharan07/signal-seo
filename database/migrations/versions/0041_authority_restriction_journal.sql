-- The local state remains restrictive before any independent journal I/O.
CREATE TABLE control.authority_restriction_outbox (
    event_id uuid PRIMARY KEY REFERENCES control.platform_events (id),
    scope_kind text NOT NULL CHECK (scope_kind = 'platform'),
    actor_user_id uuid NOT NULL,
    target_kind text NOT NULL CHECK (target_kind = 'identity_session'),
    target_id uuid NOT NULL,
    restriction_kind text NOT NULL CHECK (restriction_kind = 'session_revoked'),
    effective_epoch bigint NOT NULL CHECK (effective_epoch > 0),
    event_time timestamptz NOT NULL,
    original_facts jsonb NOT NULL CHECK (jsonb_typeof(original_facts) = 'object')
);
CREATE INDEX authority_restriction_outbox_time
ON control.authority_restriction_outbox (event_time, event_id);
CREATE TRIGGER authority_restriction_outbox_immutable
BEFORE UPDATE OR DELETE ON control.authority_restriction_outbox
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.authority_restriction_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.authority_restriction_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY authority_outbox_owner ON control.authority_restriction_outbox
TO signal_migrator USING (true) WITH CHECK (true);

CREATE FUNCTION control.enqueue_session_restriction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_epoch bigint;
BEGIN
    IF NEW.event_type != 'identity.session.revoked' THEN
        RETURN NEW;
    END IF;
    SELECT session_version INTO v_epoch
    FROM control.identity_sessions WHERE id = NEW.object_id AND revoked_at IS NOT NULL;
    IF v_epoch IS NULL THEN
        RAISE EXCEPTION 'session restriction target is not revoked' USING ERRCODE = '23503';
    END IF;
    INSERT INTO control.authority_restriction_outbox
        (event_id, scope_kind, actor_user_id, target_kind, target_id,
         restriction_kind, effective_epoch, event_time, original_facts)
    VALUES (NEW.id, 'platform', NEW.actor_user_id, 'identity_session', NEW.object_id,
            'session_revoked', v_epoch, NEW.created_at, NEW.facts);
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.enqueue_session_restriction() FROM PUBLIC;
CREATE TRIGGER enqueue_session_restriction
AFTER INSERT ON control.platform_events
FOR EACH ROW EXECUTE FUNCTION control.enqueue_session_restriction();

-- A receipt is audit evidence, not another restriction and never another outbox item.
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
    ELSE
        RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END
$$;
CREATE POLICY platform_authority_receipt_insert ON control.platform_events
FOR INSERT TO signal_migrator WITH CHECK (
    event_type = 'authority.restriction.acknowledged'
    AND EXISTS (SELECT 1 FROM control.authority_restriction_outbox o
                WHERE o.event_id = platform_events.object_id
                  AND o.actor_user_id = platform_events.actor_user_id)
);
CREATE POLICY platform_authority_owner_read ON control.platform_events
FOR SELECT TO signal_migrator USING (true);

CREATE FUNCTION control.pending_authority_restrictions(p_limit integer)
RETURNS TABLE (event_id uuid, actor_user_id uuid, target_id uuid,
               effective_epoch bigint, event_time timestamptz, original_facts jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF session_user != 'signal_authority_dispatcher' OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'authority dispatcher denied' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT o.event_id, o.actor_user_id, o.target_id, o.effective_epoch,
           o.event_time, o.original_facts
    FROM control.authority_restriction_outbox o
    WHERE NOT EXISTS (SELECT 1 FROM control.platform_events e
                      WHERE e.event_type = 'authority.restriction.acknowledged'
                        AND e.object_id = o.event_id)
    ORDER BY o.event_time, o.event_id LIMIT p_limit;
END
$$;
REVOKE ALL ON FUNCTION control.pending_authority_restrictions(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.pending_authority_restrictions(integer)
TO signal_authority_dispatcher;

CREATE FUNCTION control.record_authority_restriction_receipt(
    p_receipt_id uuid, p_event_id uuid, p_stream_generation uuid,
    p_stream_position bigint, p_payload_hash text, p_verified_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_actor uuid;
        v_existing jsonb;
BEGIN
    IF session_user != 'signal_authority_dispatcher' OR p_receipt_id IS NULL
       OR p_stream_generation IS NULL OR p_stream_position < 1
       OR p_payload_hash !~ '^[0-9a-f]{64}$' OR p_verified_at IS NULL THEN
        RAISE EXCEPTION 'authority receipt denied' USING ERRCODE = '42501';
    END IF;
    SELECT actor_user_id INTO v_actor FROM control.authority_restriction_outbox
    WHERE event_id = p_event_id FOR UPDATE;
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'authority receipt target is invalid' USING ERRCODE = '23503';
    END IF;
    SELECT facts INTO v_existing FROM control.platform_events
    WHERE event_type = 'authority.restriction.acknowledged'
      AND object_id = p_event_id;
    IF v_existing IS NOT NULL THEN
        IF v_existing ->> 'stream_generation' != p_stream_generation::text
           OR (v_existing ->> 'stream_position')::bigint != p_stream_position
           OR v_existing ->> 'payload_hash' != p_payload_hash THEN
            RAISE EXCEPTION 'authority receipt identity conflict' USING ERRCODE = '23505';
        END IF;
        RETURN false;
    END IF;
    INSERT INTO control.platform_events
        (id, event_type, actor_user_id, object_kind, object_id, facts, reason)
    VALUES (p_receipt_id, 'authority.restriction.acknowledged', v_actor,
            'authority_restriction', p_event_id,
            jsonb_build_object('schema_version', 1, 'original_event_id', p_event_id,
                'stream_generation', p_stream_generation,
                'stream_position', p_stream_position,
                'journal_record_id', p_event_id,
                'payload_hash', p_payload_hash, 'verified_at', p_verified_at), NULL);
    RETURN true;
END
$$;
REVOKE ALL ON FUNCTION control.record_authority_restriction_receipt(
    uuid, uuid, uuid, bigint, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_authority_restriction_receipt(
    uuid, uuid, uuid, bigint, text, timestamptz) TO signal_authority_dispatcher;

-- Replay is deny-only. Missing targets remain explicit denial tombstones.
CREATE TABLE control.authority_denial_tombstones (
    event_id uuid PRIMARY KEY,
    target_kind text NOT NULL CHECK (target_kind = 'identity_session'),
    target_id uuid NOT NULL,
    restriction_kind text NOT NULL CHECK (restriction_kind = 'session_revoked'),
    effective_epoch bigint NOT NULL CHECK (effective_epoch > 0),
    stream_generation uuid NOT NULL,
    stream_position bigint NOT NULL CHECK (stream_position > 0),
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    replayed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (stream_generation, stream_position)
);
CREATE INDEX authority_denial_target
ON control.authority_denial_tombstones (target_kind, target_id);
CREATE TRIGGER authority_denial_tombstones_immutable
BEFORE UPDATE OR DELETE ON control.authority_denial_tombstones
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.authority_denial_tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.authority_denial_tombstones FORCE ROW LEVEL SECURITY;
CREATE POLICY authority_denial_owner ON control.authority_denial_tombstones
TO signal_migrator USING (true) WITH CHECK (true);

CREATE FUNCTION control.block_tombstoned_session() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM control.authority_denial_tombstones
               WHERE target_kind = 'identity_session' AND target_id = NEW.id) THEN
        RAISE EXCEPTION 'authority target denied' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.block_tombstoned_session() FROM PUBLIC;
CREATE TRIGGER block_tombstoned_session BEFORE INSERT ON control.identity_sessions
FOR EACH ROW EXECUTE FUNCTION control.block_tombstoned_session();
CREATE FUNCTION control.keep_tombstoned_session_revoked() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.revoked_at IS NULL
       AND EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                   WHERE target_kind = 'identity_session' AND target_id = NEW.id) THEN
        RAISE EXCEPTION 'authority target denied' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.keep_tombstoned_session_revoked() FROM PUBLIC;
CREATE TRIGGER keep_tombstoned_session_revoked
BEFORE UPDATE ON control.identity_sessions
FOR EACH ROW EXECUTE FUNCTION control.keep_tombstoned_session_revoked();

CREATE FUNCTION control.apply_authority_denial(
    p_event_id uuid, p_target_id uuid, p_effective_epoch bigint,
    p_stream_generation uuid, p_stream_position bigint, p_payload_hash text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF session_user != 'signal_authority_dispatcher' OR p_event_id IS NULL OR p_target_id IS NULL
       OR p_effective_epoch < 1 OR p_stream_generation IS NULL
       OR p_stream_position < 1 OR p_payload_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'authority replay denied' USING ERRCODE = '42501';
    END IF;
    INSERT INTO control.authority_denial_tombstones
        (event_id, target_kind, target_id, restriction_kind, effective_epoch,
         stream_generation, stream_position, payload_hash)
    VALUES (p_event_id, 'identity_session', p_target_id, 'session_revoked',
            p_effective_epoch, p_stream_generation, p_stream_position, p_payload_hash)
    ON CONFLICT (event_id) DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones
                   WHERE event_id = p_event_id AND target_id = p_target_id
                     AND effective_epoch = p_effective_epoch
                     AND stream_generation = p_stream_generation
                     AND stream_position = p_stream_position
                     AND payload_hash = p_payload_hash) THEN
        RAISE EXCEPTION 'authority replay identity conflict' USING ERRCODE = '23505';
    END IF;
    UPDATE control.identity_sessions
    SET revoked_at = COALESCE(revoked_at, transaction_timestamp()),
        session_version = GREATEST(session_version, p_effective_epoch)
    WHERE id = p_target_id;
END
$$;
REVOKE ALL ON FUNCTION control.apply_authority_denial(
    uuid, uuid, bigint, uuid, bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_authority_denial(
    uuid, uuid, bigint, uuid, bigint, text) TO signal_authority_dispatcher;

CREATE FUNCTION control.authority_durability_status(p_event_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $$
    SELECT CASE WHEN EXISTS (SELECT 1 FROM control.platform_events e
                             WHERE e.object_id = p_event_id
                               AND e.event_type = 'authority.restriction.acknowledged')
                THEN 'ACKNOWLEDGED' ELSE 'AUTHORITY_DURABILITY_PENDING' END
    FROM control.authority_restriction_outbox o WHERE o.event_id = p_event_id
$$;
REVOKE ALL ON FUNCTION control.authority_durability_status(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.authority_durability_status(uuid)
TO signal_authority_dispatcher;

CREATE TABLE control.authority_replay_checkpoints (
    id uuid PRIMARY KEY,
    stream_generation uuid NOT NULL,
    head_position bigint NOT NULL CHECK (head_position >= 0),
    head_hash text NOT NULL CHECK (head_hash ~ '^[0-9a-f]{64}$'),
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    verified_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER authority_replay_checkpoints_immutable
BEFORE UPDATE OR DELETE ON control.authority_replay_checkpoints
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.authority_replay_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.authority_replay_checkpoints FORCE ROW LEVEL SECURITY;
CREATE POLICY authority_replay_owner ON control.authority_replay_checkpoints
TO signal_migrator USING (true) WITH CHECK (true);
CREATE FUNCTION control.record_authority_replay_checkpoint(
    p_id uuid, p_stream_generation uuid, p_head_position bigint,
    p_head_hash text, p_recovery_generation text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF session_user != 'signal_authority_dispatcher' OR p_id IS NULL
       OR p_stream_generation IS NULL OR p_head_position < 0
       OR p_head_hash !~ '^[0-9a-f]{64}$'
       OR p_recovery_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
        RAISE EXCEPTION 'authority replay checkpoint denied' USING ERRCODE = '42501';
    END IF;
    INSERT INTO control.authority_replay_checkpoints
        (id, stream_generation, head_position, head_hash, recovery_generation)
    VALUES (p_id, p_stream_generation, p_head_position, p_head_hash, p_recovery_generation);
END
$$;
REVOKE ALL ON FUNCTION control.record_authority_replay_checkpoint(
    uuid, uuid, bigint, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_authority_replay_checkpoint(
    uuid, uuid, bigint, text, text) TO signal_authority_dispatcher;
