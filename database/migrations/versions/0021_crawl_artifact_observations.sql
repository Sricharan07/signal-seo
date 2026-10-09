CREATE FUNCTION control.valid_artifact_media_type(p_media_type text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT length(p_media_type) BETWEEN 3 AND 129
       AND p_media_type ~ '^[a-z0-9!#$&^_.+-]{1,64}/[a-z0-9!#$&^_.+-]{1,64}$'
$$;

CREATE FUNCTION control.valid_artifact_key_reference(p_reference text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT length(p_reference) BETWEEN 2 AND 256
       AND p_reference ~ '^[a-z0-9][a-z0-9._:/-]*[a-z0-9]$'
       AND position('..' IN p_reference) = 0
       AND position('//' IN p_reference) = 0
$$;

CREATE FUNCTION control.valid_fetch_headers(p_headers jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_name text;
    v_value text;
    v_total integer := 0;
BEGIN
    IF jsonb_typeof(p_headers) <> 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(p_headers)) > 9
       OR EXISTS (
            SELECT 1
              FROM jsonb_each(p_headers) AS item(name, value)
             WHERE jsonb_typeof(item.value) <> 'string'
       )
    THEN
        RETURN false;
    END IF;
    FOR v_name, v_value IN SELECT key, value FROM jsonb_each_text(p_headers)
    LOOP
        IF v_name NOT IN (
                'cache-control', 'content-encoding', 'content-length', 'content-type',
                'etag', 'last-modified', 'location', 'retry-after', 'transfer-encoding'
           )
           OR length(v_value) NOT BETWEEN 1 AND 2048
           OR v_value !~ '^[ -~]+$'
        THEN
            RETURN false;
        END IF;
        v_total := v_total + length(v_name) + length(v_value);
        IF v_total > 16384 THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION control.valid_crawl_public_address(p_address inet)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT masklen(p_address) = CASE family(p_address) WHEN 4 THEN 32 ELSE 128 END
       AND NOT (
            p_address <<= '0.0.0.0/8'::inet
            OR p_address <<= '10.0.0.0/8'::inet
            OR p_address <<= '100.64.0.0/10'::inet
            OR p_address <<= '127.0.0.0/8'::inet
            OR p_address <<= '169.254.0.0/16'::inet
            OR p_address <<= '172.16.0.0/12'::inet
            OR p_address <<= '192.0.0.0/24'::inet
            OR p_address <<= '192.0.2.0/24'::inet
            OR p_address <<= '192.168.0.0/16'::inet
            OR p_address <<= '198.18.0.0/15'::inet
            OR p_address <<= '198.51.100.0/24'::inet
            OR p_address <<= '203.0.113.0/24'::inet
            OR p_address <<= '224.0.0.0/4'::inet
            OR p_address <<= '240.0.0.0/4'::inet
            OR p_address <<= '::/128'::inet
            OR p_address <<= '::1/128'::inet
            OR p_address <<= '::ffff:0:0/96'::inet
            OR p_address <<= '100::/64'::inet
            OR p_address <<= '2001:db8::/32'::inet
            OR p_address <<= '2001:10::/28'::inet
            OR p_address <<= 'fc00::/7'::inet
            OR p_address <<= 'fe80::/10'::inet
            OR p_address <<= 'ff00::/8'::inet
       )
$$;

CREATE FUNCTION control.valid_fetch_url_for_scope(p_url text, p_scope jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_origin text;
BEGIN
    v_origin := regexp_replace(p_url, '^((http|https)://[^/]+).*$','\1');
    RETURN control.valid_crawl_url_identity(p_url, p_url, p_url, v_origin, 1)
       AND EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(p_scope->'allowed_origins') AS item(value)
             WHERE item.value = v_origin
       );
END;
$$;

CREATE FUNCTION control.valid_fetch_redirect_chain(
    p_chain jsonb,
    p_scope jsonb,
    p_initial_url text,
    p_final_url text,
    p_max_redirects integer
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_value text;
    v_last text;
BEGIN
    IF jsonb_typeof(p_chain) <> 'array'
       OR jsonb_array_length(p_chain) > p_max_redirects
       OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_chain) AS item(value)
             WHERE jsonb_typeof(item.value) <> 'string'
       )
       OR jsonb_array_length(p_chain) IS DISTINCT FROM (
            SELECT count(DISTINCT item.value)
              FROM jsonb_array_elements_text(p_chain) AS item(value)
       )
       OR EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(p_chain) AS item(value)
             WHERE item.value = p_initial_url
       )
    THEN
        RETURN false;
    END IF;
    FOR v_value IN SELECT value FROM jsonb_array_elements_text(p_chain)
    LOOP
        IF control.valid_fetch_url_for_scope(v_value, p_scope) IS NOT TRUE THEN
            RETURN false;
        END IF;
        v_last := v_value;
    END LOOP;
    RETURN (v_last IS NULL AND p_final_url = p_initial_url)
        OR v_last = p_final_url;
