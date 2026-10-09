CREATE TABLE app.page_observation_intents (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    command_id uuid NOT NULL,
    manifest_id uuid NOT NULL,
    manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
    verification_id uuid NOT NULL,
    origin text NOT NULL CHECK (
        length(origin) BETWEEN 9 AND 2048
        AND origin ~ '^https://[^/?#[:space:]]+$'
    ),
    requested_by_user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    authentication_level text NOT NULL CHECK (
        authentication_level IN ('primary', 'mfa')
    ),
    membership_authorization_epoch bigint NOT NULL CHECK (
        membership_authorization_epoch > 0
    ),
    site_authorization_epoch bigint NOT NULL CHECK (site_authorization_epoch > 0),
    recovery_generation text NOT NULL CHECK (
        recovery_generation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
    ),
    prepared_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    CONSTRAINT page_observation_intents_request_key
        UNIQUE (tenant_id, requested_by_user_id, idempotency_key),
    FOREIGN KEY (tenant_id, site_id, command_id)
        REFERENCES app.commands (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, verification_id)
        REFERENCES app.site_origin_verifications (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, requested_by_user_id)
        REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX page_observation_intents_site_time
ON app.page_observation_intents (tenant_id, site_id, prepared_at DESC, id);

CREATE TABLE app.page_observation_results (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    intent_id uuid NOT NULL,
    fetch_outcome text NOT NULL CHECK (fetch_outcome IN (
        'observed', 'policy_rejected', 'transport_unavailable',
        'http_rejected', 'invalid_html'
    )),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    media_type text CHECK (length(media_type) BETWEEN 1 AND 100),
    final_url text CHECK (length(final_url) BETWEEN 9 AND 2048),
    resolved_address text CHECK (length(resolved_address) BETWEEN 1 AND 64),
    body_sha256 bytea CHECK (octet_length(body_sha256) = 32),
    title text CHECK (length(title) BETWEEN 1 AND 300),
    heading text CHECK (length(heading) BETWEEN 1 AND 500),
    meta_description text CHECK (length(meta_description) BETWEEN 1 AND 500),
    elapsed_ms integer CHECK (elapsed_ms BETWEEN 0 AND 2147483647),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, site_id, intent_id),
    FOREIGN KEY (tenant_id, site_id, intent_id)
        REFERENCES app.page_observation_intents (tenant_id, site_id, id),
    CHECK (
        (fetch_outcome = 'observed'
         AND http_status BETWEEN 200 AND 299
         AND media_type IN ('text/html', 'application/xhtml+xml')
         AND final_url IS NOT NULL
         AND resolved_address IS NOT NULL
         AND body_sha256 IS NOT NULL
         AND elapsed_ms IS NOT NULL)
        OR
        (fetch_outcome <> 'observed'
         AND title IS NULL
         AND heading IS NULL
         AND meta_description IS NULL)
    )
);
CREATE INDEX page_observation_results_site_time
ON app.page_observation_results (tenant_id, site_id, observed_at DESC, intent_id);

