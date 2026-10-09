ALTER TABLE app.commands
    DROP CONSTRAINT commands_status_check,
    DROP CONSTRAINT commands_row_version_check,
    ADD COLUMN result_reference jsonb,
    ADD CONSTRAINT commands_status_check CHECK (
        status IN ('accepted', 'workflow_admitted')
    ),
    ADD CONSTRAINT commands_row_version_check CHECK (row_version > 0),
    ADD CONSTRAINT commands_result_reference_check CHECK (
        result_reference IS NULL OR jsonb_typeof(result_reference) = 'object'
    );

ALTER TABLE app.command_events
    DROP CONSTRAINT command_events_event_type_check,
    DROP CONSTRAINT command_events_facts_check,
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
    );

DROP TRIGGER commands_immutable ON app.commands;

CREATE FUNCTION app.guard_command_progress_mutation() RETURNS trigger
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

    RAISE EXCEPTION 'invalid_command_progress_transition' USING ERRCODE = '55000';
END;
$$;

REVOKE ALL ON FUNCTION app.guard_command_progress_mutation() FROM PUBLIC;

CREATE TRIGGER command_progress_guard BEFORE UPDATE OR DELETE ON app.commands
FOR EACH ROW EXECUTE FUNCTION app.guard_command_progress_mutation();

CREATE TABLE app.consumer_inbox (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    consumer_key text NOT NULL CHECK (consumer_key = 'workflow.command-start.v1'),
    event_id uuid NOT NULL,
    command_id uuid NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, consumer_key, event_id),
    FOREIGN KEY (tenant_id, site_id, command_id, event_id)
        REFERENCES app.command_events (tenant_id, site_id, command_id, id)
);

CREATE TABLE app.workflow_refs (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    command_id uuid NOT NULL,
    workflow_id text NOT NULL,
    first_run_id text,
    workflow_type text NOT NULL CHECK (workflow_type = 'CrawlSite'),
    state_projection text NOT NULL CHECK (state_projection = 'admitted'),
    projected_event_sequence bigint NOT NULL CHECK (projected_event_sequence = 2),
    projected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, command_id),
    UNIQUE (tenant_id, workflow_id),
    FOREIGN KEY (tenant_id, site_id, command_id)
        REFERENCES app.commands (tenant_id, site_id, id),
    CHECK (
        workflow_id =
            'signal:CrawlSite:' || tenant_id::text || ':' || command_id::text
    ),
    CHECK (first_run_id IS NULL)
);

CREATE INDEX workflow_refs_site_state
ON app.workflow_refs (tenant_id, site_id, state_projection, projected_at, command_id);

