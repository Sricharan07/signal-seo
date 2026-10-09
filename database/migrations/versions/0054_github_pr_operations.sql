CREATE TABLE app.github_pr_operations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    candidate_revision_id uuid NOT NULL,
    revision_sha256 bytea NOT NULL CHECK (octet_length(revision_sha256) = 32),
    intent_sha256 bytea NOT NULL CHECK (octet_length(intent_sha256) = 32),
    decision_id uuid NOT NULL,
    extension_id uuid NOT NULL,
    binding_id uuid NOT NULL,
    recipe_release_id uuid NOT NULL,
    requested_by_user_id uuid NOT NULL,
    recovery_generation text NOT NULL,
    membership_epoch bigint NOT NULL,
    site_epoch bigint NOT NULL,
    base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
    expected_tree_sha text CHECK (expected_tree_sha ~ '^[0-9a-f]{40}$'),
    expected_commit_sha text CHECK (expected_commit_sha ~ '^[0-9a-f]{40}$'),
    branch_name text NOT NULL CHECK (branch_name ~ '^signal/[0-9a-f]{32}$'),
    step text NOT NULL CHECK (step IN ('tree', 'commit', 'branch', 'pr', 'done')),
    state text NOT NULL CHECK (state IN ('planned', 'dispatching', 'outcome_unknown', 'ready', 'opened', 'blocked')),
    journal_generation uuid,
    journal_position bigint CHECK (journal_position > 0),
    journal_body_hash bytea CHECK (octet_length(journal_body_hash) = 32),
    lease_holder uuid,
    lease_fence bigint NOT NULL DEFAULT 0 CHECK (lease_fence >= 0),
    lease_until timestamptz,
    pr_number bigint CHECK (pr_number > 0),
    pr_url text CHECK (length(pr_url) BETWEEN 1 AND 2048),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, candidate_revision_id),
    UNIQUE (tenant_id, site_id, branch_name),
    FOREIGN KEY (tenant_id, site_id, candidate_revision_id, revision_sha256)
        REFERENCES app.candidate_recipe_revisions (tenant_id, site_id, id, revision_sha256),
    FOREIGN KEY (tenant_id, site_id, decision_id)
        REFERENCES app.candidate_recipe_review_decisions (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, extension_id)
        REFERENCES app.github_pr_extensions (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, binding_id)
        REFERENCES app.github_read_bindings (tenant_id, site_id, id),
    FOREIGN KEY (recipe_release_id) REFERENCES control.recipe_releases (id),
    FOREIGN KEY (tenant_id, requested_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id),
    CHECK ((journal_generation IS NULL AND journal_position IS NULL AND journal_body_hash IS NULL)
        OR (journal_generation IS NOT NULL AND journal_position IS NOT NULL AND journal_body_hash IS NOT NULL)),
    CHECK ((expected_tree_sha IS NULL AND expected_commit_sha IS NULL)
        OR (expected_tree_sha IS NOT NULL AND expected_commit_sha IS NOT NULL)),
    CHECK ((step = 'done' AND state = 'opened' AND pr_number IS NOT NULL AND pr_url IS NOT NULL)
        OR (step != 'done' AND state != 'opened' AND pr_number IS NULL AND pr_url IS NULL))
);
CREATE INDEX github_pr_operations_site_state ON app.github_pr_operations
    (tenant_id, site_id, state, created_at DESC);
