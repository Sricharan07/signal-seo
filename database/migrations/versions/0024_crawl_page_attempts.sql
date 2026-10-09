CREATE TABLE app.crawl_page_attempts (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    frontier_id uuid NOT NULL,
    url_id uuid NOT NULL,
    fetch_attempt_id uuid NOT NULL,
    lease_owner text NOT NULL CHECK (lease_owner ~ '^[a-z][a-z0-9_.:-]{0,127}$'),
    robots_snapshot_id uuid NOT NULL,
    robots_reason text NOT NULL
        CHECK (robots_reason IN ('allowed_by_rules', 'robots_not_found')),
    origin_permit_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('dispatched', 'observed', 'failed')),
    dispatched_at timestamptz NOT NULL,
    finished_at timestamptz,
    observation_id uuid,
    terminal_reason text CHECK (terminal_reason IN (
        'transport_error', 'policy_rejected', 'observation_persistence_failed'
    )),
    completion_kind text CHECK (completion_kind IN (
        'success', 'rate_limited', 'service_unavailable', 'transport_error', 'cancelled'
    )),
    observed_latency_ms integer CHECK (observed_latency_ms BETWEEN 0 AND 120000),
    retry_after_ms integer CHECK (retry_after_ms BETWEEN 1000 AND 86400000),
    backoff_basis text CHECK (backoff_basis IN (
        'none', 'provider_seconds', 'provider_date', 'fallback_missing',
        'fallback_invalid', 'capped', 'local_persistence_failure'
    )),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, frontier_id, fetch_attempt_id),
    UNIQUE (origin_permit_id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, url_id)
        REFERENCES app.urls (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id, frontier_id, url_id)
        REFERENCES app.crawl_frontier
            (tenant_id, site_id, crawl_run_id, id, url_id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id, frontier_id, fetch_attempt_id)
        REFERENCES app.crawl_frontier_leases
            (tenant_id, site_id, crawl_run_id, frontier_id, id),
    FOREIGN KEY (tenant_id, site_id, robots_snapshot_id)
        REFERENCES app.robots_snapshots (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, observation_id)
        REFERENCES app.fetch_observations (tenant_id, site_id, id),
    FOREIGN KEY (origin_permit_id) REFERENCES control.admission_leases (id),
    CHECK (id = origin_permit_id),
    CHECK (finished_at IS NULL OR finished_at >= dispatched_at),
    CHECK (
        (state = 'dispatched' AND finished_at IS NULL AND observation_id IS NULL
            AND terminal_reason IS NULL AND completion_kind IS NULL
            AND observed_latency_ms IS NULL AND retry_after_ms IS NULL
            AND backoff_basis IS NULL)
        OR
        (state = 'observed' AND finished_at IS NOT NULL AND observation_id IS NOT NULL
            AND terminal_reason IS NULL
            AND completion_kind IN ('success', 'rate_limited', 'service_unavailable')
            AND observed_latency_ms IS NOT NULL AND backoff_basis IS NOT NULL
            AND backoff_basis <> 'local_persistence_failure')
        OR
        (state = 'failed' AND finished_at IS NOT NULL AND observation_id IS NULL
            AND terminal_reason IS NOT NULL AND completion_kind IS NOT NULL
            AND observed_latency_ms IS NOT NULL AND backoff_basis IS NOT NULL)
    ),
    CHECK (
        completion_kind IS NULL
        OR (completion_kind IN ('success', 'transport_error', 'cancelled')
            AND retry_after_ms IS NULL AND backoff_basis = 'none')
        OR (completion_kind = 'rate_limited' AND retry_after_ms IS NOT NULL
            AND backoff_basis IN (
                'provider_seconds', 'provider_date', 'fallback_missing',
                'fallback_invalid', 'capped'
            ))
        OR (completion_kind = 'service_unavailable'
            AND (
                (retry_after_ms IS NOT NULL AND backoff_basis IN (
                    'provider_seconds', 'provider_date', 'capped'
                ))
                OR (retry_after_ms IS NULL AND backoff_basis IN (
                    'fallback_missing', 'fallback_invalid',
                    'local_persistence_failure'
                ))
            ))
    ),
    CHECK (
        terminal_reason IS NULL
        OR (terminal_reason = 'transport_error' AND completion_kind = 'transport_error')
        OR (terminal_reason = 'policy_rejected' AND completion_kind = 'cancelled')
        OR (terminal_reason = 'observation_persistence_failed'
            AND completion_kind = 'service_unavailable'
            AND backoff_basis = 'local_persistence_failure')
    )
);

