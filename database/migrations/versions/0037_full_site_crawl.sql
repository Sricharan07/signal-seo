CREATE TABLE app.crawl_robots_dispatches (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    frontier_id uuid NOT NULL,
    frontier_lease_id uuid NOT NULL,
    origin_permit_id uuid NOT NULL,
    worker_key text NOT NULL CHECK (worker_key ~ '^[a-z][a-z0-9_.:-]{0,127}$'),
    request_url text NOT NULL,
    request_sha256 bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
    max_response_bytes integer NOT NULL CHECK (max_response_bytes BETWEEN 1 AND 524288),
    state text NOT NULL DEFAULT 'dispatched'
        CHECK (state IN ('dispatched', 'observed', 'failed')),
    dispatched_at timestamptz NOT NULL,
    finished_at timestamptz,
    network_outcome text CHECK (network_outcome IN (
        'fetched', 'not_found', 'forbidden', 'backoff', 'server_error',
        'client_error', 'transport_error', 'policy_rejected',
        'unsupported_encoding', 'unsupported_media_type', 'body_limit'
    )),
    robots_snapshot_id uuid,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id, frontier_id, frontier_lease_id)
        REFERENCES app.crawl_frontier_leases
            (tenant_id, site_id, crawl_run_id, frontier_id, id),
    FOREIGN KEY (origin_permit_id) REFERENCES control.admission_leases (id),
    FOREIGN KEY (tenant_id, site_id, robots_snapshot_id)
        REFERENCES app.robots_snapshots (tenant_id, site_id, id),
    CHECK (control.valid_crawl_url_identity(
        request_url, request_url, request_url,
        regexp_replace(request_url, '/robots[.]txt$', ''), 1
    )),
    CHECK (request_url = regexp_replace(request_url, '/robots[.]txt$', '') || '/robots.txt'),
    CHECK (
        (state = 'dispatched' AND finished_at IS NULL AND network_outcome IS NULL
            AND robots_snapshot_id IS NULL)
        OR
        (state IN ('observed', 'failed') AND finished_at >= dispatched_at
            AND network_outcome IS NOT NULL AND robots_snapshot_id IS NOT NULL)
    )
);

CREATE INDEX crawl_robots_dispatches_unknown
ON app.crawl_robots_dispatches (dispatched_at, tenant_id, id)
WHERE state = 'dispatched';

CREATE TABLE app.crawl_page_records (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    frontier_id uuid NOT NULL,
    url_id uuid NOT NULL,
    observation_id uuid NOT NULL,
    http_status integer NOT NULL CHECK (http_status BETWEEN 100 AND 599),
    canonical_url text,
    title text CHECK (title IS NULL OR length(title) BETWEEN 1 AND 512),
    meta_description text CHECK (
        meta_description IS NULL OR length(meta_description) BETWEEN 1 AND 2048
    ),
    robots_meta jsonb NOT NULL,
    headings jsonb NOT NULL,
    hreflang jsonb NOT NULL,
    structured_data_types jsonb NOT NULL,
    internal_links jsonb NOT NULL,
    external_links jsonb NOT NULL,
    body_sha256 bytea NOT NULL CHECK (octet_length(body_sha256) = 32),
    parse_error_count integer NOT NULL CHECK (parse_error_count BETWEEN 0 AND 1000000),
    output_truncated boolean NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, frontier_id),
    UNIQUE (tenant_id, site_id, observation_id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, frontier_id)
        REFERENCES app.crawl_frontier (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, url_id)
        REFERENCES app.urls (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, observation_id)
        REFERENCES app.fetch_observations (tenant_id, site_id, id),
    CHECK (canonical_url IS NULL OR length(canonical_url) BETWEEN 1 AND 2048),
    CHECK (jsonb_typeof(robots_meta) = 'array' AND jsonb_array_length(robots_meta) <= 32),
    CHECK (jsonb_typeof(headings) = 'array' AND jsonb_array_length(headings) <= 512),
    CHECK (jsonb_typeof(hreflang) = 'array' AND jsonb_array_length(hreflang) <= 256),
    CHECK (jsonb_typeof(structured_data_types) = 'array'
        AND jsonb_array_length(structured_data_types) <= 256),
    CHECK (jsonb_typeof(internal_links) = 'array'
        AND jsonb_array_length(internal_links) <= 4096),
    CHECK (jsonb_typeof(external_links) = 'array'
        AND jsonb_array_length(external_links) <= 4096)
);

CREATE INDEX crawl_page_records_run
ON app.crawl_page_records (tenant_id, site_id, crawl_run_id, recorded_at, id);

CREATE TABLE app.crawl_frontier_settlements (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    frontier_id uuid NOT NULL,
    url_id uuid NOT NULL,
    frontier_lease_id uuid,
    terminal_state text NOT NULL CHECK (terminal_state IN (
        'fetched', 'robots_denied', 'budget_exhausted', 'http_error',
        'transport_error', 'policy_rejected', 'parse_error', 'dispatch_unknown',
        'attempts_exhausted'
    )),
    error_class text CHECK (error_class IS NULL OR error_class IN (
        'redirect', 'client_error', 'server_error', 'rate_limited', 'body_limit',
        'unsupported_media_type', 'unsupported_encoding', 'transport', 'policy',
        'parser', 'duration_budget', 'byte_budget', 'count_budget',
        'dispatch_outcome_unknown', 'worker_lost', 'attempt_limit'
    )),
    egress_operation_id uuid,
    robots_snapshot_id uuid,
    observation_id uuid,
    page_record_id uuid,
    decoded_bytes integer NOT NULL CHECK (decoded_bytes BETWEEN 0 AND 5242880),
    settled_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, frontier_id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, frontier_id)
        REFERENCES app.crawl_frontier (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, url_id)
        REFERENCES app.urls (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id, frontier_id, frontier_lease_id)
        REFERENCES app.crawl_frontier_leases
            (tenant_id, site_id, crawl_run_id, frontier_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id, egress_operation_id)
        REFERENCES app.egress_operations (tenant_id, site_id, crawl_run_id, id),
    FOREIGN KEY (tenant_id, site_id, robots_snapshot_id)
        REFERENCES app.robots_snapshots (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, observation_id)
        REFERENCES app.fetch_observations (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, page_record_id)
        REFERENCES app.crawl_page_records (tenant_id, site_id, id),
    CHECK (
        (terminal_state = 'fetched' AND error_class IS NULL
            AND egress_operation_id IS NOT NULL AND robots_snapshot_id IS NOT NULL
            AND observation_id IS NOT NULL AND page_record_id IS NOT NULL)
        OR
        (terminal_state = 'parse_error' AND error_class = 'parser'
            AND egress_operation_id IS NOT NULL AND robots_snapshot_id IS NOT NULL
            AND observation_id IS NOT NULL AND page_record_id IS NULL)
        OR
        (terminal_state NOT IN ('fetched', 'parse_error') AND error_class IS NOT NULL
            AND observation_id IS NULL AND page_record_id IS NULL)
    ),
    CHECK (frontier_lease_id IS NOT NULL OR terminal_state = 'budget_exhausted'),
    CHECK (decoded_bytes = 0 OR terminal_state IN ('fetched', 'http_error', 'parse_error'))
);

CREATE INDEX crawl_frontier_settlements_run
ON app.crawl_frontier_settlements
    (tenant_id, site_id, crawl_run_id, terminal_state, settled_at, id);

CREATE TABLE app.crawl_manifests (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
    coverage text NOT NULL CHECK (coverage IN ('complete', 'partial')),
    discovered_count integer NOT NULL CHECK (discovered_count BETWEEN 0 AND 1000000),
    terminal_count integer NOT NULL CHECK (
        terminal_count BETWEEN 0 AND 1000000 AND terminal_count <= discovered_count
    ),
    fetched_bytes bigint NOT NULL CHECK (fetched_bytes BETWEEN 0 AND 107374182400),
    state_counts jsonb NOT NULL,
    completed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    CHECK (jsonb_typeof(state_counts) = 'object'),
    CHECK (terminal_count = discovered_count)
);

