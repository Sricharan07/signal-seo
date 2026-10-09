CREATE TABLE app.crawl_audit_reports (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    manifest_id uuid NOT NULL,
    manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
    detector_release_id uuid NOT NULL,
    findings jsonb NOT NULL CHECK (
        jsonb_typeof(findings) = 'array' AND jsonb_array_length(findings) <= 10000
    ),
    coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage) = 'object'),
    input_count integer NOT NULL CHECK (input_count BETWEEN 1 AND 1000),
    finding_count integer NOT NULL CHECK (finding_count BETWEEN 0 AND 10000),
    analyzed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, manifest_id, detector_release_id),
    FOREIGN KEY (tenant_id, site_id, crawl_run_id)
        REFERENCES app.crawl_runs (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, manifest_id)
        REFERENCES app.crawl_manifests (tenant_id, site_id, id),
    CHECK (finding_count = jsonb_array_length(findings))
);

CREATE TRIGGER crawl_audit_reports_immutable BEFORE UPDATE OR DELETE
ON app.crawl_audit_reports FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.crawl_audit_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.crawl_audit_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY crawl_audit_report_scope ON app.crawl_audit_reports
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION control.load_crawl_audit_inputs(
    p_tenant_id uuid, p_site_id uuid, p_manifest_id uuid
)
RETURNS TABLE (
    crawl_run_id uuid, manifest_sha256 bytea, manifest_coverage text,
    discovered_count integer, frontier_id uuid, url_id uuid, fetch_url text,
    depth integer, discovered_from_url_id uuid, settlement_id uuid,
    terminal_state text, http_status integer, robots_snapshot_id uuid,
    page_record_id uuid, title text, meta_description text, canonical_url text,
    headings jsonb, structured_data_types jsonb, internal_links jsonb,
    parse_error_count integer, output_truncated boolean, body_sha256 bytea
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY
    SELECT run.id, manifest.manifest_sha256, manifest.coverage,
           manifest.discovered_count, frontier.id, url.id, url.fetch_url,
           frontier.depth, frontier.discovered_from_url_id, settlement.id,
           settlement.terminal_state, operation.http_status,
           settlement.robots_snapshot_id, page.id, page.title,
           page.meta_description, page.canonical_url, page.headings,
           page.structured_data_types, page.internal_links,
           page.parse_error_count, page.output_truncated, page.body_sha256
      FROM app.crawl_manifests AS manifest
      JOIN app.crawl_runs AS run
        ON run.tenant_id = manifest.tenant_id AND run.site_id = manifest.site_id
       AND run.id = manifest.crawl_run_id AND run.status = 'completed'
      JOIN app.crawl_frontier AS frontier
        ON frontier.tenant_id = run.tenant_id AND frontier.site_id = run.site_id
       AND frontier.crawl_run_id = run.id
      JOIN app.urls AS url
        ON url.tenant_id = frontier.tenant_id AND url.site_id = frontier.site_id
       AND url.id = frontier.url_id
      JOIN app.crawl_frontier_settlements AS settlement
        ON settlement.tenant_id = frontier.tenant_id
       AND settlement.site_id = frontier.site_id
       AND settlement.crawl_run_id = frontier.crawl_run_id
       AND settlement.frontier_id = frontier.id
      LEFT JOIN app.egress_operations AS operation
        ON operation.tenant_id = settlement.tenant_id
       AND operation.site_id = settlement.site_id
       AND operation.crawl_run_id = settlement.crawl_run_id
       AND operation.id = settlement.egress_operation_id
      LEFT JOIN app.crawl_page_records AS page
        ON page.tenant_id = frontier.tenant_id AND page.site_id = frontier.site_id
       AND page.crawl_run_id = frontier.crawl_run_id
       AND page.frontier_id = frontier.id
     WHERE manifest.tenant_id = p_tenant_id AND manifest.site_id = p_site_id
       AND manifest.id = p_manifest_id
     ORDER BY frontier.id;
END;
$$;

CREATE FUNCTION control.record_crawl_audit_report(
    p_tenant_id uuid, p_site_id uuid, p_manifest_id uuid, p_report_id uuid,
    p_detector_release_id uuid, p_manifest_sha256 bytea,
    p_findings jsonb, p_coverage jsonb, p_input_count integer
)
RETURNS TABLE (report_id uuid, duplicate boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_manifest app.crawl_manifests%%ROWTYPE;
        v_existing app.crawl_audit_reports%%ROWTYPE;
        v_item jsonb; v_source_id uuid; v_source_kind text; v_locator text;
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_manifest FROM app.crawl_manifests AS manifest
     WHERE manifest.tenant_id = p_tenant_id AND manifest.site_id = p_site_id
       AND manifest.id = p_manifest_id FOR SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'manifest_unavailable'::text;
        RETURN;
    END IF;
    IF p_manifest_sha256 IS DISTINCT FROM v_manifest.manifest_sha256
       OR p_input_count IS DISTINCT FROM v_manifest.discovered_count
       OR p_detector_release_id IS NULL OR p_report_id IS NULL
       OR p_findings IS NULL OR jsonb_typeof(p_findings) <> 'array'
       OR jsonb_array_length(p_findings) > 10000
       OR octet_length(p_findings::text) > 8388608
       OR p_coverage IS NULL OR p_coverage <> jsonb_build_object(
            'pages', CASE WHEN v_manifest.coverage = 'complete' THEN 'complete'
                          ELSE 'partial' END,
            'metadata', CASE WHEN NOT EXISTS (
                SELECT 1 FROM app.crawl_page_records AS page
                 WHERE page.tenant_id = p_tenant_id AND page.site_id = p_site_id
                   AND page.crawl_run_id = v_manifest.crawl_run_id
            ) THEN 'not_assessed' WHEN EXISTS (
                SELECT 1 FROM app.crawl_page_records AS page
                 WHERE page.tenant_id = p_tenant_id AND page.site_id = p_site_id
                   AND page.crawl_run_id = v_manifest.crawl_run_id
                   AND page.output_truncated
            ) THEN 'partial' ELSE 'observed' END,
            'sitemaps', 'not_assessed',
            'robots', CASE WHEN EXISTS (
                SELECT 1 FROM app.crawl_frontier_settlements AS settlement
                 WHERE settlement.tenant_id = p_tenant_id
                   AND settlement.site_id = p_site_id
                   AND settlement.crawl_run_id = v_manifest.crawl_run_id
                   AND settlement.terminal_state = 'robots_denied'
            ) THEN 'partial' ELSE 'observed' END
       )
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'invalid_report'::text;
        RETURN;
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_findings) LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 8
           OR NOT (v_item ?& ARRAY['id', 'key', 'title', 'summary', 'severity',
                                    'resource_locator', 'source_kind', 'source_id'])
           OR jsonb_typeof(v_item->'id') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item->'key') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item->'title') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item->'summary') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item->'severity') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item->'resource_locator') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item->'source_kind') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_item->'source_id') IS DISTINCT FROM 'string'
           OR (v_item->>'key') !~ '^[a-z][a-z0-9_.-]{0,127}$'
           OR length(v_item->>'title') NOT BETWEEN 1 AND 160
           OR length(v_item->>'summary') NOT BETWEEN 1 AND 500
           OR v_item->>'severity' NOT IN ('low', 'medium', 'high')
           OR length(v_item->>'resource_locator') NOT BETWEEN 1 AND 2048
           OR v_item->>'source_kind' NOT IN ('page', 'settlement')
        THEN
            RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'invalid_finding'::text;
            RETURN;
        END IF;
        BEGIN
            v_source_id := (v_item->>'source_id')::uuid;
            PERFORM (v_item->>'id')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'invalid_finding'::text;
            RETURN;
        END;
        v_source_kind := v_item->>'source_kind';
        v_locator := v_item->>'resource_locator';
        IF v_source_kind = 'page' AND NOT EXISTS (
            SELECT 1 FROM app.crawl_page_records AS page
            JOIN app.crawl_frontier AS frontier
              ON frontier.tenant_id = page.tenant_id
             AND frontier.site_id = page.site_id
             AND frontier.crawl_run_id = page.crawl_run_id
             AND frontier.id = page.frontier_id
            JOIN app.urls AS url ON url.tenant_id = frontier.tenant_id
             AND url.site_id = frontier.site_id AND url.id = frontier.url_id
            WHERE page.tenant_id = p_tenant_id AND page.site_id = p_site_id
              AND page.crawl_run_id = v_manifest.crawl_run_id
              AND page.id = v_source_id AND url.fetch_url = v_locator
        ) THEN
            RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'invalid_evidence'::text;
            RETURN;
        END IF;
        IF v_source_kind = 'settlement' AND NOT EXISTS (
            SELECT 1 FROM app.crawl_frontier_settlements AS settlement
            JOIN app.crawl_frontier AS frontier
              ON frontier.tenant_id = settlement.tenant_id
             AND frontier.site_id = settlement.site_id
             AND frontier.crawl_run_id = settlement.crawl_run_id
             AND frontier.id = settlement.frontier_id
            JOIN app.urls AS url ON url.tenant_id = frontier.tenant_id
             AND url.site_id = frontier.site_id AND url.id = frontier.url_id
            WHERE settlement.tenant_id = p_tenant_id AND settlement.site_id = p_site_id
              AND settlement.crawl_run_id = v_manifest.crawl_run_id
              AND settlement.id = v_source_id AND url.fetch_url = v_locator
        ) THEN
            RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'invalid_evidence'::text;
            RETURN;
        END IF;
    END LOOP;
    IF (SELECT count(DISTINCT value->>'id') FROM jsonb_array_elements(p_findings)) <>
       jsonb_array_length(p_findings)
       OR (SELECT count(DISTINCT jsonb_build_array(value->>'source_id', value->>'key'))
             FROM jsonb_array_elements(p_findings)) <> jsonb_array_length(p_findings)
    THEN
        RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'invalid_finding'::text;
        RETURN;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'crawl-audit-report:' || p_tenant_id::text || ':' || p_manifest_id::text ||
        ':' || p_detector_release_id::text, 0
    ));
    SELECT * INTO v_existing FROM app.crawl_audit_reports AS report
     WHERE report.tenant_id = p_tenant_id AND report.site_id = p_site_id
       AND report.manifest_id = p_manifest_id
       AND report.detector_release_id = p_detector_release_id FOR SHARE;
    IF FOUND THEN
        IF v_existing.id = p_report_id AND v_existing.manifest_sha256 = p_manifest_sha256
           AND v_existing.findings = p_findings AND v_existing.coverage = p_coverage
           AND v_existing.input_count = p_input_count
        THEN
            RETURN QUERY SELECT v_existing.id, true, 'recorded'::text;
        ELSE
            RETURN QUERY SELECT NULL::uuid, NULL::boolean, 'report_conflict'::text;
        END IF;
        RETURN;
    END IF;
    INSERT INTO app.crawl_audit_reports (
        tenant_id, site_id, id, crawl_run_id, manifest_id, manifest_sha256,
        detector_release_id, findings, coverage, input_count, finding_count,
        analyzed_at
    ) VALUES (
        p_tenant_id, p_site_id, p_report_id, v_manifest.crawl_run_id,
        p_manifest_id, p_manifest_sha256, p_detector_release_id, p_findings,
        p_coverage, p_input_count, jsonb_array_length(p_findings),
        clock_timestamp()
    );
    RETURN QUERY SELECT p_report_id, false, 'recorded'::text;
END;
$$;

REVOKE ALL ON FUNCTION control.load_crawl_audit_inputs(uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.record_crawl_audit_report(
    uuid, uuid, uuid, uuid, uuid, bytea, jsonb, jsonb, integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.load_crawl_audit_inputs(uuid, uuid, uuid)
TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.record_crawl_audit_report(
    uuid, uuid, uuid, uuid, uuid, bytea, jsonb, jsonb, integer
) TO signal_crawl_ingest;