CREATE INDEX crawl_page_attempts_run_state
ON app.crawl_page_attempts (tenant_id, site_id, crawl_run_id, state, dispatched_at, id);
CREATE INDEX crawl_page_attempts_observation
ON app.crawl_page_attempts (tenant_id, site_id, observation_id)
WHERE observation_id IS NOT NULL;

CREATE FUNCTION app.guard_crawl_page_attempt_mutation() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_crawl_page_attempt' USING ERRCODE = '55000';
    END IF;
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.crawl_run_id IS DISTINCT FROM NEW.crawl_run_id
       OR OLD.frontier_id IS DISTINCT FROM NEW.frontier_id
       OR OLD.url_id IS DISTINCT FROM NEW.url_id
       OR OLD.fetch_attempt_id IS DISTINCT FROM NEW.fetch_attempt_id
       OR OLD.lease_owner IS DISTINCT FROM NEW.lease_owner
       OR OLD.robots_snapshot_id IS DISTINCT FROM NEW.robots_snapshot_id
       OR OLD.robots_reason IS DISTINCT FROM NEW.robots_reason
       OR OLD.origin_permit_id IS DISTINCT FROM NEW.origin_permit_id
       OR OLD.dispatched_at IS DISTINCT FROM NEW.dispatched_at
       OR OLD.state <> 'dispatched'
       OR NEW.state = 'dispatched'
    THEN
        RAISE EXCEPTION 'immutable_crawl_page_attempt' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION app.guard_crawl_page_attempt_mutation() FROM PUBLIC;
CREATE TRIGGER crawl_page_attempts_guard
BEFORE UPDATE OR DELETE ON app.crawl_page_attempts
FOR EACH ROW EXECUTE FUNCTION app.guard_crawl_page_attempt_mutation();

CREATE FUNCTION app.guard_fetch_observation_page_attempt() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_attempt app.crawl_page_attempts%%ROWTYPE;
BEGIN
    SELECT attempt.* INTO v_attempt
      FROM app.crawl_page_attempts AS attempt
     WHERE attempt.tenant_id = NEW.tenant_id
       AND attempt.site_id = NEW.site_id
       AND attempt.crawl_run_id = NEW.crawl_run_id
       AND attempt.frontier_id = NEW.frontier_id
       AND attempt.url_id = NEW.url_id
       AND attempt.fetch_attempt_id = NEW.fetch_attempt_id
     FOR UPDATE;
    IF FOUND
       AND (v_attempt.state = 'failed'
            OR (v_attempt.state = 'observed'
                AND v_attempt.observation_id IS DISTINCT FROM NEW.id))
    THEN
        RAISE EXCEPTION 'terminal_crawl_page_attempt' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION app.guard_fetch_observation_page_attempt() FROM PUBLIC;
CREATE TRIGGER fetch_observations_page_attempt_guard
BEFORE INSERT ON app.fetch_observations
FOR EACH ROW EXECUTE FUNCTION app.guard_fetch_observation_page_attempt();

