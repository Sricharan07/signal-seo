CREATE TABLE app.change_measurement_plans (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    horizon integer NOT NULL CHECK (horizon IN (7,28,90)),
    verification_attempt_id uuid NOT NULL,
    verified_live_at timestamptz NOT NULL,
    page_url text NOT NULL,
    baseline_start date NOT NULL,
    baseline_end date NOT NULL,
    baseline jsonb NOT NULL,
    post_start date NOT NULL,
    post_end date NOT NULL,
    due_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,operation_id,horizon),
    FOREIGN KEY (tenant_id,site_id,operation_id)
        REFERENCES app.github_pr_operations(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,verification_attempt_id)
        REFERENCES app.github_delivery_receipts(tenant_id,site_id,attempt_id),
    CHECK (baseline_end-baseline_start=horizon-1 AND post_end-post_start=horizon-1),
    CHECK (due_at=verified_live_at+horizon*interval '24 hours'),
    CHECK (jsonb_typeof(baseline)='object')
);
CREATE TABLE app.change_measurement_observations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    horizon integer NOT NULL,
    sequence_number integer NOT NULL,
    document jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,operation_id,horizon,sequence_number),
    FOREIGN KEY (tenant_id,site_id,operation_id,horizon)
        REFERENCES app.change_measurement_plans(tenant_id,site_id,operation_id,horizon),
    CHECK (sequence_number>0 AND document->>'state' IN
        ('not_yet_due','awaiting_data','measured_as_reported','unavailable','failed'))
);
ALTER TABLE app.change_measurement_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.change_measurement_plans FORCE ROW LEVEL SECURITY;
CREATE POLICY measurement_plan_scope ON app.change_measurement_plans
USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
ALTER TABLE app.change_measurement_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.change_measurement_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY measurement_observation_scope ON app.change_measurement_observations
USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE TRIGGER measurement_plan_immutable BEFORE UPDATE OR DELETE ON app.change_measurement_plans
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER measurement_observation_immutable BEFORE UPDATE OR DELETE ON app.change_measurement_observations
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
REVOKE ALL ON app.change_measurement_plans,app.change_measurement_observations
FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,
signal_crawl_admission,signal_crawl_ingest,signal_bootstrap;

