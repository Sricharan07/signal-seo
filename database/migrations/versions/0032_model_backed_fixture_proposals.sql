CREATE TABLE app.agent_runs (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    role_key text NOT NULL CHECK (role_key = 'content_strategy'),
    release_key text NOT NULL CHECK (
        release_key = 'local-gpt-5.6-luna-metadata-v1'
    ),
    source_finding_id uuid NOT NULL,
    source_evidence_id uuid NOT NULL,
    prompt_sha256 bytea NOT NULL CHECK (octet_length(prompt_sha256) = 32),
    input_sha256 bytea NOT NULL CHECK (octet_length(input_sha256) = 32),
    attempt_number integer NOT NULL CHECK (attempt_number BETWEEN 1 AND 3),
    status text NOT NULL CHECK (status IN ('requested', 'completed', 'failed', 'unknown')),
    failure_code text CHECK (
        failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,127}$'
    ),
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (
        tenant_id, site_id, source_evidence_id, release_key, attempt_number
    ),
    FOREIGN KEY (tenant_id, site_id, source_finding_id)
        REFERENCES app.findings (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, source_evidence_id)
        REFERENCES app.evidence_records (tenant_id, site_id, id),
    CHECK (
        (status = 'requested' AND failure_code IS NULL AND completed_at IS NULL)
        OR (status = 'completed' AND failure_code IS NULL AND completed_at IS NOT NULL)
        OR (status IN ('failed', 'unknown')
            AND failure_code IS NOT NULL AND completed_at IS NOT NULL)
    )
);
CREATE INDEX agent_runs_source_status
ON app.agent_runs (
    tenant_id, site_id, source_evidence_id, release_key, status, attempt_number DESC
);

CREATE TABLE app.model_calls (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    agent_run_id uuid NOT NULL,
    call_number integer NOT NULL CHECK (call_number = 1),
    provider text NOT NULL CHECK (provider = 'openai'),
    model_requested text NOT NULL CHECK (model_requested = 'gpt-5.6-luna'),
    status text NOT NULL CHECK (status IN ('requested', 'completed', 'failed', 'unknown')),
    provider_response_id text CHECK (
        provider_response_id IS NULL
        OR provider_response_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$'
    ),
    model_reported text CHECK (
        model_reported IS NULL
        OR model_reported ~ '^gpt-5[.]6-luna[A-Za-z0-9._:-]{0,96}$'
    ),
    output jsonb CHECK (output IS NULL OR jsonb_typeof(output) = 'object'),
    output_canonical bytea CHECK (
        output_canonical IS NULL OR octet_length(output_canonical) BETWEEN 1 AND 2048
    ),
    output_sha256 bytea CHECK (
        output_sha256 IS NULL OR octet_length(output_sha256) = 32
    ),
    input_tokens bigint CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens IS NULL OR output_tokens >= 0),
    cached_input_tokens bigint CHECK (
        cached_input_tokens IS NULL OR cached_input_tokens >= 0
    ),
    total_tokens bigint CHECK (total_tokens IS NULL OR total_tokens >= 0),
    error_code text CHECK (
        error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,127}$'
    ),
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, agent_run_id, call_number),
    FOREIGN KEY (tenant_id, site_id, agent_run_id)
        REFERENCES app.agent_runs (tenant_id, site_id, id),
    CHECK (
        (status = 'requested'
            AND provider_response_id IS NULL AND model_reported IS NULL
            AND output IS NULL AND output_canonical IS NULL AND output_sha256 IS NULL
            AND input_tokens IS NULL AND output_tokens IS NULL
            AND cached_input_tokens IS NULL AND total_tokens IS NULL
            AND error_code IS NULL AND completed_at IS NULL)
        OR (status = 'completed'
            AND provider_response_id IS NOT NULL AND model_reported IS NOT NULL
            AND output IS NOT NULL AND output_canonical IS NOT NULL
            AND output_sha256 IS NOT NULL AND input_tokens IS NOT NULL
            AND output_tokens IS NOT NULL AND cached_input_tokens IS NOT NULL
            AND total_tokens IS NOT NULL AND error_code IS NULL
            AND completed_at IS NOT NULL)
        OR (status IN ('failed', 'unknown')
            AND provider_response_id IS NULL AND model_reported IS NULL
            AND output IS NULL AND output_canonical IS NULL AND output_sha256 IS NULL
            AND input_tokens IS NULL AND output_tokens IS NULL
            AND cached_input_tokens IS NULL AND total_tokens IS NULL
            AND error_code IS NOT NULL AND completed_at IS NOT NULL)
    ),
    CHECK (
        status <> 'completed'
        OR (
            total_tokens >= input_tokens + output_tokens
            AND cached_input_tokens <= input_tokens
        )
    )
);
CREATE INDEX model_calls_run
ON app.model_calls (tenant_id, site_id, agent_run_id, call_number);

