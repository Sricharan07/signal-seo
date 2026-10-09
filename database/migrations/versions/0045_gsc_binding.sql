CREATE TABLE app.gsc_oauth_attempts (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    verified_origin text NOT NULL,
    state_sha256 bytea NOT NULL CHECK (octet_length(state_sha256) = 32),
    redirect_uri text NOT NULL CHECK (length(redirect_uri) BETWEEN 12 AND 2048),
    code_challenge text NOT NULL CHECK (code_challenge ~ '^[A-Za-z0-9_-]{43}$'),
    secret_reference text NOT NULL,
    status text NOT NULL DEFAULT 'authorizing'
        CHECK (status IN ('authorizing', 'exchanging', 'selecting', 'confirmed')),
    candidates jsonb,
    issued_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    staged_at timestamptz,
    confirmed_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (state_sha256),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK (expires_at = issued_at + interval '10 minutes'),
    CHECK (secret_reference = 'secret://gsc/' || id::text),
    CHECK ((status = 'authorizing' AND consumed_at IS NULL AND staged_at IS NULL
            AND candidates IS NULL AND confirmed_at IS NULL)
        OR (status = 'exchanging' AND consumed_at IS NOT NULL AND staged_at IS NULL
            AND candidates IS NULL AND confirmed_at IS NULL)
        OR (status = 'selecting' AND consumed_at IS NOT NULL AND staged_at IS NOT NULL
            AND candidates IS NOT NULL AND confirmed_at IS NULL)
        OR (status = 'confirmed' AND consumed_at IS NOT NULL AND staged_at IS NOT NULL
            AND candidates IS NOT NULL AND confirmed_at IS NOT NULL))
);

CREATE TABLE app.gsc_binding_events (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    binding_id uuid NOT NULL,
    attempt_id uuid,
    actor_user_id uuid NOT NULL,
    event_kind text NOT NULL CHECK (event_kind IN ('bound', 'revoked', 'reauth_required')),
    verified_origin text NOT NULL,
    property_resource_name text NOT NULL,
    property_type text NOT NULL CHECK (property_type IN ('domain', 'url_prefix')),
    secret_reference text NOT NULL,
    granted_scope text NOT NULL CHECK (
        granted_scope = 'https://www.googleapis.com/auth/webmasters.readonly'
    ),
    reason text CHECK (reason IN (
        'owner_disconnect', 'provider_revoked', 'reduced_scope', 'provider_rotated'
    )),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, attempt_id)
        REFERENCES app.gsc_oauth_attempts (tenant_id, site_id, id),
    CHECK ((event_kind = 'bound' AND attempt_id IS NOT NULL AND reason IS NULL)
        OR (event_kind <> 'bound' AND reason IS NOT NULL))
);
CREATE UNIQUE INDEX gsc_binding_one_initial_event
ON app.gsc_binding_events (tenant_id, site_id, binding_id) WHERE event_kind = 'bound';

CREATE TABLE app.gsc_import_generations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    binding_id uuid NOT NULL,
    property_resource_name text NOT NULL,
    source text NOT NULL DEFAULT 'google_search_console'
        CHECK (source = 'google_search_console'),
    search_type text NOT NULL CHECK (search_type IN ('web', 'image', 'video', 'news', 'discover')),
    dimensions jsonb NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    data_state text NOT NULL CHECK (data_state IN ('final', 'all')),
    aggregation_type text NOT NULL CHECK (aggregation_type IN ('auto', 'byPage', 'byProperty')),
    rows jsonb NOT NULL,
    coverage jsonb NOT NULL,
    response_sha256 bytea NOT NULL CHECK (octet_length(response_sha256) = 32),
    egress_operation_id uuid NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    CHECK (start_date <= end_date AND end_date - start_date <= 92),
    CHECK (jsonb_typeof(dimensions) = 'array' AND jsonb_array_length(dimensions) BETWEEN 1 AND 5),
    CHECK (jsonb_typeof(rows) = 'array' AND jsonb_array_length(rows) <= 5000),
    CHECK (jsonb_typeof(coverage) = 'object')
);