CREATE TRIGGER page_observation_intents_immutable
BEFORE UPDATE OR DELETE ON app.page_observation_intents
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER page_observation_results_immutable
BEFORE UPDATE OR DELETE ON app.page_observation_results
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.page_observation_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.page_observation_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY page_observation_intents_scope ON app.page_observation_intents
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.page_observation_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.page_observation_results FORCE ROW LEVEL SECURITY;
CREATE POLICY page_observation_results_scope ON app.page_observation_results
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.evidence_records
    DROP CONSTRAINT evidence_records_source_kind_check,
    DROP CONSTRAINT evidence_records_source_identifier_check,
    DROP CONSTRAINT evidence_records_quality_check,
    DROP CONSTRAINT evidence_records_rights_status_check,
    ADD COLUMN observation_intent_id uuid,
    ADD COLUMN facts jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD CONSTRAINT evidence_records_source_kind_check CHECK (
        source_kind IN ('synthetic_fixture', 'verified_origin')
    ),
    ADD CONSTRAINT evidence_records_source_identifier_check CHECK (
        (source_kind = 'synthetic_fixture'
         AND source_identifier = 'fixture:local-pilot/missing-meta-description/v1')
        OR
        (source_kind = 'verified_origin'
         AND length(source_identifier) BETWEEN 9 AND 2048
         AND source_identifier ~ '^https://[^#[:space:]]+$')
    ),
    ADD CONSTRAINT evidence_records_quality_check CHECK (
        (source_kind = 'synthetic_fixture'
         AND quality = '{"coverage":"complete","customer_origin_read":false,"parser_release":"python-html-parser-v1","schema_version":1}'::jsonb)
        OR
        (source_kind = 'verified_origin'
         AND quality = '{"coverage":"single_homepage","customer_origin_read":true,"parser_release":"python-html-parser-v2","schema_version":1}'::jsonb)
    ),
    ADD CONSTRAINT evidence_records_rights_status_check CHECK (
        (source_kind = 'synthetic_fixture' AND rights_status = 'internal_fixture')
        OR (source_kind = 'verified_origin' AND rights_status = 'verified_owner')
    ),
    ADD CONSTRAINT evidence_records_facts_check CHECK (
        jsonb_typeof(facts) = 'object'
        AND (
            (source_kind = 'synthetic_fixture' AND facts = '{}'::jsonb)
            OR
            (source_kind = 'verified_origin'
             AND facts ?& ARRAY['http_status','media_type','final_url','title','heading','meta_description'])
        )
    ),
    ADD CONSTRAINT evidence_records_observation_intent_fk
        FOREIGN KEY (tenant_id, site_id, observation_intent_id)
        REFERENCES app.page_observation_intents (tenant_id, site_id, id),
    ADD CONSTRAINT evidence_records_observation_source_check CHECK (
        (source_kind = 'synthetic_fixture' AND observation_intent_id IS NULL)
        OR (source_kind = 'verified_origin' AND observation_intent_id IS NOT NULL)
    );
ALTER TABLE app.findings
    DROP CONSTRAINT findings_resource_locator_check,
    ADD CONSTRAINT findings_resource_locator_check CHECK (
        resource_locator = '/fixture/missing-meta-description'
        OR (
            length(resource_locator) BETWEEN 9 AND 2048
            AND resource_locator ~ '^https://[^#[:space:]]+$'
        )
    );

