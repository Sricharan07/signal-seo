-- Global catalogs are migration configuration, not tenant data or authority.
CREATE TABLE control.sql_dispatch_values (
    contract_key text NOT NULL,
    value text NOT NULL,
    PRIMARY KEY (contract_key, value)
);
CREATE TABLE control.sql_dispatch_shapes (
    contract_key text NOT NULL,
    rule_key text NOT NULL,
    row_type regclass NOT NULL,
    predicate_sql text NOT NULL,
    PRIMARY KEY (contract_key, rule_key)
);
CREATE TABLE control.authority_restriction_kinds (
    target_kind text PRIMARY KEY,
    restriction_kind text NOT NULL UNIQUE,
    target_requires_restriction boolean NOT NULL DEFAULT false
);
CREATE TABLE control.platform_event_references (
    event_type text PRIMARY KEY,
    reference_table regclass,
    predicate_sql text,
    failure_message text NOT NULL DEFAULT 'platform event object reference is invalid',
    CHECK ((reference_table IS NULL) = (predicate_sql IS NULL))
);
CREATE TABLE control.owner_egress_profiles (
    profile text PRIMARY KEY,
    robots_get boolean NOT NULL DEFAULT false,
    robots_post_pattern text,
    nullable_request_result boolean NOT NULL DEFAULT false,
    admission_sql text NOT NULL DEFAULT 'true'
);
CREATE TABLE control.owner_egress_request_rules (
    profile text NOT NULL REFERENCES control.owner_egress_profiles(profile),
    method text NOT NULL CHECK (method IN ('GET', 'POST')),
    exact_url text,
    anchored_pattern text,
    CHECK ((exact_url IS NULL) <> (anchored_pattern IS NULL)),
    CHECK (anchored_pattern IS NULL OR (left(anchored_pattern,1)='^' AND right(anchored_pattern,1)='$')),
    UNIQUE NULLS NOT DISTINCT (profile, method, exact_url, anchored_pattern)
);
CREATE TABLE control.shared_egress_profiles (
    profile text PRIMARY KEY,
    predicate_sql text NOT NULL,
    failure_message text NOT NULL,
    authority_guard regprocedure,
    always_deny boolean NOT NULL DEFAULT false,
    handles_null boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX shared_egress_one_null_route ON control.shared_egress_profiles(handles_null) WHERE handles_null;
CREATE TABLE control.shared_egress_request_rules (
    profile text NOT NULL REFERENCES control.shared_egress_profiles(profile),
    method text,
    exact_url text,
    anchored_pattern text,
    CHECK (exact_url IS NULL OR anchored_pattern IS NULL),
    CHECK (anchored_pattern IS NULL OR (left(anchored_pattern,1)='^' AND right(anchored_pattern,1)='$')),
    UNIQUE NULLS NOT DISTINCT (profile, method, exact_url, anchored_pattern)
);
CREATE TABLE control.strategy_source_dispatch (
    source_key text PRIMARY KEY,
    ordinal integer NOT NULL UNIQUE,
    owner_reader regprocedure NOT NULL,
    worker_reader regprocedure NOT NULL
);

DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['sql_dispatch_values','sql_dispatch_shapes','authority_restriction_kinds',
        'platform_event_references','owner_egress_profiles','owner_egress_request_rules',
        'shared_egress_profiles','shared_egress_request_rules','strategy_source_dispatch'] LOOP
        EXECUTE format('ALTER TABLE control.%%I OWNER TO signal_migrator',t);
        EXECUTE format('REVOKE ALL ON control.%%I FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager',t);
    END LOOP;
END $$;

CREATE FUNCTION control.sql_dispatch_value_allowed(p_contract text,p_value text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT CASE WHEN p_value IS NULL THEN NULL ELSE EXISTS (
        SELECT 1 FROM control.sql_dispatch_values WHERE contract_key=p_contract AND value=p_value
    ) END;
$$;

-- Predicates are reviewed SQL owned solely by the migrator. The typed row is a
-- parameter, never interpolated SQL. Preserve CHECK's three-valued OR semantics.
CREATE FUNCTION control.sql_dispatch_shape_allowed(p_contract text,p_row jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; result boolean; saw_null boolean:=false;
BEGIN
    FOR r IN SELECT * FROM control.sql_dispatch_shapes WHERE contract_key=p_contract ORDER BY rule_key LOOP
        EXECUTE format('SELECT (%%s) FROM jsonb_populate_record(NULL::%%s,$1)',
            r.predicate_sql,r.row_type) INTO result USING p_row;
        IF result THEN RETURN true; END IF;
        saw_null:=saw_null OR result IS NULL;
    END LOOP;
    RETURN CASE WHEN saw_null THEN NULL ELSE false END;
END $$;

CREATE FUNCTION control.authority_kind_allowed(p_target text,p_restriction text,p_target_only boolean)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT CASE WHEN p_target IS NULL THEN NULL WHEN bool_or(result) THEN true
        WHEN count(*) FILTER (WHERE result IS NULL)>0 THEN NULL ELSE false END
    FROM (SELECT target_kind=p_target AND (CASE WHEN p_target_only AND NOT target_requires_restriction
        THEN true ELSE restriction_kind=p_restriction END) result
        FROM control.authority_restriction_kinds) rules;
$$;

-- Cross-table CHECK functions are unsafe during pg_restore. These immediate row
-- guards retain CHECK's NULL acceptance and diagnostic constraint name instead.
CREATE FUNCTION control.enforce_sql_dispatch() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE allowed boolean;
BEGIN
    EXECUTE format('SELECT (%%s) FROM jsonb_populate_record(NULL::%%s,$1)',TG_ARGV[1],TG_RELID::regclass)
        INTO allowed USING to_jsonb(NEW);
    IF allowed IS FALSE THEN
        RAISE EXCEPTION 'new row for relation "%%" violates check constraint "%%"',TG_TABLE_NAME,TG_ARGV[0]
            USING ERRCODE='23514',CONSTRAINT=TG_ARGV[0],TABLE=TG_TABLE_NAME,SCHEMA=TG_TABLE_SCHEMA;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.enforce_sql_dispatch() FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.owner_connector_request_allowed(p_profile text,p_method text,p_url text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT coalesce((SELECT CASE WHEN p.nullable_request_result THEN (
        SELECT CASE WHEN bool_or(result) THEN true WHEN count(*) FILTER (WHERE result IS NULL)>0
            THEN NULL ELSE false END FROM (
            SELECT method=p_method AND CASE WHEN exact_url IS NOT NULL THEN p_url=exact_url
                ELSE p_url ~ anchored_pattern END result
            FROM control.owner_egress_request_rules WHERE profile=p.profile) matches
        ) ELSE EXISTS (SELECT 1 FROM control.owner_egress_request_rules r WHERE r.profile=p.profile
            AND r.method=p_method AND CASE WHEN r.exact_url IS NOT NULL THEN p_url=r.exact_url
                ELSE p_url ~ r.anchored_pattern END) END
        FROM control.owner_egress_profiles p WHERE p.profile=p_profile),
        CASE WHEN EXISTS(SELECT 1 FROM control.owner_egress_profiles WHERE profile=p_profile AND nullable_request_result)
            THEN NULL ELSE false END);
$$;
CREATE FUNCTION control.owner_connector_robots_method(p_profile text,p_url text)
RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT CASE WHEN EXISTS (SELECT 1 FROM control.owner_egress_profiles WHERE profile=p_profile
        AND robots_get AND p_url !~ robots_post_pattern) THEN 'GET' ELSE 'POST' END;
$$;
CREATE FUNCTION control.owner_connector_profile_admitted(p_hash bytea,p_generation text,p_site uuid,
    p_profile text,p_target text,p_tenant uuid,p_operation uuid,p_kind text,p_verified_origin text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE predicate text; allowed boolean;
BEGIN
    SELECT admission_sql INTO predicate FROM control.owner_egress_profiles WHERE profile=p_profile;
    IF NOT FOUND THEN RETURN false; END IF;
    EXECUTE 'SELECT ('||predicate||')' INTO allowed
        USING p_hash,p_generation,p_site,p_profile,p_target,p_tenant,p_operation,p_kind,p_verified_origin;
    RETURN allowed;
END $$;

CREATE FUNCTION control.platform_event_reference_rule(p_event text)
RETURNS TABLE(reference_table regclass,predicate_sql text,failure_message text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT r.reference_table,r.predicate_sql,r.failure_message
        FROM control.platform_event_references r WHERE r.event_type=p_event;
$$;
REVOKE ALL ON FUNCTION control.platform_event_reference_rule(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.platform_event_reference_rule(text)
    TO signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,
       signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

-- Reference reads retain the old invoker's grants and forced-RLS visibility.
CREATE OR REPLACE FUNCTION control.validate_platform_event_reference() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r record; valid boolean;
BEGIN
    SELECT * INTO r FROM control.platform_event_reference_rule(NEW.event_type);
    IF NOT FOUND THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
    IF r.reference_table IS NULL THEN RETURN NEW; END IF;
    EXECUTE format('SELECT EXISTS(SELECT 1 FROM %%s r, jsonb_populate_record(NULL::control.platform_events,$1) e WHERE %%s)',
        r.reference_table,r.predicate_sql) INTO valid USING to_jsonb(NEW);
    IF NOT valid THEN RAISE EXCEPTION '%%',r.failure_message USING ERRCODE='23503'; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER platform_event_reference_guard ON control.platform_events;
CREATE TRIGGER platform_event_reference_guard BEFORE INSERT ON control.platform_events
FOR EACH ROW WHEN (NEW.event_type IS NOT NULL) EXECUTE FUNCTION control.validate_platform_event_reference();

CREATE OR REPLACE FUNCTION control.bind_shared_egress_profile(
    p_tenant_id uuid,p_site_id uuid,p_operation_id uuid,p_request_sha256 bytea,p_profile text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE; r control.shared_egress_profiles%%ROWTYPE;
    allowed boolean; guard_name text;
BEGIN
    SELECT * INTO r FROM control.shared_egress_profiles WHERE profile=p_profile OR (p_profile IS NULL AND handles_null);
    IF NOT FOUND THEN RAISE EXCEPTION 'invalid_egress_profile' USING ERRCODE='22023'; END IF;
    IF r.always_deny THEN RAISE EXCEPTION '%%',r.failure_message USING ERRCODE='22023'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant_id AND site_id=p_site_id
        AND id=p_operation_id FOR UPDATE;
    IF NOT FOUND OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_request_sha256
        OR o.egress_profile NOT IN ('legacy_unqualified',p_profile)
    THEN RAISE EXCEPTION '%%',r.failure_message USING ERRCODE='22023'; END IF;
    EXECUTE format('SELECT (%%s) FROM jsonb_populate_record(NULL::app.egress_operations,$1) o',r.predicate_sql)
        INTO allowed USING to_jsonb(o);
    IF NOT allowed OR NOT EXISTS(SELECT 1 FROM control.shared_egress_request_rules q WHERE q.profile=r.profile
        AND (q.method IS NULL OR q.method=o.method)
        AND (q.exact_url IS NULL OR q.exact_url=o.request_url)
        AND (q.anchored_pattern IS NULL OR o.request_url ~ q.anchored_pattern))
    THEN RAISE EXCEPTION '%%',r.failure_message USING ERRCODE='22023'; END IF;
    IF r.authority_guard IS NOT NULL THEN
        SELECT format('%%I.%%I',n.nspname,p.proname) INTO STRICT guard_name
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.oid=r.authority_guard;
        EXECUTE format('SELECT %%s($1)',guard_name) USING o;
    END IF;
    IF o.egress_profile='legacy_unqualified' THEN
        UPDATE app.egress_operations SET egress_profile=p_profile
            WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id;
    END IF;
    RETURN 'bound';
END $$;

CREATE FUNCTION control.dispatch_strategy_sources(p_hash bytea,p_generation text,p_site uuid,p_worker boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; reader regprocedure; reader_name text; patch jsonb; packet jsonb:='{}'::jsonb;
BEGIN
    FOR r IN SELECT * FROM control.strategy_source_dispatch ORDER BY ordinal LOOP
        reader:=CASE WHEN p_worker THEN r.worker_reader ELSE r.owner_reader END;
        SELECT format('%%I.%%I',n.nspname,p.proname) INTO STRICT reader_name
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.oid=reader;
        EXECUTE format('SELECT %%s($1,$2,$3)',reader_name) INTO patch USING p_hash,p_generation,p_site;
        IF patch IS NULL THEN RETURN NULL; END IF;
        packet:=packet || patch;
    END LOOP;
    RETURN packet;
END $$;
CREATE OR REPLACE FUNCTION control.seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT control.dispatch_strategy_sources(p_hash,p_generation,p_site,false);
$$;
CREATE OR REPLACE FUNCTION control.weekly_skill_seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['strategy_rebuild']);
    PERFORM control.weekly_skill_context(p_hash,p_generation,p_site);
    RETURN control.dispatch_strategy_sources(p_hash,p_generation,p_site,true);
END $$;

REVOKE ALL ON FUNCTION control.sql_dispatch_value_allowed(text,text),
    control.sql_dispatch_shape_allowed(text,jsonb),control.authority_kind_allowed(text,text,boolean),
    control.owner_connector_request_allowed(text,text,text),control.owner_connector_robots_method(text,text),
    control.owner_connector_profile_admitted(bytea,text,uuid,text,text,uuid,uuid,text,text),
    control.validate_platform_event_reference(),control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text),
    control.dispatch_strategy_sources(bytea,text,uuid,boolean),control.seo_strategy_sources(bytea,text,uuid),
    control.weekly_skill_seo_strategy_sources(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.sql_dispatch_value_allowed(text,text),
    control.sql_dispatch_shape_allowed(text,jsonb),control.authority_kind_allowed(text,text,boolean)
    TO signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,
       signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;
GRANT EXECUTE ON FUNCTION control.seo_strategy_sources(bytea,text,uuid) TO signal_api;
GRANT EXECUTE ON FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) TO signal_workflow;

-- Seeds captured from the accepted 0099 definitions; no historical migration is rewritten.
INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('agent_runs_release_key_check','local-gpt-5.6-luna-metadata-v1'),
    ('agent_runs_release_key_check','verified-gpt-5.6-luna-metadata-v1'),
    ('agent_runs_release_key_check','local-gpt-6-luna-metadata-v2'),
    ('agent_runs_release_key_check','verified-gpt-6-luna-metadata-v2');

ALTER TABLE app.agent_runs DROP CONSTRAINT agent_runs_release_key_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.agent_runs WHERE NOT (control.sql_dispatch_value_allowed('agent_runs_release_key_check',release_key))) THEN
        RAISE EXCEPTION 'agent_runs_release_key_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_agent_runs_release_key_check BEFORE INSERT OR UPDATE ON app.agent_runs
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('agent_runs_release_key_check','control.sql_dispatch_value_allowed(''agent_runs_release_key_check'',release_key)');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('approval_requests_requested_authority_check','accept_local_fixture_draft'),
    ('approval_requests_requested_authority_check','accept_model_fixture_draft'),
    ('approval_requests_requested_authority_check','accept_verified_homepage_metadata_draft');

ALTER TABLE app.approval_requests DROP CONSTRAINT approval_requests_requested_authority_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.approval_requests WHERE NOT (control.sql_dispatch_value_allowed('approval_requests_requested_authority_check',requested_authority))) THEN
        RAISE EXCEPTION 'approval_requests_requested_authority_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_approval_requests_requested_authority_check BEFORE INSERT OR UPDATE ON app.approval_requests
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('approval_requests_requested_authority_check','control.sql_dispatch_value_allowed(''approval_requests_requested_authority_check'',requested_authority)');

INSERT INTO control.sql_dispatch_shapes (contract_key,rule_key,row_type,predicate_sql) VALUES
    ('audit_events_event_type_check','01','app.audit_events','((aggregate_sequence = 1) AND (event_type = ''invitation.created''::text))'),
    ('audit_events_event_type_check','02','app.audit_events','((aggregate_sequence = 2) AND (event_type = ANY (ARRAY[''invitation.accepted''::text, ''invitation.revoked''::text])))');

ALTER TABLE app.audit_events DROP CONSTRAINT audit_events_event_type_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.audit_events WHERE NOT (control.sql_dispatch_shape_allowed('audit_events_event_type_check',jsonb_build_object('event_type',event_type,'aggregate_sequence',aggregate_sequence)))) THEN
        RAISE EXCEPTION 'audit_events_event_type_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_audit_events_event_type_check BEFORE INSERT OR UPDATE ON app.audit_events
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('audit_events_event_type_check','control.sql_dispatch_shape_allowed(''audit_events_event_type_check'',jsonb_build_object(''event_type'',event_type,''aggregate_sequence'',aggregate_sequence))');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('candidate_recipe_review_decisions_decision_channel_check','dashboard'),
    ('candidate_recipe_review_decisions_decision_channel_check','slack'),
    ('candidate_recipe_review_decisions_decision_channel_check','telegram');

ALTER TABLE app.candidate_recipe_review_decisions DROP CONSTRAINT candidate_recipe_review_decisions_decision_channel_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.candidate_recipe_review_decisions WHERE NOT (control.sql_dispatch_value_allowed('candidate_recipe_review_decisions_decision_channel_check',decision_channel))) THEN
        RAISE EXCEPTION 'candidate_recipe_review_decisions_decision_channel_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_candidate_recipe_review_decisions_decision_channel_check BEFORE INSERT OR UPDATE ON app.candidate_recipe_review_decisions
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('candidate_recipe_review_decisions_decision_channel_check','control.sql_dispatch_value_allowed(''candidate_recipe_review_decisions_decision_channel_check'',decision_channel)');

INSERT INTO control.sql_dispatch_shapes (contract_key,rule_key,row_type,predicate_sql) VALUES
    ('candidate_recipe_evidence_kind','01','app.candidate_recipe_revisions','(audit_report_id IS NOT NULL)'),
    ('candidate_recipe_evidence_kind','02','app.candidate_recipe_revisions','COALESCE(((((convert_from(canonical_manifest, ''UTF8''::name))::jsonb #>> ''{evidence,finding,key}''::text[]) = ANY (ARRAY[''indexnow.key.required''::text, ''links.internal.add''::text])) AND ((((convert_from(canonical_manifest, ''UTF8''::name))::jsonb ->> ''approval_class''::text) = ''owner_review''::text) OR ((((convert_from(canonical_manifest, ''UTF8''::name))::jsonb #>> ''{evidence,finding,key}''::text[]) = ''indexnow.key.required''::text) AND (((convert_from(canonical_manifest, ''UTF8''::name))::jsonb ->> ''approval_class''::text) = ''A4''::text) AND (((convert_from(canonical_manifest, ''UTF8''::name))::jsonb ->> ''autonomy_eligible''::text) = ''false''::text) AND ((convert_from(canonical_manifest, ''UTF8''::name))::jsonb ? ''static_key_placement''::text)))), false)');

ALTER TABLE app.candidate_recipe_revisions DROP CONSTRAINT candidate_recipe_evidence_kind;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.candidate_recipe_revisions WHERE NOT (control.sql_dispatch_shape_allowed('candidate_recipe_evidence_kind',jsonb_build_object('audit_report_id',audit_report_id,'canonical_manifest',canonical_manifest)))) THEN
        RAISE EXCEPTION 'candidate_recipe_evidence_kind validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_candidate_recipe_evidence_kind BEFORE INSERT OR UPDATE ON app.candidate_recipe_revisions
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('candidate_recipe_evidence_kind','control.sql_dispatch_shape_allowed(''candidate_recipe_evidence_kind'',jsonb_build_object(''audit_report_id'',audit_report_id,''canonical_manifest'',canonical_manifest))');

INSERT INTO control.sql_dispatch_shapes (contract_key,rule_key,row_type,predicate_sql) VALUES
    ('command_events_shape_check','01','app.command_events','((event_number = 1) AND (event_type = ''command.accepted''::text) AND (facts = ''{"schema_version": 1}''::jsonb))'),
    ('command_events_shape_check','02','app.command_events','((event_number = 2) AND (event_type = ''command.workflow_admitted''::text) AND (facts = jsonb_build_object(''consumer_key'', ''workflow.command-start.v1'', ''schema_version'', 1, ''workflow_id'', (((''signal:CrawlSite:''::text || (tenant_id)::text) || '':''::text) || (command_id)::text))))'),
    ('command_events_shape_check','03','app.command_events','((event_number = 3) AND (event_type = ''command.workflow_started''::text) AND ((facts ->> ''first_run_id''::text) ~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$''::text) AND (facts = jsonb_build_object(''first_run_id'', (facts ->> ''first_run_id''::text), ''schema_version'', 1, ''start_evidence'', (facts ->> ''start_evidence''::text), ''workflow_id'', (((''signal:CrawlSite:''::text || (tenant_id)::text) || '':''::text) || (command_id)::text))) AND ((facts ->> ''start_evidence''::text) = ANY (ARRAY[''start_acknowledged''::text, ''already_started''::text])))'),
    ('command_events_shape_check','04','app.command_events','((event_number = 4) AND (event_type = ''command.workflow_succeeded''::text) AND ((facts ->> ''first_run_id''::text) ~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$''::text) AND (facts = jsonb_build_object(''first_run_id'', (facts ->> ''first_run_id''::text), ''result_reference'', (facts -> ''result_reference''::text), ''schema_version'', 1, ''workflow_id'', (((''signal:CrawlSite:''::text || (tenant_id)::text) || '':''::text) || (command_id)::text))) AND ((facts -> ''result_reference''::text) = jsonb_build_object(''crawl_policy_version'', (((facts -> ''result_reference''::text) ->> ''crawl_policy_version''::text))::integer, ''coverage'', ((facts -> ''result_reference''::text) ->> ''coverage''::text), ''discovered_count'', (((facts -> ''result_reference''::text) ->> ''discovered_count''::text))::integer, ''kind'', ''crawl_manifest'', ''manifest_id'', ((facts -> ''result_reference''::text) ->> ''manifest_id''::text), ''manifest_sha256'', ((facts -> ''result_reference''::text) ->> ''manifest_sha256''::text), ''schema_version'', 1, ''scope_version'', (((facts -> ''result_reference''::text) ->> ''scope_version''::text))::integer, ''terminal_count'', (((facts -> ''result_reference''::text) ->> ''terminal_count''::text))::integer)) AND (((facts -> ''result_reference''::text) ->> ''manifest_id''::text) ~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$''::text) AND (((facts -> ''result_reference''::text) ->> ''manifest_sha256''::text) ~ ''^[0-9a-f]{64}$''::text) AND (((facts -> ''result_reference''::text) ->> ''coverage''::text) = ANY (ARRAY[''complete''::text, ''partial''::text])) AND (((((facts -> ''result_reference''::text) ->> ''discovered_count''::text))::integer >= 0) AND ((((facts -> ''result_reference''::text) ->> ''discovered_count''::text))::integer <= 1000000)) AND (((((facts -> ''result_reference''::text) ->> ''terminal_count''::text))::integer >= 0) AND ((((facts -> ''result_reference''::text) ->> ''terminal_count''::text))::integer <= (((facts -> ''result_reference''::text) ->> ''discovered_count''::text))::integer)) AND (((((facts -> ''result_reference''::text) ->> ''scope_version''::text))::integer >= 1) AND ((((facts -> ''result_reference''::text) ->> ''scope_version''::text))::integer <= 2147483647)) AND (((((facts -> ''result_reference''::text) ->> ''crawl_policy_version''::text))::integer >= 1) AND ((((facts -> ''result_reference''::text) ->> ''crawl_policy_version''::text))::integer <= 2147483647)))'),
    ('command_events_shape_check','05','app.command_events','((event_number = 4) AND (event_type = ANY (ARRAY[''command.workflow_failed''::text, ''command.workflow_cancelled''::text])) AND ((facts ->> ''first_run_id''::text) ~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$''::text) AND (facts = jsonb_build_object(''first_run_id'', (facts ->> ''first_run_id''::text), ''reason'', (facts ->> ''reason''::text), ''schema_version'', 1, ''workflow_id'', (((''signal:CrawlSite:''::text || (tenant_id)::text) || '':''::text) || (command_id)::text))) AND (((event_type = ''command.workflow_failed''::text) AND ((facts ->> ''reason''::text) = ''crawl_activity_failed''::text)) OR ((event_type = ''command.workflow_cancelled''::text) AND ((facts ->> ''reason''::text) = ''crawl_cancelled''::text))))');

ALTER TABLE app.command_events DROP CONSTRAINT command_events_shape_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.command_events WHERE NOT (control.sql_dispatch_shape_allowed('command_events_shape_check',jsonb_build_object('event_number',event_number,'event_type',event_type,'facts',facts,'tenant_id',tenant_id,'command_id',command_id)))) THEN
        RAISE EXCEPTION 'command_events_shape_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_command_events_shape_check BEFORE INSERT OR UPDATE ON app.command_events
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('command_events_shape_check','control.sql_dispatch_shape_allowed(''command_events_shape_check'',jsonb_build_object(''event_number'',event_number,''event_type'',event_type,''facts'',facts,''tenant_id'',tenant_id,''command_id'',command_id))');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('commands_status_check','accepted'),
    ('commands_status_check','workflow_admitted'),
    ('commands_status_check','processing'),
    ('commands_status_check','succeeded'),
    ('commands_status_check','failed'),
    ('commands_status_check','cancelled');

ALTER TABLE app.commands DROP CONSTRAINT commands_status_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.commands WHERE NOT (control.sql_dispatch_value_allowed('commands_status_check',status))) THEN
        RAISE EXCEPTION 'commands_status_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_commands_status_check BEFORE INSERT OR UPDATE ON app.commands
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('commands_status_check','control.sql_dispatch_value_allowed(''commands_status_check'',status)');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('egress_operations_egress_profile_check','legacy_unqualified'),
    ('egress_operations_egress_profile_check','crawl_page'),
    ('egress_operations_egress_profile_check','crawl_robots'),
    ('egress_operations_egress_profile_check','browser_read'),
    ('egress_operations_egress_profile_check','github_rest'),
    ('egress_operations_egress_profile_check','slack_bot'),
    ('egress_operations_egress_profile_check','slack_oauth'),
    ('egress_operations_egress_profile_check','github_repository_write'),
    ('egress_operations_egress_profile_check','google_oauth_token'),
    ('egress_operations_egress_profile_check','google_oauth_revoke'),
    ('egress_operations_egress_profile_check','gsc_api'),
    ('egress_operations_egress_profile_check','bing_oauth_token'),
    ('egress_operations_egress_profile_check','bing_api'),
    ('egress_operations_egress_profile_check','jev'),
    ('egress_operations_egress_profile_check','model_json'),
    ('egress_operations_egress_profile_check','openai_model'),
    ('egress_operations_egress_profile_check','openai_assistant'),
    ('egress_operations_egress_profile_check','perplexity_assistant'),
    ('egress_operations_egress_profile_check','gemini_assistant'),
    ('egress_operations_egress_profile_check','npm_registry'),
    ('egress_operations_egress_profile_check','crawl_key_file'),
    ('egress_operations_egress_profile_check','indexnow_submit'),
    ('egress_operations_egress_profile_check','dataforseo'),
    ('egress_operations_egress_profile_check','ga4_admin'),
    ('egress_operations_egress_profile_check','ga4_data'),
    ('egress_operations_egress_profile_check','telegram_bot'),
    ('egress_operations_egress_profile_check','browser_worker_read'),
    ('egress_operations_egress_profile_check','wordpress_rest'),
    ('egress_operations_egress_profile_check','drive_metadata'),
    ('egress_operations_egress_profile_check','drive_export'),
    ('egress_operations_egress_profile_check','webflow'),
    ('egress_operations_egress_profile_check','webflow_oauth'),
    ('egress_operations_egress_profile_check','webflow_revoke');

ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.egress_operations WHERE NOT (control.sql_dispatch_value_allowed('egress_operations_egress_profile_check',egress_profile))) THEN
        RAISE EXCEPTION 'egress_operations_egress_profile_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_egress_operations_egress_profile_check BEFORE INSERT OR UPDATE ON app.egress_operations
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('egress_operations_egress_profile_check','control.sql_dispatch_value_allowed(''egress_operations_egress_profile_check'',egress_profile)');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('github_pr_operations_decision_channel_check','dashboard'),
    ('github_pr_operations_decision_channel_check','slack'),
    ('github_pr_operations_decision_channel_check','telegram');

