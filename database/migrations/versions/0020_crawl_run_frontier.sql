CREATE FUNCTION control.valid_crawl_url_identity(
    p_original_url text,
    p_fetch_url text,
    p_normalized_key text,
    p_origin text,
    p_normalization_version integer
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority text;
BEGIN
    IF p_normalization_version <> 1
       OR length(p_original_url) NOT BETWEEN 1 AND 2048
       OR length(p_fetch_url) NOT BETWEEN 1 AND 2048
       OR length(p_normalized_key) NOT BETWEEN 1 AND 2048
       OR length(p_origin) NOT BETWEEN 8 AND 512
       OR p_original_url ~ '[[:cntrl:]]'
       OR p_fetch_url !~ '^[ -~]+$'
       OR p_fetch_url ~ '[[:cntrl:]]'
       OR position(chr(92) IN p_fetch_url) > 0
       OR position('#' IN p_fetch_url) > 0
       OR p_normalized_key IS DISTINCT FROM p_fetch_url
       OR p_origin IS DISTINCT FROM lower(p_origin)
       OR p_origin !~ '^https?://[^/?#@[:space:]]+$'
       OR position(chr(92) IN p_origin) > 0
       OR p_fetch_url NOT LIKE p_origin || '/%%'
    THEN
        RETURN false;
    END IF;

    v_authority := regexp_replace(p_origin, '^https?://', '');
    IF v_authority LIKE '[%%' THEN
        RETURN v_authority ~ '^\[[0-9a-f:]+\]$';
    END IF;
    RETURN v_authority ~ '^[a-z0-9][a-z0-9.-]*[a-z0-9]$'
       AND position('..' IN v_authority) = 0
       AND right(v_authority, 1) <> '.'
       AND NOT EXISTS (
            SELECT 1
              FROM unnest(string_to_array(v_authority, '.')) AS label(value)
             WHERE length(label.value) NOT BETWEEN 1 AND 63
                OR label.value !~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'
       );
END;
$$;

CREATE FUNCTION control.valid_crawl_scope_snapshot(p_snapshot jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_origin text;
    v_seed text;
    v_sorted jsonb;
BEGIN
    IF jsonb_typeof(p_snapshot) <> 'object'
       OR jsonb_typeof(p_snapshot->'allowed_origins') <> 'array'
       OR jsonb_typeof(p_snapshot->'seed_urls') <> 'array'
       OR p_snapshot->'authorized_content_classes' IS DISTINCT FROM '["public_html"]'::jsonb
       OR p_snapshot->>'purpose' IS DISTINCT FROM 'connected_site_audit'
       OR p_snapshot->'schema_version' IS DISTINCT FROM '1'::jsonb
       OR p_snapshot->'normalization_version' IS DISTINCT FROM '1'::jsonb
       OR length(p_snapshot->>'user_agent') NOT BETWEEN 8 AND 200
       OR p_snapshot->>'user_agent' !~ '^SignalBot/[ -~]+$'
       OR p_snapshot IS DISTINCT FROM jsonb_build_object(
            'allowed_origins', p_snapshot->'allowed_origins',
            'authorized_content_classes', '["public_html"]'::jsonb,
            'normalization_version', 1,
            'purpose', 'connected_site_audit',
            'schema_version', 1,
            'seed_urls', p_snapshot->'seed_urls',
            'user_agent', p_snapshot->>'user_agent'
       )
       OR jsonb_array_length(p_snapshot->'allowed_origins') NOT BETWEEN 1 AND 16
       OR jsonb_array_length(p_snapshot->'seed_urls') <> 1
       OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_snapshot->'allowed_origins') AS item(value)
             WHERE jsonb_typeof(item.value) <> 'string'
       )
       OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_snapshot->'seed_urls') AS item(value)
             WHERE jsonb_typeof(item.value) <> 'string'
       )
    THEN
        RETURN false;
    END IF;

    SELECT jsonb_agg(item.value ORDER BY item.value)
      INTO v_sorted
      FROM jsonb_array_elements_text(p_snapshot->'allowed_origins') AS item(value);
    IF v_sorted IS DISTINCT FROM p_snapshot->'allowed_origins'
       OR jsonb_array_length(p_snapshot->'allowed_origins') IS DISTINCT FROM (
            SELECT count(DISTINCT item.value)
              FROM jsonb_array_elements_text(p_snapshot->'allowed_origins') AS item(value)
       )
    THEN
        RETURN false;
    END IF;

    FOR v_origin IN
        SELECT item.value
          FROM jsonb_array_elements_text(p_snapshot->'allowed_origins') AS item(value)
    LOOP
        IF control.valid_crawl_url_identity(v_origin || '/', v_origin || '/',
                                            v_origin || '/', v_origin, 1) IS NOT TRUE
        THEN
            RETURN false;
        END IF;
    END LOOP;

    SELECT item.value
      INTO v_seed
      FROM jsonb_array_elements_text(p_snapshot->'seed_urls') AS item(value);
    IF control.valid_crawl_url_identity(v_seed, v_seed, v_seed,
            regexp_replace(v_seed, '^((http|https)://[^/]+).*$','\1'), 1) IS NOT TRUE
       OR NOT EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(p_snapshot->'allowed_origins') AS item(value)
             WHERE v_seed LIKE item.value || '/%%'
       )
    THEN
        RETURN false;
    END IF;
    RETURN true;
