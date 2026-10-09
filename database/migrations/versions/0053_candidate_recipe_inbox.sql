ALTER TABLE app.candidate_recipe_revisions
    ADD CONSTRAINT candidate_recipe_revisions_hash_reference_key
    UNIQUE (tenant_id, site_id, id, revision_sha256);

CREATE TABLE app.candidate_recipe_review_decisions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    candidate_revision_id uuid NOT NULL,
    revision_sha256 bytea NOT NULL CHECK (octet_length(revision_sha256) = 32),
    decided_by_user_id uuid NOT NULL,
    authentication_level text NOT NULL CHECK (authentication_level IN ('primary', 'mfa')),
    actor_role text NOT NULL CHECK (actor_role = 'owner'),
    decision_channel text NOT NULL CHECK (decision_channel = 'dashboard'),
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected', 'changes_requested')),
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    ),
    membership_epoch bigint NOT NULL CHECK (membership_epoch > 0),
    site_authorization_epoch bigint NOT NULL CHECK (site_authorization_epoch > 0),
    decided_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT candidate_recipe_review_decisions_revision_key
        UNIQUE (tenant_id, site_id, candidate_revision_id),
    FOREIGN KEY (tenant_id, site_id, candidate_revision_id, revision_sha256)
        REFERENCES app.candidate_recipe_revisions (tenant_id, site_id, id, revision_sha256),
    FOREIGN KEY (tenant_id, decided_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX candidate_recipe_review_decisions_time
ON app.candidate_recipe_review_decisions (tenant_id, site_id, decided_at DESC, id);
CREATE TRIGGER candidate_recipe_review_decisions_immutable
BEFORE UPDATE OR DELETE ON app.candidate_recipe_review_decisions
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.candidate_recipe_review_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.candidate_recipe_review_decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY candidate_recipe_review_decisions_scope ON app.candidate_recipe_review_decisions
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.candidate_recipe_review_decisions FROM PUBLIC, signal_identity,
    signal_bootstrap, signal_api, signal_scheduler, signal_workflow,
    signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.read_authenticated_candidate_recipe_inbox(
    p_session_hash bytea, p_site_id uuid, p_generation text
) RETURNS TABLE (
    revision_id uuid, revision_sha256 text, canonical_manifest jsonb,
    sealed_at timestamptz, recipe_release_id uuid, release_content_hash text,
    base_sha text, patch_sha256 text, review_status text, decision_id uuid,
    decision text, decided_by_user_id uuid, decision_channel text,
    decided_at timestamptz, outcome text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;
    RETURN QUERY
    SELECT revision.id, encode(revision.revision_sha256, 'hex'),
           convert_from(revision.canonical_manifest, 'UTF8')::jsonb,
           revision.sealed_at, revision.recipe_release_id,
           encode(revision.release_content_hash, 'hex'), revision.base_sha,
           revision.patch_sha256,
           CASE
             WHEN decision.id IS NOT NULL THEN decision.decision
             WHEN EXISTS (
               SELECT 1 FROM app.candidate_recipe_revisions AS newer
                WHERE newer.tenant_id = revision.tenant_id AND newer.site_id = revision.site_id
                  AND newer.audit_report_id = revision.audit_report_id
                  AND newer.finding_id = revision.finding_id
                  AND (newer.sealed_at, newer.id) > (revision.sealed_at, revision.id)
             ) THEN 'superseded'
             WHEN NOT EXISTS (
               SELECT 1 FROM app.github_pr_extensions AS extension
                JOIN app.github_read_bindings AS binding
                  ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
                 AND binding.id = extension.binding_id AND binding.status = 'active'
                WHERE extension.tenant_id = revision.tenant_id AND extension.site_id = revision.site_id
                  AND extension.id = revision.extension_id AND extension.status = 'observed'
                  AND extension.base_sha = revision.base_sha
             ) THEN 'stale_base'
             ELSE 'pending'
           END,
           decision.id, decision.decision, decision.decided_by_user_id,
           decision.decision_channel, decision.decided_at, 'found'::text
      FROM app.candidate_recipe_revisions AS revision
      LEFT JOIN app.candidate_recipe_review_decisions AS decision
        ON decision.tenant_id = revision.tenant_id AND decision.site_id = revision.site_id
       AND decision.candidate_revision_id = revision.id
     WHERE revision.tenant_id = v_authority.tenant_id AND revision.site_id = p_site_id
     ORDER BY revision.sealed_at DESC, revision.id DESC
     LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, 'not_found'::text;
    END IF;
END; $$;

CREATE FUNCTION control.decide_authenticated_candidate_recipe_revision(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_revision_id uuid, p_expected_revision_sha256 bytea,
    p_decision_id uuid, p_decision text
) RETURNS TABLE (
    revision_id uuid, revision_sha256 text, canonical_manifest jsonb,
    sealed_at timestamptz, recipe_release_id uuid, release_content_hash text,
    base_sha text, patch_sha256 text, review_status text, decision_id uuid,
    decision text, decided_by_user_id uuid, decision_channel text,
    decided_at timestamptz, reused boolean, outcome text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_existing app.candidate_recipe_review_decisions%%ROWTYPE;
        v_now timestamptz := transaction_timestamp(); v_reused boolean := false;
BEGIN
    IF p_revision_id IS NULL OR p_decision_id IS NULL OR p_revision_id = p_decision_id
       OR p_expected_revision_sha256 IS NULL OR octet_length(p_expected_revision_sha256) <> 32
       OR p_decision NOT IN ('approved', 'rejected', 'changes_requested') THEN
        RAISE EXCEPTION 'invalid_candidate_recipe_decision_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, v_authority.outcome;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'approval_permission_denied';
        RETURN;
    END IF;
    SELECT * INTO v_revision FROM app.candidate_recipe_revisions AS revision
     WHERE revision.tenant_id = v_authority.tenant_id AND revision.site_id = p_site_id
       AND revision.id = p_revision_id FOR KEY SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'approval_not_found';
        RETURN;
    END IF;
    IF v_revision.revision_sha256 <> p_expected_revision_sha256 THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'stale_revision';
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_recipe_revisions AS newer
        WHERE newer.tenant_id = v_revision.tenant_id AND newer.site_id = v_revision.site_id
          AND newer.audit_report_id = v_revision.audit_report_id AND newer.finding_id = v_revision.finding_id
          AND (newer.sealed_at, newer.id) > (v_revision.sealed_at, v_revision.id)) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'stale_revision';
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
        JOIN app.github_read_bindings AS binding ON binding.tenant_id = extension.tenant_id
         AND binding.site_id = extension.site_id AND binding.id = extension.binding_id
         AND binding.status = 'active'
        WHERE extension.tenant_id = v_revision.tenant_id AND extension.site_id = v_revision.site_id
          AND extension.id = v_revision.extension_id AND extension.status = 'observed'
          AND extension.base_sha = v_revision.base_sha) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'stale_revision';
        RETURN;
    END IF;
    SELECT * INTO v_existing FROM app.candidate_recipe_review_decisions AS decision
     WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
       AND decision.candidate_revision_id = p_revision_id;
    IF FOUND THEN
        IF v_existing.decision <> p_decision OR v_existing.decided_by_user_id <> v_authority.user_id THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
                NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
                NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'decision_conflict';
            RETURN;
        END IF;
        v_reused := true;
    ELSE
        INSERT INTO app.candidate_recipe_review_decisions (
            tenant_id, site_id, id, candidate_revision_id, revision_sha256, decided_by_user_id,
            authentication_level, actor_role, decision_channel, decision, recovery_generation,
            membership_epoch, site_authorization_epoch, decided_at
        ) VALUES (v_authority.tenant_id, p_site_id, p_decision_id, p_revision_id,
            p_expected_revision_sha256, v_authority.user_id, v_authority.authentication_level,
            v_authority.role_key, 'dashboard', p_decision, p_generation,
            v_authority.membership_epoch, v_authority.site_authorization_epoch, v_now)
        ON CONFLICT ON CONSTRAINT candidate_recipe_review_decisions_revision_key DO NOTHING;
        IF NOT FOUND THEN
            SELECT * INTO v_existing FROM app.candidate_recipe_review_decisions AS decision
             WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
               AND decision.candidate_revision_id = p_revision_id;
            IF NOT FOUND OR v_existing.decision <> p_decision
               OR v_existing.decided_by_user_id <> v_authority.user_id THEN
                RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
                    NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, false, 'decision_conflict';
                RETURN;
            END IF;
            v_reused := true;
        ELSE
            SELECT * INTO v_existing FROM app.candidate_recipe_review_decisions AS decision
             WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
               AND decision.candidate_revision_id = p_revision_id;
        END IF;
    END IF;
    RETURN QUERY SELECT v_revision.id, encode(v_revision.revision_sha256, 'hex'),
        convert_from(v_revision.canonical_manifest, 'UTF8')::jsonb, v_revision.sealed_at,
        v_revision.recipe_release_id, encode(v_revision.release_content_hash, 'hex'),
        v_revision.base_sha, v_revision.patch_sha256, v_existing.decision, v_existing.id,
        v_existing.decision, v_existing.decided_by_user_id, v_existing.decision_channel,
        v_existing.decided_at, v_reused, 'decided';
END; $$;

REVOKE ALL ON FUNCTION control.read_authenticated_candidate_recipe_inbox(bytea, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.decide_authenticated_candidate_recipe_revision(
    bytea, uuid, text, uuid, bytea, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_authenticated_candidate_recipe_inbox(bytea, uuid, text)
TO signal_identity;
GRANT EXECUTE ON FUNCTION control.decide_authenticated_candidate_recipe_revision(
    bytea, uuid, text, uuid, bytea, uuid, text) TO signal_identity;
