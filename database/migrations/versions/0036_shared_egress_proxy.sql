ALTER TABLE control.admission_leases
    DROP CONSTRAINT admission_leases_workload_id_check,
    ADD CONSTRAINT admission_leases_workload_id_check CHECK (
        workload_id ~ '^crawl:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        OR workload_id ~ '^egress:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    DROP CONSTRAINT admission_leases_permit_kind_check,
    ADD CONSTRAINT admission_leases_permit_kind_check CHECK (
        permit_kind IN ('robots', 'html_navigation', 'shared_egress')
    );

CREATE TABLE app.egress_operations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    worker_key text NOT NULL CHECK (worker_key ~ '^[a-z][a-z0-9_.:-]{0,127}$'),
    purpose text NOT NULL CHECK (purpose IN ('crawl', 'browser', 'connector', 'model')),
    method text NOT NULL CHECK (method IN ('GET', 'HEAD', 'POST')),
    request_url text NOT NULL,
    origin text NOT NULL,
    request_sha256 bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
    request_body_sha256 bytea CHECK (request_body_sha256 IS NULL OR octet_length(request_body_sha256) = 32),
    request_bytes integer NOT NULL CHECK (request_bytes BETWEEN 0 AND 1048576),
    max_response_bytes integer NOT NULL CHECK (max_response_bytes BETWEEN 1 AND 5242880),
    credentialed boolean NOT NULL,
    robots_snapshot_id uuid NOT NULL,
    robots_reason text NOT NULL CHECK (robots_reason IN ('allowed_by_rules', 'robots_not_found')),
    origin_permit_id uuid NOT NULL,
    state text NOT NULL DEFAULT 'dispatched' CHECK (state IN ('dispatched', 'observed', 'failed')),
    dispatched_at timestamptz NOT NULL,
    finished_at timestamptz,
    network_outcome text CHECK (network_outcome IN (
        'fetched', 'redirect_rejected', 'unsupported_encoding',
        'unsupported_media_type', 'body_limit', 'policy_rejected', 'transport_error'
    )),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    response_headers jsonb,
    response_sha256 bytea CHECK (response_sha256 IS NULL OR octet_length(response_sha256) = 32),
    response_bytes integer CHECK (response_bytes BETWEEN 0 AND 5242880),
    media_type text CHECK (media_type IS NULL OR control.valid_artifact_media_type(media_type)),
    resolved_address inet CHECK (
        resolved_address IS NULL OR control.valid_crawl_public_address(resolved_address)
    ),
    completion_kind text CHECK (completion_kind IN (
        'success', 'rate_limited', 'service_unavailable', 'transport_error', 'cancelled'
    )),
    observed_latency_ms integer CHECK (observed_latency_ms BETWEEN 0 AND 120000),
    retry_after_ms integer CHECK (retry_after_ms BETWEEN 1000 AND 86400000),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, robots_snapshot_id)
        REFERENCES app.robots_snapshots (tenant_id, site_id, id),
    FOREIGN KEY (origin_permit_id) REFERENCES control.admission_leases (id),
    CHECK (origin_permit_id = id),
    CHECK (control.valid_crawl_url_identity(
        request_url, request_url, request_url, origin, 1
    )),
    CHECK ((method IN ('GET', 'HEAD') AND request_bytes = 0 AND request_body_sha256 IS NULL)
        OR (method = 'POST' AND request_body_sha256 IS NOT NULL)),
    CHECK ((purpose IN ('crawl', 'browser') AND method IN ('GET', 'HEAD') AND NOT credentialed)
        OR purpose IN ('connector', 'model')),
    CHECK (
        (state = 'dispatched' AND finished_at IS NULL AND network_outcome IS NULL
            AND http_status IS NULL AND response_headers IS NULL AND response_sha256 IS NULL
            AND response_bytes IS NULL AND media_type IS NULL AND resolved_address IS NULL
            AND completion_kind IS NULL AND observed_latency_ms IS NULL
            AND retry_after_ms IS NULL)
        OR
        (state IN ('observed', 'failed') AND finished_at >= dispatched_at
            AND network_outcome IS NOT NULL AND response_headers IS NOT NULL
            AND response_bytes IS NOT NULL AND completion_kind IS NOT NULL
            AND observed_latency_ms IS NOT NULL)
    ),
    CHECK (
        (network_outcome IS NULL)
        OR (network_outcome IN (
                'fetched', 'redirect_rejected', 'unsupported_encoding',
                'unsupported_media_type', 'body_limit'
            ) AND state = 'observed' AND http_status IS NOT NULL
            AND resolved_address IS NOT NULL)
        OR (network_outcome IN ('policy_rejected', 'transport_error')
            AND state = 'failed' AND http_status IS NULL AND resolved_address IS NULL)
    ),
    CHECK (
        network_outcome IS NULL
        OR (network_outcome = 'fetched' AND response_sha256 IS NOT NULL)
        OR (network_outcome <> 'fetched' AND response_sha256 IS NULL AND response_bytes = 0)
    )
);