-- Select one generation for the entire window. Never splice top-row imports,
-- spread aggregate values across dates, or use a site total as a page total.
CREATE FUNCTION control.measurement_source_window(
    p_tenant uuid,p_site uuid,p_page text,p_start date,p_end date,p_cutoff timestamptz
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE g app.gsc_import_generations%%ROWTYPE; b app.bing_import_generations%%ROWTYPE;
    v_gsc jsonb; v_bing jsonb; v_metrics jsonb; v_page integer; v_date integer;
    v_binding uuid; v_count integer; v_incomplete date;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT binding_id INTO v_binding FROM control.current_gsc_binding(p_tenant,p_site);
    IF NOT FOUND THEN
        v_gsc:=jsonb_build_object('state','unavailable','reason','PROVIDER_UNBOUND_OR_REVOKED',
            'generation_id',NULL,'coverage',NULL,'metrics',NULL);
    ELSE
        SELECT * INTO g FROM app.gsc_import_generations x
        WHERE x.tenant_id=p_tenant AND x.site_id=p_site AND x.binding_id=v_binding
          AND x.imported_at<=p_cutoff AND x.search_type='web' AND x.dimensions ? 'page'
          AND x.start_date<=p_start AND x.end_date>=p_end
          AND ((x.start_date=p_start AND x.end_date=p_end) OR x.dimensions ? 'date')
        ORDER BY x.imported_at DESC,x.id DESC LIMIT 1;
        IF NOT FOUND THEN
            v_gsc:=jsonb_build_object('state','awaiting_data','reason','NO_PAGE_GENERATION_COVERS_WINDOW',
                'generation_id',NULL,'coverage',NULL,'metrics',NULL);
        ELSE
            SELECT ordinality::integer-1 INTO v_page FROM jsonb_array_elements_text(g.dimensions)
                WITH ORDINALITY d(value,ordinality) WHERE value='page';
            SELECT ordinality::integer-1 INTO v_date FROM jsonb_array_elements_text(g.dimensions)
                WITH ORDINALITY d(value,ordinality) WHERE value='date';
            v_incomplete:=(g.coverage->>'first_incomplete_date')::date;
            IF v_incomplete IS NOT NULL AND v_incomplete<=p_end THEN
                v_gsc:=jsonb_build_object('state','awaiting_data','reason','PROVIDER_LAG',
                    'generation_id',g.id,'coverage',g.coverage,'metrics',NULL);
            ELSE
                SELECT count(*),jsonb_build_object('clicks',sum((r->>'clicks')::numeric),
                    'impressions',sum((r->>'impressions')::numeric),
                    'ctr',sum((r->>'clicks')::numeric)/NULLIF(sum((r->>'impressions')::numeric),0),
                    'position',sum((r->>'position')::numeric*(r->>'impressions')::numeric)
                        /NULLIF(sum((r->>'impressions')::numeric),0))
                INTO v_count,v_metrics FROM jsonb_array_elements(g.rows) r
                WHERE r->'keys'->>v_page=p_page
                  AND (v_date IS NULL OR (r->'keys'->>v_date)::date BETWEEN p_start AND p_end);
                v_gsc:=jsonb_build_object('state',CASE WHEN v_count=0 THEN 'unavailable'
                    ELSE 'measured_as_reported' END,'reason',CASE WHEN v_count=0
                    THEN 'PAGE_ROWS_NOT_REPORTED' ELSE 'AS_REPORTED_COMPLETENESS_NOT_GUARANTEED' END,
                    'generation_id',g.id,'coverage',g.coverage,
                    'metrics',CASE WHEN v_count=0 THEN NULL ELSE v_metrics END);
            END IF;
        END IF;
    END IF;
    SELECT binding_id INTO v_binding FROM control.current_bing_binding(p_tenant,p_site);
    IF NOT FOUND THEN
        v_bing:=jsonb_build_object('state','unavailable','reason','PROVIDER_UNBOUND_OR_REVOKED',
            'generation_id',NULL,'coverage',NULL,'metrics',NULL);
    ELSE
        SELECT * INTO b FROM app.bing_import_generations x
        WHERE x.tenant_id=p_tenant AND x.site_id=p_site AND x.binding_id=v_binding
          AND x.kind='performance' AND x.imported_at<=p_cutoff
          AND (SELECT count(DISTINCT (to_timestamp(substring(r->>'date' FROM
              '^/Date\((-?[0-9]+)')::numeric/1000) AT TIME ZONE 'UTC')::date)
              FROM jsonb_array_elements(x.rows) r WHERE
              (to_timestamp(substring(r->>'date' FROM '^/Date\((-?[0-9]+)')::numeric/1000)
                  AT TIME ZONE 'UTC')::date BETWEEN p_start AND p_end)=p_end-p_start+1
        ORDER BY x.imported_at DESC,x.id DESC LIMIT 1;
        IF NOT FOUND THEN
            v_bing:=jsonb_build_object('state','awaiting_data','reason','NO_SITE_GENERATION_COVERS_WINDOW',
                'generation_id',NULL,'coverage',NULL,'metrics',NULL);
        ELSE
            SELECT jsonb_build_object('clicks',sum((r->>'clicks')::numeric),
                'impressions',sum((r->>'impressions')::numeric),
                'ctr',NULL,'position',NULL) INTO v_metrics FROM jsonb_array_elements(b.rows) r
            WHERE (to_timestamp(substring(r->>'date' FROM '^/Date\((-?[0-9]+)')::numeric/1000)
                AT TIME ZONE 'UTC')::date BETWEEN p_start AND p_end;
            v_bing:=jsonb_build_object('state','measured_as_reported',
                'reason','AS_REPORTED_COMPLETENESS_NOT_GUARANTEED','generation_id',b.id,
                'coverage',b.coverage,'metrics',v_metrics);
        END IF;
    END IF;
    RETURN jsonb_build_object('gsc_page',v_gsc,'bing_site_context',v_bing,
        'bing_page',jsonb_build_object('state','unavailable',
            'reason','NOT_PROVIDED_BY_CURRENT_BING_IMPORT','generation_id',NULL,
            'coverage',NULL,'metrics',NULL));
EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format
    OR datetime_field_overflow OR division_by_zero THEN
    RETURN jsonb_build_object('gsc_page',jsonb_build_object('state','failed',
        'reason','SOURCE_RECORD_INVALID','generation_id',NULL,'coverage',NULL,'metrics',NULL),
        'bing_site_context',jsonb_build_object('state','failed','reason','SOURCE_RECORD_INVALID',
        'generation_id',NULL,'coverage',NULL,'metrics',NULL),'bing_page',
        jsonb_build_object('state','unavailable','reason','NOT_PROVIDED_BY_CURRENT_BING_IMPORT',
        'generation_id',NULL,'coverage',NULL,'metrics',NULL));
END $$;
REVOKE ALL ON FUNCTION control.measurement_source_window(uuid,uuid,text,date,date,timestamptz) FROM PUBLIC;

CREATE FUNCTION control.capture_change_measurement_baseline(p_receipt app.github_delivery_receipts) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_doc jsonb; v_page text; v_created timestamptz; v_live timestamptz;
    v_before date; v_after date; h integer;
BEGIN
    v_doc:=convert_from(p_receipt.canonical_receipt,'UTF8')::jsonb;
    IF v_doc->>'outcome' IS DISTINCT FROM 'verified' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_receipt.tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_receipt.site_id::text,true);
    SELECT convert_from(r.canonical_manifest,'UTF8')::jsonb->'evidence'->>'page_url',o.created_at
        INTO v_page,v_created FROM app.github_pr_operations o JOIN app.candidate_recipe_revisions r
        ON r.tenant_id=o.tenant_id AND r.site_id=o.site_id AND r.id=o.candidate_revision_id
        WHERE o.tenant_id=p_receipt.tenant_id AND o.site_id=p_receipt.site_id AND o.id=p_receipt.operation_id;
    SELECT finished_at INTO v_live FROM app.egress_operations
        WHERE tenant_id=p_receipt.tenant_id AND site_id=p_receipt.site_id
          AND id=(v_doc->>'live_egress_operation_id')::uuid;
    IF v_page IS NULL OR v_live IS NULL THEN RAISE EXCEPTION 'measurement_evidence_missing'; END IF;
    -- Whole source-calendar days exclude both the PR preparation day and the
    -- live-verification day. Baseline generations must predate PR preparation.
    v_before:=(v_created AT TIME ZONE 'America/Los_Angeles')::date-1;
    v_after:=(v_live AT TIME ZONE 'America/Los_Angeles')::date+1;
    FOREACH h IN ARRAY ARRAY[7,28,90] LOOP
        INSERT INTO app.change_measurement_plans(tenant_id,site_id,operation_id,horizon,
            verification_attempt_id,verified_live_at,page_url,baseline_start,baseline_end,
            baseline,post_start,post_end,due_at)
        VALUES(p_receipt.tenant_id,p_receipt.site_id,p_receipt.operation_id,h,p_receipt.attempt_id,v_live,v_page,
            v_before-h+1,v_before,control.measurement_source_window(p_receipt.tenant_id,p_receipt.site_id,
                v_page,v_before-h+1,v_before,v_created),v_after,v_after+h-1,
            v_live+h*interval '24 hours') ON CONFLICT DO NOTHING;
    END LOOP;
    RETURN;