END;
$$;

CREATE FUNCTION control.valid_crawl_limits_snapshot(p_limits jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_max_urls bigint;
    v_max_depth integer;
    v_max_attempts integer;
    v_max_redirects integer;
    v_max_body_bytes bigint;
    v_max_total_bytes bigint;
    v_max_duration_seconds integer;
    v_request_timeout_ms integer;
    v_total_timeout_ms integer;
BEGIN
    IF jsonb_typeof(p_limits) <> 'object' THEN
        RETURN false;
    END IF;
    v_max_urls := (p_limits->>'max_urls')::bigint;
    v_max_depth := (p_limits->>'max_depth')::integer;
    v_max_attempts := (p_limits->>'max_attempts_per_url')::integer;
    v_max_redirects := (p_limits->>'max_redirects')::integer;
    v_max_body_bytes := (p_limits->>'max_body_bytes')::bigint;
    v_max_total_bytes := (p_limits->>'max_total_bytes')::bigint;
    v_max_duration_seconds := (p_limits->>'max_duration_seconds')::integer;
    v_request_timeout_ms := (p_limits->>'request_timeout_ms')::integer;
    v_total_timeout_ms := (p_limits->>'total_timeout_ms')::integer;

    RETURN p_limits = jsonb_build_object(
               'max_attempts_per_url', v_max_attempts,
               'max_body_bytes', v_max_body_bytes,
               'max_depth', v_max_depth,
               'max_duration_seconds', v_max_duration_seconds,
               'max_redirects', v_max_redirects,
               'max_total_bytes', v_max_total_bytes,
               'max_urls', v_max_urls,
               'request_timeout_ms', v_request_timeout_ms,
               'schema_version', 1,
               'total_timeout_ms', v_total_timeout_ms
           )
       AND v_max_urls BETWEEN 1 AND 1000000
       AND v_max_depth BETWEEN 0 AND 32
       AND v_max_attempts BETWEEN 1 AND 10
       AND v_max_redirects BETWEEN 0 AND 10
       AND v_max_body_bytes BETWEEN 1024 AND 5242880
       AND v_max_total_bytes BETWEEN v_max_body_bytes AND 107374182400
       AND v_max_duration_seconds BETWEEN 1 AND 86400
       AND v_request_timeout_ms BETWEEN 250 AND 30000
       AND v_total_timeout_ms BETWEEN 1000 AND 120000
       AND v_total_timeout_ms >= v_request_timeout_ms;
EXCEPTION WHEN OTHERS THEN
    RETURN false;
END;
$$;

REVOKE ALL ON FUNCTION control.valid_crawl_url_identity(text, text, text, text, integer)
FROM PUBLIC;
REVOKE ALL ON FUNCTION control.valid_crawl_scope_snapshot(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.valid_crawl_limits_snapshot(jsonb) FROM PUBLIC;

ALTER TABLE app.workflow_refs
    ADD CONSTRAINT workflow_refs_exact_run_reference
    UNIQUE (tenant_id, site_id, command_id, workflow_id, first_run_id);

CREATE TABLE app.crawl_runs (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    command_id uuid NOT NULL,
    workflow_id text NOT NULL,
    first_run_id text NOT NULL,
    scope_version integer NOT NULL CHECK (scope_version BETWEEN 1 AND 2147483647),
    crawl_policy_version integer NOT NULL
        CHECK (crawl_policy_version BETWEEN 1 AND 2147483647),
    scope_snapshot jsonb NOT NULL
        CHECK (control.valid_crawl_scope_snapshot(scope_snapshot)),
    limits_snapshot jsonb NOT NULL
        CHECK (control.valid_crawl_limits_snapshot(limits_snapshot)),
    fetch_profile_hash bytea NOT NULL CHECK (octet_length(fetch_profile_hash) = 32),
    started_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    ended_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status = 'running'),
    coverage_summary jsonb,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, command_id),
    FOREIGN KEY (tenant_id, site_id, command_id, workflow_id, first_run_id)
        REFERENCES app.workflow_refs
            (tenant_id, site_id, command_id, workflow_id, first_run_id),
    CHECK (workflow_id =
        'signal:CrawlSite:' || tenant_id::text || ':' || command_id::text),
    CHECK (first_run_id ~
        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    CHECK (ended_at IS NULL AND coverage_summary IS NULL)
);

CREATE INDEX crawl_runs_site_started
ON app.crawl_runs (tenant_id, site_id, started_at DESC, id);

CREATE TABLE app.urls (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    original_url text NOT NULL,
    fetch_url text NOT NULL,
    normalized_key text NOT NULL,
    origin text NOT NULL,
    normalization_version integer NOT NULL,
    discovered_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, normalization_version, normalized_key),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    CHECK (control.valid_crawl_url_identity(
        original_url, fetch_url, normalized_key, origin, normalization_version
    ))
);

CREATE INDEX urls_site_origin
ON app.urls (tenant_id, site_id, origin, id);

