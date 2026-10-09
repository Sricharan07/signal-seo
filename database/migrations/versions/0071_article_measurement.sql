-- A new page has no pre-change window, not a zero-valued synthetic baseline.
CREATE FUNCTION control.new_page_measurement_baseline() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT jsonb_build_object('state','new_page','reason','NO_PRE_CHANGE_WINDOW',
    'gsc_page',s,'bing_site_context',s,'bing_page',s)
FROM (SELECT jsonb_build_object('state','unavailable','reason','NO_PRE_CHANGE_WINDOW',
    'generation_id',NULL,'coverage',NULL,'metrics',NULL) AS s) sources;
$$;
REVOKE ALL ON FUNCTION control.new_page_measurement_baseline() FROM PUBLIC;

ALTER TABLE app.change_measurement_plans
    ALTER COLUMN baseline_start DROP NOT NULL,
    ALTER COLUMN baseline_end DROP NOT NULL,
    ADD CONSTRAINT measurement_baseline_window CHECK (
        (baseline_start IS NOT NULL AND baseline_end IS NOT NULL
            AND baseline->>'state' IS DISTINCT FROM 'new_page') OR
        (baseline_start IS NULL AND baseline_end IS NULL
            AND baseline=control.new_page_measurement_baseline()));

-- Delegate all technical receipts to the original 0094 function unchanged.
ALTER FUNCTION control.capture_change_measurement_baseline(app.github_delivery_receipts)
    RENAME TO capture_technical_change_measurement_baseline;
CREATE FUNCTION control.capture_change_measurement_baseline(p_receipt app.github_delivery_receipts)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_doc jsonb; v_operation app.github_pr_operations%%ROWTYPE;
    v_manifest jsonb; v_page text; v_kind text; v_live timestamptz;
    v_before date; v_after date; h integer; v_baseline jsonb;
BEGIN
    v_doc:=convert_from(p_receipt.canonical_receipt,'UTF8')::jsonb;
    IF v_doc->>'outcome' IS DISTINCT FROM 'verified' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_receipt.tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_receipt.site_id::text,true);
    SELECT * INTO v_operation FROM app.github_pr_operations
        WHERE tenant_id=p_receipt.tenant_id AND site_id=p_receipt.site_id
          AND id=p_receipt.operation_id;
    IF v_operation.authority_kind IS DISTINCT FROM 'owner_editorial' THEN
        PERFORM control.capture_technical_change_measurement_baseline(p_receipt);
        RETURN;
    END IF;
    v_manifest:=control.pr_delivery_manifest(p_receipt.tenant_id,p_receipt.site_id,
        v_operation.candidate_revision_id,v_operation.authority_kind);
    v_page:=v_manifest->'evidence'->>'page_url';
    v_kind:=v_manifest->>'work_type';
    SELECT finished_at INTO v_live FROM app.egress_operations
        WHERE tenant_id=p_receipt.tenant_id AND site_id=p_receipt.site_id
          AND id=(v_doc->>'live_egress_operation_id')::uuid;
    IF v_page IS NULL OR v_live IS NULL OR v_kind IS NULL
        OR v_kind NOT IN ('new_article','content_refresh') THEN
        RAISE EXCEPTION 'measurement_evidence_missing';
    END IF;
    v_before:=(v_operation.created_at AT TIME ZONE 'America/Los_Angeles')::date-1;
    v_after:=(v_live AT TIME ZONE 'America/Los_Angeles')::date+1;
    FOREACH h IN ARRAY ARRAY[7,28,90] LOOP
        IF v_kind='new_article' THEN
            v_baseline:=control.new_page_measurement_baseline();
        ELSE
            v_baseline:=control.measurement_source_window(p_receipt.tenant_id,p_receipt.site_id,
                v_page,v_before-h+1,v_before,v_operation.created_at);
        END IF;
        INSERT INTO app.change_measurement_plans(tenant_id,site_id,operation_id,horizon,
            verification_attempt_id,verified_live_at,page_url,baseline_start,baseline_end,
            baseline,post_start,post_end,due_at)
        VALUES(p_receipt.tenant_id,p_receipt.site_id,p_receipt.operation_id,h,
            p_receipt.attempt_id,v_live,v_page,
            CASE WHEN v_kind='new_article' THEN NULL ELSE v_before-h+1 END,
            CASE WHEN v_kind='new_article' THEN NULL ELSE v_before END,
            v_baseline,v_after,v_after+h-1,v_live+h*interval '24 hours')
        ON CONFLICT DO NOTHING;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION control.capture_change_measurement_baseline(app.github_delivery_receipts)
    FROM PUBLIC;

-- Keep the horizon algorithm intact. Only the confounder interval for a page
-- without a baseline starts at live verification; dated baselines are unchanged.
DO $$ DECLARE v_body text; v_old text:='BETWEEN p.baseline_start AND p.post_end;';
BEGIN
    SELECT prosrc INTO STRICT v_body FROM pg_proc
        WHERE oid='control.measure_change_horizon(uuid,uuid,uuid,integer,timestamptz)'::regprocedure;
    IF (length(v_body)-length(replace(v_body,v_old,'')))<>length(v_old) THEN
        RAISE EXCEPTION 'measurement_horizon_definition_changed';
    END IF;
    v_body:=replace(v_body,v_old,
        'BETWEEN COALESCE(p.baseline_start,(p.verified_live_at AT TIME ZONE ''America/Los_Angeles'')::date) AND p.post_end;');
    EXECUTE 'CREATE OR REPLACE FUNCTION control.measure_change_horizon('
        || 'p_tenant uuid,p_site uuid,p_operation uuid,p_horizon integer,'
        || 'p_now timestamptz DEFAULT transaction_timestamp()) RETURNS jsonb '
        || 'LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS '
        || quote_literal(v_body);
END $$;
