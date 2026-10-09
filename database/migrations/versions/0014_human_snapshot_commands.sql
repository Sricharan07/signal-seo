ALTER TABLE app.commands
    ALTER COLUMN actor_service DROP NOT NULL,
    ADD COLUMN actor_user_id uuid;

ALTER TABLE app.commands
    DROP CONSTRAINT commands_actor_service_check,
    DROP CONSTRAINT commands_check,
    DROP CONSTRAINT commands_route_key_check;

ALTER TABLE app.commands
    ADD CONSTRAINT commands_actor_service_check CHECK (
        actor_service IS NULL OR actor_service ~ '^[a-z][a-z0-9_.-]{0,63}$'
    ),
    ADD CONSTRAINT commands_actor_exactly_one_check CHECK (
        (actor_service IS NULL) <> (actor_user_id IS NULL)
    ),
    ADD CONSTRAINT commands_principal_key_check CHECK (
        (actor_service IS NOT NULL AND principal_key = 'service:' || actor_service)
        OR
        (actor_user_id IS NOT NULL AND principal_key = 'user:' || actor_user_id::text)
    ),
    ADD CONSTRAINT commands_route_key_check CHECK (
        (actor_service IS NOT NULL AND route_key = 'internal.site.snapshot')
        OR
        (actor_user_id IS NOT NULL AND route_key = 'api.site.snapshot')
    ),
    ADD CONSTRAINT commands_actor_user_membership_fk
        FOREIGN KEY (tenant_id, actor_user_id)
        REFERENCES app.memberships (tenant_id, user_id);

CREATE FUNCTION control.resolve_snapshot_authority(
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
           identity_session.authentication_level
      INTO v_tenant_id, v_user_id, v_authentication_level
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

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_requested_site_id::text, true);
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
     WHERE membership.tenant_id = v_tenant_id
       AND membership.user_id = v_user_id
       AND membership.state = 'active'
       AND site_membership.site_id = p_requested_site_id
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

    RETURN QUERY SELECT 'authorized'::text, v_tenant_id, v_user_id, v_role_key,
                        v_authentication_level, v_membership_epoch,
                        v_site_authorization_epoch;
END;
$$;

REVOKE ALL ON FUNCTION control.resolve_snapshot_authority(bytea, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.resolve_snapshot_authority(bytea, uuid, text)
TO signal_identity;

CREATE FUNCTION control.accept_authenticated_snapshot(
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
        SELECT existing.id, existing.status, existing.accepted_at,
               existing.request_fingerprint
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
        RETURN QUERY SELECT v_existing.id, true, v_existing.status,
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
    SELECT created.id, false, created.status, created.accepted_at, 'accepted'::text
      FROM app.commands AS created
     WHERE created.tenant_id = v_authority.tenant_id
       AND created.site_id = p_requested_site_id
       AND created.id = p_command_id;
END;
$$;

REVOKE ALL ON FUNCTION control.accept_authenticated_snapshot(
    bytea, uuid, text, text, uuid, uuid, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.accept_authenticated_snapshot(
    bytea, uuid, text, text, uuid, uuid, uuid
) TO signal_identity;

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
                            NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;

    SELECT existing.id, existing.actor_user_id, existing.kind,
           existing.status, existing.accepted_at
      INTO v_command
      FROM app.commands AS existing
     WHERE existing.tenant_id = v_authority.tenant_id
       AND existing.site_id = p_requested_site_id
       AND existing.id = p_command_id
       AND existing.actor_user_id = v_authority.user_id
       AND existing.route_key = 'api.site.snapshot';
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                            NULL::timestamptz, 'not_found'::text;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_command.id, v_command.actor_user_id, v_command.kind,
                        v_command.status, v_command.accepted_at, 'found'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.read_authenticated_snapshot_command(
    bytea, uuid, text, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_authenticated_snapshot_command(
    bytea, uuid, text, uuid
) TO signal_identity;