END $$;
REVOKE ALL ON FUNCTION control.capture_change_measurement_baseline(app.github_delivery_receipts) FROM PUBLIC;
CREATE FUNCTION control.capture_change_measurement_trigger() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    PERFORM control.capture_change_measurement_baseline(NEW);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.capture_change_measurement_trigger() FROM PUBLIC;
CREATE TRIGGER capture_change_measurement AFTER INSERT ON app.github_delivery_receipts
FOR EACH ROW EXECUTE FUNCTION control.capture_change_measurement_trigger();
DO $$ DECLARE r app.github_delivery_receipts%%ROWTYPE; s record;
BEGIN
    FOR s IN SELECT tenant_id,site_id FROM control.tenant_site_routes LOOP
        PERFORM set_config('signal.tenant_id',s.tenant_id::text,true);
        PERFORM set_config('signal.site_id',s.site_id::text,true);
        FOR r IN SELECT * FROM app.github_delivery_receipts
            WHERE tenant_id=s.tenant_id AND site_id=s.site_id ORDER BY recorded_at,attempt_id LOOP
            PERFORM control.capture_change_measurement_baseline(r);
        END LOOP;
    END LOOP;
    PERFORM set_config('signal.tenant_id','',true);
    PERFORM set_config('signal.site_id','',true);
