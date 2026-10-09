ALTER TABLE app.bing_import_generations DROP CONSTRAINT bing_import_generations_kind_check;
ALTER TABLE app.bing_import_generations ADD CONSTRAINT bing_import_generations_kind_check
CHECK (kind IN ('performance', 'own_site_inbound_link_counts',
               'own_site_inbound_link_details', 'page_performance'));
CREATE INDEX bing_page_performance_latest ON app.bing_import_generations
    (tenant_id, site_id, binding_id, imported_at DESC, id DESC)
    WHERE kind = 'page_performance';

CREATE FUNCTION control.record_bing_page_import_generation(
    p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid,
    p_property text, p_kind text, p_rows jsonb, p_coverage jsonb,
    p_response_sha256 bytea, p_egress_operation_id uuid
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_binding record; v_row jsonb; v_field text; v_date text; v_ms numeric;
BEGIN
    IF p_id IS NULL OR p_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_kind IS DISTINCT FROM 'page_performance'
       OR jsonb_typeof(p_rows) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_rows) > 5000
       OR octet_length(p_rows::text) > 2097152
       OR jsonb_typeof(p_coverage) IS DISTINCT FROM 'object'
       OR p_coverage->'complete' IS DISTINCT FROM 'false'::jsonb
       OR p_coverage->>'missing_data' IS DISTINCT FROM 'unknown'
       OR p_coverage->>'source' IS DISTINCT FROM 'bing_webmaster'
       OR p_coverage->>'coverage' IS DISTINCT FROM 'provider_returned_top_pages'
       OR p_coverage->>'date_granularity' IS DISTINCT FROM 'unknown'
       OR p_coverage->>'provider_update_frequency' IS DISTINCT FROM 'weekly'
       OR jsonb_typeof(p_coverage->'dropped_out_of_site_rows') IS DISTINCT FROM 'number'
       OR (p_coverage->>'dropped_out_of_site_rows') !~ '^[0-9]{1,4}$'
       OR (p_coverage->>'dropped_out_of_site_rows')::numeric > 5000
       OR jsonb_array_length(p_rows) + (p_coverage->>'dropped_out_of_site_rows')::numeric > 5000
       OR octet_length(p_coverage::text) > 8192
       OR octet_length(p_response_sha256) IS DISTINCT FROM 32
    THEN RAISE EXCEPTION 'invalid_bing_page_import' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_binding FROM control.current_bing_binding(p_tenant_id, p_site_id);
    IF NOT FOUND OR v_binding.binding_id IS DISTINCT FROM p_binding_id
       OR v_binding.property_resource_name IS DISTINCT FROM p_property
    THEN RETURN 'binding_unavailable'; END IF;
    PERFORM 1 FROM app.bing_binding_events AS bound
     WHERE bound.tenant_id = p_tenant_id AND bound.site_id = p_site_id
       AND bound.binding_id = p_binding_id AND bound.event_kind = 'bound' FOR UPDATE;
    IF EXISTS (SELECT 1 FROM app.bing_binding_events AS restriction
       WHERE restriction.tenant_id = p_tenant_id AND restriction.site_id = p_site_id
         AND restriction.binding_id = p_binding_id AND restriction.event_kind <> 'bound')
    THEN RETURN 'binding_unavailable'; END IF;
    FOR v_row IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
        IF jsonb_typeof(v_row) IS DISTINCT FROM 'object'
           OR NOT (v_row ?& ARRAY['page_url', 'date', 'clicks', 'impressions',
                                 'avg_click_position', 'avg_impression_position'])
           OR v_row - ARRAY['page_url', 'date', 'clicks', 'impressions',
                            'avg_click_position', 'avg_impression_position'] <> '{}'::jsonb
           OR jsonb_typeof(v_row->'page_url') IS DISTINCT FROM 'string'
           OR length(v_row->>'page_url') NOT BETWEEN 1 AND 2048
           OR NOT starts_with(v_row->>'page_url', v_binding.origin || '/')
           OR (v_row->>'page_url') ~ '[^!-~]|[#\\]'
           OR (v_row->>'page_url') ~ '%%([^0-9A-Fa-f]|[0-9A-Fa-f]([^0-9A-Fa-f]|$)|$)'
           OR (v_row->>'page_url') ~* '%%(0[0-9a-f]|1[0-9a-f]|7f)'
           OR jsonb_typeof(v_row->'date') IS DISTINCT FROM 'string'
        THEN RAISE EXCEPTION 'invalid_bing_page_row' USING ERRCODE = '22023'; END IF;
        FOREACH v_field IN ARRAY ARRAY['clicks', 'impressions',
                                      'avg_click_position', 'avg_impression_position'] LOOP
            IF jsonb_typeof(v_row->v_field) IS DISTINCT FROM 'number'
               OR (v_row->>v_field) !~ '^[0-9]{1,19}$'
               OR (v_row->>v_field)::numeric > (CASE WHEN v_field IN ('clicks', 'impressions')
                   THEN 9223372036854775807 ELSE 2147483647 END)
            THEN RAISE EXCEPTION 'invalid_bing_page_metric' USING ERRCODE = '22023'; END IF;
        END LOOP;
        v_date := v_row->>'date';
        IF v_date !~ '^/Date\(-?[0-9]{1,16}[+-](0[0-9]|1[0-3])[0-5][0-9]\)/$'
           AND v_date !~ '^/Date\(-?[0-9]{1,16}[+-]1400\)/$'
        THEN RAISE EXCEPTION 'invalid_bing_page_date' USING ERRCODE = '22023'; END IF;
        v_ms := substring(v_date FROM '^/Date\((-?[0-9]+)')::numeric;
        IF v_ms < -62135596800000 OR v_ms > 253402300799999
        THEN RAISE EXCEPTION 'invalid_bing_page_date' USING ERRCODE = '22023'; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_rows) AS r(value)
               GROUP BY value->>'page_url', value->>'date' HAVING count(*) > 1)
    THEN RAISE EXCEPTION 'duplicate_bing_page_row' USING ERRCODE = '22023'; END IF;
    PERFORM 1 FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.egress_profile = 'bing_api' AND egress.credentialed
       AND egress.method = 'GET' AND egress.state = 'observed'
       AND egress.http_status = 200 AND egress.response_sha256 = p_response_sha256
       AND egress.response_bytes <= 1048576
       AND egress.request_url = 'https://www.bing.com/webmaster/api.svc/json/GetPageStats?siteUrl='
           || replace(replace(replace(p_property, '%%', '%%25'), ':', '%%3A'), '/', '%%2F');
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    INSERT INTO app.bing_import_generations (
        tenant_id, site_id, id, binding_id, property_resource_name,
        kind, rows, coverage, response_sha256, egress_operation_id
    ) VALUES (
        p_tenant_id, p_site_id, p_id, p_binding_id, p_property,
        p_kind, p_rows, p_coverage, p_response_sha256, p_egress_operation_id
    );
    RETURN 'recorded';