ALTER TABLE app.github_pr_operations DROP CONSTRAINT github_pr_operations_decision_channel_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.github_pr_operations WHERE NOT (control.sql_dispatch_value_allowed('github_pr_operations_decision_channel_check',decision_channel))) THEN
        RAISE EXCEPTION 'github_pr_operations_decision_channel_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_github_pr_operations_decision_channel_check BEFORE INSERT OR UPDATE ON app.github_pr_operations
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('github_pr_operations_decision_channel_check','control.sql_dispatch_value_allowed(''github_pr_operations_decision_channel_check'',decision_channel)');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('model_budget_calls_role_check','page_type'),
    ('model_budget_calls_role_check','fact_extraction'),
    ('model_budget_calls_role_check','metadata_draft'),
    ('model_budget_calls_role_check','claim_check'),
    ('model_budget_calls_role_check','article_outline'),
    ('model_budget_calls_role_check','article_draft'),
    ('model_budget_calls_role_check','article_critique'),
    ('model_budget_calls_role_check','article_revise'),
    ('model_budget_calls_role_check','report_text'),
    ('model_budget_calls_role_check','topic_ideas'),
    ('model_budget_calls_role_check','owner_answers');

ALTER TABLE app.model_budget_calls DROP CONSTRAINT model_budget_calls_role_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.model_budget_calls WHERE NOT (control.sql_dispatch_value_allowed('model_budget_calls_role_check',role))) THEN
        RAISE EXCEPTION 'model_budget_calls_role_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_model_budget_calls_role_check BEFORE INSERT OR UPDATE ON app.model_budget_calls
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('model_budget_calls_role_check','control.sql_dispatch_value_allowed(''model_budget_calls_role_check'',role)');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('owner_connector_egress_operations_profile_check','github_rest'),
    ('owner_connector_egress_operations_profile_check','google_oauth_token'),
    ('owner_connector_egress_operations_profile_check','google_oauth_revoke'),
    ('owner_connector_egress_operations_profile_check','gsc_api'),
    ('owner_connector_egress_operations_profile_check','slack_oauth'),
    ('owner_connector_egress_operations_profile_check','slack_bot'),
    ('owner_connector_egress_operations_profile_check','pagespeed'),
    ('owner_connector_egress_operations_profile_check','webflow'),
    ('owner_connector_egress_operations_profile_check','webflow_oauth'),
    ('owner_connector_egress_operations_profile_check','webflow_revoke'),
    ('owner_connector_egress_operations_profile_check','bing_oauth_token'),
    ('owner_connector_egress_operations_profile_check','bing_api'),
    ('owner_connector_egress_operations_profile_check','dataforseo');

