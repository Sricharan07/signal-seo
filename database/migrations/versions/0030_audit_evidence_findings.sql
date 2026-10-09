CREATE TABLE app.evidence_records (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    command_id uuid NOT NULL,
    manifest_id uuid NOT NULL,
    manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
    evidence_type text NOT NULL CHECK (evidence_type = 'page_metadata'),
    source_kind text NOT NULL CHECK (source_kind = 'synthetic_fixture'),
    source_identifier text NOT NULL CHECK (
        source_identifier = 'fixture:local-pilot/missing-meta-description/v1'
    ),
    observed_at timestamptz NOT NULL,
    valid_until timestamptz,
    quality jsonb NOT NULL CHECK (
        quality = '{"coverage":"complete","customer_origin_read":false,"parser_release":"python-html-parser-v1","schema_version":1}'::jsonb
    ),
    rights_status text NOT NULL CHECK (rights_status = 'internal_fixture'),
    content_hash bytea NOT NULL CHECK (octet_length(content_hash) = 32),
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT evidence_records_command_source_key
        UNIQUE (tenant_id, site_id, command_id, source_identifier),
    FOREIGN KEY (tenant_id, site_id, command_id)
        REFERENCES app.commands (tenant_id, site_id, id),
    CHECK (valid_until IS NULL OR valid_until > observed_at)
);
CREATE INDEX evidence_records_type_observed
ON app.evidence_records (tenant_id, site_id, evidence_type, observed_at DESC, id);

CREATE TABLE app.findings (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    first_command_id uuid NOT NULL,
    latest_command_id uuid NOT NULL,
    finding_key text NOT NULL CHECK (finding_key ~ '^[a-z][a-z0-9_.-]{0,127}$'),
    detector_release_id uuid NOT NULL,
    resource_locator text NOT NULL CHECK (
        resource_locator = '/fixture/missing-meta-description'
    ),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 160),
    summary text NOT NULL CHECK (length(summary) BETWEEN 1 AND 500),
    severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status text NOT NULL CHECK (status IN ('open', 'resolved', 'retracted')),
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    confidence_class text NOT NULL CHECK (
        confidence_class IN ('deterministic', 'corroborated', 'directional')
    ),
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT findings_current_detector_key UNIQUE (
        tenant_id, site_id, detector_release_id, finding_key, resource_locator
    ),
    FOREIGN KEY (tenant_id, site_id, first_command_id)
        REFERENCES app.commands (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, latest_command_id)
        REFERENCES app.commands (tenant_id, site_id, id),
    CHECK (last_seen_at >= first_seen_at)
);
CREATE INDEX findings_status_severity_seen
ON app.findings (tenant_id, site_id, status, severity, last_seen_at DESC, id);

CREATE TABLE app.finding_evidence (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    finding_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    relation text NOT NULL CHECK (relation = 'supports'),
    linked_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, finding_id, evidence_id, relation),
    FOREIGN KEY (tenant_id, site_id, finding_id)
        REFERENCES app.findings (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, evidence_id)
        REFERENCES app.evidence_records (tenant_id, site_id, id)
);
CREATE INDEX finding_evidence_evidence
ON app.finding_evidence (tenant_id, site_id, evidence_id, finding_id);

CREATE TRIGGER evidence_records_immutable
BEFORE UPDATE OR DELETE ON app.evidence_records
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION app.guard_finding_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'finding_deletion_prohibited' USING ERRCODE = '55000';
    END IF;
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.site_id IS DISTINCT FROM OLD.site_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.first_command_id IS DISTINCT FROM OLD.first_command_id
       OR NEW.finding_key IS DISTINCT FROM OLD.finding_key
       OR NEW.detector_release_id IS DISTINCT FROM OLD.detector_release_id
       OR NEW.resource_locator IS DISTINCT FROM OLD.resource_locator
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.summary IS DISTINCT FROM OLD.summary
       OR NEW.severity IS DISTINCT FROM OLD.severity
       OR NEW.status IS DISTINCT FROM OLD.status
       OR NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at
       OR NEW.confidence_class IS DISTINCT FROM OLD.confidence_class
       OR NEW.last_seen_at < OLD.last_seen_at
    THEN
        RAISE EXCEPTION 'finding_mutation_prohibited' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER findings_guarded
