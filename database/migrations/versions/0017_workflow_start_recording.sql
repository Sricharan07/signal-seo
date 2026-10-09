ALTER TABLE app.commands
    DROP CONSTRAINT commands_status_check,
    ADD CONSTRAINT commands_status_check CHECK (
        status IN ('accepted', 'workflow_admitted', 'processing')
    );

ALTER TABLE app.command_events
    DROP CONSTRAINT command_events_shape_check,
    ADD CONSTRAINT command_events_shape_check CHECK (
        (
            event_number = 1
            AND event_type = 'command.accepted'
            AND facts = '{"schema_version":1}'::jsonb
        )
        OR
        (
            event_number = 2
            AND event_type = 'command.workflow_admitted'
            AND facts = jsonb_build_object(
                'consumer_key', 'workflow.command-start.v1',
                'schema_version', 1,
                'workflow_id',
                'signal:CrawlSite:' || tenant_id::text || ':' || command_id::text
            )
        )
        OR
        (
            event_number = 3
            AND event_type = 'command.workflow_started'
            AND facts->>'first_run_id' ~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            AND facts = jsonb_build_object(
                'first_run_id', facts->>'first_run_id',
                'schema_version', 1,
                'start_evidence', facts->>'start_evidence',
                'workflow_id',
                'signal:CrawlSite:' || tenant_id::text || ':' || command_id::text
            )
            AND facts->>'start_evidence' IN ('start_acknowledged', 'already_started')
        )
    );

CREATE OR REPLACE FUNCTION app.guard_command_progress_mutation() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_command_record' USING ERRCODE = '55000';
    END IF;

    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.actor_service IS DISTINCT FROM NEW.actor_service
       OR OLD.actor_user_id IS DISTINCT FROM NEW.actor_user_id
       OR OLD.kind IS DISTINCT FROM NEW.kind
       OR OLD.schema_version IS DISTINCT FROM NEW.schema_version
       OR OLD.principal_key IS DISTINCT FROM NEW.principal_key
       OR OLD.route_key IS DISTINCT FROM NEW.route_key
       OR OLD.scope_kind IS DISTINCT FROM NEW.scope_kind
       OR OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key
       OR OLD.request_fingerprint IS DISTINCT FROM NEW.request_fingerprint
       OR OLD.payload IS DISTINCT FROM NEW.payload
       OR OLD.accepted_at IS DISTINCT FROM NEW.accepted_at
       OR OLD.result_reference IS DISTINCT FROM NEW.result_reference
    THEN
        RAISE EXCEPTION 'immutable_command_intent' USING ERRCODE = '55000';
    END IF;

    IF OLD.status = 'accepted'
       AND NEW.status = 'workflow_admitted'
       AND NEW.row_version = OLD.row_version + 1
    THEN
        RETURN NEW;
    END IF;

    IF OLD.status = 'workflow_admitted'
       AND NEW.status = 'processing'
       AND NEW.row_version = OLD.row_version + 1
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'invalid_command_progress_transition' USING ERRCODE = '55000';
END;
$$;

ALTER TABLE app.workflow_refs
    DROP CONSTRAINT workflow_refs_state_projection_check,
    DROP CONSTRAINT workflow_refs_projected_event_sequence_check,
    DROP CONSTRAINT workflow_refs_first_run_id_check,
    ADD CONSTRAINT workflow_refs_progress_check CHECK (
        (
            state_projection = 'admitted'
            AND projected_event_sequence = 2
            AND first_run_id IS NULL
        )
        OR
        (
            state_projection = 'running'
            AND projected_event_sequence = 3
            AND first_run_id ~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        )
    );

DROP TRIGGER workflow_refs_admission_immutable ON app.workflow_refs;