ALTER TABLE app.owner_connector_egress_operations DROP CONSTRAINT owner_connector_egress_operations_profile_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.owner_connector_egress_operations WHERE NOT (control.sql_dispatch_value_allowed('owner_connector_egress_operations_profile_check',profile))) THEN
        RAISE EXCEPTION 'owner_connector_egress_operations_profile_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_owner_connector_egress_operations_profile_check BEFORE INSERT OR UPDATE ON app.owner_connector_egress_operations
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('owner_connector_egress_operations_profile_check','control.sql_dispatch_value_allowed(''owner_connector_egress_operations_profile_check'',profile)');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('weekly_skill_intents_stage_check','import_gsc'),
    ('weekly_skill_intents_stage_check','import_bing'),
    ('weekly_skill_intents_stage_check','import_ga4'),
    ('weekly_skill_intents_stage_check','pagespeed_refresh'),
    ('weekly_skill_intents_stage_check','visibility_reobserve'),
    ('weekly_skill_intents_stage_check','brain_refresh'),
    ('weekly_skill_intents_stage_check','strategy_rebuild'),
    ('weekly_skill_intents_stage_check','internal_link_proposals'),
    ('weekly_skill_intents_stage_check','brief_proposals'),
    ('weekly_skill_intents_stage_check','report_delivery'),
    ('weekly_skill_intents_stage_check','chat_report_delivery');