CREATE UNIQUE INDEX github_pr_operations_one_unresolved_site
ON app.github_pr_operations (tenant_id, site_id)
WHERE state IN ('planned', 'ready', 'dispatching', 'outcome_unknown');
CREATE TABLE app.github_pr_operation_events (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    operation_id uuid NOT NULL,
    step text NOT NULL CHECK (step IN ('tree', 'commit', 'branch', 'pr', 'done')),
    event_kind text NOT NULL CHECK (event_kind IN (
        'planned', 'journal_acknowledged', 'lease_claimed', 'dispatching',
        'outcome_unknown', 'reconciled', 'completed', 'blocked'
    )),
    evidence_sha256 bytea CHECK (octet_length(evidence_sha256) = 32),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, operation_id)
        REFERENCES app.github_pr_operations (tenant_id, site_id, id)
);
CREATE TRIGGER github_pr_operation_events_immutable BEFORE UPDATE OR DELETE
ON app.github_pr_operation_events FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.github_pr_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_pr_operations FORCE ROW LEVEL SECURITY;
CREATE POLICY github_pr_operation_scope ON app.github_pr_operations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
ALTER TABLE app.github_pr_operation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_pr_operation_events FORCE ROW LEVEL SECURITY;
CREATE POLICY github_pr_operation_event_scope ON app.github_pr_operation_events
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.github_pr_operations, app.github_pr_operation_events FROM PUBLIC,
    signal_identity, signal_bootstrap, signal_api, signal_scheduler, signal_workflow,
    signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.github_pr_operation_eligible(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid
) RETURNS TABLE (outcome text, tenant_id uuid, user_id uuid,
    membership_epoch bigint, site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' OR NOT control.current_github_site_proof(
        v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT 'authority_denied'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT revision.* INTO v_revision FROM app.candidate_recipe_revisions revision
     WHERE revision.tenant_id = v_authority.tenant_id
       AND revision.site_id = p_site_id AND revision.id = p_revision_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'revision_unavailable'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT decision.* INTO v_decision FROM app.candidate_recipe_review_decisions decision
     WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
       AND decision.candidate_revision_id = p_revision_id;
    IF NOT FOUND OR v_decision.decision <> 'approved'
       OR v_decision.revision_sha256 <> v_revision.revision_sha256
       OR v_decision.recovery_generation <> p_generation
       OR v_decision.membership_epoch <> v_authority.membership_epoch
       OR v_decision.site_authorization_epoch <> v_authority.site_authorization_epoch THEN
        RETURN QUERY SELECT 'decision_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_recipe_revisions newer
       WHERE newer.tenant_id = v_revision.tenant_id AND newer.site_id = v_revision.site_id
         AND newer.audit_report_id = v_revision.audit_report_id
         AND newer.finding_id = v_revision.finding_id
         AND (newer.sealed_at, newer.id) > (v_revision.sealed_at, v_revision.id)) THEN
        RETURN QUERY SELECT 'revision_superseded'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.github_pr_extensions ext
       JOIN app.github_read_bindings binding ON binding.tenant_id = ext.tenant_id
          AND binding.site_id = ext.site_id AND binding.id = ext.binding_id
       WHERE ext.tenant_id = v_revision.tenant_id AND ext.site_id = v_revision.site_id
         AND ext.id = v_revision.extension_id AND ext.status = 'observed'
         AND ext.base_sha = v_revision.base_sha AND ext.coverage = 'complete'
         AND ext.repository_id = binding.repository_id AND ext.framework = 'eleventy'
         AND ext.content_format = 'html' AND binding.status = 'active'
         AND binding.protected AND binding.base_sha = v_revision.base_sha) THEN
        RETURN QUERY SELECT 'binding_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.candidate_build_receipts build
       WHERE build.tenant_id = v_revision.tenant_id AND build.site_id = v_revision.site_id
         AND build.build_id = v_revision.build_id AND build.exit_class = 'passed'
         AND build.base_sha = v_revision.base_sha
         AND build.patch_sha256 = v_revision.patch_sha256) THEN
        RETURN QUERY SELECT 'build_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM control.recipe_releases release
       WHERE release.id = v_revision.recipe_release_id
         AND release.content_hash = v_revision.release_content_hash
         AND (SELECT event.status FROM control.recipe_release_events event
              WHERE event.release_id = release.id
              ORDER BY event.sequence_number DESC LIMIT 1) = 'REVIEWED'
         AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones denial
              WHERE denial.target_kind = 'recipe_release'
                AND denial.target_id = release.id)) THEN
        RETURN QUERY SELECT 'release_inactive'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'eligible'::text, v_authority.tenant_id, v_authority.user_id,
        v_authority.membership_epoch, v_authority.site_authorization_epoch;
END; $$;

