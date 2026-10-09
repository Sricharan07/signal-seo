CREATE TABLE app.decision_records (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    purpose text NOT NULL CHECK (purpose ~ '^[a-z][a-z0-9_.:-]{0,127}$'),
    provider text NOT NULL CHECK (
        provider IN ('typesafe', 'openai_fallback', 'deterministic_fallback')
    ),
    model_requested text NOT NULL CHECK (
        model_requested ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    model_reported text NOT NULL CHECK (
        model_reported ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$'
    ),
    input_sha256 bytea NOT NULL CHECK (octet_length(input_sha256) = 32),
    question_schema_sha256 bytea NOT NULL CHECK (
        octet_length(question_schema_sha256) = 32
    ),
    answer jsonb NOT NULL CHECK (
        jsonb_typeof(answer) = 'object'
        AND jsonb_typeof(answer->'recommendation') = 'object'
        AND octet_length(answer::text) BETWEEN 2 AND 131072
    ),
    probabilities jsonb NOT NULL CHECK (
        jsonb_typeof(probabilities) = 'object'
        AND octet_length(probabilities::text) BETWEEN 2 AND 65536
    ),
    confidence numeric NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    threshold numeric NOT NULL CHECK (threshold BETWEEN 0 AND 1),
    policy_ceiling text NOT NULL CHECK (policy_ceiling IN ('ship', 'ask_owner', 'reject')),
    outcome text NOT NULL CHECK (outcome IN ('ship', 'ask_owner', 'reject')),
    fallback boolean NOT NULL,
    fallback_reason text CHECK (
        fallback_reason IS NULL OR fallback_reason ~ '^[A-Z][A-Z0-9_]{0,127}$'
    ),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    CHECK (
        CASE policy_ceiling
            WHEN 'ship' THEN true
            WHEN 'ask_owner' THEN outcome IN ('ask_owner', 'reject')
            WHEN 'reject' THEN outcome = 'reject'
            ELSE false
        END
    ),
    CHECK (
        (provider = 'typesafe' AND NOT fallback AND fallback_reason IS NULL)
        OR (
            provider IN ('openai_fallback', 'deterministic_fallback')
            AND fallback
            AND fallback_reason IS NOT NULL
        )
    ),
    CHECK (
        (provider = 'typesafe'
            AND probabilities ?& ARRAY['ship', 'ask_owner', 'reject'])
        OR (provider <> 'typesafe' AND probabilities = '{}'::jsonb)
    ),
    CHECK (
        (provider = 'typesafe' AND model_requested = 'jev-latest'
            AND model_reported ~ '^jev-[0-9]+[.][0-9]+[.][0-9]+')
        OR (provider = 'openai_fallback' AND model_requested = 'gpt-5.6-luna'
            AND model_reported ~ '^gpt-5[.]6-luna')
        OR (provider = 'deterministic_fallback'
            AND model_requested = 'deterministic-v1'
            AND model_reported = 'deterministic-v1')
    )
);

CREATE INDEX decision_records_site_created
ON app.decision_records (tenant_id, site_id, created_at DESC, id DESC);

CREATE TRIGGER decision_records_immutable
BEFORE UPDATE OR DELETE ON app.decision_records
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.decision_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.decision_records FORCE ROW LEVEL SECURITY;
CREATE POLICY decision_records_scope ON app.decision_records
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.record_decision_recommendation(
    p_tenant_id uuid,
    p_site_id uuid,
    p_decision_id uuid,
    p_purpose text,
    p_provider text,
    p_model_requested text,
    p_model_reported text,
    p_input_sha256 bytea,
    p_question_schema_sha256 bytea,
    p_answer jsonb,
    p_probabilities jsonb,
    p_confidence numeric,
    p_threshold numeric,
    p_policy_ceiling text,
    p_outcome text,
    p_fallback boolean,
    p_fallback_reason text
)
RETURNS TABLE (
    decision_id uuid,
    created_at timestamptz,
    duplicate boolean,
    record_outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_existing record;
    v_created_at timestamptz;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_decision_id IS NULL
       OR p_decision_id::text !~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_purpose IS NULL OR p_purpose !~ '^[a-z][a-z0-9_.:-]{0,127}$'
       OR p_provider NOT IN ('typesafe', 'openai_fallback', 'deterministic_fallback')
       OR p_model_requested IS NULL
       OR p_model_requested !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       OR p_model_reported IS NULL
       OR p_model_reported !~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$'
       OR p_input_sha256 IS NULL OR octet_length(p_input_sha256) <> 32
       OR p_question_schema_sha256 IS NULL
       OR octet_length(p_question_schema_sha256) <> 32
       OR p_answer IS NULL OR jsonb_typeof(p_answer) <> 'object'
       OR octet_length(p_answer::text) NOT BETWEEN 2 AND 131072
       OR p_probabilities IS NULL OR jsonb_typeof(p_probabilities) <> 'object'
       OR octet_length(p_probabilities::text) NOT BETWEEN 2 AND 65536
       OR p_confidence IS NULL OR p_confidence NOT BETWEEN 0 AND 1
       OR p_threshold IS NULL OR p_threshold NOT BETWEEN 0 AND 1
       OR p_policy_ceiling NOT IN ('ship', 'ask_owner', 'reject')
       OR p_outcome NOT IN ('ship', 'ask_owner', 'reject')
       OR p_fallback IS NULL
       OR (
           p_fallback_reason IS NOT NULL
           AND p_fallback_reason !~ '^[A-Z][A-Z0-9_]{0,127}$'
       )
       OR (p_policy_ceiling = 'ask_owner' AND p_outcome = 'ship')
       OR (p_policy_ceiling = 'reject' AND p_outcome <> 'reject')
       OR (
           p_provider = 'typesafe'
           AND (p_fallback OR p_fallback_reason IS NOT NULL
                OR p_model_requested <> 'jev-latest'
                OR p_model_reported !~ '^jev-[0-9]+[.][0-9]+[.][0-9]+')
       )
       OR (
           p_provider = 'openai_fallback'
           AND (NOT p_fallback OR p_fallback_reason IS NULL
                OR p_model_requested <> 'gpt-5.6-luna'
                OR p_model_reported !~ '^gpt-5[.]6-luna')
       )
       OR (
           p_provider = 'deterministic_fallback'
           AND (NOT p_fallback OR p_fallback_reason IS NULL
                OR p_model_requested <> 'deterministic-v1'
                OR p_model_reported <> 'deterministic-v1')
       )
    THEN
        RAISE EXCEPTION 'invalid_decision_record_input' USING ERRCODE = '22023';
    END IF;

    IF p_provider = 'typesafe' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(p_probabilities)) <> 3
           OR NOT (p_probabilities ?& ARRAY['ship', 'ask_owner', 'reject'])
           OR jsonb_typeof(p_probabilities->'ship') <> 'number'
           OR jsonb_typeof(p_probabilities->'ask_owner') <> 'number'
           OR jsonb_typeof(p_probabilities->'reject') <> 'number'
           OR (p_probabilities->>'ship')::numeric NOT BETWEEN 0 AND 1
           OR (p_probabilities->>'ask_owner')::numeric NOT BETWEEN 0 AND 1
           OR (p_probabilities->>'reject')::numeric NOT BETWEEN 0 AND 1
           OR abs(
               (p_probabilities->>'ship')::numeric
               + (p_probabilities->>'ask_owner')::numeric
               + (p_probabilities->>'reject')::numeric
               - 1
           ) > 0.000001
        THEN
            RAISE EXCEPTION 'invalid_decision_probabilities' USING ERRCODE = '22023';
        END IF;
    ELSIF p_probabilities <> '{}'::jsonb THEN
        RAISE EXCEPTION 'fallback_probabilities_prohibited' USING ERRCODE = '22023';
    END IF;

    IF jsonb_typeof(p_answer->'recommendation') <> 'object' THEN
        RAISE EXCEPTION 'invalid_decision_answer' USING ERRCODE = '22023';
    END IF;

    IF p_provider = 'typesafe' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(p_answer->'recommendation')) <> 4
           OR (p_answer->'recommendation'->>'type') IS DISTINCT FROM 'choice'
           OR (p_answer->'recommendation'->>'choice') NOT IN (
               'ship', 'ask_owner', 'reject'
           )
           OR jsonb_typeof(p_answer->'recommendation'->'probabilities') <> 'object'
           OR (p_answer->'recommendation'->'probabilities') IS DISTINCT FROM p_probabilities
           OR jsonb_typeof(p_answer->'recommendation'->'confidence') <> 'number'
           OR (p_answer->'recommendation'->>'confidence')::numeric
                IS DISTINCT FROM p_confidence
           OR (
               p_answer->'recommendation'->>'choice' = 'ask_owner'
               AND p_outcome = 'ship'
           )
           OR (
               p_answer->'recommendation'->>'choice' = 'reject'
               AND p_outcome <> 'reject'
           )
           OR (
               p_answer->'recommendation'->>'choice' = 'ship'
               AND p_confidence < p_threshold
               AND p_outcome = 'ship'
           )
        THEN
            RAISE EXCEPTION 'invalid_decision_answer' USING ERRCODE = '22023';
        END IF;
    ELSE
        IF (SELECT count(*) FROM jsonb_object_keys(p_answer->'recommendation')) <> 3
           OR (p_answer->'recommendation'->>'type') IS DISTINCT FROM 'fallback'
           OR (p_answer->'recommendation'->>'recommendation') IS DISTINCT FROM p_outcome
           OR (p_answer->'recommendation'->>'recommendation') NOT IN (
               'ask_owner', 'reject'
           )
           OR (p_answer->'recommendation'->>'reason_code') IS NULL
           OR (p_answer->'recommendation'->>'reason_code') !~
                '^[A-Z][A-Z0-9_]{0,127}$'
        THEN
            RAISE EXCEPTION 'invalid_decision_answer' USING ERRCODE = '22023';
        END IF;
    END IF;

    IF p_tenant_id IS DISTINCT FROM app.current_tenant_id()
       OR p_site_id IS DISTINCT FROM app.current_site_id()
    THEN
        RAISE EXCEPTION 'decision_scope_mismatch' USING ERRCODE = '42501';
    END IF;

    PERFORM 1
      FROM app.tenants AS tenant
      JOIN app.sites AS site ON site.tenant_id = tenant.tenant_id
     WHERE tenant.tenant_id = p_tenant_id
       AND tenant.lifecycle = 'active'
       AND site.id = p_site_id
       AND site.state <> 'archived';
    IF NOT FOUND THEN
        RETURN QUERY SELECT p_decision_id, NULL::timestamptz, false,
            'scope_unavailable'::text;
        RETURN;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_decision_id::text, 0));
    SELECT record.* INTO v_existing
      FROM app.decision_records AS record
     WHERE record.tenant_id = p_tenant_id
       AND record.site_id = p_site_id
       AND record.id = p_decision_id;
    IF FOUND THEN
        IF v_existing.purpose IS DISTINCT FROM p_purpose
           OR v_existing.provider IS DISTINCT FROM p_provider
           OR v_existing.model_requested IS DISTINCT FROM p_model_requested
           OR v_existing.model_reported IS DISTINCT FROM p_model_reported
           OR v_existing.input_sha256 IS DISTINCT FROM p_input_sha256
           OR v_existing.question_schema_sha256 IS DISTINCT FROM p_question_schema_sha256
           OR v_existing.answer IS DISTINCT FROM p_answer
           OR v_existing.probabilities IS DISTINCT FROM p_probabilities
           OR v_existing.confidence IS DISTINCT FROM p_confidence
           OR v_existing.threshold IS DISTINCT FROM p_threshold
           OR v_existing.policy_ceiling IS DISTINCT FROM p_policy_ceiling
           OR v_existing.outcome IS DISTINCT FROM p_outcome
           OR v_existing.fallback IS DISTINCT FROM p_fallback
           OR v_existing.fallback_reason IS DISTINCT FROM p_fallback_reason
        THEN
            RETURN QUERY SELECT p_decision_id, v_existing.created_at, false,
                'record_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT p_decision_id, v_existing.created_at, true, 'recorded'::text;
        RETURN;
    END IF;

    INSERT INTO app.decision_records (
        tenant_id, site_id, id, purpose, provider, model_requested, model_reported,
        input_sha256, question_schema_sha256, answer, probabilities, confidence,
        threshold, policy_ceiling, outcome, fallback, fallback_reason
    ) VALUES (
        p_tenant_id, p_site_id, p_decision_id, p_purpose, p_provider,
        p_model_requested, p_model_reported, p_input_sha256,
        p_question_schema_sha256, p_answer, p_probabilities, p_confidence,
        p_threshold, p_policy_ceiling, p_outcome, p_fallback, p_fallback_reason
    )
    RETURNING app.decision_records.created_at INTO v_created_at;

    RETURN QUERY SELECT p_decision_id, v_created_at, false, 'recorded'::text;
END;
$$;

REVOKE ALL ON app.decision_records
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;
REVOKE ALL ON FUNCTION control.record_decision_recommendation(
    uuid, uuid, uuid, text, text, text, text, bytea, bytea, jsonb, jsonb,
    numeric, numeric, text, text, boolean, text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_decision_recommendation(
    uuid, uuid, uuid, text, text, text, text, bytea, bytea, jsonb, jsonb,
    numeric, numeric, text, text, boolean, text
) TO signal_workflow;