ALTER TABLE app.weekly_skill_intents DROP CONSTRAINT weekly_skill_intents_stage_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.weekly_skill_intents WHERE NOT (control.sql_dispatch_value_allowed('weekly_skill_intents_stage_check',stage))) THEN
        RAISE EXCEPTION 'weekly_skill_intents_stage_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_weekly_skill_intents_stage_check BEFORE INSERT OR UPDATE ON app.weekly_skill_intents
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('weekly_skill_intents_stage_check','control.sql_dispatch_value_allowed(''weekly_skill_intents_stage_check'',stage)');

INSERT INTO control.sql_dispatch_values (contract_key,value) VALUES
    ('weekly_skill_results_stage_check','import_gsc'),
    ('weekly_skill_results_stage_check','import_bing'),
    ('weekly_skill_results_stage_check','import_ga4'),
    ('weekly_skill_results_stage_check','pagespeed_refresh'),
    ('weekly_skill_results_stage_check','visibility_reobserve'),
    ('weekly_skill_results_stage_check','brain_refresh'),
    ('weekly_skill_results_stage_check','strategy_rebuild'),
    ('weekly_skill_results_stage_check','internal_link_proposals'),
    ('weekly_skill_results_stage_check','brief_proposals'),
    ('weekly_skill_results_stage_check','report_delivery'),
    ('weekly_skill_results_stage_check','chat_report_delivery');

ALTER TABLE app.weekly_skill_results DROP CONSTRAINT weekly_skill_results_stage_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM app.weekly_skill_results WHERE NOT (control.sql_dispatch_value_allowed('weekly_skill_results_stage_check',stage))) THEN
        RAISE EXCEPTION 'weekly_skill_results_stage_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_weekly_skill_results_stage_check BEFORE INSERT OR UPDATE ON app.weekly_skill_results
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('weekly_skill_results_stage_check','control.sql_dispatch_value_allowed(''weekly_skill_results_stage_check'',stage)');

INSERT INTO control.sql_dispatch_shapes (contract_key,rule_key,row_type,predicate_sql) VALUES
    ('admission_leases_workload_id_check','01','control.admission_leases','workload_id ~ ''^(crawl|egress|owner|psi):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$''::text');

ALTER TABLE control.admission_leases DROP CONSTRAINT admission_leases_workload_id_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM control.admission_leases WHERE NOT (control.sql_dispatch_shape_allowed('admission_leases_workload_id_check',jsonb_build_object('workload_id',workload_id)))) THEN
        RAISE EXCEPTION 'admission_leases_workload_id_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_admission_leases_workload_id_check BEFORE INSERT OR UPDATE ON control.admission_leases
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('admission_leases_workload_id_check','control.sql_dispatch_shape_allowed(''admission_leases_workload_id_check'',jsonb_build_object(''workload_id'',workload_id))');

ALTER TABLE control.authority_denial_tombstones DROP CONSTRAINT authority_denial_tombstones_restriction_kind_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE NOT (control.authority_kind_allowed(target_kind,restriction_kind,false))) THEN
        RAISE EXCEPTION 'authority_denial_tombstones_restriction_kind_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_authority_denial_tombstones_restriction_kind_check BEFORE INSERT OR UPDATE ON control.authority_denial_tombstones
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('authority_denial_tombstones_restriction_kind_check','control.authority_kind_allowed(target_kind,restriction_kind,false)');

ALTER TABLE control.authority_denial_tombstones DROP CONSTRAINT authority_denial_tombstones_target_kind_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE NOT (control.authority_kind_allowed(target_kind,restriction_kind,true))) THEN
        RAISE EXCEPTION 'authority_denial_tombstones_target_kind_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_authority_denial_tombstones_target_kind_check BEFORE INSERT OR UPDATE ON control.authority_denial_tombstones
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('authority_denial_tombstones_target_kind_check','control.authority_kind_allowed(target_kind,restriction_kind,true)');

ALTER TABLE control.authority_restriction_outbox DROP CONSTRAINT authority_restriction_outbox_restriction_kind_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM control.authority_restriction_outbox WHERE NOT (control.authority_kind_allowed(target_kind,restriction_kind,false))) THEN
        RAISE EXCEPTION 'authority_restriction_outbox_restriction_kind_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_authority_restriction_outbox_restriction_kind_check BEFORE INSERT OR UPDATE ON control.authority_restriction_outbox
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('authority_restriction_outbox_restriction_kind_check','control.authority_kind_allowed(target_kind,restriction_kind,false)');

ALTER TABLE control.authority_restriction_outbox DROP CONSTRAINT authority_restriction_outbox_target_kind_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM control.authority_restriction_outbox WHERE NOT (control.authority_kind_allowed(target_kind,restriction_kind,true))) THEN
        RAISE EXCEPTION 'authority_restriction_outbox_target_kind_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_authority_restriction_outbox_target_kind_check BEFORE INSERT OR UPDATE ON control.authority_restriction_outbox
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('authority_restriction_outbox_target_kind_check','control.authority_kind_allowed(target_kind,restriction_kind,true)');

