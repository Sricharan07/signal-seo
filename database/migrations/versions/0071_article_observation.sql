-- Immutable canonical bytes remain untouched; observation metadata is derived, not authority.
CREATE VIEW control.pr_delivery_revisions AS
SELECT tenant_id,site_id,id,canonical_manifest,'technical'::text AS candidate_kind FROM app.candidate_recipe_revisions
UNION ALL
SELECT tenant_id,site_id,id,canonical,'content'::text FROM app.content_candidates;
REVOKE ALL ON control.pr_delivery_revisions FROM PUBLIC,signal_api,signal_identity,signal_workflow;

CREATE FUNCTION control.pr_delivery_manifest(p_tenant uuid,p_site uuid,p_revision uuid,p_kind text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c app.content_candidates%%ROWTYPE; origin text; path text;
BEGIN
    IF p_kind<>'owner_editorial' THEN
        RETURN (SELECT convert_from(canonical_manifest,'UTF8')::jsonb FROM app.candidate_recipe_revisions
          WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_revision);
    END IF;
    SELECT * INTO c FROM app.content_candidates WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_revision;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT primary_origin INTO origin FROM app.sites WHERE tenant_id=p_tenant AND id=p_site;
    path:=c.manifest->'changed_files'->0->>'path';
    RETURN c.manifest||jsonb_build_object(
      'result_sha256',c.manifest->'changed_files'->0->>'result_sha256',
      'evidence',jsonb_build_object('site_origin',origin,'page_url',origin||'/'||
        CASE WHEN path='index.html' THEN '' ELSE path END));
END $$;
REVOKE ALL ON FUNCTION control.pr_delivery_manifest(uuid,uuid,uuid,text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.begin_github_delivery_observation(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid,
    p_attempt_id uuid, p_environment text, p_actor_id bigint
) RETURNS TABLE (canonical_manifest bytea, canonical_receipt bytea, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_previous app.github_delivery_attempts%%ROWTYPE; v_sequence integer;
BEGIN
    IF p_attempt_id IS NULL OR p_environment IS NULL OR p_environment !~
       '^[A-Za-z0-9][A-Za-z0-9_. /-]{0,99}$' OR p_actor_id IS NULL OR p_actor_id < 1 THEN
        RAISE EXCEPTION 'invalid_delivery_observation' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.github_delivery_read_authority(
        p_session_hash, p_site_id, p_generation, p_operation_id);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::bytea, NULL::bytea, v_authority.outcome; RETURN;
    END IF;
    PERFORM 1 FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    IF FOUND THEN
        IF v_attempt.operation_id <> p_operation_id OR v_attempt.environment <> p_environment
           OR v_attempt.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::bytea, receipt.canonical_receipt, 'completed'::text
          FROM app.github_delivery_receipts receipt WHERE receipt.tenant_id = v_authority.tenant_id
           AND receipt.site_id = p_site_id AND receipt.attempt_id = p_attempt_id;
        IF NOT FOUND THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'outcome_unknown'::text;
        END IF;
        RETURN;
    END IF;
    SELECT * INTO v_previous FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND operation_id = p_operation_id ORDER BY sequence_number DESC LIMIT 1;
    IF FOUND THEN
        IF v_previous.environment <> p_environment OR v_previous.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        IF v_previous.sequence_number >= 24 THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_budget_exhausted'::text; RETURN;
        END IF;
        IF v_previous.next_observe_at > transaction_timestamp()
           OR (v_previous.state = 'dispatching' AND v_previous.expires_at > transaction_timestamp()) THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_backoff'::text; RETURN;
        END IF;
        UPDATE app.github_delivery_attempts SET state = 'outcome_unknown'
         WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id
           AND id = v_previous.id AND state = 'dispatching';
    END IF;
    v_sequence := coalesce(v_previous.sequence_number, 0) + 1;
    INSERT INTO app.github_delivery_attempts
      (tenant_id, site_id, id, operation_id, sequence_number, environment, deployment_actor_id,
       recovery_generation, membership_epoch, site_epoch, state, expires_at, next_observe_at)
    VALUES (v_authority.tenant_id, p_site_id, p_attempt_id, p_operation_id, v_sequence,
      p_environment, p_actor_id, p_generation, v_authority.membership_epoch, v_authority.site_epoch,
      'dispatching', transaction_timestamp() + interval '300 seconds',
      transaction_timestamp() + make_interval(secs => least(3600, 30 * power(2, least(v_sequence - 1, 7)))::integer));
    RETURN QUERY SELECT revision.canonical_manifest, NULL::bytea, 'dispatching'::text
      FROM app.github_pr_operations operation JOIN control.pr_delivery_revisions revision
      ON revision.tenant_id = operation.tenant_id AND revision.site_id = operation.site_id
        AND revision.id = operation.candidate_revision_id
        AND revision.candidate_kind = CASE WHEN operation.authority_kind='owner_editorial' THEN 'content' ELSE 'technical' END
     WHERE operation.tenant_id = v_authority.tenant_id AND operation.site_id = p_site_id
       AND operation.id = p_operation_id;
END; $$;

CREATE OR REPLACE FUNCTION control.github_delivery_read_permit(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    IF NOT FOUND THEN RETURN 'observation_unavailable'; END IF;
    SELECT * INTO v_authority FROM control.github_delivery_read_authority(
        p_session_hash, p_site_id, p_generation, v_attempt.operation_id);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_attempt.state <> 'dispatching' OR v_attempt.expires_at <= transaction_timestamp()
      OR v_attempt.recovery_generation <> p_generation
      OR v_attempt.membership_epoch <> v_authority.membership_epoch
      OR v_attempt.site_epoch <> v_authority.site_epoch THEN RETURN 'observation_stale'; END IF;
    RETURN 'permitted';
END; $$;

CREATE OR REPLACE FUNCTION control.finish_github_delivery_observation(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid,
    p_canonical_receipt bytea, p_receipt_sha256 bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_operation app.github_pr_operations%%ROWTYPE; v_receipt jsonb; v_manifest jsonb;
        v_existing app.github_delivery_receipts%%ROWTYPE; v_item jsonb; v_egress app.egress_operations%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'observation_unavailable'; END IF;
    SELECT * INTO v_existing FROM app.github_delivery_receipts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND attempt_id = p_attempt_id;
    IF FOUND THEN
        IF v_existing.canonical_receipt = p_canonical_receipt AND v_existing.receipt_sha256 = p_receipt_sha256
          THEN RETURN 'completed'; END IF;
        RETURN 'receipt_conflict';
    END IF;
    IF control.github_delivery_read_permit(p_session_hash, p_site_id, p_generation, p_attempt_id)
        <> 'permitted' THEN RETURN 'observation_stale'; END IF;
    IF p_canonical_receipt IS NULL OR octet_length(p_canonical_receipt) NOT BETWEEN 2 AND 65536
       OR p_receipt_sha256 IS DISTINCT FROM sha256(p_canonical_receipt) THEN
        RETURN 'receipt_invalid';
    END IF;
    SELECT * INTO v_operation FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = v_attempt.operation_id;
    SELECT control.pr_delivery_manifest(v_authority.tenant_id,p_site_id,v_operation.candidate_revision_id,v_operation.authority_kind) INTO v_manifest;
    v_receipt := convert_from(p_canonical_receipt, 'UTF8')::jsonb;
    IF v_receipt->>'schema_version' IS DISTINCT FROM '1'
      OR v_receipt->>'site_id' IS DISTINCT FROM p_site_id::text
      OR v_receipt->>'attempt_id' IS DISTINCT FROM p_attempt_id::text
      OR v_receipt->>'operation_id' IS DISTINCT FROM v_operation.id::text
      OR v_receipt->>'revision_sha256' IS DISTINCT FROM encode(v_operation.revision_sha256, 'hex')
      OR v_receipt->>'environment' IS DISTINCT FROM v_attempt.environment
      OR v_receipt->>'deployment_actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
      OR coalesce(v_receipt->>'outcome', '') NOT IN ('verified', 'not_yet_deployed', 'inconclusive', 'regressed')
      OR coalesce(v_receipt->'provider'->>'stage', '') NOT IN ('pr_opened', 'checks', 'merged', 'deployed')
      OR v_receipt->>'recovery_plan' IS DISTINCT FROM v_manifest->>'recovery_plan'
      OR v_receipt->>'delivery_certified' IS DISTINCT FROM 'false'
      OR jsonb_typeof(v_receipt->'provider_evidence') IS DISTINCT FROM 'array'
      OR jsonb_array_length(v_receipt->'provider_evidence') > 20 THEN RETURN 'receipt_invalid'; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_receipt->'provider_evidence') LOOP
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_item->>'egress_operation_id')::uuid;
        IF NOT FOUND OR v_egress.purpose <> 'connector' OR v_egress.method <> 'GET'
          OR v_egress.egress_profile <> 'github_rest'
          OR v_egress.origin <> 'https://api.github.com' OR v_egress.network_outcome <> 'fetched'
          OR v_egress.request_url IS DISTINCT FROM v_item->>'url'
          OR v_egress.http_status::text IS DISTINCT FROM v_item->>'http_status'
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_item->>'body_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'provider_evidence_invalid'; END IF;
    END LOOP;
    IF v_receipt->'provider'->>'stage' <> 'pr_opened'
       AND jsonb_array_length(v_receipt->'provider_evidence') < 4 THEN RETURN 'provider_evidence_invalid'; END IF;
    IF v_receipt->'provider'->>'stage' = 'deployed' THEN
        IF v_receipt->'provider'->>'merged_tree_sha' IS DISTINCT FROM v_operation.expected_tree_sha
          OR v_receipt->'provider'->'deployment'->>'sha' IS DISTINCT FROM v_receipt->'provider'->>'merged_sha'
          OR v_receipt->'provider'->'deployment'->>'environment' IS DISTINCT FROM v_attempt.environment
          OR v_receipt->'provider'->'deployment'->>'actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
          OR v_receipt->'provider'->'deployment'->>'environment_url' IS NULL
          OR v_receipt->'provider'->'deployment'->>'environment_url' NOT IN
            (v_manifest->'evidence'->>'site_origin', (v_manifest->'evidence'->>'site_origin')||'/',
             v_manifest->'evidence'->>'page_url')
          OR jsonb_array_length(v_receipt->'provider_evidence') < 7 THEN RETURN 'deployment_identity_invalid'; END IF;
    END IF;
    IF v_receipt->>'outcome' = 'verified' THEN
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_receipt->>'live_egress_operation_id')::uuid;
        IF NOT FOUND OR v_receipt->'provider'->>'stage' IS DISTINCT FROM 'deployed'
          OR v_receipt->'live'->>'outcome' IS DISTINCT FROM 'verified'
          OR v_receipt->'live'->>'matched' IS DISTINCT FROM 'true'
          OR v_receipt->'live'->>'fetched_sha256' IS DISTINCT FROM v_manifest->>'result_sha256'
          OR v_egress.purpose <> 'crawl' OR v_egress.method <> 'GET' OR v_egress.credentialed
          OR v_egress.egress_profile <> 'crawl_page'
          OR v_egress.request_url IS DISTINCT FROM v_manifest->'evidence'->>'page_url'
          OR v_egress.state <> 'observed' OR v_egress.network_outcome <> 'fetched' OR v_egress.http_status <> 200
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_receipt->'live'->>'fetched_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'live_evidence_invalid'; END IF;
    END IF;
    INSERT INTO app.github_delivery_receipts (tenant_id, site_id, attempt_id, operation_id,
      canonical_receipt, receipt_sha256) VALUES (v_authority.tenant_id, p_site_id, p_attempt_id,
      v_operation.id, p_canonical_receipt, p_receipt_sha256);
    UPDATE app.github_delivery_attempts SET state = 'completed' WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    RETURN 'completed';
END; $$;