CREATE FUNCTION control.prepare_github_pr_operation(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_revision_id uuid, p_revision_sha256 bytea, p_operation_id uuid,
    p_intent_sha256 bytea
) RETURNS TABLE (operation_id uuid, operation_state text, operation_step text,
    canonical_manifest bytea, operation_created_at timestamptz, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
        v_extension app.github_pr_extensions%%ROWTYPE;
        v_existing app.github_pr_operations%%ROWTYPE;
        v_branch text;
BEGIN
    IF p_operation_id IS NULL OR p_revision_id IS NULL
       OR octet_length(p_revision_sha256) <> 32 OR octet_length(p_intent_sha256) <> 32 THEN
        RAISE EXCEPTION 'invalid_pr_operation_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, p_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, v_eligible.outcome;
        RETURN;
    END IF;
    SELECT * INTO v_revision FROM app.candidate_recipe_revisions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id AND id = p_revision_id;
    IF v_revision.revision_sha256 <> p_revision_sha256 THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, 'revision_stale'::text;
        RETURN;
    END IF;
    SELECT * INTO v_decision FROM app.candidate_recipe_review_decisions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id
       AND candidate_revision_id = p_revision_id;
    SELECT * INTO v_extension FROM app.github_pr_extensions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id
       AND id = v_revision.extension_id;
    v_branch := 'signal/' || replace(p_operation_id::text, '-', '');
    SELECT * INTO v_existing FROM app.github_pr_operations
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site_id
       AND (id = p_operation_id OR candidate_revision_id = p_revision_id);
    IF FOUND THEN
        IF v_existing.id <> p_operation_id OR v_existing.candidate_revision_id <> p_revision_id
           OR v_existing.revision_sha256 <> p_revision_sha256
           OR v_existing.intent_sha256 <> p_intent_sha256
           OR v_existing.branch_name <> v_branch THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
                NULL::timestamptz, 'operation_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.state, v_existing.step,
            v_revision.canonical_manifest, v_existing.created_at, 'prepared'::text;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_pr_operations other
       WHERE other.tenant_id = v_eligible.tenant_id AND other.site_id = p_site_id
         AND other.state NOT IN ('opened', 'blocked')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, 'resource_conflict'::text;
        RETURN;
    END IF;
    INSERT INTO app.github_pr_operations (
        tenant_id, site_id, id, candidate_revision_id, revision_sha256, intent_sha256,
        decision_id, extension_id, binding_id, recipe_release_id, requested_by_user_id,
        recovery_generation, membership_epoch, site_epoch, base_sha,
        branch_name, step, state
    ) VALUES (v_eligible.tenant_id, p_site_id, p_operation_id, p_revision_id,
        p_revision_sha256, p_intent_sha256, v_decision.id, v_extension.id,
        v_extension.binding_id, v_revision.recipe_release_id, v_eligible.user_id,
        p_generation, v_eligible.membership_epoch, v_eligible.site_epoch,
        v_revision.base_sha, v_branch, 'tree', 'planned');
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind)
    VALUES (v_eligible.tenant_id, p_site_id, gen_random_uuid(), p_operation_id, 'tree', 'planned');
    RETURN QUERY SELECT p_operation_id, 'planned'::text, 'tree'::text,
        v_revision.canonical_manifest, transaction_timestamp(), 'prepared'::text;
END; $$;

CREATE FUNCTION control.acknowledge_github_pr_intent(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid,
    p_journal_generation uuid, p_position bigint, p_body_hash bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM * FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_operation.tenant_id <> v_eligible.tenant_id OR p_journal_generation IS NULL
       OR p_position < 1 OR octet_length(p_body_hash) <> 32 THEN
        RETURN 'journal_invalid';
    END IF;
    IF v_operation.journal_generation IS NOT NULL THEN
        IF (v_operation.journal_generation, v_operation.journal_position,
            v_operation.journal_body_hash) = (p_journal_generation, p_position, p_body_hash) THEN
            RETURN 'acknowledged';
        END IF;
        RETURN 'journal_conflict';
    END IF;
    UPDATE app.github_pr_operations SET journal_generation = p_journal_generation,
        journal_position = p_position, journal_body_hash = p_body_hash,
        updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        v_operation.step, 'journal_acknowledged', p_body_hash);
    RETURN 'acknowledged';
END; $$;

