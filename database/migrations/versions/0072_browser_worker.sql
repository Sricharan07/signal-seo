ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check
CHECK (egress_profile IN (
    'legacy_unqualified','crawl_page','crawl_robots','browser_read','browser_worker_read',
    'github_rest','slack_bot','slack_oauth','telegram_bot','github_repository_write',
    'google_oauth_token','google_oauth_revoke','gsc_api','bing_oauth_token','bing_api',
    'jev','model_json','openai_model','openai_assistant','perplexity_assistant',
    'gemini_assistant','dataforseo','crawl_key_file','indexnow_submit','ga4_admin','ga4_data'
));

CREATE FUNCTION control.bind_browser_read_profile(
    p_tenant uuid, p_site uuid, p_operation uuid, p_request bytea, p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.egress_operations%%ROWTYPE;
BEGIN
    IF p_profile IS DISTINCT FROM 'browser_worker_read' OR octet_length(p_request) <> 32 THEN
        RAISE EXCEPTION 'invalid_browser_profile' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant::text, true);
    PERFORM set_config('signal.site_id', p_site::text, true);
    SELECT * INTO v_operation FROM app.egress_operations
     WHERE tenant_id = p_tenant AND site_id = p_site AND id = p_operation FOR UPDATE;
    IF NOT FOUND OR v_operation.state <> 'dispatched'
       OR v_operation.request_sha256 IS DISTINCT FROM p_request
       OR v_operation.purpose <> 'browser' OR v_operation.method NOT IN ('GET', 'HEAD')
       OR v_operation.credentialed OR v_operation.request_bytes <> 0
       OR v_operation.egress_profile NOT IN ('legacy_unqualified', 'browser_worker_read') THEN
        RAISE EXCEPTION 'browser_profile_conflict' USING ERRCODE = '22023';
    END IF;
    IF v_operation.egress_profile = 'legacy_unqualified' THEN
        UPDATE app.egress_operations SET egress_profile = 'browser_worker_read'
         WHERE tenant_id = p_tenant AND site_id = p_site AND id = p_operation;
    END IF;
    RETURN 'bound';