CREATE TRIGGER consumer_inbox_immutable BEFORE UPDATE OR DELETE ON app.consumer_inbox
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE TRIGGER workflow_refs_admission_immutable BEFORE UPDATE OR DELETE ON app.workflow_refs
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.consumer_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.consumer_inbox FORCE ROW LEVEL SECURITY;
CREATE POLICY consumer_inbox_scope ON app.consumer_inbox
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.workflow_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.workflow_refs FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_refs_scope ON app.workflow_refs
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.admit_command_event(
    p_tenant_id uuid,
    p_outbox_id uuid,
    p_event_id uuid,
    p_site_id uuid,
    p_command_id uuid,
    p_aggregate_kind text,
    p_event_type text,
    p_schema_version integer,
    p_payload jsonb,
    p_consumer_key text
)
RETURNS TABLE (
    source_event_id uuid,
    progress_event_id uuid,
    command_id uuid,
    site_id uuid,
    workflow_id text,
    workflow_type text,
    state_projection text,
    processed_at timestamptz,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_source record;
    v_existing record;
    v_progress_event_id uuid;
    v_processed_at timestamptz;
    v_workflow_id text;
BEGIN
    IF p_tenant_id IS NULL OR p_outbox_id IS NULL OR p_event_id IS NULL
       OR p_site_id IS NULL OR p_command_id IS NULL
       OR p_aggregate_kind IS DISTINCT FROM 'command'
       OR p_event_type IS DISTINCT FROM 'command.accepted'
       OR p_schema_version IS DISTINCT FROM 1
       OR p_payload IS DISTINCT FROM '{"schema_version":1}'::jsonb
       OR p_consumer_key IS DISTINCT FROM 'workflow.command-start.v1'
    THEN
        RAISE EXCEPTION 'invalid_command_admission_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', '', true);

    SELECT message.site_id, message.aggregate_id
      INTO v_source
      FROM app.outbox AS message
     WHERE message.tenant_id = p_tenant_id
       AND message.id = p_outbox_id
       AND message.event_id = p_event_id
       AND message.site_id = p_site_id
       AND message.aggregate_id = p_command_id
       AND message.aggregate_kind = p_aggregate_kind
       AND message.event_type = p_event_type
       AND message.schema_version = p_schema_version
       AND message.payload = p_payload
     FOR KEY SHARE OF message;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'event_unavailable'::text;
        RETURN;
    END IF;

    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT inbox.event_id, progress.id AS progress_event_id,
           inbox.command_id, inbox.site_id, workflow.workflow_id,
           workflow.workflow_type, workflow.state_projection,
           inbox.processed_at
      INTO v_existing
      FROM app.consumer_inbox AS inbox
      JOIN app.workflow_refs AS workflow
        ON workflow.tenant_id = inbox.tenant_id
       AND workflow.site_id = inbox.site_id
       AND workflow.command_id = inbox.command_id
      JOIN app.command_events AS progress
        ON progress.tenant_id = inbox.tenant_id
       AND progress.site_id = inbox.site_id
       AND progress.command_id = inbox.command_id
       AND progress.event_number = 2
       AND progress.event_type = 'command.workflow_admitted'
     WHERE inbox.tenant_id = p_tenant_id
       AND inbox.consumer_key = p_consumer_key
       AND inbox.event_id = p_event_id;
    IF FOUND THEN
        RETURN QUERY SELECT v_existing.event_id, v_existing.progress_event_id,
                            v_existing.command_id, v_existing.site_id,
                            v_existing.workflow_id, v_existing.workflow_type,
                            v_existing.state_projection, v_existing.processed_at,
                            true, 'admitted'::text;
        RETURN;
    END IF;

    PERFORM 1
      FROM control.tenant_directory AS directory_row
      JOIN app.tenants AS tenant_row
        ON tenant_row.tenant_id = directory_row.tenant_id
     WHERE directory_row.tenant_id = p_tenant_id
       AND directory_row.lifecycle = 'active'
       AND tenant_row.lifecycle = 'active'
     FOR SHARE OF directory_row, tenant_row;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'scope_unavailable'::text;
        RETURN;
    END IF;

    PERFORM 1
      FROM app.sites AS site_row
     WHERE site_row.tenant_id = p_tenant_id
       AND site_row.id = p_site_id
       AND site_row.state <> 'archived'
     FOR SHARE OF site_row;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'scope_unavailable'::text;
        RETURN;
    END IF;

    SELECT accepted.id
      INTO v_source
      FROM app.command_events AS accepted
      JOIN app.commands AS command
        ON command.tenant_id = accepted.tenant_id
       AND command.site_id = accepted.site_id
       AND command.id = accepted.command_id
     WHERE accepted.tenant_id = p_tenant_id
       AND accepted.site_id = p_site_id
       AND accepted.id = p_event_id
       AND accepted.command_id = p_command_id
       AND accepted.event_number = 1
       AND accepted.event_type = 'command.accepted'
       AND accepted.facts = '{"schema_version":1}'::jsonb
       AND command.kind = 'site.snapshot'
       AND command.schema_version = 1
       AND command.status = 'accepted'
     FOR KEY SHARE OF accepted
     FOR UPDATE OF command;
    IF NOT FOUND THEN
        SELECT inbox.event_id, progress.id AS progress_event_id,
               inbox.command_id, inbox.site_id, workflow.workflow_id,
               workflow.workflow_type, workflow.state_projection,
               inbox.processed_at
          INTO v_existing
          FROM app.consumer_inbox AS inbox
          JOIN app.workflow_refs AS workflow
            ON workflow.tenant_id = inbox.tenant_id
           AND workflow.site_id = inbox.site_id
           AND workflow.command_id = inbox.command_id
          JOIN app.command_events AS progress
            ON progress.tenant_id = inbox.tenant_id
           AND progress.site_id = inbox.site_id
           AND progress.command_id = inbox.command_id
           AND progress.event_number = 2
           AND progress.event_type = 'command.workflow_admitted'
         WHERE inbox.tenant_id = p_tenant_id
           AND inbox.consumer_key = p_consumer_key
           AND inbox.event_id = p_event_id;
        IF FOUND THEN
            RETURN QUERY SELECT v_existing.event_id, v_existing.progress_event_id,
                                v_existing.command_id, v_existing.site_id,
                                v_existing.workflow_id, v_existing.workflow_type,
                                v_existing.state_projection, v_existing.processed_at,
                                true, 'admitted'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                            NULL::text, NULL::text, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'event_unavailable'::text;
        RETURN;
    END IF;

    v_workflow_id :=
        'signal:CrawlSite:' || p_tenant_id::text || ':' || p_command_id::text;
    v_progress_event_id := gen_random_uuid();
    v_processed_at := statement_timestamp();

    INSERT INTO app.consumer_inbox (
        tenant_id, site_id, consumer_key, event_id, command_id, processed_at
    )
    VALUES (
        p_tenant_id, p_site_id, p_consumer_key, p_event_id,
        p_command_id, v_processed_at
    );
    INSERT INTO app.workflow_refs (
        tenant_id, site_id, command_id, workflow_id, workflow_type,
        state_projection, projected_event_sequence, projected_at
    )
    VALUES (
        p_tenant_id, p_site_id, p_command_id, v_workflow_id, 'CrawlSite',
        'admitted', 2, v_processed_at
    );
    UPDATE app.commands AS command
       SET status = 'workflow_admitted',
           row_version = command.row_version + 1
     WHERE command.tenant_id = p_tenant_id
       AND command.site_id = p_site_id
       AND command.id = p_command_id;
    INSERT INTO app.command_events (
        tenant_id, site_id, id, command_id, event_number, event_type, facts,
        created_at
    )
    VALUES (
        p_tenant_id, p_site_id, v_progress_event_id, p_command_id, 2,
        'command.workflow_admitted',
        jsonb_build_object(
            'consumer_key', p_consumer_key,
            'schema_version', 1,
            'workflow_id', v_workflow_id
        ),
        v_processed_at
    );

    RETURN QUERY SELECT p_event_id, v_progress_event_id, p_command_id, p_site_id,
                        v_workflow_id, 'CrawlSite'::text, 'admitted'::text,
                        v_processed_at, false, 'admitted'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.admit_command_event(
    uuid, uuid, uuid, uuid, uuid, text, text, integer, jsonb, text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.admit_command_event(
    uuid, uuid, uuid, uuid, uuid, text, text, integer, jsonb, text
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
                            NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;

    SELECT existing.id, existing.actor_user_id, existing.kind,
           existing.status, existing.accepted_at, workflow.workflow_id,
           workflow.workflow_type, workflow.state_projection,
           workflow.projected_at
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
                            NULL::timestamptz, 'not_found'::text;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_command.id, v_command.actor_user_id, v_command.kind,
                        v_command.status, v_command.accepted_at,
                        v_command.workflow_id, v_command.workflow_type,
                        v_command.state_projection, v_command.projected_at,
                        'found'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.read_authenticated_snapshot_command(
    bytea, uuid, text, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_authenticated_snapshot_command(
    bytea, uuid, text, uuid
) TO signal_identity;

CREATE OR REPLACE FUNCTION control.accept_authenticated_snapshot(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_idempotency_key text,
    p_command_id uuid,
    p_event_id uuid,
    p_outbox_id uuid
)
RETURNS TABLE (
    command_id uuid,
    reused boolean,
    status text,
    accepted_at timestamptz,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_principal_key text;
    v_fingerprint bytea;
    v_inserted_id uuid;
    v_existing record;
BEGIN
    IF p_idempotency_key IS NULL
       OR p_idempotency_key !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_command_id IS NULL OR p_event_id IS NULL OR p_outbox_id IS NULL
       OR p_command_id = p_event_id OR p_command_id = p_outbox_id
       OR p_event_id = p_outbox_id
       OR p_command_id::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_event_id::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_outbox_id::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    THEN
        RAISE EXCEPTION 'invalid_authenticated_snapshot_input' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash,
          p_requested_site_id,
          p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::boolean, NULL::text,
                            NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;

    v_principal_key := 'user:' || v_authority.user_id::text;
    v_fingerprint := sha256(
        convert_to('signal-command-fingerprint-v1', 'UTF8') || decode('00', 'hex')
        || convert_to(v_authority.tenant_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(v_authority.user_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to('api.site.snapshot', 'UTF8') || decode('00', 'hex')
        || convert_to(p_requested_site_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to('{"schema_version":1}', 'UTF8')
    );

    INSERT INTO app.commands (
        tenant_id, id, site_id, actor_user_id, kind, schema_version,
        principal_key, route_key, scope_kind, idempotency_key,
        request_fingerprint, payload
    )
    VALUES (
        v_authority.tenant_id, p_command_id, p_requested_site_id,
        v_authority.user_id, 'site.snapshot', 1, v_principal_key,
        'api.site.snapshot', 'site', p_idempotency_key, v_fingerprint,
        '{"schema_version":1}'::jsonb
    )
    ON CONFLICT (tenant_id, principal_key, route_key, idempotency_key)
    DO NOTHING
    RETURNING id INTO v_inserted_id;

    IF v_inserted_id IS NULL THEN
        SELECT existing.id, existing.accepted_at, existing.request_fingerprint
          INTO v_existing
          FROM app.commands AS existing
         WHERE existing.tenant_id = v_authority.tenant_id
           AND existing.site_id = p_requested_site_id
           AND existing.principal_key = v_principal_key
           AND existing.route_key = 'api.site.snapshot'
           AND existing.idempotency_key = p_idempotency_key;
        IF NOT FOUND OR v_existing.request_fingerprint <> v_fingerprint THEN
            RETURN QUERY SELECT NULL::uuid, NULL::boolean, NULL::text,
                                NULL::timestamptz, 'idempotency_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, true, 'accepted'::text,
                            v_existing.accepted_at, 'accepted'::text;
        RETURN;
    END IF;

    INSERT INTO app.command_events (
        tenant_id, site_id, id, command_id, event_number, event_type, facts
    )
    VALUES (
        v_authority.tenant_id, p_requested_site_id, p_event_id, p_command_id,
        1, 'command.accepted', '{"schema_version":1}'::jsonb
    );
    INSERT INTO app.outbox (
        tenant_id, site_id, id, event_id, aggregate_kind, aggregate_id,
        event_type, schema_version, payload
    )
    VALUES (
        v_authority.tenant_id, p_requested_site_id, p_outbox_id, p_event_id,
        'command', p_command_id, 'command.accepted', 1,
        '{"schema_version":1}'::jsonb
    );

    RETURN QUERY
    SELECT created.id, false, 'accepted'::text, created.accepted_at, 'accepted'::text
      FROM app.commands AS created
     WHERE created.tenant_id = v_authority.tenant_id
       AND created.site_id = p_requested_site_id
       AND created.id = p_command_id;
END;
$$;