CREATE FUNCTION app.guard_agent_run_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent_run_deletion_prohibited' USING ERRCODE = '55000';
    END IF;
    IF OLD.status <> 'requested'
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.site_id IS DISTINCT FROM OLD.site_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.role_key IS DISTINCT FROM OLD.role_key
       OR NEW.release_key IS DISTINCT FROM OLD.release_key
       OR NEW.source_finding_id IS DISTINCT FROM OLD.source_finding_id
       OR NEW.source_evidence_id IS DISTINCT FROM OLD.source_evidence_id
       OR NEW.prompt_sha256 IS DISTINCT FROM OLD.prompt_sha256
       OR NEW.input_sha256 IS DISTINCT FROM OLD.input_sha256
       OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.status NOT IN ('completed', 'failed', 'unknown')
    THEN
        RAISE EXCEPTION 'agent_run_mutation_prohibited' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION app.guard_model_call_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'model_call_deletion_prohibited' USING ERRCODE = '55000';
    END IF;
    IF OLD.status <> 'requested'
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.site_id IS DISTINCT FROM OLD.site_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
       OR NEW.call_number IS DISTINCT FROM OLD.call_number
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.model_requested IS DISTINCT FROM OLD.model_requested
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.status NOT IN ('completed', 'failed', 'unknown')
    THEN
        RAISE EXCEPTION 'model_call_mutation_prohibited' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER agent_runs_guarded
BEFORE UPDATE OR DELETE ON app.agent_runs
FOR EACH ROW EXECUTE FUNCTION app.guard_agent_run_mutation();
CREATE TRIGGER model_calls_guarded
BEFORE UPDATE OR DELETE ON app.model_calls
FOR EACH ROW EXECUTE FUNCTION app.guard_model_call_mutation();

ALTER TABLE app.agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.agent_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runs_scope ON app.agent_runs
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.model_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.model_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY model_calls_scope ON app.model_calls
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.proposal_revisions
    DROP CONSTRAINT proposal_revisions_source_evidence_key,
    ADD COLUMN producer_kind text NOT NULL DEFAULT 'local_deterministic'
        CHECK (producer_kind IN ('local_deterministic', 'openai_model')),
    ADD COLUMN model_call_id uuid,
    ADD CONSTRAINT proposal_revisions_source_evidence_key UNIQUE (
        tenant_id, site_id, proposal_id, source_evidence_id, producer_kind
    ),
    ADD CONSTRAINT proposal_revisions_model_call_fk FOREIGN KEY (
        tenant_id, site_id, model_call_id
    ) REFERENCES app.model_calls (tenant_id, site_id, id),
    ADD CONSTRAINT proposal_revisions_producer_check CHECK (
        (producer_kind = 'local_deterministic' AND model_call_id IS NULL)
        OR (producer_kind = 'openai_model' AND model_call_id IS NOT NULL)
    );

ALTER TABLE app.approval_requests
    DROP CONSTRAINT approval_requests_requested_authority_check,
    ADD CONSTRAINT approval_requests_requested_authority_check CHECK (
        requested_authority IN (
            'accept_local_fixture_draft', 'accept_model_fixture_draft'
        )
    );

