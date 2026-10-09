CREATE TABLE app.autonomy_gate_records (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    operation_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    recipe_release_id uuid NOT NULL,
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    sealed_revision_sha256 bytea NOT NULL CHECK (octet_length(sealed_revision_sha256) = 32),
    input_sha256 bytea NOT NULL CHECK (octet_length(input_sha256) = 32),
    work_type text NOT NULL CHECK (control.valid_standing_work_type(work_type)),
    resource_path text NOT NULL,
    cost_cents bigint NOT NULL CHECK (cost_cents BETWEEN 0 AND 100000000),
    policy_version text NOT NULL CHECK (policy_version = 'autonomy-policy-1.0.0'),
    policy_outcome text NOT NULL CHECK (policy_outcome IN ('eligible','ask_owner','reject')),
    policy_reason text NOT NULL CHECK (policy_reason ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    model_decision_id uuid,
    provider text,
    fallback boolean,
    confidence numeric CHECK (confidence BETWEEN 0 AND 1),
    threshold numeric CHECK (threshold BETWEEN 0 AND 1),
    outcome text NOT NULL CHECK (outcome IN ('ship','ask_owner','reject')),
    reason text NOT NULL CHECK (reason ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    reserved_operation_id uuid,
    decided_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, model_decision_id)
        REFERENCES app.decision_records (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, reserved_operation_id)
        REFERENCES app.autonomy_reservations (tenant_id, site_id, operation_id),
    CHECK ((policy_outcome = 'eligible') = (model_decision_id IS NOT NULL)),
    CHECK ((model_decision_id IS NULL AND provider IS NULL AND fallback IS NULL
            AND confidence IS NULL AND threshold IS NULL)
        OR (model_decision_id = id AND provider IS NOT NULL AND fallback IS NOT NULL
            AND confidence IS NOT NULL AND threshold IS NOT NULL)),
    CHECK ((outcome = 'ship') = (reserved_operation_id IS NOT NULL)),
    CHECK (reserved_operation_id IS NULL OR reserved_operation_id = operation_id)
);
CREATE INDEX autonomy_gate_records_site_decided ON app.autonomy_gate_records
    (tenant_id, site_id, decided_at DESC);
CREATE TRIGGER autonomy_gate_records_immutable BEFORE UPDATE OR DELETE
ON app.autonomy_gate_records FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.autonomy_gate_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomy_gate_records FORCE ROW LEVEL SECURITY;
CREATE POLICY autonomy_gate_records_scope ON app.autonomy_gate_records
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.autonomy_gate_records
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.autonomy_gate_preflight(
    p_tenant_id uuid, p_site_id uuid, p_grant_id uuid, p_generation text,
    p_recipe_release_id uuid, p_work_type text, p_resource_path text, p_cost_cents bigint
) RETURNS TABLE (eligible boolean, reason text, threshold numeric)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_scope record; g app.standing_authorizations%%ROWTYPE;
        v_week date; v_usage app.autonomy_weekly_usage%%ROWTYPE; v_count integer;
BEGIN
    IF session_user != 'signal_workflow' OR p_cost_cents IS NULL
       OR p_cost_cents NOT BETWEEN 0 AND 100000000 THEN
        RETURN QUERY SELECT false, 'invalid_cost'::text, NULL::numeric; RETURN;
    END IF;
    SELECT * INTO v_scope FROM control.standing_grant_eligibility(
        p_tenant_id, p_site_id, p_grant_id, p_generation,
        p_recipe_release_id, p_work_type, p_resource_path);
    IF NOT v_scope.eligible THEN
        RETURN QUERY SELECT false, v_scope.reason, NULL::numeric; RETURN;
    END IF;
    SELECT * INTO g FROM app.standing_authorizations
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_grant_id;
    v_week := date_trunc('week', transaction_timestamp() AT TIME ZONE 'UTC')::date;
    SELECT * INTO v_usage FROM app.autonomy_weekly_usage
    WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND week_start = v_week;
    v_count := COALESCE((v_usage.per_type_counts ->> p_work_type)::integer, 0);
    IF COALESCE(v_usage.total_count, 0) >= g.weekly_total_cap
       OR v_count >= (g.weekly_volume_caps ->> p_work_type)::integer
       OR COALESCE(v_usage.spend_cents, 0) + p_cost_cents > g.weekly_spend_cents THEN
        RETURN QUERY SELECT false, 'weekly_cap_reached'::text, NULL::numeric; RETURN;
    END IF;
    RETURN QUERY SELECT true, 'eligible'::text, v_scope.threshold;
END $$;
REVOKE ALL ON FUNCTION control.autonomy_gate_preflight(
    uuid,uuid,uuid,text,uuid,text,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.autonomy_gate_preflight(
    uuid,uuid,uuid,text,uuid,text,text,bigint) TO signal_workflow;

CREATE FUNCTION control.finalize_autonomy_gate(
    p_tenant_id uuid, p_site_id uuid, p_decision_id uuid, p_operation_id uuid,
    p_grant_id uuid, p_generation text, p_recipe_release_id uuid,
    p_revision_sha256 bytea, p_input_sha256 bytea, p_work_type text,
    p_resource_path text, p_cost_cents bigint, p_policy_outcome text,
    p_policy_reason text
) RETURNS TABLE (gate_outcome text, gate_reason text, reserved boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_model app.decision_records%%ROWTYPE; v_scope record;
        v_outcome text; v_reason text; v_reservation text; v_reserved uuid;
BEGIN
    IF session_user != 'signal_workflow' OR p_tenant_id IS NULL OR p_site_id IS NULL
       OR p_decision_id IS NULL OR p_operation_id IS NULL OR p_grant_id IS NULL
       OR p_recipe_release_id IS NULL OR p_generation IS NULL
       OR p_generation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR octet_length(p_revision_sha256) != 32 OR octet_length(p_input_sha256) != 32
       OR NOT control.valid_standing_work_type(p_work_type)
       OR p_resource_path IS NULL OR length(p_resource_path) NOT BETWEEN 1 AND 1024
       OR p_resource_path !~ '^/[A-Za-z0-9_./-]*$'
       OR p_resource_path LIKE '%%..%%' OR p_resource_path LIKE '%%//%%'
       OR p_cost_cents NOT BETWEEN 0 AND 100000000
       OR p_policy_outcome NOT IN ('eligible','ask_owner','reject')
       OR p_policy_reason !~ '^[A-Z][A-Z0-9_]{0,127}$' THEN
        RETURN QUERY SELECT 'reject'::text, 'INVALID_GATE_INPUT'::text, false; RETURN;
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    IF EXISTS (SELECT 1 FROM app.autonomy_gate_records WHERE id = p_decision_id) THEN
        RETURN QUERY SELECT 'ask_owner'::text, 'HISTORICAL_GATE_ONLY'::text, false; RETURN;
    END IF;
    IF p_policy_outcome != 'eligible' THEN
        v_outcome := p_policy_outcome;
        v_reason := p_policy_reason;
    ELSE
        SELECT * INTO v_model FROM app.decision_records
        WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_decision_id;
        IF NOT FOUND OR v_model.purpose != 'autonomy_gate'
           OR v_model.input_sha256 != p_input_sha256 THEN
            RETURN QUERY SELECT 'reject'::text, 'MODEL_EVIDENCE_UNAVAILABLE'::text, false;
            RETURN;
        END IF;
        SELECT * INTO v_scope FROM control.standing_grant_eligibility(
            p_tenant_id, p_site_id, p_grant_id, p_generation,
            p_recipe_release_id, p_work_type, p_resource_path);
        IF NOT v_scope.eligible THEN
            v_outcome := 'ask_owner'; v_reason := 'AUTHORITY_CHANGED';
        ELSIF v_model.threshold != v_scope.threshold
           OR v_model.policy_ceiling != 'ship' THEN
            v_outcome := 'ask_owner'; v_reason := 'POLICY_MISMATCH';
        ELSIF v_model.outcome = 'reject' THEN
            v_outcome := 'reject'; v_reason := 'MODEL_REJECTED';
        ELSIF v_model.outcome != 'ship' OR v_model.provider != 'typesafe'
           OR v_model.fallback OR v_model.confidence < v_scope.threshold THEN
            v_outcome := 'ask_owner'; v_reason := 'OWNER_REVIEW_REQUIRED';
        ELSIF v_model.answer->'risk_class'->>'type' IS DISTINCT FROM 'choice'
           OR (v_model.answer->'risk_class'->>'choice' IS DISTINCT FROM 'low'
               AND v_model.answer->'risk_class'->>'choice' IS DISTINCT FROM 'moderate') THEN
            v_outcome := 'ask_owner'; v_reason := 'MODEL_RISK_REVIEW_REQUIRED';
        ELSE
            v_reservation := control.reserve_standing_budget(
                p_tenant_id, p_site_id, p_grant_id, p_generation,
                p_recipe_release_id, p_work_type, p_resource_path,
                p_operation_id, p_revision_sha256, p_cost_cents);
            IF v_reservation = 'reserved' THEN
                v_outcome := 'ship'; v_reason := 'JEV_SHIP_AND_BUDGET_RESERVED';
                v_reserved := p_operation_id;
            ELSE
                v_outcome := 'ask_owner'; v_reason := 'BUDGET_OR_AUTHORITY_CHANGED';
            END IF;
        END IF;
    END IF;
    INSERT INTO app.autonomy_gate_records (
        tenant_id, site_id, id, operation_id, grant_id, recipe_release_id,
        recovery_generation, sealed_revision_sha256, input_sha256,
        work_type, resource_path, cost_cents, policy_version, policy_outcome,
        policy_reason, model_decision_id, provider, fallback, confidence,
        threshold, outcome, reason, reserved_operation_id)
    VALUES (p_tenant_id, p_site_id, p_decision_id, p_operation_id, p_grant_id,
        p_recipe_release_id, p_generation, p_revision_sha256, p_input_sha256,
        p_work_type, p_resource_path, p_cost_cents, 'autonomy-policy-1.0.0',
        p_policy_outcome, p_policy_reason,
        CASE WHEN p_policy_outcome = 'eligible' THEN p_decision_id ELSE NULL END,
        CASE WHEN p_policy_outcome = 'eligible' THEN v_model.provider ELSE NULL END,
        CASE WHEN p_policy_outcome = 'eligible' THEN v_model.fallback ELSE NULL END,
        CASE WHEN p_policy_outcome = 'eligible' THEN v_model.confidence ELSE NULL END,
        CASE WHEN p_policy_outcome = 'eligible' THEN v_model.threshold ELSE NULL END,
        v_outcome, v_reason, v_reserved);
    RETURN QUERY SELECT v_outcome, v_reason, v_reserved IS NOT NULL;
END $$;
REVOKE ALL ON FUNCTION control.finalize_autonomy_gate(
    uuid,uuid,uuid,uuid,uuid,text,uuid,bytea,bytea,text,text,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.finalize_autonomy_gate(
    uuid,uuid,uuid,uuid,uuid,text,uuid,bytea,bytea,text,text,bigint,text,text) TO signal_workflow;