END;
$$;

REVOKE ALL ON FUNCTION control.valid_artifact_media_type(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.valid_artifact_key_reference(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.valid_fetch_headers(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.valid_crawl_public_address(inet) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.valid_fetch_url_for_scope(text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.valid_fetch_redirect_chain(jsonb, jsonb, text, text, integer)
FROM PUBLIC;

ALTER TABLE app.crawl_frontier
    ADD CONSTRAINT crawl_frontier_exact_fetch_identity
    UNIQUE (tenant_id, site_id, crawl_run_id, id, url_id);

CREATE TABLE app.artifacts (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    object_key text NOT NULL,
    object_version text NOT NULL,
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    byte_length bigint NOT NULL CHECK (byte_length BETWEEN 0 AND 1073741824),
    media_type text NOT NULL CHECK (control.valid_artifact_media_type(media_type)),
    encryption_key_ref text NOT NULL
        CHECK (control.valid_artifact_key_reference(encryption_key_ref)),
    durability_state text NOT NULL DEFAULT 'verified'
        CHECK (durability_state IN ('verified', 'missing', 'corrupt', 'unreadable')),
    created_at timestamptz NOT NULL,
    retain_until timestamptz NOT NULL,
    legal_hold boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, id, sha256),
    UNIQUE (tenant_id, site_id, object_key, object_version),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    CHECK (object_version = encode(sha256, 'hex')),
    CHECK (object_key = 'artifacts/v1/' || tenant_id::text || '/' || site_id::text
        || '/' || id::text || '/' || encode(sha256, 'hex') || '.sig'),
    CHECK (retain_until > created_at)
);

CREATE INDEX artifacts_retention
ON app.artifacts (tenant_id, site_id, retain_until, id);
CREATE INDEX artifacts_integrity
ON app.artifacts (tenant_id, site_id, durability_state, id);

CREATE TABLE app.artifact_attestations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    check_type text NOT NULL
        CHECK (check_type IN ('upload_readback', 'scheduled_integrity', 'restore_verification')),
    result text NOT NULL CHECK (result IN ('verified', 'missing', 'corrupt', 'unreadable')),
    verified_hash bytea,
    verified_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, artifact_id)
        REFERENCES app.artifacts (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, artifact_id, verified_hash)
        REFERENCES app.artifacts (tenant_id, site_id, id, sha256),
    CHECK ((result = 'verified') = (verified_hash IS NOT NULL))
);

CREATE INDEX artifact_attestations_history
ON app.artifact_attestations (tenant_id, site_id, artifact_id, verified_at DESC, id);

CREATE TABLE app.fetch_observations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    frontier_id uuid NOT NULL,
    url_id uuid NOT NULL,
    fetch_attempt_id uuid NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    outcome text NOT NULL CHECK (outcome IN (
        'fetched', 'unsupported_encoding', 'body_limit', 'unsupported_media_type'
    )),
    http_status integer NOT NULL CHECK (http_status BETWEEN 100 AND 599),
    final_url text NOT NULL,
    response_headers jsonb NOT NULL CHECK (control.valid_fetch_headers(response_headers)),
    redirect_chain jsonb NOT NULL CHECK (
        jsonb_typeof(redirect_chain) = 'array' AND jsonb_array_length(redirect_chain) <= 10
    ),
    resolved_address inet NOT NULL CHECK (control.valid_crawl_public_address(resolved_address)),
    media_type text CHECK (media_type IS NULL OR control.valid_artifact_media_type(media_type)),
    raw_artifact_id uuid,
    decoded_bytes bigint NOT NULL CHECK (decoded_bytes BETWEEN 0 AND 1073741824),
    elapsed_ms integer NOT NULL CHECK (elapsed_ms BETWEEN 0 AND 120000),
    network_profile_hash bytea NOT NULL CHECK (octet_length(network_profile_hash) = 32),
    recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, fetch_attempt_id),
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
    FOREIGN KEY (tenant_id, site_id, raw_artifact_id)
        REFERENCES app.artifacts (tenant_id, site_id, id),
    CHECK (finished_at >= started_at),
    CHECK (
        (outcome = 'fetched' AND raw_artifact_id IS NOT NULL
            AND media_type IN ('text/html', 'application/xhtml+xml'))
        OR
        (outcome <> 'fetched' AND raw_artifact_id IS NULL AND decoded_bytes = 0)
    )
);