CREATE INDEX egress_operations_site_dispatched
ON app.egress_operations (tenant_id, site_id, dispatched_at DESC, id DESC);
CREATE INDEX egress_operations_unknown
ON app.egress_operations (dispatched_at, tenant_id, id)
WHERE state = 'dispatched';

CREATE FUNCTION app.guard_egress_operation_mutation() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_egress_operation' USING ERRCODE = '55000';
    END IF;
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.crawl_run_id IS DISTINCT FROM NEW.crawl_run_id
       OR OLD.worker_key IS DISTINCT FROM NEW.worker_key
       OR OLD.purpose IS DISTINCT FROM NEW.purpose
       OR OLD.method IS DISTINCT FROM NEW.method
       OR OLD.request_url IS DISTINCT FROM NEW.request_url
       OR OLD.origin IS DISTINCT FROM NEW.origin
       OR OLD.request_sha256 IS DISTINCT FROM NEW.request_sha256
       OR OLD.request_body_sha256 IS DISTINCT FROM NEW.request_body_sha256
       OR OLD.request_bytes IS DISTINCT FROM NEW.request_bytes
       OR OLD.max_response_bytes IS DISTINCT FROM NEW.max_response_bytes
       OR OLD.credentialed IS DISTINCT FROM NEW.credentialed
       OR OLD.robots_snapshot_id IS DISTINCT FROM NEW.robots_snapshot_id
       OR OLD.robots_reason IS DISTINCT FROM NEW.robots_reason
       OR OLD.origin_permit_id IS DISTINCT FROM NEW.origin_permit_id
       OR OLD.dispatched_at IS DISTINCT FROM NEW.dispatched_at
       OR OLD.state <> 'dispatched'
       OR NEW.state NOT IN ('observed', 'failed')
    THEN
        RAISE EXCEPTION 'immutable_egress_operation' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION app.guard_egress_operation_mutation() FROM PUBLIC;
CREATE TRIGGER egress_operations_guard
BEFORE UPDATE OR DELETE ON app.egress_operations
FOR EACH ROW EXECUTE FUNCTION app.guard_egress_operation_mutation();