CREATE TABLE app.crawl_frontier (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    url_id uuid NOT NULL,
    discovered_from_url_id uuid,
    depth integer NOT NULL CHECK (depth BETWEEN 0 AND 32),
    discovery_reason text NOT NULL
        CHECK (discovery_reason IN ('root', 'internal_link', 'sitemap', 'redirect')),
    priority integer NOT NULL CHECK (priority BETWEEN 0 AND 1000),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'permanently_failed')),
    next_attempt_at timestamptz NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 10),
    lease_id uuid,
    lease_owner text,
    lease_until timestamptz,
    terminal_at timestamptz,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, url_id),
    UNIQUE (tenant_id, lease_id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, url_id)
        REFERENCES app.urls (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id, discovered_from_url_id)
        REFERENCES app.crawl_frontier (tenant_id, site_id, crawl_run_id, url_id),
    CHECK (
        (discovery_reason = 'root' AND discovered_from_url_id IS NULL AND depth = 0)
        OR
        (discovery_reason <> 'root' AND discovered_from_url_id IS NOT NULL)
    ),
    CHECK (
        (status = 'pending' AND attempt_count = 0
            AND lease_id IS NULL AND lease_owner IS NULL AND lease_until IS NULL
            AND terminal_at IS NULL)
        OR
        (status = 'leased' AND attempt_count > 0
            AND lease_id IS NOT NULL
            AND lease_owner ~ '^[a-z][a-z0-9_.:-]{0,127}$'
            AND lease_until IS NOT NULL AND terminal_at IS NULL)
        OR
        (status = 'permanently_failed' AND attempt_count > 0
            AND lease_id IS NOT NULL
            AND lease_owner ~ '^[a-z][a-z0-9_.:-]{0,127}$'
            AND lease_until IS NOT NULL AND terminal_at IS NOT NULL)
    )
);

CREATE INDEX crawl_frontier_ready
ON app.crawl_frontier
    (tenant_id, site_id, crawl_run_id, status, next_attempt_at, priority DESC, id);
CREATE INDEX crawl_frontier_source
ON app.crawl_frontier (tenant_id, site_id, crawl_run_id, discovered_from_url_id);
CREATE INDEX crawl_frontier_lease
ON app.crawl_frontier (tenant_id, site_id, lease_until, id)
WHERE status = 'leased';

CREATE TABLE app.crawl_frontier_leases (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    frontier_id uuid NOT NULL,
    lease_owner text NOT NULL CHECK (lease_owner ~ '^[a-z][a-z0-9_.:-]{0,127}$'),
    attempt_number integer NOT NULL CHECK (attempt_number BETWEEN 1 AND 10),
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, frontier_id, id),
    UNIQUE (tenant_id, site_id, crawl_run_id, frontier_id, attempt_number),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, frontier_id)
        REFERENCES app.crawl_frontier (tenant_id, site_id, id),
    CHECK (expires_at > issued_at)
);

CREATE INDEX crawl_frontier_leases_run
ON app.crawl_frontier_leases
    (tenant_id, site_id, crawl_run_id, frontier_id, issued_at, id);

ALTER TABLE app.crawl_frontier
    ADD CONSTRAINT crawl_frontier_current_lease_receipt
    FOREIGN KEY (tenant_id, site_id, crawl_run_id, id, lease_id)
    REFERENCES app.crawl_frontier_leases
        (tenant_id, site_id, crawl_run_id, frontier_id, id);

CREATE FUNCTION app.guard_crawl_run_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_crawl_run' USING ERRCODE = '55000'; END $$;

CREATE FUNCTION app.guard_url_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_url_identity' USING ERRCODE = '55000'; END $$;

CREATE FUNCTION app.guard_crawl_lease_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_crawl_lease' USING ERRCODE = '55000'; END $$;

CREATE FUNCTION app.guard_crawl_frontier_mutation() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_crawl_frontier_identity' USING ERRCODE = '55000';
    END IF;
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
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

    IF OLD.status = 'pending'
       AND NEW.status = 'leased'
       AND OLD.attempt_count = 0
       AND NEW.attempt_count = 1
       AND NEW.lease_id IS NOT NULL
       AND NEW.lease_owner IS NOT NULL
       AND NEW.lease_until > statement_timestamp()
       AND NEW.terminal_at IS NULL
       AND EXISTS (
            SELECT 1 FROM app.crawl_frontier_leases AS receipt
             WHERE receipt.tenant_id = NEW.tenant_id
               AND receipt.site_id = NEW.site_id
               AND receipt.crawl_run_id = NEW.crawl_run_id
               AND receipt.frontier_id = NEW.id
               AND receipt.id = NEW.lease_id
               AND receipt.lease_owner = NEW.lease_owner
               AND receipt.attempt_number = NEW.attempt_count
               AND receipt.expires_at = NEW.lease_until
       )
    THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'leased'
       AND OLD.lease_until <= statement_timestamp()
       AND OLD.attempt_count < 10
       AND NEW.status = 'leased'
       AND NEW.attempt_count = OLD.attempt_count + 1
       AND NEW.lease_id IS DISTINCT FROM OLD.lease_id
       AND NEW.lease_owner IS NOT NULL
       AND NEW.lease_until > statement_timestamp()
       AND NEW.terminal_at IS NULL
       AND EXISTS (
            SELECT 1 FROM app.crawl_frontier_leases AS receipt
             WHERE receipt.tenant_id = NEW.tenant_id
               AND receipt.site_id = NEW.site_id
               AND receipt.crawl_run_id = NEW.crawl_run_id
               AND receipt.frontier_id = NEW.id
               AND receipt.id = NEW.lease_id
               AND receipt.lease_owner = NEW.lease_owner
               AND receipt.attempt_number = NEW.attempt_count
               AND receipt.expires_at = NEW.lease_until
       )
    THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'leased'
       AND OLD.lease_until <= statement_timestamp()
       AND NEW.status = 'permanently_failed'
       AND NEW.attempt_count = OLD.attempt_count
       AND NEW.lease_id = OLD.lease_id
       AND NEW.lease_owner = OLD.lease_owner
       AND NEW.lease_until = OLD.lease_until
       AND NEW.terminal_at >= OLD.lease_until
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid_crawl_frontier_transition' USING ERRCODE = '55000';
END;
$$;