ALTER TABLE app.crawl_page_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_page_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_page_attempt_scope ON app.crawl_page_attempts
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.get_crawl_page_attempt(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_frontier_id uuid,
    p_fetch_attempt_id uuid,
    p_lease_owner text,
    p_origin_permit_id uuid
)
RETURNS TABLE (
    attempt_id uuid, attempt_state text, crawl_run_id uuid, frontier_id uuid,
    url_id uuid, fetch_attempt_id uuid, robots_snapshot_id uuid, robots_reason text,
    origin_permit_id uuid, dispatched_at timestamptz, finished_at timestamptz,
    observation_id uuid, terminal_reason text, completion_kind text,
    observed_latency_ms integer, retry_after_ms integer, backoff_basis text,
    duplicate boolean, outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_attempt app.crawl_page_attempts%%ROWTYPE;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_frontier_id IS NULL OR p_fetch_attempt_id IS NULL
       OR p_origin_permit_id IS NULL
       OR p_lease_owner IS NULL OR p_lease_owner !~ '^[a-z][a-z0-9_.:-]{0,127}$'
    THEN
        RAISE EXCEPTION 'invalid_crawl_page_attempt_lookup' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT attempt.* INTO v_attempt
      FROM app.crawl_page_attempts AS attempt
     WHERE attempt.tenant_id = p_tenant_id
       AND attempt.site_id = p_site_id
       AND attempt.crawl_run_id = p_crawl_run_id
       AND attempt.frontier_id = p_frontier_id
       AND attempt.fetch_attempt_id = p_fetch_attempt_id
       AND attempt.lease_owner = p_lease_owner
       AND attempt.origin_permit_id = p_origin_permit_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'missing'::text;
        RETURN;
    END IF;
    RETURN QUERY SELECT v_attempt.id, v_attempt.state, v_attempt.crawl_run_id,
        v_attempt.frontier_id, v_attempt.url_id, v_attempt.fetch_attempt_id,
        v_attempt.robots_snapshot_id, v_attempt.robots_reason,
        v_attempt.origin_permit_id, v_attempt.dispatched_at, v_attempt.finished_at,
        v_attempt.observation_id, v_attempt.terminal_reason,
        v_attempt.completion_kind, v_attempt.observed_latency_ms,
        v_attempt.retry_after_ms, v_attempt.backoff_basis, true, 'found'::text;
END;
$$;

CREATE FUNCTION control.begin_crawl_page_attempt(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_frontier_id uuid,
    p_url_id uuid,
    p_fetch_attempt_id uuid,
    p_lease_owner text,
    p_origin_permit_id uuid,
    p_robots_snapshot_id uuid,
    p_robots_reason text
)
RETURNS TABLE (
    attempt_id uuid, attempt_state text, crawl_run_id uuid, frontier_id uuid,
    url_id uuid, fetch_attempt_id uuid, robots_snapshot_id uuid, robots_reason text,
    origin_permit_id uuid, dispatched_at timestamptz, finished_at timestamptz,
    observation_id uuid, terminal_reason text, completion_kind text,
    observed_latency_ms integer, retry_after_ms integer, backoff_basis text,
    duplicate boolean, outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_attempt app.crawl_page_attempts%%ROWTYPE;
    v_current record;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_permit control.admission_leases%%ROWTYPE;
    v_bucket_id uuid;
    v_expected_workload text;
    v_fingerprint bytea;
    v_inserted integer;
BEGIN
    v_expected_workload := 'crawl:' || p_crawl_run_id::text || ':' || p_fetch_attempt_id::text;
    v_fingerprint := sha256(convert_to(
        p_tenant_id::text || ':' || p_site_id::text || ':' || p_crawl_run_id::text || ':' ||
        p_frontier_id::text || ':' || p_fetch_attempt_id::text || ':' || p_lease_owner,
        'UTF8'
    ));
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_frontier_id IS NULL OR p_url_id IS NULL OR p_fetch_attempt_id IS NULL
       OR p_origin_permit_id IS NULL OR p_robots_snapshot_id IS NULL
       OR p_lease_owner IS NULL OR p_lease_owner !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_robots_reason IS NULL
       OR p_robots_reason NOT IN ('allowed_by_rules', 'robots_not_found')
    THEN
        RAISE EXCEPTION 'invalid_crawl_page_attempt' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);

    SELECT attempt.* INTO v_attempt
      FROM app.crawl_page_attempts AS attempt
     WHERE attempt.tenant_id = p_tenant_id
       AND attempt.origin_permit_id = p_origin_permit_id;
    IF FOUND THEN
        IF v_attempt.site_id IS DISTINCT FROM p_site_id
           OR v_attempt.crawl_run_id IS DISTINCT FROM p_crawl_run_id
           OR v_attempt.frontier_id IS DISTINCT FROM p_frontier_id
           OR v_attempt.url_id IS DISTINCT FROM p_url_id
           OR v_attempt.fetch_attempt_id IS DISTINCT FROM p_fetch_attempt_id
           OR v_attempt.lease_owner IS DISTINCT FROM p_lease_owner
           OR v_attempt.robots_snapshot_id IS DISTINCT FROM p_robots_snapshot_id
           OR v_attempt.robots_reason IS DISTINCT FROM p_robots_reason
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
                NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::text, NULL::integer, NULL::integer, NULL::text,
                false, 'attempt_conflict'::text;
        ELSE
            RETURN QUERY SELECT v_attempt.id, v_attempt.state,
                v_attempt.crawl_run_id, v_attempt.frontier_id, v_attempt.url_id,
                v_attempt.fetch_attempt_id, v_attempt.robots_snapshot_id,
                v_attempt.robots_reason, v_attempt.origin_permit_id,
                v_attempt.dispatched_at, v_attempt.finished_at,
                v_attempt.observation_id, v_attempt.terminal_reason,
                v_attempt.completion_kind, v_attempt.observed_latency_ms,
                v_attempt.retry_after_ms, v_attempt.backoff_basis,
                true, 'begun'::text;
        END IF;
        RETURN;
    END IF;

    SELECT frontier.lease_until, receipt.expires_at AS receipt_expires_at,
           url.origin, snapshot.expires_at AS robots_expires_at,
           snapshot.crawl_delay_ms
      INTO v_current
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
      JOIN app.robots_snapshots AS snapshot
        ON snapshot.tenant_id = run.tenant_id
       AND snapshot.site_id = run.site_id
       AND snapshot.crawl_run_id = run.id
       AND snapshot.id = p_robots_snapshot_id
       AND snapshot.origin = url.origin
     WHERE frontier.tenant_id = p_tenant_id
       AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_crawl_run_id
       AND frontier.id = p_frontier_id
       AND frontier.url_id = p_url_id
       AND frontier.status = 'leased'
       AND frontier.lease_id = p_fetch_attempt_id
       AND frontier.lease_owner = p_lease_owner
       AND frontier.lease_until > v_now
       AND receipt.lease_owner = p_lease_owner
       AND receipt.expires_at = frontier.lease_until
       AND receipt.expires_at > v_now
       AND run.status = 'running'
       AND workflow.state_projection = 'running'
       AND directory.lifecycle = 'active'
       AND tenant.lifecycle = 'active'
       AND site.state <> 'archived'
       AND snapshot.expires_at > v_now
       AND (
            (p_robots_reason = 'allowed_by_rules' AND snapshot.decision_status = 'rules')
            OR (p_robots_reason = 'robots_not_found'
                AND snapshot.decision_status = 'allow_missing')
       )
       AND EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(
                run.scope_snapshot->'allowed_origins'
            ) AS allowed(value) WHERE allowed.value = url.origin
       )
     FOR UPDATE OF receipt
     FOR SHARE OF frontier, url, run, workflow, directory, tenant, site, snapshot;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'authority_unavailable'::text;
        RETURN;
    END IF;

    SELECT permit.bucket_id INTO v_bucket_id
      FROM control.admission_leases AS permit
     WHERE permit.id = p_origin_permit_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'permit_unavailable'::text;
        RETURN;
    END IF;
    SELECT bucket.* INTO v_bucket
      FROM control.origin_buckets AS bucket
     WHERE bucket.id = v_bucket_id
     FOR UPDATE;
    SELECT permit.* INTO v_permit
      FROM control.admission_leases AS permit
     WHERE permit.id = p_origin_permit_id
     FOR UPDATE;
    v_now := clock_timestamp();
    IF v_current.lease_until <= v_now OR v_current.receipt_expires_at <= v_now
       OR v_current.robots_expires_at <= v_now
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'authority_unavailable'::text;
        RETURN;
    END IF;
    IF v_bucket.id IS NULL OR v_permit.id IS NULL
       OR v_permit.bucket_id IS DISTINCT FROM v_bucket.id
       OR v_bucket.origin IS DISTINCT FROM v_current.origin
       OR v_bucket.profile_version IS DISTINCT FROM 1
       OR v_permit.workload_id IS DISTINCT FROM v_expected_workload
       OR v_permit.permit_kind IS DISTINCT FROM 'html_navigation'
       OR v_permit.authority_fingerprint IS DISTINCT FROM v_fingerprint
       OR v_permit.released_at IS NOT NULL
       OR v_permit.expires_at <= v_now
       OR v_permit.min_delay_ms < GREATEST(
            1000, COALESCE(v_current.crawl_delay_ms, 1000)
       )
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'permit_unavailable'::text;
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM app.fetch_observations AS observation
         WHERE observation.tenant_id = p_tenant_id
           AND observation.site_id = p_site_id
           AND observation.crawl_run_id = p_crawl_run_id
           AND observation.frontier_id = p_frontier_id
           AND observation.url_id = p_url_id
           AND observation.fetch_attempt_id = p_fetch_attempt_id
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'attempt_conflict'::text;
        RETURN;
    END IF;

    INSERT INTO app.crawl_page_attempts (
        tenant_id, site_id, id, crawl_run_id, frontier_id, url_id,
        fetch_attempt_id, lease_owner, robots_snapshot_id, robots_reason,
        origin_permit_id, state, dispatched_at
    ) VALUES (
        p_tenant_id, p_site_id, p_origin_permit_id, p_crawl_run_id,
        p_frontier_id, p_url_id, p_fetch_attempt_id, p_lease_owner,
        p_robots_snapshot_id, p_robots_reason, p_origin_permit_id,
        'dispatched', v_now
    ) ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    SELECT attempt.* INTO v_attempt
      FROM app.crawl_page_attempts AS attempt
     WHERE attempt.tenant_id = p_tenant_id
       AND attempt.origin_permit_id = p_origin_permit_id;
    IF NOT FOUND
       OR v_attempt.site_id IS DISTINCT FROM p_site_id
       OR v_attempt.crawl_run_id IS DISTINCT FROM p_crawl_run_id
       OR v_attempt.frontier_id IS DISTINCT FROM p_frontier_id
       OR v_attempt.url_id IS DISTINCT FROM p_url_id
       OR v_attempt.fetch_attempt_id IS DISTINCT FROM p_fetch_attempt_id
       OR v_attempt.lease_owner IS DISTINCT FROM p_lease_owner
       OR v_attempt.robots_snapshot_id IS DISTINCT FROM p_robots_snapshot_id
       OR v_attempt.robots_reason IS DISTINCT FROM p_robots_reason
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'attempt_conflict'::text;
        RETURN;
    END IF;
    RETURN QUERY SELECT v_attempt.id, v_attempt.state, v_attempt.crawl_run_id,
        v_attempt.frontier_id, v_attempt.url_id, v_attempt.fetch_attempt_id,
        v_attempt.robots_snapshot_id, v_attempt.robots_reason,
        v_attempt.origin_permit_id, v_attempt.dispatched_at, v_attempt.finished_at,
        v_attempt.observation_id, v_attempt.terminal_reason,
        v_attempt.completion_kind, v_attempt.observed_latency_ms,
        v_attempt.retry_after_ms, v_attempt.backoff_basis,
        v_inserted = 0, 'begun'::text;