ALTER TABLE app.egress_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.egress_operations FORCE ROW LEVEL SECURITY;
CREATE POLICY egress_operations_scope ON app.egress_operations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.begin_shared_egress_operation(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_worker_key text,
    p_operation_id uuid,
    p_purpose text,
    p_method text,
    p_request_url text,
    p_origin text,
    p_request_sha256 bytea,
    p_request_body_sha256 bytea,
    p_request_bytes integer,
    p_max_response_bytes integer,
    p_credentialed boolean,
    p_robots_snapshot_id uuid,
    p_robots_reason text,
    p_profile_version integer,
    p_min_delay_ms integer,
    p_lease_seconds integer
)
RETURNS TABLE (
    operation_id uuid,
    bucket_id uuid,
    admitted_origin text,
    profile_version integer,
    workload_id text,
    permit_kind text,
    min_delay_ms integer,
    requested_lease_seconds integer,
    issued_at timestamptz,
    expires_at timestamptz,
    authority_fingerprint bytea,
    operation_state text,
    finished_at timestamptz,
    network_outcome text,
    http_status integer,
    response_headers jsonb,
    response_sha256 bytea,
    response_bytes integer,
    media_type text,
    resolved_address inet,
    completion_kind text,
    observed_latency_ms integer,
    retry_after_ms integer,
    available_at timestamptz,
    active_in_flight integer,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_workload_id text;
    v_authority_fingerprint bytea;
    v_existing record;
    v_run record;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_lease control.admission_leases%%ROWTYPE;
    v_inserted_id uuid;
    v_expires_at timestamptz;
    v_available_at timestamptz;
BEGIN
    v_workload_id := 'egress:' || p_crawl_run_id::text || ':' || p_operation_id::text;
    v_authority_fingerprint := sha256(convert_to(
        p_tenant_id::text || ':' || p_site_id::text || ':' || p_crawl_run_id::text || ':' ||
        p_worker_key || ':' || p_operation_id::text || ':' || encode(p_request_sha256, 'hex') ||
        ':' || p_robots_snapshot_id::text,
        'UTF8'
    ));
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_operation_id IS NULL OR p_operation_id::text !~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_worker_key IS NULL OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_purpose NOT IN ('crawl', 'browser', 'connector', 'model')
       OR p_method NOT IN ('GET', 'HEAD', 'POST')
       OR p_request_url IS NULL OR p_origin IS NULL
       OR control.valid_crawl_url_identity(
            p_request_url, p_request_url, p_request_url, p_origin, 1
          ) IS NOT TRUE
       OR p_request_sha256 IS NULL OR octet_length(p_request_sha256) <> 32
       OR p_request_bytes IS NULL OR p_request_bytes NOT BETWEEN 0 AND 1048576
       OR p_max_response_bytes IS NULL OR p_max_response_bytes NOT BETWEEN 1 AND 5242880
       OR p_credentialed IS NULL
       OR p_robots_snapshot_id IS NULL
       OR p_robots_reason NOT IN ('allowed_by_rules', 'robots_not_found')
       OR p_profile_version IS DISTINCT FROM 1
       OR p_min_delay_ms IS NULL OR p_min_delay_ms NOT BETWEEN 1000 AND 60000
       OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 120
       OR (p_method IN ('GET', 'HEAD')
            AND (p_request_bytes <> 0 OR p_request_body_sha256 IS NOT NULL))
       OR (p_method = 'POST'
            AND (p_request_body_sha256 IS NULL OR octet_length(p_request_body_sha256) <> 32))
       OR (p_purpose IN ('crawl', 'browser')
            AND (p_method = 'POST' OR p_credentialed))
    THEN
        RAISE EXCEPTION 'invalid_shared_egress_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT operation.*, lease.bucket_id, lease.workload_id, lease.permit_kind,
           lease.min_delay_ms, lease.requested_lease_seconds, lease.issued_at,
           lease.expires_at, lease.authority_fingerprint,
           bucket.profile_version AS bucket_profile_version,
           bucket.next_allowed_at, bucket.degraded_until, bucket.in_flight_count
      INTO v_existing
      FROM app.egress_operations AS operation
      JOIN control.admission_leases AS lease ON lease.id = operation.origin_permit_id
      JOIN control.origin_buckets AS bucket ON bucket.id = lease.bucket_id
     WHERE operation.tenant_id = p_tenant_id
       AND operation.site_id = p_site_id
       AND operation.id = p_operation_id
     FOR UPDATE OF operation;
    IF FOUND THEN
        IF v_existing.crawl_run_id IS DISTINCT FROM p_crawl_run_id
           OR v_existing.worker_key IS DISTINCT FROM p_worker_key
           OR v_existing.purpose IS DISTINCT FROM p_purpose
           OR v_existing.method IS DISTINCT FROM p_method
           OR v_existing.request_url IS DISTINCT FROM p_request_url
           OR v_existing.origin IS DISTINCT FROM p_origin
           OR v_existing.request_sha256 IS DISTINCT FROM p_request_sha256
           OR v_existing.request_body_sha256 IS DISTINCT FROM p_request_body_sha256
           OR v_existing.request_bytes IS DISTINCT FROM p_request_bytes
           OR v_existing.max_response_bytes IS DISTINCT FROM p_max_response_bytes
           OR v_existing.credentialed IS DISTINCT FROM p_credentialed
           OR v_existing.robots_snapshot_id IS DISTINCT FROM p_robots_snapshot_id
           OR v_existing.robots_reason IS DISTINCT FROM p_robots_reason
           OR v_existing.bucket_profile_version IS DISTINCT FROM p_profile_version
           OR v_existing.workload_id IS DISTINCT FROM v_workload_id
           OR v_existing.permit_kind IS DISTINCT FROM 'shared_egress'
           OR v_existing.min_delay_ms IS DISTINCT FROM p_min_delay_ms
           OR v_existing.requested_lease_seconds IS DISTINCT FROM p_lease_seconds
           OR v_existing.authority_fingerprint IS DISTINCT FROM v_authority_fingerprint
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
                NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
                NULL::timestamptz, NULL::bytea, NULL::text, NULL::timestamptz,
                NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
                NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
                NULL::timestamptz, NULL::integer, NULL::boolean,
                'operation_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.bucket_id, v_existing.origin,
            v_existing.bucket_profile_version, v_existing.workload_id,
            v_existing.permit_kind, v_existing.min_delay_ms,
            v_existing.requested_lease_seconds, v_existing.issued_at,
            v_existing.expires_at, v_existing.authority_fingerprint,
            v_existing.state, v_existing.finished_at, v_existing.network_outcome,
            v_existing.http_status, v_existing.response_headers,
            v_existing.response_sha256, v_existing.response_bytes,
            v_existing.media_type, v_existing.resolved_address,
            v_existing.completion_kind, v_existing.observed_latency_ms,
            v_existing.retry_after_ms,
            GREATEST(v_existing.next_allowed_at,
                COALESCE(v_existing.degraded_until, v_existing.next_allowed_at)),
            v_existing.in_flight_count, true,
            CASE WHEN v_existing.state = 'dispatched'
                THEN 'dispatch_unknown'::text ELSE 'replayed'::text END;
        RETURN;
    END IF;

    SELECT run.started_at, run.limits_snapshot, snapshot.expires_at AS snapshot_expires_at
      INTO v_run
      FROM app.crawl_runs AS run
      JOIN app.workflow_refs AS workflow
        ON workflow.tenant_id = run.tenant_id
       AND workflow.site_id = run.site_id
       AND workflow.command_id = run.command_id
      JOIN app.robots_snapshots AS snapshot
        ON snapshot.tenant_id = run.tenant_id
       AND snapshot.site_id = run.site_id
       AND snapshot.crawl_run_id = run.id
       AND snapshot.id = p_robots_snapshot_id
      JOIN control.tenant_directory AS directory ON directory.tenant_id = run.tenant_id
      JOIN app.tenants AS tenant ON tenant.tenant_id = run.tenant_id
      JOIN app.sites AS site
        ON site.tenant_id = run.tenant_id AND site.id = run.site_id
     WHERE run.tenant_id = p_tenant_id
       AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id
       AND run.status = 'running'
       AND workflow.state_projection = 'running'
       AND directory.lifecycle = 'active'
       AND tenant.lifecycle = 'active'
       AND site.state <> 'archived'
       AND snapshot.origin = p_origin
       AND snapshot.fetch_profile_hash = run.fetch_profile_hash
       AND snapshot.expires_at > v_now
       AND (
            (p_robots_reason = 'robots_not_found'
                AND snapshot.decision_status = 'allow_missing')
            OR (p_robots_reason = 'allowed_by_rules'
                AND snapshot.decision_status = 'rules')
       )
       AND EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(
                run.scope_snapshot->'allowed_origins'
            ) AS allowed(value) WHERE allowed.value = p_origin
       )
     FOR SHARE OF run, workflow, snapshot, directory, tenant, site;
    IF NOT FOUND
       OR v_now >= v_run.started_at + make_interval(
            secs => (v_run.limits_snapshot->>'max_duration_seconds')::integer
          )
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::bytea, NULL::text, NULL::timestamptz,
            NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
            NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
            NULL::timestamptz, NULL::integer, false, 'authority_unavailable'::text;
        RETURN;
    END IF;

    INSERT INTO control.origin_buckets (
        origin, profile_version, request_tokens, in_flight_count,
        last_refill_at, next_allowed_at, created_at, updated_at
    ) VALUES (p_origin, p_profile_version, 1, 0, v_now, v_now, v_now, v_now)
    ON CONFLICT ON CONSTRAINT origin_buckets_origin_profile DO NOTHING;
    SELECT * INTO v_bucket
      FROM control.origin_buckets AS bucket
     WHERE bucket.origin = p_origin AND bucket.profile_version = p_profile_version
     FOR UPDATE;
    v_now := clock_timestamp();

    UPDATE control.admission_leases AS lease
       SET released_at = v_now, completion_kind = 'lease_expired'
     WHERE lease.bucket_id = v_bucket.id
       AND lease.released_at IS NULL
       AND lease.expires_at <= v_now;
    UPDATE control.origin_buckets AS bucket
       SET in_flight_count = (
               SELECT count(*)::integer FROM control.admission_leases AS lease
                WHERE lease.bucket_id = v_bucket.id AND lease.released_at IS NULL
           ),
           last_refill_at = CASE
               WHEN bucket.request_tokens = 0 AND bucket.next_allowed_at <= v_now
               THEN v_now ELSE bucket.last_refill_at
           END,
           request_tokens = CASE
               WHEN bucket.next_allowed_at <= v_now THEN 1 ELSE bucket.request_tokens
           END,
           degraded_until = CASE
               WHEN bucket.degraded_until <= v_now THEN NULL ELSE bucket.degraded_until
           END,
           updated_at = v_now
     WHERE bucket.id = v_bucket.id
     RETURNING * INTO v_bucket;

    SELECT * INTO v_lease
      FROM control.admission_leases AS lease
     WHERE lease.bucket_id = v_bucket.id
       AND lease.workload_id = v_workload_id
       AND lease.permit_kind = 'shared_egress';
    IF FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::bytea, NULL::text, NULL::timestamptz,
            NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
            NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
            NULL::timestamptz, v_bucket.in_flight_count, NULL::boolean,
            'operation_conflict'::text;
        RETURN;
    END IF;
    IF v_bucket.degraded_until > v_now THEN
        v_available_at := v_bucket.degraded_until;
    ELSIF v_bucket.in_flight_count >= 1 THEN
        SELECT min(lease.expires_at) INTO v_available_at
          FROM control.admission_leases AS lease
         WHERE lease.bucket_id = v_bucket.id AND lease.released_at IS NULL;
        v_available_at := GREATEST(v_available_at, v_bucket.next_allowed_at);
    ELSIF v_bucket.request_tokens = 0 OR v_bucket.next_allowed_at > v_now THEN
        v_available_at := v_bucket.next_allowed_at;
    END IF;
    IF v_available_at IS NOT NULL THEN
        RETURN QUERY SELECT NULL::uuid, v_bucket.id, p_origin, p_profile_version,
            NULL::text, 'shared_egress'::text, p_min_delay_ms, p_lease_seconds,
            NULL::timestamptz, NULL::timestamptz, NULL::bytea, NULL::text,
            NULL::timestamptz, NULL::text, NULL::integer, NULL::jsonb, NULL::bytea,
            NULL::integer, NULL::text, NULL::inet, NULL::text, NULL::integer,
            NULL::integer, v_available_at, v_bucket.in_flight_count, false,
            CASE WHEN v_bucket.degraded_until > v_now THEN 'deferred_backoff'::text
                 WHEN v_bucket.in_flight_count >= 1 THEN 'deferred_in_flight'::text
                 ELSE 'deferred_politeness'::text END;
        RETURN;
    END IF;

    v_expires_at := LEAST(
        v_now + make_interval(secs => p_lease_seconds),
        v_run.snapshot_expires_at,
        v_run.started_at + make_interval(
            secs => (v_run.limits_snapshot->>'max_duration_seconds')::integer
        )
    );
    IF v_expires_at <= v_now THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::bytea, NULL::text, NULL::timestamptz,
            NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
            NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
            NULL::timestamptz, NULL::integer, false, 'authority_unavailable'::text;
        RETURN;
    END IF;
    INSERT INTO control.admission_leases (
        id, bucket_id, workload_id, permit_kind, authority_fingerprint,
        min_delay_ms, requested_lease_seconds, issued_at, expires_at
    ) VALUES (
        p_operation_id, v_bucket.id, v_workload_id, 'shared_egress',
        v_authority_fingerprint, p_min_delay_ms, p_lease_seconds, v_now, v_expires_at
    ) ON CONFLICT DO NOTHING
    RETURNING id INTO v_inserted_id;
    IF v_inserted_id IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::bytea, NULL::text, NULL::timestamptz,
            NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
            NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
            NULL::timestamptz, v_bucket.in_flight_count, NULL::boolean,
            'operation_conflict'::text;
        RETURN;
    END IF;
    INSERT INTO app.egress_operations (
        tenant_id, site_id, id, crawl_run_id, worker_key, purpose, method,
        request_url, origin, request_sha256, request_body_sha256, request_bytes,
        max_response_bytes, credentialed, robots_snapshot_id, robots_reason,
        origin_permit_id, dispatched_at
    ) VALUES (
        p_tenant_id, p_site_id, p_operation_id, p_crawl_run_id, p_worker_key,
        p_purpose, p_method, p_request_url, p_origin, p_request_sha256,
        p_request_body_sha256, p_request_bytes, p_max_response_bytes,
        p_credentialed, p_robots_snapshot_id, p_robots_reason, p_operation_id, v_now
    );
    UPDATE control.origin_buckets AS bucket
       SET request_tokens = 0,
           in_flight_count = 1,
           next_allowed_at = GREATEST(
               bucket.next_allowed_at,
               v_now + p_min_delay_ms * interval '1 millisecond'
           ),
           updated_at = v_now
     WHERE bucket.id = v_bucket.id
     RETURNING * INTO v_bucket;
    RETURN QUERY SELECT p_operation_id, v_bucket.id, p_origin, p_profile_version,
        v_workload_id, 'shared_egress'::text, p_min_delay_ms, p_lease_seconds,
        v_now, v_expires_at, v_authority_fingerprint, 'dispatched'::text,
        NULL::timestamptz, NULL::text, NULL::integer, NULL::jsonb, NULL::bytea,
        NULL::integer, NULL::text, NULL::inet, NULL::text, NULL::integer,
        NULL::integer, v_expires_at, v_bucket.in_flight_count, false, 'admitted'::text;