CREATE INDEX crawl_manifests_site_completed
ON app.crawl_manifests (tenant_id, site_id, completed_at DESC, id);

CREATE FUNCTION app.guard_full_crawl_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_full_crawl_evidence' USING ERRCODE = '55000'; END $$;

CREATE FUNCTION app.guard_robots_dispatch_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.crawl_run_id IS DISTINCT FROM NEW.crawl_run_id
       OR OLD.frontier_id IS DISTINCT FROM NEW.frontier_id
       OR OLD.frontier_lease_id IS DISTINCT FROM NEW.frontier_lease_id
       OR OLD.origin_permit_id IS DISTINCT FROM NEW.origin_permit_id
       OR OLD.worker_key IS DISTINCT FROM NEW.worker_key
       OR OLD.request_url IS DISTINCT FROM NEW.request_url
       OR OLD.request_sha256 IS DISTINCT FROM NEW.request_sha256
       OR OLD.max_response_bytes IS DISTINCT FROM NEW.max_response_bytes
       OR OLD.dispatched_at IS DISTINCT FROM NEW.dispatched_at
       OR OLD.state <> 'dispatched'
       OR NEW.state NOT IN ('observed', 'failed')
    THEN
        RAISE EXCEPTION 'immutable_robots_dispatch' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION app.guard_full_crawl_immutable() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.guard_robots_dispatch_mutation() FROM PUBLIC;

CREATE TRIGGER crawl_robots_dispatches_guard BEFORE UPDATE OR DELETE
ON app.crawl_robots_dispatches FOR EACH ROW
EXECUTE FUNCTION app.guard_robots_dispatch_mutation();
CREATE TRIGGER crawl_page_records_immutable BEFORE UPDATE OR DELETE
ON app.crawl_page_records FOR EACH ROW EXECUTE FUNCTION app.guard_full_crawl_immutable();
CREATE TRIGGER crawl_frontier_settlements_immutable BEFORE UPDATE OR DELETE
ON app.crawl_frontier_settlements FOR EACH ROW EXECUTE FUNCTION app.guard_full_crawl_immutable();
CREATE TRIGGER crawl_manifests_immutable BEFORE UPDATE OR DELETE
ON app.crawl_manifests FOR EACH ROW EXECUTE FUNCTION app.guard_full_crawl_immutable();

CREATE FUNCTION app.guard_verified_crawl_egress() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.purpose <> 'crawl' THEN RETURN NEW; END IF;
    PERFORM 1
      FROM app.crawl_runs AS run
      JOIN app.sites AS site
        ON site.tenant_id = run.tenant_id AND site.id = run.site_id
      JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = site.tenant_id AND verification.site_id = site.id
       AND verification.origin = site.primary_origin
      JOIN control.public_origin_claims AS claim
        ON claim.origin = verification.origin AND claim.tenant_id = verification.tenant_id
       AND claim.site_id = verification.site_id AND claim.verification_id = verification.id
      JOIN app.crawl_frontier AS frontier
        ON frontier.tenant_id = run.tenant_id AND frontier.site_id = run.site_id
       AND frontier.crawl_run_id = run.id
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
     WHERE run.tenant_id = NEW.tenant_id AND run.site_id = NEW.site_id
       AND run.id = NEW.crawl_run_id AND run.status = 'running'
       AND site.state <> 'archived' AND site.ownership_status = 'verified'
       AND verification.recheck_at > clock_timestamp()
       AND claim.recheck_at > clock_timestamp()
       AND jsonb_array_length(run.scope_snapshot->'allowed_origins') = 1
       AND run.scope_snapshot->'allowed_origins'->>0 = site.primary_origin
       AND frontier.status = 'leased' AND frontier.lease_until > clock_timestamp()
       AND url.fetch_url = NEW.request_url AND url.origin = NEW.origin
       AND NEW.origin = site.primary_origin
     FOR SHARE OF run, site, verification, claim, frontier, url;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'verified_crawl_egress_unavailable' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION app.guard_verified_crawl_egress() FROM PUBLIC;
CREATE TRIGGER egress_operations_verified_crawl BEFORE INSERT
ON app.egress_operations FOR EACH ROW
EXECUTE FUNCTION app.guard_verified_crawl_egress();

ALTER TABLE app.crawl_robots_dispatches ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_robots_dispatches FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_robots_dispatch_scope ON app.crawl_robots_dispatches
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.crawl_page_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_page_records FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_page_record_scope ON app.crawl_page_records
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.crawl_frontier_settlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_frontier_settlements FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_frontier_settlement_scope ON app.crawl_frontier_settlements
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.crawl_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_manifests FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_manifest_scope ON app.crawl_manifests
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.crawl_frontier
    DROP CONSTRAINT crawl_frontier_status_check,
    DROP CONSTRAINT crawl_frontier_check1,
    ADD CONSTRAINT crawl_frontier_status_check CHECK (
        status IN ('pending', 'leased', 'permanently_failed', 'settled')
    ),
    ADD CONSTRAINT crawl_frontier_state_shape CHECK (
        (status = 'pending' AND attempt_count = 0
            AND lease_id IS NULL AND lease_owner IS NULL AND lease_until IS NULL
            AND terminal_at IS NULL)
        OR
        (status = 'leased' AND attempt_count > 0
            AND lease_id IS NOT NULL AND lease_owner IS NOT NULL
            AND lease_until IS NOT NULL AND terminal_at IS NULL)
        OR
        (status = 'permanently_failed' AND attempt_count > 0
            AND lease_id IS NOT NULL AND lease_owner IS NOT NULL
            AND lease_until IS NOT NULL AND terminal_at IS NOT NULL)
        OR
        (status = 'settled' AND terminal_at IS NOT NULL
            AND ((attempt_count = 0 AND lease_id IS NULL AND lease_owner IS NULL
                    AND lease_until IS NULL)
                 OR (attempt_count > 0 AND lease_id IS NOT NULL AND lease_owner IS NOT NULL
                    AND lease_until IS NOT NULL)))
    );

