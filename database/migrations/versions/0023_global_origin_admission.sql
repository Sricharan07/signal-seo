CREATE TABLE control.origin_buckets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    origin text NOT NULL,
    profile_version integer NOT NULL CHECK (profile_version = 1),
    request_tokens bigint NOT NULL DEFAULT 1 CHECK (request_tokens BETWEEN 0 AND 1),
    in_flight_count integer NOT NULL DEFAULT 0 CHECK (in_flight_count BETWEEN 0 AND 1),
    last_refill_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    next_allowed_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    degraded_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT origin_buckets_origin_profile UNIQUE (origin, profile_version),
    CHECK (control.valid_crawl_url_identity(
        origin || '/', origin || '/', origin || '/', origin, 1
    )),
    CHECK (updated_at >= created_at),
    CHECK (last_refill_at >= created_at),
    CHECK (next_allowed_at >= created_at),
    CHECK (degraded_until IS NULL OR degraded_until >= created_at)
);

CREATE INDEX origin_buckets_ready
ON control.origin_buckets (next_allowed_at, degraded_until, id);

CREATE TABLE control.admission_leases (
    id uuid PRIMARY KEY,
    bucket_id uuid NOT NULL REFERENCES control.origin_buckets (id),
    workload_id text NOT NULL CHECK (
        workload_id ~ '^crawl:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    permit_kind text NOT NULL CHECK (permit_kind IN ('robots', 'html_navigation')),
    authority_fingerprint bytea NOT NULL CHECK (octet_length(authority_fingerprint) = 32),
    min_delay_ms integer NOT NULL CHECK (min_delay_ms BETWEEN 1000 AND 60000),
    requested_lease_seconds integer NOT NULL
        CHECK (requested_lease_seconds BETWEEN 1 AND 120),
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    released_at timestamptz,
    completion_kind text CHECK (completion_kind IN (
        'success', 'rate_limited', 'service_unavailable', 'transport_error',
        'cancelled', 'lease_expired'
    )),
    observed_latency_ms integer CHECK (observed_latency_ms BETWEEN 0 AND 120000),
    retry_after_ms integer CHECK (retry_after_ms BETWEEN 1000 AND 86400000),
    UNIQUE (bucket_id, workload_id, permit_kind),
    CHECK (expires_at > issued_at),
    CHECK (expires_at <= issued_at + interval '120 seconds'),
    CHECK (
        (released_at IS NULL AND completion_kind IS NULL
            AND observed_latency_ms IS NULL AND retry_after_ms IS NULL)
        OR
        (released_at IS NOT NULL AND released_at >= issued_at
            AND completion_kind IS NOT NULL)
    ),
    CHECK (
        completion_kind IS NULL
        OR (completion_kind = 'lease_expired'
            AND observed_latency_ms IS NULL AND retry_after_ms IS NULL)
        OR (completion_kind = 'rate_limited'
            AND observed_latency_ms IS NOT NULL AND retry_after_ms IS NOT NULL)
        OR (completion_kind = 'service_unavailable'
            AND observed_latency_ms IS NOT NULL)
        OR (completion_kind IN ('success', 'transport_error', 'cancelled')
            AND observed_latency_ms IS NOT NULL AND retry_after_ms IS NULL)
    )
);

CREATE INDEX admission_leases_active_expiry
ON control.admission_leases (expires_at, bucket_id, id)
WHERE released_at IS NULL;
CREATE INDEX admission_leases_bucket_history
ON control.admission_leases (bucket_id, issued_at DESC, id);

CREATE FUNCTION control.guard_admission_lease_mutation() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_admission_lease_identity' USING ERRCODE = '55000';
    END IF;
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.bucket_id IS DISTINCT FROM NEW.bucket_id
       OR OLD.workload_id IS DISTINCT FROM NEW.workload_id
       OR OLD.permit_kind IS DISTINCT FROM NEW.permit_kind
       OR OLD.authority_fingerprint IS DISTINCT FROM NEW.authority_fingerprint
       OR OLD.min_delay_ms IS DISTINCT FROM NEW.min_delay_ms
       OR OLD.requested_lease_seconds IS DISTINCT FROM NEW.requested_lease_seconds
       OR OLD.issued_at IS DISTINCT FROM NEW.issued_at
       OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
       OR OLD.released_at IS NOT NULL
    THEN
        RAISE EXCEPTION 'immutable_admission_lease_identity' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION control.guard_admission_lease_mutation() FROM PUBLIC;
CREATE TRIGGER admission_leases_guard
BEFORE UPDATE OR DELETE ON control.admission_leases
FOR EACH ROW EXECUTE FUNCTION control.guard_admission_lease_mutation();

CREATE FUNCTION control.acquire_origin_permit(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_frontier_id uuid,
    p_frontier_lease_id uuid,
    p_worker_key text,
    p_permit_id uuid,
    p_origin text,
    p_profile_version integer,
    p_workload_id text,
    p_permit_kind text,
    p_min_delay_ms integer,
    p_lease_seconds integer
)
RETURNS TABLE (
    permit_id uuid,
    bucket_id uuid,
    admitted_origin text,
    profile_version integer,
    workload_id text,
    permit_kind text,
    min_delay_ms integer,
    requested_lease_seconds integer,
    issued_at timestamptz,
    expires_at timestamptz,
    released_at timestamptz,
    completion_kind text,
    observed_latency_ms integer,
    retry_after_ms integer,
    authority_fingerprint bytea,
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
    v_expected_workload text;
    v_authority_fingerprint bytea;
    v_existing record;
    v_frontier record;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_inserted_id uuid;
    v_expires_at timestamptz;
    v_available_at timestamptz;
BEGIN
    v_expected_workload := 'crawl:' || p_crawl_run_id::text || ':' || p_frontier_lease_id::text;
    v_authority_fingerprint := sha256(convert_to(
        p_tenant_id::text || ':' || p_site_id::text || ':' || p_crawl_run_id::text || ':' ||
        p_frontier_id::text || ':' || p_frontier_lease_id::text || ':' || p_worker_key,
        'UTF8'
    ));
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_frontier_id IS NULL OR p_frontier_lease_id IS NULL OR p_permit_id IS NULL
       OR p_worker_key IS NULL OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_origin IS NULL OR control.valid_crawl_url_identity(
            p_origin || '/', p_origin || '/', p_origin || '/', p_origin, 1
          ) IS NOT TRUE
       OR p_profile_version IS DISTINCT FROM 1
       OR p_workload_id IS DISTINCT FROM v_expected_workload
       OR p_permit_kind IS NULL OR p_permit_kind NOT IN ('robots', 'html_navigation')
       OR p_min_delay_ms IS NULL OR p_min_delay_ms NOT BETWEEN 1000 AND 60000
       OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 120
    THEN
        RAISE EXCEPTION 'invalid_origin_permit_input' USING ERRCODE = '22023';
    END IF;

    SELECT lease.*, bucket.origin, bucket.profile_version AS bucket_profile_version
      INTO v_existing
      FROM control.admission_leases AS lease
      JOIN control.origin_buckets AS bucket ON bucket.id = lease.bucket_id
     WHERE lease.id = p_permit_id;
    IF FOUND THEN
        IF v_existing.origin IS DISTINCT FROM p_origin
           OR v_existing.bucket_profile_version IS DISTINCT FROM p_profile_version
           OR v_existing.workload_id IS DISTINCT FROM p_workload_id
           OR v_existing.permit_kind IS DISTINCT FROM p_permit_kind
           OR v_existing.authority_fingerprint IS DISTINCT FROM v_authority_fingerprint
           OR v_existing.min_delay_ms IS DISTINCT FROM p_min_delay_ms
           OR v_existing.requested_lease_seconds IS DISTINCT FROM p_lease_seconds
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
                NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
                NULL::timestamptz, NULL::timestamptz, NULL::text, NULL::integer,
                NULL::integer, NULL::bytea, NULL::timestamptz, NULL::integer,
                NULL::boolean, 'permit_conflict'::text;
            RETURN;
        END IF;
        IF v_existing.released_at IS NULL THEN
            PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
            PERFORM set_config('signal.site_id', p_site_id::text, true);
            SELECT frontier.lease_until, receipt.expires_at AS receipt_expires_at
              INTO v_frontier
              FROM app.crawl_frontier AS frontier
              JOIN app.crawl_frontier_leases AS receipt
                ON receipt.tenant_id = frontier.tenant_id
               AND receipt.site_id = frontier.site_id
               AND receipt.crawl_run_id = frontier.crawl_run_id
               AND receipt.frontier_id = frontier.id
               AND receipt.id = frontier.lease_id
              JOIN app.urls AS url
                ON url.tenant_id = frontier.tenant_id
               AND url.site_id = frontier.site_id
               AND url.id = frontier.url_id
              JOIN app.crawl_runs AS run
                ON run.tenant_id = frontier.tenant_id
               AND run.site_id = frontier.site_id
               AND run.id = frontier.crawl_run_id
              JOIN app.workflow_refs AS workflow
                ON workflow.tenant_id = run.tenant_id
               AND workflow.site_id = run.site_id
               AND workflow.command_id = run.command_id
              JOIN control.tenant_directory AS directory
                ON directory.tenant_id = run.tenant_id
              JOIN app.tenants AS tenant ON tenant.tenant_id = run.tenant_id
              JOIN app.sites AS site
                ON site.tenant_id = run.tenant_id AND site.id = run.site_id
             WHERE frontier.tenant_id = p_tenant_id
               AND frontier.site_id = p_site_id
               AND frontier.id = p_frontier_id
               AND frontier.crawl_run_id = p_crawl_run_id
               AND frontier.status = 'leased'
               AND frontier.lease_id = p_frontier_lease_id
               AND frontier.lease_owner = p_worker_key
               AND frontier.lease_until > v_now
               AND receipt.expires_at = frontier.lease_until
               AND receipt.expires_at > v_now
               AND url.origin = p_origin
               AND run.status = 'running'
               AND workflow.state_projection = 'running'
               AND directory.lifecycle = 'active'
               AND tenant.lifecycle = 'active'
               AND site.state <> 'archived'
             FOR SHARE OF frontier, receipt, url, run, workflow, directory, tenant, site;
            IF NOT FOUND THEN
                RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
                    NULL::text, NULL::text, NULL::integer, NULL::integer,
                    NULL::timestamptz, NULL::timestamptz, NULL::timestamptz,
                    NULL::text, NULL::integer, NULL::integer, NULL::bytea,
                    NULL::timestamptz, NULL::integer, NULL::boolean,
                    'frontier_unavailable'::text;
                RETURN;
            END IF;
        END IF;
        SELECT * INTO v_bucket
          FROM control.origin_buckets AS bucket
         WHERE bucket.id = v_existing.bucket_id
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
        SELECT lease.*, bucket.origin, bucket.profile_version AS bucket_profile_version
          INTO v_existing
          FROM control.admission_leases AS lease
          JOIN control.origin_buckets AS bucket ON bucket.id = lease.bucket_id
         WHERE lease.id = p_permit_id;
        IF v_existing.released_at IS NULL THEN
            RETURN QUERY SELECT v_existing.id, v_existing.bucket_id, v_existing.origin,
                v_existing.bucket_profile_version, v_existing.workload_id,
                v_existing.permit_kind, v_existing.min_delay_ms,
                v_existing.requested_lease_seconds, v_existing.issued_at,
                v_existing.expires_at, v_existing.released_at,
                v_existing.completion_kind, v_existing.observed_latency_ms,
                v_existing.retry_after_ms, v_existing.authority_fingerprint,
                v_existing.expires_at, v_bucket.in_flight_count, true, 'admitted'::text;
        ELSE
            RETURN QUERY SELECT v_existing.id, v_existing.bucket_id, v_existing.origin,
                v_existing.bucket_profile_version, v_existing.workload_id,
                v_existing.permit_kind, v_existing.min_delay_ms,
                v_existing.requested_lease_seconds, v_existing.issued_at,
                v_existing.expires_at, v_existing.released_at,
                v_existing.completion_kind, v_existing.observed_latency_ms,
                v_existing.retry_after_ms, v_existing.authority_fingerprint,
                GREATEST(v_bucket.next_allowed_at,
                    COALESCE(v_bucket.degraded_until, v_bucket.next_allowed_at)),
                v_bucket.in_flight_count, true, 'replayed'::text;
        END IF;
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT frontier.id, frontier.crawl_run_id, frontier.lease_id, frontier.lease_owner,
           frontier.lease_until, receipt.expires_at AS receipt_expires_at, url.origin
      INTO v_frontier
      FROM app.crawl_frontier AS frontier
      JOIN app.crawl_frontier_leases AS receipt
        ON receipt.tenant_id = frontier.tenant_id
       AND receipt.site_id = frontier.site_id
       AND receipt.crawl_run_id = frontier.crawl_run_id
       AND receipt.frontier_id = frontier.id
       AND receipt.id = frontier.lease_id
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id
       AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
      JOIN app.crawl_runs AS run
        ON run.tenant_id = frontier.tenant_id
       AND run.site_id = frontier.site_id
       AND run.id = frontier.crawl_run_id
      JOIN app.workflow_refs AS workflow
        ON workflow.tenant_id = run.tenant_id
       AND workflow.site_id = run.site_id
       AND workflow.command_id = run.command_id
      JOIN control.tenant_directory AS directory
        ON directory.tenant_id = run.tenant_id
      JOIN app.tenants AS tenant ON tenant.tenant_id = run.tenant_id
      JOIN app.sites AS site
        ON site.tenant_id = run.tenant_id AND site.id = run.site_id
     WHERE frontier.tenant_id = p_tenant_id
       AND frontier.site_id = p_site_id
       AND frontier.id = p_frontier_id
       AND frontier.crawl_run_id = p_crawl_run_id
       AND frontier.status = 'leased'
       AND frontier.lease_id = p_frontier_lease_id
       AND frontier.lease_owner = p_worker_key
       AND frontier.lease_until > v_now
       AND receipt.expires_at = frontier.lease_until
       AND receipt.expires_at > v_now
       AND url.origin = p_origin
       AND run.status = 'running'
       AND workflow.state_projection = 'running'
       AND directory.lifecycle = 'active'
       AND tenant.lifecycle = 'active'
       AND site.state <> 'archived'
       AND EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(
                run.scope_snapshot->'allowed_origins'
            ) AS allowed(value) WHERE allowed.value = p_origin
       )
     FOR SHARE OF frontier, receipt, url, run, workflow, directory, tenant, site;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::text, NULL::integer,
            NULL::integer, NULL::bytea, NULL::timestamptz, NULL::integer,
            NULL::boolean, 'frontier_unavailable'::text;
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
    IF v_frontier.lease_until <= v_now OR v_frontier.receipt_expires_at <= v_now THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::text, NULL::integer,
            NULL::integer, NULL::bytea, NULL::timestamptz, NULL::integer,
            NULL::boolean, 'frontier_unavailable'::text;
        RETURN;
    END IF;

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

    SELECT lease.* INTO v_existing
      FROM control.admission_leases AS lease
     WHERE lease.bucket_id = v_bucket.id
       AND lease.workload_id = p_workload_id
       AND lease.permit_kind = p_permit_kind;
    IF FOUND THEN
        IF v_existing.id IS DISTINCT FROM p_permit_id
           OR v_existing.authority_fingerprint IS DISTINCT FROM v_authority_fingerprint
           OR v_existing.min_delay_ms IS DISTINCT FROM p_min_delay_ms
           OR v_existing.requested_lease_seconds IS DISTINCT FROM p_lease_seconds
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
                NULL::text, NULL::text, NULL::integer, NULL::integer,
                NULL::timestamptz, NULL::timestamptz, NULL::timestamptz,
                NULL::text, NULL::integer, NULL::integer, NULL::bytea,
                NULL::timestamptz, v_bucket.in_flight_count, NULL::boolean,
                'permit_conflict'::text;
        ELSIF v_existing.released_at IS NULL THEN
            RETURN QUERY SELECT v_existing.id, v_existing.bucket_id, p_origin,
                p_profile_version, v_existing.workload_id, v_existing.permit_kind,
                v_existing.min_delay_ms, v_existing.requested_lease_seconds,
                v_existing.issued_at, v_existing.expires_at, v_existing.released_at,
                v_existing.completion_kind, v_existing.observed_latency_ms,
                v_existing.retry_after_ms, v_existing.authority_fingerprint,
                v_existing.expires_at, v_bucket.in_flight_count, true, 'admitted'::text;
        ELSE
            RETURN QUERY SELECT v_existing.id, v_existing.bucket_id, p_origin,
                p_profile_version, v_existing.workload_id, v_existing.permit_kind,
                v_existing.min_delay_ms, v_existing.requested_lease_seconds,
                v_existing.issued_at, v_existing.expires_at, v_existing.released_at,
                v_existing.completion_kind, v_existing.observed_latency_ms,
                v_existing.retry_after_ms, v_existing.authority_fingerprint,
                GREATEST(v_bucket.next_allowed_at,
                    COALESCE(v_bucket.degraded_until, v_bucket.next_allowed_at)),
                v_bucket.in_flight_count, true, 'replayed'::text;
        END IF;
        RETURN;
    END IF;

    IF v_bucket.degraded_until > v_now THEN
        RETURN QUERY SELECT NULL::uuid, v_bucket.id, p_origin, p_profile_version,
            NULL::text, p_permit_kind, p_min_delay_ms, p_lease_seconds,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz, NULL::text,
            NULL::integer, NULL::integer, NULL::bytea, v_bucket.degraded_until,
            v_bucket.in_flight_count, false, 'deferred_backoff'::text;
        RETURN;
    END IF;
    IF v_bucket.in_flight_count >= 1 THEN
        SELECT min(lease.expires_at) INTO v_available_at
          FROM control.admission_leases AS lease
         WHERE lease.bucket_id = v_bucket.id AND lease.released_at IS NULL;
        v_available_at := GREATEST(
            v_available_at,
            v_bucket.next_allowed_at,
            COALESCE(v_bucket.degraded_until, v_bucket.next_allowed_at)
        );
        RETURN QUERY SELECT NULL::uuid, v_bucket.id, p_origin, p_profile_version,
            NULL::text, p_permit_kind, p_min_delay_ms, p_lease_seconds,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz, NULL::text,
            NULL::integer, NULL::integer, NULL::bytea, v_available_at,
            v_bucket.in_flight_count, false, 'deferred_in_flight'::text;
        RETURN;
    END IF;
    IF v_bucket.request_tokens = 0 OR v_bucket.next_allowed_at > v_now THEN
        RETURN QUERY SELECT NULL::uuid, v_bucket.id, p_origin, p_profile_version,
            NULL::text, p_permit_kind, p_min_delay_ms, p_lease_seconds,
            NULL::timestamptz, NULL::timestamptz, NULL::timestamptz, NULL::text,
            NULL::integer, NULL::integer, NULL::bytea, v_bucket.next_allowed_at,
            v_bucket.in_flight_count, false, 'deferred_politeness'::text;
        RETURN;
    END IF;

    v_expires_at := LEAST(
        v_now + make_interval(secs => p_lease_seconds),
        v_frontier.lease_until,
        v_frontier.receipt_expires_at
    );
    IF v_expires_at <= v_now THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::text, NULL::integer,
            NULL::integer, NULL::bytea, NULL::timestamptz, NULL::integer,
            NULL::boolean, 'frontier_unavailable'::text;
        RETURN;
    END IF;
    INSERT INTO control.admission_leases (
        id, bucket_id, workload_id, permit_kind, authority_fingerprint,
        min_delay_ms, requested_lease_seconds, issued_at, expires_at
    ) VALUES (
        p_permit_id, v_bucket.id, p_workload_id, p_permit_kind,
        v_authority_fingerprint, p_min_delay_ms, p_lease_seconds, v_now, v_expires_at
    ) ON CONFLICT DO NOTHING
    RETURNING id INTO v_inserted_id;
    IF v_inserted_id IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::integer,
            NULL::text, NULL::text, NULL::integer, NULL::integer, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::text, NULL::integer,
            NULL::integer, NULL::bytea, NULL::timestamptz,
            v_bucket.in_flight_count, NULL::boolean, 'permit_conflict'::text;
        RETURN;
    END IF;
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
    RETURN QUERY SELECT p_permit_id, v_bucket.id, p_origin, p_profile_version,
        p_workload_id, p_permit_kind, p_min_delay_ms, p_lease_seconds, v_now,
        v_expires_at, NULL::timestamptz, NULL::text, NULL::integer, NULL::integer,
        v_authority_fingerprint, v_expires_at, v_bucket.in_flight_count,
        false, 'admitted'::text;
END;
$$;

CREATE FUNCTION control.finish_origin_permit(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_frontier_id uuid,
    p_frontier_lease_id uuid,
    p_worker_key text,
    p_permit_id uuid,
    p_bucket_id uuid,
    p_origin text,
    p_profile_version integer,
    p_workload_id text,
    p_permit_kind text,
    p_issued_at timestamptz,
    p_expires_at timestamptz,
    p_completion_kind text,
    p_observed_latency_ms integer,
    p_retry_after_ms integer
)
RETURNS TABLE (
    finished_at timestamptz,
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
    v_expected_workload text;
    v_authority_fingerprint bytea;
    v_lease control.admission_leases%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_delay_ms integer;
    v_backoff_until timestamptz;
BEGIN
    v_expected_workload := 'crawl:' || p_crawl_run_id::text || ':' || p_frontier_lease_id::text;
    v_authority_fingerprint := sha256(convert_to(
        p_tenant_id::text || ':' || p_site_id::text || ':' || p_crawl_run_id::text || ':' ||
        p_frontier_id::text || ':' || p_frontier_lease_id::text || ':' || p_worker_key,
        'UTF8'
    ));
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_frontier_id IS NULL OR p_frontier_lease_id IS NULL OR p_permit_id IS NULL
       OR p_bucket_id IS NULL
       OR p_worker_key IS NULL OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_origin IS NULL OR p_profile_version IS DISTINCT FROM 1
       OR p_workload_id IS DISTINCT FROM v_expected_workload
       OR p_permit_kind IS NULL OR p_permit_kind NOT IN ('robots', 'html_navigation')
       OR p_issued_at IS NULL OR p_expires_at IS NULL OR p_expires_at <= p_issued_at
       OR p_completion_kind NOT IN (
            'success', 'rate_limited', 'service_unavailable', 'transport_error', 'cancelled'
       )
       OR p_completion_kind IS NULL
       OR p_observed_latency_ms IS NULL
       OR p_observed_latency_ms NOT BETWEEN 0 AND 120000
       OR (p_completion_kind = 'rate_limited'
            AND (p_retry_after_ms IS NULL
                OR p_retry_after_ms NOT BETWEEN 1000 AND 86400000))
       OR (p_completion_kind = 'service_unavailable'
            AND p_retry_after_ms IS NOT NULL
            AND p_retry_after_ms NOT BETWEEN 1000 AND 86400000)
       OR (p_completion_kind IN ('success', 'transport_error', 'cancelled')
            AND p_retry_after_ms IS NOT NULL)
    THEN
        RAISE EXCEPTION 'invalid_origin_permit_completion' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_bucket
      FROM control.origin_buckets AS bucket
     WHERE bucket.id = p_bucket_id
     FOR UPDATE;
    SELECT * INTO v_lease
      FROM control.admission_leases AS lease
     WHERE lease.id = p_permit_id;
    v_now := clock_timestamp();
    IF NOT FOUND
       OR v_bucket.id IS NULL
       OR v_lease.bucket_id IS DISTINCT FROM p_bucket_id
       OR v_bucket.origin IS DISTINCT FROM p_origin
       OR v_bucket.profile_version IS DISTINCT FROM p_profile_version
       OR v_lease.workload_id IS DISTINCT FROM p_workload_id
       OR v_lease.permit_kind IS DISTINCT FROM p_permit_kind
       OR v_lease.authority_fingerprint IS DISTINCT FROM v_authority_fingerprint
       OR v_lease.issued_at IS DISTINCT FROM p_issued_at
       OR v_lease.expires_at IS DISTINCT FROM p_expires_at
    THEN
        RETURN QUERY SELECT NULL::timestamptz, NULL::timestamptz,
            NULL::timestamptz, NULL::integer, NULL::boolean, 'permit_conflict'::text;
        RETURN;
    END IF;

    IF v_lease.released_at IS NOT NULL THEN
        IF v_lease.completion_kind = p_completion_kind
           AND v_lease.observed_latency_ms = p_observed_latency_ms
           AND v_lease.retry_after_ms IS NOT DISTINCT FROM p_retry_after_ms
        THEN
            RETURN QUERY SELECT v_lease.released_at, v_bucket.next_allowed_at,
                v_bucket.degraded_until, v_bucket.in_flight_count, true, 'finished'::text;
        ELSIF v_lease.completion_kind = 'lease_expired' THEN
            RETURN QUERY SELECT v_lease.released_at, v_bucket.next_allowed_at,
                v_bucket.degraded_until, v_bucket.in_flight_count, true,
                'permit_expired'::text;
        ELSE
            RETURN QUERY SELECT NULL::timestamptz, NULL::timestamptz,
                NULL::timestamptz, NULL::integer, NULL::boolean,
                'permit_conflict'::text;
        END IF;
        RETURN;
    END IF;
    IF v_lease.expires_at <= v_now THEN
        UPDATE control.admission_leases AS lease
           SET released_at = v_now, completion_kind = 'lease_expired'
         WHERE lease.id = p_permit_id;
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
        RETURN QUERY SELECT v_now, v_bucket.next_allowed_at, v_bucket.degraded_until,
            v_bucket.in_flight_count, false, 'permit_expired'::text;
        RETURN;
    END IF;

    UPDATE control.admission_leases AS lease
       SET released_at = v_now,
           completion_kind = p_completion_kind,
           observed_latency_ms = p_observed_latency_ms,
           retry_after_ms = p_retry_after_ms
     WHERE lease.id = p_permit_id;
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
    RETURN QUERY SELECT v_now, v_bucket.next_allowed_at, v_bucket.degraded_until,
        v_bucket.in_flight_count, false, 'finished'::text;
END;
$$;

CREATE FUNCTION control.reconcile_expired_origin_permits(p_limit integer)
RETURNS TABLE (reconciled_count integer, observed_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_count integer := 0;
    v_bucket_id uuid;
    v_reconciled integer;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'invalid_origin_reconcile_limit' USING ERRCODE = '22023';
    END IF;
    FOR v_bucket_id IN
        SELECT bucket.id
          FROM control.origin_buckets AS bucket
         WHERE EXISTS (
            SELECT 1 FROM control.admission_leases AS lease
             WHERE lease.bucket_id = bucket.id
               AND lease.released_at IS NULL
               AND lease.expires_at <= v_now
         )
         ORDER BY bucket.id
         LIMIT p_limit
         FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE control.admission_leases AS lease
           SET released_at = v_now, completion_kind = 'lease_expired'
         WHERE lease.bucket_id = v_bucket_id
           AND lease.released_at IS NULL
           AND lease.expires_at <= v_now;
        GET DIAGNOSTICS v_reconciled = ROW_COUNT;
        v_count := v_count + v_reconciled;
        UPDATE control.origin_buckets AS bucket
           SET in_flight_count = (
                   SELECT count(*)::integer FROM control.admission_leases AS lease
                    WHERE lease.bucket_id = v_bucket_id AND lease.released_at IS NULL
               ),
               last_refill_at = CASE
                   WHEN bucket.request_tokens = 0 AND bucket.next_allowed_at <= v_now
                   THEN v_now ELSE bucket.last_refill_at
               END,
               request_tokens = CASE
                   WHEN bucket.next_allowed_at <= v_now THEN 1 ELSE bucket.request_tokens
               END,
               degraded_until = CASE
                   WHEN bucket.degraded_until <= v_now THEN NULL
                   ELSE bucket.degraded_until
               END,
               updated_at = v_now
         WHERE bucket.id = v_bucket_id;
    END LOOP;
    RETURN QUERY SELECT v_count, v_now;
END;
$$;

REVOKE ALL ON FUNCTION control.acquire_origin_permit(
    uuid, uuid, uuid, uuid, uuid, text, uuid, text, integer, text, text, integer, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_origin_permit(
    uuid, uuid, uuid, uuid, uuid, text, uuid, uuid, text, integer, text, text,
    timestamptz, timestamptz, text, integer, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.reconcile_expired_origin_permits(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.acquire_origin_permit(
    uuid, uuid, uuid, uuid, uuid, text, uuid, text, integer, text, text, integer, integer
) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.finish_origin_permit(
    uuid, uuid, uuid, uuid, uuid, text, uuid, uuid, text, integer, text, text,
    timestamptz, timestamptz, text, integer, integer
) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.reconcile_expired_origin_permits(integer)
TO signal_crawl_admission;
