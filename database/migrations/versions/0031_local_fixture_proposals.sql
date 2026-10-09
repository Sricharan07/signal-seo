CREATE TABLE app.proposals (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    source_finding_id uuid NOT NULL,
    created_by_user_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT proposals_source_finding_key
        UNIQUE (tenant_id, site_id, source_finding_id),
    FOREIGN KEY (tenant_id, site_id, source_finding_id)
        REFERENCES app.findings (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, created_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);

CREATE TABLE app.proposal_revisions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    proposal_id uuid NOT NULL,
    id uuid NOT NULL,
    revision_number integer NOT NULL CHECK (revision_number BETWEEN 1 AND 10000),
    source_finding_id uuid NOT NULL,
    source_evidence_id uuid NOT NULL,
    created_by_user_id uuid NOT NULL,
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    manifest_canonical bytea NOT NULL CHECK (
        octet_length(manifest_canonical) BETWEEN 1 AND 32768
    ),
    manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT proposal_revisions_number_key
        UNIQUE (tenant_id, site_id, proposal_id, revision_number),
    CONSTRAINT proposal_revisions_source_evidence_key
        UNIQUE (tenant_id, site_id, proposal_id, source_evidence_id),
    CONSTRAINT proposal_revisions_hash_reference_key
        UNIQUE (tenant_id, site_id, id, manifest_sha256),
    FOREIGN KEY (tenant_id, site_id, proposal_id)
        REFERENCES app.proposals (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, source_finding_id)
        REFERENCES app.findings (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, source_evidence_id)
        REFERENCES app.evidence_records (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, created_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX proposal_revisions_created
ON app.proposal_revisions (tenant_id, site_id, created_at DESC, id);

CREATE TABLE app.approval_requests (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    proposal_revision_id uuid NOT NULL,
    revision_sha256 bytea NOT NULL CHECK (octet_length(revision_sha256) = 32),
    requested_by_user_id uuid NOT NULL,
    approval_class text NOT NULL CHECK (approval_class = 'A1'),
    requested_authority text NOT NULL CHECK (
        requested_authority = 'accept_local_fixture_draft'
    ),
    request_channel text NOT NULL CHECK (request_channel = 'signal_chat'),
    requested_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > requested_at),
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT approval_requests_revision_key
        UNIQUE (tenant_id, site_id, proposal_revision_id),
    CONSTRAINT approval_requests_decision_reference_key
        UNIQUE (tenant_id, site_id, id, proposal_revision_id, revision_sha256),
    FOREIGN KEY (tenant_id, site_id, proposal_revision_id, revision_sha256)
        REFERENCES app.proposal_revisions (
            tenant_id, site_id, id, manifest_sha256
        ),
    FOREIGN KEY (tenant_id, requested_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX approval_requests_expiry
ON app.approval_requests (tenant_id, site_id, expires_at, id);

CREATE TABLE app.approval_decisions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    approval_request_id uuid NOT NULL,
    proposal_revision_id uuid NOT NULL,
    revision_sha256 bytea NOT NULL CHECK (octet_length(revision_sha256) = 32),
    decided_by_user_id uuid NOT NULL,
    authentication_level text NOT NULL CHECK (
        authentication_level IN ('primary', 'mfa')
    ),
    actor_role text NOT NULL CHECK (actor_role = 'owner'),
    decision_channel text NOT NULL CHECK (decision_channel = 'dashboard'),
    decision text NOT NULL CHECK (
        decision IN ('approved', 'rejected', 'changes_requested')
    ),
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    ),
    membership_epoch bigint NOT NULL CHECK (membership_epoch > 0),
    site_authorization_epoch bigint NOT NULL CHECK (site_authorization_epoch > 0),
    decided_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT approval_decisions_request_key
        UNIQUE (tenant_id, site_id, approval_request_id),
    FOREIGN KEY (
        tenant_id, site_id, approval_request_id,
        proposal_revision_id, revision_sha256
    ) REFERENCES app.approval_requests (
        tenant_id, site_id, id, proposal_revision_id, revision_sha256
    ),
    FOREIGN KEY (tenant_id, decided_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX approval_decisions_time
ON app.approval_decisions (tenant_id, site_id, decided_at DESC, id);

CREATE TRIGGER proposals_immutable
BEFORE UPDATE OR DELETE ON app.proposals
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER proposal_revisions_immutable
BEFORE UPDATE OR DELETE ON app.proposal_revisions
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER approval_requests_immutable
BEFORE UPDATE OR DELETE ON app.approval_requests
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER approval_decisions_immutable
BEFORE UPDATE OR DELETE ON app.approval_decisions
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.proposals FORCE ROW LEVEL SECURITY;
CREATE POLICY proposals_scope ON app.proposals
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.proposal_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.proposal_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY proposal_revisions_scope ON app.proposal_revisions
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.approval_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY approval_requests_scope ON app.approval_requests
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.approval_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.approval_decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY approval_decisions_scope ON app.approval_decisions
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.prepare_authenticated_local_fixture_proposal(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
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
    v_source record;
    v_expected_manifest jsonb;
    v_proposal_id uuid;
    v_revision record;
    v_request record;
    v_reused boolean := false;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_proposal_id IS NULL OR p_revision_id IS NULL OR p_approval_request_id IS NULL
       OR p_proposal_id = p_revision_id OR p_proposal_id = p_approval_request_id
       OR p_revision_id = p_approval_request_id
       OR p_manifest IS NULL OR jsonb_typeof(p_manifest) <> 'object'
       OR p_manifest_canonical IS NULL
       OR octet_length(p_manifest_canonical) NOT BETWEEN 1 AND 32768
       OR p_manifest_sha256 IS NULL OR octet_length(p_manifest_sha256) <> 32
    THEN
        RAISE EXCEPTION 'invalid_local_fixture_proposal_input' USING ERRCODE = '22023';
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

    SELECT finding.id AS finding_id, finding.latest_command_id,
           finding.resource_locator, finding.confidence_class,
           evidence.id AS evidence_id, evidence.observed_at
      INTO v_source
      FROM app.findings AS finding
      JOIN LATERAL (
          SELECT observed.id, observed.observed_at
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
     ORDER BY finding.last_seen_at DESC, finding.id
     LIMIT 1
     FOR KEY SHARE OF finding;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            'finding_not_ready'::text;
        RETURN;
    END IF;

    v_expected_manifest := jsonb_build_object(
        'schema_version', 1,
        'proposal_kind', 'local_fixture_metadata_draft',
        'site_id', p_requested_site_id::text,
        'finding', jsonb_build_object(
            'id', v_source.finding_id::text,
            'evidence_id', v_source.evidence_id::text,
            'command_id', v_source.latest_command_id::text,
            'resource_locator', v_source.resource_locator,
            'confidence_class', v_source.confidence_class,
            'observed_at', to_char(
                v_source.observed_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        ),
        'target', jsonb_build_object(
            'resource_locator', '/fixture/missing-meta-description',
            'field', 'meta_description',
            'before', NULL,
            'after', 'Explore the Signal test fixture and its durable SEO evidence.'
        ),
        'recipe', jsonb_build_object(
            'id', 'title_description_improvement',
            'version', 'local-fixture-0.1.0',
            'qualification', 'fixture_only'
        ),
        'assessment', jsonb_build_object(
            'impact', 'One synthetic fixture metadata field',
            'risk', 'low',
            'confidence_basis', 'Deterministic HTML metadata parser',
            'customer_origin_read', false
        ),
        'tests', jsonb_build_array(
            'finding_evidence_bound',
            'target_field_scoped',
            'customer_origin_not_read',
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
                'release', 'local-deterministic-v1',
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
        'authority', jsonb_build_object(
            'approval_class', 'A1',
            'requested', 'accept_local_fixture_draft',
            'external_write', false
        ),
        'cost', jsonb_build_object(
            'currency', 'USD',
            'maximum_minor_units', 0
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

    INSERT INTO app.proposals (
        tenant_id, site_id, id, source_finding_id, created_by_user_id, created_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_proposal_id,
        v_source.finding_id, v_authority.user_id, v_now
    )
    ON CONFLICT ON CONSTRAINT proposals_source_finding_key DO NOTHING;

    SELECT current_proposal.id INTO v_proposal_id
      FROM app.proposals AS current_proposal
     WHERE current_proposal.tenant_id = v_authority.tenant_id
       AND current_proposal.site_id = p_requested_site_id
       AND current_proposal.source_finding_id = v_source.finding_id
     FOR UPDATE;
    IF v_proposal_id IS NULL THEN
        RAISE EXCEPTION 'local_fixture_proposal_identity_failed' USING ERRCODE = '55000';
    END IF;

    INSERT INTO app.proposal_revisions AS created_revision (
        tenant_id, site_id, proposal_id, id, revision_number,
        source_finding_id, source_evidence_id, created_by_user_id,
        manifest, manifest_canonical, manifest_sha256, created_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, v_proposal_id, p_revision_id,
        COALESCE((
            SELECT max(existing.revision_number)
              FROM app.proposal_revisions AS existing
             WHERE existing.tenant_id = v_authority.tenant_id
               AND existing.site_id = p_requested_site_id
               AND existing.proposal_id = v_proposal_id
        ), 0) + 1,
        v_source.finding_id, v_source.evidence_id, v_authority.user_id,
        p_manifest, p_manifest_canonical, p_manifest_sha256, v_now
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
           AND existing.source_evidence_id = v_source.evidence_id;
        IF NOT FOUND OR v_revision.manifest_sha256 <> p_manifest_sha256
           OR v_revision.manifest <> p_manifest
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
                NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
                'manifest_conflict'::text;
            RETURN;
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
        'accept_local_fixture_draft', 'signal_chat', v_now, v_now + interval '24 hours'
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
    END IF;

    RETURN QUERY SELECT v_proposal_id, v_revision.id, v_revision.revision_number,
        encode(v_revision.manifest_sha256, 'hex'), v_revision.manifest,
        v_revision.created_by_user_id, v_revision.created_at, v_request.id,
        CASE WHEN v_request.expires_at <= v_now THEN 'expired' ELSE 'pending' END,
        v_request.requested_at, v_request.expires_at, NULL::uuid, NULL::text,
        NULL::uuid, NULL::text, NULL::timestamptz, v_reused, 'prepared'::text;
END;
$$;

CREATE FUNCTION control.read_authenticated_local_fixture_proposals(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text
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
BEGIN
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

    RETURN QUERY
    SELECT proposal.id, revision.id, revision.revision_number,
           encode(revision.manifest_sha256, 'hex'), revision.manifest,
           revision.created_by_user_id, revision.created_at, request.id,
           CASE
               WHEN decision.id IS NOT NULL THEN decision.decision
               WHEN request.expires_at <= transaction_timestamp() THEN 'expired'
               ELSE 'pending'
           END,
           request.requested_at, request.expires_at, decision.id,
           decision.decision, decision.decided_by_user_id,
           decision.decision_channel, decision.decided_at, false, 'found'::text
      FROM app.proposals AS proposal
      JOIN app.proposal_revisions AS revision
        ON revision.tenant_id = proposal.tenant_id
       AND revision.site_id = proposal.site_id
       AND revision.proposal_id = proposal.id
      JOIN app.approval_requests AS request
        ON request.tenant_id = revision.tenant_id
       AND request.site_id = revision.site_id
       AND request.proposal_revision_id = revision.id
      LEFT JOIN app.approval_decisions AS decision
        ON decision.tenant_id = request.tenant_id
       AND decision.site_id = request.site_id
       AND decision.approval_request_id = request.id
     WHERE proposal.tenant_id = v_authority.tenant_id
       AND proposal.site_id = p_requested_site_id
     ORDER BY revision.created_at DESC, revision.id DESC
     LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            'not_found'::text;
    END IF;
END;
$$;

CREATE FUNCTION control.decide_authenticated_local_fixture_approval(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_approval_request_id uuid,
    p_expected_revision_sha256 bytea,
    p_decision_id uuid,
    p_decision text
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
    v_request record;
    v_existing record;
    v_decision record;
    v_reused boolean := false;
    v_now timestamptz := transaction_timestamp();
BEGIN
    IF p_approval_request_id IS NULL OR p_decision_id IS NULL
       OR p_approval_request_id = p_decision_id
       OR p_expected_revision_sha256 IS NULL
       OR octet_length(p_expected_revision_sha256) <> 32
       OR p_decision NOT IN ('approved', 'rejected', 'changes_requested')
    THEN
        RAISE EXCEPTION 'invalid_local_fixture_approval_input' USING ERRCODE = '22023';
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

    SELECT proposal.id AS proposal_id, revision.id AS revision_id,
           revision.revision_number, revision.manifest_sha256,
           revision.manifest, revision.created_by_user_id, revision.created_at,
           request.id AS request_id, request.requested_at, request.expires_at
      INTO v_request
      FROM app.approval_requests AS request
      JOIN app.proposal_revisions AS revision
        ON revision.tenant_id = request.tenant_id
       AND revision.site_id = request.site_id
       AND revision.id = request.proposal_revision_id
      JOIN app.proposals AS proposal
        ON proposal.tenant_id = revision.tenant_id
       AND proposal.site_id = revision.site_id
       AND proposal.id = revision.proposal_id
     WHERE request.tenant_id = v_authority.tenant_id
       AND request.site_id = p_requested_site_id
       AND request.id = p_approval_request_id
     FOR KEY SHARE OF request, revision, proposal;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            'approval_not_found'::text;
        RETURN;
    END IF;
    IF v_request.manifest_sha256 <> p_expected_revision_sha256 THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
            NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
            NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
            'stale_revision'::text;
        RETURN;
    END IF;

    SELECT existing.id, existing.decision, existing.decided_by_user_id,
           existing.decision_channel, existing.decided_at
      INTO v_existing
      FROM app.approval_decisions AS existing
     WHERE existing.tenant_id = v_authority.tenant_id
       AND existing.site_id = p_requested_site_id
       AND existing.approval_request_id = p_approval_request_id;
    IF FOUND THEN
        IF v_existing.decision <> p_decision
           OR v_existing.decided_by_user_id <> v_authority.user_id
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
                NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
                'decision_conflict'::text;
            RETURN;
        END IF;
        v_decision := v_existing;
        v_reused := true;
    ELSE
        IF v_request.expires_at <= v_now THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
                NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
                NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
                'approval_expired'::text;
            RETURN;
        END IF;
        INSERT INTO app.approval_decisions AS created (
            tenant_id, site_id, id, approval_request_id, proposal_revision_id,
            revision_sha256, decided_by_user_id, authentication_level, actor_role,
            decision_channel, decision, recovery_generation, membership_epoch,
            site_authorization_epoch, decided_at
        ) VALUES (
            v_authority.tenant_id, p_requested_site_id, p_decision_id,
            p_approval_request_id, v_request.revision_id,
            v_request.manifest_sha256, v_authority.user_id,
            v_authority.authentication_level, v_authority.role_key, 'dashboard',
            p_decision, p_current_recovery_generation, v_authority.membership_epoch,
            v_authority.site_authorization_epoch, v_now
        )
        ON CONFLICT ON CONSTRAINT approval_decisions_request_key DO NOTHING
        RETURNING created.id, created.decision, created.decided_by_user_id,
                  created.decision_channel, created.decided_at INTO v_decision;
        IF v_decision.id IS NULL THEN
            SELECT existing.id, existing.decision, existing.decided_by_user_id,
                   existing.decision_channel, existing.decided_at
              INTO v_decision
              FROM app.approval_decisions AS existing
             WHERE existing.tenant_id = v_authority.tenant_id
               AND existing.site_id = p_requested_site_id
               AND existing.approval_request_id = p_approval_request_id;
            IF NOT FOUND OR v_decision.decision <> p_decision
               OR v_decision.decided_by_user_id <> v_authority.user_id
            THEN
                RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::integer, NULL::text,
                    NULL::jsonb, NULL::uuid, NULL::timestamptz, NULL::uuid, NULL::text,
                    NULL::timestamptz, NULL::timestamptz, NULL::uuid, NULL::text,
                    NULL::uuid, NULL::text, NULL::timestamptz, NULL::boolean,
                    'decision_conflict'::text;
                RETURN;
            END IF;
            v_reused := true;
        END IF;
    END IF;

    RETURN QUERY SELECT v_request.proposal_id, v_request.revision_id,
        v_request.revision_number, encode(v_request.manifest_sha256, 'hex'),
        v_request.manifest, v_request.created_by_user_id, v_request.created_at,
        v_request.request_id, v_decision.decision, v_request.requested_at,
        v_request.expires_at, v_decision.id, v_decision.decision,
        v_decision.decided_by_user_id, v_decision.decision_channel,
        v_decision.decided_at, v_reused, 'decided'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.prepare_authenticated_local_fixture_proposal(
    bytea, uuid, text, uuid, uuid, uuid, jsonb, bytea, bytea
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_authenticated_local_fixture_proposals(
    bytea, uuid, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.decide_authenticated_local_fixture_approval(
    bytea, uuid, text, uuid, bytea, uuid, text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_authenticated_local_fixture_proposal(
    bytea, uuid, text, uuid, uuid, uuid, jsonb, bytea, bytea
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.read_authenticated_local_fixture_proposals(
    bytea, uuid, text
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.decide_authenticated_local_fixture_approval(
    bytea, uuid, text, uuid, bytea, uuid, text
) TO signal_identity;
