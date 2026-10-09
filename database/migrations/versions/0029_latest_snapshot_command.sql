CREATE FUNCTION control.read_latest_authenticated_snapshot_command(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text
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
    result_reference jsonb,
    terminal_reason text,
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
    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash,
          p_requested_site_id,
          p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                            NULL::timestamptz, NULL::text, NULL::text, NULL::text,
                            NULL::text, NULL::timestamptz, NULL::jsonb, NULL::text,
                            v_authority.outcome;
        RETURN;
    END IF;

    SELECT existing.id, existing.actor_user_id, existing.kind,
           existing.status, existing.accepted_at, workflow.workflow_id,
           workflow.workflow_type, workflow.first_run_id,
           workflow.state_projection, workflow.projected_at,
           existing.result_reference, terminal.facts->>'reason' AS terminal_reason
      INTO v_command
      FROM app.commands AS existing
      LEFT JOIN app.workflow_refs AS workflow
        ON workflow.tenant_id = existing.tenant_id
       AND workflow.site_id = existing.site_id
       AND workflow.command_id = existing.id
      LEFT JOIN app.command_events AS terminal
        ON terminal.tenant_id = existing.tenant_id
       AND terminal.site_id = existing.site_id
       AND terminal.command_id = existing.id
       AND terminal.event_number = 4
     WHERE existing.tenant_id = v_authority.tenant_id
       AND existing.site_id = p_requested_site_id
       AND existing.actor_user_id = v_authority.user_id
       AND existing.route_key = 'api.site.snapshot'
     ORDER BY existing.accepted_at DESC, existing.id DESC
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                            NULL::timestamptz, NULL::text, NULL::text, NULL::text,
                            NULL::text, NULL::timestamptz, NULL::jsonb, NULL::text,
                            'not_found'::text;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_command.id, v_command.actor_user_id, v_command.kind,
                        v_command.status, v_command.accepted_at,
                        v_command.workflow_id, v_command.workflow_type,
                        v_command.first_run_id, v_command.state_projection,
                        v_command.projected_at, v_command.result_reference,
                        v_command.terminal_reason, 'found'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.read_latest_authenticated_snapshot_command(
    bytea, uuid, text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_latest_authenticated_snapshot_command(
    bytea, uuid, text
) TO signal_identity;
