DROP TRIGGER outbox_immutable ON app.outbox;

ALTER TABLE app.outbox
ADD CONSTRAINT outbox_delivery_state_check CHECK (
    (lease_owner IS NULL OR lease_owner ~ '^[a-z][a-z0-9_.:-]{0,63}$')
    AND (lease_owner IS NULL OR (delivered_at IS NULL AND attempt_count > 0))
    AND (delivered_at IS NULL OR (
        lease_owner IS NULL AND lease_until IS NULL AND attempt_count > 0
    ))
);

CREATE FUNCTION app.guard_outbox_delivery_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_outbox_record' USING ERRCODE = '55000';
    END IF;

    IF ROW(
        NEW.tenant_id, NEW.site_id, NEW.id, NEW.event_id,
        NEW.aggregate_kind, NEW.aggregate_id, NEW.event_type,
        NEW.schema_version, NEW.payload
    ) IS DISTINCT FROM ROW(
        OLD.tenant_id, OLD.site_id, OLD.id, OLD.event_id,
        OLD.aggregate_kind, OLD.aggregate_id, OLD.event_type,
        OLD.schema_version, OLD.payload
    ) THEN
        RAISE EXCEPTION 'immutable_outbox_payload' USING ERRCODE = '55000';
    END IF;

    IF OLD.delivered_at IS NULL
       AND NEW.delivered_at IS NULL
       AND NEW.available_at = OLD.available_at
       AND NEW.lease_owner IS NOT NULL
       AND NEW.lease_until > statement_timestamp()
       AND NEW.lease_until <= statement_timestamp() + interval '5 minutes'
       AND NEW.attempt_count = OLD.attempt_count + 1
       AND (OLD.lease_until IS NULL OR OLD.lease_until <= statement_timestamp())
    THEN
        RETURN NEW;
    END IF;

    IF OLD.delivered_at IS NULL
       AND OLD.lease_owner IS NOT NULL
       AND OLD.lease_until > statement_timestamp()
       AND NEW.delivered_at >= statement_timestamp()
       AND NEW.delivered_at <= clock_timestamp() + interval '1 second'
       AND NEW.available_at = OLD.available_at
       AND NEW.lease_owner IS NULL
       AND NEW.lease_until IS NULL
       AND NEW.attempt_count = OLD.attempt_count
    THEN
        RETURN NEW;
    END IF;

    IF OLD.delivered_at IS NULL
       AND OLD.lease_owner IS NOT NULL
       AND OLD.lease_until > statement_timestamp()
       AND NEW.delivered_at IS NULL
       AND NEW.available_at > statement_timestamp()
       AND NEW.available_at <= statement_timestamp() + interval '1 hour 1 second'
       AND NEW.lease_owner IS NULL
       AND NEW.lease_until IS NULL
       AND NEW.attempt_count = OLD.attempt_count
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'invalid_outbox_delivery_transition' USING ERRCODE = '55000';
END;
$$;

REVOKE ALL ON FUNCTION app.guard_outbox_delivery_mutation() FROM PUBLIC;

CREATE TRIGGER outbox_delivery_guard BEFORE UPDATE OR DELETE ON app.outbox
FOR EACH ROW EXECUTE FUNCTION app.guard_outbox_delivery_mutation();

CREATE INDEX outbox_dispatch_pending
ON app.outbox (tenant_id, available_at, id)
WHERE delivered_at IS NULL;

CREATE POLICY outbox_dispatch_function_scope ON app.outbox
TO signal_migrator
USING (
    tenant_id = app.current_tenant_id()
    AND app.current_site_id() IS NULL
)
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND app.current_site_id() IS NULL
);

CREATE FUNCTION control.claim_outbox_batch(
    p_tenant_id uuid,
    p_worker_key text,
    p_batch_size integer,
    p_lease_seconds integer
)
RETURNS TABLE (
    outbox_id uuid,
    event_id uuid,
    site_id uuid,
    command_id uuid,
    aggregate_kind text,
    event_type text,
    schema_version integer,
    payload jsonb,
    available_at timestamptz,
    lease_until timestamptz,
    attempt_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF p_tenant_id IS NULL
       OR p_worker_key IS NULL
       OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,63}$'
       OR p_batch_size IS NULL OR p_batch_size < 1 OR p_batch_size > 100
       OR p_lease_seconds IS NULL OR p_lease_seconds < 1 OR p_lease_seconds > 300
    THEN
        RAISE EXCEPTION 'invalid_outbox_claim_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', '', true);

    PERFORM 1
      FROM control.tenant_directory AS directory_row
      JOIN app.tenants AS tenant_row
        ON tenant_row.tenant_id = directory_row.tenant_id
     WHERE directory_row.tenant_id = p_tenant_id
       AND directory_row.lifecycle = 'active'
       AND tenant_row.lifecycle = 'active'
     FOR SHARE OF directory_row, tenant_row;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH candidates AS (
        SELECT pending.id
          FROM app.outbox AS pending
         WHERE pending.tenant_id = p_tenant_id
           AND pending.delivered_at IS NULL
           AND pending.available_at <= statement_timestamp()
           AND (pending.lease_until IS NULL OR pending.lease_until <= statement_timestamp())
           AND pending.attempt_count < 2147483647
         ORDER BY pending.available_at, pending.id
         FOR UPDATE SKIP LOCKED
         LIMIT p_batch_size
    ), claimed AS (
        UPDATE app.outbox AS pending
           SET lease_owner = p_worker_key,
               lease_until = statement_timestamp() + make_interval(secs => p_lease_seconds),
               attempt_count = pending.attempt_count + 1
          FROM candidates
         WHERE pending.tenant_id = p_tenant_id
           AND pending.id = candidates.id
        RETURNING pending.id, pending.event_id, pending.site_id,
                  pending.aggregate_id, pending.aggregate_kind, pending.event_type,
                  pending.schema_version, pending.payload,
                  pending.available_at, pending.lease_until,
                  pending.attempt_count
    )
    SELECT claimed.id, claimed.event_id, claimed.site_id,
           claimed.aggregate_id, claimed.aggregate_kind, claimed.event_type,
           claimed.schema_version, claimed.payload,
           claimed.available_at, claimed.lease_until,
           claimed.attempt_count
      FROM claimed
     ORDER BY claimed.available_at, claimed.id;
