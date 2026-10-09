CREATE TABLE control.platform_events (
    id uuid PRIMARY KEY,
    event_type text NOT NULL CHECK (event_type = 'identity.session.issued'),
    actor_user_id uuid NOT NULL REFERENCES control.users (id),
    object_kind text NOT NULL CHECK (object_kind = 'identity_session'),
    object_id uuid NOT NULL,
    facts jsonb NOT NULL CHECK (
        jsonb_typeof(facts) = 'object'
        AND facts - 'schema_version' - 'authentication_level' = '{}'::jsonb
        AND facts -> 'schema_version' = '1'::jsonb
        AND jsonb_typeof(facts -> 'authentication_level') = 'string'
        AND facts ->> 'authentication_level' IN ('primary', 'mfa')
    ),
    reason text CHECK (reason IS NULL),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (object_kind, object_id, event_type)
);
CREATE INDEX platform_events_object_history
ON control.platform_events (object_kind, object_id, created_at, id);
CREATE INDEX platform_events_type_history
ON control.platform_events (event_type, created_at, id);

CREATE FUNCTION control.validate_platform_event_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM control.identity_sessions identity_session
        WHERE identity_session.id = NEW.object_id
          AND identity_session.user_id = NEW.actor_user_id
    ) THEN
        RAISE EXCEPTION 'platform event object reference is invalid'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.validate_platform_event_reference() FROM PUBLIC;
CREATE TRIGGER platform_event_reference_guard
BEFORE INSERT ON control.platform_events
FOR EACH ROW EXECUTE FUNCTION control.validate_platform_event_reference();

CREATE TRIGGER platform_events_immutable
BEFORE UPDATE OR DELETE ON control.platform_events
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE control.platform_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.platform_events FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_identity_event_insert ON control.platform_events
FOR INSERT TO signal_identity
WITH CHECK (
    EXISTS (
        SELECT 1
        FROM control.identity_sessions identity_session
        WHERE identity_session.id = platform_events.object_id
          AND identity_session.user_id = platform_events.actor_user_id
          AND identity_session.token_hash = control.current_identity_session_hash()
    )
);

GRANT INSERT (
    id,
    event_type,
    actor_user_id,
    object_kind,
    object_id,
    facts,
    reason
) ON control.platform_events TO signal_identity;