INSERT INTO control.sql_dispatch_shapes (contract_key,rule_key,row_type,predicate_sql) VALUES
    ('platform_events_contract_check','01','control.platform_events','((event_type = ''identity.session.issued''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''identity_session''::text) AND (reason IS NULL) AND (jsonb_typeof(facts) = ''object''::text) AND (((facts - ''schema_version''::text) - ''authentication_level''::text) = ''{}''::jsonb) AND ((facts -> ''schema_version''::text) = ''1''::jsonb) AND (jsonb_typeof((facts -> ''authentication_level''::text)) = ''string''::text) AND ((facts ->> ''authentication_level''::text) = ANY (ARRAY[''primary''::text, ''mfa''::text])))'),
    ('platform_events_contract_check','02','control.platform_events','((event_type = ''identity.login.failed''::text) AND (actor_user_id IS NULL) AND (object_kind = ''oidc_login_attempt''::text) AND (reason = ANY (ARRAY[''provider_configuration_failed''::text, ''pkce_unavailable''::text, ''pkce_consume_failed''::text, ''provider_assertion_failed''::text, ''identity_not_authorized''::text, ''session_persistence_failed''::text, ''invitation_identity_not_verified''::text, ''invitation_proof_persistence_failed''::text])) AND (facts = ''{"schema_version": 1}''::jsonb))'),
    ('platform_events_contract_check','03','control.platform_events','((event_type = ''identity.session.revoked''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''identity_session''::text) AND (reason = ''user_logout''::text) AND (jsonb_typeof(facts) = ''object''::text) AND (((facts - ''schema_version''::text) - ''presented_session_kind''::text) = ''{}''::jsonb) AND ((facts -> ''schema_version''::text) = ''1''::jsonb) AND (jsonb_typeof((facts -> ''presented_session_kind''::text)) = ''string''::text) AND ((facts ->> ''presented_session_kind''::text) = ANY (ARRAY[''identity''::text, ''tenant''::text])))'),
    ('platform_events_contract_check','04','control.platform_events','((event_type = ''authority.restriction.acknowledged''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''authority_restriction''::text) AND (reason IS NULL) AND (jsonb_typeof(facts) = ''object''::text) AND ((facts -> ''schema_version''::text) = ''1''::jsonb) AND ((((((((facts - ''schema_version''::text) - ''original_event_id''::text) - ''stream_generation''::text) - ''stream_position''::text) - ''journal_record_id''::text) - ''payload_hash''::text) - ''verified_at''::text) = ''{}''::jsonb) AND ((facts ->> ''original_event_id''::text) = (object_id)::text) AND ((facts ->> ''journal_record_id''::text) = (object_id)::text) AND ((facts ->> ''stream_generation''::text) ~ ''^[0-9a-f-]{36}$''::text) AND ((facts ->> ''stream_position''::text) ~ ''^[1-9][0-9]*$''::text) AND ((facts ->> ''payload_hash''::text) ~ ''^[0-9a-f]{64}$''::text) AND (jsonb_typeof((facts -> ''verified_at''::text)) = ''string''::text))'),
    ('platform_events_contract_check','05','control.platform_events','((event_type = ''recipe.release.revoked''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''recipe_release''::text) AND (reason = ''operator_revocation''::text) AND (jsonb_typeof(facts) = ''object''::text) AND ((((facts - ''schema_version''::text) - ''status_event_id''::text) - ''restriction_kind''::text) = ''{}''::jsonb) AND ((facts -> ''schema_version''::text) = ''1''::jsonb) AND ((facts ->> ''restriction_kind''::text) = ''recipe_release_revoked''::text) AND (jsonb_typeof((facts -> ''status_event_id''::text)) = ''string''::text) AND ((facts ->> ''status_event_id''::text) = (id)::text))'),
    ('platform_events_contract_check','06','control.platform_events','((event_type = ''standing.grant.revoked''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''standing_grant''::text) AND (reason = ''owner_revocation''::text) AND (jsonb_typeof(facts) = ''object''::text) AND ((((facts - ''schema_version''::text) - ''restriction_kind''::text) - ''grant_id''::text) = ''{}''::jsonb) AND ((facts -> ''schema_version''::text) = ''1''::jsonb) AND ((facts ->> ''restriction_kind''::text) = ''standing_grant_revoked''::text) AND ((facts ->> ''grant_id''::text) = (object_id)::text))'),
    ('platform_events_contract_check','07','control.platform_events','((event_type = ''ga4.binding.revoked''::text) AND (object_kind = ''ga4_binding''::text) AND (actor_user_id IS NOT NULL) AND (reason = ''owner_revocation''::text) AND (facts = jsonb_build_object(''schema_version'', 1, ''restriction_kind'', ''ga4_binding_revoked'', ''target_id'', object_id)))'),
    ('platform_events_contract_check','08','control.platform_events','((event_type = ANY (ARRAY[''slack.binding.revoked''::text, ''slack.link.revoked''::text, ''telegram.binding.revoked''::text, ''telegram.link.revoked''::text])) AND (actor_user_id IS NOT NULL) AND (object_kind = ANY (ARRAY[''slack_binding''::text, ''slack_link''::text, ''telegram_binding''::text, ''telegram_link''::text])) AND (reason = ''owner_revocation''::text) AND (facts = jsonb_build_object(''schema_version'', 1, ''restriction_kind'', (object_kind || ''_revoked''::text), ''target_id'', object_id)))'),
    ('platform_events_contract_check','09','control.platform_events','((event_type = ''wordpress.binding.revoked''::text) AND (object_kind = ''wordpress_binding''::text) AND (actor_user_id IS NOT NULL) AND (reason = ''owner_revocation''::text) AND (facts = jsonb_build_object(''schema_version'', 1, ''restriction_kind'', ''wordpress_binding_revoked'', ''target_id'', object_id)))'),
    ('platform_events_contract_check','10','control.platform_events','((event_type = ''docs.binding.revoked''::text) AND (object_kind = ''docs_binding''::text) AND (actor_user_id IS NOT NULL) AND (reason = ''owner_revocation''::text) AND (facts = jsonb_build_object(''schema_version'', 1, ''restriction_kind'', ''docs_binding_revoked'', ''target_id'', object_id)))'),
    ('platform_events_contract_check','11','control.platform_events','((event_type = ''webflow.binding.revoked''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''webflow_binding''::text) AND (reason = ''owner_revocation''::text) AND (facts = jsonb_build_object(''schema_version'', 1, ''restriction_kind'', ''webflow_binding_revoked'', ''target_id'', object_id)))'),
    ('platform_events_contract_check','12','control.platform_events','((event_type = ''github.pr.revoked''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''github_pr_extension''::text) AND (reason = ''owner_revocation''::text) AND (facts = jsonb_build_object(''schema_version'', 1, ''restriction_kind'', ''github_pr_extension_revoked'', ''target_id'', object_id)))'),
    ('platform_events_contract_check','13','control.platform_events','((event_type = ''invitation.revoked''::text) AND (actor_user_id IS NOT NULL) AND (object_kind = ''invitation''::text) AND (reason = ''owner_revocation''::text) AND (facts = jsonb_build_object(''schema_version'', 1, ''restriction_kind'', ''invitation_revoked'', ''target_id'', object_id)))');

ALTER TABLE control.platform_events DROP CONSTRAINT platform_events_contract_check;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM control.platform_events WHERE NOT (control.sql_dispatch_shape_allowed('platform_events_contract_check',jsonb_build_object('event_type',event_type,'actor_user_id',actor_user_id,'object_kind',object_kind,'object_id',object_id,'id',id,'reason',reason,'facts',facts)))) THEN
        RAISE EXCEPTION 'platform_events_contract_check validation failed' USING ERRCODE='23514';
    END IF;
END $$;
CREATE TRIGGER z_sql_dispatch_platform_events_contract_check BEFORE INSERT OR UPDATE ON control.platform_events
FOR EACH ROW EXECUTE FUNCTION control.enforce_sql_dispatch('platform_events_contract_check','control.sql_dispatch_shape_allowed(''platform_events_contract_check'',jsonb_build_object(''event_type'',event_type,''actor_user_id'',actor_user_id,''object_kind'',object_kind,''object_id'',object_id,''id'',id,''reason'',reason,''facts'',facts))');

INSERT INTO control.authority_restriction_kinds VALUES
    ('identity_session','session_revoked',false),
    ('recipe_release','recipe_release_revoked',false),
    ('standing_grant','standing_grant_revoked',false),
    ('slack_binding','slack_binding_revoked',false),
    ('slack_link','slack_link_revoked',false),
    ('ga4_binding','ga4_binding_revoked',false),
    ('telegram_binding','telegram_binding_revoked',false),
    ('telegram_link','telegram_link_revoked',false),
    ('wordpress_binding','wordpress_binding_revoked',false),
    ('docs_binding','docs_binding_revoked',true),
    ('webflow_binding','webflow_binding_revoked',false),
    ('github_pr_extension','github_pr_extension_revoked',false),
    ('invitation','invitation_revoked',false);

INSERT INTO control.platform_event_references (event_type,reference_table,predicate_sql) VALUES
    ('identity.session.issued','control.identity_sessions','r.id=e.object_id AND r.user_id=e.actor_user_id'),
    ('identity.session.revoked','control.identity_sessions','r.id=e.object_id AND r.user_id=e.actor_user_id AND r.revoked_at IS NOT NULL'),
    ('identity.login.failed','control.oidc_login_attempts','r.id=e.object_id AND r.consumed_at IS NOT NULL'),
    ('authority.restriction.acknowledged','control.authority_restriction_outbox','r.event_id=e.object_id AND r.actor_user_id=e.actor_user_id'),
    ('recipe.release.revoked','control.recipe_release_events','r.id=e.id AND r.release_id=e.object_id AND r.actor_user_id=e.actor_user_id AND r.status=''REVOKED'' AND r.source=''operator'''),
    ('standing.grant.revoked','app.standing_authorization_revocations','r.id=e.id AND r.grant_id=e.object_id AND r.actor_user_id=e.actor_user_id'),
    ('slack.binding.revoked','control.slack_revocations','r.event_id=e.id AND r.target_id=e.object_id AND r.actor_user_id=e.actor_user_id AND r.target_kind=e.object_kind'),
    ('slack.link.revoked','control.slack_revocations','r.event_id=e.id AND r.target_id=e.object_id AND r.actor_user_id=e.actor_user_id AND r.target_kind=e.object_kind'),
    ('telegram.binding.revoked','control.telegram_revocations','r.event_id=e.id AND r.target_id=e.object_id AND r.actor_user_id=e.actor_user_id AND r.target_kind=e.object_kind'),
    ('telegram.link.revoked','control.telegram_revocations','r.event_id=e.id AND r.target_id=e.object_id AND r.actor_user_id=e.actor_user_id AND r.target_kind=e.object_kind'),
    ('ga4.binding.revoked','control.ga4_binding_restrictions','r.event_id=e.id AND r.target_id=e.object_id AND r.actor_user_id=e.actor_user_id'),
    ('wordpress.binding.revoked','control.wordpress_revocations','r.event_id=e.id AND r.binding_id=e.object_id AND r.actor_id=e.actor_user_id'),
    ('docs.binding.revoked','control.docs_revocations','r.event_id=e.id AND r.binding_id=e.object_id AND r.actor_user_id=e.actor_user_id'),
    ('webflow.binding.revoked',NULL,NULL),
    ('github.pr.revoked',NULL,NULL),
    ('invitation.revoked',NULL,NULL);