END $$;

CREATE FUNCTION control.measurement_delta(p_before jsonb,p_after jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT jsonb_object_agg(k,CASE WHEN p_before->>k IS NULL OR p_after->>k IS NULL THEN NULL
    ELSE (p_after->>k)::numeric-(p_before->>k)::numeric END)
FROM unnest(ARRAY['clicks','impressions','ctr','position']) k;
$$;
REVOKE ALL ON FUNCTION control.measurement_delta(jsonb,jsonb) FROM PUBLIC;

CREATE FUNCTION control.measure_change_horizon(p_tenant uuid,p_site uuid,p_operation uuid,p_horizon integer,
    p_now timestamptz DEFAULT transaction_timestamp())
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p app.change_measurement_plans%%ROWTYPE; v_post jsonb; v_doc jsonb;
    v_previous jsonb; v_sequence integer; v_state text; v_reason text; v_confounders jsonb;
BEGIN
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO p FROM app.change_measurement_plans WHERE tenant_id=p_tenant AND site_id=p_site
        AND operation_id=p_operation AND horizon=p_horizon FOR UPDATE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT document,sequence_number INTO v_previous,v_sequence FROM app.change_measurement_observations
        WHERE tenant_id=p_tenant AND site_id=p_site AND operation_id=p_operation AND horizon=p_horizon
        ORDER BY sequence_number DESC LIMIT 1;
    v_post:=control.measurement_source_window(p_tenant,p_site,p.page_url,p.post_start,p.post_end,
        p_now);
    IF p_now<p.due_at THEN
        v_state:='not_yet_due'; v_reason:='HORIZON_NOT_YET_DUE';
    ELSIF p_now<((p.post_end+1)::timestamp AT TIME ZONE 'America/Los_Angeles') THEN
        v_state:='awaiting_data'; v_reason:='POST_WINDOW_NOT_CLOSED';
    ELSE
        v_state:=v_post->'gsc_page'->>'state'; v_reason:=v_post->'gsc_page'->>'reason';
    END IF;
    IF v_state IN ('not_yet_due','awaiting_data') AND v_reason IN
        ('HORIZON_NOT_YET_DUE','POST_WINDOW_NOT_CLOSED') THEN v_post:=NULL; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('operation_id',c.operation_id,
        'verified_live_at',c.verified_live_at,'page_url',c.page_url) ORDER BY c.verified_live_at,c.operation_id),
        '[]'::jsonb) INTO v_confounders FROM app.change_measurement_plans c
    WHERE c.tenant_id=p_tenant AND c.site_id=p_site AND c.horizon=7 AND c.page_url=p.page_url
        AND c.operation_id<>p_operation AND (c.verified_live_at AT TIME ZONE 'America/Los_Angeles')::date
        BETWEEN p.baseline_start AND p.post_end;
    v_doc:=jsonb_build_object('state',v_state,'reason',v_reason,'post',v_post,
        'observed_change',jsonb_build_object(
            'gsc_page',control.measurement_delta(p.baseline->'gsc_page'->'metrics',v_post->'gsc_page'->'metrics'),
            'bing_site_context',control.measurement_delta(p.baseline->'bing_site_context'->'metrics',
                v_post->'bing_site_context'->'metrics')),'confounders',v_confounders);
    IF v_doc IS DISTINCT FROM v_previous THEN
        INSERT INTO app.change_measurement_observations(tenant_id,site_id,operation_id,horizon,
            sequence_number,document) VALUES(p_tenant,p_site,p_operation,p_horizon,
            COALESCE(v_sequence,0)+1,v_doc);
    END IF;
    RETURN v_doc;