END;
$$;

CREATE FUNCTION control.read_bing_page_performance(p_tenant_id uuid, p_site_id uuid)
RETURNS TABLE (generation_id uuid, site_url text, rows jsonb, coverage jsonb, response_sha256 bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_binding record;
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_binding FROM control.current_bing_binding(p_tenant_id, p_site_id);
    IF NOT FOUND THEN RETURN; END IF;
    RETURN QUERY SELECT generation.id, generation.property_resource_name,
        generation.rows, generation.coverage, generation.response_sha256
      FROM app.bing_import_generations AS generation
     WHERE generation.tenant_id = p_tenant_id AND generation.site_id = p_site_id
       AND generation.binding_id = v_binding.binding_id
       AND generation.property_resource_name = v_binding.property_resource_name
       AND generation.kind = 'page_performance'
     ORDER BY generation.imported_at DESC, generation.id DESC LIMIT 1;
END;
$$;

REVOKE ALL ON FUNCTION control.record_bing_page_import_generation(
    uuid, uuid, uuid, uuid, text, text, jsonb, jsonb, bytea, uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_bing_page_performance(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_bing_page_import_generation(
    uuid, uuid, uuid, uuid, text, text, jsonb, jsonb, bytea, uuid
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.read_bing_page_performance(uuid, uuid) TO signal_crawl_ingest;