CREATE FUNCTION app.guard_workflow_progress_mutation() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_workflow_reference' USING ERRCODE = '55000';
    END IF;

    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.command_id IS DISTINCT FROM NEW.command_id
       OR OLD.workflow_id IS DISTINCT FROM NEW.workflow_id
       OR OLD.workflow_type IS DISTINCT FROM NEW.workflow_type
    THEN
        RAISE EXCEPTION 'immutable_workflow_identity' USING ERRCODE = '55000';
    END IF;

    IF OLD.state_projection = 'admitted'
       AND OLD.first_run_id IS NULL
       AND OLD.projected_event_sequence = 2
       AND NEW.state_projection = 'running'
       AND NEW.first_run_id IS NOT NULL
       AND NEW.projected_event_sequence = 3
       AND NEW.projected_at >= OLD.projected_at
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'invalid_workflow_progress_transition' USING ERRCODE = '55000';
END;
$$;

REVOKE ALL ON FUNCTION app.guard_workflow_progress_mutation() FROM PUBLIC;

CREATE TRIGGER workflow_refs_progress_guard BEFORE UPDATE OR DELETE ON app.workflow_refs
FOR EACH ROW EXECUTE FUNCTION app.guard_workflow_progress_mutation();

CREATE FUNCTION control.record_workflow_started(
    p_tenant_id uuid,
    p_site_id uuid,
    p_command_id uuid,
    p_workflow_id text,
    p_first_run_id text,
    p_start_evidence text,
    p_progress_event_id uuid
)
RETURNS TABLE (
    progress_event_id uuid,
    command_id uuid,
    workflow_id text,
    first_run_id text,
    start_evidence text,
    state_projection text,
    projected_at timestamptz,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_existing record;
    v_projected_at timestamptz;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_command_id IS NULL
       OR p_progress_event_id IS NULL
       OR p_progress_event_id::text !~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_workflow_id IS DISTINCT FROM
            'signal:CrawlSite:' || p_tenant_id::text || ':' || p_command_id::text
       OR p_first_run_id IS NULL
       OR p_first_run_id !~
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR p_start_evidence IS NULL
       OR p_start_evidence NOT IN ('start_acknowledged', 'already_started')
    THEN
        RAISE EXCEPTION 'invalid_workflow_start_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);

    SELECT workflow.first_run_id, workflow.state_projection,
           workflow.projected_event_sequence, command.status, command.row_version
      INTO v_existing
      FROM app.workflow_refs AS workflow
      JOIN app.commands AS command
        ON command.tenant_id = workflow.tenant_id
       AND command.site_id = workflow.site_id
       AND command.id = workflow.command_id
     WHERE workflow.tenant_id = p_tenant_id
       AND workflow.site_id = p_site_id
       AND workflow.command_id = p_command_id
       AND workflow.workflow_id = p_workflow_id
       AND workflow.workflow_type = 'CrawlSite'
     FOR UPDATE OF workflow, command;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                            NULL::text, NULL::text, NULL::timestamptz, NULL::boolean,
                            'workflow_unavailable'::text;
        RETURN;
    END IF;

    IF v_existing.state_projection = 'running' THEN
        IF v_existing.first_run_id IS DISTINCT FROM p_first_run_id THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                                NULL::text, NULL::text, NULL::timestamptz, NULL::boolean,
                                'run_conflict'::text;
            RETURN;
        END IF;

        RETURN QUERY
        SELECT event.id, workflow.command_id, workflow.workflow_id,
               workflow.first_run_id, event.facts->>'start_evidence',
               workflow.state_projection,
               workflow.projected_at, true, 'recorded'::text
          FROM app.workflow_refs AS workflow
          JOIN app.command_events AS event
            ON event.tenant_id = workflow.tenant_id
           AND event.site_id = workflow.site_id
           AND event.command_id = workflow.command_id
           AND event.event_number = 3
           AND event.event_type = 'command.workflow_started'
         WHERE workflow.tenant_id = p_tenant_id
           AND workflow.site_id = p_site_id
           AND workflow.command_id = p_command_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'invalid_workflow_start_projection' USING ERRCODE = '55000';
        END IF;
        RETURN;
    END IF;

    IF v_existing.state_projection <> 'admitted'
       OR v_existing.first_run_id IS NOT NULL
       OR v_existing.projected_event_sequence <> 2
       OR v_existing.status <> 'workflow_admitted'
       OR v_existing.row_version <> 2
    THEN
        RAISE EXCEPTION 'invalid_workflow_start_projection' USING ERRCODE = '55000';
    END IF;

    v_projected_at := statement_timestamp();
    UPDATE app.workflow_refs AS workflow
       SET first_run_id = p_first_run_id,
           state_projection = 'running',
           projected_event_sequence = 3,
           projected_at = v_projected_at
     WHERE workflow.tenant_id = p_tenant_id
       AND workflow.site_id = p_site_id
       AND workflow.command_id = p_command_id;
    UPDATE app.commands AS command
       SET status = 'processing',
           row_version = command.row_version + 1
     WHERE command.tenant_id = p_tenant_id
       AND command.site_id = p_site_id
       AND command.id = p_command_id;
    INSERT INTO app.command_events (
        tenant_id, site_id, id, command_id, event_number, event_type, facts,
        created_at
    )
    VALUES (
        p_tenant_id, p_site_id, p_progress_event_id, p_command_id, 3,
        'command.workflow_started',
        jsonb_build_object(
            'first_run_id', p_first_run_id,
            'schema_version', 1,
            'start_evidence', p_start_evidence,
            'workflow_id', p_workflow_id
        ),
        v_projected_at
    );

    RETURN QUERY SELECT p_progress_event_id, p_command_id, p_workflow_id,
                        p_first_run_id, p_start_evidence, 'running'::text, v_projected_at,
                        false, 'recorded'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.record_workflow_started(
    uuid, uuid, uuid, text, text, text, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_workflow_started(
    uuid, uuid, uuid, text, text, text, uuid
) TO signal_workflow;

DROP FUNCTION control.read_authenticated_snapshot_command(bytea, uuid, text, uuid);

CREATE FUNCTION control.read_authenticated_snapshot_command(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_command_id uuid
)
RETURNS TABLE (
    command_id uuid,
    actor_user_id uuid,
    kind text,
    status text,
    accepted_at timestamptz,
    workflow_id text,
    workflow_type text,
    first_run_id text,
    workflow_state text,
    projected_at timestamptz,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_command record;
BEGIN
    IF p_command_id IS NULL THEN
        RAISE EXCEPTION 'invalid_authenticated_command_read_input' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash,
          p_requested_site_id,
          p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                            NULL::timestamptz, NULL::text, NULL::text, NULL::text,
                            NULL::text, NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;

    SELECT existing.id, existing.actor_user_id, existing.kind,
           existing.status, existing.accepted_at, workflow.workflow_id,
           workflow.workflow_type, workflow.first_run_id,
           workflow.state_projection, workflow.projected_at
      INTO v_command
      FROM app.commands AS existing
      LEFT JOIN app.workflow_refs AS workflow
        ON workflow.tenant_id = existing.tenant_id
       AND workflow.site_id = existing.site_id
       AND workflow.command_id = existing.id
     WHERE existing.tenant_id = v_authority.tenant_id
       AND existing.site_id = p_requested_site_id
       AND existing.id = p_command_id
       AND existing.actor_user_id = v_authority.user_id
       AND existing.route_key = 'api.site.snapshot';
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                            NULL::timestamptz, NULL::text, NULL::text, NULL::text,
                            NULL::text, NULL::timestamptz, 'not_found'::text;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_command.id, v_command.actor_user_id, v_command.kind,
                        v_command.status, v_command.accepted_at,
                        v_command.workflow_id, v_command.workflow_type,
                        v_command.first_run_id, v_command.state_projection,
                        v_command.projected_at, 'found'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.read_authenticated_snapshot_command(
    bytea, uuid, text, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_authenticated_snapshot_command(
    bytea, uuid, text, uuid
) TO signal_identity;