CREATE FUNCTION control.begin_authenticated_fixture_model_run(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_agent_run_id uuid,
    p_model_call_id uuid,
    p_expected_finding_id uuid,
    p_expected_evidence_id uuid,
    p_expected_command_id uuid,
    p_prompt_sha256 bytea,
    p_input_sha256 bytea
)
RETURNS TABLE (
    agent_run_id uuid,
    model_call_id uuid,
    attempt_number integer,
    finding_id uuid,
    evidence_id uuid,
    command_id uuid,
    manifest_id uuid,
    resource_locator text,
    confidence_class text,
    evidence_observed_at timestamptz,
    run_status text,
    reused boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_source record;
    v_existing record;
    v_attempt integer;
    v_now timestamptz := transaction_timestamp();
    v_expected_prompt constant bytea := decode(
        '6c50fef0b4d7c0f096b34a2cd7f52de346e205bae3d788a7f25a7ddce73746e0',
        'hex'
    );
BEGIN
    IF p_agent_run_id IS NULL OR p_model_call_id IS NULL
       OR p_agent_run_id = p_model_call_id
       OR p_expected_finding_id IS NULL OR p_expected_evidence_id IS NULL
       OR p_expected_command_id IS NULL
       OR p_prompt_sha256 IS DISTINCT FROM v_expected_prompt
       OR p_input_sha256 IS NULL OR octet_length(p_input_sha256) <> 32
    THEN
        RAISE EXCEPTION 'invalid_fixture_model_run_input' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash, p_requested_site_id, p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::text, NULL::boolean, v_authority.outcome;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::text, NULL::boolean,
            'approval_permission_denied'::text;
        RETURN;
    END IF;

    SELECT finding.id AS finding_id, finding.latest_command_id,
           finding.resource_locator, finding.confidence_class,
           evidence.id AS evidence_id, evidence.manifest_id, evidence.observed_at
      INTO v_source
      FROM app.findings AS finding
      JOIN LATERAL (
          SELECT observed.id, observed.manifest_id, observed.observed_at
            FROM app.finding_evidence AS link
            JOIN app.evidence_records AS observed
              ON observed.tenant_id = link.tenant_id
             AND observed.site_id = link.site_id
             AND observed.id = link.evidence_id
           WHERE link.tenant_id = finding.tenant_id
             AND link.site_id = finding.site_id
             AND link.finding_id = finding.id
             AND link.relation = 'supports'
           ORDER BY observed.observed_at DESC, observed.id DESC
           LIMIT 1
      ) AS evidence ON true
     WHERE finding.tenant_id = v_authority.tenant_id
       AND finding.site_id = p_requested_site_id
       AND finding.finding_key = 'metadata.meta_description.missing'
       AND finding.status = 'open'
       AND finding.resource_locator = '/fixture/missing-meta-description'
       AND finding.id = p_expected_finding_id
       AND finding.latest_command_id = p_expected_command_id
       AND evidence.id = p_expected_evidence_id
     ORDER BY finding.last_seen_at DESC, finding.id
     LIMIT 1
     FOR UPDATE OF finding;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::uuid,
            NULL::uuid, NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::timestamptz, NULL::text, NULL::boolean,
            'finding_not_ready'::text;
        RETURN;
    END IF;

    SELECT run.id, call.id AS call_id, run.attempt_number, run.status,
           run.input_sha256
      INTO v_existing
      FROM app.agent_runs AS run
      JOIN app.model_calls AS call
        ON call.tenant_id = run.tenant_id
       AND call.site_id = run.site_id
       AND call.agent_run_id = run.id
       AND call.call_number = 1
     WHERE run.tenant_id = v_authority.tenant_id
       AND run.site_id = p_requested_site_id
       AND run.source_evidence_id = v_source.evidence_id
       AND run.release_key = 'local-gpt-5.6-luna-metadata-v1'
       AND run.status IN ('completed', 'requested', 'unknown')
     ORDER BY CASE run.status
                  WHEN 'completed' THEN 1 WHEN 'unknown' THEN 2 ELSE 3
              END,
              run.attempt_number DESC
     LIMIT 1;
    IF FOUND THEN
        IF v_existing.input_sha256 IS DISTINCT FROM p_input_sha256 THEN
            RETURN QUERY SELECT v_existing.id, v_existing.call_id,
                v_existing.attempt_number, v_source.finding_id,
                v_source.evidence_id, v_source.latest_command_id,
                v_source.manifest_id, v_source.resource_locator,
                v_source.confidence_class, v_source.observed_at,
                v_existing.status, true, 'input_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.call_id,
            v_existing.attempt_number, v_source.finding_id, v_source.evidence_id,
            v_source.latest_command_id, v_source.manifest_id,
            v_source.resource_locator, v_source.confidence_class,
            v_source.observed_at, v_existing.status, true,
            CASE v_existing.status
                WHEN 'completed' THEN 'completed'
                WHEN 'unknown' THEN 'outcome_unknown'
                ELSE 'in_progress'
            END;
        RETURN;
    END IF;

    SELECT COALESCE(max(run.attempt_number), 0) + 1
      INTO v_attempt
      FROM app.agent_runs AS run
     WHERE run.tenant_id = v_authority.tenant_id
       AND run.site_id = p_requested_site_id
       AND run.source_evidence_id = v_source.evidence_id
       AND run.release_key = 'local-gpt-5.6-luna-metadata-v1';
    IF v_attempt > 3 THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer,
            v_source.finding_id, v_source.evidence_id, v_source.latest_command_id,
            v_source.manifest_id, v_source.resource_locator,
            v_source.confidence_class, v_source.observed_at, NULL::text,
            NULL::boolean, 'attempt_limit'::text;
        RETURN;
    END IF;

    INSERT INTO app.agent_runs (
        tenant_id, site_id, id, role_key, release_key, source_finding_id,
        source_evidence_id, prompt_sha256, input_sha256, attempt_number,
        status, started_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_agent_run_id,
        'content_strategy', 'local-gpt-5.6-luna-metadata-v1',
        v_source.finding_id, v_source.evidence_id, p_prompt_sha256,
        p_input_sha256, v_attempt, 'requested', v_now
    );
    INSERT INTO app.model_calls (
        tenant_id, site_id, id, agent_run_id, call_number, provider,
        model_requested, status, started_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_model_call_id,
        p_agent_run_id, 1, 'openai', 'gpt-5.6-luna', 'requested', v_now
    );

    RETURN QUERY SELECT p_agent_run_id, p_model_call_id, v_attempt,
        v_source.finding_id, v_source.evidence_id, v_source.latest_command_id,
        v_source.manifest_id, v_source.resource_locator,
        v_source.confidence_class, v_source.observed_at, 'requested'::text,
        false, 'started'::text;
END;
$$;

CREATE FUNCTION control.fail_authenticated_fixture_model_run(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_agent_run_id uuid,
    p_model_call_id uuid,
    p_error_code text,
    p_outcome_unknown boolean
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_status text;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_agent_run_id IS NULL OR p_model_call_id IS NULL
       OR p_agent_run_id = p_model_call_id
       OR p_error_code IS NULL OR p_error_code !~ '^[A-Z][A-Z0-9_]{0,127}$'
       OR p_outcome_unknown IS NULL
    THEN
        RAISE EXCEPTION 'invalid_fixture_model_failure_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash, p_requested_site_id, p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN v_authority.outcome;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN 'approval_permission_denied';
    END IF;
    v_status := CASE WHEN p_outcome_unknown THEN 'unknown' ELSE 'failed' END;
    UPDATE app.model_calls
       SET status = v_status, error_code = p_error_code, completed_at = v_now
     WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_requested_site_id
       AND id = p_model_call_id
       AND agent_run_id = p_agent_run_id
       AND status = 'requested';
    IF NOT FOUND THEN
        RETURN 'run_conflict';
    END IF;
    UPDATE app.agent_runs
       SET status = v_status, failure_code = p_error_code, completed_at = v_now
     WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_requested_site_id
       AND id = p_agent_run_id
       AND status = 'requested';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fixture_model_run_completion_conflict' USING ERRCODE = '55000';
    END IF;
    RETURN v_status;
END;
$$;

CREATE FUNCTION control.complete_authenticated_fixture_model_proposal(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_agent_run_id uuid,
    p_model_call_id uuid,
    p_provider_response_id text,
    p_model_reported text,
    p_output jsonb,
    p_output_canonical bytea,
    p_output_sha256 bytea,
    p_input_tokens bigint,
    p_output_tokens bigint,
    p_cached_input_tokens bigint,
    p_total_tokens bigint,
    p_proposal_id uuid,
    p_revision_id uuid,
    p_approval_request_id uuid,
    p_manifest jsonb,
    p_manifest_canonical bytea,
    p_manifest_sha256 bytea
)
RETURNS TABLE (
    proposal_id uuid,
    revision_id uuid,
    revision_number integer,
    revision_sha256 text,
    manifest jsonb,
    created_by_user_id uuid,
    created_at timestamptz,
    approval_request_id uuid,
    approval_status text,
    approval_requested_at timestamptz,
    approval_expires_at timestamptz,
    decision_id uuid,
    decision text,
    decided_by_user_id uuid,
    decision_channel text,
    decided_at timestamptz,
    reused boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_run record;
    v_expected_manifest jsonb;
    v_proposal_id uuid;
    v_revision record;
    v_request record;
    v_reused boolean := false;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_agent_run_id IS NULL OR p_model_call_id IS NULL
       OR p_provider_response_id IS NULL
       OR p_provider_response_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$'
       OR p_model_reported IS NULL
       OR p_model_reported !~ '^gpt-5[.]6-luna[A-Za-z0-9._:-]{0,96}$'
       OR p_output IS NULL OR jsonb_typeof(p_output) <> 'object'
       OR p_output_canonical IS NULL
       OR octet_length(p_output_canonical) NOT BETWEEN 1 AND 2048
       OR p_output_sha256 IS NULL OR octet_length(p_output_sha256) <> 32
       OR p_input_tokens IS NULL OR p_input_tokens < 0
       OR p_output_tokens IS NULL OR p_output_tokens < 0
       OR p_cached_input_tokens IS NULL OR p_cached_input_tokens < 0
       OR p_cached_input_tokens > p_input_tokens
       OR p_total_tokens IS NULL
       OR p_total_tokens < p_input_tokens + p_output_tokens
       OR p_proposal_id IS NULL OR p_revision_id IS NULL
       OR p_approval_request_id IS NULL
       OR p_manifest IS NULL OR jsonb_typeof(p_manifest) <> 'object'
       OR p_manifest_canonical IS NULL
       OR octet_length(p_manifest_canonical) NOT BETWEEN 1 AND 32768
       OR p_manifest_sha256 IS NULL OR octet_length(p_manifest_sha256) <> 32
    THEN
        RAISE EXCEPTION 'invalid_fixture_model_completion_input' USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_output)) <> 2
       OR jsonb_typeof(p_output->'meta_description') <> 'string'
       OR length(p_output->>'meta_description') NOT BETWEEN 70 AND 160
       OR btrim(p_output->>'meta_description') <> p_output->>'meta_description'
       OR jsonb_typeof(p_output->'rationale') <> 'string'
       OR length(p_output->>'rationale') NOT BETWEEN 1 AND 300
       OR btrim(p_output->>'rationale') <> p_output->>'rationale'
       OR convert_from(p_output_canonical, 'UTF8')::jsonb <> p_output
       OR sha256(p_output_canonical) <> p_output_sha256
    THEN
        RAISE EXCEPTION 'invalid_fixture_model_output' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash, p_requested_site_id, p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            v_authority.outcome;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            'approval_permission_denied'::text;
        RETURN;
    END IF;

    SELECT run.source_finding_id, run.source_evidence_id, run.input_sha256,
           run.prompt_sha256, run.status, finding.latest_command_id,
           finding.resource_locator, finding.confidence_class,
           evidence.observed_at
      INTO v_run
      FROM app.agent_runs AS run
      JOIN app.model_calls AS call
        ON call.tenant_id = run.tenant_id
       AND call.site_id = run.site_id
       AND call.agent_run_id = run.id
       AND call.id = p_model_call_id
       AND call.status = 'requested'
      JOIN app.findings AS finding
        ON finding.tenant_id = run.tenant_id
       AND finding.site_id = run.site_id
       AND finding.id = run.source_finding_id
       AND finding.status = 'open'
       AND finding.finding_key = 'metadata.meta_description.missing'
       AND finding.resource_locator = '/fixture/missing-meta-description'
      JOIN app.evidence_records AS evidence
        ON evidence.tenant_id = run.tenant_id
       AND evidence.site_id = run.site_id
       AND evidence.id = run.source_evidence_id
      JOIN app.finding_evidence AS link
        ON link.tenant_id = run.tenant_id
       AND link.site_id = run.site_id
       AND link.finding_id = run.source_finding_id
       AND link.evidence_id = run.source_evidence_id
       AND link.relation = 'supports'
     WHERE run.tenant_id = v_authority.tenant_id
       AND run.site_id = p_requested_site_id
       AND run.id = p_agent_run_id
       AND run.status = 'requested'
       AND run.release_key = 'local-gpt-5.6-luna-metadata-v1'
       AND NOT EXISTS (
           SELECT 1
             FROM app.finding_evidence AS newer_link
             JOIN app.evidence_records AS newer
               ON newer.tenant_id = newer_link.tenant_id
              AND newer.site_id = newer_link.site_id
              AND newer.id = newer_link.evidence_id
            WHERE newer_link.tenant_id = run.tenant_id
              AND newer_link.site_id = run.site_id
              AND newer_link.finding_id = run.source_finding_id
              AND newer_link.relation = 'supports'
              AND (newer.observed_at, newer.id) > (evidence.observed_at, evidence.id)
       )
     FOR UPDATE OF run, call, finding;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            'run_conflict'::text;
        RETURN;
    END IF;

    v_expected_manifest := jsonb_build_object(
        'schema_version', 1,
        'proposal_kind', 'model_fixture_metadata_draft',
        'site_id', p_requested_site_id::text,
        'finding', jsonb_build_object(
            'id', v_run.source_finding_id::text,
            'evidence_id', v_run.source_evidence_id::text,
            'command_id', v_run.latest_command_id::text,
            'resource_locator', v_run.resource_locator,
            'confidence_class', v_run.confidence_class,
            'observed_at', to_char(
                v_run.observed_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        ),
        'target', jsonb_build_object(
            'resource_locator', '/fixture/missing-meta-description',
            'field', 'meta_description',
            'before', NULL,
            'after', p_output->>'meta_description'
        ),
        'recipe', jsonb_build_object(
            'id', 'title_description_improvement',
            'version', 'local-model-0.1.0',
            'qualification', 'fixture_only'
        ),
        'assessment', jsonb_build_object(
            'impact', 'One synthetic fixture metadata field',
            'risk', 'low',
            'confidence_basis', 'Model draft constrained by deterministic fixture evidence',
            'rationale', p_output->>'rationale',
            'customer_origin_read', false
        ),
        'tests', jsonb_build_array(
            'finding_evidence_bound',
            'model_output_schema_valid',
            'target_field_scoped',
            'external_write_disabled'
        ),
        'role_contributions', jsonb_build_array(
            jsonb_build_object(
                'role', 'technical_seo',
                'release', 'local-deterministic-v1',
                'result', 'finding_supported'
            ),
            jsonb_build_object(
                'role', 'content_strategy',
                'release', 'local-gpt-5.6-luna-metadata-v1',
                'result', 'metadata_draft_prepared'
            ),
            jsonb_build_object(
                'role', 'independent_reviewer',
                'release', 'local-deterministic-v1',
                'result', 'scope_checks_passed'
            ),
            jsonb_build_object(
                'role', 'coordinator',
                'release', 'local-deterministic-v1',
                'result', 'approval_requested'
            )
        ),
        'model', jsonb_build_object(
            'call_id', p_model_call_id::text,
            'release', 'local-gpt-5.6-luna-metadata-v1',
            'model_requested', 'gpt-5.6-luna',
            'model_reported', p_model_reported,
            'provider_response_id', p_provider_response_id,
            'prompt_sha256', encode(v_run.prompt_sha256, 'hex'),
            'input_sha256', encode(v_run.input_sha256, 'hex'),
            'output_sha256', encode(p_output_sha256, 'hex'),
            'store', false,
            'usage', jsonb_build_object(
                'input_tokens', p_input_tokens,
                'output_tokens', p_output_tokens,
                'cached_input_tokens', p_cached_input_tokens,
                'total_tokens', p_total_tokens
            )
        ),
        'authority', jsonb_build_object(
            'approval_class', 'A1',
            'requested', 'accept_model_fixture_draft',
            'external_write', false
        ),
        'cost', jsonb_build_object(
            'currency', 'USD',
            'maximum_minor_units', 1
        ),
        'recovery', jsonb_build_object(
            'mode', 'discard_local_draft',
            'external_state_changed', false,
            'summary', 'Discard the draft; no external state has changed.'
        )
    );
    IF p_manifest <> v_expected_manifest
       OR convert_from(p_manifest_canonical, 'UTF8')::jsonb <> v_expected_manifest
       OR sha256(p_manifest_canonical) <> p_manifest_sha256
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            'manifest_conflict'::text;
        RETURN;
    END IF;

    UPDATE app.model_calls
       SET status = 'completed', provider_response_id = p_provider_response_id,
           model_reported = p_model_reported, output = p_output,
           output_canonical = p_output_canonical, output_sha256 = p_output_sha256,
           input_tokens = p_input_tokens, output_tokens = p_output_tokens,
           cached_input_tokens = p_cached_input_tokens, total_tokens = p_total_tokens,
           completed_at = v_now
     WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_requested_site_id
       AND id = p_model_call_id
       AND agent_run_id = p_agent_run_id
       AND status = 'requested';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fixture_model_call_completion_conflict' USING ERRCODE = '55000';
    END IF;
    UPDATE app.agent_runs
       SET status = 'completed', completed_at = v_now
     WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_requested_site_id
       AND id = p_agent_run_id
       AND status = 'requested';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fixture_model_run_completion_conflict' USING ERRCODE = '55000';
    END IF;

    INSERT INTO app.proposals (
        tenant_id, site_id, id, source_finding_id, created_by_user_id, created_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_proposal_id,
        v_run.source_finding_id, v_authority.user_id, v_now
    ) ON CONFLICT ON CONSTRAINT proposals_source_finding_key DO NOTHING;
    SELECT current_proposal.id INTO v_proposal_id
      FROM app.proposals AS current_proposal
     WHERE current_proposal.tenant_id = v_authority.tenant_id
       AND current_proposal.site_id = p_requested_site_id
       AND current_proposal.source_finding_id = v_run.source_finding_id
     FOR UPDATE;

    INSERT INTO app.proposal_revisions AS created_revision (
        tenant_id, site_id, proposal_id, id, revision_number,
        source_finding_id, source_evidence_id, created_by_user_id,
        manifest, manifest_canonical, manifest_sha256, created_at,
        producer_kind, model_call_id
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, v_proposal_id, p_revision_id,
        COALESCE((
            SELECT max(existing.revision_number)
              FROM app.proposal_revisions AS existing
             WHERE existing.tenant_id = v_authority.tenant_id
               AND existing.site_id = p_requested_site_id
               AND existing.proposal_id = v_proposal_id
        ), 0) + 1,
        v_run.source_finding_id, v_run.source_evidence_id, v_authority.user_id,
        p_manifest, p_manifest_canonical, p_manifest_sha256, v_now,
        'openai_model', p_model_call_id
    )
    ON CONFLICT ON CONSTRAINT proposal_revisions_source_evidence_key DO NOTHING
    RETURNING created_revision.id, created_revision.revision_number,
              created_revision.manifest_sha256, created_revision.manifest,
              created_revision.created_by_user_id, created_revision.created_at
         INTO v_revision;
    IF v_revision.id IS NULL THEN
        SELECT existing.id, existing.revision_number, existing.manifest_sha256,
               existing.manifest, existing.created_by_user_id, existing.created_at
          INTO v_revision
          FROM app.proposal_revisions AS existing
         WHERE existing.tenant_id = v_authority.tenant_id
           AND existing.site_id = p_requested_site_id
           AND existing.proposal_id = v_proposal_id
           AND existing.source_evidence_id = v_run.source_evidence_id
           AND existing.producer_kind = 'openai_model';
        IF NOT FOUND OR v_revision.manifest_sha256 <> p_manifest_sha256
           OR v_revision.manifest <> p_manifest
        THEN
            RAISE EXCEPTION 'fixture_model_revision_conflict' USING ERRCODE = '55000';
        END IF;
        v_reused := true;
    END IF;

    INSERT INTO app.approval_requests AS created_request (
        tenant_id, site_id, id, proposal_revision_id, revision_sha256,
        requested_by_user_id, approval_class, requested_authority,
        request_channel, requested_at, expires_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_approval_request_id,
        v_revision.id, v_revision.manifest_sha256, v_authority.user_id, 'A1',
        'accept_model_fixture_draft', 'signal_chat', v_now,
        v_now + interval '24 hours'
    )
    ON CONFLICT ON CONSTRAINT approval_requests_revision_key DO NOTHING
    RETURNING created_request.id, created_request.requested_at,
              created_request.expires_at INTO v_request;
    IF v_request.id IS NULL THEN
        SELECT existing.id, existing.requested_at, existing.expires_at
          INTO v_request
          FROM app.approval_requests AS existing
         WHERE existing.tenant_id = v_authority.tenant_id
           AND existing.site_id = p_requested_site_id
           AND existing.proposal_revision_id = v_revision.id;
        v_reused := true;
    END IF;

    RETURN QUERY SELECT v_proposal_id, v_revision.id, v_revision.revision_number,
        encode(v_revision.manifest_sha256, 'hex'), v_revision.manifest,
        v_revision.created_by_user_id, v_revision.created_at, v_request.id,
        CASE WHEN v_request.expires_at <= v_now THEN 'expired' ELSE 'pending' END,
        v_request.requested_at, v_request.expires_at, NULL::uuid, NULL::text,
        NULL::uuid, NULL::text, NULL::timestamptz, v_reused, 'prepared'::text;
END;
$$;

REVOKE ALL ON app.agent_runs, app.model_calls
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;
REVOKE ALL ON FUNCTION app.guard_agent_run_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app.guard_model_call_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION control.begin_authenticated_fixture_model_run(
    bytea, uuid, text, uuid, uuid, uuid, uuid, uuid, bytea, bytea
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.fail_authenticated_fixture_model_run(
    bytea, uuid, text, uuid, uuid, text, boolean
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.complete_authenticated_fixture_model_proposal(
    bytea, uuid, text, uuid, uuid, text, text, jsonb, bytea, bytea,
    bigint, bigint, bigint, bigint, uuid, uuid, uuid, jsonb, bytea, bytea
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.begin_authenticated_fixture_model_run(
    bytea, uuid, text, uuid, uuid, uuid, uuid, uuid, bytea, bytea
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.fail_authenticated_fixture_model_run(
    bytea, uuid, text, uuid, uuid, text, boolean
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.complete_authenticated_fixture_model_proposal(
    bytea, uuid, text, uuid, uuid, text, text, jsonb, bytea, bytea,
    bigint, bigint, bigint, bigint, uuid, uuid, uuid, jsonb, bytea, bytea
) TO signal_identity;