END;
$$;
REVOKE ALL ON FUNCTION control.bind_browser_read_profile(uuid, uuid, uuid, bytea, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_browser_read_profile(uuid, uuid, uuid, bytea, text)
TO signal_crawl_admission;

CREATE TABLE app.browser_sessions (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    crawl_run_id uuid NOT NULL, purpose text NOT NULL
        CHECK (purpose IN ('render', 'read', 'verify')),
    goal text NOT NULL CHECK (octet_length(goal) BETWEEN 1 AND 1024),
    start_url text NOT NULL CHECK (octet_length(start_url) BETWEEN 1 AND 2048),
    image_digest text NOT NULL CHECK (image_digest ~ '^sha256:[0-9a-f]{64}$'),
    max_steps integer NOT NULL CHECK (max_steps BETWEEN 1 AND 20),
    max_seconds integer NOT NULL CHECK (max_seconds BETWEEN 1 AND 120),
    max_bytes integer NOT NULL CHECK (max_bytes BETWEEN 1024 AND 20971520),
    expected_fragment_sha256 bytea CHECK (octet_length(expected_fragment_sha256) = 32),
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id) REFERENCES app.crawl_runs (tenant_id, site_id, id),
    CHECK ((purpose = 'verify') = (expected_fragment_sha256 IS NOT NULL))
);
CREATE TABLE app.browser_steps (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, session_id uuid NOT NULL,
    sequence integer NOT NULL CHECK (sequence BETWEEN 1 AND 21),
    action text NOT NULL CHECK (action IN ('navigate', 'follow_link', 'scroll',
        'wait_network_idle', 'read_tree', 'screenshot', 'finish')),
    outcome text NOT NULL CHECK (outcome IN ('observed', 'complete', 'incomplete',
        'choice_stopped', 'verified', 'mismatch', 'failed')),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'
        AND octet_length(evidence::text) <= 16384),
    snapshot_artifact_id uuid,
    screenshot_artifact_id uuid,
    decision_id uuid,
    recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (tenant_id, site_id, session_id, sequence),
    FOREIGN KEY (tenant_id, site_id, session_id) REFERENCES app.browser_sessions (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, snapshot_artifact_id) REFERENCES app.artifacts (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, screenshot_artifact_id) REFERENCES app.artifacts (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, decision_id) REFERENCES app.decision_records (tenant_id, site_id, id),
    CHECK ((action = 'finish') = (outcome <> 'observed'))
);
CREATE TABLE app.browser_step_egress (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, session_id uuid NOT NULL,
    sequence integer NOT NULL, operation_id uuid NOT NULL, robots_snapshot_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, site_id, session_id, sequence, operation_id),
    FOREIGN KEY (tenant_id, site_id, session_id, sequence)
        REFERENCES app.browser_steps (tenant_id, site_id, session_id, sequence),
    FOREIGN KEY (tenant_id, site_id, operation_id) REFERENCES app.egress_operations (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, robots_snapshot_id) REFERENCES app.robots_snapshots (tenant_id, site_id, id)
);
ALTER TABLE app.browser_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.browser_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE app.browser_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.browser_steps FORCE ROW LEVEL SECURITY;
ALTER TABLE app.browser_step_egress ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.browser_step_egress FORCE ROW LEVEL SECURITY;
CREATE POLICY browser_sessions_scope ON app.browser_sessions
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY browser_steps_scope ON app.browser_steps
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY browser_step_egress_scope ON app.browser_step_egress
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE TRIGGER browser_sessions_immutable BEFORE UPDATE OR DELETE ON app.browser_sessions
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER browser_steps_immutable BEFORE UPDATE OR DELETE ON app.browser_steps
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER browser_step_egress_immutable BEFORE UPDATE OR DELETE ON app.browser_step_egress
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.open_browser_session(
    p_tenant uuid, p_site uuid, p_id uuid, p_run uuid, p_purpose text, p_goal text,
    p_url text, p_image text, p_steps integer, p_seconds integer, p_bytes integer, p_expected bytea
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF p_tenant IS DISTINCT FROM app.current_tenant_id()
       OR p_site IS DISTINCT FROM app.current_site_id() THEN
        RAISE EXCEPTION 'browser_scope_mismatch' USING ERRCODE = '42501';
    END IF;
    PERFORM 1 FROM app.crawl_runs r JOIN app.sites s
      ON s.tenant_id = r.tenant_id AND s.id = r.site_id
      JOIN app.tenants t ON t.tenant_id = r.tenant_id
     WHERE r.tenant_id = p_tenant AND r.site_id = p_site AND r.id = p_run
       AND r.status = 'running' AND s.state <> 'archived' AND t.lifecycle = 'active';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'browser_authority_unavailable' USING ERRCODE = '55000';
    END IF;
    INSERT INTO app.browser_sessions (tenant_id, site_id, id, crawl_run_id, purpose,
        goal, start_url, image_digest, max_steps, max_seconds, max_bytes, expected_fragment_sha256)
    VALUES (p_tenant, p_site, p_id, p_run, p_purpose, p_goal, p_url, p_image,
        p_steps, p_seconds, p_bytes, p_expected);
END;
$$;

CREATE FUNCTION control.record_browser_step(
    p_tenant uuid, p_site uuid, p_id uuid, p_sequence integer, p_action text,
    p_outcome text, p_evidence jsonb, p_artifacts jsonb, p_decision uuid, p_operations uuid[]
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_session app.browser_sessions%%ROWTYPE;
    v_last app.browser_steps%%ROWTYPE;
    v_operation app.egress_operations%%ROWTYPE;
    v_artifact jsonb;
    v_snapshot uuid;
    v_screenshot uuid;
    v_id uuid;
    v_decision app.decision_records%%ROWTYPE;
BEGIN
    IF p_tenant IS DISTINCT FROM app.current_tenant_id()
       OR p_site IS DISTINCT FROM app.current_site_id() THEN
        RAISE EXCEPTION 'browser_scope_mismatch' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_session FROM app.browser_sessions
     WHERE tenant_id = p_tenant AND site_id = p_site AND id = p_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'browser_session_unavailable' USING ERRCODE = '55000';
    END IF;
    SELECT * INTO v_last FROM app.browser_steps WHERE tenant_id = p_tenant
     AND site_id = p_site AND session_id = p_id ORDER BY sequence DESC LIMIT 1;
    IF p_sequence IS DISTINCT FROM coalesce(v_last.sequence, 0) + 1
       OR v_last.action = 'finish' OR p_sequence > v_session.max_steps + 1
       OR p_action IS NULL OR p_outcome IS NULL OR p_evidence IS NULL
       OR jsonb_typeof(p_evidence) <> 'object'
       OR (p_evidence->>'goal') IS DISTINCT FROM v_session.goal
       OR (p_evidence->>'resulting_url') IS NULL
       OR octet_length(p_evidence->>'resulting_url') NOT BETWEEN 1 AND 2048
       OR (p_evidence->>'element_digest') IS NULL
       OR (p_evidence->>'element_digest') !~ '^[0-9a-f]{64}$'
       OR (p_evidence->>'snapshot_digest') IS NULL
       OR (p_evidence->>'snapshot_digest') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(p_evidence->'bytes') IS DISTINCT FROM 'number'
       OR (p_evidence->>'bytes')::bigint < coalesce((v_last.evidence->>'bytes')::bigint, 0)
       OR (p_evidence->>'bytes')::bigint > v_session.max_bytes
       OR (p_action <> 'finish' AND (p_sequence > v_session.max_steps
           OR statement_timestamp() > v_session.created_at + make_interval(secs => v_session.max_seconds)))
       OR p_artifacts IS NULL OR jsonb_typeof(p_artifacts) <> 'array'
       OR jsonb_array_length(p_artifacts) > 2 OR p_operations IS NULL
       OR cardinality(p_operations) > 64 THEN
        RAISE EXCEPTION 'browser_budget_or_sequence_denied' USING ERRCODE = '22023';
    END IF;
    IF p_decision IS NOT NULL THEN
        SELECT * INTO v_decision FROM app.decision_records
         WHERE tenant_id = p_tenant AND site_id = p_site AND id = p_decision;
        IF NOT FOUND OR v_decision.purpose <> 'browser_link'
           OR (p_action = 'follow_link' AND (
               v_decision.fallback OR v_decision.provider <> 'typesafe'
               OR (v_decision.answer->'element'->>'choice') IS DISTINCT FROM (p_evidence->>'choice')
               OR NOT (p_evidence->'listed_ids' ? (p_evidence->>'choice'))
               OR (v_decision.answer->'element'->>'confidence')::numeric < v_decision.threshold
           )) THEN
            RAISE EXCEPTION 'browser_choice_denied' USING ERRCODE = '22023';
        END IF;
    ELSIF p_action = 'follow_link' THEN
        RAISE EXCEPTION 'browser_choice_required' USING ERRCODE = '22023';
    END IF;
    FOR v_artifact IN SELECT value FROM jsonb_array_elements(p_artifacts) LOOP
        IF (v_artifact->>'media_type') NOT IN ('application/json', 'image/png')
           OR (v_artifact->>'byte_length')::bigint > v_session.max_bytes THEN
            RAISE EXCEPTION 'browser_artifact_invalid' USING ERRCODE = '22023';
        END IF;
        v_id := (v_artifact->>'id')::uuid;
        INSERT INTO app.artifacts (tenant_id, site_id, id, object_key, object_version,
            sha256, byte_length, media_type, encryption_key_ref, created_at, retain_until)
        VALUES (p_tenant, p_site, v_id, v_artifact->>'object_key', v_artifact->>'sha256',
            decode(v_artifact->>'sha256', 'hex'), (v_artifact->>'byte_length')::bigint,
            v_artifact->>'media_type', v_artifact->>'key_ref',
            (v_artifact->>'created_at')::timestamptz,
            (v_artifact->>'created_at')::timestamptz + interval '30 days');
        INSERT INTO app.artifact_attestations (tenant_id, site_id, id, artifact_id,
            check_type, result, verified_hash, verified_at)
        VALUES (p_tenant, p_site, gen_random_uuid(), v_id, 'upload_readback', 'verified',
            decode(v_artifact->>'sha256', 'hex'), statement_timestamp());
        IF v_artifact->>'media_type' = 'application/json' THEN
            IF v_snapshot IS NOT NULL OR (v_artifact->>'sha256') IS DISTINCT FROM
                (p_evidence->>'snapshot_digest') THEN
                RAISE EXCEPTION 'browser_snapshot_digest_mismatch' USING ERRCODE = '22023';
            END IF;
            v_snapshot := v_id;
        ELSE
            IF v_screenshot IS NOT NULL OR p_action <> 'screenshot' THEN
                RAISE EXCEPTION 'browser_screenshot_invalid' USING ERRCODE = '22023';
            END IF;
            v_screenshot := v_id;
        END IF;
    END LOOP;
    IF p_action <> 'finish' AND v_snapshot IS NULL THEN
        RAISE EXCEPTION 'browser_snapshot_required' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.browser_steps VALUES (p_tenant, p_site, p_id, p_sequence, p_action,
        p_outcome, p_evidence, v_snapshot, v_screenshot, p_decision, statement_timestamp());
    FOREACH v_id IN ARRAY p_operations LOOP
        SELECT * INTO v_operation FROM app.egress_operations WHERE tenant_id = p_tenant
         AND site_id = p_site AND id = v_id;
        IF NOT FOUND OR v_operation.crawl_run_id <> v_session.crawl_run_id
           OR v_operation.purpose <> 'browser' OR v_operation.egress_profile <> 'browser_worker_read'
           OR v_operation.state NOT IN ('observed', 'failed')
           OR v_operation.dispatched_at < v_session.created_at THEN
            RAISE EXCEPTION 'browser_egress_reference_denied' USING ERRCODE = '22023';
        END IF;
        INSERT INTO app.browser_step_egress VALUES (p_tenant, p_site, p_id, p_sequence,
            v_id, v_operation.robots_snapshot_id);
    END LOOP;
    IF p_evidence ? 'robots_denials' THEN
        IF jsonb_typeof(p_evidence->'robots_denials') <> 'array'
           OR jsonb_array_length(p_evidence->'robots_denials') > 16 THEN
            RAISE EXCEPTION 'browser_robots_reference_denied' USING ERRCODE = '22023';
        END IF;
        FOR v_id IN SELECT value::uuid FROM jsonb_array_elements_text(p_evidence->'robots_denials') LOOP
            PERFORM 1 FROM app.robots_snapshots WHERE tenant_id = p_tenant
             AND site_id = p_site AND id = v_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'browser_robots_reference_denied' USING ERRCODE = '22023';
            END IF;
        END LOOP;
    END IF;
END;
$$;
REVOKE ALL ON app.browser_sessions, app.browser_steps, app.browser_step_egress
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;
REVOKE ALL ON FUNCTION control.open_browser_session(uuid, uuid, uuid, uuid, text, text,
    text, text, integer, integer, integer, bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_browser_step(uuid, uuid, uuid, integer, text, text,
    jsonb, jsonb, uuid, uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.open_browser_session(uuid, uuid, uuid, uuid, text, text,
    text, text, integer, integer, integer, bytea) TO signal_workflow;
GRANT EXECUTE ON FUNCTION control.record_browser_step(uuid, uuid, uuid, integer, text, text,
    jsonb, jsonb, uuid, uuid[]) TO signal_workflow;