CREATE INDEX fetch_observations_url_history
ON app.fetch_observations (tenant_id, site_id, url_id, started_at DESC, id);
CREATE INDEX fetch_observations_run_outcome
ON app.fetch_observations (tenant_id, site_id, crawl_run_id, outcome, id);
CREATE INDEX fetch_observations_artifact
ON app.fetch_observations (tenant_id, site_id, raw_artifact_id)
WHERE raw_artifact_id IS NOT NULL;

CREATE FUNCTION app.guard_artifact_mutation() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    v_attestation_id uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_artifact' USING ERRCODE = '55000';
    END IF;
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.site_id IS DISTINCT FROM NEW.site_id
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.object_key IS DISTINCT FROM NEW.object_key
       OR OLD.object_version IS DISTINCT FROM NEW.object_version
       OR OLD.sha256 IS DISTINCT FROM NEW.sha256
       OR OLD.byte_length IS DISTINCT FROM NEW.byte_length
       OR OLD.media_type IS DISTINCT FROM NEW.media_type
       OR OLD.encryption_key_ref IS DISTINCT FROM NEW.encryption_key_ref
       OR OLD.created_at IS DISTINCT FROM NEW.created_at
       OR OLD.retain_until IS DISTINCT FROM NEW.retain_until
       OR OLD.legal_hold IS DISTINCT FROM NEW.legal_hold
       OR OLD.durability_state IS NOT DISTINCT FROM NEW.durability_state
    THEN
        RAISE EXCEPTION 'immutable_artifact' USING ERRCODE = '55000';
    END IF;
    BEGIN
        v_attestation_id := current_setting('signal.artifact_attestation_id', true)::uuid;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'artifact_attestation_required' USING ERRCODE = '55000';
    END;
    IF NOT EXISTS (
        SELECT 1 FROM app.artifact_attestations AS attestation
         WHERE attestation.tenant_id = NEW.tenant_id
           AND attestation.site_id = NEW.site_id
           AND attestation.id = v_attestation_id
           AND attestation.artifact_id = NEW.id
           AND attestation.result = NEW.durability_state
    ) THEN
        RAISE EXCEPTION 'artifact_attestation_required' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION app.guard_artifact_attestation_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_artifact_attestation' USING ERRCODE = '55000'; END $$;

CREATE FUNCTION app.guard_fetch_observation_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_fetch_observation' USING ERRCODE = '55000'; END $$;

REVOKE ALL ON FUNCTION app.guard_artifact_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.guard_artifact_attestation_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.guard_fetch_observation_mutation() FROM PUBLIC;

CREATE TRIGGER artifacts_guard BEFORE UPDATE OR DELETE ON app.artifacts
FOR EACH ROW EXECUTE FUNCTION app.guard_artifact_mutation();
CREATE TRIGGER artifact_attestations_immutable
BEFORE UPDATE OR DELETE ON app.artifact_attestations
FOR EACH ROW EXECUTE FUNCTION app.guard_artifact_attestation_mutation();
CREATE TRIGGER fetch_observations_immutable
BEFORE UPDATE OR DELETE ON app.fetch_observations
FOR EACH ROW EXECUTE FUNCTION app.guard_fetch_observation_mutation();