BEFORE UPDATE OR DELETE ON app.findings
FOR EACH ROW EXECUTE FUNCTION app.guard_finding_mutation();

CREATE TRIGGER finding_evidence_immutable
BEFORE UPDATE OR DELETE ON app.finding_evidence
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.evidence_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.evidence_records FORCE ROW LEVEL SECURITY;
CREATE POLICY evidence_records_scope ON app.evidence_records
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.findings FORCE ROW LEVEL SECURITY;
CREATE POLICY findings_scope ON app.findings
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.finding_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.finding_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY finding_evidence_scope ON app.finding_evidence
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.record_authenticated_local_fixture_finding(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_evidence_id uuid,
    p_finding_id uuid,
    p_content_hash bytea
)
RETURNS TABLE (
    finding_id uuid,
    evidence_id uuid,
    command_id uuid,
    manifest_id uuid,
    finding_key text,
    title text,
    summary text,
    resource_locator text,
    severity text,
    status text,
    confidence_class text,
    source_kind text,
    source_identifier text,
    content_sha256 text,
    evidence_observed_at timestamptz,
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    reused boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_command record;
    v_evidence record;
    v_finding record;
    v_reused boolean := false;
    v_detector_release_id constant uuid := '95eb3b3a-2318-4c31-a733-3c5ee4d17dc5';
    v_observed_at timestamptz := transaction_timestamp();
BEGIN
    IF p_evidence_id IS NULL OR p_finding_id IS NULL
       OR p_evidence_id = p_finding_id
       OR p_content_hash IS NULL OR octet_length(p_content_hash) <> 32
    THEN
        RAISE EXCEPTION 'invalid_fixture_finding_input' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash,
          p_requested_site_id,
          p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean, v_authority.outcome;
        RETURN;
    END IF;

    SELECT existing.id, (existing.result_reference->>'manifest_id')::uuid AS manifest_id,
           decode(existing.result_reference->>'manifest_sha256', 'hex') AS manifest_sha256
      INTO v_command
      FROM app.commands AS existing
     WHERE existing.tenant_id = v_authority.tenant_id
       AND existing.site_id = p_requested_site_id
       AND existing.actor_user_id = v_authority.user_id
       AND existing.route_key = 'api.site.snapshot'
       AND existing.status = 'succeeded'
       AND existing.result_reference->>'kind' = 'crawl_manifest'
     ORDER BY existing.accepted_at DESC, existing.id DESC
     LIMIT 1
     FOR KEY SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean, 'audit_not_ready'::text;
        RETURN;
    END IF;

    INSERT INTO app.evidence_records AS created (
        tenant_id, site_id, id, command_id, manifest_id, manifest_sha256,
        evidence_type, source_kind, source_identifier, observed_at, quality,
        rights_status, content_hash
    )
    VALUES (
        v_authority.tenant_id, p_requested_site_id, p_evidence_id, v_command.id,
        v_command.manifest_id, v_command.manifest_sha256, 'page_metadata',
        'synthetic_fixture', 'fixture:local-pilot/missing-meta-description/v1',
        v_observed_at,
        '{"coverage":"complete","customer_origin_read":false,"parser_release":"python-html-parser-v1","schema_version":1}'::jsonb,
        'internal_fixture', p_content_hash
    )
    ON CONFLICT ON CONSTRAINT evidence_records_command_source_key DO NOTHING
    RETURNING created.id, created.observed_at, created.content_hash,
              created.manifest_id INTO v_evidence;

    IF v_evidence.id IS NULL THEN
        SELECT existing.id, existing.observed_at, existing.content_hash,
               existing.manifest_id
          INTO v_evidence
          FROM app.evidence_records AS existing
         WHERE existing.tenant_id = v_authority.tenant_id
           AND existing.site_id = p_requested_site_id
           AND existing.command_id = v_command.id
           AND existing.source_identifier =
               'fixture:local-pilot/missing-meta-description/v1';
        IF NOT FOUND OR v_evidence.content_hash <> p_content_hash
           OR v_evidence.manifest_id <> v_command.manifest_id
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
                NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
                NULL::timestamptz, NULL::timestamptz, NULL::boolean,
                'evidence_conflict'::text;
            RETURN;
        END IF;
        v_reused := true;
    END IF;

    INSERT INTO app.findings AS current_finding (
        tenant_id, site_id, id, first_command_id, latest_command_id, finding_key,
        detector_release_id, resource_locator, title, summary, severity, status,
        first_seen_at, last_seen_at, confidence_class
    )
    VALUES (
        v_authority.tenant_id, p_requested_site_id, p_finding_id, v_command.id,
        v_command.id, 'metadata.meta_description.missing', v_detector_release_id,
        '/fixture/missing-meta-description', 'Missing meta description',
        'The synthetic page fixture does not contain a non-empty meta description.',
        'medium', 'open', v_evidence.observed_at, v_evidence.observed_at,
        'deterministic'
    )
    ON CONFLICT ON CONSTRAINT findings_current_detector_key DO UPDATE
       SET latest_command_id = EXCLUDED.latest_command_id,
           last_seen_at = GREATEST(current_finding.last_seen_at, EXCLUDED.last_seen_at)
    RETURNING current_finding.id, current_finding.first_seen_at,
              current_finding.last_seen_at INTO v_finding;

    INSERT INTO app.finding_evidence (
        tenant_id, site_id, finding_id, evidence_id, relation, linked_at
    )
    VALUES (
        v_authority.tenant_id, p_requested_site_id, v_finding.id,
        v_evidence.id, 'supports', v_evidence.observed_at
    )
    ON CONFLICT DO NOTHING;

    RETURN QUERY SELECT v_finding.id, v_evidence.id, v_command.id,
        v_command.manifest_id, 'metadata.meta_description.missing'::text,
        'Missing meta description'::text,
        'The synthetic page fixture does not contain a non-empty meta description.'::text,
        '/fixture/missing-meta-description'::text, 'medium'::text, 'open'::text,
        'deterministic'::text, 'synthetic_fixture'::text,
        'fixture:local-pilot/missing-meta-description/v1'::text,
        encode(v_evidence.content_hash, 'hex'), v_evidence.observed_at,
        v_finding.first_seen_at, v_finding.last_seen_at, v_reused, 'recorded'::text;
END;
$$;

CREATE FUNCTION control.read_authenticated_findings(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text
)
RETURNS TABLE (
    finding_id uuid,
    evidence_id uuid,
    command_id uuid,
    manifest_id uuid,
    finding_key text,
    title text,
    summary text,
    resource_locator text,
    severity text,
    status text,
    confidence_class text,
    source_kind text,
    source_identifier text,
    content_sha256 text,
    evidence_observed_at timestamptz,
    first_seen_at timestamptz,
    last_seen_at timestamptz,
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
          p_session_hash,
          p_requested_site_id,
          p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean, v_authority.outcome;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT finding.id, evidence.id, finding.latest_command_id, evidence.manifest_id,
           finding.finding_key, finding.title, finding.summary,
           finding.resource_locator, finding.severity, finding.status,
           finding.confidence_class, evidence.source_kind,
           evidence.source_identifier, encode(evidence.content_hash, 'hex'),
           evidence.observed_at, finding.first_seen_at, finding.last_seen_at,
           false, 'found'::text
      FROM app.findings AS finding
      JOIN LATERAL (
          SELECT observed.*
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
     ORDER BY finding.last_seen_at DESC, finding.id
     LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::timestamptz, NULL::timestamptz, NULL::boolean, 'not_found'::text;
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION app.guard_finding_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_authenticated_local_fixture_finding(
    bytea, uuid, text, uuid, uuid, bytea
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_authenticated_findings(bytea, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_authenticated_local_fixture_finding(
    bytea, uuid, text, uuid, uuid, bytea
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.read_authenticated_findings(bytea, uuid, text)
TO signal_identity;