END;
$$;

CREATE FUNCTION control.finish_shared_egress_operation(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_worker_key text,
    p_operation_id uuid,
    p_bucket_id uuid,
    p_origin text,
    p_profile_version integer,
    p_request_sha256 bytea,
    p_robots_snapshot_id uuid,
    p_network_outcome text,
    p_http_status integer,
    p_response_headers jsonb,
    p_response_sha256 bytea,
    p_response_bytes integer,
    p_media_type text,
    p_resolved_address inet,
    p_completion_kind text,
    p_observed_latency_ms integer,
    p_retry_after_ms integer
)
RETURNS TABLE (
    operation_id uuid,
    operation_state text,
    finished_at timestamptz,
    network_outcome text,
    http_status integer,
    response_headers jsonb,
    response_sha256 bytea,
    response_bytes integer,
    media_type text,
    resolved_address inet,
    completion_kind text,
    observed_latency_ms integer,
    retry_after_ms integer,
    next_allowed_at timestamptz,
    degraded_until timestamptz,
    active_in_flight integer,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_workload_id text;
    v_authority_fingerprint bytea;
    v_operation app.egress_operations%%ROWTYPE;
    v_lease control.admission_leases%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_state text;
    v_delay_ms integer;
    v_backoff_until timestamptz;
BEGIN
    v_workload_id := 'egress:' || p_crawl_run_id::text || ':' || p_operation_id::text;
    v_authority_fingerprint := sha256(convert_to(
        p_tenant_id::text || ':' || p_site_id::text || ':' || p_crawl_run_id::text || ':' ||
        p_worker_key || ':' || p_operation_id::text || ':' || encode(p_request_sha256, 'hex') ||
        ':' || p_robots_snapshot_id::text,
        'UTF8'
    ));
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_operation_id IS NULL OR p_bucket_id IS NULL
       OR p_worker_key IS NULL OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_origin IS NULL OR p_profile_version IS DISTINCT FROM 1
       OR p_request_sha256 IS NULL OR octet_length(p_request_sha256) <> 32
       OR p_robots_snapshot_id IS NULL
       OR p_network_outcome NOT IN (
            'fetched', 'redirect_rejected', 'unsupported_encoding',
            'unsupported_media_type', 'body_limit', 'policy_rejected', 'transport_error'
       )
       OR p_response_headers IS NULL
       OR control.valid_fetch_headers(p_response_headers) IS NOT TRUE
       OR p_response_bytes IS NULL OR p_response_bytes NOT BETWEEN 0 AND 5242880
       OR (p_media_type IS NOT NULL
            AND control.valid_artifact_media_type(p_media_type) IS NOT TRUE)
       OR p_completion_kind NOT IN (
            'success', 'rate_limited', 'service_unavailable', 'transport_error', 'cancelled'
       )
       OR p_observed_latency_ms IS NULL OR p_observed_latency_ms NOT BETWEEN 0 AND 120000
       OR (p_network_outcome IN (
                'fetched', 'redirect_rejected', 'unsupported_encoding',
                'unsupported_media_type', 'body_limit'
            ) AND (p_http_status IS NULL OR p_resolved_address IS NULL))
       OR (p_network_outcome IN ('policy_rejected', 'transport_error')
            AND (p_http_status IS NOT NULL OR p_resolved_address IS NOT NULL
                OR p_media_type IS NOT NULL OR p_response_headers <> '{}'::jsonb))
       OR (p_network_outcome = 'fetched'
            AND (p_response_sha256 IS NULL OR octet_length(p_response_sha256) <> 32))
       OR (p_network_outcome <> 'fetched'
            AND (p_response_sha256 IS NOT NULL OR p_response_bytes <> 0))
       OR (p_network_outcome = 'transport_error' AND p_completion_kind <> 'transport_error')
       OR (p_network_outcome = 'policy_rejected' AND p_completion_kind <> 'cancelled')
       OR (p_http_status = 429 AND p_completion_kind <> 'rate_limited')
       OR (p_http_status = 503 AND p_completion_kind <> 'service_unavailable')
       OR (p_http_status IS NOT NULL AND p_http_status NOT IN (429, 503)
            AND p_completion_kind <> 'success')
       OR (p_completion_kind = 'rate_limited'
            AND (p_retry_after_ms IS NULL OR p_retry_after_ms NOT BETWEEN 1000 AND 86400000))
       OR (p_completion_kind = 'service_unavailable'
            AND p_retry_after_ms IS NOT NULL
            AND p_retry_after_ms NOT BETWEEN 1000 AND 86400000)
       OR (p_completion_kind IN ('success', 'transport_error', 'cancelled')
            AND p_retry_after_ms IS NOT NULL)
    THEN
        RAISE EXCEPTION 'invalid_shared_egress_completion' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_bucket
      FROM control.origin_buckets AS bucket
     WHERE bucket.id = p_bucket_id
     FOR UPDATE;
    SELECT operation.* INTO v_operation
      FROM app.egress_operations AS operation
     WHERE operation.tenant_id = p_tenant_id
       AND operation.site_id = p_site_id
       AND operation.id = p_operation_id
     FOR UPDATE;
    SELECT * INTO v_lease
      FROM control.admission_leases AS lease
     WHERE lease.id = p_operation_id;
    v_now := clock_timestamp();
    IF v_bucket.id IS NULL OR v_operation.id IS NULL OR v_lease.id IS NULL
       OR v_operation.crawl_run_id IS DISTINCT FROM p_crawl_run_id
       OR v_operation.worker_key IS DISTINCT FROM p_worker_key
       OR v_operation.origin IS DISTINCT FROM p_origin
       OR v_operation.request_sha256 IS DISTINCT FROM p_request_sha256
       OR v_operation.robots_snapshot_id IS DISTINCT FROM p_robots_snapshot_id
       OR v_operation.origin_permit_id IS DISTINCT FROM p_operation_id
       OR v_lease.bucket_id IS DISTINCT FROM p_bucket_id
       OR v_bucket.origin IS DISTINCT FROM p_origin
       OR v_bucket.profile_version IS DISTINCT FROM p_profile_version
       OR v_lease.workload_id IS DISTINCT FROM v_workload_id
       OR v_lease.permit_kind IS DISTINCT FROM 'shared_egress'
       OR v_lease.authority_fingerprint IS DISTINCT FROM v_authority_fingerprint
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::timestamptz,
            NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
            NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
            NULL::timestamptz, NULL::timestamptz, NULL::integer, NULL::boolean,
            'operation_conflict'::text;
        RETURN;
    END IF;

    IF v_operation.state <> 'dispatched' THEN
        IF v_operation.network_outcome IS NOT DISTINCT FROM p_network_outcome
           AND v_operation.http_status IS NOT DISTINCT FROM p_http_status
           AND v_operation.response_headers IS NOT DISTINCT FROM p_response_headers
           AND v_operation.response_sha256 IS NOT DISTINCT FROM p_response_sha256
           AND v_operation.response_bytes IS NOT DISTINCT FROM p_response_bytes
           AND v_operation.media_type IS NOT DISTINCT FROM p_media_type
           AND v_operation.resolved_address IS NOT DISTINCT FROM p_resolved_address
           AND v_operation.completion_kind IS NOT DISTINCT FROM p_completion_kind
           AND v_operation.observed_latency_ms IS NOT DISTINCT FROM p_observed_latency_ms
           AND v_operation.retry_after_ms IS NOT DISTINCT FROM p_retry_after_ms
        THEN
            RETURN QUERY SELECT v_operation.id, v_operation.state,
                v_operation.finished_at, v_operation.network_outcome,
                v_operation.http_status, v_operation.response_headers,
                v_operation.response_sha256, v_operation.response_bytes,
                v_operation.media_type, v_operation.resolved_address,
                v_operation.completion_kind, v_operation.observed_latency_ms,
                v_operation.retry_after_ms, v_bucket.next_allowed_at,
                v_bucket.degraded_until, v_bucket.in_flight_count, true,
                'finished'::text;
        ELSE
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::timestamptz,
                NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
                NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
                NULL::timestamptz, NULL::timestamptz, NULL::integer, NULL::boolean,
                'operation_conflict'::text;
        END IF;
        RETURN;
    END IF;

    IF v_lease.released_at IS NOT NULL OR v_lease.expires_at <= v_now THEN
        IF v_lease.released_at IS NULL THEN
            UPDATE control.admission_leases AS lease
               SET released_at = v_now, completion_kind = 'lease_expired'
             WHERE lease.id = p_operation_id;
            UPDATE control.origin_buckets AS bucket
               SET in_flight_count = (
                       SELECT count(*)::integer FROM control.admission_leases AS lease
                        WHERE lease.bucket_id = v_bucket.id AND lease.released_at IS NULL
                   ),
                   request_tokens = CASE
                       WHEN bucket.next_allowed_at <= v_now THEN 1 ELSE bucket.request_tokens
                   END,
                   updated_at = v_now
             WHERE bucket.id = v_bucket.id
             RETURNING * INTO v_bucket;
        END IF;
        RETURN QUERY SELECT v_operation.id, v_operation.state, NULL::timestamptz,
            NULL::text, NULL::integer, NULL::jsonb, NULL::bytea, NULL::integer,
            NULL::text, NULL::inet, NULL::text, NULL::integer, NULL::integer,
            v_bucket.next_allowed_at, v_bucket.degraded_until,
            v_bucket.in_flight_count, false, 'permit_expired'::text;
        RETURN;
    END IF;

    v_state := CASE WHEN p_network_outcome IN ('policy_rejected', 'transport_error')
        THEN 'failed' ELSE 'observed' END;
    UPDATE app.egress_operations AS operation
       SET state = v_state,
           finished_at = v_now,
           network_outcome = p_network_outcome,
           http_status = p_http_status,
           response_headers = p_response_headers,
           response_sha256 = p_response_sha256,
           response_bytes = p_response_bytes,
           media_type = p_media_type,
           resolved_address = p_resolved_address,
           completion_kind = p_completion_kind,
           observed_latency_ms = p_observed_latency_ms,
           retry_after_ms = p_retry_after_ms
     WHERE operation.tenant_id = p_tenant_id
       AND operation.site_id = p_site_id
       AND operation.id = p_operation_id;
    UPDATE control.admission_leases AS lease
       SET released_at = v_now,
           completion_kind = p_completion_kind,
           observed_latency_ms = p_observed_latency_ms,
           retry_after_ms = p_retry_after_ms
     WHERE lease.id = p_operation_id;

    IF p_completion_kind = 'rate_limited' THEN
        v_delay_ms := p_retry_after_ms;
    ELSIF p_completion_kind = 'service_unavailable' THEN
        v_delay_ms := COALESCE(p_retry_after_ms, 30000);
    ELSIF p_completion_kind = 'transport_error' THEN
        v_delay_ms := 5000;
    ELSIF p_completion_kind = 'success' AND p_observed_latency_ms >= 2000 THEN
        v_delay_ms := LEAST(p_observed_latency_ms, 60000);
    END IF;
    IF v_delay_ms IS NOT NULL THEN
        v_backoff_until := v_now + v_delay_ms * interval '1 millisecond';
    END IF;
    UPDATE control.origin_buckets AS bucket
       SET in_flight_count = (
               SELECT count(*)::integer FROM control.admission_leases AS lease
                WHERE lease.bucket_id = v_bucket.id AND lease.released_at IS NULL
           ),
           next_allowed_at = CASE WHEN v_backoff_until IS NULL
               THEN bucket.next_allowed_at
               ELSE GREATEST(bucket.next_allowed_at, v_backoff_until)
           END,
           degraded_until = CASE
               WHEN p_completion_kind IN ('rate_limited', 'service_unavailable')
               THEN GREATEST(
                   COALESCE(bucket.degraded_until, v_backoff_until), v_backoff_until
               )
               WHEN bucket.degraded_until <= v_now THEN NULL
               ELSE bucket.degraded_until
           END,
           updated_at = v_now
     WHERE bucket.id = v_bucket.id
     RETURNING * INTO v_bucket;

    RETURN QUERY SELECT p_operation_id, v_state, v_now, p_network_outcome,
        p_http_status, p_response_headers, p_response_sha256, p_response_bytes,
        p_media_type, p_resolved_address, p_completion_kind,
        p_observed_latency_ms, p_retry_after_ms, v_bucket.next_allowed_at,
        v_bucket.degraded_until, v_bucket.in_flight_count, false, 'finished'::text;
END;
$$;

REVOKE ALL ON app.egress_operations
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;
REVOKE ALL ON FUNCTION control.begin_shared_egress_operation(
    uuid, uuid, uuid, text, uuid, text, text, text, text, bytea, bytea,
    integer, integer, boolean, uuid, text, integer, integer, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_shared_egress_operation(
    uuid, uuid, uuid, text, uuid, uuid, text, integer, bytea, uuid, text,
    integer, jsonb, bytea, integer, text, inet, text, integer, integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.begin_shared_egress_operation(
    uuid, uuid, uuid, text, uuid, text, text, text, text, bytea, bytea,
    integer, integer, boolean, uuid, text, integer, integer, integer
) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.finish_shared_egress_operation(
    uuid, uuid, uuid, text, uuid, uuid, text, integer, bytea, uuid, text,
    integer, jsonb, bytea, integer, text, inet, text, integer, integer
) TO signal_crawl_admission;