CREATE FUNCTION control.claim_github_pr_operation(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid
) RETURNS TABLE (fence bigint, operation_state text, operation_step text, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM * FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'operation_unavailable'::text;
        RETURN;
    END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' OR v_eligible.tenant_id <> v_operation.tenant_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, v_eligible.outcome;
        RETURN;
    END IF;
    IF v_operation.journal_generation IS NULL OR p_worker_id IS NULL THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'journal_unavailable'::text;
        RETURN;
    END IF;
    IF v_operation.lease_until > transaction_timestamp()
       AND v_operation.lease_holder <> p_worker_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'lease_held'::text;
        RETURN;
    END IF;
    IF v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_until <= transaction_timestamp() THEN
        UPDATE app.github_pr_operations SET lease_holder = p_worker_id,
            lease_fence = lease_fence + 1,
            lease_until = transaction_timestamp() + interval '60 seconds',
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id
         RETURNING lease_fence INTO v_operation.lease_fence;
        INSERT INTO app.github_pr_operation_events
            (tenant_id, site_id, id, operation_id, step, event_kind)
        VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
            v_operation.step, 'lease_claimed');
    END IF;
    RETURN QUERY SELECT v_operation.lease_fence, v_operation.state,
        v_operation.step, 'claimed'::text;
END; $$;

