ALTER TABLE app.agent_runs
    DROP CONSTRAINT agent_runs_release_key_check,
    ADD CONSTRAINT agent_runs_release_key_check CHECK (
        release_key IN (
            'local-gpt-5.6-luna-metadata-v1',
            'verified-gpt-5.6-luna-metadata-v1'
        )
    );

ALTER TABLE app.approval_requests
    DROP CONSTRAINT approval_requests_requested_authority_check,
    ADD CONSTRAINT approval_requests_requested_authority_check CHECK (
        requested_authority IN (
            'accept_local_fixture_draft',
            'accept_model_fixture_draft',
            'accept_verified_homepage_metadata_draft'
        )
    );

CREATE FUNCTION control.begin_authenticated_verified_homepage_model_run(
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
        'f963221dfbac7cc218bead459e6f11ca61d07e41a98ae8fa46d784f5d06b9347',
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
        RAISE EXCEPTION 'invalid_verified_model_run_input' USING ERRCODE = '22023';
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
      JOIN app.finding_evidence AS link
        ON link.tenant_id = finding.tenant_id
       AND link.site_id = finding.site_id
       AND link.finding_id = finding.id
       AND link.relation = 'supports'
      JOIN app.evidence_records AS evidence
        ON evidence.tenant_id = link.tenant_id
       AND evidence.site_id = link.site_id
       AND evidence.id = link.evidence_id
       AND evidence.source_kind = 'verified_origin'
       AND evidence.source_identifier = finding.resource_locator
       AND evidence.command_id = finding.latest_command_id
       AND evidence.facts->'meta_description' = 'null'::jsonb
       AND (
           jsonb_typeof(evidence.facts->'title') = 'string'
           OR jsonb_typeof(evidence.facts->'heading') = 'string'
       )
      JOIN app.page_observation_results AS observation
        ON observation.tenant_id = evidence.tenant_id
       AND observation.site_id = evidence.site_id
       AND observation.intent_id = evidence.observation_intent_id
       AND observation.fetch_outcome = 'observed'
       AND observation.final_url = evidence.source_identifier
       AND observation.meta_description IS NULL
     WHERE finding.tenant_id = v_authority.tenant_id
       AND finding.site_id = p_requested_site_id
       AND finding.finding_key = 'metadata.meta_description.missing'
       AND finding.status = 'open'
       AND finding.resource_locator ~ '^https://[^#[:space:]]+$'
       AND finding.id = p_expected_finding_id
       AND finding.latest_command_id = p_expected_command_id
       AND evidence.id = p_expected_evidence_id
     ORDER BY evidence.observed_at DESC, evidence.id DESC
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
       AND run.release_key = 'verified-gpt-5.6-luna-metadata-v1'
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
       AND run.release_key = 'verified-gpt-5.6-luna-metadata-v1';
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
        'content_strategy', 'verified-gpt-5.6-luna-metadata-v1',
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

CREATE FUNCTION control.fail_authenticated_verified_homepage_model_run(
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
        RAISE EXCEPTION 'invalid_verified_model_failure_input' USING ERRCODE = '22023';
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
    UPDATE app.model_calls AS call
       SET status = v_status, error_code = p_error_code, completed_at = v_now
      FROM app.agent_runs AS run
     WHERE call.tenant_id = v_authority.tenant_id
       AND call.site_id = p_requested_site_id
       AND call.id = p_model_call_id
       AND call.agent_run_id = p_agent_run_id
       AND call.status = 'requested'
       AND run.tenant_id = call.tenant_id
       AND run.site_id = call.site_id
       AND run.id = call.agent_run_id
       AND run.release_key = 'verified-gpt-5.6-luna-metadata-v1';
    IF NOT FOUND THEN
        RETURN 'run_conflict';
    END IF;
    UPDATE app.agent_runs
       SET status = v_status, failure_code = p_error_code, completed_at = v_now
     WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_requested_site_id
       AND id = p_agent_run_id
       AND status = 'requested'
       AND release_key = 'verified-gpt-5.6-luna-metadata-v1';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'verified_model_run_completion_conflict' USING ERRCODE = '55000';
    END IF;
    RETURN v_status;
END;
$$;

CREATE FUNCTION control.complete_authenticated_verified_homepage_model_proposal(
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
        RAISE EXCEPTION 'invalid_verified_model_completion_input' USING ERRCODE = '22023';
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
        RAISE EXCEPTION 'invalid_verified_model_output' USING ERRCODE = '22023';
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
       AND finding.resource_locator ~ '^https://[^#[:space:]]+$'
      JOIN app.evidence_records AS evidence
        ON evidence.tenant_id = run.tenant_id
       AND evidence.site_id = run.site_id
       AND evidence.id = run.source_evidence_id
       AND evidence.source_kind = 'verified_origin'
       AND evidence.source_identifier = finding.resource_locator
       AND evidence.facts->'meta_description' = 'null'::jsonb
      JOIN app.page_observation_results AS observation
        ON observation.tenant_id = evidence.tenant_id
       AND observation.site_id = evidence.site_id
       AND observation.intent_id = evidence.observation_intent_id
       AND observation.fetch_outcome = 'observed'
       AND observation.final_url = finding.resource_locator
       AND observation.meta_description IS NULL
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
       AND run.release_key = 'verified-gpt-5.6-luna-metadata-v1'
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
        'proposal_kind', 'model_verified_homepage_metadata_draft',
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
            'resource_locator', v_run.resource_locator,
            'field', 'meta_description',
            'before', NULL,
            'after', p_output->>'meta_description'
        ),
        'recipe', jsonb_build_object(
            'id', 'title_description_improvement',
            'version', 'verified-homepage-model-1.0.0',
            'qualification', 'verified_homepage_proposal_only'
        ),
        'assessment', jsonb_build_object(
            'impact', 'One owner-verified homepage metadata field',
            'risk', 'low',
            'confidence_basis', 'Model draft constrained by verified homepage metadata',
            'rationale', p_output->>'rationale',
            'customer_origin_read', true
        ),
        'tests', jsonb_build_array(
            'finding_evidence_bound',
            'model_output_schema_valid',
            'verified_homepage_target_scoped',
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
                'release', 'verified-gpt-5.6-luna-metadata-v1',
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
            'release', 'verified-gpt-5.6-luna-metadata-v1',
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
            'requested', 'accept_verified_homepage_metadata_draft',
            'external_write', false
        ),
        'cost', jsonb_build_object(
            'currency', 'USD',
            'maximum_minor_units', 1
        ),
        'recovery', jsonb_build_object(
            'mode', 'discard_local_draft',
            'external_state_changed', false,
            'summary', 'Discard the proposed draft; no external state has changed.'
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
        RAISE EXCEPTION 'verified_model_call_completion_conflict' USING ERRCODE = '55000';
    END IF;
    UPDATE app.agent_runs
       SET status = 'completed', completed_at = v_now
     WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_requested_site_id
       AND id = p_agent_run_id
       AND status = 'requested';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'verified_model_run_completion_conflict' USING ERRCODE = '55000';
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
            RAISE EXCEPTION 'verified_model_revision_conflict' USING ERRCODE = '55000';
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
        'accept_verified_homepage_metadata_draft', 'signal_chat', v_now,
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

REVOKE ALL ON FUNCTION control.begin_authenticated_verified_homepage_model_run(
    bytea, uuid, text, uuid, uuid, uuid, uuid, uuid, bytea, bytea
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.fail_authenticated_verified_homepage_model_run(
    bytea, uuid, text, uuid, uuid, text, boolean
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.complete_authenticated_verified_homepage_model_proposal(
    bytea, uuid, text, uuid, uuid, text, text, jsonb, bytea, bytea,
    bigint, bigint, bigint, bigint, uuid, uuid, uuid, jsonb, bytea, bytea
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.begin_authenticated_verified_homepage_model_run(
    bytea, uuid, text, uuid, uuid, uuid, uuid, uuid, bytea, bytea
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.fail_authenticated_verified_homepage_model_run(
    bytea, uuid, text, uuid, uuid, text, boolean
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.complete_authenticated_verified_homepage_model_proposal(
    bytea, uuid, text, uuid, uuid, text, text, jsonb, bytea, bytea,
    bigint, bigint, bigint, bigint, uuid, uuid, uuid, jsonb, bytea, bytea
) TO signal_identity;
