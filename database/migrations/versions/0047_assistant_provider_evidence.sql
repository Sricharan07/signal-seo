CREATE TABLE app.assistant_provider_evidence (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    provider text NOT NULL CHECK (provider IN ('openai', 'perplexity', 'gemini')),
    model_requested text NOT NULL,
    model_reported text NOT NULL,
    response_id text NOT NULL,
    request_body_sha256 bytea NOT NULL CHECK (octet_length(request_body_sha256) = 32),
    response_sha256 bytea NOT NULL CHECK (octet_length(response_sha256) = 32),
    response jsonb NOT NULL CHECK (jsonb_typeof(response) = 'object'),
    egress_operation_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, egress_operation_id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, egress_operation_id)
        REFERENCES app.egress_operations (tenant_id, site_id, id),
    CHECK ((provider = 'openai' AND model_requested = 'gpt-4.1-mini')
        OR (provider = 'perplexity' AND model_requested = 'fast')
        OR (provider = 'gemini' AND model_requested = 'gemini-2.5-flash')),
    CHECK (model_reported ~ '^[A-Za-z0-9_./:-]{1,128}$'),
    CHECK (response_id ~ '^[A-Za-z0-9_./:-]{1,128}$'),
    CHECK (octet_length(response::text) <= 131072)
);
ALTER TABLE app.assistant_provider_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.assistant_provider_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY assistant_provider_evidence_scope ON app.assistant_provider_evidence
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE TRIGGER assistant_provider_evidence_immutable
BEFORE UPDATE OR DELETE ON app.assistant_provider_evidence
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text)
RENAME TO bind_pre_assistant_egress_profile;
REVOKE ALL ON FUNCTION control.bind_pre_assistant_egress_profile(uuid, uuid, uuid, bytea, text)
FROM signal_crawl_admission;

CREATE FUNCTION control.bind_shared_egress_profile(
    p_tenant_id uuid, p_site_id uuid, p_operation_id uuid,
    p_request_sha256 bytea, p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF p_profile IN ('openai_assistant', 'perplexity_assistant', 'gemini_assistant') THEN
        RAISE EXCEPTION 'assistant_egress_profile_requires_model_purpose' USING ERRCODE = '22023';
    END IF;
    RETURN control.bind_pre_assistant_egress_profile(
        p_tenant_id, p_site_id, p_operation_id, p_request_sha256, p_profile
    );
END;
$$;

REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text)
TO signal_crawl_admission;