CREATE OR REPLACE FUNCTION app.guard_crawl_frontier_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.crawl_run_id IS DISTINCT FROM NEW.crawl_run_id
       OR OLD.url_id IS DISTINCT FROM NEW.url_id
       OR OLD.discovered_from_url_id IS DISTINCT FROM NEW.discovered_from_url_id
       OR OLD.depth IS DISTINCT FROM NEW.depth
       OR OLD.discovery_reason IS DISTINCT FROM NEW.discovery_reason
       OR OLD.priority IS DISTINCT FROM NEW.priority
       OR OLD.next_attempt_at IS DISTINCT FROM NEW.next_attempt_at
    THEN
        RAISE EXCEPTION 'immutable_crawl_frontier_identity' USING ERRCODE = '55000';
    END IF;
    IF OLD.status = 'pending' AND NEW.status = 'leased'
       AND OLD.attempt_count = 0 AND NEW.attempt_count = 1
       AND NEW.lease_id IS NOT NULL AND NEW.lease_owner IS NOT NULL
       AND NEW.lease_until > statement_timestamp() AND NEW.terminal_at IS NULL
       AND EXISTS (SELECT 1 FROM app.crawl_frontier_leases AS receipt
            WHERE receipt.tenant_id = NEW.tenant_id AND receipt.site_id = NEW.site_id
              AND receipt.crawl_run_id = NEW.crawl_run_id
              AND receipt.frontier_id = NEW.id AND receipt.id = NEW.lease_id
              AND receipt.lease_owner = NEW.lease_owner
              AND receipt.attempt_number = NEW.attempt_count
              AND receipt.expires_at = NEW.lease_until)
    THEN RETURN NEW; END IF;
    IF OLD.status = 'leased' AND OLD.lease_until <= statement_timestamp()
       AND OLD.attempt_count < 10 AND NEW.status = 'leased'
       AND NEW.attempt_count = OLD.attempt_count + 1
       AND NEW.lease_id IS DISTINCT FROM OLD.lease_id
       AND NEW.lease_owner IS NOT NULL AND NEW.lease_until > statement_timestamp()
       AND NEW.terminal_at IS NULL
       AND EXISTS (SELECT 1 FROM app.crawl_frontier_leases AS receipt
            WHERE receipt.tenant_id = NEW.tenant_id AND receipt.site_id = NEW.site_id
              AND receipt.crawl_run_id = NEW.crawl_run_id
              AND receipt.frontier_id = NEW.id AND receipt.id = NEW.lease_id
              AND receipt.lease_owner = NEW.lease_owner
              AND receipt.attempt_number = NEW.attempt_count
              AND receipt.expires_at = NEW.lease_until)
       AND NOT EXISTS (SELECT 1 FROM app.crawl_robots_dispatches AS dispatch
            WHERE dispatch.tenant_id = OLD.tenant_id AND dispatch.site_id = OLD.site_id
              AND dispatch.crawl_run_id = OLD.crawl_run_id
              AND dispatch.frontier_id = OLD.id
              AND dispatch.frontier_lease_id = OLD.lease_id)
       AND NOT EXISTS (SELECT 1 FROM app.egress_operations AS operation
            JOIN app.urls AS url ON url.tenant_id = OLD.tenant_id
                AND url.site_id = OLD.site_id AND url.id = OLD.url_id
            JOIN app.crawl_frontier_leases AS previous
              ON previous.tenant_id = OLD.tenant_id AND previous.site_id = OLD.site_id
             AND previous.crawl_run_id = OLD.crawl_run_id
             AND previous.frontier_id = OLD.id AND previous.id = OLD.lease_id
            WHERE operation.tenant_id = OLD.tenant_id
              AND operation.site_id = OLD.site_id
              AND operation.crawl_run_id = OLD.crawl_run_id
              AND operation.purpose = 'crawl' AND operation.request_url = url.fetch_url
              AND operation.dispatched_at >= previous.issued_at)
    THEN RETURN NEW; END IF;
    IF OLD.status = 'leased' AND OLD.lease_until <= statement_timestamp()
       AND NEW.status = 'permanently_failed'
       AND NEW.attempt_count = OLD.attempt_count AND NEW.lease_id = OLD.lease_id
       AND NEW.lease_owner = OLD.lease_owner AND NEW.lease_until = OLD.lease_until
       AND NEW.terminal_at >= OLD.lease_until
    THEN RETURN NEW; END IF;
    IF OLD.status IN ('pending', 'leased', 'permanently_failed')
       AND NEW.status = 'settled' AND NEW.attempt_count = OLD.attempt_count
       AND NEW.lease_id IS NOT DISTINCT FROM OLD.lease_id
       AND NEW.lease_owner IS NOT DISTINCT FROM OLD.lease_owner
       AND NEW.lease_until IS NOT DISTINCT FROM OLD.lease_until
       AND NEW.terminal_at IS NOT NULL
       AND EXISTS (SELECT 1 FROM app.crawl_frontier_settlements AS settlement
            WHERE settlement.tenant_id = NEW.tenant_id AND settlement.site_id = NEW.site_id
              AND settlement.crawl_run_id = NEW.crawl_run_id
              AND settlement.frontier_id = NEW.id)
    THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'invalid_crawl_frontier_transition' USING ERRCODE = '55000';
END;
$$;

ALTER TABLE app.crawl_runs
    DROP CONSTRAINT crawl_runs_status_check,
    DROP CONSTRAINT crawl_runs_check1,
    ADD CONSTRAINT crawl_runs_status_check CHECK (status IN ('running', 'completed')),
    ADD CONSTRAINT crawl_runs_terminal_shape CHECK (
        (status = 'running' AND ended_at IS NULL AND coverage_summary IS NULL)
        OR (status = 'completed' AND ended_at IS NOT NULL AND coverage_summary IS NOT NULL)
    );

CREATE OR REPLACE FUNCTION app.guard_crawl_run_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.command_id IS DISTINCT FROM NEW.command_id
       OR OLD.workflow_id IS DISTINCT FROM NEW.workflow_id
       OR OLD.first_run_id IS DISTINCT FROM NEW.first_run_id
       OR OLD.scope_version IS DISTINCT FROM NEW.scope_version
       OR OLD.crawl_policy_version IS DISTINCT FROM NEW.crawl_policy_version
       OR OLD.scope_snapshot IS DISTINCT FROM NEW.scope_snapshot
       OR OLD.limits_snapshot IS DISTINCT FROM NEW.limits_snapshot
       OR OLD.fetch_profile_hash IS DISTINCT FROM NEW.fetch_profile_hash
       OR OLD.started_at IS DISTINCT FROM NEW.started_at
    THEN RAISE EXCEPTION 'immutable_crawl_run' USING ERRCODE = '55000'; END IF;
    IF OLD.status = 'running' AND NEW.status = 'completed'
       AND NEW.ended_at IS NOT NULL AND NEW.coverage_summary IS NOT NULL
       AND EXISTS (SELECT 1 FROM app.crawl_manifests AS manifest
            WHERE manifest.tenant_id = NEW.tenant_id AND manifest.site_id = NEW.site_id
              AND manifest.crawl_run_id = NEW.id
              AND manifest.completed_at = NEW.ended_at
              AND manifest.state_counts = NEW.coverage_summary)
    THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'immutable_crawl_run' USING ERRCODE = '55000';
END;
$$;