UPDATE control.platform_event_references SET failure_message='invalid_docs_revocation' WHERE event_type='docs.binding.revoked';

INSERT INTO control.owner_egress_profiles (profile,robots_get,robots_post_pattern,nullable_request_result) VALUES
    ('github_rest',true,'/access_tokens$|/searchAnalytics/query$',false),
    ('google_oauth_token',false,'/access_tokens$|/searchAnalytics/query$',false),
    ('google_oauth_revoke',false,'/access_tokens$|/searchAnalytics/query$',false),
    ('gsc_api',true,'/access_tokens$|/searchAnalytics/query$',false),
    ('slack_oauth',false,'/access_tokens$|/searchAnalytics/query$',false),
    ('slack_bot',false,'/access_tokens$|/searchAnalytics/query$',false),
    ('pagespeed',true,'/access_tokens$|/searchAnalytics/query$',true),
    ('webflow',true,'/access_tokens$|/searchAnalytics/query$',false),
    ('webflow_oauth',false,'/access_tokens$|/searchAnalytics/query$',false),
    ('webflow_revoke',false,'/access_tokens$|/searchAnalytics/query$',false),
    ('bing_oauth_token',false,'/access_tokens$|/searchAnalytics/query$',false),
    ('bing_api',true,'/access_tokens$|/searchAnalytics/query$',false),
    ('dataforseo',false,'/access_tokens$|/searchAnalytics/query$',false);

UPDATE control.owner_egress_profiles SET admission_sql='control.webflow_owner_egress_allowed($1,$2,$3,$4,$5)'
    WHERE profile IN ('webflow','webflow_oauth','webflow_revoke');
UPDATE control.owner_egress_profiles SET admission_sql='control.pagespeed_admission_allowed($6,$3,$7,$8,$5,$9)'
    WHERE profile='pagespeed';
INSERT INTO control.owner_egress_request_rules (profile,method,exact_url,anchored_pattern) VALUES
    ('github_rest','POST',NULL,'^https://api[.]github[.]com/app/installations/[1-9][0-9]*/access_tokens$'),
    ('github_rest','GET',NULL,'^https://api[.]github[.]com/app/installations/[1-9][0-9]*$'),
    ('github_rest','GET',NULL,'^https://api[.]github[.]com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/branches/[^?#[:space:]]+|/git/(commits|blobs)/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}[?]recursive=1)?$'),
    ('google_oauth_token','POST','https://oauth2.googleapis.com/token',NULL),
    ('google_oauth_revoke','POST','https://oauth2.googleapis.com/revoke',NULL),
    ('gsc_api','GET','https://www.googleapis.com/webmasters/v3/sites',NULL),
    ('gsc_api','POST',NULL,'^https://www[.]googleapis[.]com/webmasters/v3/sites/[^/?#[:space:]]+/searchAnalytics/query$'),
    ('slack_oauth','POST','https://slack.com/api/oauth.v2.access',NULL),
    ('slack_bot','POST','https://slack.com/api/chat.postMessage',NULL),
    ('slack_bot','POST','https://slack.com/api/auth.revoke',NULL),
    ('pagespeed','GET',NULL,'^https://www[.]googleapis[.]com/pagespeedonline/v5/runPagespeed[?]url=[^&?#[:space:]]+&strategy=(mobile|desktop)&category=performance$'),
    ('webflow_oauth','POST','https://api.webflow.com/oauth/access_token',NULL),
    ('webflow_revoke','POST','https://webflow.com/oauth/revoke_authorization',NULL),
    ('webflow','GET','https://api.webflow.com/v2/token/introspect',NULL),
    ('webflow','GET',NULL,'^https://api[.]webflow[.]com/v2/sites/[0-9a-f]{24}/(custom_domains|collections)$'),
    ('webflow','GET',NULL,'^https://api[.]webflow[.]com/v2/collections/[0-9a-f]{24}$'),
    ('bing_oauth_token','POST','https://www.bing.com/webmasters/oauth/token',NULL),
    ('bing_api','GET','https://www.bing.com/webmaster/api.svc/json/GetUserSites',NULL),
    ('bing_api','GET',NULL,'^https://www[.]bing[.]com/webmaster/api[.]svc/json/(GetRankAndTrafficStats|GetPageStats)[?]siteUrl=[A-Za-z0-9%%._~-]+$'),
    ('dataforseo','POST','https://api.dataforseo.com/v3/serp/google/organic/live/advanced',NULL),
    ('dataforseo','POST','https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live',NULL),
    ('dataforseo','POST','https://api.dataforseo.com/v3/backlinks/summary/live',NULL);

CREATE OR REPLACE FUNCTION control.begin_owner_connector_egress(p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_kind text, p_profile text, p_method text, p_request_url text, p_target_url text, p_origin text, p_request_sha256 bytea, p_body_sha256 bytea, p_request_bytes integer, p_max_response_bytes integer, p_credentialed boolean, p_robots_operation_id uuid, p_min_delay_ms integer)
 RETURNS TABLE(outcome text, bucket_id uuid, expires_at timestamp with time zone, robots_allowed boolean, robots_crawl_delay_ms integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
            CASE WHEN p_kind = 'robots' THEN control.owner_connector_robots_method(p_profile,p_target_url)
                ELSE p_method END, p_target_url) IS NOT TRUE
       OR (p_method = 'GET' AND (p_request_bytes <> 0 OR p_body_sha256 IS NOT NULL))
       OR (p_method = 'POST' AND octet_length(p_body_sha256) IS DISTINCT FROM 32)
       OR p_method IS NULL OR p_method NOT IN ('GET', 'POST')
       OR (p_kind = 'robots' AND (p_method <> 'GET' OR p_credentialed
            OR p_request_url IS DISTINCT FROM p_origin || '/robots.txt' OR p_robots_operation_id IS NOT NULL))
       OR (p_kind = 'provider' AND (p_target_url IS DISTINCT FROM p_request_url OR p_robots_operation_id IS NULL))
    THEN RAISE EXCEPTION 'invalid_owner_connector_request' USING ERRCODE = '22023'; END IF;
    IF p_profile='dataforseo' AND p_kind='provider' THEN PERFORM control.assert_strategy_provider_dispatch(p_session_hash,p_generation,p_site_id,p_operation_id,p_target_url,p_body_sha256); END IF; SELECT * INTO v_owner FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF v_owner.outcome IS DISTINCT FROM 'authorized' OR v_owner.role_key IS DISTINCT FROM 'owner'
       OR v_owner.authentication_level IS DISTINCT FROM 'mfa'
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    SELECT * INTO v_verified FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_verified.outcome IS DISTINCT FROM 'verified'
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    IF control.owner_connector_profile_admitted(p_session_hash,p_generation,p_site_id,p_profile,
        p_target_url,v_owner.tenant_id,p_operation_id,p_kind,v_verified.origin) IS NOT TRUE
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
END; $function$
;

CREATE FUNCTION control.require_wordpress_egress_authority(o app.egress_operations) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE suffix text; BEGIN
 suffix:=substr(o.request_url,length(o.origin)+1);
 IF o.method='POST' THEN
  IF suffix<>'/wp-json/wp/v2/posts' OR NOT EXISTS(SELECT 1 FROM app.wordpress_intents i
   JOIN app.wordpress_candidates c ON c.tenant_id=i.tenant_id AND c.site_id=i.site_id AND c.id=i.candidate_id
   JOIN app.wordpress_bindings b ON b.tenant_id=c.tenant_id AND b.site_id=c.site_id AND b.id=c.binding_id
   JOIN app.wordpress_receipts r ON r.tenant_id=i.tenant_id AND r.site_id=i.site_id AND r.intent_id=i.id AND r.outcome='dispatching'
   WHERE i.tenant_id=o.tenant_id AND i.site_id=o.site_id AND control.wordpress_dispatch_current(i.id) AND i.journal_receipt IS NOT NULL
   AND r.message_sha256=o.request_body_sha256 AND b.origin=o.origin AND control.wordpress_binding_current(b.id,i.recovery_generation)) THEN
    RAISE EXCEPTION 'wordpress_write_denied' USING ERRCODE='22023'; END IF;
 ELSE
  IF NOT (suffix='/wp-json/wp/v2/users/me?context=edit' AND EXISTS(SELECT 1 FROM control.public_origin_claims c WHERE c.tenant_id=o.tenant_id AND c.site_id=o.site_id AND c.origin=o.origin AND c.recheck_at>transaction_timestamp()))
  AND NOT EXISTS(SELECT 1 FROM app.wordpress_bindings b WHERE b.tenant_id=o.tenant_id AND b.site_id=o.site_id AND b.origin=o.origin
   AND control.wordpress_binding_current(b.id,b.recovery_generation) AND (
    EXISTS(SELECT 1 FROM app.wordpress_intents i JOIN app.wordpress_candidates c ON c.tenant_id=i.tenant_id AND c.site_id=i.site_id AND c.id=i.candidate_id
     WHERE c.binding_id=b.id AND suffix='/wp-json/wp/v2/posts?slug='||i.marker||'&status=draft&context=edit&author='||b.user_id::text||'&per_page=100')
    OR EXISTS(SELECT 1 FROM app.wordpress_intents i JOIN app.wordpress_candidates c ON c.tenant_id=i.tenant_id AND c.site_id=i.site_id AND c.id=i.candidate_id
     WHERE c.binding_id=b.id AND i.state='recorded' AND suffix='/wp-json/wp/v2/posts/'||i.post_id::text||'?context=edit'))) THEN
    RAISE EXCEPTION 'wordpress_read_denied' USING ERRCODE='22023'; END IF;
 END IF;

