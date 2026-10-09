CREATE TABLE app.robots_snapshots (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    origin text NOT NULL,
    robots_url text NOT NULL,
    final_url text NOT NULL,
    fetch_profile_hash bytea NOT NULL CHECK (octet_length(fetch_profile_hash) = 32),
    product_token text NOT NULL CHECK (product_token ~ '^[A-Za-z_-]{1,64}$'),
    parser_release text NOT NULL CHECK (parser_release = 'protego/0.6.2+signal-rfc9309-v1'),
    fetched_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    retrieval_outcome text NOT NULL CHECK (retrieval_outcome IN (
        'fetched', 'not_found', 'forbidden', 'backoff', 'server_error', 'client_error',
        'transport_error', 'policy_rejected', 'unsupported_encoding',
        'unsupported_media_type', 'body_limit'
    )),
    decision_status text NOT NULL
        CHECK (decision_status IN ('rules', 'allow_missing', 'deny', 'backoff', 'suspended')),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    response_headers jsonb NOT NULL CHECK (control.valid_fetch_headers(response_headers)),
    redirect_chain jsonb NOT NULL CHECK (
        jsonb_typeof(redirect_chain) = 'array' AND jsonb_array_length(redirect_chain) <= 5
    ),
    resolved_address inet CHECK (
        resolved_address IS NULL OR control.valid_crawl_public_address(resolved_address)
    ),
    media_type text CHECK (media_type IS NULL OR control.valid_artifact_media_type(media_type)),
    raw_artifact_id uuid,
    source_hash bytea CHECK (source_hash IS NULL OR octet_length(source_hash) = 32),
    rules_hash bytea CHECK (rules_hash IS NULL OR octet_length(rules_hash) = 32),
    parse_error_count integer NOT NULL CHECK (parse_error_count BETWEEN 0 AND 10000),
    rule_count integer NOT NULL CHECK (rule_count BETWEEN 0 AND 10000),
    crawl_delay_ms integer CHECK (crawl_delay_ms BETWEEN 1 AND 60000),
    network_profile_hash bytea NOT NULL CHECK (octet_length(network_profile_hash) = 32),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, raw_artifact_id)
        REFERENCES app.artifacts (tenant_id, site_id, id),
    CHECK (expires_at > fetched_at AND expires_at <= fetched_at + interval '24 hours'),
    CHECK (recorded_at >= fetched_at),
    CHECK (robots_url = origin || '/robots.txt'),
    CHECK (
        (retrieval_outcome = 'fetched' AND decision_status = 'rules'
            AND http_status BETWEEN 200 AND 299 AND media_type = 'text/plain'
            AND raw_artifact_id IS NOT NULL AND source_hash IS NOT NULL AND rules_hash IS NOT NULL)
        OR (retrieval_outcome = 'not_found' AND decision_status = 'allow_missing'
            AND http_status = 404)
        OR (retrieval_outcome = 'forbidden' AND decision_status = 'deny'
            AND http_status IN (401, 403))
        OR (retrieval_outcome = 'backoff' AND decision_status = 'backoff'
            AND http_status = 429)
        OR (retrieval_outcome = 'server_error' AND decision_status = 'suspended'
            AND http_status BETWEEN 500 AND 599)
        OR (retrieval_outcome = 'client_error' AND decision_status = 'suspended'
            AND http_status BETWEEN 400 AND 499
            AND http_status NOT IN (401, 403, 404, 429))
        OR (retrieval_outcome = 'policy_rejected' AND decision_status = 'suspended'
            AND (http_status IS NULL OR http_status BETWEEN 100 AND 599))
        OR (retrieval_outcome = 'transport_error' AND decision_status = 'suspended')
        OR (retrieval_outcome IN (
                'unsupported_encoding', 'unsupported_media_type', 'body_limit'
            ) AND decision_status = 'suspended' AND http_status BETWEEN 200 AND 299)
    ),
    CHECK (
        (retrieval_outcome = 'fetched')
        OR (raw_artifact_id IS NULL AND source_hash IS NULL AND rules_hash IS NULL
            AND parse_error_count = 0 AND rule_count = 0 AND crawl_delay_ms IS NULL)
    ),
    CHECK (
        retrieval_outcome IN ('transport_error', 'policy_rejected')
        OR resolved_address IS NOT NULL
    )
);