CREATE FUNCTION control.begin_crawl_robots_dispatch(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid,
    p_frontier_id uuid, p_frontier_lease_id uuid, p_origin_permit_id uuid,
    p_worker_key text, p_dispatch_id uuid, p_request_url text,
    p_request_sha256 bytea, p_max_response_bytes integer
)
RETURNS TABLE (dispatch_id uuid, dispatch_state text, duplicate boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_existing app.crawl_robots_dispatches%%ROWTYPE;
BEGIN
    IF p_dispatch_id IS NULL OR p_tenant_id IS NULL OR p_site_id IS NULL
       OR p_crawl_run_id IS NULL OR p_frontier_id IS NULL
       OR p_frontier_lease_id IS NULL OR p_origin_permit_id IS NULL
       OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR octet_length(p_request_sha256) IS DISTINCT FROM 32
       OR p_max_response_bytes NOT BETWEEN 1 AND 524288
    THEN RAISE EXCEPTION 'invalid_robots_dispatch_input' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_existing FROM app.crawl_robots_dispatches AS dispatch
     WHERE dispatch.tenant_id = p_tenant_id AND dispatch.id = p_dispatch_id;
    IF FOUND THEN
        IF v_existing.site_id IS DISTINCT FROM p_site_id
           OR v_existing.crawl_run_id IS DISTINCT FROM p_crawl_run_id
           OR v_existing.frontier_id IS DISTINCT FROM p_frontier_id
           OR v_existing.frontier_lease_id IS DISTINCT FROM p_frontier_lease_id
           OR v_existing.origin_permit_id IS DISTINCT FROM p_origin_permit_id
           OR v_existing.worker_key IS DISTINCT FROM p_worker_key
           OR v_existing.request_url IS DISTINCT FROM p_request_url
           OR v_existing.request_sha256 IS DISTINCT FROM p_request_sha256
           OR v_existing.max_response_bytes IS DISTINCT FROM p_max_response_bytes
        THEN RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::boolean,
                                  'dispatch_conflict'::text; RETURN; END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.state, true,
            CASE WHEN v_existing.state = 'dispatched'
                 THEN 'dispatch_unknown' ELSE 'replayed' END;
        RETURN;
    END IF;
    PERFORM 1
      FROM app.crawl_runs AS run
      JOIN app.crawl_frontier AS frontier
        ON frontier.tenant_id = run.tenant_id AND frontier.site_id = run.site_id
       AND frontier.crawl_run_id = run.id
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
      JOIN app.sites AS site
        ON site.tenant_id = run.tenant_id AND site.id = run.site_id
      JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = site.tenant_id AND verification.site_id = site.id
       AND verification.origin = site.primary_origin
      JOIN control.public_origin_claims AS claim
        ON claim.origin = verification.origin AND claim.tenant_id = verification.tenant_id
       AND claim.site_id = verification.site_id AND claim.verification_id = verification.id
      JOIN control.admission_leases AS permit
        ON permit.id = p_origin_permit_id
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id AND run.status = 'running'
       AND frontier.id = p_frontier_id AND frontier.status = 'leased'
       AND frontier.lease_id = p_frontier_lease_id
       AND frontier.lease_owner = p_worker_key
       AND frontier.lease_until > clock_timestamp()
       AND url.origin = site.primary_origin
       AND site.ownership_status = 'verified'
       AND verification.recheck_at > clock_timestamp()
       AND claim.recheck_at > clock_timestamp()
       AND p_request_url = site.primary_origin || '/robots.txt'
       AND permit.released_at IS NULL AND permit.expires_at > clock_timestamp()
       AND permit.permit_kind = 'robots' AND permit.workload_id =
            'crawl:' || p_crawl_run_id::text || ':' || p_frontier_lease_id::text
     FOR SHARE OF run, frontier, url, site, verification, claim, permit;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::boolean,
                            'authority_unavailable'::text; RETURN;
    END IF;
    INSERT INTO app.crawl_robots_dispatches (
        tenant_id, site_id, id, crawl_run_id, frontier_id, frontier_lease_id,
        origin_permit_id, worker_key, request_url, request_sha256,
        max_response_bytes, dispatched_at
    ) VALUES (
        p_tenant_id, p_site_id, p_dispatch_id, p_crawl_run_id, p_frontier_id,
        p_frontier_lease_id, p_origin_permit_id, p_worker_key, p_request_url,
        p_request_sha256, p_max_response_bytes, clock_timestamp()
    );
    RETURN QUERY SELECT p_dispatch_id, 'dispatched'::text, false, 'admitted'::text;
END;
$$;

CREATE FUNCTION control.verified_crawl_origin(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid
)
RETURNS TABLE (origin text, seed_url text, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY
    SELECT site.primary_origin,
           run.scope_snapshot->'seed_urls'->>0,
           'verified'::text
      FROM app.crawl_runs AS run
      JOIN app.sites AS site
        ON site.tenant_id = run.tenant_id AND site.id = run.site_id
      JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = site.tenant_id AND verification.site_id = site.id
       AND verification.origin = site.primary_origin
      JOIN control.public_origin_claims AS claim
        ON claim.origin = verification.origin AND claim.tenant_id = verification.tenant_id
       AND claim.site_id = verification.site_id AND claim.verification_id = verification.id
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id AND run.status = 'running'
       AND site.state <> 'archived' AND site.ownership_status = 'verified'
       AND verification.recheck_at > clock_timestamp()
       AND claim.recheck_at > clock_timestamp()
       AND jsonb_array_length(run.scope_snapshot->'allowed_origins') = 1
       AND run.scope_snapshot->'allowed_origins'->>0 = site.primary_origin
       AND run.scope_snapshot->'seed_urls'->>0 LIKE site.primary_origin || '/%%'
     ORDER BY verification.verified_at DESC, verification.id DESC
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::text, NULL::text, 'unavailable'::text;
    END IF;
END;
$$;

CREATE FUNCTION control.verified_site_origin(p_tenant_id uuid, p_site_id uuid)
RETURNS TABLE (origin text, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY
    SELECT site.primary_origin, 'verified'::text
      FROM app.sites AS site
      JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = site.tenant_id AND verification.site_id = site.id
       AND verification.origin = site.primary_origin
      JOIN control.public_origin_claims AS claim
        ON claim.origin = verification.origin AND claim.tenant_id = verification.tenant_id
       AND claim.site_id = verification.site_id AND claim.verification_id = verification.id
     WHERE site.tenant_id = p_tenant_id AND site.id = p_site_id
       AND site.state <> 'archived' AND site.ownership_status = 'verified'
       AND verification.recheck_at > clock_timestamp()
       AND claim.recheck_at > clock_timestamp()
     ORDER BY verification.verified_at DESC, verification.id DESC
     LIMIT 1;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::text, 'unavailable'::text; END IF;
END;
$$;

CREATE FUNCTION control.finish_crawl_robots_dispatch(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid, p_dispatch_id uuid,
    p_request_sha256 bytea, p_network_outcome text, p_robots_snapshot_id uuid
)
RETURNS TABLE (dispatch_state text, finished_at timestamptz, duplicate boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_dispatch app.crawl_robots_dispatches%%ROWTYPE; v_snapshot record;
BEGIN
    IF octet_length(p_request_sha256) IS DISTINCT FROM 32 OR p_network_outcome NOT IN (
        'fetched', 'not_found', 'forbidden', 'backoff', 'server_error', 'client_error',
        'transport_error', 'policy_rejected', 'unsupported_encoding',
        'unsupported_media_type', 'body_limit'
    ) THEN RAISE EXCEPTION 'invalid_robots_completion_input' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_dispatch FROM app.crawl_robots_dispatches AS dispatch
     WHERE dispatch.tenant_id = p_tenant_id AND dispatch.site_id = p_site_id
       AND dispatch.crawl_run_id = p_crawl_run_id AND dispatch.id = p_dispatch_id
     FOR UPDATE;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::text, NULL::timestamptz, NULL::boolean,
                                         'dispatch_unavailable'::text; RETURN; END IF;
    IF v_dispatch.request_sha256 IS DISTINCT FROM p_request_sha256 THEN
        RETURN QUERY SELECT NULL::text, NULL::timestamptz, NULL::boolean,
                            'dispatch_conflict'::text; RETURN;
    END IF;
    IF v_dispatch.state <> 'dispatched' THEN
        IF v_dispatch.network_outcome = p_network_outcome
           AND v_dispatch.robots_snapshot_id = p_robots_snapshot_id
        THEN RETURN QUERY SELECT v_dispatch.state, v_dispatch.finished_at, true,
                                 'finished'::text;
        ELSE RETURN QUERY SELECT NULL::text, NULL::timestamptz, NULL::boolean,
                                 'dispatch_conflict'::text; END IF;
        RETURN;
    END IF;
    SELECT snapshot.id, snapshot.retrieval_outcome INTO v_snapshot
      FROM app.robots_snapshots AS snapshot
     WHERE snapshot.tenant_id = p_tenant_id AND snapshot.site_id = p_site_id
       AND snapshot.crawl_run_id = p_crawl_run_id AND snapshot.id = p_robots_snapshot_id
     FOR SHARE;
    IF NOT FOUND OR v_snapshot.retrieval_outcome <> p_network_outcome THEN
        RETURN QUERY SELECT NULL::text, NULL::timestamptz, NULL::boolean,
                            'snapshot_unavailable'::text; RETURN;
    END IF;
    UPDATE app.crawl_robots_dispatches AS dispatch
       SET state = CASE WHEN p_network_outcome IN ('fetched', 'not_found')
                        THEN 'observed' ELSE 'failed' END,
           finished_at = clock_timestamp(), network_outcome = p_network_outcome,
           robots_snapshot_id = p_robots_snapshot_id
     WHERE dispatch.tenant_id = p_tenant_id AND dispatch.id = p_dispatch_id
     RETURNING dispatch.state, dispatch.finished_at INTO v_dispatch.state, v_dispatch.finished_at;
    RETURN QUERY SELECT v_dispatch.state, v_dispatch.finished_at, false, 'finished'::text;
END;
$$;

CREATE FUNCTION control.record_crawl_page(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid, p_frontier_id uuid,
    p_url_id uuid, p_observation_id uuid, p_page_record_id uuid, p_http_status integer,
    p_canonical_url text, p_title text, p_meta_description text, p_robots_meta jsonb,
    p_headings jsonb, p_hreflang jsonb, p_structured_data_types jsonb,
    p_internal_links jsonb, p_external_links jsonb, p_body_sha256 bytea,
    p_parse_error_count integer, p_output_truncated boolean
)
RETURNS TABLE (page_record_id uuid, duplicate boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_existing app.crawl_page_records%%ROWTYPE;
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_existing FROM app.crawl_page_records AS page
     WHERE page.tenant_id = p_tenant_id AND page.id = p_page_record_id;
    IF FOUND THEN
        IF v_existing.site_id = p_site_id AND v_existing.crawl_run_id = p_crawl_run_id
           AND v_existing.frontier_id = p_frontier_id AND v_existing.url_id = p_url_id
           AND v_existing.observation_id = p_observation_id
           AND v_existing.http_status = p_http_status
           AND v_existing.canonical_url IS NOT DISTINCT FROM p_canonical_url
           AND v_existing.title IS NOT DISTINCT FROM p_title
           AND v_existing.meta_description IS NOT DISTINCT FROM p_meta_description
           AND v_existing.robots_meta = p_robots_meta
           AND v_existing.headings = p_headings
           AND v_existing.hreflang = p_hreflang
           AND v_existing.structured_data_types = p_structured_data_types
           AND v_existing.internal_links = p_internal_links
           AND v_existing.external_links = p_external_links
           AND v_existing.body_sha256 = p_body_sha256
           AND v_existing.parse_error_count = p_parse_error_count
           AND v_existing.output_truncated = p_output_truncated
        THEN RETURN QUERY SELECT v_existing.id, true, 'recorded'::text;
        ELSE RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'page_conflict'::text; END IF;
        RETURN;
    END IF;
    PERFORM 1 FROM app.fetch_observations AS observation
     JOIN app.crawl_frontier AS frontier
       ON frontier.tenant_id = observation.tenant_id
      AND frontier.site_id = observation.site_id
      AND frontier.id = observation.frontier_id
     JOIN app.artifacts AS artifact
       ON artifact.tenant_id = observation.tenant_id
      AND artifact.site_id = observation.site_id
      AND artifact.id = observation.raw_artifact_id
     WHERE observation.tenant_id = p_tenant_id AND observation.site_id = p_site_id
       AND observation.crawl_run_id = p_crawl_run_id
       AND observation.frontier_id = p_frontier_id AND observation.url_id = p_url_id
       AND observation.id = p_observation_id AND observation.outcome = 'fetched'
       AND observation.http_status = p_http_status
       AND artifact.sha256 = p_body_sha256 AND frontier.status = 'leased'
     FOR SHARE OF observation, frontier, artifact;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid, NULL::boolean,
                                         'observation_unavailable'::text; RETURN; END IF;
    INSERT INTO app.crawl_page_records (
        tenant_id, site_id, id, crawl_run_id, frontier_id, url_id, observation_id,
        http_status, canonical_url, title, meta_description, robots_meta, headings,
        hreflang, structured_data_types, internal_links, external_links, body_sha256,
        parse_error_count, output_truncated, recorded_at
    ) VALUES (
        p_tenant_id, p_site_id, p_page_record_id, p_crawl_run_id, p_frontier_id,
        p_url_id, p_observation_id, p_http_status, p_canonical_url, p_title,
        p_meta_description, p_robots_meta, p_headings, p_hreflang,
        p_structured_data_types, p_internal_links, p_external_links, p_body_sha256,
        p_parse_error_count, p_output_truncated, clock_timestamp()
    );
    RETURN QUERY SELECT p_page_record_id, false, 'recorded'::text;
EXCEPTION WHEN check_violation OR invalid_text_representation THEN
    RAISE EXCEPTION 'invalid_crawl_page_input' USING ERRCODE = '22023';
END;
$$;

CREATE FUNCTION control.settle_crawl_frontier(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid, p_frontier_id uuid,
    p_url_id uuid, p_frontier_lease_id uuid, p_settlement_id uuid,
    p_terminal_state text, p_error_class text, p_egress_operation_id uuid,
    p_robots_snapshot_id uuid, p_observation_id uuid, p_page_record_id uuid,
    p_decoded_bytes integer
)
RETURNS TABLE (terminal_state text, consumed_bytes bigint, duplicate boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_frontier app.crawl_frontier%%ROWTYPE;
        v_existing app.crawl_frontier_settlements%%ROWTYPE;
        v_run app.crawl_runs%%ROWTYPE; v_consumed bigint;
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_existing FROM app.crawl_frontier_settlements AS settlement
     WHERE settlement.tenant_id = p_tenant_id AND settlement.id = p_settlement_id;
    IF FOUND THEN
        IF v_existing.site_id = p_site_id AND v_existing.crawl_run_id = p_crawl_run_id
           AND v_existing.frontier_id = p_frontier_id AND v_existing.url_id = p_url_id
           AND v_existing.frontier_lease_id IS NOT DISTINCT FROM p_frontier_lease_id
           AND v_existing.terminal_state = p_terminal_state
           AND v_existing.error_class IS NOT DISTINCT FROM p_error_class
           AND v_existing.egress_operation_id IS NOT DISTINCT FROM p_egress_operation_id
           AND v_existing.robots_snapshot_id IS NOT DISTINCT FROM p_robots_snapshot_id
           AND v_existing.observation_id IS NOT DISTINCT FROM p_observation_id
           AND v_existing.page_record_id IS NOT DISTINCT FROM p_page_record_id
           AND v_existing.decoded_bytes = p_decoded_bytes
        THEN
            SELECT COALESCE(sum(decoded_bytes), 0) INTO v_consumed
              FROM app.crawl_frontier_settlements
             WHERE tenant_id = p_tenant_id AND site_id = p_site_id
               AND crawl_run_id = p_crawl_run_id;
            RETURN QUERY SELECT v_existing.terminal_state, v_consumed, true,
                                'settled'::text;
        ELSE RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::boolean,
                                 'settlement_conflict'::text; END IF;
        RETURN;
    END IF;
    SELECT * INTO v_run FROM app.crawl_runs AS run
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id AND run.status = 'running' FOR UPDATE;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::boolean,
                                         'run_unavailable'::text; RETURN; END IF;
    SELECT * INTO v_frontier FROM app.crawl_frontier AS frontier
     WHERE frontier.tenant_id = p_tenant_id AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_crawl_run_id AND frontier.id = p_frontier_id
       AND frontier.url_id = p_url_id AND frontier.status = 'leased'
       AND frontier.lease_id = p_frontier_lease_id FOR UPDATE;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::boolean,
                                         'frontier_unavailable'::text; RETURN; END IF;
    IF p_egress_operation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM app.egress_operations AS operation
        JOIN app.urls AS url ON url.tenant_id = operation.tenant_id
            AND url.site_id = operation.site_id AND url.id = p_url_id
        JOIN app.crawl_frontier_leases AS receipt
          ON receipt.tenant_id = p_tenant_id AND receipt.site_id = p_site_id
         AND receipt.crawl_run_id = p_crawl_run_id
         AND receipt.frontier_id = p_frontier_id
         AND receipt.id = p_frontier_lease_id
        WHERE operation.tenant_id = p_tenant_id AND operation.site_id = p_site_id
          AND operation.crawl_run_id = p_crawl_run_id
          AND operation.id = p_egress_operation_id
          AND operation.purpose = 'crawl' AND operation.request_url = url.fetch_url
          AND operation.dispatched_at >= receipt.issued_at
          AND operation.robots_snapshot_id IS NOT DISTINCT FROM p_robots_snapshot_id
          AND (operation.state IN ('observed', 'failed')
               OR (p_terminal_state = 'dispatch_unknown'
                   AND operation.state = 'dispatched'))
    ) THEN RAISE EXCEPTION 'invalid_settlement_egress' USING ERRCODE = '22023'; END IF;
    IF p_observation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM app.fetch_observations AS observation
        WHERE observation.tenant_id = p_tenant_id AND observation.site_id = p_site_id
          AND observation.crawl_run_id = p_crawl_run_id
          AND observation.frontier_id = p_frontier_id
          AND observation.url_id = p_url_id
          AND observation.fetch_attempt_id = p_frontier_lease_id
          AND observation.id = p_observation_id
          AND observation.decoded_bytes = p_decoded_bytes
    ) THEN RAISE EXCEPTION 'invalid_settlement_observation' USING ERRCODE = '22023'; END IF;
    IF p_page_record_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM app.crawl_page_records AS page
        WHERE page.tenant_id = p_tenant_id AND page.site_id = p_site_id
          AND page.crawl_run_id = p_crawl_run_id
          AND page.frontier_id = p_frontier_id
          AND page.url_id = p_url_id AND page.observation_id = p_observation_id
          AND page.id = p_page_record_id
    ) THEN RAISE EXCEPTION 'invalid_settlement_page' USING ERRCODE = '22023'; END IF;
    IF p_decoded_bytes < 0 OR p_decoded_bytes >
        (v_run.limits_snapshot->>'max_body_bytes')::integer
    THEN RAISE EXCEPTION 'invalid_settlement_bytes' USING ERRCODE = '22023'; END IF;
    SELECT COALESCE(sum(decoded_bytes), 0) INTO v_consumed
      FROM app.crawl_frontier_settlements
     WHERE tenant_id = p_tenant_id AND site_id = p_site_id
       AND crawl_run_id = p_crawl_run_id;
    IF v_consumed + p_decoded_bytes >
        (v_run.limits_snapshot->>'max_total_bytes')::bigint
    THEN RETURN QUERY SELECT NULL::text, v_consumed, NULL::boolean,
                             'byte_budget_exhausted'::text; RETURN; END IF;
    INSERT INTO app.crawl_frontier_settlements (
        tenant_id, site_id, id, crawl_run_id, frontier_id, url_id,
        frontier_lease_id, terminal_state, error_class, egress_operation_id,
        robots_snapshot_id, observation_id, page_record_id, decoded_bytes, settled_at
    ) VALUES (
        p_tenant_id, p_site_id, p_settlement_id, p_crawl_run_id, p_frontier_id,
        p_url_id, p_frontier_lease_id, p_terminal_state, p_error_class,
        p_egress_operation_id, p_robots_snapshot_id, p_observation_id,
        p_page_record_id, p_decoded_bytes, clock_timestamp()
    );
    UPDATE app.crawl_frontier SET status = 'settled', terminal_at = clock_timestamp()
     WHERE tenant_id = p_tenant_id AND id = p_frontier_id;
    RETURN QUERY SELECT p_terminal_state, v_consumed + p_decoded_bytes, false,
                        'settled'::text;
EXCEPTION WHEN check_violation OR foreign_key_violation THEN
    RAISE EXCEPTION 'invalid_frontier_settlement' USING ERRCODE = '22023';
END;
$$;

CREATE FUNCTION control.crawl_run_progress(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid
)
RETURNS TABLE (
    run_status text, started_at timestamptz, max_duration_seconds integer,
    max_total_bytes bigint, discovered_count bigint, terminal_count bigint,
    consumed_bytes bigint, pending_count bigint, leased_count bigint,
    state_counts jsonb, outcome text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY
    SELECT run.status, run.started_at,
           (run.limits_snapshot->>'max_duration_seconds')::integer,
           (run.limits_snapshot->>'max_total_bytes')::bigint,
           count(frontier.id), count(settlement.id),
           COALESCE(sum(settlement.decoded_bytes), 0),
           count(frontier.id) FILTER (WHERE frontier.status = 'pending'),
           count(frontier.id) FILTER (WHERE frontier.status = 'leased'),
           jsonb_build_object(
             'attempts_exhausted', count(*) FILTER (WHERE terminal_state = 'attempts_exhausted'),
             'budget_exhausted', count(*) FILTER (WHERE terminal_state = 'budget_exhausted'),
             'dispatch_unknown', count(*) FILTER (WHERE terminal_state = 'dispatch_unknown'),
             'fetched', count(*) FILTER (WHERE terminal_state = 'fetched'),
             'http_error', count(*) FILTER (WHERE terminal_state = 'http_error'),
             'parse_error', count(*) FILTER (WHERE terminal_state = 'parse_error'),
             'policy_rejected', count(*) FILTER (WHERE terminal_state = 'policy_rejected'),
             'robots_denied', count(*) FILTER (WHERE terminal_state = 'robots_denied'),
             'transport_error', count(*) FILTER (WHERE terminal_state = 'transport_error')
           ),
           'found'::text
      FROM app.crawl_runs AS run
      LEFT JOIN app.crawl_frontier AS frontier
        ON frontier.tenant_id = run.tenant_id AND frontier.site_id = run.site_id
       AND frontier.crawl_run_id = run.id
      LEFT JOIN app.crawl_frontier_settlements AS settlement
        ON settlement.tenant_id = frontier.tenant_id
       AND settlement.site_id = frontier.site_id
       AND settlement.crawl_run_id = frontier.crawl_run_id
       AND settlement.frontier_id = frontier.id
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id
     GROUP BY run.status, run.started_at, run.limits_snapshot;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::text, NULL::timestamptz, NULL::integer, NULL::bigint,
            NULL::bigint, NULL::bigint, NULL::bigint, NULL::bigint, NULL::bigint, NULL::jsonb,
            'missing'::text;
    END IF;
END;
$$;

CREATE FUNCTION control.list_crawl_recovery_leases(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid, p_worker_key text
)
RETURNS TABLE (
    crawl_run_id uuid, frontier_id uuid, url_id uuid, original_url text,
    fetch_url text, normalized_key text, origin text, depth integer,
    discovery_reason text, priority integer, attempt_count integer,
    lease_id uuid, lease_owner text, lease_until timestamptz,
    scope_version integer, crawl_policy_version integer,
    scope_snapshot jsonb, limits_snapshot jsonb, fetch_profile_hash bytea,
    duplicate boolean, egress_operation_id uuid, egress_robots_snapshot_id uuid,
    robots_dispatched boolean,
    has_observation boolean
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    IF p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,127}$' THEN
        RAISE EXCEPTION 'invalid_recovery_worker' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY
    SELECT run.id, frontier.id, url.id, url.original_url, url.fetch_url,
           url.normalized_key, url.origin, frontier.depth,
           frontier.discovery_reason, frontier.priority, frontier.attempt_count,
           frontier.lease_id, frontier.lease_owner, frontier.lease_until,
           run.scope_version, run.crawl_policy_version,
           run.scope_snapshot, run.limits_snapshot, run.fetch_profile_hash,
           true, operation.id, operation.robots_snapshot_id,
           EXISTS (SELECT 1 FROM app.crawl_robots_dispatches AS dispatch
                WHERE dispatch.tenant_id = p_tenant_id AND dispatch.site_id = p_site_id
                  AND dispatch.crawl_run_id = p_crawl_run_id
                  AND dispatch.frontier_id = frontier.id
                  AND dispatch.frontier_lease_id = frontier.lease_id
                  AND dispatch.state = 'dispatched'),
           EXISTS (SELECT 1 FROM app.fetch_observations AS observation
                WHERE observation.tenant_id = p_tenant_id
                  AND observation.site_id = p_site_id
                  AND observation.crawl_run_id = p_crawl_run_id
                  AND observation.frontier_id = frontier.id
                  AND observation.fetch_attempt_id = frontier.lease_id)
      FROM app.crawl_runs AS run
      JOIN app.crawl_frontier AS frontier
        ON frontier.tenant_id = run.tenant_id AND frontier.site_id = run.site_id
       AND frontier.crawl_run_id = run.id
      JOIN app.crawl_frontier_leases AS receipt
        ON receipt.tenant_id = frontier.tenant_id
       AND receipt.site_id = frontier.site_id
       AND receipt.crawl_run_id = frontier.crawl_run_id
       AND receipt.frontier_id = frontier.id AND receipt.id = frontier.lease_id
      JOIN app.urls AS url ON url.tenant_id = frontier.tenant_id
       AND url.site_id = frontier.site_id AND url.id = frontier.url_id
      LEFT JOIN LATERAL (
          SELECT egress.id, egress.robots_snapshot_id
            FROM app.egress_operations AS egress
           WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
             AND egress.crawl_run_id = p_crawl_run_id AND egress.purpose = 'crawl'
             AND egress.request_url = url.fetch_url
             AND egress.dispatched_at >= receipt.issued_at
           ORDER BY egress.dispatched_at DESC, egress.id DESC LIMIT 1
      ) AS operation ON true
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id AND run.status = 'running'
       AND frontier.status = 'leased'
       AND (frontier.lease_owner = p_worker_key
            OR frontier.lease_until <= clock_timestamp())
     ORDER BY frontier.depth, frontier.id;
END;
$$;

CREATE FUNCTION control.finalize_crawl_run(
    p_tenant_id uuid, p_site_id uuid, p_crawl_run_id uuid,
    p_manifest_id uuid, p_manifest_sha256 bytea
)
RETURNS TABLE (
    manifest_id uuid, manifest_hash bytea, coverage text,
    discovered_count integer, terminal_count integer, fetched_bytes bigint,
    state_counts jsonb, completed_at timestamptz, duplicate boolean, outcome text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_run app.crawl_runs%%ROWTYPE; v_manifest app.crawl_manifests%%ROWTYPE;
        v_now timestamptz := clock_timestamp(); v_counts jsonb; v_discovered integer;
        v_terminal integer; v_bytes bigint; v_coverage text; v_expected_hash bytea;
BEGIN
    IF octet_length(p_manifest_sha256) IS DISTINCT FROM 32 THEN
        RAISE EXCEPTION 'invalid_manifest_hash' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_manifest FROM app.crawl_manifests AS manifest
     WHERE manifest.tenant_id = p_tenant_id AND manifest.id = p_manifest_id;
    IF FOUND THEN
        IF v_manifest.site_id = p_site_id AND v_manifest.crawl_run_id = p_crawl_run_id
           AND v_manifest.manifest_sha256 = p_manifest_sha256
        THEN RETURN QUERY SELECT v_manifest.id, v_manifest.manifest_sha256,
            v_manifest.coverage, v_manifest.discovered_count, v_manifest.terminal_count,
            v_manifest.fetched_bytes, v_manifest.state_counts, v_manifest.completed_at,
            true, 'finalized'::text;
        ELSE RETURN QUERY SELECT NULL::uuid, NULL::bytea, NULL::text, NULL::integer,
            NULL::integer, NULL::bigint, NULL::jsonb, NULL::timestamptz,
            NULL::boolean, 'manifest_conflict'::text; END IF;
        RETURN;
    END IF;
    SELECT * INTO v_run FROM app.crawl_runs AS run
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id FOR UPDATE;
    IF NOT FOUND OR v_run.status <> 'running' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, NULL::text, NULL::integer,
            NULL::integer, NULL::bigint, NULL::jsonb, NULL::timestamptz,
            NULL::boolean, 'run_unavailable'::text; RETURN;
    END IF;
    IF v_now >= v_run.started_at + make_interval(
        secs => (v_run.limits_snapshot->>'max_duration_seconds')::integer
    ) OR (SELECT COALESCE(sum(decoded_bytes), 0)
            FROM app.crawl_frontier_settlements
           WHERE tenant_id = p_tenant_id AND site_id = p_site_id
             AND crawl_run_id = p_crawl_run_id) >=
         (v_run.limits_snapshot->>'max_total_bytes')::bigint THEN
        INSERT INTO app.crawl_frontier_settlements (
            tenant_id, site_id, id, crawl_run_id, frontier_id, url_id,
            frontier_lease_id, terminal_state, error_class, decoded_bytes, settled_at
        )
        SELECT frontier.tenant_id, frontier.site_id, frontier.id, frontier.crawl_run_id,
               frontier.id, frontier.url_id, NULL, 'budget_exhausted',
               CASE WHEN v_now >= v_run.started_at + make_interval(
                    secs => (v_run.limits_snapshot->>'max_duration_seconds')::integer
               ) THEN 'duration_budget' ELSE 'byte_budget' END, 0, v_now
          FROM app.crawl_frontier AS frontier
         WHERE frontier.tenant_id = p_tenant_id AND frontier.site_id = p_site_id
           AND frontier.crawl_run_id = p_crawl_run_id AND frontier.status = 'pending'
        ON CONFLICT DO NOTHING;
        UPDATE app.crawl_frontier AS frontier SET status = 'settled', terminal_at = v_now
         WHERE frontier.tenant_id = p_tenant_id AND frontier.site_id = p_site_id
           AND frontier.crawl_run_id = p_crawl_run_id AND frontier.status = 'pending';
    END IF;
    INSERT INTO app.crawl_frontier_settlements (
        tenant_id, site_id, id, crawl_run_id, frontier_id, url_id,
        frontier_lease_id, terminal_state, error_class, decoded_bytes, settled_at
    )
    SELECT frontier.tenant_id, frontier.site_id, frontier.id, frontier.crawl_run_id,
           frontier.id, frontier.url_id, frontier.lease_id, 'attempts_exhausted',
           'attempt_limit', 0, v_now
      FROM app.crawl_frontier AS frontier
     WHERE frontier.tenant_id = p_tenant_id AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_crawl_run_id
       AND frontier.status = 'permanently_failed'
    ON CONFLICT DO NOTHING;
    UPDATE app.crawl_frontier AS frontier SET status = 'settled', terminal_at = v_now
     WHERE frontier.tenant_id = p_tenant_id AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_crawl_run_id
       AND frontier.status = 'permanently_failed';
    IF EXISTS (SELECT 1 FROM app.crawl_frontier AS frontier
        WHERE frontier.tenant_id = p_tenant_id AND frontier.site_id = p_site_id
          AND frontier.crawl_run_id = p_crawl_run_id AND frontier.status <> 'settled')
    THEN RETURN QUERY SELECT NULL::uuid, NULL::bytea, NULL::text, NULL::integer,
        NULL::integer, NULL::bigint, NULL::jsonb, NULL::timestamptz,
        NULL::boolean, 'not_ready'::text; RETURN; END IF;
    SELECT count(*)::integer, count(settlement.id)::integer,
           COALESCE(sum(settlement.decoded_bytes), 0),
           jsonb_build_object(
             'attempts_exhausted', count(*) FILTER (WHERE terminal_state = 'attempts_exhausted'),
             'budget_exhausted', count(*) FILTER (WHERE terminal_state = 'budget_exhausted'),
             'dispatch_unknown', count(*) FILTER (WHERE terminal_state = 'dispatch_unknown'),
             'fetched', count(*) FILTER (WHERE terminal_state = 'fetched'),
             'http_error', count(*) FILTER (WHERE terminal_state = 'http_error'),
             'parse_error', count(*) FILTER (WHERE terminal_state = 'parse_error'),
             'policy_rejected', count(*) FILTER (WHERE terminal_state = 'policy_rejected'),
             'robots_denied', count(*) FILTER (WHERE terminal_state = 'robots_denied'),
             'transport_error', count(*) FILTER (WHERE terminal_state = 'transport_error')
           )
      INTO v_discovered, v_terminal, v_bytes, v_counts
      FROM app.crawl_frontier AS frontier
      LEFT JOIN app.crawl_frontier_settlements AS settlement
        ON settlement.tenant_id = frontier.tenant_id
       AND settlement.site_id = frontier.site_id
       AND settlement.crawl_run_id = frontier.crawl_run_id
       AND settlement.frontier_id = frontier.id
     WHERE frontier.tenant_id = p_tenant_id AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_crawl_run_id;
    v_coverage := CASE WHEN (v_counts->>'budget_exhausted')::bigint > 0
                            OR (v_counts->>'dispatch_unknown')::bigint > 0
                            OR (v_counts->>'attempts_exhausted')::bigint > 0
                            OR (v_counts->>'http_error')::bigint > 0
                            OR (v_counts->>'transport_error')::bigint > 0
                            OR (v_counts->>'policy_rejected')::bigint > 0
                            OR (v_counts->>'parse_error')::bigint > 0
                            OR (v_counts->>'robots_denied')::bigint > 0
                            OR v_discovered >= (v_run.limits_snapshot->>'max_urls')::integer
                            OR EXISTS (SELECT 1 FROM app.crawl_frontier AS frontier
                                  JOIN app.crawl_page_records AS page
                                    ON page.tenant_id = frontier.tenant_id
                                   AND page.site_id = frontier.site_id
                                   AND page.crawl_run_id = frontier.crawl_run_id
                                   AND page.frontier_id = frontier.id
                                 WHERE frontier.tenant_id = p_tenant_id
                                   AND frontier.site_id = p_site_id
                                   AND frontier.crawl_run_id = p_crawl_run_id
                                   AND frontier.depth >=
                                      (v_run.limits_snapshot->>'max_depth')::integer
                                   AND jsonb_array_length(page.internal_links) > 0)
                       THEN 'partial' ELSE 'complete' END;
    v_expected_hash := sha256(convert_to(
        'crawl-manifest-v1|' || p_crawl_run_id::text || '|' || v_discovered::text ||
        '|' || v_terminal::text || '|' || v_bytes::text ||
        '|attempts_exhausted=' || (v_counts->>'attempts_exhausted') ||
        '|budget_exhausted=' || (v_counts->>'budget_exhausted') ||
        '|dispatch_unknown=' || (v_counts->>'dispatch_unknown') ||
        '|fetched=' || (v_counts->>'fetched') ||
        '|http_error=' || (v_counts->>'http_error') ||
        '|parse_error=' || (v_counts->>'parse_error') ||
        '|policy_rejected=' || (v_counts->>'policy_rejected') ||
        '|robots_denied=' || (v_counts->>'robots_denied') ||
        '|transport_error=' || (v_counts->>'transport_error'), 'UTF8'
    ));
    IF p_manifest_sha256 IS DISTINCT FROM v_expected_hash THEN
        RETURN QUERY SELECT NULL::uuid, v_expected_hash, NULL::text, v_discovered,
            v_terminal, v_bytes, v_counts, NULL::timestamptz, NULL::boolean,
            'manifest_hash_mismatch'::text;
        RETURN;
    END IF;
    INSERT INTO app.crawl_manifests (
        tenant_id, site_id, id, crawl_run_id, manifest_sha256, coverage,
        discovered_count, terminal_count, fetched_bytes, state_counts, completed_at
    ) VALUES (
        p_tenant_id, p_site_id, p_manifest_id, p_crawl_run_id, p_manifest_sha256,
        v_coverage, v_discovered, v_terminal, v_bytes, v_counts, v_now
    ) RETURNING * INTO v_manifest;
    UPDATE app.crawl_runs SET status = 'completed', ended_at = v_now,
                              coverage_summary = v_counts
     WHERE tenant_id = p_tenant_id AND id = p_crawl_run_id;
    RETURN QUERY SELECT v_manifest.id, v_manifest.manifest_sha256, v_manifest.coverage,
        v_manifest.discovered_count, v_manifest.terminal_count,
        v_manifest.fetched_bytes, v_manifest.state_counts, v_manifest.completed_at,
        false, 'finalized'::text;
END;
$$;

CREATE FUNCTION control.load_crawl_manifest(
    p_tenant_id uuid, p_site_id uuid, p_command_id uuid, p_first_run_id text
)
RETURNS TABLE (
    manifest_id uuid, manifest_hash bytea, coverage text,
    discovered_count integer, terminal_count integer,
    scope_version integer, crawl_policy_version integer
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY
    SELECT manifest.id, manifest.manifest_sha256, manifest.coverage,
           manifest.discovered_count, manifest.terminal_count,
           run.scope_version, run.crawl_policy_version
      FROM app.crawl_runs AS run
      JOIN app.crawl_manifests AS manifest
        ON manifest.tenant_id = run.tenant_id AND manifest.site_id = run.site_id
       AND manifest.crawl_run_id = run.id
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.command_id = p_command_id AND run.first_run_id = p_first_run_id
       AND run.status = 'completed';
END;
$$;

REVOKE ALL ON FUNCTION control.begin_crawl_robots_dispatch(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, text, bytea, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_crawl_robots_dispatch(
    uuid, uuid, uuid, uuid, bytea, text, uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.verified_crawl_origin(uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.verified_site_origin(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_crawl_page(
    uuid, uuid, uuid, uuid, uuid, uuid, uuid, integer, text, text, text,
    jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, bytea, integer, boolean
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.settle_crawl_frontier(
    uuid, uuid, uuid, uuid, uuid, uuid, uuid, text, text, uuid, uuid,
    uuid, uuid, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.crawl_run_progress(uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.list_crawl_recovery_leases(uuid, uuid, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finalize_crawl_run(
    uuid, uuid, uuid, uuid, bytea
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.load_crawl_manifest(uuid, uuid, uuid, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.begin_crawl_robots_dispatch(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, text, bytea, integer
) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.finish_crawl_robots_dispatch(
    uuid, uuid, uuid, uuid, bytea, text, uuid
) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.verified_crawl_origin(uuid, uuid, uuid)
TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.verified_site_origin(uuid, uuid)
TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.record_crawl_page(
    uuid, uuid, uuid, uuid, uuid, uuid, uuid, integer, text, text, text,
    jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, bytea, integer, boolean
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.settle_crawl_frontier(
    uuid, uuid, uuid, uuid, uuid, uuid, uuid, text, text, uuid, uuid,
    uuid, uuid, integer
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.crawl_run_progress(uuid, uuid, uuid)
    TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.list_crawl_recovery_leases(uuid, uuid, uuid, text)
    TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.finalize_crawl_run(
    uuid, uuid, uuid, uuid, bytea
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.load_crawl_manifest(uuid, uuid, uuid, text)
TO signal_crawl_ingest;