END;
$$;

CREATE FUNCTION control.mark_outbox_delivered(
    p_tenant_id uuid,
    p_outbox_id uuid,
    p_worker_key text,
    p_attempt_count integer
)
RETURNS TABLE (delivery_time timestamptz, outcome text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_delivered_at timestamptz;
BEGIN
    IF p_tenant_id IS NULL OR p_outbox_id IS NULL
       OR p_worker_key IS NULL
       OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,63}$'
       OR p_attempt_count IS NULL OR p_attempt_count < 1
    THEN
        RAISE EXCEPTION 'invalid_outbox_delivery_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', '', true);

    PERFORM 1 FROM control.tenant_directory AS directory_row
     WHERE directory_row.tenant_id = p_tenant_id
     FOR KEY SHARE OF directory_row;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::timestamptz, 'lease_lost'::text;
        RETURN;
    END IF;

    UPDATE app.outbox AS pending
       SET delivered_at = clock_timestamp(),
           lease_owner = NULL,
           lease_until = NULL
     WHERE pending.tenant_id = p_tenant_id
       AND pending.id = p_outbox_id
       AND pending.delivered_at IS NULL
       AND pending.lease_owner = p_worker_key
       AND pending.lease_until > statement_timestamp()
       AND pending.attempt_count = p_attempt_count
    RETURNING pending.delivered_at INTO v_delivered_at;

    IF v_delivered_at IS NULL THEN
        RETURN QUERY SELECT NULL::timestamptz, 'lease_lost'::text;
        RETURN;
    END IF;
    RETURN QUERY SELECT v_delivered_at, 'delivered'::text;
END;
$$;

CREATE FUNCTION control.reschedule_outbox(
    p_tenant_id uuid,
    p_outbox_id uuid,
    p_worker_key text,
    p_attempt_count integer,
    p_delay_seconds integer
)
RETURNS TABLE (next_available_at timestamptz, outcome text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_available_at timestamptz;
BEGIN
    IF p_tenant_id IS NULL OR p_outbox_id IS NULL
       OR p_worker_key IS NULL
       OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,63}$'
       OR p_attempt_count IS NULL OR p_attempt_count < 1
       OR p_delay_seconds IS NULL OR p_delay_seconds < 1 OR p_delay_seconds > 3600
    THEN
        RAISE EXCEPTION 'invalid_outbox_reschedule_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', '', true);

    PERFORM 1 FROM control.tenant_directory AS directory_row
     WHERE directory_row.tenant_id = p_tenant_id
     FOR KEY SHARE OF directory_row;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::timestamptz, 'lease_lost'::text;
        RETURN;
    END IF;

    UPDATE app.outbox AS pending
       SET available_at = clock_timestamp() + make_interval(secs => p_delay_seconds),
           lease_owner = NULL,
           lease_until = NULL
     WHERE pending.tenant_id = p_tenant_id
       AND pending.id = p_outbox_id
       AND pending.delivered_at IS NULL
       AND pending.lease_owner = p_worker_key
       AND pending.lease_until > statement_timestamp()
       AND pending.attempt_count = p_attempt_count
    RETURNING pending.available_at INTO v_available_at;

    IF v_available_at IS NULL THEN
        RETURN QUERY SELECT NULL::timestamptz, 'lease_lost'::text;
        RETURN;
    END IF;
    RETURN QUERY SELECT v_available_at, 'rescheduled'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.claim_outbox_batch(uuid, text, integer, integer)
FROM PUBLIC;
REVOKE ALL ON FUNCTION control.mark_outbox_delivered(uuid, uuid, text, integer)
FROM PUBLIC;
REVOKE ALL ON FUNCTION control.reschedule_outbox(uuid, uuid, text, integer, integer)
FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.claim_outbox_batch(uuid, text, integer, integer)
TO signal_scheduler;
GRANT EXECUTE ON FUNCTION control.mark_outbox_delivered(uuid, uuid, text, integer)
TO signal_scheduler;
GRANT EXECUTE ON FUNCTION control.reschedule_outbox(uuid, uuid, text, integer, integer)
TO signal_scheduler;

REVOKE SELECT ON app.outbox FROM signal_scheduler;
REVOKE INSERT ON app.outbox FROM signal_api;
GRANT INSERT (
    tenant_id, site_id, id, event_id, aggregate_kind,
    aggregate_id, event_type, schema_version, payload
) ON app.outbox TO signal_api;