CREATE INDEX robots_snapshots_current
ON app.robots_snapshots (
    tenant_id, site_id, crawl_run_id, origin, fetch_profile_hash, fetched_at DESC, id DESC
);
CREATE INDEX robots_snapshots_artifact
ON app.robots_snapshots (tenant_id, site_id, raw_artifact_id)
WHERE raw_artifact_id IS NOT NULL;

CREATE FUNCTION app.guard_robots_snapshot_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_robots_snapshot' USING ERRCODE = '55000'; END $$;

REVOKE ALL ON FUNCTION app.guard_robots_snapshot_mutation() FROM PUBLIC;
CREATE TRIGGER robots_snapshots_immutable
BEFORE UPDATE OR DELETE ON app.robots_snapshots
FOR EACH ROW EXECUTE FUNCTION app.guard_robots_snapshot_mutation();

ALTER TABLE app.robots_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.robots_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY robots_snapshot_scope ON app.robots_snapshots
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.commit_robots_snapshot(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_snapshot_id uuid,
    p_origin text,
    p_robots_url text,
    p_final_url text,
    p_fetch_profile_hash bytea,
    p_product_token text,
    p_parser_release text,
    p_fetched_at timestamptz,
    p_expires_at timestamptz,
    p_retrieval_outcome text,
    p_decision_status text,
    p_http_status integer,
    p_response_headers jsonb,
    p_redirect_chain jsonb,
    p_resolved_address inet,
    p_media_type text,
    p_source_hash bytea,
    p_rules_hash bytea,
    p_parse_error_count integer,
    p_rule_count integer,
    p_crawl_delay_ms integer,
    p_network_profile_hash bytea,
    p_recorded_at timestamptz,
    p_artifact_id uuid,
    p_object_key text,
    p_object_version text,
    p_artifact_hash bytea,
    p_artifact_byte_length bigint,
    p_artifact_media_type text,
    p_encryption_key_ref text,
    p_artifact_created_at timestamptz,
    p_retain_until timestamptz,
    p_attestation_id uuid
)
RETURNS TABLE (
    snapshot_id uuid, crawl_run_id uuid, origin text, robots_url text, final_url text,
    fetch_profile_hash bytea, product_token text, parser_release text,
    fetched_at timestamptz, expires_at timestamptz, retrieval_outcome text,
    decision_status text, http_status integer, response_headers jsonb, redirect_chain jsonb,
    resolved_address text, media_type text, source_hash bytea, rules_hash bytea,
    parse_error_count integer, rule_count integer, crawl_delay_ms integer,
    network_profile_hash bytea, raw_artifact_id uuid, object_key text, object_version text,
    artifact_hash bytea, artifact_byte_length bigint, artifact_media_type text,
    encryption_key_ref text, artifact_created_at timestamptz, retain_until timestamptz,
    legal_hold boolean, durability_state text, duplicate boolean, outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_existing record;
    v_run record;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_snapshot_id IS NULL OR p_origin IS NULL OR p_robots_url IS NULL
       OR p_final_url IS NULL OR octet_length(p_fetch_profile_hash) IS DISTINCT FROM 32
       OR p_product_token !~ '^[A-Za-z_-]{1,64}$'
       OR p_parser_release IS DISTINCT FROM 'protego/0.6.2+signal-rfc9309-v1'
       OR p_fetched_at IS NULL OR p_expires_at IS NULL OR p_expires_at <= p_fetched_at
       OR p_expires_at > p_fetched_at + interval '24 hours'
       OR p_fetched_at > statement_timestamp() + interval '5 minutes'
       OR p_recorded_at IS NULL OR p_recorded_at < p_fetched_at
       OR p_recorded_at > statement_timestamp() + interval '5 minutes'
       OR p_retrieval_outcome NOT IN (
            'fetched', 'not_found', 'forbidden', 'backoff', 'server_error', 'client_error',
            'transport_error', 'policy_rejected', 'unsupported_encoding',
            'unsupported_media_type', 'body_limit'
       )
       OR p_decision_status NOT IN ('rules', 'allow_missing', 'deny', 'backoff', 'suspended')
       OR control.valid_fetch_headers(p_response_headers) IS NOT TRUE
       OR jsonb_typeof(p_redirect_chain) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_redirect_chain) > 5
       OR (p_resolved_address IS NOT NULL
           AND control.valid_crawl_public_address(p_resolved_address) IS NOT TRUE)
       OR (p_media_type IS NOT NULL
           AND control.valid_artifact_media_type(p_media_type) IS NOT TRUE)
       OR p_parse_error_count NOT BETWEEN 0 AND 10000 OR p_rule_count NOT BETWEEN 0 AND 10000
       OR (p_crawl_delay_ms IS NOT NULL AND p_crawl_delay_ms NOT BETWEEN 1 AND 60000)
       OR octet_length(p_network_profile_hash) IS DISTINCT FROM 32
    THEN
        RAISE EXCEPTION 'invalid_robots_snapshot' USING ERRCODE = '22023';
    END IF;
    IF NOT (
        (p_retrieval_outcome = 'fetched' AND p_decision_status = 'rules'
            AND p_http_status BETWEEN 200 AND 299 AND p_media_type = 'text/plain')
        OR (p_retrieval_outcome = 'not_found' AND p_decision_status = 'allow_missing'
            AND p_http_status = 404)
        OR (p_retrieval_outcome = 'forbidden' AND p_decision_status = 'deny'
            AND p_http_status IN (401, 403))
        OR (p_retrieval_outcome = 'backoff' AND p_decision_status = 'backoff'
            AND p_http_status = 429)
        OR (p_retrieval_outcome = 'server_error' AND p_decision_status = 'suspended'
            AND p_http_status BETWEEN 500 AND 599)
        OR (p_retrieval_outcome = 'client_error' AND p_decision_status = 'suspended'
            AND p_http_status BETWEEN 400 AND 499
            AND p_http_status NOT IN (401, 403, 404, 429))
        OR (p_retrieval_outcome = 'policy_rejected' AND p_decision_status = 'suspended'
            AND (p_http_status IS NULL OR p_http_status BETWEEN 100 AND 599))
        OR (p_retrieval_outcome = 'transport_error' AND p_decision_status = 'suspended')
        OR (p_retrieval_outcome IN (
                'unsupported_encoding', 'unsupported_media_type', 'body_limit'
            ) AND p_decision_status = 'suspended' AND p_http_status BETWEEN 200 AND 299)
    ) THEN
        RAISE EXCEPTION 'invalid_robots_status_policy' USING ERRCODE = '22023';
    END IF;
    IF p_retrieval_outcome = 'fetched' THEN
        IF p_artifact_id IS NULL OR p_object_key IS NULL OR p_object_version IS NULL
           OR octet_length(p_artifact_hash) IS DISTINCT FROM 32
           OR p_artifact_byte_length IS NULL OR p_artifact_byte_length NOT BETWEEN 0 AND 512000
           OR p_artifact_media_type IS DISTINCT FROM 'text/plain'
           OR p_source_hash IS DISTINCT FROM p_artifact_hash
           OR octet_length(p_rules_hash) IS DISTINCT FROM 32
           OR control.valid_artifact_key_reference(p_encryption_key_ref) IS NOT TRUE
           OR p_artifact_created_at IS NULL OR p_artifact_created_at < p_fetched_at
           OR p_artifact_created_at > statement_timestamp() + interval '5 minutes'
           OR p_retain_until IS NULL OR p_retain_until <= p_artifact_created_at
           OR p_attestation_id IS NULL
        THEN
            RAISE EXCEPTION 'invalid_robots_artifact' USING ERRCODE = '22023';
        END IF;
    ELSIF p_artifact_id IS NOT NULL OR p_object_key IS NOT NULL OR p_object_version IS NOT NULL
       OR p_artifact_hash IS NOT NULL OR p_artifact_byte_length IS NOT NULL
       OR p_artifact_media_type IS NOT NULL OR p_encryption_key_ref IS NOT NULL
       OR p_artifact_created_at IS NOT NULL OR p_retain_until IS NOT NULL
       OR p_attestation_id IS NOT NULL OR p_source_hash IS NOT NULL OR p_rules_hash IS NOT NULL
       OR p_parse_error_count <> 0 OR p_rule_count <> 0 OR p_crawl_delay_ms IS NOT NULL
    THEN
        RAISE EXCEPTION 'unexpected_robots_artifact' USING ERRCODE = '22023';
    END IF;
    IF p_retrieval_outcome NOT IN ('transport_error', 'policy_rejected')
       AND p_resolved_address IS NULL
    THEN
        RAISE EXCEPTION 'robots_address_required' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_tenant_id::text || ':' || p_snapshot_id::text, 0
    ));

    SELECT snapshot.*, artifact.object_key, artifact.object_version,
           artifact.sha256 AS artifact_hash, artifact.byte_length AS artifact_byte_length,
           artifact.media_type AS artifact_media_type, artifact.encryption_key_ref,
           artifact.created_at AS artifact_created_at, artifact.retain_until,
           artifact.legal_hold, artifact.durability_state
      INTO v_existing
      FROM app.robots_snapshots AS snapshot
      LEFT JOIN app.artifacts AS artifact
        ON artifact.tenant_id = snapshot.tenant_id
       AND artifact.site_id = snapshot.site_id
       AND artifact.id = snapshot.raw_artifact_id
     WHERE snapshot.tenant_id = p_tenant_id AND snapshot.id = p_snapshot_id;
    IF FOUND THEN
        IF v_existing.site_id = p_site_id AND v_existing.crawl_run_id = p_crawl_run_id
           AND v_existing.origin = p_origin AND v_existing.robots_url = p_robots_url
           AND v_existing.final_url = p_final_url
           AND v_existing.fetch_profile_hash = p_fetch_profile_hash
           AND v_existing.product_token = p_product_token
           AND v_existing.parser_release = p_parser_release
           AND v_existing.fetched_at = p_fetched_at AND v_existing.expires_at = p_expires_at
           AND v_existing.retrieval_outcome = p_retrieval_outcome
           AND v_existing.decision_status = p_decision_status
           AND v_existing.http_status IS NOT DISTINCT FROM p_http_status
           AND v_existing.response_headers = p_response_headers
           AND v_existing.redirect_chain = p_redirect_chain
           AND v_existing.resolved_address IS NOT DISTINCT FROM p_resolved_address
           AND v_existing.media_type IS NOT DISTINCT FROM p_media_type
           AND v_existing.source_hash IS NOT DISTINCT FROM p_source_hash
           AND v_existing.rules_hash IS NOT DISTINCT FROM p_rules_hash
           AND v_existing.parse_error_count = p_parse_error_count
           AND v_existing.rule_count = p_rule_count
           AND v_existing.crawl_delay_ms IS NOT DISTINCT FROM p_crawl_delay_ms
           AND v_existing.network_profile_hash = p_network_profile_hash
           AND v_existing.recorded_at = p_recorded_at
           AND v_existing.raw_artifact_id IS NOT DISTINCT FROM p_artifact_id
           AND v_existing.object_key IS NOT DISTINCT FROM p_object_key
           AND v_existing.object_version IS NOT DISTINCT FROM p_object_version
           AND v_existing.artifact_hash IS NOT DISTINCT FROM p_artifact_hash
           AND v_existing.artifact_byte_length IS NOT DISTINCT FROM p_artifact_byte_length
           AND v_existing.artifact_media_type IS NOT DISTINCT FROM p_artifact_media_type
           AND v_existing.encryption_key_ref IS NOT DISTINCT FROM p_encryption_key_ref
           AND v_existing.artifact_created_at IS NOT DISTINCT FROM p_artifact_created_at
           AND v_existing.retain_until IS NOT DISTINCT FROM p_retain_until
           AND (p_artifact_id IS NULL OR EXISTS (
                SELECT 1 FROM app.artifact_attestations AS attestation
                 WHERE attestation.tenant_id = p_tenant_id
                   AND attestation.site_id = p_site_id
                   AND attestation.id = p_attestation_id
                   AND attestation.artifact_id = p_artifact_id
                   AND attestation.check_type = 'upload_readback'
                   AND attestation.result = 'verified'
                   AND attestation.verified_hash = p_artifact_hash
           ))
        THEN
            RETURN QUERY SELECT v_existing.id, v_existing.crawl_run_id, v_existing.origin,
                v_existing.robots_url, v_existing.final_url,
                v_existing.fetch_profile_hash, v_existing.product_token,
                v_existing.parser_release, v_existing.fetched_at, v_existing.expires_at,
                v_existing.retrieval_outcome, v_existing.decision_status,
                v_existing.http_status, v_existing.response_headers,
                v_existing.redirect_chain, host(v_existing.resolved_address),
                v_existing.media_type, v_existing.source_hash, v_existing.rules_hash,
                v_existing.parse_error_count, v_existing.rule_count, v_existing.crawl_delay_ms,
                v_existing.network_profile_hash, v_existing.raw_artifact_id,
                v_existing.object_key, v_existing.object_version, v_existing.artifact_hash,
                v_existing.artifact_byte_length, v_existing.artifact_media_type,
                v_existing.encryption_key_ref, v_existing.artifact_created_at,
                v_existing.retain_until, v_existing.legal_hold, v_existing.durability_state,
                true, 'recorded'::text;
        ELSE
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text, NULL::text,
                NULL::bytea, NULL::text, NULL::text, NULL::timestamptz, NULL::timestamptz,
                NULL::text, NULL::text, NULL::integer, NULL::jsonb, NULL::jsonb, NULL::text,
                NULL::text, NULL::bytea, NULL::bytea, NULL::integer, NULL::integer,
                NULL::integer, NULL::bytea, NULL::uuid, NULL::text, NULL::text, NULL::bytea,
                NULL::bigint, NULL::text, NULL::text, NULL::timestamptz, NULL::timestamptz,
                NULL::boolean, NULL::text, NULL::boolean, 'snapshot_conflict'::text;
        END IF;
        RETURN;
    END IF;

    SELECT run.* INTO v_run
      FROM app.crawl_runs AS run
     WHERE run.tenant_id = p_tenant_id AND run.site_id = p_site_id
       AND run.id = p_crawl_run_id
     FOR SHARE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(v_run.scope_snapshot->'allowed_origins') item(value)
         WHERE item.value = p_origin
    ) OR v_run.fetch_profile_hash <> p_fetch_profile_hash
       OR split_part(v_run.scope_snapshot->>'user_agent', '/', 1) <> p_product_token
       OR p_robots_url <> p_origin || '/robots.txt'
       OR control.valid_fetch_url_for_scope(p_robots_url, v_run.scope_snapshot) IS NOT TRUE
       OR control.valid_fetch_url_for_scope(p_final_url, v_run.scope_snapshot) IS NOT TRUE
       OR control.valid_fetch_redirect_chain(
            p_redirect_chain, v_run.scope_snapshot, p_robots_url, p_final_url, 5
          ) IS NOT TRUE
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text, NULL::text,
            NULL::bytea, NULL::text, NULL::text, NULL::timestamptz, NULL::timestamptz,
            NULL::text, NULL::text, NULL::integer, NULL::jsonb, NULL::jsonb, NULL::text,
            NULL::text, NULL::bytea, NULL::bytea, NULL::integer, NULL::integer,
            NULL::integer, NULL::bytea, NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::bigint, NULL::text, NULL::text, NULL::timestamptz, NULL::timestamptz,
            NULL::boolean, NULL::text, NULL::boolean, 'snapshot_unavailable'::text;
        RETURN;
    END IF;

    IF p_artifact_id IS NOT NULL THEN
        INSERT INTO app.artifacts (
            tenant_id, site_id, id, object_key, object_version, sha256, byte_length,
            media_type, encryption_key_ref, created_at, retain_until
        ) VALUES (
            p_tenant_id, p_site_id, p_artifact_id, p_object_key, p_object_version,
            p_artifact_hash, p_artifact_byte_length, p_artifact_media_type,
            p_encryption_key_ref, p_artifact_created_at, p_retain_until
        );
        INSERT INTO app.artifact_attestations (
            tenant_id, site_id, id, artifact_id, check_type, result, verified_hash, verified_at
        ) VALUES (
            p_tenant_id, p_site_id, p_attestation_id, p_artifact_id,
            'upload_readback', 'verified', p_artifact_hash, p_artifact_created_at
        );
    END IF;
    INSERT INTO app.robots_snapshots (
        tenant_id, site_id, id, crawl_run_id, origin, robots_url, final_url,
        fetch_profile_hash, product_token, parser_release, fetched_at, expires_at,
        retrieval_outcome, decision_status, http_status, response_headers, redirect_chain,
        resolved_address, media_type, raw_artifact_id, source_hash, rules_hash,
        parse_error_count, rule_count, crawl_delay_ms, network_profile_hash, recorded_at
    ) VALUES (
        p_tenant_id, p_site_id, p_snapshot_id, p_crawl_run_id, p_origin, p_robots_url,
        p_final_url, p_fetch_profile_hash, p_product_token, p_parser_release,
        p_fetched_at, p_expires_at, p_retrieval_outcome, p_decision_status, p_http_status,
        p_response_headers, p_redirect_chain, p_resolved_address, p_media_type, p_artifact_id,
        p_source_hash, p_rules_hash, p_parse_error_count, p_rule_count, p_crawl_delay_ms,
        p_network_profile_hash, p_recorded_at
    );
    RETURN QUERY SELECT p_snapshot_id, p_crawl_run_id, p_origin, p_robots_url, p_final_url,
        p_fetch_profile_hash, p_product_token, p_parser_release, p_fetched_at, p_expires_at,
        p_retrieval_outcome, p_decision_status, p_http_status, p_response_headers,
        p_redirect_chain, host(p_resolved_address), p_media_type, p_source_hash, p_rules_hash,
        p_parse_error_count, p_rule_count, p_crawl_delay_ms, p_network_profile_hash,
        p_artifact_id, p_object_key, p_object_version, p_artifact_hash,
        p_artifact_byte_length, p_artifact_media_type, p_encryption_key_ref,
        p_artifact_created_at, p_retain_until, false, 'verified'::text,
        false, 'recorded'::text;