ALTER TABLE app.gsc_oauth_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.gsc_oauth_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY gsc_attempt_scope ON app.gsc_oauth_attempts
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
ALTER TABLE app.gsc_binding_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.gsc_binding_events FORCE ROW LEVEL SECURITY;
CREATE POLICY gsc_binding_scope ON app.gsc_binding_events
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
ALTER TABLE app.gsc_import_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.gsc_import_generations FORCE ROW LEVEL SECURITY;
CREATE POLICY gsc_import_scope ON app.gsc_import_generations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE TRIGGER gsc_binding_events_immutable BEFORE UPDATE OR DELETE ON app.gsc_binding_events
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER gsc_import_generations_immutable BEFORE UPDATE OR DELETE ON app.gsc_import_generations
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.gsc_owner_context(
    p_session_hash bytea, p_recovery_generation text, p_site_id uuid
) RETURNS TABLE (tenant_id uuid, user_id uuid, origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_origin record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_recovery_generation
    );
    IF v_authority.outcome <> 'authorized' OR v_authority.role_key <> 'owner' THEN
        RETURN;
    END IF;
    SELECT * INTO v_origin FROM control.verified_site_origin(v_authority.tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT v_authority.tenant_id, v_authority.user_id, v_origin.origin;
END;
$$;

CREATE FUNCTION control.begin_gsc_oauth_attempt(
    p_session_hash bytea, p_recovery_generation text, p_site_id uuid,
    p_attempt_id uuid, p_state_sha256 bytea, p_redirect_uri text,
    p_code_challenge text
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record;
BEGIN
    IF p_attempt_id IS NULL OR p_attempt_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR octet_length(p_state_sha256) IS DISTINCT FROM 32
       OR p_redirect_uri !~ '^https://[^[:space:]?#]+/[^[:space:]?#]*$'
       OR length(p_redirect_uri) > 2048
       OR p_code_challenge !~ '^[A-Za-z0-9_-]{43}$'
    THEN RAISE EXCEPTION 'invalid_gsc_attempt' USING ERRCODE = '22023'; END IF;
    SELECT * INTO v_owner FROM control.gsc_owner_context(
        p_session_hash, p_recovery_generation, p_site_id
    );
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    INSERT INTO app.gsc_oauth_attempts (
        tenant_id, site_id, id, user_id, verified_origin, state_sha256,
        redirect_uri, code_challenge, secret_reference, expires_at
    ) VALUES (
        v_owner.tenant_id, p_site_id, p_attempt_id, v_owner.user_id, v_owner.origin,
        p_state_sha256, p_redirect_uri, p_code_challenge,
        'secret://gsc/' || p_attempt_id::text, transaction_timestamp() + interval '10 minutes'
    );
    RETURN 'created';
END;
$$;

CREATE FUNCTION control.consume_gsc_oauth_attempt(
    p_session_hash bytea, p_recovery_generation text, p_site_id uuid,
    p_attempt_id uuid, p_state_sha256 bytea, p_redirect_uri text
) RETURNS TABLE (outcome text, origin text, code_challenge text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record; v_attempt app.gsc_oauth_attempts%%ROWTYPE;
BEGIN
    SELECT * INTO v_owner FROM control.gsc_owner_context(
        p_session_hash, p_recovery_generation, p_site_id
    );
    IF NOT FOUND THEN RETURN QUERY SELECT 'denied'::text, NULL::text, NULL::text; RETURN; END IF;
    SELECT * INTO v_attempt FROM app.gsc_oauth_attempts AS attempt
     WHERE attempt.tenant_id = v_owner.tenant_id AND attempt.site_id = p_site_id
       AND attempt.id = p_attempt_id FOR UPDATE;
    IF NOT FOUND OR v_attempt.user_id <> v_owner.user_id
       OR v_attempt.verified_origin <> v_owner.origin
       OR v_attempt.state_sha256 IS DISTINCT FROM p_state_sha256
       OR v_attempt.redirect_uri IS DISTINCT FROM p_redirect_uri
       OR v_attempt.status <> 'authorizing'
       OR v_attempt.expires_at <= transaction_timestamp()
    THEN RETURN QUERY SELECT 'unavailable'::text, NULL::text, NULL::text; RETURN; END IF;
    UPDATE app.gsc_oauth_attempts AS attempt SET status = 'exchanging',
        consumed_at = transaction_timestamp()
     WHERE attempt.tenant_id = v_owner.tenant_id AND attempt.site_id = p_site_id
       AND attempt.id = p_attempt_id;
    RETURN QUERY SELECT 'consumed'::text, v_attempt.verified_origin, v_attempt.code_challenge;
END;
$$;

CREATE FUNCTION control.stage_gsc_oauth_attempt(
    p_session_hash bytea, p_recovery_generation text, p_site_id uuid,
    p_attempt_id uuid, p_secret_reference text, p_candidates jsonb
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record; v_attempt app.gsc_oauth_attempts%%ROWTYPE;
BEGIN
    IF p_secret_reference IS DISTINCT FROM 'secret://gsc/' || p_attempt_id::text
       OR jsonb_typeof(p_candidates) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_candidates) > 1000
    THEN RAISE EXCEPTION 'invalid_gsc_stage' USING ERRCODE = '22023'; END IF;
    SELECT * INTO v_owner FROM control.gsc_owner_context(
        p_session_hash, p_recovery_generation, p_site_id
    );
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    SELECT * INTO v_attempt FROM app.gsc_oauth_attempts AS attempt
     WHERE attempt.tenant_id = v_owner.tenant_id AND attempt.site_id = p_site_id
       AND attempt.id = p_attempt_id FOR UPDATE;
    IF NOT FOUND OR v_attempt.user_id <> v_owner.user_id
       OR v_attempt.verified_origin <> v_owner.origin
       OR v_attempt.status <> 'exchanging'
       OR v_attempt.expires_at <= transaction_timestamp()
    THEN RETURN 'unavailable'; END IF;
    UPDATE app.gsc_oauth_attempts AS attempt SET status = 'selecting',
        staged_at = transaction_timestamp(), candidates = p_candidates
     WHERE attempt.tenant_id = v_owner.tenant_id AND attempt.site_id = p_site_id
       AND attempt.id = p_attempt_id;
    RETURN 'staged';
END;
$$;

CREATE FUNCTION control.confirm_gsc_binding(
    p_session_hash bytea, p_recovery_generation text, p_site_id uuid,
    p_attempt_id uuid, p_binding_id uuid, p_event_id uuid, p_property text
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record; v_attempt app.gsc_oauth_attempts%%ROWTYPE; v_candidate jsonb;
        v_type text; v_host text; v_domain text;
BEGIN
    SELECT * INTO v_owner FROM control.gsc_owner_context(
        p_session_hash, p_recovery_generation, p_site_id
    );
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    SELECT * INTO v_attempt FROM app.gsc_oauth_attempts AS attempt
     WHERE attempt.tenant_id = v_owner.tenant_id AND attempt.site_id = p_site_id
       AND attempt.id = p_attempt_id FOR UPDATE;
    IF NOT FOUND OR v_attempt.user_id <> v_owner.user_id
       OR v_attempt.verified_origin <> v_owner.origin
       OR v_attempt.status <> 'selecting'
       OR v_attempt.expires_at <= transaction_timestamp()
    THEN RETURN 'unavailable'; END IF;
    SELECT candidate.value INTO v_candidate
      FROM jsonb_array_elements(v_attempt.candidates) AS candidate(value)
     WHERE candidate.value->>'resource_name' = p_property
       AND candidate.value->>'eligible' = 'true'
     LIMIT 1;
    IF NOT FOUND THEN RETURN 'wrong_property'; END IF;
    v_type := v_candidate->>'property_type';
    v_host := split_part(split_part(v_owner.origin, '://', 2), ':', 1);
    IF v_type = 'url_prefix' THEN
        IF p_property <> v_owner.origin || '/' THEN RETURN 'wrong_property'; END IF;
    ELSIF v_type = 'domain' THEN
        v_domain := substring(p_property FROM 11);
        IF v_domain !~ '^[a-z0-9.-]+$' OR position('.' IN v_domain) = 0
           OR NOT (v_host = v_domain OR right(v_host, length(v_domain) + 1) = '.' || v_domain)
        THEN RETURN 'wrong_property'; END IF;
    ELSE RETURN 'wrong_property'; END IF;
    PERFORM 1 FROM app.sites AS site
     WHERE site.tenant_id = v_owner.tenant_id AND site.id = p_site_id FOR UPDATE;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS event
        WHERE event.tenant_id = v_owner.tenant_id AND event.site_id = p_site_id
          AND event.event_kind = 'bound'
          AND NOT EXISTS (SELECT 1 FROM app.gsc_binding_events AS later
              WHERE later.tenant_id = event.tenant_id AND later.site_id = event.site_id
                AND later.binding_id = event.binding_id AND later.event_kind <> 'bound'))
    THEN RETURN 'already_bound'; END IF;
    INSERT INTO app.gsc_binding_events (
        tenant_id, site_id, id, binding_id, attempt_id, actor_user_id,
        event_kind, verified_origin, property_resource_name, property_type,
        secret_reference, granted_scope
    ) VALUES (
        v_owner.tenant_id, p_site_id, p_event_id, p_binding_id, p_attempt_id,
        v_owner.user_id, 'bound', v_owner.origin, p_property, v_type,
        v_attempt.secret_reference, 'https://www.googleapis.com/auth/webmasters.readonly'
    );
    UPDATE app.gsc_oauth_attempts AS attempt SET status = 'confirmed',
        confirmed_at = transaction_timestamp()
     WHERE attempt.tenant_id = v_owner.tenant_id AND attempt.site_id = p_site_id
       AND attempt.id = p_attempt_id;
    RETURN 'bound';
END;
$$;

CREATE FUNCTION control.revoke_gsc_binding(
    p_session_hash bytea, p_recovery_generation text, p_site_id uuid,
    p_binding_id uuid, p_event_id uuid
) RETURNS TABLE (outcome text, secret_reference text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_bound app.gsc_binding_events%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_recovery_generation
    );
    IF v_authority.outcome <> 'authorized' OR v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT 'denied'::text, NULL::text; RETURN;
    END IF;
    SELECT * INTO v_bound FROM app.gsc_binding_events AS event
     WHERE event.tenant_id = v_authority.tenant_id AND event.site_id = p_site_id
       AND event.binding_id = p_binding_id AND event.event_kind = 'bound' FOR UPDATE;
    IF NOT FOUND THEN RETURN QUERY SELECT 'unavailable'::text, NULL::text; RETURN; END IF;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS event
       WHERE event.tenant_id = v_authority.tenant_id AND event.site_id = p_site_id
         AND event.binding_id = p_binding_id AND event.event_kind <> 'bound')
    THEN RETURN QUERY SELECT 'already_revoked'::text, NULL::text; RETURN; END IF;
    INSERT INTO app.gsc_binding_events (
        tenant_id, site_id, id, binding_id, actor_user_id, event_kind,
        verified_origin, property_resource_name, property_type, secret_reference,
        granted_scope, reason
    ) VALUES (
        v_authority.tenant_id, p_site_id, p_event_id, p_binding_id, v_authority.user_id,
        'revoked', v_bound.verified_origin, v_bound.property_resource_name,
        v_bound.property_type, v_bound.secret_reference, v_bound.granted_scope,
        'owner_disconnect'
    );
    RETURN QUERY SELECT 'revoked'::text, v_bound.secret_reference;
END;
$$;

CREATE FUNCTION control.current_gsc_binding(p_tenant_id uuid, p_site_id uuid)
RETURNS TABLE (binding_id uuid, property_resource_name text, secret_reference text, origin text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_origin record;
BEGIN
    SELECT * INTO v_origin FROM control.verified_site_origin(p_tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT event.binding_id, event.property_resource_name,
        event.secret_reference, event.verified_origin
      FROM app.gsc_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.event_kind = 'bound' AND event.verified_origin = v_origin.origin
       AND NOT EXISTS (SELECT 1 FROM app.gsc_binding_events AS later
          WHERE later.tenant_id = event.tenant_id AND later.site_id = event.site_id
            AND later.binding_id = event.binding_id AND later.event_kind <> 'bound')
     ORDER BY event.recorded_at DESC, event.id DESC LIMIT 1;
END;
$$;

CREATE FUNCTION control.mark_gsc_reauth_required(
    p_tenant_id uuid, p_site_id uuid, p_binding_id uuid,
    p_event_id uuid, p_reason text, p_egress_operation_id uuid
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_bound app.gsc_binding_events%%ROWTYPE; v_egress record;
BEGIN
    IF p_reason NOT IN ('provider_revoked', 'reduced_scope', 'provider_rotated') THEN
        RAISE EXCEPTION 'invalid_gsc_reauth_reason' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.state = 'observed'
       AND ((p_reason = 'provider_revoked' AND egress.http_status IN (400, 401, 403))
         OR (p_reason IN ('reduced_scope', 'provider_rotated') AND egress.http_status = 200))
       AND (egress.request_url = 'https://oauth2.googleapis.com/token'
         OR starts_with(egress.request_url, 'https://www.googleapis.com/webmasters/v3/sites/'));
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    SELECT * INTO v_bound FROM app.gsc_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.binding_id = p_binding_id AND event.event_kind = 'bound' FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable'; END IF;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS event
       WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
         AND event.binding_id = p_binding_id AND event.event_kind <> 'bound')
    THEN RETURN 'already_restricted'; END IF;
    INSERT INTO app.gsc_binding_events (
        tenant_id, site_id, id, binding_id, actor_user_id, event_kind,
        verified_origin, property_resource_name, property_type, secret_reference,
        granted_scope, reason
    ) VALUES (
        p_tenant_id, p_site_id, p_event_id, p_binding_id, v_bound.actor_user_id,
        'reauth_required', v_bound.verified_origin, v_bound.property_resource_name,
        v_bound.property_type, v_bound.secret_reference, v_bound.granted_scope, p_reason
    );
    RETURN 'restricted';
END;
$$;

CREATE FUNCTION control.record_gsc_import_generation(
    p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid,
    p_property text, p_search_type text, p_dimensions jsonb,
    p_start_date date, p_end_date date, p_data_state text,
    p_aggregation_type text, p_rows jsonb, p_coverage jsonb,
    p_response_sha256 bytea, p_egress_operation_id uuid
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_binding record; v_egress record;
BEGIN
    IF p_id IS NULL OR p_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_search_type NOT IN ('web', 'image', 'video', 'news', 'discover')
       OR p_data_state NOT IN ('all', 'final')
       OR p_aggregation_type NOT IN ('auto', 'byPage', 'byProperty')
       OR p_start_date IS NULL OR p_end_date IS NULL
       OR p_end_date < p_start_date OR p_end_date - p_start_date > 92
       OR jsonb_typeof(p_rows) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_rows) > 5000
       OR jsonb_typeof(p_coverage) IS DISTINCT FROM 'object'
       OR p_coverage->>'complete' IS DISTINCT FROM 'false'
       OR p_coverage->>'missing_data' IS NULL
       OR p_coverage->'filters' IS DISTINCT FROM '[]'::jsonb
       OR p_coverage->>'start_row' IS DISTINCT FROM '0'
       OR p_coverage->>'row_limit' IS DISTINCT FROM '5000'
       OR octet_length(p_response_sha256) IS DISTINCT FROM 32
    THEN RAISE EXCEPTION 'invalid_gsc_import' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_binding FROM control.current_gsc_binding(p_tenant_id, p_site_id);
    IF NOT FOUND OR v_binding.binding_id <> p_binding_id
       OR v_binding.property_resource_name <> p_property
    THEN RETURN 'binding_unavailable'; END IF;
    PERFORM 1 FROM app.gsc_binding_events AS bound
     WHERE bound.tenant_id = p_tenant_id AND bound.site_id = p_site_id
       AND bound.binding_id = p_binding_id AND bound.event_kind = 'bound' FOR UPDATE;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS restriction
       WHERE restriction.tenant_id = p_tenant_id AND restriction.site_id = p_site_id
         AND restriction.binding_id = p_binding_id AND restriction.event_kind <> 'bound')
    THEN RETURN 'binding_unavailable'; END IF;
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.method = 'POST' AND egress.state = 'observed'
       AND egress.http_status = 200 AND egress.response_sha256 = p_response_sha256
       AND starts_with(egress.request_url, 'https://www.googleapis.com/webmasters/v3/sites/')
       AND right(egress.request_url, length('/searchAnalytics/query')) = '/searchAnalytics/query';
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    INSERT INTO app.gsc_import_generations (
        tenant_id, site_id, id, binding_id, property_resource_name,
        search_type, dimensions, start_date, end_date, data_state,
        aggregation_type, rows, coverage, response_sha256, egress_operation_id
    ) VALUES (
        p_tenant_id, p_site_id, p_id, p_binding_id, p_property,
        p_search_type, p_dimensions, p_start_date, p_end_date, p_data_state,
        p_aggregation_type, p_rows, p_coverage, p_response_sha256, p_egress_operation_id
    );
    RETURN 'recorded';
END;
$$;

REVOKE ALL ON app.gsc_oauth_attempts, app.gsc_binding_events, app.gsc_import_generations
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;
REVOKE ALL ON FUNCTION control.gsc_owner_context(bytea, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.begin_gsc_oauth_attempt(bytea, text, uuid, uuid, bytea, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.consume_gsc_oauth_attempt(bytea, text, uuid, uuid, bytea, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.stage_gsc_oauth_attempt(bytea, text, uuid, uuid, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.confirm_gsc_binding(bytea, text, uuid, uuid, uuid, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.revoke_gsc_binding(bytea, text, uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.current_gsc_binding(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.mark_gsc_reauth_required(uuid, uuid, uuid, uuid, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_gsc_import_generation(
    uuid, uuid, uuid, uuid, text, text, jsonb, date, date, text, text,
    jsonb, jsonb, bytea, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.begin_gsc_oauth_attempt(bytea, text, uuid, uuid, bytea, text, text) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.consume_gsc_oauth_attempt(bytea, text, uuid, uuid, bytea, text) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.stage_gsc_oauth_attempt(bytea, text, uuid, uuid, text, jsonb) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.confirm_gsc_binding(bytea, text, uuid, uuid, uuid, uuid, text) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.revoke_gsc_binding(bytea, text, uuid, uuid, uuid) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.current_gsc_binding(uuid, uuid) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.mark_gsc_reauth_required(uuid, uuid, uuid, uuid, text, uuid)
TO signal_crawl_ingest;

-- Bind a closed request profile in the same transaction as the existing
-- intent-before-I/O admission. The legacy marker identifies older SQL-only
-- operations; application traffic cannot dispatch before this binding.
ALTER TABLE app.crawl_robots_dispatches ADD COLUMN egress_profile text NOT NULL
    DEFAULT 'crawl_robots' CHECK (egress_profile = 'crawl_robots');

ALTER TABLE app.egress_operations ADD COLUMN egress_profile text NOT NULL
    DEFAULT 'legacy_unqualified'
    CHECK (egress_profile IN (
        'legacy_unqualified', 'crawl_page', 'crawl_robots', 'browser_read',
        'github_rest', 'google_oauth_token', 'google_oauth_revoke', 'gsc_api',
        'bing_oauth_token', 'bing_api', 'jev', 'model_json', 'openai_model', 'openai_assistant',
        'perplexity_assistant', 'gemini_assistant'
    ));

CREATE OR REPLACE FUNCTION app.guard_egress_operation_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_egress_operation' USING ERRCODE = '55000';
    END IF;
    IF OLD.egress_profile = 'legacy_unqualified'
       AND NEW.egress_profile <> 'legacy_unqualified'
       AND OLD.state = 'dispatched' AND NEW.state = 'dispatched'
       AND to_jsonb(OLD) - 'egress_profile' = to_jsonb(NEW) - 'egress_profile'
    THEN
        RETURN NEW;
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
       OR OLD.egress_profile IS DISTINCT FROM NEW.egress_profile
       OR OLD.state <> 'dispatched'
       OR NEW.state NOT IN ('observed', 'failed')
    THEN
        RAISE EXCEPTION 'immutable_egress_operation' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION control.bind_shared_egress_profile(
    p_tenant_id uuid, p_site_id uuid, p_operation_id uuid,
    p_request_sha256 bytea, p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_operation app.egress_operations%%ROWTYPE;
BEGIN
    IF p_profile NOT IN (
        'crawl_page', 'crawl_robots', 'browser_read', 'github_rest',
        'google_oauth_token', 'google_oauth_revoke', 'gsc_api', 'bing_oauth_token', 'bing_api',
        'jev', 'model_json', 'openai_model', 'openai_assistant', 'perplexity_assistant',
        'gemini_assistant'
    ) OR p_tenant_id IS NULL OR p_site_id IS NULL OR p_operation_id IS NULL
       OR p_request_sha256 IS NULL OR octet_length(p_request_sha256) <> 32
    THEN
        RAISE EXCEPTION 'invalid_egress_profile' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_operation FROM app.egress_operations
     WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_operation_id
     FOR UPDATE;
    IF NOT FOUND OR v_operation.state <> 'dispatched'
       OR v_operation.request_sha256 IS DISTINCT FROM p_request_sha256
       OR (
            (p_profile IN ('crawl_page', 'crawl_robots')
                AND (v_operation.purpose <> 'crawl' OR v_operation.method <> 'GET'
                     OR v_operation.credentialed))
            OR (p_profile = 'browser_read'
                AND (v_operation.purpose <> 'browser' OR v_operation.method <> 'GET'
                     OR v_operation.credentialed))
            OR (p_profile IN ('github_rest', 'google_oauth_token',
                             'google_oauth_revoke', 'gsc_api', 'bing_oauth_token', 'bing_api',
                             'openai_assistant', 'perplexity_assistant', 'gemini_assistant')
                AND v_operation.purpose <> 'connector')
            OR (p_profile IN ('jev', 'model_json', 'openai_model')
                AND v_operation.purpose <> 'model')
            OR (p_profile = 'github_rest' AND v_operation.origin <> 'https://api.github.com')
            OR (p_profile IN ('google_oauth_token', 'google_oauth_revoke')
                AND v_operation.origin <> 'https://oauth2.googleapis.com')
            OR (p_profile = 'gsc_api' AND v_operation.origin <> 'https://www.googleapis.com')
            OR (p_profile IN ('bing_oauth_token', 'bing_api')
                AND v_operation.origin <> 'https://www.bing.com')
            OR (p_profile = 'jev' AND v_operation.origin <> 'https://api.typesafe.ai')
            OR (p_profile IN ('openai_model', 'openai_assistant')
                AND v_operation.origin <> 'https://api.openai.com')
            OR (p_profile = 'perplexity_assistant'
                AND v_operation.origin <> 'https://api.perplexity.ai')
            OR (p_profile = 'gemini_assistant'
                AND v_operation.origin <> 'https://generativelanguage.googleapis.com')
       )
    THEN
        RAISE EXCEPTION 'egress_profile_conflict' USING ERRCODE = '22023';
    END IF;
    IF v_operation.egress_profile <> 'legacy_unqualified' THEN
        IF v_operation.egress_profile <> p_profile THEN
            RAISE EXCEPTION 'egress_profile_conflict' USING ERRCODE = '22023';
        END IF;
        RETURN 'bound';
    END IF;
    UPDATE app.egress_operations SET egress_profile = p_profile
     WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_operation_id;
    RETURN 'bound';
END;
$$;

REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text)
TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.record_gsc_import_generation(
    uuid, uuid, uuid, uuid, text, text, jsonb, date, date, text, text,
    jsonb, jsonb, bytea, uuid
) TO signal_crawl_ingest;