END $$;
REVOKE ALL ON FUNCTION control.require_wordpress_egress_authority(app.egress_operations) FROM PUBLIC;

INSERT INTO control.shared_egress_profiles (profile,predicate_sql,failure_message,authority_guard,always_deny) VALUES
    ('crawl_page','o.purpose=''crawl'' AND o.method=''GET'' AND NOT o.credentialed','egress_profile_conflict',NULL,false),
    ('crawl_robots','o.purpose=''crawl'' AND o.method=''GET'' AND NOT o.credentialed','egress_profile_conflict',NULL,false),
    ('browser_read','o.purpose=''browser'' AND o.method=''GET'' AND NOT o.credentialed','egress_profile_conflict',NULL,false),
    ('github_rest','o.purpose=''connector'' AND o.origin=''https://api.github.com''','egress_profile_conflict',NULL,false),
    ('google_oauth_token','o.purpose=''connector'' AND o.origin=''https://oauth2.googleapis.com''','egress_profile_conflict',NULL,false),
    ('google_oauth_revoke','o.purpose=''connector'' AND o.origin=''https://oauth2.googleapis.com''','egress_profile_conflict',NULL,false),
    ('gsc_api','o.purpose=''connector'' AND o.origin=''https://www.googleapis.com''','egress_profile_conflict',NULL,false),
    ('bing_oauth_token','o.purpose=''connector'' AND o.origin=''https://www.bing.com''','egress_profile_conflict',NULL,false),
    ('bing_api','o.purpose=''connector'' AND o.origin=''https://www.bing.com''','egress_profile_conflict',NULL,false),
    ('jev','o.purpose=''model'' AND o.origin=''https://api.typesafe.ai''','egress_profile_conflict',NULL,false),
    ('openai_model','o.purpose=''model'' AND o.origin=''https://api.openai.com''','egress_profile_conflict',NULL,false),
    ('model_json','o.purpose=''model''','egress_profile_conflict',NULL,false),
    ('openai_assistant','false','assistant_egress_profile_requires_model_purpose',NULL,true),
    ('perplexity_assistant','false','assistant_egress_profile_requires_model_purpose',NULL,true),
    ('gemini_assistant','false','assistant_egress_profile_requires_model_purpose',NULL,true),
    ('slack_bot','o.purpose=''connector'' AND o.origin=''https://slack.com'' AND o.credentialed AND o.request_bytes<=16384 AND o.max_response_bytes<=16384','slack_profile_denied',NULL,false),
    ('slack_oauth','o.purpose=''connector'' AND o.origin=''https://slack.com'' AND o.credentialed AND o.request_bytes<=16384 AND o.max_response_bytes<=16384','slack_profile_denied',NULL,false),
    ('indexnow_submit','o.purpose=''connector'' AND o.credentialed AND o.request_bytes<=65536 AND o.max_response_bytes<=4096','indexnow_profile_denied',NULL,false),
    ('crawl_key_file','o.purpose=''crawl'' AND NOT o.credentialed AND o.request_bytes=0 AND o.max_response_bytes<=1024','indexnow_profile_denied',NULL,false),
    ('ga4_admin','o.purpose=''connector'' AND o.credentialed AND o.max_response_bytes<=131072 AND o.request_bytes<=4096 AND o.origin=''https://analyticsadmin.googleapis.com'' AND length(replace(split_part(o.request_url,''&pageToken='',2),''%%3D'',''=''))<=512','ga4_profile_denied',NULL,false),
    ('ga4_data','o.purpose=''connector'' AND o.credentialed AND o.max_response_bytes<=131072 AND o.request_bytes<=4096 AND o.origin=''https://analyticsdata.googleapis.com''','ga4_profile_denied',NULL,false),
    ('telegram_bot','o.purpose=''connector'' AND o.origin=''https://api.telegram.org'' AND o.credentialed AND o.request_bytes<=16384 AND o.max_response_bytes<=16384','telegram_profile_denied',NULL,false),
    ('wordpress_rest','o.purpose=''connector'' AND o.credentialed AND o.request_bytes<=65536 AND o.max_response_bytes<=131072','wordpress_profile_denied','control.require_wordpress_egress_authority(app.egress_operations)',false),
    ('webflow_oauth','o.purpose=''connector'' AND o.credentialed AND o.request_bytes<=8192 AND o.max_response_bytes<=16384','webflow_oauth_profile_denied',NULL,false),
    ('webflow_revoke','o.purpose=''connector'' AND o.credentialed AND o.request_bytes<=8192 AND o.max_response_bytes<=16384','webflow_oauth_profile_denied',NULL,false),
    ('npm_registry','o.purpose=''connector'' AND NOT o.credentialed AND o.request_bytes=0 AND o.request_body_sha256 IS NULL AND o.origin=''https://registry.npmjs.org'' AND o.max_response_bytes<=5242880 AND length(o.request_url)<=1024','npm_registry_profile_denied',NULL,false);

-- The old wrap chain routes NULL to Webflow revoke; retain even this denial edge.
UPDATE control.shared_egress_profiles SET handles_null=true WHERE profile='webflow_revoke';
INSERT INTO control.shared_egress_request_rules (profile,method,exact_url,anchored_pattern) VALUES
    ('crawl_page',NULL,NULL,NULL),
    ('crawl_robots',NULL,NULL,NULL),
    ('browser_read',NULL,NULL,NULL),
    ('github_rest','GET',NULL,NULL),
    ('github_rest','POST',NULL,'^https://api[.]github[.]com/app/installations/[1-9][0-9]*/access_tokens$'),
    ('google_oauth_token',NULL,NULL,NULL),
    ('google_oauth_revoke',NULL,NULL,NULL),
    ('gsc_api',NULL,NULL,NULL),
    ('bing_oauth_token',NULL,NULL,NULL),
    ('bing_api',NULL,NULL,NULL),
    ('jev',NULL,NULL,NULL),
    ('openai_model',NULL,NULL,NULL),
    ('model_json',NULL,NULL,NULL),
    ('slack_bot','POST','https://slack.com/api/chat.postMessage',NULL),
    ('slack_bot','POST','https://slack.com/api/auth.revoke',NULL),
    ('slack_oauth','POST','https://slack.com/api/oauth.v2.access',NULL),
    ('indexnow_submit','POST','https://api.indexnow.org/indexnow',NULL),
    ('crawl_key_file','GET',NULL,'^https://[^/?#]+/[A-Za-z0-9-]{8,128}\.txt$'),
    ('ga4_admin','GET',NULL,'^https://analyticsadmin[.]googleapis[.]com/v1beta/(accountSummaries|properties/[1-9][0-9]{0,19}/dataStreams)[?]pageSize=50(&pageToken=([A-Za-z0-9_=.-]|%%3D)+)?$'),
    ('ga4_data','POST',NULL,'^https://analyticsdata[.]googleapis[.]com/v1beta/properties/[1-9][0-9]{0,19}:runReport$'),
    ('telegram_bot','POST','https://api.telegram.org/getMe',NULL),
    ('telegram_bot','POST','https://api.telegram.org/setWebhook',NULL),
    ('telegram_bot','POST','https://api.telegram.org/deleteWebhook',NULL),
    ('telegram_bot','POST','https://api.telegram.org/sendMessage',NULL),
    ('telegram_bot','POST','https://api.telegram.org/answerCallbackQuery',NULL),
    ('wordpress_rest','GET',NULL,NULL),
    ('wordpress_rest','POST',NULL,NULL),
    ('webflow_oauth','POST','https://api.webflow.com/oauth/access_token',NULL),
    ('webflow_revoke','POST','https://webflow.com/oauth/revoke_authorization',NULL),
    ('npm_registry','GET',NULL,'^https://registry[.]npmjs[.]org/(@[a-z0-9][a-z0-9._-]*/)?([a-z0-9][a-z0-9._-]*)/-/\2-[0-9]+[.][0-9]+[.][0-9]+(-[A-Za-z0-9.-]+)?([+][A-Za-z0-9.-]+)?[.]tgz$');