REVOKE ALL ON FUNCTION app.guard_crawl_run_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.guard_url_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.guard_crawl_lease_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.guard_crawl_frontier_mutation() FROM PUBLIC;

CREATE TRIGGER crawl_runs_immutable BEFORE UPDATE OR DELETE ON app.crawl_runs
FOR EACH ROW EXECUTE FUNCTION app.guard_crawl_run_mutation();
CREATE TRIGGER urls_immutable BEFORE UPDATE OR DELETE ON app.urls
FOR EACH ROW EXECUTE FUNCTION app.guard_url_mutation();
CREATE TRIGGER crawl_frontier_leases_immutable
BEFORE UPDATE OR DELETE ON app.crawl_frontier_leases
FOR EACH ROW EXECUTE FUNCTION app.guard_crawl_lease_mutation();
CREATE TRIGGER crawl_frontier_guard BEFORE UPDATE OR DELETE ON app.crawl_frontier
FOR EACH ROW EXECUTE FUNCTION app.guard_crawl_frontier_mutation();

ALTER TABLE app.crawl_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_run_scope ON app.crawl_runs
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.urls ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.urls FORCE ROW LEVEL SECURITY;
CREATE POLICY url_scope ON app.urls
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.crawl_frontier ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_frontier FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_frontier_scope ON app.crawl_frontier
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.crawl_frontier_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_frontier_leases FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_frontier_lease_scope ON app.crawl_frontier_leases
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.open_crawl_run(
    p_tenant_id uuid,
    p_site_id uuid,
    p_command_id uuid,
    p_workflow_id text,
    p_first_run_id text,
    p_run_id uuid,
    p_scope_version integer,
    p_crawl_policy_version integer,
    p_scope_snapshot jsonb,
    p_limits_snapshot jsonb,
    p_fetch_profile_hash bytea,
    p_root_url_id uuid,
    p_root_frontier_id uuid,
    p_root_original_url text,
    p_root_fetch_url text,
    p_root_normalized_key text,
    p_root_origin text
)
RETURNS TABLE (
    run_id uuid,
    root_frontier_id uuid,
    root_url_id uuid,
    started_at timestamptz,
    run_status text,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_existing record;
    v_actual_url_id uuid;
    v_started_at timestamptz;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_command_id IS NULL
       OR p_run_id IS NULL OR p_root_url_id IS NULL OR p_root_frontier_id IS NULL
       OR p_workflow_id IS DISTINCT FROM
            'signal:CrawlSite:' || p_tenant_id::text || ':' || p_command_id::text
       OR p_first_run_id IS NULL
       OR p_first_run_id !~
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR p_scope_version NOT BETWEEN 1 AND 2147483647
       OR p_crawl_policy_version NOT BETWEEN 1 AND 2147483647
       OR control.valid_crawl_scope_snapshot(p_scope_snapshot) IS NOT TRUE
       OR control.valid_crawl_limits_snapshot(p_limits_snapshot) IS NOT TRUE
       OR octet_length(p_fetch_profile_hash) IS DISTINCT FROM 32
       OR control.valid_crawl_url_identity(
            p_root_original_url, p_root_fetch_url, p_root_normalized_key, p_root_origin, 1
          ) IS NOT TRUE
       OR p_scope_snapshot->'seed_urls' IS DISTINCT FROM jsonb_build_array(p_root_fetch_url)
       OR NOT EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(p_scope_snapshot->'allowed_origins') AS item(value)
             WHERE item.value = p_root_origin
       )
    THEN
        RAISE EXCEPTION 'invalid_crawl_run_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);

    -- Serialize the first open and concurrent exact retries on the durable workflow.
    PERFORM 1
      FROM app.workflow_refs AS workflow
      JOIN app.commands AS command
        ON command.tenant_id = workflow.tenant_id
       AND command.site_id = workflow.site_id
       AND command.id = workflow.command_id
     WHERE workflow.tenant_id = p_tenant_id
       AND workflow.site_id = p_site_id
       AND workflow.command_id = p_command_id
       AND workflow.workflow_id = p_workflow_id
       AND workflow.first_run_id = p_first_run_id
       AND workflow.workflow_type = 'CrawlSite'
     FOR UPDATE OF workflow, command;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::timestamptz,
                            NULL::text, NULL::boolean, 'workflow_unavailable'::text;
        RETURN;
    END IF;

    SELECT run.*, frontier.id AS root_frontier_id, url.id AS root_url_id,
           url.fetch_url AS root_fetch_url
      INTO v_existing
      FROM app.crawl_runs AS run
      JOIN app.crawl_frontier AS frontier
        ON frontier.tenant_id = run.tenant_id
       AND frontier.site_id = run.site_id
       AND frontier.crawl_run_id = run.id
       AND frontier.discovery_reason = 'root'
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id
       AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
     WHERE run.tenant_id = p_tenant_id
       AND run.site_id = p_site_id
       AND run.command_id = p_command_id
     FOR SHARE OF run, frontier, url;
    IF FOUND THEN
        IF v_existing.id = p_run_id
           AND v_existing.workflow_id = p_workflow_id
           AND v_existing.first_run_id = p_first_run_id
           AND v_existing.scope_version = p_scope_version
           AND v_existing.crawl_policy_version = p_crawl_policy_version
           AND v_existing.scope_snapshot = p_scope_snapshot
           AND v_existing.limits_snapshot = p_limits_snapshot
           AND v_existing.fetch_profile_hash = p_fetch_profile_hash
           AND v_existing.root_frontier_id = p_root_frontier_id
           AND v_existing.root_fetch_url = p_root_fetch_url
        THEN
            RETURN QUERY SELECT v_existing.id, v_existing.root_frontier_id,
                                v_existing.root_url_id, v_existing.started_at,
                                v_existing.status, true, 'opened'::text;
        ELSE
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::timestamptz,
                                NULL::text, NULL::boolean, 'run_conflict'::text;
        END IF;
        RETURN;
    END IF;

    PERFORM 1
      FROM app.workflow_refs AS workflow
      JOIN app.commands AS command
        ON command.tenant_id = workflow.tenant_id
       AND command.site_id = workflow.site_id
       AND command.id = workflow.command_id
     WHERE workflow.tenant_id = p_tenant_id
       AND workflow.site_id = p_site_id
       AND workflow.command_id = p_command_id
       AND workflow.workflow_id = p_workflow_id
       AND workflow.first_run_id = p_first_run_id
       AND workflow.workflow_type = 'CrawlSite'
       AND workflow.state_projection = 'running'
       AND command.status = 'processing';
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::timestamptz,
                            NULL::text, NULL::boolean, 'workflow_unavailable'::text;
        RETURN;
    END IF;

    v_started_at := statement_timestamp();
    INSERT INTO app.crawl_runs (
        tenant_id, site_id, id, command_id, workflow_id, first_run_id,
        scope_version, crawl_policy_version, scope_snapshot, limits_snapshot,
        fetch_profile_hash, started_at
    ) VALUES (
        p_tenant_id, p_site_id, p_run_id, p_command_id, p_workflow_id, p_first_run_id,
        p_scope_version, p_crawl_policy_version, p_scope_snapshot, p_limits_snapshot,
        p_fetch_profile_hash, v_started_at
    );
    INSERT INTO app.urls (
        tenant_id, site_id, id, original_url, fetch_url, normalized_key, origin,
        normalization_version, discovered_at
    ) VALUES (
        p_tenant_id, p_site_id, p_root_url_id, p_root_original_url, p_root_fetch_url,
        p_root_normalized_key, p_root_origin, 1, v_started_at
    ) ON CONFLICT (tenant_id, site_id, normalization_version, normalized_key) DO NOTHING;
    SELECT url.id INTO STRICT v_actual_url_id
      FROM app.urls AS url
     WHERE url.tenant_id = p_tenant_id
       AND url.site_id = p_site_id
       AND url.normalization_version = 1
       AND url.normalized_key = p_root_normalized_key;
    INSERT INTO app.crawl_frontier (
        tenant_id, site_id, id, crawl_run_id, url_id, discovered_from_url_id,
        depth, discovery_reason, priority, next_attempt_at
    ) VALUES (
        p_tenant_id, p_site_id, p_root_frontier_id, p_run_id, v_actual_url_id, NULL,
        0, 'root', 1000, v_started_at
    );

    RETURN QUERY SELECT p_run_id, p_root_frontier_id, v_actual_url_id, v_started_at,
                        'running'::text, false, 'opened'::text;