CREATE FUNCTION control.bind_assistant_egress_profile(
    p_tenant_id uuid, p_site_id uuid, p_operation_id uuid,
    p_request_sha256 bytea, p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.egress_operations%%ROWTYPE; v_endpoint text;
BEGIN
    v_endpoint := CASE p_profile
        WHEN 'openai_assistant' THEN 'https://api.openai.com/v1/responses'
        WHEN 'perplexity_assistant' THEN 'https://api.perplexity.ai/v1/agent'
        WHEN 'gemini_assistant' THEN
            'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'
    END;
    IF v_endpoint IS NULL OR p_tenant_id IS NULL OR p_site_id IS NULL
       OR p_operation_id IS NULL OR octet_length(p_request_sha256) IS DISTINCT FROM 32
    THEN RAISE EXCEPTION 'invalid_assistant_egress_profile' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_operation FROM app.egress_operations
     WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_operation_id
     FOR UPDATE;
    IF NOT FOUND OR v_operation.state <> 'dispatched'
       OR v_operation.request_sha256 IS DISTINCT FROM p_request_sha256
       OR v_operation.purpose <> 'model' OR v_operation.method <> 'POST'
       OR NOT v_operation.credentialed OR v_operation.request_url <> v_endpoint
    THEN RAISE EXCEPTION 'assistant_egress_profile_conflict' USING ERRCODE = '22023'; END IF;
    IF v_operation.egress_profile <> 'legacy_unqualified' THEN
        IF v_operation.egress_profile <> p_profile THEN
            RAISE EXCEPTION 'assistant_egress_profile_conflict' USING ERRCODE = '22023';
        END IF;
        RETURN 'bound';
    END IF;
    UPDATE app.egress_operations SET egress_profile = p_profile
     WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_operation_id;
    RETURN 'bound';
END;
$$;

REVOKE ALL ON FUNCTION control.bind_assistant_egress_profile(uuid, uuid, uuid, bytea, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_assistant_egress_profile(uuid, uuid, uuid, bytea, text)
TO signal_crawl_admission;

CREATE FUNCTION control.record_assistant_provider_evidence(
    p_tenant_id uuid, p_site_id uuid, p_id uuid, p_provider text,
    p_model_requested text, p_model_reported text, p_response_id text,
    p_request_body_sha256 bytea, p_response_sha256 bytea,
    p_response jsonb, p_egress_operation_id uuid
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_origin record; v_egress record; v_endpoint text;
BEGIN
    IF p_id IS NULL OR p_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_provider NOT IN ('openai', 'perplexity', 'gemini')
       OR p_model_requested IS DISTINCT FROM (CASE p_provider
            WHEN 'openai' THEN 'gpt-4.1-mini'
            WHEN 'perplexity' THEN 'fast'
            ELSE 'gemini-2.5-flash' END)
       OR p_model_reported !~ '^[A-Za-z0-9_./:-]{1,128}$'
       OR p_response_id !~ '^[A-Za-z0-9_./:-]{1,128}$'
       OR octet_length(p_request_body_sha256) IS DISTINCT FROM 32
       OR octet_length(p_response_sha256) IS DISTINCT FROM 32
       OR jsonb_typeof(p_response) IS DISTINCT FROM 'object'
       OR octet_length(p_response::text) > 131072
    THEN RAISE EXCEPTION 'invalid_assistant_evidence' USING ERRCODE = '22023'; END IF;
    IF p_provider = 'gemini' THEN
        IF p_response->>'modelVersion' IS DISTINCT FROM p_model_reported
           OR p_response->>'responseId' IS DISTINCT FROM p_response_id
           OR jsonb_typeof(p_response->'candidates') IS DISTINCT FROM 'array'
        THEN RAISE EXCEPTION 'invalid_assistant_response' USING ERRCODE = '22023'; END IF;
    ELSE
        IF p_response->>'model' IS DISTINCT FROM p_model_reported
           OR p_response->>'id' IS DISTINCT FROM p_response_id
           OR p_response->>'status' IS DISTINCT FROM 'completed'
           OR jsonb_typeof(p_response->'output') IS DISTINCT FROM 'array'
        THEN RAISE EXCEPTION 'invalid_assistant_response' USING ERRCODE = '22023'; END IF;
    END IF;
    v_endpoint := CASE p_provider
        WHEN 'openai' THEN 'https://api.openai.com/v1/responses'
        WHEN 'perplexity' THEN 'https://api.perplexity.ai/v1/agent'
        ELSE 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'
    END;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_origin FROM control.verified_site_origin(p_tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN 'origin_unavailable'; END IF;
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'model'
       AND egress.egress_profile = p_provider || '_assistant'
       AND egress.method = 'POST' AND egress.state = 'observed'
       AND egress.http_status = 200 AND egress.credentialed
       AND egress.request_url = v_endpoint
       AND egress.request_body_sha256 = p_request_body_sha256
       AND egress.response_sha256 = p_response_sha256;
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    INSERT INTO app.assistant_provider_evidence (
        tenant_id, site_id, id, provider, model_requested, model_reported,
        response_id, request_body_sha256, response_sha256, response, egress_operation_id
    ) VALUES (
        p_tenant_id, p_site_id, p_id, p_provider, p_model_requested, p_model_reported,
        p_response_id, p_request_body_sha256, p_response_sha256, p_response,
        p_egress_operation_id
    );
    RETURN 'recorded';
END;
$$;

REVOKE ALL ON app.assistant_provider_evidence
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;
REVOKE ALL ON FUNCTION control.record_assistant_provider_evidence(
    uuid, uuid, uuid, text, text, text, text, bytea, bytea, jsonb, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_assistant_provider_evidence(
    uuid, uuid, uuid, text, text, text, text, bytea, bytea, jsonb, uuid
) TO signal_crawl_ingest;