CREATE FUNCTION control.bind_github_pr_operation_tree(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid,
    p_tree_sha text, p_commit_sha text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM * FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.journal_generation IS NULL
       OR p_tree_sha !~ '^[0-9a-f]{40}$' OR p_commit_sha !~ '^[0-9a-f]{40}$' THEN
        RETURN 'tree_binding_denied';
    END IF;
    IF v_operation.expected_tree_sha IS NOT NULL THEN
        IF v_operation.expected_tree_sha = p_tree_sha
           AND v_operation.expected_commit_sha = p_commit_sha THEN RETURN 'bound'; END IF;
        RETURN 'tree_binding_conflict';
    END IF;
    IF v_operation.state <> 'planned' OR v_operation.step <> 'tree' THEN
        RETURN 'tree_binding_denied';
    END IF;
    UPDATE app.github_pr_operations SET expected_tree_sha = p_tree_sha,
        expected_commit_sha = p_commit_sha, updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    RETURN 'bound';
END; $$;

CREATE FUNCTION control.github_pr_dispatch_permit(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM * FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.journal_generation IS NULL
       OR v_operation.expected_tree_sha IS NULL
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR v_operation.step <> p_step OR v_operation.state <> 'dispatching' THEN
        RETURN 'permit_denied';
    END IF;
    RETURN 'permitted';
END; $$;

CREATE FUNCTION control.begin_github_pr_step(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM * FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.journal_generation IS NULL
       OR v_operation.expected_tree_sha IS NULL
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR v_operation.step <> p_step THEN RETURN 'permit_denied'; END IF;
    IF v_operation.state IN ('dispatching', 'outcome_unknown') THEN RETURN 'reconcile_required'; END IF;
    IF v_operation.state NOT IN ('planned', 'ready') THEN RETURN 'operation_closed'; END IF;
    UPDATE app.github_pr_operations SET state = 'dispatching', updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, 'dispatching');
    RETURN 'dispatching';
END; $$;

CREATE FUNCTION control.finish_github_pr_step(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text,
    p_result text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL,
    p_pr_url text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.github_pr_operations%%ROWTYPE; v_next text;
BEGIN
    PERFORM * FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    IF control.github_pr_dispatch_permit(p_session_hash, p_site_id, p_generation,
        p_operation_id, p_worker_id, p_fence, p_step) <> 'permitted'
       OR octet_length(p_evidence_sha256) <> 32
       OR p_result NOT IN ('completed', 'outcome_unknown', 'blocked') THEN
        RETURN 'permit_denied';
    END IF;
    IF p_result = 'completed' THEN
        v_next := CASE p_step WHEN 'tree' THEN 'commit' WHEN 'commit' THEN 'branch'
            WHEN 'branch' THEN 'pr' WHEN 'pr' THEN 'done' END;
        IF v_next IS NULL OR (p_step = 'pr' AND (p_pr_number < 1 OR p_pr_url !~
            '^https://github[.]com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'))
           OR (p_step <> 'pr' AND (p_pr_number IS NOT NULL OR p_pr_url IS NOT NULL)) THEN
            RETURN 'result_invalid';
        END IF;
        UPDATE app.github_pr_operations SET step = v_next,
            state = CASE WHEN v_next = 'done' THEN 'opened' ELSE 'ready' END,
            pr_number = p_pr_number, pr_url = p_pr_url,
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    ELSE
        UPDATE app.github_pr_operations SET state = p_result,
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    END IF;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, p_result, p_evidence_sha256);
    RETURN p_result;
END; $$;

CREATE FUNCTION control.reconcile_github_pr_step(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text,
    p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL, p_pr_url text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.github_pr_operations%%ROWTYPE; v_eligible record; v_next text;
BEGIN
    PERFORM * FROM control.resolve_snapshot_authority(p_session_hash, p_site_id, p_generation);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id OR v_operation.step <> p_step
       OR v_operation.state NOT IN ('dispatching', 'outcome_unknown')
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR octet_length(p_evidence_sha256) <> 32 THEN RETURN 'permit_denied'; END IF;
    v_next := CASE p_step WHEN 'tree' THEN 'commit' WHEN 'commit' THEN 'branch'
        WHEN 'branch' THEN 'pr' WHEN 'pr' THEN 'done' END;
    IF v_next IS NULL OR (p_step = 'pr' AND (p_pr_number < 1 OR p_pr_url !~
        '^https://github[.]com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'))
       OR (p_step <> 'pr' AND (p_pr_number IS NOT NULL OR p_pr_url IS NOT NULL)) THEN
        RETURN 'result_invalid';
    END IF;
    UPDATE app.github_pr_operations SET step = v_next,
        state = CASE WHEN v_next = 'done' THEN 'opened' ELSE 'ready' END,
        pr_number = p_pr_number, pr_url = p_pr_url, updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, 'reconciled', p_evidence_sha256);
    RETURN 'reconciled';
END; $$;

CREATE FUNCTION control.read_authenticated_github_pr_operations(
    p_session_hash bytea, p_site_id uuid, p_generation text
) RETURNS TABLE (operation_id uuid, candidate_revision_id uuid, revision_sha256 text,
    branch_name text, base_sha text, head_sha text, expected_tree_sha text,
    state text, step text, pr_number bigint, pr_url text,
    journal_generation uuid, journal_position bigint, journal_body_hash text,
    created_at timestamptz, updated_at timestamptz, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;
    RETURN QUERY SELECT op.id, op.candidate_revision_id, encode(op.revision_sha256, 'hex'),
        op.branch_name, op.base_sha, op.expected_commit_sha, op.expected_tree_sha,
        op.state, op.step, op.pr_number, op.pr_url, op.journal_generation,
        op.journal_position, encode(op.journal_body_hash, 'hex'),
        op.created_at, op.updated_at, 'found'::text
      FROM app.github_pr_operations op
     WHERE op.tenant_id = v_authority.tenant_id AND op.site_id = p_site_id
     ORDER BY op.created_at DESC, op.id DESC LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, 'not_found'::text;
    END IF;
END; $$;

REVOKE ALL ON FUNCTION control.github_pr_operation_eligible(bytea, uuid, text, uuid),
    control.prepare_github_pr_operation(bytea, uuid, text, uuid, bytea, uuid, bytea),
    control.acknowledge_github_pr_intent(bytea, uuid, text, uuid, uuid, bigint, bytea),
    control.claim_github_pr_operation(bytea, uuid, text, uuid, uuid),
    control.bind_github_pr_operation_tree(bytea, uuid, text, uuid, text, text),
    control.github_pr_dispatch_permit(bytea, uuid, text, uuid, uuid, bigint, text),
    control.begin_github_pr_step(bytea, uuid, text, uuid, uuid, bigint, text),
    control.finish_github_pr_step(bytea, uuid, text, uuid, uuid, bigint, text, text, bytea, bigint, text),
    control.reconcile_github_pr_step(bytea, uuid, text, uuid, uuid, bigint, text, bytea, bigint, text),
    control.read_authenticated_github_pr_operations(bytea, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_github_pr_operation(bytea, uuid, text, uuid, bytea, uuid, bytea),
    control.acknowledge_github_pr_intent(bytea, uuid, text, uuid, uuid, bigint, bytea),
    control.claim_github_pr_operation(bytea, uuid, text, uuid, uuid),
    control.bind_github_pr_operation_tree(bytea, uuid, text, uuid, text, text),
    control.github_pr_dispatch_permit(bytea, uuid, text, uuid, uuid, bigint, text),
    control.begin_github_pr_step(bytea, uuid, text, uuid, uuid, bigint, text),
    control.finish_github_pr_step(bytea, uuid, text, uuid, uuid, bigint, text, text, bytea, bigint, text),
    control.reconcile_github_pr_step(bytea, uuid, text, uuid, uuid, bigint, text, bytea, bigint, text),
    control.read_authenticated_github_pr_operations(bytea, uuid, text) TO signal_identity;

ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check
CHECK (egress_profile IN (
    'legacy_unqualified', 'crawl_page', 'crawl_robots', 'browser_read',
    'github_rest', 'github_repository_write', 'google_oauth_token', 'google_oauth_revoke',
    'gsc_api', 'bing_oauth_token', 'bing_api', 'jev', 'model_json', 'openai_model',
    'openai_assistant', 'perplexity_assistant', 'gemini_assistant'
));

CREATE FUNCTION control.bind_github_repository_write_profile(
    p_tenant_id uuid, p_site_id uuid, p_operation_id uuid,
    p_request_sha256 bytea, p_repository text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.egress_operations%%ROWTYPE; v_suffix text; v_prefix text;
BEGIN
    IF p_tenant_id IS NULL OR p_site_id IS NULL OR p_operation_id IS NULL
       OR octet_length(p_request_sha256) IS DISTINCT FROM 32
       OR p_repository IS NULL OR p_repository !~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$'
       OR split_part(p_repository, '/', 2) IN ('.', '..') THEN
        RAISE EXCEPTION 'invalid_repository_write_profile' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_operation FROM app.egress_operations
     WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    v_prefix := 'https://api.github.com/repos/' || p_repository;
    v_suffix := substr(v_operation.request_url, length(v_prefix) + 1);
    IF NOT FOUND OR v_operation.state <> 'dispatched'
       OR v_operation.request_sha256 IS DISTINCT FROM p_request_sha256
       OR v_operation.purpose <> 'connector' OR NOT v_operation.credentialed
       OR v_operation.origin <> 'https://api.github.com'
       OR left(v_operation.request_url, length(v_prefix)) <> v_prefix
       OR v_operation.request_bytes > 131072 OR v_operation.max_response_bytes > 262144
       OR NOT (
           (v_operation.method = 'POST' AND v_suffix IN
               ('/git/refs', '/git/trees', '/git/blobs', '/git/commits', '/pulls'))
           OR (v_operation.method = 'GET' AND (
               v_suffix ~ '^/git/(trees|blobs|commits)/[0-9a-f]{40}$'
               OR v_suffix ~ '^/git/ref/heads/signal/[0-9a-f]{32}$'
               OR v_suffix ~ '^/pulls[?][A-Za-z0-9_%%&=:+./-]+$'
           ))
       ) THEN
        RAISE EXCEPTION 'repository_write_profile_conflict' USING ERRCODE = '22023';
    END IF;
    IF v_operation.egress_profile <> 'legacy_unqualified' THEN
        IF v_operation.egress_profile <> 'github_repository_write' THEN
            RAISE EXCEPTION 'repository_write_profile_conflict' USING ERRCODE = '22023';
        END IF;
        RETURN 'bound';
    END IF;
    UPDATE app.egress_operations SET egress_profile = 'github_repository_write'
     WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_operation_id;
    RETURN 'bound';
END; $$;
REVOKE ALL ON FUNCTION control.bind_github_repository_write_profile(uuid, uuid, uuid, bytea, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_github_repository_write_profile(uuid, uuid, uuid, bytea, text)
TO signal_crawl_admission;

ALTER FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text)
RENAME TO bind_pre_repository_write_egress_profile;
REVOKE ALL ON FUNCTION control.bind_pre_repository_write_egress_profile(uuid, uuid, uuid, bytea, text)
FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(
    p_tenant_id uuid, p_site_id uuid, p_operation_id uuid,
    p_request_sha256 bytea, p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_operation app.egress_operations%%ROWTYPE;
BEGIN
    IF p_profile = 'github_rest' THEN
        PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
        PERFORM set_config('signal.site_id', p_site_id::text, true);
        SELECT * INTO v_operation FROM app.egress_operations
         WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_operation_id;
        IF NOT FOUND OR NOT (v_operation.method = 'GET' OR (
            v_operation.method = 'POST' AND v_operation.request_url ~
                '^https://api[.]github[.]com/app/installations/[1-9][0-9]*/access_tokens$'
        )) THEN
            RAISE EXCEPTION 'github_read_profile_mutation_denied' USING ERRCODE = '22023';
        END IF;
    END IF;
    RETURN control.bind_pre_repository_write_egress_profile(
        p_tenant_id, p_site_id, p_operation_id, p_request_sha256, p_profile);
END; $$;
REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid, uuid, uuid, bytea, text)
TO signal_crawl_admission;