END;
$$;

CREATE FUNCTION control.finish_crawl_page_attempt(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_frontier_id uuid,
    p_url_id uuid,
    p_fetch_attempt_id uuid,
    p_lease_owner text,
    p_origin_permit_id uuid,
    p_observation_id uuid,
    p_terminal_reason text,
    p_completion_kind text,
    p_observed_latency_ms integer,
    p_retry_after_ms integer,
    p_backoff_basis text
)
RETURNS TABLE (
    attempt_id uuid, attempt_state text, crawl_run_id uuid, frontier_id uuid,
    url_id uuid, fetch_attempt_id uuid, robots_snapshot_id uuid, robots_reason text,
    origin_permit_id uuid, dispatched_at timestamptz, finished_at timestamptz,
    observation_id uuid, terminal_reason text, completion_kind text,
    observed_latency_ms integer, retry_after_ms integer, backoff_basis text,
    duplicate boolean, outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_attempt app.crawl_page_attempts%%ROWTYPE;
    v_observation app.fetch_observations%%ROWTYPE;
    v_permit control.admission_leases%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_finish record;
    v_state text;
BEGIN
    v_state := CASE WHEN p_observation_id IS NULL THEN 'failed' ELSE 'observed' END;
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_frontier_id IS NULL OR p_url_id IS NULL OR p_fetch_attempt_id IS NULL
       OR p_origin_permit_id IS NULL
       OR p_lease_owner IS NULL OR p_lease_owner !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_completion_kind IS NULL
       OR p_completion_kind NOT IN (
            'success', 'rate_limited', 'service_unavailable', 'transport_error', 'cancelled'
       )
       OR p_observed_latency_ms IS NULL
       OR p_observed_latency_ms NOT BETWEEN 0 AND 120000
       OR p_backoff_basis IS NULL
       OR p_backoff_basis NOT IN (
            'none', 'provider_seconds', 'provider_date', 'fallback_missing',
            'fallback_invalid', 'capped', 'local_persistence_failure'
       )
       OR (p_completion_kind IN ('success', 'transport_error', 'cancelled')
            AND (p_retry_after_ms IS NOT NULL OR p_backoff_basis <> 'none'))
       OR (p_completion_kind = 'rate_limited'
            AND (p_retry_after_ms IS NULL
                OR p_retry_after_ms NOT BETWEEN 1000 AND 86400000
                OR p_backoff_basis NOT IN (
                    'provider_seconds', 'provider_date', 'fallback_missing',
                    'fallback_invalid', 'capped'
                )))
       OR (p_completion_kind = 'service_unavailable'
            AND (
                (p_retry_after_ms IS NOT NULL
                    AND (p_retry_after_ms NOT BETWEEN 1000 AND 86400000
                        OR p_backoff_basis NOT IN (
                            'provider_seconds', 'provider_date', 'capped'
                        )))
                OR (p_retry_after_ms IS NULL
                    AND p_backoff_basis NOT IN (
                        'fallback_missing', 'fallback_invalid',
                        'local_persistence_failure'
                    ))
            ))
       OR (p_observation_id IS NOT NULL AND p_terminal_reason IS NOT NULL)
       OR (p_observation_id IS NOT NULL
            AND p_backoff_basis = 'local_persistence_failure')
       OR (p_observation_id IS NULL AND p_terminal_reason IS NULL)
       OR (p_terminal_reason = 'transport_error'
            AND p_completion_kind <> 'transport_error')
       OR (p_terminal_reason = 'policy_rejected'
            AND p_completion_kind <> 'cancelled')
       OR (p_terminal_reason = 'observation_persistence_failed'
            AND (p_completion_kind <> 'service_unavailable'
                OR p_retry_after_ms IS NOT NULL
                OR p_backoff_basis <> 'local_persistence_failure'))
       OR (p_terminal_reason IS NOT NULL AND p_terminal_reason NOT IN (
            'transport_error', 'policy_rejected', 'observation_persistence_failed'
       ))
    THEN
        RAISE EXCEPTION 'invalid_crawl_page_attempt_completion' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT attempt.* INTO v_attempt
      FROM app.crawl_page_attempts AS attempt
     WHERE attempt.tenant_id = p_tenant_id
       AND attempt.site_id = p_site_id
       AND attempt.crawl_run_id = p_crawl_run_id
       AND attempt.frontier_id = p_frontier_id
       AND attempt.url_id = p_url_id
       AND attempt.fetch_attempt_id = p_fetch_attempt_id
       AND attempt.lease_owner = p_lease_owner
       AND attempt.origin_permit_id = p_origin_permit_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'attempt_unavailable'::text;
        RETURN;
    END IF;
    IF v_attempt.state <> 'dispatched' THEN
        IF v_attempt.state = v_state
           AND v_attempt.observation_id IS NOT DISTINCT FROM p_observation_id
           AND v_attempt.terminal_reason IS NOT DISTINCT FROM p_terminal_reason
           AND v_attempt.completion_kind IS NOT DISTINCT FROM p_completion_kind
           AND v_attempt.observed_latency_ms IS NOT DISTINCT FROM p_observed_latency_ms
           AND v_attempt.retry_after_ms IS NOT DISTINCT FROM p_retry_after_ms
           AND v_attempt.backoff_basis IS NOT DISTINCT FROM p_backoff_basis
        THEN
            RETURN QUERY SELECT v_attempt.id, v_attempt.state,
                v_attempt.crawl_run_id, v_attempt.frontier_id, v_attempt.url_id,
                v_attempt.fetch_attempt_id, v_attempt.robots_snapshot_id,
                v_attempt.robots_reason, v_attempt.origin_permit_id,
                v_attempt.dispatched_at, v_attempt.finished_at,
                v_attempt.observation_id, v_attempt.terminal_reason,
                v_attempt.completion_kind, v_attempt.observed_latency_ms,
                v_attempt.retry_after_ms, v_attempt.backoff_basis,
                true, 'finished'::text;
        ELSE
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
                NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::text, NULL::integer, NULL::integer, NULL::text,
                false, 'attempt_conflict'::text;
        END IF;
        RETURN;
    END IF;

    IF p_observation_id IS NOT NULL THEN
        SELECT observation.* INTO v_observation
          FROM app.fetch_observations AS observation
         WHERE observation.tenant_id = p_tenant_id
           AND observation.site_id = p_site_id
           AND observation.id = p_observation_id
           AND observation.crawl_run_id = p_crawl_run_id
           AND observation.frontier_id = p_frontier_id
           AND observation.url_id = p_url_id
           AND observation.fetch_attempt_id = p_fetch_attempt_id;
        IF NOT FOUND
           OR v_observation.started_at < v_attempt.dispatched_at
           OR v_observation.elapsed_ms IS DISTINCT FROM p_observed_latency_ms
           OR (v_observation.http_status = 429
                AND p_completion_kind <> 'rate_limited')
           OR (v_observation.http_status = 503
                AND p_completion_kind <> 'service_unavailable')
           OR (v_observation.http_status NOT IN (429, 503)
                AND p_completion_kind <> 'success')
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
                NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::text, NULL::integer, NULL::integer, NULL::text,
                false, 'observation_conflict'::text;
            RETURN;
        END IF;
    ELSIF EXISTS (
        SELECT 1
          FROM app.fetch_observations AS observation
         WHERE observation.tenant_id = p_tenant_id
           AND observation.site_id = p_site_id
           AND observation.crawl_run_id = p_crawl_run_id
           AND observation.frontier_id = p_frontier_id
           AND observation.url_id = p_url_id
           AND observation.fetch_attempt_id = p_fetch_attempt_id
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'observation_conflict'::text;
        RETURN;
    END IF;

    SELECT permit.* INTO v_permit
      FROM control.admission_leases AS permit
     WHERE permit.id = p_origin_permit_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'permit_unavailable'::text;
        RETURN;
    END IF;
    SELECT bucket.* INTO v_bucket
      FROM control.origin_buckets AS bucket
     WHERE bucket.id = v_permit.bucket_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'permit_unavailable'::text;
        RETURN;
    END IF;
    SELECT * INTO v_finish
      FROM control.finish_origin_permit(
        p_tenant_id, p_site_id, p_crawl_run_id, p_frontier_id,
        p_fetch_attempt_id, p_lease_owner, p_origin_permit_id,
        v_permit.bucket_id, v_bucket.origin, v_bucket.profile_version,
        v_permit.workload_id, v_permit.permit_kind, v_permit.issued_at,
        v_permit.expires_at, p_completion_kind, p_observed_latency_ms,
        p_retry_after_ms
      );
    IF v_finish.outcome = 'permit_expired' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'permit_expired'::text;
        RETURN;
    ELSIF v_finish.outcome <> 'finished' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::uuid,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::text, NULL::integer, NULL::integer, NULL::text,
            false, 'permit_conflict'::text;
        RETURN;
    END IF;

    UPDATE app.crawl_page_attempts AS attempt
       SET state = v_state,
           finished_at = v_finish.finished_at,
           observation_id = p_observation_id,
           terminal_reason = p_terminal_reason,
           completion_kind = p_completion_kind,
           observed_latency_ms = p_observed_latency_ms,
           retry_after_ms = p_retry_after_ms,
           backoff_basis = p_backoff_basis
     WHERE attempt.tenant_id = p_tenant_id
       AND attempt.id = p_origin_permit_id
     RETURNING attempt.* INTO v_attempt;
    RETURN QUERY SELECT v_attempt.id, v_attempt.state, v_attempt.crawl_run_id,
        v_attempt.frontier_id, v_attempt.url_id, v_attempt.fetch_attempt_id,
        v_attempt.robots_snapshot_id, v_attempt.robots_reason,
        v_attempt.origin_permit_id, v_attempt.dispatched_at, v_attempt.finished_at,
        v_attempt.observation_id, v_attempt.terminal_reason,
        v_attempt.completion_kind, v_attempt.observed_latency_ms,
        v_attempt.retry_after_ms, v_attempt.backoff_basis,
        false, 'finished'::text;
END;
$$;

CREATE FUNCTION control.get_fetch_observation_for_lease(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_frontier_id uuid,
    p_fetch_attempt_id uuid,
    p_lease_owner text
)
RETURNS TABLE (
    observation_id uuid, crawl_run_id uuid, frontier_id uuid, url_id uuid,
    fetch_attempt_id uuid, started_at timestamptz, finished_at timestamptz,
    observation_outcome text, http_status integer, final_url text,
    response_headers jsonb, redirect_chain jsonb, resolved_address text,
    media_type text, decoded_bytes bigint, elapsed_ms integer,
    network_profile_hash bytea, raw_artifact_id uuid, object_key text,
    object_version text, artifact_hash bytea, artifact_byte_length bigint,
    artifact_media_type text, encryption_key_ref text,
    artifact_created_at timestamptz, retain_until timestamptz,
    legal_hold boolean, durability_state text, lookup_outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_observation record;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_frontier_id IS NULL OR p_fetch_attempt_id IS NULL
       OR p_lease_owner IS NULL OR p_lease_owner !~ '^[a-z][a-z0-9_.:-]{0,127}$'
    THEN
        RAISE EXCEPTION 'invalid_fetch_observation_lookup' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT observation.*, artifact.object_key, artifact.object_version,
           artifact.sha256 AS artifact_hash,
           artifact.byte_length AS artifact_byte_length,
           artifact.media_type AS artifact_media_type,
           artifact.encryption_key_ref, artifact.created_at AS artifact_created_at,
           artifact.retain_until, artifact.legal_hold, artifact.durability_state
      INTO v_observation
      FROM app.fetch_observations AS observation
      JOIN app.crawl_frontier_leases AS receipt
        ON receipt.tenant_id = observation.tenant_id
       AND receipt.site_id = observation.site_id
       AND receipt.crawl_run_id = observation.crawl_run_id
       AND receipt.frontier_id = observation.frontier_id
       AND receipt.id = observation.fetch_attempt_id
      LEFT JOIN app.artifacts AS artifact
        ON artifact.tenant_id = observation.tenant_id
       AND artifact.site_id = observation.site_id
       AND artifact.id = observation.raw_artifact_id
     WHERE observation.tenant_id = p_tenant_id
       AND observation.site_id = p_site_id
       AND observation.crawl_run_id = p_crawl_run_id
       AND observation.frontier_id = p_frontier_id
       AND observation.fetch_attempt_id = p_fetch_attempt_id
       AND receipt.lease_owner = p_lease_owner;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::timestamptz, NULL::timestamptz, NULL::text,
            NULL::integer, NULL::text, NULL::jsonb, NULL::jsonb, NULL::text,
            NULL::text, NULL::bigint, NULL::integer, NULL::bytea, NULL::uuid,
            NULL::text, NULL::text, NULL::bytea, NULL::bigint, NULL::text,
            NULL::text, NULL::timestamptz, NULL::timestamptz, NULL::boolean,
            NULL::text, 'missing'::text;
        RETURN;
    END IF;
    RETURN QUERY SELECT v_observation.id, v_observation.crawl_run_id,
        v_observation.frontier_id, v_observation.url_id,
        v_observation.fetch_attempt_id, v_observation.started_at,
        v_observation.finished_at, v_observation.outcome,
        v_observation.http_status, v_observation.final_url,
        v_observation.response_headers, v_observation.redirect_chain,
        host(v_observation.resolved_address), v_observation.media_type,
        v_observation.decoded_bytes, v_observation.elapsed_ms,
        v_observation.network_profile_hash, v_observation.raw_artifact_id,
        v_observation.object_key, v_observation.object_version,
        v_observation.artifact_hash, v_observation.artifact_byte_length,
        v_observation.artifact_media_type, v_observation.encryption_key_ref,
        v_observation.artifact_created_at, v_observation.retain_until,
        v_observation.legal_hold, v_observation.durability_state, 'found'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.get_crawl_page_attempt(
    uuid, uuid, uuid, uuid, uuid, text, uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.begin_crawl_page_attempt(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, uuid, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_crawl_page_attempt(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, uuid, text,
    text, integer, integer, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.get_fetch_observation_for_lease(
    uuid, uuid, uuid, uuid, uuid, text
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.get_crawl_page_attempt(
    uuid, uuid, uuid, uuid, uuid, text, uuid
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.begin_crawl_page_attempt(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, uuid, text
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.finish_crawl_page_attempt(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, uuid, text,
    text, integer, integer, text
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.get_fetch_observation_for_lease(
    uuid, uuid, uuid, uuid, uuid, text
) TO signal_crawl_ingest;