CREATE OR REPLACE FUNCTION control.strategy_learning_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v record; packet jsonb; measures jsonb; history jsonb; changes jsonb; binding uuid;
BEGIN
    packet:='{}'::jsonb;
    IF packet IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    SELECT binding_id INTO binding FROM control.current_gsc_binding(v.tenant_id,p_site);
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'id',md5('measurement:' || v.tenant_id::text || ':' || p_site::text || ':' ||
            o.operation_id::text || ':' || o.horizon::text || ':' || o.sequence_number::text)::uuid,
        'operation_id',o.operation_id,'horizon',o.horizon,
        'sequence_number',o.sequence_number,'recorded_at',o.recorded_at,
        'work_type',CASE WHEN op.authority_kind='owner_editorial' THEN editorial.manifest->>'work_type'
            WHEN r.recipe_key LIKE 'technical_%%' THEN 'metadata_pr'
            ELSE convert_from(r.canonical_body,'UTF8')::jsonb->>'standing_work_type' END,
        'recipe_key',CASE WHEN op.authority_kind='owner_editorial' THEN 'owner_editorial' ELSE r.recipe_key END,
        'baseline',p.baseline,'document',o.document,
        'baseline_window',jsonb_build_object('start',p.baseline_start,'end',p.baseline_end),
        'post_window',jsonb_build_object('start',p.post_start,'end',p.post_end)
        ) ORDER BY o.operation_id,o.horizon),'[]'::jsonb) INTO measures
    FROM (SELECT DISTINCT ON (x.operation_id,x.horizon) x.*
        FROM app.change_measurement_observations x
        WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site AND x.horizon IN (28,90)
        ORDER BY x.operation_id,x.horizon,x.sequence_number DESC) o
    JOIN app.change_measurement_plans p ON p.tenant_id=v.tenant_id AND p.site_id=p_site
        AND p.operation_id=o.operation_id AND p.horizon=o.horizon
    JOIN app.github_pr_operations op ON op.tenant_id=v.tenant_id AND op.site_id=p_site AND op.id=o.operation_id
    LEFT JOIN app.candidate_recipe_revisions c ON c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=op.technical_revision_id
    LEFT JOIN control.recipe_releases r ON r.id=c.recipe_release_id
    LEFT JOIN app.content_candidates editorial ON editorial.tenant_id=v.tenant_id
        AND editorial.site_id=p_site AND editorial.id=op.content_candidate_id
    WHERE r.id IS NOT NULL OR editorial.id IS NOT NULL;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'dimensions',x.dimensions,
        'rows',x.rows,'coverage',x.coverage,'window',jsonb_build_object('start',x.start_date,'end',x.end_date),
        'imported_at',x.imported_at) ORDER BY x.imported_at,x.id),'[]'::jsonb) INTO history
    FROM app.gsc_import_generations x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND x.binding_id=binding AND x.search_type='web' AND x.dimensions @> '["page","date"]'::jsonb
        AND jsonb_array_length(x.dimensions)=2
        AND x.end_date >= (transaction_timestamp() AT TIME ZONE 'America/Los_Angeles')::date - interval '14 months';
    SELECT coalesce(jsonb_agg(jsonb_build_object('operation_id',p.operation_id,
        'page_url',p.page_url,'verified_live_at',p.verified_live_at,'post_end',p.post_end)
        ORDER BY p.operation_id),'[]'::jsonb) INTO changes FROM app.change_measurement_plans p
    WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.horizon=90;
    RETURN packet || jsonb_build_object('learning',jsonb_build_object(
        'as_of',(transaction_timestamp() AT TIME ZONE 'America/Los_Angeles')::date,
        'measurements',measures,'generations',history,'changes',changes));
END; $function$
;
REVOKE ALL ON FUNCTION control.strategy_learning_sources(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.weekly_strategy_learning_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v record; packet jsonb; measures jsonb; history jsonb; changes jsonb; binding uuid;
BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['strategy_rebuild']);
    packet:='{}'::jsonb;
    IF packet IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO v FROM control.weekly_skill_context(p_hash,p_generation,p_site);
    IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
    SELECT binding_id INTO binding FROM control.current_gsc_binding(v.tenant_id,p_site);
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'id',md5('measurement:' || v.tenant_id::text || ':' || p_site::text || ':' ||
            o.operation_id::text || ':' || o.horizon::text || ':' || o.sequence_number::text)::uuid,
        'operation_id',o.operation_id,'horizon',o.horizon,
        'sequence_number',o.sequence_number,'recorded_at',o.recorded_at,
        'work_type',CASE WHEN op.authority_kind='owner_editorial' THEN editorial.manifest->>'work_type'
            WHEN r.recipe_key LIKE 'technical_%%' THEN 'metadata_pr'
            ELSE convert_from(r.canonical_body,'UTF8')::jsonb->>'standing_work_type' END,
        'recipe_key',CASE WHEN op.authority_kind='owner_editorial' THEN 'owner_editorial' ELSE r.recipe_key END,
        'baseline',p.baseline,'document',o.document,
        'baseline_window',jsonb_build_object('start',p.baseline_start,'end',p.baseline_end),
        'post_window',jsonb_build_object('start',p.post_start,'end',p.post_end)
        ) ORDER BY o.operation_id,o.horizon),'[]'::jsonb) INTO measures
    FROM (SELECT DISTINCT ON (x.operation_id,x.horizon) x.*
        FROM app.change_measurement_observations x
        WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site AND x.horizon IN (28,90)
        ORDER BY x.operation_id,x.horizon,x.sequence_number DESC) o
    JOIN app.change_measurement_plans p ON p.tenant_id=v.tenant_id AND p.site_id=p_site
        AND p.operation_id=o.operation_id AND p.horizon=o.horizon
    JOIN app.github_pr_operations op ON op.tenant_id=v.tenant_id AND op.site_id=p_site AND op.id=o.operation_id
    LEFT JOIN app.candidate_recipe_revisions c ON c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=op.technical_revision_id
    LEFT JOIN control.recipe_releases r ON r.id=c.recipe_release_id
    LEFT JOIN app.content_candidates editorial ON editorial.tenant_id=v.tenant_id
        AND editorial.site_id=p_site AND editorial.id=op.content_candidate_id
    WHERE r.id IS NOT NULL OR editorial.id IS NOT NULL;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'dimensions',x.dimensions,
        'rows',x.rows,'coverage',x.coverage,'window',jsonb_build_object('start',x.start_date,'end',x.end_date),
        'imported_at',x.imported_at) ORDER BY x.imported_at,x.id),'[]'::jsonb) INTO history
    FROM app.gsc_import_generations x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND x.binding_id=binding AND x.search_type='web' AND x.dimensions @> '["page","date"]'::jsonb
        AND jsonb_array_length(x.dimensions)=2
        AND x.end_date >= (transaction_timestamp() AT TIME ZONE 'America/Los_Angeles')::date - interval '14 months';
    SELECT coalesce(jsonb_agg(jsonb_build_object('operation_id',p.operation_id,
        'page_url',p.page_url,'verified_live_at',p.verified_live_at,'post_end',p.post_end)
        ORDER BY p.operation_id),'[]'::jsonb) INTO changes FROM app.change_measurement_plans p
    WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.horizon=90;
    RETURN packet || jsonb_build_object('learning',jsonb_build_object(
        'as_of',(transaction_timestamp() AT TIME ZONE 'America/Los_Angeles')::date,
        'measurements',measures,'generations',history,'changes',changes));
END; $function$
;
REVOKE ALL ON FUNCTION control.weekly_strategy_learning_sources(bytea,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.strategy_link_sources(p_hash bytea,p_generation text,p_site uuid) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('internal_links',control.internal_link_sources(p_hash,p_generation,p_site));
$$;
REVOKE ALL ON FUNCTION control.strategy_link_sources(bytea,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.weekly_strategy_link_sources(p_hash bytea,p_generation text,p_site uuid) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('internal_links',control.weekly_skill_link_graph(p_hash,p_generation,p_site));
$$;
REVOKE ALL ON FUNCTION control.weekly_strategy_link_sources(bytea,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.strategy_topic_sources(p_hash bytea,p_generation text,p_site uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 RETURN control.keyword_topic_sources(v.tenant_id,p_site)||jsonb_build_object('bing_pages',control.strategy_bing_pages(v.tenant_id,p_site));
END $$;
REVOKE ALL ON FUNCTION control.strategy_topic_sources(bytea,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.weekly_strategy_topic_sources(p_hash bytea,p_generation text,p_site uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.weekly_skill_context(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 RETURN control.keyword_topic_sources(v.tenant_id,p_site)||jsonb_build_object('bing_pages',control.strategy_bing_pages(v.tenant_id,p_site));
END $$;
REVOKE ALL ON FUNCTION control.weekly_strategy_topic_sources(bytea,text,uuid) FROM PUBLIC;

INSERT INTO control.strategy_source_dispatch VALUES
 ('base',10,'control.seo_strategy_base_sources(bytea,text,uuid)','control.weekly_skill_seo_strategy_base_sources(bytea,text,uuid)'),
 ('learning',20,'control.strategy_learning_sources(bytea,text,uuid)','control.weekly_strategy_learning_sources(bytea,text,uuid)'),
 ('internal_links',30,'control.strategy_link_sources(bytea,text,uuid)','control.weekly_strategy_link_sources(bytea,text,uuid)'),
 ('topics',40,'control.strategy_topic_sources(bytea,text,uuid)','control.weekly_strategy_topic_sources(bytea,text,uuid)');

DROP FUNCTION control.bind_before_ga4_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_before_indexnow_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_before_npm_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_before_slack_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_before_telegram_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_before_webflow_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_before_wordpress_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_pre_assistant_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.bind_pre_repository_write_egress_profile(uuid,uuid,uuid,bytea,text);
DROP FUNCTION control.owner_connector_request_allowed_0144(text,text,text);
DROP FUNCTION control.owner_request_before_pagespeed(text,text,text);
DROP FUNCTION control.owner_request_before_publishing(text,text,text);
DROP FUNCTION control.seo_strategy_sources_0137(bytea,text,uuid);
DROP FUNCTION control.seo_strategy_sources_before_internal_links(bytea,text,uuid);
DROP FUNCTION control.weekly_skill_seo_strategy_sources_0137(bytea,text,uuid);
DROP FUNCTION control.weekly_skill_sources_before_internal_links(bytea,text,uuid);