END;
$$;

CREATE FUNCTION control.get_current_robots_snapshot(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_origin text,
    p_fetch_profile_hash bytea,
    p_at_time timestamptz
)
RETURNS TABLE (
    snapshot_id uuid, crawl_run_id uuid, origin text, robots_url text, final_url text,
    fetch_profile_hash bytea, product_token text, parser_release text,
    fetched_at timestamptz, expires_at timestamptz, retrieval_outcome text,
    decision_status text, http_status integer, response_headers jsonb, redirect_chain jsonb,
    resolved_address text, media_type text, source_hash bytea, rules_hash bytea,
    parse_error_count integer, rule_count integer, crawl_delay_ms integer,
    network_profile_hash bytea, raw_artifact_id uuid, object_key text, object_version text,
    artifact_hash bytea, artifact_byte_length bigint, artifact_media_type text,
    encryption_key_ref text, artifact_created_at timestamptz, retain_until timestamptz,
    legal_hold boolean, durability_state text, outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_origin IS NULL OR octet_length(p_fetch_profile_hash) IS DISTINCT FROM 32
       OR p_at_time IS NULL
    THEN
        RAISE EXCEPTION 'invalid_robots_lookup' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY
    SELECT snapshot.id, snapshot.crawl_run_id, snapshot.origin, snapshot.robots_url,
        snapshot.final_url, snapshot.fetch_profile_hash, snapshot.product_token,
        snapshot.parser_release, snapshot.fetched_at, snapshot.expires_at,
        snapshot.retrieval_outcome, snapshot.decision_status, snapshot.http_status,
        snapshot.response_headers, snapshot.redirect_chain, host(snapshot.resolved_address),
        snapshot.media_type, snapshot.source_hash, snapshot.rules_hash,
        snapshot.parse_error_count, snapshot.rule_count, snapshot.crawl_delay_ms,
        snapshot.network_profile_hash, snapshot.raw_artifact_id, artifact.object_key,
        artifact.object_version, artifact.sha256, artifact.byte_length, artifact.media_type,
        artifact.encryption_key_ref, artifact.created_at, artifact.retain_until,
        artifact.legal_hold, artifact.durability_state, 'found'::text
      FROM app.robots_snapshots AS snapshot
      LEFT JOIN app.artifacts AS artifact
        ON artifact.tenant_id = snapshot.tenant_id
       AND artifact.site_id = snapshot.site_id
       AND artifact.id = snapshot.raw_artifact_id
     WHERE snapshot.tenant_id = p_tenant_id AND snapshot.site_id = p_site_id
       AND snapshot.crawl_run_id = p_crawl_run_id AND snapshot.origin = p_origin
       AND snapshot.fetch_profile_hash = p_fetch_profile_hash
       AND snapshot.fetched_at <= p_at_time AND snapshot.expires_at > p_at_time
     ORDER BY snapshot.fetched_at DESC, snapshot.id DESC
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text, NULL::text,
            NULL::bytea, NULL::text, NULL::text, NULL::timestamptz, NULL::timestamptz,
            NULL::text, NULL::text, NULL::integer, NULL::jsonb, NULL::jsonb, NULL::text,
            NULL::text, NULL::bytea, NULL::bytea, NULL::integer, NULL::integer,
            NULL::integer, NULL::bytea, NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::bigint, NULL::text, NULL::text, NULL::timestamptz, NULL::timestamptz,
            NULL::boolean, NULL::text, 'missing'::text;
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION control.commit_robots_snapshot(
    uuid, uuid, uuid, uuid, text, text, text, bytea, text, text, timestamptz,
    timestamptz, text, text, integer, jsonb, jsonb, inet, text, bytea, bytea,
    integer, integer, integer, bytea, timestamptz, uuid, text, text, bytea, bigint,
    text, text, timestamptz, timestamptz, uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.get_current_robots_snapshot(
    uuid, uuid, uuid, text, bytea, timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.commit_robots_snapshot(
    uuid, uuid, uuid, uuid, text, text, text, bytea, text, text, timestamptz,
    timestamptz, text, text, integer, jsonb, jsonb, inet, text, bytea, bytea,
    integer, integer, integer, bytea, timestamptz, uuid, text, text, bytea, bigint,
    text, text, timestamptz, timestamptz, uuid
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.get_current_robots_snapshot(
    uuid, uuid, uuid, text, bytea, timestamptz
) TO signal_crawl_ingest;