ALTER TABLE app.artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.artifacts FORCE ROW LEVEL SECURITY;
CREATE POLICY artifact_scope ON app.artifacts
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.artifact_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.artifact_attestations FORCE ROW LEVEL SECURITY;
CREATE POLICY artifact_attestation_scope ON app.artifact_attestations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.fetch_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.fetch_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY fetch_observation_scope ON app.fetch_observations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.get_artifact(
    p_tenant_id uuid,
    p_site_id uuid,
    p_artifact_id uuid
)
RETURNS TABLE (
    artifact_id uuid,
    object_key text,
    object_version text,
    artifact_hash bytea,
    byte_length bigint,
    media_type text,
    encryption_key_ref text,
    created_at timestamptz,
    retain_until timestamptz,
    legal_hold boolean,
    durability_state text,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_artifact app.artifacts%%ROWTYPE;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_artifact_id IS NULL THEN
        RAISE EXCEPTION 'invalid_artifact_lookup' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT artifact.* INTO v_artifact
      FROM app.artifacts AS artifact
     WHERE artifact.tenant_id = p_tenant_id
       AND artifact.site_id = p_site_id
       AND artifact.id = p_artifact_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::bigint, NULL::text, NULL::text, NULL::timestamptz, NULL::timestamptz,
            NULL::boolean, NULL::text, 'missing'::text;
        RETURN;
    END IF;
    RETURN QUERY SELECT v_artifact.id, v_artifact.object_key, v_artifact.object_version,
        v_artifact.sha256, v_artifact.byte_length, v_artifact.media_type,
        v_artifact.encryption_key_ref, v_artifact.created_at, v_artifact.retain_until,
        v_artifact.legal_hold, v_artifact.durability_state, 'found'::text;
END;
$$;

CREATE FUNCTION control.commit_fetch_observation(
    p_tenant_id uuid,
    p_site_id uuid,
    p_crawl_run_id uuid,
    p_frontier_id uuid,
    p_url_id uuid,
    p_fetch_attempt_id uuid,
    p_lease_owner text,
    p_observation_id uuid,
    p_started_at timestamptz,
    p_finished_at timestamptz,
    p_observation_outcome text,
    p_http_status integer,
    p_final_url text,
    p_response_headers jsonb,
    p_redirect_chain jsonb,
    p_resolved_address inet,
    p_media_type text,
    p_decoded_bytes bigint,
    p_elapsed_ms integer,
    p_network_profile_hash bytea,
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
    observation_id uuid,
    crawl_run_id uuid,
    frontier_id uuid,
    url_id uuid,
    fetch_attempt_id uuid,
    started_at timestamptz,
    finished_at timestamptz,
    observation_outcome text,
    http_status integer,
    final_url text,
    response_headers jsonb,
    redirect_chain jsonb,
    resolved_address text,
    media_type text,
    decoded_bytes bigint,
    elapsed_ms integer,
    network_profile_hash bytea,
    raw_artifact_id uuid,
    object_key text,
    object_version text,
    artifact_hash bytea,
    artifact_byte_length bigint,
    artifact_media_type text,
    encryption_key_ref text,
    artifact_created_at timestamptz,
    retain_until timestamptz,
    legal_hold boolean,
    durability_state text,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_existing record;
    v_lease record;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_crawl_run_id IS NULL
       OR p_frontier_id IS NULL OR p_url_id IS NULL OR p_fetch_attempt_id IS NULL
       OR p_observation_id IS NULL
       OR p_lease_owner IS NULL OR p_lease_owner !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_started_at IS NULL OR p_finished_at IS NULL OR p_finished_at < p_started_at
       OR p_finished_at > statement_timestamp() + interval '5 minutes'
       OR p_observation_outcome IS NULL OR p_observation_outcome NOT IN (
            'fetched', 'unsupported_encoding', 'body_limit', 'unsupported_media_type'
       )
       OR p_http_status IS NULL OR p_http_status NOT BETWEEN 100 AND 599
       OR p_final_url IS NULL
       OR control.valid_fetch_headers(p_response_headers) IS NOT TRUE
       OR jsonb_typeof(p_redirect_chain) IS DISTINCT FROM 'array'
       OR control.valid_crawl_public_address(p_resolved_address) IS NOT TRUE
       OR (p_media_type IS NOT NULL
           AND control.valid_artifact_media_type(p_media_type) IS NOT TRUE)
       OR p_decoded_bytes IS NULL OR p_decoded_bytes NOT BETWEEN 0 AND 1073741824
       OR p_elapsed_ms IS NULL OR p_elapsed_ms NOT BETWEEN 0 AND 120000
       OR abs(
            (extract(epoch FROM (p_finished_at - p_started_at)) * 1000)::numeric
            - p_elapsed_ms::numeric
          ) > 1000
       OR octet_length(p_network_profile_hash) IS DISTINCT FROM 32
    THEN
        RAISE EXCEPTION 'invalid_fetch_observation' USING ERRCODE = '22023';
    END IF;
    IF p_observation_outcome = 'fetched' THEN
        IF p_artifact_id IS NULL OR p_object_key IS NULL OR p_object_version IS NULL
           OR octet_length(p_artifact_hash) IS DISTINCT FROM 32
           OR p_artifact_byte_length IS NULL OR p_artifact_byte_length <> p_decoded_bytes
           OR p_artifact_media_type IS DISTINCT FROM p_media_type
           OR p_media_type NOT IN ('text/html', 'application/xhtml+xml')
           OR control.valid_artifact_media_type(p_artifact_media_type) IS NOT TRUE
           OR control.valid_artifact_key_reference(p_encryption_key_ref) IS NOT TRUE
           OR p_artifact_created_at IS NULL OR p_artifact_created_at < p_finished_at
           OR p_artifact_created_at > statement_timestamp() + interval '5 minutes'
           OR p_retain_until IS NULL OR p_retain_until <= p_artifact_created_at
           OR p_attestation_id IS NULL
        THEN
            RAISE EXCEPTION 'invalid_fetch_artifact' USING ERRCODE = '22023';
        END IF;
    ELSIF p_artifact_id IS NOT NULL OR p_object_key IS NOT NULL
       OR p_object_version IS NOT NULL OR p_artifact_hash IS NOT NULL
       OR p_artifact_byte_length IS NOT NULL OR p_artifact_media_type IS NOT NULL
       OR p_encryption_key_ref IS NOT NULL OR p_artifact_created_at IS NOT NULL
       OR p_retain_until IS NOT NULL OR p_attestation_id IS NOT NULL
       OR p_decoded_bytes <> 0
    THEN
        RAISE EXCEPTION 'unexpected_fetch_artifact' USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);

    SELECT observation.*, artifact.object_key, artifact.object_version,
           artifact.sha256 AS artifact_hash, artifact.byte_length AS artifact_byte_length,
           artifact.media_type AS artifact_media_type, artifact.encryption_key_ref,
           artifact.created_at AS artifact_created_at, artifact.retain_until,
           artifact.legal_hold, artifact.durability_state
      INTO v_existing
      FROM app.fetch_observations AS observation
      LEFT JOIN app.artifacts AS artifact
        ON artifact.tenant_id = observation.tenant_id
       AND artifact.site_id = observation.site_id
       AND artifact.id = observation.raw_artifact_id
     WHERE observation.tenant_id = p_tenant_id
       AND observation.fetch_attempt_id = p_fetch_attempt_id;
    IF FOUND THEN
        IF v_existing.site_id = p_site_id
           AND v_existing.id = p_observation_id
           AND v_existing.crawl_run_id = p_crawl_run_id
           AND v_existing.frontier_id = p_frontier_id
           AND v_existing.url_id = p_url_id
           AND v_existing.started_at = p_started_at
           AND v_existing.finished_at = p_finished_at
           AND v_existing.outcome = p_observation_outcome
           AND v_existing.http_status = p_http_status
           AND v_existing.final_url = p_final_url
           AND v_existing.response_headers = p_response_headers
           AND v_existing.redirect_chain = p_redirect_chain
           AND v_existing.resolved_address = p_resolved_address
           AND v_existing.media_type IS NOT DISTINCT FROM p_media_type
           AND v_existing.decoded_bytes = p_decoded_bytes
           AND v_existing.elapsed_ms = p_elapsed_ms
           AND v_existing.network_profile_hash = p_network_profile_hash
           AND v_existing.raw_artifact_id IS NOT DISTINCT FROM p_artifact_id
           AND v_existing.object_key IS NOT DISTINCT FROM p_object_key
           AND v_existing.object_version IS NOT DISTINCT FROM p_object_version
           AND v_existing.artifact_hash IS NOT DISTINCT FROM p_artifact_hash
           AND v_existing.artifact_byte_length IS NOT DISTINCT FROM p_artifact_byte_length
           AND v_existing.artifact_media_type IS NOT DISTINCT FROM p_artifact_media_type
           AND v_existing.encryption_key_ref IS NOT DISTINCT FROM p_encryption_key_ref
           AND v_existing.artifact_created_at IS NOT DISTINCT FROM p_artifact_created_at
           AND v_existing.retain_until IS NOT DISTINCT FROM p_retain_until
        THEN
            RETURN QUERY SELECT v_existing.id, v_existing.crawl_run_id,
                v_existing.frontier_id, v_existing.url_id, v_existing.fetch_attempt_id,
                v_existing.started_at, v_existing.finished_at, v_existing.outcome,
                v_existing.http_status, v_existing.final_url, v_existing.response_headers,
                v_existing.redirect_chain, host(v_existing.resolved_address),
                v_existing.media_type, v_existing.decoded_bytes, v_existing.elapsed_ms,
                v_existing.network_profile_hash, v_existing.raw_artifact_id,
                v_existing.object_key, v_existing.object_version, v_existing.artifact_hash,
                v_existing.artifact_byte_length, v_existing.artifact_media_type,
                v_existing.encryption_key_ref, v_existing.artifact_created_at,
                v_existing.retain_until, v_existing.legal_hold, v_existing.durability_state,
                true, 'recorded'::text;
        ELSE
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::timestamptz, NULL::timestamptz, NULL::text,
                NULL::integer, NULL::text, NULL::jsonb, NULL::jsonb, NULL::text,
                NULL::text, NULL::bigint, NULL::integer, NULL::bytea, NULL::uuid,
                NULL::text, NULL::text, NULL::bytea, NULL::bigint, NULL::text,
                NULL::text, NULL::timestamptz, NULL::timestamptz, NULL::boolean,
                NULL::text, NULL::boolean, 'observation_conflict'::text;
        END IF;
        RETURN;
    END IF;

    SELECT receipt.issued_at, receipt.expires_at, receipt.lease_owner,
           frontier.url_id, url.fetch_url AS initial_url,
           run.scope_snapshot, run.limits_snapshot
      INTO v_lease
      FROM app.crawl_frontier_leases AS receipt
      JOIN app.crawl_frontier AS frontier
        ON frontier.tenant_id = receipt.tenant_id
       AND frontier.site_id = receipt.site_id
       AND frontier.crawl_run_id = receipt.crawl_run_id
       AND frontier.id = receipt.frontier_id
      JOIN app.crawl_runs AS run
        ON run.tenant_id = receipt.tenant_id
       AND run.site_id = receipt.site_id
       AND run.id = receipt.crawl_run_id
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id
       AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
     WHERE receipt.tenant_id = p_tenant_id
       AND receipt.site_id = p_site_id
       AND receipt.crawl_run_id = p_crawl_run_id
       AND receipt.frontier_id = p_frontier_id
       AND receipt.id = p_fetch_attempt_id
       AND receipt.lease_owner = p_lease_owner
       AND frontier.url_id = p_url_id
     FOR SHARE OF receipt, frontier, run;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::timestamptz, NULL::timestamptz, NULL::text,
            NULL::integer, NULL::text, NULL::jsonb, NULL::jsonb, NULL::text,
            NULL::text, NULL::bigint, NULL::integer, NULL::bytea, NULL::uuid,
            NULL::text, NULL::text, NULL::bytea, NULL::bigint, NULL::text,
            NULL::text, NULL::timestamptz, NULL::timestamptz, NULL::boolean,
            NULL::text, NULL::boolean, 'fetch_unavailable'::text;
        RETURN;
    END IF;
    IF p_started_at < v_lease.issued_at OR p_started_at >= v_lease.expires_at
       OR p_elapsed_ms > (v_lease.limits_snapshot->>'total_timeout_ms')::integer
       OR p_decoded_bytes > (v_lease.limits_snapshot->>'max_body_bytes')::bigint
       OR control.valid_fetch_url_for_scope(p_final_url, v_lease.scope_snapshot) IS NOT TRUE
       OR control.valid_fetch_redirect_chain(
            p_redirect_chain, v_lease.scope_snapshot, v_lease.initial_url, p_final_url,
            (v_lease.limits_snapshot->>'max_redirects')::integer
          ) IS NOT TRUE
    THEN
        RAISE EXCEPTION 'invalid_fetch_evidence' USING ERRCODE = '22023';
    END IF;

    IF p_artifact_id IS NOT NULL THEN
        INSERT INTO app.artifacts (
            tenant_id, site_id, id, object_key, object_version, sha256, byte_length,
            media_type, encryption_key_ref, durability_state, created_at, retain_until,
            legal_hold
        ) VALUES (
            p_tenant_id, p_site_id, p_artifact_id, p_object_key, p_object_version,
            p_artifact_hash, p_artifact_byte_length, p_artifact_media_type,
            p_encryption_key_ref, 'verified', p_artifact_created_at, p_retain_until, false
        );
        INSERT INTO app.artifact_attestations (
            tenant_id, site_id, id, artifact_id, check_type, result, verified_hash,
            verified_at
        ) VALUES (
            p_tenant_id, p_site_id, p_attestation_id, p_artifact_id,
            'upload_readback', 'verified', p_artifact_hash, p_artifact_created_at
        );
    END IF;
    INSERT INTO app.fetch_observations (
        tenant_id, site_id, id, crawl_run_id, frontier_id, url_id, fetch_attempt_id,
        started_at, finished_at, outcome, http_status, final_url, response_headers,
        redirect_chain, resolved_address, media_type, raw_artifact_id, decoded_bytes,
        elapsed_ms, network_profile_hash
    ) VALUES (
        p_tenant_id, p_site_id, p_observation_id, p_crawl_run_id, p_frontier_id,
        p_url_id, p_fetch_attempt_id, p_started_at, p_finished_at,
        p_observation_outcome, p_http_status, p_final_url, p_response_headers,
        p_redirect_chain, p_resolved_address, p_media_type, p_artifact_id,
        p_decoded_bytes, p_elapsed_ms, p_network_profile_hash
    );

    RETURN QUERY SELECT p_observation_id, p_crawl_run_id, p_frontier_id, p_url_id,
        p_fetch_attempt_id, p_started_at, p_finished_at, p_observation_outcome,
        p_http_status, p_final_url, p_response_headers, p_redirect_chain,
        host(p_resolved_address), p_media_type, p_decoded_bytes, p_elapsed_ms,
        p_network_profile_hash, p_artifact_id, p_object_key, p_object_version,
        p_artifact_hash, p_artifact_byte_length, p_artifact_media_type,
        p_encryption_key_ref, p_artifact_created_at, p_retain_until,
        CASE WHEN p_artifact_id IS NULL THEN NULL ELSE false END,
        CASE WHEN p_artifact_id IS NULL THEN NULL ELSE 'verified' END,
        false, 'recorded'::text;
END;
$$;

CREATE FUNCTION control.record_artifact_attestation(
    p_tenant_id uuid,
    p_site_id uuid,
    p_artifact_id uuid,
    p_attestation_id uuid,
    p_check_type text,
    p_result text,
    p_verified_hash bytea,
    p_verified_at timestamptz
)
RETURNS TABLE (
    attestation_id uuid,
    artifact_id uuid,
    check_type text,
    result text,
    verified_hash bytea,
    verified_at timestamptz,
    durability_state text,
    duplicate boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_existing app.artifact_attestations%%ROWTYPE;
    v_artifact app.artifacts%%ROWTYPE;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_artifact_id IS NULL
       OR p_attestation_id IS NULL
       OR p_check_type NOT IN ('scheduled_integrity', 'restore_verification')
       OR p_result NOT IN ('verified', 'missing', 'corrupt', 'unreadable')
       OR p_verified_at IS NULL
       OR p_verified_at > statement_timestamp() + interval '5 minutes'
       OR (p_result = 'verified') IS DISTINCT FROM (p_verified_hash IS NOT NULL)
       OR (p_verified_hash IS NOT NULL AND octet_length(p_verified_hash) <> 32)
    THEN
        RAISE EXCEPTION 'invalid_artifact_attestation' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);

    SELECT attestation.* INTO v_existing
      FROM app.artifact_attestations AS attestation
     WHERE attestation.tenant_id = p_tenant_id
       AND attestation.id = p_attestation_id;
    IF FOUND THEN
        IF v_existing.site_id = p_site_id
           AND v_existing.artifact_id = p_artifact_id
           AND v_existing.check_type = p_check_type
           AND v_existing.result = p_result
           AND v_existing.verified_hash IS NOT DISTINCT FROM p_verified_hash
           AND v_existing.verified_at = p_verified_at
        THEN
            RETURN QUERY SELECT v_existing.id, v_existing.artifact_id,
                v_existing.check_type, v_existing.result, v_existing.verified_hash,
                v_existing.verified_at, v_existing.result, true, 'recorded'::text;
        ELSE
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
                NULL::bytea, NULL::timestamptz, NULL::text, NULL::boolean,
                'attestation_conflict'::text;
        END IF;
        RETURN;
    END IF;

    SELECT artifact.* INTO v_artifact
      FROM app.artifacts AS artifact
     WHERE artifact.tenant_id = p_tenant_id
       AND artifact.site_id = p_site_id
       AND artifact.id = p_artifact_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::bytea, NULL::timestamptz, NULL::text, NULL::boolean,
            'artifact_unavailable'::text;
        RETURN;
    END IF;
    IF p_verified_at < v_artifact.created_at THEN
        RAISE EXCEPTION 'invalid_artifact_attestation_time' USING ERRCODE = '22023';
    END IF;
    IF p_result = 'verified' AND p_verified_hash <> v_artifact.sha256 THEN
        RAISE EXCEPTION 'artifact_hash_mismatch' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.artifact_attestations (
        tenant_id, site_id, id, artifact_id, check_type, result, verified_hash,
        verified_at
    ) VALUES (
        p_tenant_id, p_site_id, p_attestation_id, p_artifact_id, p_check_type,
        p_result, p_verified_hash, p_verified_at
    );
    PERFORM set_config('signal.artifact_attestation_id', p_attestation_id::text, true);
    UPDATE app.artifacts AS artifact
       SET durability_state = p_result
     WHERE artifact.tenant_id = p_tenant_id
       AND artifact.site_id = p_site_id
       AND artifact.id = p_artifact_id;
    RETURN QUERY SELECT p_attestation_id, p_artifact_id, p_check_type, p_result,
        p_verified_hash, p_verified_at, p_result, false, 'recorded'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.get_artifact(uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.commit_fetch_observation(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, timestamptz, timestamptz,
    text, integer, text, jsonb, jsonb, inet, text, bigint, integer, bytea,
    uuid, text, text, bytea, bigint, text, text, timestamptz, timestamptz, uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_artifact_attestation(
    uuid, uuid, uuid, uuid, text, text, bytea, timestamptz
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.get_artifact(uuid, uuid, uuid)
TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.commit_fetch_observation(
    uuid, uuid, uuid, uuid, uuid, uuid, text, uuid, timestamptz, timestamptz,
    text, integer, text, jsonb, jsonb, inet, text, bigint, integer, bytea,
    uuid, text, text, bytea, bigint, text, text, timestamptz, timestamptz, uuid
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.record_artifact_attestation(
    uuid, uuid, uuid, uuid, text, text, bytea, timestamptz
) TO signal_crawl_ingest;