END;
$$;

CREATE FUNCTION control.enqueue_crawl_url(
    p_tenant_id uuid,
    p_site_id uuid,
    p_run_id uuid,
    p_candidate_url_id uuid,
    p_candidate_frontier_id uuid,
    p_discovered_from_url_id uuid,
    p_depth integer,
    p_discovery_reason text,
    p_priority integer,
    p_delay_seconds integer,
    p_original_url text,
    p_fetch_url text,
    p_normalized_key text,
    p_origin text
)
RETURNS TABLE (
    frontier_id uuid,
    url_id uuid,
    frontier_status text,
    next_attempt_at timestamptz,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_run app.crawl_runs%%ROWTYPE;
    v_existing record;
    v_source_depth integer;
    v_actual_url_id uuid;
    v_next_attempt_at timestamptz;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_run_id IS NULL
       OR p_candidate_url_id IS NULL OR p_candidate_frontier_id IS NULL
       OR p_discovered_from_url_id IS NULL
       OR p_depth IS NULL OR p_depth NOT BETWEEN 0 AND 32
       OR p_discovery_reason IS NULL
       OR p_discovery_reason NOT IN ('internal_link', 'sitemap', 'redirect')
       OR p_priority IS NULL OR p_priority NOT BETWEEN 0 AND 1000
       OR p_delay_seconds IS NULL OR p_delay_seconds NOT BETWEEN 0 AND 86400
       OR control.valid_crawl_url_identity(
            p_original_url, p_fetch_url, p_normalized_key, p_origin, 1
          ) IS NOT TRUE
    THEN
        RAISE EXCEPTION 'invalid_crawl_frontier_input' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);

    SELECT run.* INTO v_run
      FROM app.crawl_runs AS run
      JOIN app.workflow_refs AS workflow
        ON workflow.tenant_id = run.tenant_id
       AND workflow.site_id = run.site_id
       AND workflow.command_id = run.command_id
     WHERE run.tenant_id = p_tenant_id
       AND run.site_id = p_site_id
       AND run.id = p_run_id
       AND run.status = 'running'
       AND workflow.state_projection = 'running'
     FOR UPDATE OF run, workflow;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'run_unavailable'::text;
        RETURN;
    END IF;

    SELECT frontier.id, frontier.url_id, frontier.status, frontier.next_attempt_at
      INTO v_existing
      FROM app.crawl_frontier AS frontier
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id
       AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
     WHERE frontier.tenant_id = p_tenant_id
       AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_run_id
       AND url.normalization_version = 1
       AND url.normalized_key = p_normalized_key;
    IF FOUND THEN
        RETURN QUERY SELECT v_existing.id, v_existing.url_id, 'pending'::text,
                            v_existing.next_attempt_at, true, 'queued'::text;
        RETURN;
    END IF;

    IF statement_timestamp() >= v_run.started_at + make_interval(
        secs => (v_run.limits_snapshot->>'max_duration_seconds')::integer
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'budget_exhausted'::text;
        RETURN;
    END IF;

    PERFORM 1
      FROM control.tenant_directory AS directory_row
      JOIN app.tenants AS tenant_row
        ON tenant_row.tenant_id = directory_row.tenant_id
      JOIN app.sites AS site_row
        ON site_row.tenant_id = tenant_row.tenant_id
     WHERE directory_row.tenant_id = p_tenant_id
       AND directory_row.lifecycle = 'active'
       AND tenant_row.lifecycle = 'active'
       AND site_row.id = p_site_id
       AND site_row.state <> 'archived'
     FOR SHARE OF directory_row, tenant_row, site_row;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'scope_unavailable'::text;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements_text(v_run.scope_snapshot->'allowed_origins') AS item(value)
         WHERE item.value = p_origin
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'url_out_of_scope'::text;
        RETURN;
    END IF;

    SELECT source.depth INTO v_source_depth
      FROM app.crawl_frontier AS source
     WHERE source.tenant_id = p_tenant_id
       AND source.site_id = p_site_id
       AND source.crawl_run_id = p_run_id
       AND source.url_id = p_discovered_from_url_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'source_unavailable'::text;
        RETURN;
    END IF;
    IF (p_discovery_reason = 'redirect' AND p_depth <> v_source_depth)
       OR (p_discovery_reason <> 'redirect' AND p_depth <> v_source_depth + 1)
       OR p_depth > (v_run.limits_snapshot->>'max_depth')::integer
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'depth_exceeded'::text;
        RETURN;
    END IF;
    IF (SELECT count(*) FROM app.crawl_frontier AS frontier
         WHERE frontier.tenant_id = p_tenant_id
           AND frontier.site_id = p_site_id
           AND frontier.crawl_run_id = p_run_id)
       >= (v_run.limits_snapshot->>'max_urls')::bigint
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::timestamptz,
                            NULL::boolean, 'budget_exhausted'::text;
        RETURN;
    END IF;

    v_next_attempt_at := statement_timestamp() + make_interval(secs => p_delay_seconds);
    INSERT INTO app.urls (
        tenant_id, site_id, id, original_url, fetch_url, normalized_key, origin,
        normalization_version, discovered_at
    ) VALUES (
        p_tenant_id, p_site_id, p_candidate_url_id, p_original_url, p_fetch_url,
        p_normalized_key, p_origin, 1, statement_timestamp()
    ) ON CONFLICT (tenant_id, site_id, normalization_version, normalized_key) DO NOTHING;
    SELECT url.id INTO STRICT v_actual_url_id
      FROM app.urls AS url
     WHERE url.tenant_id = p_tenant_id
       AND url.site_id = p_site_id
       AND url.normalization_version = 1
       AND url.normalized_key = p_normalized_key;
    INSERT INTO app.crawl_frontier (
        tenant_id, site_id, id, crawl_run_id, url_id, discovered_from_url_id,
        depth, discovery_reason, priority, next_attempt_at
    ) VALUES (
        p_tenant_id, p_site_id, p_candidate_frontier_id, p_run_id, v_actual_url_id,
        p_discovered_from_url_id, p_depth, p_discovery_reason, p_priority,
        v_next_attempt_at
    );
    RETURN QUERY SELECT p_candidate_frontier_id, v_actual_url_id, 'pending'::text,
                        v_next_attempt_at, false, 'queued'::text;
END;
$$;

CREATE FUNCTION control.claim_crawl_frontier(
    p_tenant_id uuid,
    p_site_id uuid,
    p_run_id uuid,
    p_worker_key text,
    p_lease_id uuid,
    p_lease_seconds integer
)
RETURNS TABLE (
    crawl_run_id uuid,
    frontier_id uuid,
    url_id uuid,
    original_url text,
    fetch_url text,
    normalized_key text,
    origin text,
    depth integer,
    discovery_reason text,
    priority integer,
    attempt_count integer,
    lease_id uuid,
    lease_owner text,
    lease_until timestamptz,
    scope_version integer,
    crawl_policy_version integer,
    scope_snapshot jsonb,
    limits_snapshot jsonb,
    fetch_profile_hash bytea,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_run app.crawl_runs%%ROWTYPE;
    v_item record;
    v_now timestamptz;
    v_max_attempts integer;
    v_lease_until timestamptz;
    v_receipt_id uuid;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_run_id IS NULL
       OR p_worker_key IS NULL
       OR p_worker_key !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_lease_id IS NULL
       OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 300
    THEN
        RAISE EXCEPTION 'invalid_crawl_claim_input' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    v_now := statement_timestamp();

    SELECT frontier.*, url.original_url, url.fetch_url, url.normalized_key, url.origin,
           run.scope_version, run.crawl_policy_version, run.scope_snapshot,
           run.limits_snapshot, run.fetch_profile_hash
      INTO v_item
      FROM app.crawl_frontier AS frontier
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id
       AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
      JOIN app.crawl_runs AS run
        ON run.tenant_id = frontier.tenant_id
       AND run.site_id = frontier.site_id
       AND run.id = frontier.crawl_run_id
     WHERE frontier.tenant_id = p_tenant_id
       AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_run_id
       AND frontier.lease_id = p_lease_id
       AND frontier.lease_owner = p_worker_key
       AND frontier.status = 'leased'
       AND frontier.lease_until > v_now
     FOR SHARE OF frontier, url, run;
    IF FOUND THEN
        RETURN QUERY SELECT v_item.crawl_run_id, v_item.id, v_item.url_id,
            v_item.original_url, v_item.fetch_url, v_item.normalized_key, v_item.origin,
            v_item.depth, v_item.discovery_reason, v_item.priority, v_item.attempt_count,
            v_item.lease_id, v_item.lease_owner, v_item.lease_until,
            v_item.scope_version, v_item.crawl_policy_version, v_item.scope_snapshot,
            v_item.limits_snapshot, v_item.fetch_profile_hash, true, 'leased'::text;
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM app.crawl_frontier_leases AS receipt
         WHERE receipt.tenant_id = p_tenant_id
           AND receipt.id = p_lease_id
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::integer, NULL::uuid, NULL::text, NULL::timestamptz, NULL::integer,
            NULL::integer, NULL::jsonb, NULL::jsonb, NULL::bytea, NULL::boolean,
            'lease_unavailable'::text;
        RETURN;
    END IF;

    SELECT run.* INTO v_run
      FROM app.crawl_runs AS run
      JOIN app.workflow_refs AS workflow
        ON workflow.tenant_id = run.tenant_id
       AND workflow.site_id = run.site_id
       AND workflow.command_id = run.command_id
     WHERE run.tenant_id = p_tenant_id
       AND run.site_id = p_site_id
       AND run.id = p_run_id
       AND run.status = 'running'
       AND workflow.state_projection = 'running'
     FOR UPDATE OF run, workflow;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::integer, NULL::uuid, NULL::text, NULL::timestamptz, NULL::integer,
            NULL::integer, NULL::jsonb, NULL::jsonb, NULL::bytea, NULL::boolean,
            'run_unavailable'::text;
        RETURN;
    END IF;
    IF v_now >= v_run.started_at + make_interval(
        secs => (v_run.limits_snapshot->>'max_duration_seconds')::integer
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::integer, NULL::uuid, NULL::text, NULL::timestamptz, NULL::integer,
            NULL::integer, NULL::jsonb, NULL::jsonb, NULL::bytea, NULL::boolean,
            'run_budget_exhausted'::text;
        RETURN;
    END IF;
    v_lease_until := LEAST(
        v_now + make_interval(secs => p_lease_seconds),
        v_run.started_at + make_interval(
            secs => (v_run.limits_snapshot->>'max_duration_seconds')::integer
        )
    );
    PERFORM 1
      FROM control.tenant_directory AS directory_row
      JOIN app.tenants AS tenant_row
        ON tenant_row.tenant_id = directory_row.tenant_id
      JOIN app.sites AS site_row
        ON site_row.tenant_id = tenant_row.tenant_id
     WHERE directory_row.tenant_id = p_tenant_id
       AND directory_row.lifecycle = 'active'
       AND tenant_row.lifecycle = 'active'
       AND site_row.id = p_site_id
       AND site_row.state <> 'archived'
     FOR SHARE OF directory_row, tenant_row, site_row;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::integer, NULL::uuid, NULL::text, NULL::timestamptz, NULL::integer,
            NULL::integer, NULL::jsonb, NULL::jsonb, NULL::bytea, NULL::boolean,
            'scope_unavailable'::text;
        RETURN;
    END IF;

    v_max_attempts := (v_run.limits_snapshot->>'max_attempts_per_url')::integer;
    WITH exhausted AS (
        SELECT frontier.tenant_id, frontier.id
          FROM app.crawl_frontier AS frontier
         WHERE frontier.tenant_id = p_tenant_id
           AND frontier.site_id = p_site_id
           AND frontier.crawl_run_id = p_run_id
           AND frontier.status = 'leased'
           AND frontier.lease_until <= v_now
           AND frontier.attempt_count >= v_max_attempts
         ORDER BY frontier.lease_until, frontier.id
         LIMIT 100
         FOR UPDATE SKIP LOCKED
    )
    UPDATE app.crawl_frontier AS frontier
       SET status = 'permanently_failed', terminal_at = v_now
      FROM exhausted
     WHERE frontier.tenant_id = exhausted.tenant_id
       AND frontier.id = exhausted.id;

    SELECT frontier.*, url.original_url, url.fetch_url, url.normalized_key, url.origin
      INTO v_item
      FROM app.crawl_frontier AS frontier
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id
       AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
     WHERE frontier.tenant_id = p_tenant_id
       AND frontier.site_id = p_site_id
       AND frontier.crawl_run_id = p_run_id
       AND frontier.next_attempt_at <= v_now
       AND frontier.attempt_count < v_max_attempts
       AND (
            frontier.status = 'pending'
            OR (frontier.status = 'leased' AND frontier.lease_until <= v_now)
       )
       AND NOT EXISTS (
            SELECT 1
              FROM app.crawl_frontier AS active
              JOIN app.urls AS active_url
                ON active_url.tenant_id = active.tenant_id
               AND active_url.site_id = active.site_id
               AND active_url.id = active.url_id
             WHERE active.tenant_id = frontier.tenant_id
               AND active.site_id = frontier.site_id
               AND active.crawl_run_id = frontier.crawl_run_id
               AND active.status = 'leased'
               AND active.lease_until > v_now
               AND active_url.origin = url.origin
       )
     ORDER BY frontier.priority DESC, frontier.depth, frontier.next_attempt_at, frontier.id
     LIMIT 1
     FOR UPDATE OF frontier SKIP LOCKED;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::integer, NULL::uuid, NULL::text, NULL::timestamptz, NULL::integer,
            NULL::integer, NULL::jsonb, NULL::jsonb, NULL::bytea, false, 'empty'::text;
        RETURN;
    END IF;

    INSERT INTO app.crawl_frontier_leases (
        tenant_id, site_id, id, crawl_run_id, frontier_id, lease_owner,
        attempt_number, issued_at, expires_at
    ) VALUES (
        p_tenant_id, p_site_id, p_lease_id, p_run_id, v_item.id, p_worker_key,
        v_item.attempt_count + 1, v_now, v_lease_until
    ) ON CONFLICT DO NOTHING
    RETURNING id INTO v_receipt_id;
    IF v_receipt_id IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::integer, NULL::uuid, NULL::text, NULL::timestamptz, NULL::integer,
            NULL::integer, NULL::jsonb, NULL::jsonb, NULL::bytea, NULL::boolean,
            'lease_unavailable'::text;
        RETURN;
    END IF;

    UPDATE app.crawl_frontier AS frontier
       SET status = 'leased',
           attempt_count = frontier.attempt_count + 1,
           lease_id = p_lease_id,
           lease_owner = p_worker_key,
           lease_until = v_lease_until
     WHERE frontier.tenant_id = p_tenant_id
       AND frontier.id = v_item.id;

    RETURN QUERY SELECT v_item.crawl_run_id, v_item.id, v_item.url_id,
        v_item.original_url, v_item.fetch_url, v_item.normalized_key, v_item.origin,
        v_item.depth, v_item.discovery_reason, v_item.priority, v_item.attempt_count + 1,
        p_lease_id, p_worker_key, v_lease_until,
        v_run.scope_version, v_run.crawl_policy_version, v_run.scope_snapshot,
        v_run.limits_snapshot, v_run.fetch_profile_hash, false, 'leased'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.open_crawl_run(
    uuid, uuid, uuid, text, text, uuid, integer, integer, jsonb, jsonb, bytea,
    uuid, uuid, text, text, text, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.enqueue_crawl_url(
    uuid, uuid, uuid, uuid, uuid, uuid, integer, text, integer, integer,
    text, text, text, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.claim_crawl_frontier(
    uuid, uuid, uuid, text, uuid, integer
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.open_crawl_run(
    uuid, uuid, uuid, text, text, uuid, integer, integer, jsonb, jsonb, bytea,
    uuid, uuid, text, text, text, text
) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.enqueue_crawl_url(
    uuid, uuid, uuid, uuid, uuid, uuid, integer, text, integer, integer,
    text, text, text, text
) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.claim_crawl_frontier(
    uuid, uuid, uuid, text, uuid, integer
) TO signal_crawl_admission;