REVOKE ALL ON app.page_observation_intents, app.page_observation_results
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.prepare_authenticated_page_observation(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_intent_id uuid,
    p_idempotency_key uuid,
    p_request_hash bytea
)
RETURNS TABLE (
    intent_id uuid,
    origin text,
    command_id uuid,
    manifest_id uuid,
    manifest_sha256 text,
    verification_id uuid,
    prepared_at timestamptz,
    replayed boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_existing record;
    v_verification record;
    v_command record;
    v_prepared_at timestamptz := transaction_timestamp();
BEGIN
    IF p_intent_id IS NULL OR p_idempotency_key IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
    THEN
        RAISE EXCEPTION 'invalid_page_observation_input' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash, p_requested_site_id, p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::text, NULL::uuid, NULL::timestamptz, NULL::boolean,
            v_authority.outcome;
        RETURN;
    END IF;

    SELECT existing.id, existing.site_id, existing.origin, existing.command_id, existing.manifest_id,
           existing.manifest_sha256, existing.verification_id,
           existing.prepared_at, existing.request_hash
      INTO v_existing
      FROM app.page_observation_intents AS existing
     WHERE existing.tenant_id = v_authority.tenant_id
       AND existing.requested_by_user_id = v_authority.user_id
       AND existing.idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.request_hash <> p_request_hash
           OR v_existing.site_id IS DISTINCT FROM p_requested_site_id
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
                NULL::text, NULL::uuid, NULL::timestamptz, NULL::boolean,
                'request_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.origin,
            v_existing.command_id, v_existing.manifest_id,
            encode(v_existing.manifest_sha256, 'hex'),
            v_existing.verification_id, v_existing.prepared_at, true,
            'prepared'::text;
        RETURN;
    END IF;

    SELECT verification.id, verification.origin
      INTO v_verification
      FROM app.sites AS site
      JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = site.tenant_id
       AND verification.site_id = site.id
       AND verification.origin = site.primary_origin
      JOIN control.public_origin_claims AS claim
        ON claim.origin = verification.origin
       AND claim.tenant_id = verification.tenant_id
       AND claim.site_id = verification.site_id
       AND claim.verification_id = verification.id
     WHERE site.tenant_id = v_authority.tenant_id
       AND site.id = p_requested_site_id
       AND site.ownership_status = 'verified'
       AND verification.recheck_at > transaction_timestamp()
       AND claim.recheck_at > transaction_timestamp()
     ORDER BY verification.verified_at DESC, verification.id DESC
     LIMIT 1
     FOR KEY SHARE OF site, verification, claim;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::text, NULL::uuid, NULL::timestamptz, NULL::boolean,
            'origin_not_verified'::text;
        RETURN;
    END IF;

    SELECT command.id,
           (command.result_reference->>'manifest_id')::uuid AS manifest_id,
           decode(command.result_reference->>'manifest_sha256', 'hex') AS manifest_sha256
      INTO v_command
      FROM app.commands AS command
     WHERE command.tenant_id = v_authority.tenant_id
       AND command.site_id = p_requested_site_id
       AND command.actor_user_id = v_authority.user_id
       AND command.route_key = 'api.site.snapshot'
       AND command.status = 'succeeded'
       AND command.result_reference->>'kind' = 'crawl_manifest'
     ORDER BY command.accepted_at DESC, command.id DESC
     LIMIT 1
     FOR KEY SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::text, NULL::uuid, NULL::timestamptz, NULL::boolean,
            'audit_not_ready'::text;
        RETURN;
    END IF;

    INSERT INTO app.page_observation_intents (
        tenant_id, site_id, id, command_id, manifest_id, manifest_sha256,
        verification_id, origin, requested_by_user_id, idempotency_key,
        request_hash, authentication_level, membership_authorization_epoch,
        site_authorization_epoch, recovery_generation, prepared_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_intent_id, v_command.id,
        v_command.manifest_id, v_command.manifest_sha256, v_verification.id,
        v_verification.origin, v_authority.user_id, p_idempotency_key,
        p_request_hash, v_authority.authentication_level,
        v_authority.membership_epoch, v_authority.site_authorization_epoch,
        p_current_recovery_generation, v_prepared_at
    )
    ON CONFLICT ON CONSTRAINT page_observation_intents_request_key DO NOTHING;

    SELECT existing.id, existing.site_id, existing.origin, existing.command_id,
           existing.manifest_id, existing.manifest_sha256,
           existing.verification_id, existing.prepared_at, existing.request_hash
      INTO v_existing
      FROM app.page_observation_intents AS existing
     WHERE existing.tenant_id = v_authority.tenant_id
       AND existing.requested_by_user_id = v_authority.user_id
       AND existing.idempotency_key = p_idempotency_key;
    IF NOT FOUND OR v_existing.request_hash <> p_request_hash
       OR v_existing.site_id <> p_requested_site_id
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::uuid,
            NULL::text, NULL::uuid, NULL::timestamptz, NULL::boolean,
            'request_conflict'::text;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_existing.id, v_existing.origin,
        v_existing.command_id, v_existing.manifest_id,
        encode(v_existing.manifest_sha256, 'hex'), v_existing.verification_id,
        v_existing.prepared_at, v_existing.id <> p_intent_id, 'prepared'::text;
END;
$$;

CREATE FUNCTION control.record_authenticated_page_observation(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text,
    p_intent_id uuid,
    p_evidence_id uuid,
    p_finding_id uuid,
    p_fetch_outcome text,
    p_http_status integer,
    p_media_type text,
    p_final_url text,
    p_resolved_address text,
    p_body_sha256 bytea,
    p_title text,
    p_heading text,
    p_meta_description text,
    p_elapsed_ms integer
)
RETURNS TABLE (
    observation_intent_id uuid,
    evidence_id uuid,
    finding_id uuid,
    command_id uuid,
    manifest_id uuid,
    origin text,
    final_url text,
    http_status integer,
    media_type text,
    title text,
    heading text,
    meta_description text,
    body_sha256 text,
    observed_at timestamptz,
    fetch_outcome text,
    reused boolean,
    outcome text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_authority record;
    v_intent record;
    v_existing record;
    v_evidence record;
    v_finding_id uuid;
    v_observed_at timestamptz := transaction_timestamp();
    v_reused boolean := false;
    v_detector_release_id constant uuid := '95eb3b3a-2318-4c31-a733-3c5ee4d17dc5';
BEGIN
    IF p_intent_id IS NULL OR p_evidence_id IS NULL OR p_finding_id IS NULL
       OR p_evidence_id = p_finding_id
       OR p_fetch_outcome NOT IN (
           'observed', 'policy_rejected', 'transport_unavailable',
           'http_rejected', 'invalid_html'
       )
       OR p_elapsed_ms IS NULL OR p_elapsed_ms < 0
       OR (p_fetch_outcome = 'observed' AND (
           p_http_status NOT BETWEEN 200 AND 299
           OR p_media_type NOT IN ('text/html', 'application/xhtml+xml')
           OR p_final_url IS NULL OR length(p_final_url) NOT BETWEEN 9 AND 2048
           OR p_resolved_address IS NULL OR length(p_resolved_address) NOT BETWEEN 1 AND 64
           OR p_body_sha256 IS NULL OR octet_length(p_body_sha256) <> 32
           OR (p_title IS NOT NULL AND length(p_title) NOT BETWEEN 1 AND 300)
           OR (p_heading IS NOT NULL AND length(p_heading) NOT BETWEEN 1 AND 500)
           OR (p_meta_description IS NOT NULL AND length(p_meta_description) NOT BETWEEN 1 AND 500)
       ))
       OR (p_fetch_outcome <> 'observed' AND (
           p_title IS NOT NULL OR p_heading IS NOT NULL OR p_meta_description IS NOT NULL
       ))
    THEN
        RAISE EXCEPTION 'invalid_page_observation_result' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_authority
      FROM control.resolve_snapshot_authority(
          p_session_hash, p_requested_site_id, p_current_recovery_generation
      );
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::integer, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::text, NULL::boolean, v_authority.outcome;
        RETURN;
    END IF;

    SELECT intent.* INTO v_intent
      FROM app.page_observation_intents AS intent
      JOIN app.sites AS site
        ON site.tenant_id = intent.tenant_id AND site.id = intent.site_id
      JOIN app.site_origin_verifications AS verification
        ON verification.tenant_id = intent.tenant_id
       AND verification.site_id = intent.site_id
       AND verification.id = intent.verification_id
      JOIN control.public_origin_claims AS claim
        ON claim.origin = intent.origin
       AND claim.tenant_id = intent.tenant_id
       AND claim.site_id = intent.site_id
       AND claim.verification_id = intent.verification_id
     WHERE intent.tenant_id = v_authority.tenant_id
       AND intent.site_id = p_requested_site_id
       AND intent.id = p_intent_id
       AND intent.requested_by_user_id = v_authority.user_id
       AND intent.membership_authorization_epoch = v_authority.membership_epoch
       AND intent.site_authorization_epoch = v_authority.site_authorization_epoch
       AND intent.recovery_generation = p_current_recovery_generation
       AND site.primary_origin = intent.origin
       AND site.ownership_status = 'verified'
       AND verification.recheck_at > transaction_timestamp()
       AND claim.recheck_at > transaction_timestamp()
     FOR KEY SHARE OF intent, site, verification, claim;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::integer, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::text, NULL::boolean, 'observation_not_authorized'::text;
        RETURN;
    END IF;

    IF p_fetch_outcome = 'observed' AND NOT (
        p_final_url = v_intent.origin OR p_final_url LIKE v_intent.origin || '/%%'
    ) THEN
        RAISE EXCEPTION 'invalid_page_observation_origin' USING ERRCODE = '22023';
    END IF;

    INSERT INTO app.page_observation_results AS result (
        tenant_id, site_id, intent_id, fetch_outcome, http_status, media_type,
        final_url, resolved_address, body_sha256, title, heading,
        meta_description, elapsed_ms, observed_at
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_intent_id, p_fetch_outcome,
        p_http_status, p_media_type, p_final_url, p_resolved_address,
        p_body_sha256, p_title, p_heading, p_meta_description, p_elapsed_ms,
        v_observed_at
    )
    ON CONFLICT DO NOTHING
    RETURNING result.* INTO v_existing;

    IF v_existing.intent_id IS NULL THEN
        SELECT result.* INTO v_existing
          FROM app.page_observation_results AS result
         WHERE result.tenant_id = v_authority.tenant_id
           AND result.site_id = p_requested_site_id
           AND result.intent_id = p_intent_id;
        IF NOT FOUND
           OR v_existing.fetch_outcome IS DISTINCT FROM p_fetch_outcome
           OR v_existing.http_status IS DISTINCT FROM p_http_status
           OR v_existing.media_type IS DISTINCT FROM p_media_type
           OR v_existing.final_url IS DISTINCT FROM p_final_url
           OR v_existing.resolved_address IS DISTINCT FROM p_resolved_address
           OR v_existing.body_sha256 IS DISTINCT FROM p_body_sha256
           OR v_existing.title IS DISTINCT FROM p_title
           OR v_existing.heading IS DISTINCT FROM p_heading
           OR v_existing.meta_description IS DISTINCT FROM p_meta_description
           OR v_existing.elapsed_ms IS DISTINCT FROM p_elapsed_ms
        THEN
            RETURN QUERY SELECT p_intent_id, NULL::uuid, NULL::uuid,
                v_intent.command_id, v_intent.manifest_id, v_intent.origin,
                NULL::text, NULL::integer, NULL::text, NULL::text, NULL::text,
                NULL::text, NULL::text, NULL::timestamptz, p_fetch_outcome,
                NULL::boolean, 'observation_conflict'::text;
            RETURN;
        END IF;
        v_reused := true;
    END IF;

    IF p_fetch_outcome <> 'observed' THEN
        RETURN QUERY SELECT p_intent_id, NULL::uuid, NULL::uuid,
            v_intent.command_id, v_intent.manifest_id, v_intent.origin,
            v_existing.final_url, v_existing.http_status, v_existing.media_type,
            NULL::text, NULL::text, NULL::text,
            CASE WHEN v_existing.body_sha256 IS NULL THEN NULL::text
                 ELSE encode(v_existing.body_sha256, 'hex') END,
            v_existing.observed_at, v_existing.fetch_outcome, v_reused,
            'recorded'::text;
        RETURN;
    END IF;

    INSERT INTO app.evidence_records AS evidence (
        tenant_id, site_id, id, command_id, manifest_id, manifest_sha256,
        evidence_type, source_kind, source_identifier, observed_at, quality,
        rights_status, content_hash, observation_intent_id, facts
    ) VALUES (
        v_authority.tenant_id, p_requested_site_id, p_evidence_id,
        v_intent.command_id, v_intent.manifest_id, v_intent.manifest_sha256,
        'page_metadata', 'verified_origin', p_final_url, v_existing.observed_at,
        '{"coverage":"single_homepage","customer_origin_read":true,"parser_release":"python-html-parser-v2","schema_version":1}'::jsonb,
        'verified_owner', p_body_sha256, p_intent_id,
        jsonb_build_object(
            'http_status', p_http_status, 'media_type', p_media_type,
            'final_url', p_final_url, 'title', p_title, 'heading', p_heading,
            'meta_description', p_meta_description
        )
    )
    ON CONFLICT ON CONSTRAINT evidence_records_command_source_key DO NOTHING
    RETURNING evidence.id, evidence.content_hash INTO v_evidence;
    IF v_evidence.id IS NULL THEN
        SELECT evidence.id, evidence.content_hash INTO v_evidence
          FROM app.evidence_records AS evidence
         WHERE evidence.tenant_id = v_authority.tenant_id
           AND evidence.site_id = p_requested_site_id
           AND evidence.command_id = v_intent.command_id
           AND evidence.source_identifier = p_final_url;
        IF NOT FOUND OR v_evidence.content_hash <> p_body_sha256 THEN
            RETURN QUERY SELECT p_intent_id, NULL::uuid, NULL::uuid,
                v_intent.command_id, v_intent.manifest_id, v_intent.origin,
                p_final_url, p_http_status, p_media_type, p_title, p_heading,
                p_meta_description, encode(p_body_sha256, 'hex'),
                v_existing.observed_at, p_fetch_outcome, v_reused,
                'evidence_conflict'::text;
            RETURN;
        END IF;
    END IF;

    IF p_meta_description IS NULL THEN
        INSERT INTO app.findings AS finding (
            tenant_id, site_id, id, first_command_id, latest_command_id,
            finding_key, detector_release_id, resource_locator, title, summary,
            severity, status, first_seen_at, last_seen_at, confidence_class
        ) VALUES (
            v_authority.tenant_id, p_requested_site_id, p_finding_id,
            v_intent.command_id, v_intent.command_id,
            'metadata.meta_description.missing', v_detector_release_id,
            p_final_url, 'Missing meta description',
            'The verified homepage does not contain a non-empty meta description.',
            'medium', 'open', v_existing.observed_at, v_existing.observed_at,
            'deterministic'
        )
        ON CONFLICT ON CONSTRAINT findings_current_detector_key DO UPDATE
           SET latest_command_id = EXCLUDED.latest_command_id,
               last_seen_at = GREATEST(finding.last_seen_at, EXCLUDED.last_seen_at)
        RETURNING finding.id INTO v_finding_id;

        INSERT INTO app.finding_evidence (
            tenant_id, site_id, finding_id, evidence_id, relation, linked_at
        ) VALUES (
            v_authority.tenant_id, p_requested_site_id, v_finding_id,
            v_evidence.id, 'supports', v_existing.observed_at
        ) ON CONFLICT DO NOTHING;
    END IF;

    RETURN QUERY SELECT p_intent_id, v_evidence.id, v_finding_id,
        v_intent.command_id, v_intent.manifest_id, v_intent.origin,
        p_final_url, p_http_status, p_media_type, p_title, p_heading,
        p_meta_description, encode(p_body_sha256, 'hex'), v_existing.observed_at,
        p_fetch_outcome, v_reused, 'recorded'::text;
END;
$$;

CREATE FUNCTION control.read_latest_authenticated_page_observation(
    p_session_hash bytea,
    p_requested_site_id uuid,
    p_current_recovery_generation text
)
RETURNS TABLE (
    observation_intent_id uuid,
    evidence_id uuid,
    finding_id uuid,
    command_id uuid,
    manifest_id uuid,
    origin text,
    final_url text,
    http_status integer,
    media_type text,
    title text,
    heading text,
    meta_description text,
    body_sha256 text,
    observed_at timestamptz,
    fetch_outcome text,
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
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::integer, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::text, NULL::boolean, v_authority.outcome;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT intent.id, evidence.id, finding.id, intent.command_id,
           intent.manifest_id, intent.origin, result.final_url,
           result.http_status, result.media_type, result.title, result.heading,
           result.meta_description, encode(result.body_sha256, 'hex'),
           result.observed_at, result.fetch_outcome, false, 'found'::text
      FROM app.page_observation_results AS result
      JOIN app.page_observation_intents AS intent
        ON intent.tenant_id = result.tenant_id
       AND intent.site_id = result.site_id
       AND intent.id = result.intent_id
      LEFT JOIN app.evidence_records AS evidence
        ON evidence.tenant_id = result.tenant_id
       AND evidence.site_id = result.site_id
       AND evidence.observation_intent_id = result.intent_id
      LEFT JOIN app.finding_evidence AS link
        ON link.tenant_id = evidence.tenant_id
       AND link.site_id = evidence.site_id
       AND link.evidence_id = evidence.id
       AND link.relation = 'supports'
      LEFT JOIN app.findings AS finding
        ON finding.tenant_id = link.tenant_id
       AND finding.site_id = link.site_id
       AND finding.id = link.finding_id
     WHERE result.tenant_id = v_authority.tenant_id
       AND result.site_id = p_requested_site_id
       AND result.fetch_outcome = 'observed'
     ORDER BY result.observed_at DESC, result.intent_id DESC
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
            NULL::uuid, NULL::text, NULL::text, NULL::integer, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
            NULL::text, NULL::boolean, 'not_found'::text;
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION control.prepare_authenticated_page_observation(
    bytea, uuid, text, uuid, uuid, bytea
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_authenticated_page_observation(
    bytea, uuid, text, uuid, uuid, uuid, text, integer, text, text, text,
    bytea, text, text, text, integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_latest_authenticated_page_observation(
    bytea, uuid, text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_authenticated_page_observation(
    bytea, uuid, text, uuid, uuid, bytea
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.record_authenticated_page_observation(
    bytea, uuid, text, uuid, uuid, uuid, text, integer, text, text, text,
    bytea, text, text, text, integer
) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.read_latest_authenticated_page_observation(
    bytea, uuid, text
) TO signal_identity;