END $$;
REVOKE ALL ON FUNCTION control.measure_change_horizon(uuid,uuid,uuid,integer,timestamptz) FROM PUBLIC;

CREATE FUNCTION control.run_weekly_change_measurements(
    p_tenant uuid,p_site uuid,p_week date,p_cycle uuid,p_grant uuid,p_generation text
) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p record; v_count integer:=0;
BEGIN
    IF control.weekly_cycle_authority(p_tenant,p_site,p_week,p_cycle,p_grant,p_generation)<>'active'
        THEN RETURN NULL; END IF;
    FOR p IN SELECT operation_id,horizon FROM app.change_measurement_plans
        WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY due_at,operation_id,horizon LOOP
        PERFORM control.measure_change_horizon(p_tenant,p_site,p.operation_id,p.horizon);
        v_count:=v_count+1;
    END LOOP;
    RETURN v_count;
END $$;
REVOKE ALL ON FUNCTION control.run_weekly_change_measurements(uuid,uuid,date,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.run_weekly_change_measurements(uuid,uuid,date,uuid,uuid,text) TO signal_workflow;

-- This read-only background work exercises no publishing authority. Its inputs
-- can name only an existing live-verification-bound plan on an active site.
CREATE FUNCTION control.list_change_measurement_schedule(p_tenant uuid,p_site uuid)
RETURNS TABLE(operation_id uuid,horizon integer,due_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_workflow' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    RETURN QUERY SELECT p.operation_id,p.horizon,p.due_at FROM app.change_measurement_plans p
    JOIN app.sites s ON s.tenant_id=p.tenant_id AND s.id=p.site_id
    JOIN app.tenants t ON t.tenant_id=s.tenant_id
    WHERE p.tenant_id=p_tenant AND p.site_id=p_site AND s.state='active' AND t.lifecycle='active';
END $$;
CREATE FUNCTION control.measure_scheduled_change(p_tenant uuid,p_site uuid,p_operation uuid,p_horizon integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user<>'signal_workflow' THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    IF NOT EXISTS (SELECT 1 FROM app.sites s JOIN app.tenants t ON t.tenant_id=s.tenant_id
        WHERE s.tenant_id=p_tenant AND s.id=p_site AND s.state='active' AND t.lifecycle='active')
        THEN RETURN NULL; END IF;
    RETURN control.measure_change_horizon(p_tenant,p_site,p_operation,p_horizon);
END $$;
REVOKE ALL ON FUNCTION control.list_change_measurement_schedule(uuid,uuid),
    control.measure_scheduled_change(uuid,uuid,uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.list_change_measurement_schedule(uuid,uuid),
    control.measure_scheduled_change(uuid,uuid,uuid,integer) TO signal_workflow;

CREATE FUNCTION control.change_measurement_projection(p_tenant uuid,p_site uuid,p_week date)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT COALESCE(jsonb_agg(jsonb_build_object('operation_id',p.operation_id,'horizon',p.horizon,
    'page_url',p.page_url,'verified_live_at',p.verified_live_at,'due_at',p.due_at,
    'baseline_start',p.baseline_start,'baseline_end',p.baseline_end,'baseline',p.baseline,
    'post_start',p.post_start,'post_end',p.post_end,
    'evidence_url','/changes#operation-' || p.operation_id::text,
    'verification_attempt_id',p.verification_attempt_id,
    'observation',COALESCE(o.document,jsonb_build_object('state',CASE WHEN
        transaction_timestamp()<p.due_at THEN 'not_yet_due' ELSE 'awaiting_data' END,
        'reason','MEASUREMENT_NOT_RECORDED','post',NULL,'observed_change',NULL,'confounders','[]'::jsonb)),
    'recorded_at',o.recorded_at) ORDER BY p.due_at,p.operation_id,p.horizon),'[]'::jsonb)
FROM app.change_measurement_plans p LEFT JOIN LATERAL (
    SELECT document,recorded_at FROM app.change_measurement_observations x
    WHERE x.tenant_id=p.tenant_id AND x.site_id=p.site_id AND x.operation_id=p.operation_id
        AND x.horizon=p.horizon AND (p_week IS NULL OR x.recorded_at<
            (p_week+7)::timestamp AT TIME ZONE 'UTC')
    ORDER BY sequence_number DESC LIMIT 1) o ON true
WHERE p.tenant_id=p_tenant AND p.site_id=p_site AND (p_week IS NULL OR
    ((p.due_at AT TIME ZONE 'UTC')::date>=p_week AND (p.due_at AT TIME ZONE 'UTC')::date<p_week+7)
    OR ((o.recorded_at AT TIME ZONE 'UTC')::date>=p_week AND (o.recorded_at AT TIME ZONE 'UTC')::date<p_week+7));
$$;
REVOKE ALL ON FUNCTION control.change_measurement_projection(uuid,uuid,date) FROM PUBLIC;

CREATE FUNCTION control.read_change_measurement_report(
    p_session bytea,p_site uuid,p_generation text,p_week date
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_report jsonb; v_tenant uuid; v_inbox jsonb; v_next jsonb;
BEGIN
    v_report:=control.read_weekly_delivery_report(p_session,p_site,p_generation,p_week);
    IF v_report IS NULL THEN RETURN NULL; END IF;
    v_tenant:=current_setting('signal.tenant_id')::uuid;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('revision_id',x.revision_id,'kind',x.kind,
        'url',x.url) ORDER BY x.kind,x.revision_id),'[]'::jsonb)
        INTO v_inbox FROM (
        SELECT r.id AS revision_id,'technical'::text AS kind,
            '/approvals?revision=' || r.id::text AS url
        FROM app.candidate_recipe_revisions r
        WHERE r.tenant_id=v_tenant AND r.site_id=p_site AND NOT EXISTS (
            SELECT 1 FROM app.candidate_recipe_review_decisions d WHERE d.tenant_id=r.tenant_id
                AND d.site_id=r.site_id AND d.candidate_revision_id=r.id)
        AND NOT EXISTS (SELECT 1 FROM app.candidate_recipe_revisions newer WHERE newer.tenant_id=r.tenant_id
            AND newer.site_id=r.site_id AND newer.audit_report_id=r.audit_report_id
            AND newer.finding_id=r.finding_id AND (newer.sealed_at,newer.id)>(r.sealed_at,r.id))
        AND EXISTS (SELECT 1 FROM app.github_pr_extensions e JOIN app.github_read_bindings b
            ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id
            WHERE e.tenant_id=r.tenant_id AND e.site_id=r.site_id AND e.id=r.extension_id
                AND e.status='observed' AND e.base_sha=r.base_sha AND b.status='active')
        AND NOT EXISTS (SELECT 1 FROM app.github_pr_operations o
            WHERE o.tenant_id=v_tenant AND o.site_id=p_site AND o.candidate_revision_id=r.id)
        UNION ALL SELECT c.id,'editorial'::text,'/approvals'::text FROM app.content_candidates c
        WHERE c.tenant_id=v_tenant AND c.site_id=p_site AND NOT EXISTS (
            SELECT 1 FROM app.content_candidate_reviews r WHERE r.tenant_id=c.tenant_id
                AND r.site_id=c.site_id AND r.candidate_id=c.id)) x;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('revision_sha256',encode(d.sealed_revision_sha256,'hex'),
        'next_week',d.next_week,'reason',d.reason,'resolved_week',NULL)
        ORDER BY d.next_week,d.sealed_revision_sha256),'[]'::jsonb) INTO v_next
    FROM app.weekly_deferred_revisions d WHERE d.tenant_id=v_tenant AND d.site_id=p_site
        AND NOT EXISTS (SELECT 1 FROM app.weekly_deferral_resolutions r WHERE r.tenant_id=d.tenant_id
            AND r.site_id=d.site_id AND r.source_week=d.source_week
            AND r.sealed_revision_sha256=d.sealed_revision_sha256);
    -- Include the existing delivery backlog as well as weekly-cap deferrals.
    SELECT v_next || COALESCE(jsonb_agg(jsonb_build_object(
        'revision_sha256',encode(r.revision_sha256,'hex'),
        'next_week',GREATEST(p_week,cycle.week_start),'reason','DELIVERY_BACKLOG','resolved_week',NULL)
        ORDER BY w.created_at,w.id),'[]'::jsonb) INTO v_next
    FROM app.weekly_delivery_workloads w JOIN app.candidate_recipe_revisions r
        ON r.tenant_id=w.tenant_id AND r.site_id=w.site_id AND r.id=w.revision_id
    JOIN app.weekly_cycles cycle ON cycle.tenant_id=w.tenant_id AND cycle.site_id=w.site_id
        AND cycle.id=w.cycle_id
    LEFT JOIN app.github_pr_operations o ON o.tenant_id=w.tenant_id
        AND o.site_id=w.site_id AND o.id=w.operation_id
    WHERE w.tenant_id=v_tenant AND w.site_id=p_site
        AND NOT EXISTS (SELECT 1 FROM app.weekly_delivery_workloads newer
            WHERE newer.tenant_id=w.tenant_id AND newer.site_id=w.site_id
                AND newer.revision_id=w.revision_id AND (newer.created_at,newer.id)>(w.created_at,w.id))
        AND (o.id IS NULL OR o.state IN ('planned','ready','dispatching','outcome_unknown','opened'))
        AND NOT EXISTS (SELECT 1 FROM app.github_delivery_receipts receipt
            WHERE receipt.tenant_id=w.tenant_id AND receipt.site_id=w.site_id
                AND receipt.operation_id=w.operation_id
                AND convert_from(receipt.canonical_receipt,'UTF8')::jsonb->>'outcome'='verified')
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_next) item
            WHERE item->>'revision_sha256'=encode(r.revision_sha256,'hex'));
    v_next:=jsonb_build_object('state',CASE WHEN jsonb_array_length(v_next)>0
        THEN 'available' ELSE 'unavailable' END,'source','weekly_backlog',
        'reason',CASE WHEN jsonb_array_length(v_next)>0 THEN NULL
        ELSE 'STRATEGY_OR_BACKLOG_UNAVAILABLE' END,'items',v_next);
    RETURN v_report || jsonb_build_object('schema_version',2,
        'measurements',control.change_measurement_projection(v_tenant,p_site,p_week),
        'what_is_next',v_next,'needs_decision',jsonb_build_object('count',jsonb_array_length(v_inbox),
        'items',v_inbox,'url','/approvals'));
END $$;
REVOKE ALL ON FUNCTION control.read_change_measurement_report(bytea,uuid,text,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_change_measurement_report(bytea,uuid,text,date) TO signal_api;
