CREATE OR REPLACE FUNCTION control.admit_command_event(
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
           workflow.workflow_type, 'admitted'::text AS state_projection,
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
               workflow.workflow_type, 'admitted'::text AS state_projection,
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
