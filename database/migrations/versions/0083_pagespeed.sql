CREATE TABLE app.pagespeed_sample (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    week_start date NOT NULL, verified_origin text NOT NULL,
    page_record_id uuid NOT NULL, url text NOT NULL,
    strategy text NOT NULL CHECK(strategy IN ('mobile','desktop')),
    sample_rank integer NOT NULL CHECK(sample_rank BETWEEN 1 AND 5),
    selection_source text NOT NULL CHECK(selection_source IN ('gsc_clicks','crawl_order')),
    gsc_generation_id uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(tenant_id,site_id,id),
    UNIQUE(tenant_id,site_id,week_start,url,strategy),
    UNIQUE(tenant_id,site_id,week_start,sample_rank,strategy),
    FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
    FOREIGN KEY(tenant_id,site_id,page_record_id) REFERENCES app.crawl_page_records(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,gsc_generation_id) REFERENCES app.gsc_import_generations(tenant_id,site_id,id),
    CHECK (extract(isodow FROM week_start)=1),
    CHECK ((selection_source='gsc_clicks') = (gsc_generation_id IS NOT NULL)),
    CHECK (control.valid_crawl_url_identity(url,url,url,verified_origin,1))
);
CREATE TABLE app.pagespeed_observations (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    sample_id uuid NOT NULL, egress_operation_id uuid NOT NULL,
    response_sha256 bytea NOT NULL CHECK(octet_length(response_sha256)=32),
    observation jsonb NOT NULL CHECK(jsonb_typeof(observation)='object'
        AND octet_length(observation::text)<=32768),
    fetched_at timestamptz NOT NULL,
    PRIMARY KEY(tenant_id,site_id,id),
    UNIQUE(tenant_id,site_id,sample_id),
    FOREIGN KEY(tenant_id,site_id,sample_id) REFERENCES app.pagespeed_sample(tenant_id,site_id,id),
    FOREIGN KEY(tenant_id,site_id,egress_operation_id)
        REFERENCES app.owner_connector_egress_operations(tenant_id,site_id,id)
);
CREATE TABLE app.pagespeed_failures (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, sample_id uuid NOT NULL,
    reason text NOT NULL CHECK(reason IN ('PSI_RESPONSE_REJECTED','PSI_RATE_LIMITED',
        'PSI_PROVIDER_UNAVAILABLE','PSI_CREDENTIAL_REJECTED','PSI_TRANSPORT_UNAVAILABLE')),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(tenant_id,site_id,sample_id),
    FOREIGN KEY(tenant_id,site_id,sample_id) REFERENCES app.pagespeed_sample(tenant_id,site_id,id)
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['pagespeed_sample','pagespeed_observations','pagespeed_failures'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY pagespeed_scope ON app.%%I USING '
            '(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK '
            '(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
        EXECUTE format('CREATE TRIGGER pagespeed_immutable BEFORE UPDATE OR DELETE ON app.%%I '
            'FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_identity,signal_api,'
            'signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest,signal_bootstrap',t);
    END LOOP;
END $$;

CREATE FUNCTION control.pagespeed_quote(p_text text) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE b bytea:=convert_to(p_text,'UTF8'); i integer; n integer; result text:='';
BEGIN
    FOR i IN 0..octet_length(b)-1 LOOP
        n:=get_byte(b,i);
        result:=result||CASE WHEN n BETWEEN 65 AND 90 OR n BETWEEN 97 AND 122
            OR n BETWEEN 48 AND 57 OR n IN (45,46,95,126) THEN chr(n)
            WHEN n=32 THEN '+' ELSE '%%'||upper(lpad(to_hex(n),2,'0')) END;
    END LOOP;
    RETURN result;
END $$;
REVOKE ALL ON FUNCTION control.pagespeed_quote(text) FROM PUBLIC;

CREATE FUNCTION control.pagespeed_request_url(p_url text,p_strategy text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
SELECT 'https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url='||
    control.pagespeed_quote(p_url)||'&strategy='||p_strategy||'&category=performance';
$$;
REVOKE ALL ON FUNCTION control.pagespeed_request_url(text,text) FROM PUBLIC;

CREATE FUNCTION control.schedule_pagespeed_sample(p_session bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; v_week date:=(date_trunc('week',clock_timestamp() AT TIME ZONE 'UTC'))::date;
    v_generation uuid; v_rows jsonb:='[]'; v_result jsonb; v_page_dimension integer;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','denied'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('pagespeed:'||a.tenant_id::text||':'||p_site::text,0));
    IF NOT EXISTS(SELECT 1 FROM app.pagespeed_sample WHERE tenant_id=a.tenant_id
        AND site_id=p_site AND week_start=v_week) THEN
        SELECT g.id,g.rows,(SELECT ordinal::integer-1 FROM jsonb_array_elements_text(g.dimensions)
            WITH ORDINALITY d(value,ordinal) WHERE value='page' LIMIT 1)
        INTO v_generation,v_rows,v_page_dimension FROM app.gsc_import_generations g
        JOIN control.current_gsc_binding(a.tenant_id,p_site) b ON b.binding_id=g.binding_id
        WHERE g.tenant_id=a.tenant_id AND g.site_id=p_site AND g.search_type='web'
            AND g.dimensions ? 'page' AND g.imported_at>clock_timestamp()-interval '35 days'
        ORDER BY g.imported_at DESC,g.id DESC LIMIT 1;
        WITH latest AS (
            SELECT id FROM app.crawl_runs WHERE tenant_id=a.tenant_id AND site_id=p_site
                AND status='completed' ORDER BY ended_at DESC,id DESC LIMIT 1
        ), pages AS (
            SELECT p.id,u.fetch_url,u.discovered_at,
                (SELECT sum((r->>'clicks')::numeric) FROM jsonb_array_elements(coalesce(v_rows,'[]')) r
                    WHERE r->'keys'->>v_page_dimension=u.fetch_url AND r->>'clicks' ~ '^[0-9]+([.][0-9]+)?$') clicks
            FROM app.crawl_page_records p JOIN latest l ON l.id=p.crawl_run_id
            JOIN app.crawl_frontier f ON f.tenant_id=p.tenant_id AND f.site_id=p.site_id AND f.id=p.frontier_id
            JOIN app.urls u ON u.tenant_id=f.tenant_id AND u.site_id=f.site_id AND u.id=f.url_id
            WHERE p.tenant_id=a.tenant_id AND p.site_id=p_site AND u.origin=a.origin
        ), ranked AS (
            SELECT *,row_number() OVER(ORDER BY clicks DESC NULLS LAST,discovered_at,fetch_url) rank
            FROM pages ORDER BY clicks DESC NULLS LAST,discovered_at,fetch_url LIMIT 5
        )
        INSERT INTO app.pagespeed_sample(tenant_id,site_id,id,week_start,verified_origin,
            page_record_id,url,strategy,sample_rank,selection_source,gsc_generation_id)
        SELECT a.tenant_id,p_site,gen_random_uuid(),v_week,a.origin,id,fetch_url,
            strategy,rank,CASE WHEN v_generation IS NULL THEN 'crawl_order' ELSE 'gsc_clicks' END,v_generation
        FROM ranked CROSS JOIN unnest(ARRAY['mobile','desktop']) strategy;
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object('sample_id',s.id,'url',s.url,'strategy',s.strategy,
        'verified_origin',s.verified_origin,'sample_rank',s.sample_rank,'selection_source',s.selection_source)
        ORDER BY s.sample_rank,s.strategy),'[]') INTO v_result FROM (
    SELECT s.* FROM app.pagespeed_sample s WHERE s.tenant_id=a.tenant_id AND s.site_id=p_site AND s.week_start=v_week
        AND s.verified_origin=a.origin
        AND NOT EXISTS(SELECT 1 FROM app.owner_connector_egress_operations e
            WHERE e.tenant_id=s.tenant_id AND e.site_id=s.site_id AND e.id=s.id)
    ORDER BY s.sample_rank,s.strategy LIMIT greatest(0,4-(SELECT count(*) FROM app.owner_connector_egress_operations e
        WHERE e.tenant_id=a.tenant_id AND e.site_id=p_site AND e.profile='pagespeed' AND e.kind='provider'
            AND e.issued_at>=date_trunc('day',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'))
    ) s;
    RETURN jsonb_build_object('outcome','scheduled','samples',v_result,'daily_request_cap',4,
        'weekly_page_cap',5,'week_start',v_week);
END $$;
REVOKE ALL ON FUNCTION control.schedule_pagespeed_sample(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.schedule_pagespeed_sample(bytea,text,uuid) TO signal_identity;

ALTER TABLE app.owner_connector_egress_operations DROP CONSTRAINT owner_connector_egress_operations_profile_check;
ALTER TABLE app.owner_connector_egress_operations ADD CONSTRAINT owner_connector_egress_operations_profile_check
CHECK(profile IN ('github_rest','google_oauth_token','google_oauth_revoke','gsc_api','slack_oauth','slack_bot','pagespeed'));
ALTER FUNCTION control.owner_connector_request_allowed(text,text,text) RENAME TO owner_request_before_pagespeed;
CREATE FUNCTION control.owner_connector_request_allowed(p_profile text,p_method text,p_url text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT CASE WHEN p_profile='pagespeed' THEN p_method='GET' AND p_url ~
    '^https://www[.]googleapis[.]com/pagespeedonline/v5/runPagespeed[?]url=[^&?#[:space:]]+&strategy=(mobile|desktop)&category=performance$'
    ELSE control.owner_request_before_pagespeed(p_profile,p_method,p_url) END;
$$;
REVOKE ALL ON FUNCTION control.owner_connector_request_allowed(text,text,text) FROM PUBLIC;

CREATE FUNCTION control.pagespeed_admission_allowed(p_tenant uuid,p_site uuid,p_operation uuid,
    p_kind text,p_target text,p_verified_origin text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s app.pagespeed_sample%%ROWTYPE; v_count integer;
BEGIN
    SELECT * INTO s FROM app.pagespeed_sample WHERE tenant_id=p_tenant AND site_id=p_site
        AND verified_origin=p_verified_origin AND week_start=
            date_trunc('week',clock_timestamp() AT TIME ZONE 'UTC')::date
        AND control.pagespeed_request_url(url,strategy)=p_target
        AND (p_kind='robots' OR id=p_operation);
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('pagespeed:'||p_tenant::text||':'||p_site::text,0));
    IF EXISTS(SELECT 1 FROM app.owner_connector_egress_operations
        WHERE tenant_id=p_tenant AND site_id=p_site AND id=s.id AND kind='provider')
    THEN RETURN true; END IF;
    SELECT count(*) INTO v_count FROM app.owner_connector_egress_operations
    WHERE tenant_id=p_tenant AND site_id=p_site AND profile='pagespeed' AND kind='provider'
        AND issued_at >= date_trunc('day',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
    RETURN v_count<4;
END $$;
REVOKE ALL ON FUNCTION control.pagespeed_admission_allowed(uuid,uuid,uuid,text,text,text) FROM PUBLIC;

CREATE FUNCTION control.valid_pagespeed_projection(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE section text; v jsonb; metrics jsonb; m jsonb; name text; period jsonb;
    rating text; n numeric; good numeric; poor numeric; finding jsonb; source text; expected_status text;
BEGIN
    IF p IS NULL OR jsonb_typeof(p)<>'object' OR NOT p ?& ARRAY['evidence_id','url','strategy',
        'lighthouse_version','lab','field_url','field_origin','fetched_at','response_sha256','findings']
        OR p->>'fetched_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+[+]00:00$'
        OR length(p->>'fetched_at')>40 THEN RETURN false; END IF;
    FOREACH section IN ARRAY ARRAY['lab','field_url','field_origin'] LOOP
        v:=p->section; metrics:=v->'metrics';
        IF jsonb_typeof(v) IS DISTINCT FROM 'object' OR jsonb_typeof(metrics) IS DISTINCT FROM 'object'
        THEN RETURN false; END IF;
        IF section='lab' THEN
            IF v - 'source' - 'status' - 'reason' - 'run_at' - 'performance_score' - 'metrics'<>'{}'::jsonb
                OR v->>'source' IS DISTINCT FROM 'psi_lighthouse'
                OR v->>'status' IS NULL OR v->>'status' NOT IN ('available','unavailable')
                OR NOT v ?& ARRAY['source','status','reason','run_at','performance_score','metrics']
                OR (v->>'reason' IS NOT NULL AND v->>'reason'<>'lighthouse_runtime_error')
                OR v->>'run_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(Z|[+]00:00)$'
                OR length(v->>'run_at')>40
                OR jsonb_typeof(v->'performance_score') NOT IN ('number','null')
                OR (v->>'performance_score')::numeric NOT BETWEEN 0 AND 1
                OR metrics - 'lcp' - 'fcp' - 'tbt' - 'speed_index' - 'cls'<>'{}'::jsonb
                OR NOT metrics ?& ARRAY['lcp','fcp','tbt','speed_index','cls'] THEN RETURN false; END IF;
        ELSE
            period:=v->'collection_period';
            IF v - 'source' - 'locator' - 'percentile' - 'collection_period' - 'metrics' - 'status'<>'{}'::jsonb
                OR NOT v ?& ARRAY['source','locator','percentile','collection_period','metrics','status']
                OR v->'percentile' IS DISTINCT FROM '75'::jsonb
                OR v->>'status' IS NULL OR v->>'status' NOT IN ('good','needs_improvement','poor','unavailable')
                OR metrics - 'lcp' - 'inp' - 'cls'<>'{}'::jsonb
                OR NOT metrics ?& ARRAY['lcp','inp','cls']
                OR jsonb_typeof(period) IS DISTINCT FROM 'object'
                OR period - 'state' - 'reason' - 'first_date' - 'last_date'<>'{}'::jsonb
                OR NOT period ?& ARRAY['state','reason','first_date','last_date'] THEN RETURN false; END IF;
            IF period->>'state'='unavailable' THEN
                IF period->>'reason' IS DISTINCT FROM 'collection_period_not_reported'
                    OR period->'first_date'<>'null'::jsonb OR period->'last_date'<>'null'::jsonb
                THEN RETURN false; END IF;
            ELSIF period->>'state'='available' THEN
                IF period->'reason'<>'null'::jsonb OR period->>'first_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                    OR period->>'last_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                    OR (period->>'last_date')::date-(period->>'first_date')::date NOT BETWEEN 0 AND 31
                THEN RETURN false; END IF;
            ELSE RETURN false; END IF;
        END IF;
        FOR name,m IN SELECT key,value FROM jsonb_each(metrics) LOOP
            IF jsonb_typeof(m)<>'object' OR NOT m ?& ARRAY['state','reason','value','unit']
                OR (CASE WHEN section='lab' THEN m - 'state' - 'reason' - 'value' - 'unit'
                    ELSE m - 'state' - 'reason' - 'value' - 'unit' - 'rating' END)<>'{}'::jsonb
                OR m->>'unit' IS DISTINCT FROM (CASE WHEN name='cls' THEN 'score' ELSE 'ms' END)
            THEN RETURN false; END IF;
            IF m->>'state'='unavailable' THEN
                IF m->'value'<>'null'::jsonb OR m->>'reason' NOT IN ('metric_not_reported',
                    'insufficient_field_data','field_data_not_reported','percentile_not_reported',
                    'origin_fallback_not_page_data','lighthouse_runtime_error')
                    OR m->>'reason' IS NULL OR (section<>'lab' AND m->'rating' IS DISTINCT FROM 'null'::jsonb)
                THEN RETURN false; END IF;
            ELSIF m->>'state'='available' THEN
                IF m->'reason'<>'null'::jsonb OR jsonb_typeof(m->'value') IS DISTINCT FROM 'number'
                THEN RETURN false; END IF;
                n:=(m->>'value')::numeric;
                IF n NOT BETWEEN 0 AND 1000000000 THEN RETURN false; END IF;
                IF section<>'lab' THEN
                    good:=CASE name WHEN 'lcp' THEN 2500 WHEN 'inp' THEN 200 ELSE 0.1 END;
                    poor:=CASE name WHEN 'lcp' THEN 4000 WHEN 'inp' THEN 500 ELSE 0.25 END;
                    rating:=CASE WHEN n<=good THEN 'good' WHEN n>poor THEN 'poor' ELSE 'needs_improvement' END;
                    IF m->>'rating' IS DISTINCT FROM rating THEN RETURN false; END IF;
                END IF;
            ELSE RETURN false; END IF;
        END LOOP;
        IF section='lab' THEN
            IF v->>'status'='unavailable' THEN
                IF v->>'reason' IS DISTINCT FROM 'lighthouse_runtime_error'
                    OR v->'performance_score' IS DISTINCT FROM 'null'::jsonb
                    OR EXISTS(SELECT 1 FROM jsonb_each(metrics) m WHERE
                        m.value->>'state' IS DISTINCT FROM 'unavailable' OR
                        m.value->>'reason' IS DISTINCT FROM 'lighthouse_runtime_error')
                THEN RETURN false; END IF;
            ELSIF v->'reason' IS DISTINCT FROM 'null'::jsonb
                OR EXISTS(SELECT 1 FROM jsonb_each(metrics) m WHERE m.value->>'reason'='lighthouse_runtime_error')
            THEN RETURN false; END IF;
        ELSE
            SELECT CASE WHEN bool_or(value->>'rating' IS NULL) THEN 'unavailable'
                WHEN bool_or(value->>'rating'='poor') THEN 'poor'
                WHEN bool_or(value->>'rating'='needs_improvement') THEN 'needs_improvement'
                ELSE 'good' END INTO expected_status FROM jsonb_each(metrics);
            IF v->>'status' IS DISTINCT FROM expected_status THEN RETURN false; END IF;
        END IF;
    END LOOP;
    FOR finding IN SELECT value FROM jsonb_array_elements(p->'findings') LOOP
        source:=split_part(finding->>'key','.',3); name:=split_part(finding->>'key','.',4);
        IF finding - 'id' - 'key' - 'title' - 'summary' - 'severity' - 'resource_locator' - 'source_kind' - 'source_id'<>'{}'::jsonb
            OR NOT finding ?& ARRAY['id','key','title','summary','severity','resource_locator','source_kind','source_id']
            OR finding->>'key' !~ '^performance[.]cwv[.](url|origin)[.](lcp|inp|cls)[.]poor$'
            OR finding->>'title' IS DISTINCT FROM 'Poor '||upper(name)
            OR finding->>'id' IS NULL OR finding->>'id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[45][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            OR finding->>'summary' IS DISTINCT FROM 'CrUX '||source||' '||(p->>'strategy')||' p75 '||upper(name)||
                ' exceeds the poor threshold. Observation only; no automated fix is available.'
            OR finding->>'severity' IS DISTINCT FROM 'medium'
            OR finding->>'source_kind' IS DISTINCT FROM 'pagespeed_observation'
            OR finding->>'source_id' IS DISTINCT FROM p->>'evidence_id'
            OR finding->>'resource_locator' IS DISTINCT FROM p->('field_'||source)->>'locator'
            OR p->('field_'||source)->'metrics'->name->>'rating' IS DISTINCT FROM 'poor'
        THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN others THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION control.valid_pagespeed_projection(jsonb) FROM PUBLIC;

CREATE FUNCTION control.finish_pagespeed(p_session bytea,p_generation text,p_site uuid,p_sample uuid,
    p_observation jsonb,p_hash bytea,p_reason text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; s app.pagespeed_sample%%ROWTYPE; e app.owner_connector_egress_operations%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    SELECT * INTO s FROM app.pagespeed_sample WHERE tenant_id=a.tenant_id AND site_id=p_site
        AND id=p_sample AND verified_origin=a.origin FOR SHARE;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    SELECT * INTO e FROM app.owner_connector_egress_operations WHERE tenant_id=a.tenant_id
        AND site_id=p_site AND id=p_sample AND profile='pagespeed' AND kind='provider'
        AND state IN ('observed','failed') AND verified_origin=a.origin;
    IF NOT FOUND THEN RETURN 'egress_unavailable'; END IF;
    IF p_reason IS NOT NULL THEN
        IF p_observation IS NOT NULL OR p_hash IS NOT NULL THEN RETURN 'rejected'; END IF;
        INSERT INTO app.pagespeed_failures(tenant_id,site_id,sample_id,reason)
        VALUES(a.tenant_id,p_site,p_sample,p_reason) ON CONFLICT DO NOTHING;
        RETURN 'recorded';
    END IF;
    IF control.valid_pagespeed_projection(p_observation) IS NOT TRUE
        OR e.state<>'observed' OR e.response_evidence->>'http_status'<>'200'
        OR e.response_evidence->>'body_sha256' IS DISTINCT FROM encode(p_hash,'hex')
        OR p_observation->>'evidence_id' IS DISTINCT FROM p_sample::text
        OR p_observation->>'url' IS DISTINCT FROM s.url
        OR p_observation->>'strategy' IS DISTINCT FROM s.strategy
        OR p_observation->>'response_sha256' IS DISTINCT FROM encode(p_hash,'hex')
        OR p_observation - 'evidence_id' - 'url' - 'strategy' - 'lighthouse_version' - 'lab'
            - 'field_url' - 'field_origin' - 'fetched_at' - 'response_sha256' - 'findings'<>'{}'::jsonb
        OR p_observation->>'lighthouse_version' !~ '^[0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}$'
        OR p_observation->'field_url'->>'source' IS DISTINCT FROM 'url'
        OR p_observation->'field_url'->>'locator' IS DISTINCT FROM s.url
        OR p_observation->'field_origin'->>'source' IS DISTINCT FROM 'origin'
        OR p_observation->'field_origin'->>'locator' IS DISTINCT FROM s.verified_origin
        OR jsonb_typeof(p_observation->'lab') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_observation->'findings') IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_observation->'findings')>6
        OR (p_observation->>'fetched_at')::timestamptz NOT BETWEEN e.issued_at AND clock_timestamp()
    THEN RETURN 'rejected'; END IF;
    INSERT INTO app.pagespeed_observations(tenant_id,site_id,id,sample_id,egress_operation_id,
        response_sha256,observation,fetched_at)
    VALUES(a.tenant_id,p_site,p_sample,p_sample,p_sample,p_hash,p_observation,
        (p_observation->>'fetched_at')::timestamptz) ON CONFLICT DO NOTHING;
    IF NOT EXISTS(SELECT 1 FROM app.pagespeed_observations WHERE tenant_id=a.tenant_id
        AND site_id=p_site AND id=p_sample AND observation=p_observation AND response_sha256=p_hash)
    THEN RETURN 'conflict'; END IF;
    RETURN 'recorded';
END $$;
REVOKE ALL ON FUNCTION control.finish_pagespeed(bytea,text,uuid,uuid,jsonb,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.finish_pagespeed(bytea,text,uuid,uuid,jsonb,bytea,text) TO signal_identity;

CREATE FUNCTION control.read_pagespeed(p_session bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; v_samples jsonb;
BEGIN
    SELECT * INTO a FROM control.gsc_owner_context(p_session,p_generation,p_site);
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','denied'); END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object('sample_id',s.id,'url',s.url,'strategy',s.strategy,
        'week_start',s.week_start,'selection_source',s.selection_source,'observation',o.observation,
        'reason',f.reason,'state',CASE WHEN o.id IS NOT NULL THEN 'observed'
            WHEN f.reason IS NOT NULL THEN 'unavailable' WHEN e.id IS NOT NULL THEN 'outcome_unknown'
            ELSE 'scheduled' END) ORDER BY s.sample_rank,s.strategy),'[]') INTO v_samples
    FROM app.pagespeed_sample s LEFT JOIN app.pagespeed_observations o
        ON o.tenant_id=s.tenant_id AND o.site_id=s.site_id AND o.sample_id=s.id
    LEFT JOIN app.pagespeed_failures f ON f.tenant_id=s.tenant_id AND f.site_id=s.site_id AND f.sample_id=s.id
    LEFT JOIN app.owner_connector_egress_operations e ON e.tenant_id=s.tenant_id AND e.site_id=s.site_id AND e.id=s.id
    WHERE s.tenant_id=a.tenant_id AND s.site_id=p_site AND s.verified_origin=a.origin
        AND s.week_start=(SELECT max(week_start) FROM app.pagespeed_sample
            WHERE tenant_id=a.tenant_id AND site_id=p_site AND verified_origin=a.origin);
    RETURN jsonb_build_object('outcome','found','samples',v_samples,'daily_request_cap',4,
        'weekly_page_cap',5,'local_lighthouse','unavailable');
END $$;
REVOKE ALL ON FUNCTION control.read_pagespeed(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_pagespeed(bytea,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.begin_owner_connector_egress(
    p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid,
    p_kind text, p_profile text, p_method text, p_request_url text, p_target_url text,
    p_origin text, p_request_sha256 bytea, p_body_sha256 bytea, p_request_bytes integer,
    p_max_response_bytes integer, p_credentialed boolean, p_robots_operation_id uuid,
    p_min_delay_ms integer
) RETURNS TABLE (outcome text, bucket_id uuid, expires_at timestamptz,
    robots_allowed boolean, robots_crawl_delay_ms integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_now timestamptz := clock_timestamp(); v_owner record; v_verified record;
    v_existing app.owner_connector_egress_operations%%ROWTYPE;
    v_robots app.owner_connector_egress_operations%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_expires timestamptz; v_session_expires timestamptz; v_workload text; v_fingerprint bytea;
    v_rechecked record;
BEGIN
    IF p_operation_id IS NULL OR p_operation_id::text !~
        '^[0-9a-f]{8}-[0-9a-f]{4}-[45][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_kind IS NULL OR p_kind NOT IN ('robots', 'provider')
       OR octet_length(p_request_sha256) IS DISTINCT FROM 32
       OR p_request_bytes IS NULL OR p_request_bytes NOT BETWEEN 0 AND 262144
       OR p_max_response_bytes IS NULL OR p_max_response_bytes NOT BETWEEN 1 AND 524288
       OR p_credentialed IS NULL OR p_min_delay_ms IS NULL OR p_min_delay_ms NOT BETWEEN 1000 AND 60000
       OR control.valid_crawl_url_identity(p_request_url,p_request_url,p_request_url,p_origin,1) IS NOT TRUE
       OR control.valid_crawl_url_identity(p_target_url,p_target_url,p_target_url,p_origin,1) IS NOT TRUE
       OR control.owner_connector_request_allowed(p_profile,
            CASE WHEN p_kind = 'robots' THEN CASE WHEN p_profile IN ('github_rest','gsc_api','pagespeed')
                AND p_target_url !~ '/access_tokens$|/searchAnalytics/query$' THEN 'GET' ELSE 'POST' END
                ELSE p_method END, p_target_url) IS NOT TRUE
       OR (p_method = 'GET' AND (p_request_bytes <> 0 OR p_body_sha256 IS NOT NULL))
       OR (p_method = 'POST' AND octet_length(p_body_sha256) IS DISTINCT FROM 32)
       OR p_method IS NULL OR p_method NOT IN ('GET', 'POST')
       OR (p_kind = 'robots' AND (p_method <> 'GET' OR p_credentialed
            OR p_request_url IS DISTINCT FROM p_origin || '/robots.txt' OR p_robots_operation_id IS NOT NULL))
       OR (p_kind = 'provider' AND (p_target_url IS DISTINCT FROM p_request_url OR p_robots_operation_id IS NULL))
    THEN RAISE EXCEPTION 'invalid_owner_connector_request' USING ERRCODE = '22023'; END IF;
    SELECT * INTO v_owner FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF v_owner.outcome IS DISTINCT FROM 'authorized' OR v_owner.role_key IS DISTINCT FROM 'owner'
       OR v_owner.authentication_level IS DISTINCT FROM 'mfa'
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    SELECT * INTO v_verified FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_verified.outcome IS DISTINCT FROM 'verified'
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    IF p_profile='pagespeed' AND control.pagespeed_admission_allowed(
        v_owner.tenant_id,p_site_id,p_operation_id,p_kind,p_target_url,v_verified.origin) IS NOT TRUE
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    SELECT * INTO v_existing FROM app.owner_connector_egress_operations AS op
     WHERE op.tenant_id=v_owner.tenant_id AND op.site_id=p_site_id AND op.id=p_operation_id FOR UPDATE;
    IF FOUND THEN
        IF v_existing.session_hash IS DISTINCT FROM p_session_hash
           OR v_existing.recovery_generation IS DISTINCT FROM p_generation
           OR v_existing.actor_user_id IS DISTINCT FROM v_owner.user_id
           OR v_existing.verified_origin IS DISTINCT FROM v_verified.origin
           OR v_existing.kind IS DISTINCT FROM p_kind OR v_existing.profile IS DISTINCT FROM p_profile
           OR v_existing.method IS DISTINCT FROM p_method OR v_existing.request_url IS DISTINCT FROM p_request_url
           OR v_existing.target_url IS DISTINCT FROM p_target_url OR v_existing.origin IS DISTINCT FROM p_origin
           OR v_existing.request_sha256 IS DISTINCT FROM p_request_sha256
           OR v_existing.request_body_sha256 IS DISTINCT FROM p_body_sha256
           OR v_existing.request_bytes IS DISTINCT FROM p_request_bytes
           OR v_existing.max_response_bytes IS DISTINCT FROM p_max_response_bytes
           OR v_existing.credentialed IS DISTINCT FROM p_credentialed
           OR v_existing.robots_operation_id IS DISTINCT FROM p_robots_operation_id
        THEN RETURN QUERY SELECT 'conflict'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
        RETURN QUERY SELECT CASE WHEN v_existing.state='dispatched' THEN 'unknown'
            WHEN p_kind='robots' AND v_existing.state='observed' AND v_existing.robots_expires_at>v_now
                THEN 'robots_replayed' ELSE 'replayed' END,
            v_existing.bucket_id,v_existing.expires_at,v_existing.robots_allowed,v_existing.robots_crawl_delay_ms;
        RETURN;
    END IF;
    IF p_kind='provider' THEN
        SELECT * INTO v_robots FROM app.owner_connector_egress_operations AS op
         WHERE op.tenant_id=v_owner.tenant_id AND op.site_id=p_site_id AND op.id=p_robots_operation_id FOR SHARE;
        IF NOT FOUND OR v_robots.kind<>'robots' OR v_robots.state<>'observed'
           OR v_robots.robots_allowed IS DISTINCT FROM true OR v_robots.robots_expires_at<=v_now
           OR v_robots.session_hash IS DISTINCT FROM p_session_hash
           OR v_robots.recovery_generation IS DISTINCT FROM p_generation
           OR v_robots.verified_origin IS DISTINCT FROM v_verified.origin
           OR v_robots.profile IS DISTINCT FROM p_profile OR v_robots.target_url IS DISTINCT FROM p_target_url
           OR p_min_delay_ms<GREATEST(1000,coalesce(v_robots.robots_crawl_delay_ms,1000))
        THEN RETURN QUERY SELECT 'robots_denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    END IF;
    INSERT INTO control.origin_buckets(origin,profile_version,request_tokens,in_flight_count,
        last_refill_at,next_allowed_at,created_at,updated_at)
    VALUES(p_origin,1,1,0,v_now,v_now,v_now,v_now)
    ON CONFLICT ON CONSTRAINT origin_buckets_origin_profile DO NOTHING;
    SELECT * INTO v_bucket FROM control.origin_buckets AS bucket
     WHERE bucket.origin=p_origin AND bucket.profile_version=1 FOR UPDATE;
    v_now:=clock_timestamp();
    SELECT LEAST(session.expires_at,identity.expires_at) INTO v_session_expires FROM app.sessions AS session
     JOIN control.identity_sessions AS identity ON identity.id=session.identity_session_id
     WHERE session.session_token_hash=p_session_hash AND session.expires_at>v_now
       AND identity.expires_at>v_now AND session.revoked_at IS NULL AND identity.revoked_at IS NULL;
    IF NOT FOUND OR (p_kind='provider' AND v_robots.robots_expires_at<=v_now) THEN
        RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN;
    END IF;
    SELECT * INTO v_rechecked FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_rechecked.outcome IS DISTINCT FROM 'verified' OR v_rechecked.origin IS DISTINCT FROM v_verified.origin THEN
        RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN;
    END IF;
    UPDATE control.admission_leases AS lease SET released_at=v_now,completion_kind='lease_expired'
     WHERE lease.bucket_id=v_bucket.id AND lease.released_at IS NULL AND lease.expires_at<=v_now;
    UPDATE control.origin_buckets AS bucket SET
        in_flight_count=(SELECT count(*) FROM control.admission_leases AS lease
            WHERE lease.bucket_id=bucket.id AND lease.released_at IS NULL),
        request_tokens=CASE WHEN bucket.next_allowed_at<=v_now THEN 1 ELSE bucket.request_tokens END,
        last_refill_at=CASE WHEN bucket.request_tokens=0 AND bucket.next_allowed_at<=v_now
            THEN v_now ELSE bucket.last_refill_at END,
        degraded_until=CASE WHEN bucket.degraded_until<=v_now THEN NULL ELSE bucket.degraded_until END,
        updated_at=v_now WHERE bucket.id=v_bucket.id RETURNING * INTO v_bucket;
    IF v_bucket.degraded_until>v_now OR v_bucket.in_flight_count>=1
        OR v_bucket.request_tokens=0 OR v_bucket.next_allowed_at>v_now
    THEN RETURN QUERY SELECT 'deferred'::text,v_bucket.id,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    v_expires:=LEAST(v_now+interval '30 seconds',v_session_expires);
    IF p_kind='provider' THEN v_expires:=LEAST(v_expires,v_robots.robots_expires_at); END IF;
    v_workload:='owner:'||p_site_id::text||':'||p_operation_id::text;
    v_fingerprint:=sha256(convert_to(encode(p_session_hash,'hex')||':'||p_generation||':'||
        p_site_id::text||':'||p_operation_id::text||':'||encode(p_request_sha256,'hex'),'UTF8'));
    INSERT INTO control.admission_leases(id,bucket_id,workload_id,permit_kind,authority_fingerprint,
        min_delay_ms,requested_lease_seconds,issued_at,expires_at)
    VALUES(p_operation_id,v_bucket.id,v_workload,'shared_egress',v_fingerprint,p_min_delay_ms,30,v_now,v_expires);
    INSERT INTO app.owner_connector_egress_operations(tenant_id,site_id,id,actor_user_id,session_hash,
        recovery_generation,verified_origin,kind,profile,method,request_url,target_url,origin,
        request_sha256,request_body_sha256,request_bytes,max_response_bytes,credentialed,
        robots_operation_id,bucket_id,issued_at,expires_at)
    VALUES(v_owner.tenant_id,p_site_id,p_operation_id,v_owner.user_id,p_session_hash,p_generation,
        v_verified.origin,p_kind,p_profile,p_method,p_request_url,p_target_url,p_origin,p_request_sha256,
        p_body_sha256,p_request_bytes,p_max_response_bytes,p_credentialed,p_robots_operation_id,v_bucket.id,v_now,v_expires);
    UPDATE control.origin_buckets SET request_tokens=0,in_flight_count=1,
        next_allowed_at=GREATEST(next_allowed_at,v_now+p_min_delay_ms*interval '1 millisecond'),updated_at=v_now
     WHERE id=v_bucket.id;
    RETURN QUERY SELECT 'admitted'::text,v_bucket.id,v_expires,NULL::boolean,NULL::integer;
END; $$;
