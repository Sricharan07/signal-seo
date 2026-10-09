-- Credential admission remains distinct; the permission decision is shared.
CREATE TYPE control.port_authority_context AS (
    outcome text, tenant_id uuid, user_id uuid, role_key text,
    authentication_level text, membership_epoch bigint, site_authorization_epoch bigint,
    membership_id uuid, origin text, grant_id uuid, recipe_release_id uuid
);
ALTER TYPE control.port_authority_context OWNER TO signal_migrator;
REVOKE ALL ON TYPE control.port_authority_context FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE FUNCTION control.port_permission(p_outcome text,p_role text,p_required_role text,p_fail_closed boolean)
RETURNS boolean LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT CASE WHEN p_fail_closed THEN p_outcome IS NOT DISTINCT FROM 'authorized'
        AND (p_required_role IS NULL OR p_role IS NOT DISTINCT FROM p_required_role)
        ELSE p_outcome='authorized' AND (p_required_role IS NULL OR p_role=p_required_role) END;
$$;
ALTER FUNCTION control.port_permission(text,text,text,boolean) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_permission(text,text,text,boolean) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE FUNCTION control.port_required_role(p_actor text) RETURNS text
LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT CASE WHEN p_actor IN ('weekly_delivery','internal_link_skill','weekly_strategy') THEN 'workload' ELSE 'owner' END;
$$;
ALTER FUNCTION control.port_required_role(text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_required_role(text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE FUNCTION control.admit_port_context(p_actor text,p_context text,p_hash bytea,p_generation text,p_site uuid,p_mode text)
RETURNS SETOF control.port_authority_context
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF p_actor='owner' AND p_context='authority' THEN
        RETURN QUERY SELECT a.*,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid
            FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation) a;
    ELSIF p_actor='weekly_delivery' AND p_context='authority' THEN
        RETURN QUERY SELECT a.*,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid
            FROM control.resolve_weekly_delivery_authority(p_hash,p_site,p_generation,p_mode) a;
    ELSIF p_actor='internal_link_skill' AND p_context='authority' THEN
        RETURN QUERY SELECT a.*,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid
            FROM control.resolve_internal_link_skill_authority(p_hash,p_site,p_generation) a;
    ELSIF p_actor='weekly_skill' AND p_context IN ('brain','brand','ga4') THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,NULL::text,
            NULL::bigint,NULL::bigint,a.membership_id,NULL::text,NULL::uuid,NULL::uuid
            FROM control.weekly_skill_context(p_hash,p_generation,p_site) a;
    ELSIF p_actor='weekly_skill' AND p_context='pagespeed' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,NULL::text,
            NULL::bigint,NULL::bigint,NULL::uuid,a.origin,NULL::uuid,NULL::uuid
            FROM control.weekly_skill_pagespeed_context(p_hash,p_generation,p_site) a;
    ELSIF p_actor='assistant' AND p_context='brain' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,a.role_key,NULL::text,
            NULL::bigint,NULL::bigint,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid
            FROM control.assistant_scope(p_hash,p_generation,p_site) a;
    ELSIF p_actor='owner' AND p_context='brain' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,NULL::text,
            NULL::bigint,NULL::bigint,a.membership_id,NULL::text,NULL::uuid,NULL::uuid
            FROM control.business_brain_owner(p_hash,p_generation,p_site) a;
    ELSIF p_actor='owner' AND p_context='brand' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,NULL::text,
            NULL::bigint,NULL::bigint,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid
            FROM control.brand_document_owner(p_hash,p_generation,p_site) a;
    ELSIF p_actor='owner' AND p_context='ga4' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,NULL::text,
            NULL::bigint,NULL::bigint,NULL::uuid,a.origin,NULL::uuid,NULL::uuid
            FROM control.ga4_owner_context(p_hash,p_generation,p_site) a;
    ELSIF p_actor='owner' AND p_context='pagespeed' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,NULL::text,
            NULL::bigint,NULL::bigint,NULL::uuid,a.origin,NULL::uuid,NULL::uuid
            FROM control.gsc_owner_context(p_hash,p_generation,p_site) a;
    ELSIF p_actor='weekly_strategy' AND p_context='authority' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,'workload'::text,
            NULL::text,NULL::bigint,NULL::bigint,a.membership_id,NULL::text,NULL::uuid,NULL::uuid
            FROM control.weekly_skill_context(p_hash,p_generation,p_site) a;
    ELSIF p_actor='strategy_provider' AND p_context='provider' THEN
        RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,
            NULL::text,NULL::bigint,NULL::bigint,NULL::uuid,a.origin,a.grant_id,a.recipe_release_id
            FROM control.strategy_provider_context(p_hash,p_generation,p_site) a;
    ELSE
        RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
    END IF;
END $$;
ALTER FUNCTION control.admit_port_context(text,text,bytea,text,uuid,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.admit_port_context(text,text,bytea,text,uuid,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;
-- model_budget_dispatch: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_model_budget_dispatch(p_actor text, p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','assistant','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v record; c app.model_budget_calls%%ROWTYPE; account jsonb; BEGIN
 SELECT * INTO v FROM control.admit_port_context('owner','brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO c FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
 IF NOT FOUND OR c.generation IS DISTINCT FROM p_generation OR c.request_sha256 IS DISTINCT FROM p_digest
 OR c.month<>date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date
 OR EXISTS(SELECT 1 FROM app.model_budget_dispatches WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id)
 THEN RETURN 'unavailable'; END IF;
 account:=control.model_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_dispatches VALUES(v.tenant_id,p_site,p_id,transaction_timestamp());
 RETURN 'admitted';
END;
ELSIF p_actor='assistant' THEN
DECLARE v record; c app.model_budget_calls%%ROWTYPE; account jsonb; BEGIN
 SELECT * INTO v FROM control.admit_port_context('assistant','brain',p_hash,p_generation,p_site,NULL);
 IF NOT EXISTS(SELECT 1 FROM app.assistant_messages WHERE id=p_id AND role='signal' AND NOT finalized) THEN RETURN 'denied'; END IF; IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO c FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id AND role='owner_answers';
 IF NOT FOUND OR c.generation IS DISTINCT FROM p_generation OR c.request_sha256 IS DISTINCT FROM p_digest
 OR c.month<>date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date
 OR EXISTS(SELECT 1 FROM app.model_budget_dispatches WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id)
 THEN RETURN 'unavailable'; END IF;
 account:=control.assistant_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_dispatches VALUES(v.tenant_id,p_site,p_id,transaction_timestamp());
 RETURN 'admitted';
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v record; c app.model_budget_calls%%ROWTYPE; account jsonb; BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{brain_refresh}'::text[]);PERFORM control.assert_weekly_skill_model_call(p_hash,p_generation,p_site,p_id);
 SELECT * INTO v FROM control.admit_port_context('weekly_skill','brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO c FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
 IF NOT FOUND OR c.generation IS DISTINCT FROM p_generation OR c.request_sha256 IS DISTINCT FROM p_digest
 OR c.month<>date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date
 OR EXISTS(SELECT 1 FROM app.model_budget_dispatches WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id)
 THEN RETURN 'unavailable'; END IF;
 account:=control.weekly_skill_model_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_dispatches VALUES(v.tenant_id,p_site,p_id,transaction_timestamp());
 RETURN 'admitted';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_model_budget_dispatch(text,bytea,text,uuid,uuid,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_model_budget_dispatch(text,bytea,text,uuid,uuid,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.model_budget_dispatch(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_dispatch('owner',p_hash,p_generation,p_site,p_id,p_digest);
END;
$function$;

REVOKE ALL ON FUNCTION control.model_budget_dispatch(bytea,text,uuid,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.model_budget_dispatch(bytea,text,uuid,uuid,bytea) TO signal_api;

GRANT EXECUTE ON FUNCTION control.model_budget_dispatch(bytea,text,uuid,uuid,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.assistant_budget_dispatch(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_dispatch('assistant',p_hash,p_generation,p_site,p_id,p_digest);
END;
$function$;

REVOKE ALL ON FUNCTION control.assistant_budget_dispatch(bytea,text,uuid,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.assistant_budget_dispatch(bytea,text,uuid,uuid,bytea) TO signal_api;

GRANT EXECUTE ON FUNCTION control.assistant_budget_dispatch(bytea,text,uuid,uuid,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_model_budget_dispatch(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_dispatch('weekly_skill',p_hash,p_generation,p_site,p_id,p_digest);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_model_budget_dispatch(bytea,text,uuid,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_model_budget_dispatch(bytea,text,uuid,uuid,bytea) TO signal_workflow;
-- End port group 0.
-- model_budget_finish: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_model_budget_finish(p_actor text, p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_cost bigint, p_digest bytea, p_receipt jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','assistant','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v record; prior app.model_budget_receipts%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.admit_port_context('owner','brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 IF NOT EXISTS(SELECT 1 FROM app.model_budget_calls c JOIN app.model_budget_dispatches d
 ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.call_id=c.id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_id AND c.generation=p_generation)
 THEN RETURN 'unavailable'; END IF;
 SELECT * INTO prior FROM app.model_budget_receipts WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id;
 IF FOUND THEN RETURN CASE WHEN prior.cost_micros=p_cost AND prior.response_sha256=p_digest
 AND prior.receipt=p_receipt THEN 'replayed' ELSE 'conflict' END; END IF;
 INSERT INTO app.model_budget_receipts VALUES(v.tenant_id,p_site,p_id,p_cost,p_digest,p_receipt,transaction_timestamp());
 RETURN 'completed';
END;
ELSIF p_actor='assistant' THEN
DECLARE v record; prior app.model_budget_receipts%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.admit_port_context('assistant','brain',p_hash,p_generation,p_site,NULL);
 IF NOT EXISTS(SELECT 1 FROM app.assistant_messages WHERE id=p_id AND role='signal' AND NOT finalized) THEN RETURN 'denied'; END IF; IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 IF NOT EXISTS(SELECT 1 FROM app.model_budget_calls c JOIN app.model_budget_dispatches d
 ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.call_id=c.id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_id AND c.role='owner_answers' AND c.generation=p_generation)
 THEN RETURN 'unavailable'; END IF;
 SELECT * INTO prior FROM app.model_budget_receipts WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id;
 IF FOUND THEN RETURN CASE WHEN prior.cost_micros=p_cost AND prior.response_sha256=p_digest
 AND prior.receipt=p_receipt THEN 'replayed' ELSE 'conflict' END; END IF;
 INSERT INTO app.model_budget_receipts VALUES(v.tenant_id,p_site,p_id,p_cost,p_digest,p_receipt,transaction_timestamp());
 RETURN 'completed';
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v record; prior app.model_budget_receipts%%ROWTYPE; BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{brain_refresh}'::text[]);PERFORM control.assert_weekly_skill_model_call(p_hash,p_generation,p_site,p_id);
 SELECT * INTO v FROM control.admit_port_context('weekly_skill','brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 IF NOT EXISTS(SELECT 1 FROM app.model_budget_calls c JOIN app.model_budget_dispatches d
 ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.call_id=c.id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_id AND c.generation=p_generation)
 THEN RETURN 'unavailable'; END IF;
 SELECT * INTO prior FROM app.model_budget_receipts WHERE tenant_id=v.tenant_id AND site_id=p_site AND call_id=p_id;
 IF FOUND THEN RETURN CASE WHEN prior.cost_micros=p_cost AND prior.response_sha256=p_digest
 AND prior.receipt=p_receipt THEN 'replayed' ELSE 'conflict' END; END IF;
 INSERT INTO app.model_budget_receipts VALUES(v.tenant_id,p_site,p_id,p_cost,p_digest,p_receipt,transaction_timestamp());
 RETURN 'completed';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_model_budget_finish(text,bytea,text,uuid,uuid,bigint,bytea,jsonb) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_model_budget_finish(text,bytea,text,uuid,uuid,bigint,bytea,jsonb) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.model_budget_finish(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_cost bigint, p_digest bytea, p_receipt jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_finish('owner',p_hash,p_generation,p_site,p_id,p_cost,p_digest,p_receipt);
END;
$function$;

REVOKE ALL ON FUNCTION control.model_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.model_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) TO signal_api;

GRANT EXECUTE ON FUNCTION control.model_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) TO signal_identity;

CREATE OR REPLACE FUNCTION control.assistant_budget_finish(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_cost bigint, p_digest bytea, p_receipt jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_finish('assistant',p_hash,p_generation,p_site,p_id,p_cost,p_digest,p_receipt);
END;
$function$;

REVOKE ALL ON FUNCTION control.assistant_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.assistant_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) TO signal_api;

GRANT EXECUTE ON FUNCTION control.assistant_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_model_budget_finish(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_cost bigint, p_digest bytea, p_receipt jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_finish('weekly_skill',p_hash,p_generation,p_site,p_id,p_cost,p_digest,p_receipt);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_model_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_model_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) TO signal_workflow;
-- End port group 1.
-- model_budget_read: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_model_budget_read(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; cap bigint; used bigint; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','assistant','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{brain_refresh,strategy_rebuild,brief_proposals}'::text[]);

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
 SELECT c.cap_micros INTO cap FROM app.model_budget_caps c WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site
 ORDER BY c.recorded_at DESC,c.id DESC LIMIT 1; cap:=coalesce(cap,25000000);
 SELECT coalesce(sum(coalesce(r.cost_micros,c.reserved_micros)),0) INTO used
 FROM app.model_budget_calls c LEFT JOIN app.model_budget_receipts r
 ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.call_id=c.id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.month=date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date;
 RETURN jsonb_build_object('tenant_id',v.tenant_id,'site_id',p_site,'cap_micros',cap,'used_micros',used,'warning',used*5>=cap*4,
 'state',CASE WHEN used>=cap THEN 'unavailable' ELSE 'available' END,
 'month',date_trunc('month',transaction_timestamp() AT TIME ZONE 'UTC')::date);
END;
$function$;

ALTER FUNCTION control.port_model_budget_read(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_model_budget_read(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.model_budget_read(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_read('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.model_budget_read(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.model_budget_read(bytea,text,uuid) TO signal_api;

GRANT EXECUTE ON FUNCTION control.model_budget_read(bytea,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.assistant_budget_read(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_read('assistant',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.assistant_budget_read(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.assistant_budget_read(bytea,text,uuid) TO signal_api;

GRANT EXECUTE ON FUNCTION control.assistant_budget_read(bytea,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_model_budget_read(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_read('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_model_budget_read(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_model_budget_read(bytea,text,uuid) TO signal_workflow;
-- End port group 2.
-- model_budget_reserve: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_model_budget_reserve(p_actor text, p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea, p_role text, p_release jsonb, p_amount bigint)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','assistant','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v record; account jsonb; prior app.model_budget_calls%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.admit_port_context('owner','brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 IF p_id IS NULL OR p_digest IS NULL OR octet_length(p_digest)<>32 OR p_amount IS NULL
 OR p_amount NOT BETWEEN 1 AND 1000000000 OR p_role IS NULL OR p_release IS NULL THEN RETURN 'invalid'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO prior FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
 IF FOUND THEN
  IF prior.request_sha256 IS DISTINCT FROM p_digest OR prior.role IS DISTINCT FROM p_role
  OR prior.model_release IS DISTINCT FROM p_release OR prior.reserved_micros IS DISTINCT FROM p_amount
  OR prior.generation IS DISTINCT FROM p_generation THEN RETURN 'conflict'; END IF;
  RETURN 'replayed';
 END IF;
 account:=control.model_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint+p_amount>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_calls(tenant_id,site_id,id,generation,request_sha256,role,model_release,reserved_micros)
 VALUES(v.tenant_id,p_site,p_id,p_generation,p_digest,p_role,p_release,p_amount);
 RETURN 'reserved';
END;
ELSIF p_actor='assistant' THEN
DECLARE v record; account jsonb; prior app.model_budget_calls%%ROWTYPE; BEGIN
 SELECT * INTO v FROM control.admit_port_context('assistant','brain',p_hash,p_generation,p_site,NULL);
 IF NOT EXISTS(SELECT 1 FROM app.assistant_messages WHERE id=p_id AND role='signal' AND NOT finalized) THEN RETURN 'denied'; END IF; IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 IF p_role IS DISTINCT FROM 'owner_answers' OR p_id IS NULL OR p_digest IS NULL OR octet_length(p_digest)<>32 OR p_amount IS NULL
 OR p_amount NOT BETWEEN 1 AND 1000000000 OR p_role IS NULL OR p_release IS NULL THEN RETURN 'invalid'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO prior FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
 IF FOUND THEN
  IF prior.request_sha256 IS DISTINCT FROM p_digest OR prior.role IS DISTINCT FROM p_role
  OR prior.model_release IS DISTINCT FROM p_release OR prior.reserved_micros IS DISTINCT FROM p_amount
  OR prior.generation IS DISTINCT FROM p_generation THEN RETURN 'conflict'; END IF;
  RETURN 'replayed';
 END IF;
 account:=control.assistant_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint+p_amount>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_calls(tenant_id,site_id,id,generation,request_sha256,role,model_release,reserved_micros)
 VALUES(v.tenant_id,p_site,p_id,p_generation,p_digest,p_role,p_release,p_amount);
 RETURN 'reserved';
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v record; account jsonb; prior app.model_budget_calls%%ROWTYPE; BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{brain_refresh}'::text[]);PERFORM control.assert_weekly_skill_model_reservation(p_hash,p_generation,p_site,p_id,p_role,p_amount);
 SELECT * INTO v FROM control.admit_port_context('weekly_skill','brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 IF p_id IS NULL OR p_digest IS NULL OR octet_length(p_digest)<>32 OR p_amount IS NULL
 OR p_amount NOT BETWEEN 1 AND 1000000000 OR p_role IS NULL OR p_release IS NULL THEN RETURN 'invalid'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR NO KEY UPDATE;
 SELECT * INTO prior FROM app.model_budget_calls WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id;
 IF FOUND THEN
  IF prior.request_sha256 IS DISTINCT FROM p_digest OR prior.role IS DISTINCT FROM p_role
  OR prior.model_release IS DISTINCT FROM p_release OR prior.reserved_micros IS DISTINCT FROM p_amount
  OR prior.generation IS DISTINCT FROM p_generation THEN RETURN 'conflict'; END IF;
  RETURN 'replayed';
 END IF;
 account:=control.weekly_skill_model_budget_read(p_hash,p_generation,p_site);
 IF (account->>'used_micros')::bigint+p_amount>(account->>'cap_micros')::bigint THEN RETURN 'exhausted'; END IF;
 INSERT INTO app.model_budget_calls(tenant_id,site_id,id,generation,request_sha256,role,model_release,reserved_micros,weekly_skill_handle_hash)
 VALUES(v.tenant_id,p_site,p_id,p_generation,p_digest,p_role,p_release,p_amount,p_hash);
 RETURN 'reserved';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_model_budget_reserve(text,bytea,text,uuid,uuid,bytea,text,jsonb,bigint) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_model_budget_reserve(text,bytea,text,uuid,uuid,bytea,text,jsonb,bigint) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.model_budget_reserve(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea, p_role text, p_release jsonb, p_amount bigint)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_reserve('owner',p_hash,p_generation,p_site,p_id,p_digest,p_role,p_release,p_amount);
END;
$function$;

REVOKE ALL ON FUNCTION control.model_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.model_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) TO signal_api;

GRANT EXECUTE ON FUNCTION control.model_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) TO signal_identity;

CREATE OR REPLACE FUNCTION control.assistant_budget_reserve(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea, p_role text, p_release jsonb, p_amount bigint)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_reserve('assistant',p_hash,p_generation,p_site,p_id,p_digest,p_role,p_release,p_amount);
END;
$function$;

REVOKE ALL ON FUNCTION control.assistant_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.assistant_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) TO signal_api;

GRANT EXECUTE ON FUNCTION control.assistant_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_model_budget_reserve(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_digest bytea, p_role text, p_release jsonb, p_amount bigint)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_model_budget_reserve('weekly_skill',p_hash,p_generation,p_site,p_id,p_digest,p_role,p_release,p_amount);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_model_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_model_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint) TO signal_workflow;
-- End port group 3.
-- dispatch_before_dependencies: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_dispatch_before_dependencies(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='internal_link_skill' THEN
PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'build_id',p_build_id);

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,control.port_required_role(p_actor),false) THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_intent FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
        AND intent.id = p_build_id FOR UPDATE;
    IF NOT FOUND OR v_intent.requested_by_user_id <> v_authority.user_id
       OR v_intent.membership_epoch <> v_authority.membership_epoch
       OR v_intent.site_epoch <> v_authority.site_authorization_epoch
       OR v_intent.recovery_generation <> p_generation THEN
        RETURN 'build_not_authorized';
    END IF;
    IF v_intent.status <> 'prepared' THEN RETURN 'dispatch_unknown'; END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id)
       OR NOT EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
           JOIN app.github_read_bindings AS binding
             ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
            AND binding.id = extension.binding_id AND binding.status = 'active'
          WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
            AND extension.id = v_intent.extension_id AND extension.status = 'observed'
            AND extension.base_sha = v_intent.base_sha AND extension.tree_sha = v_intent.tree_sha) THEN
        RETURN 'extension_inactive';
    END IF;
    UPDATE app.candidate_build_intents SET status = 'dispatched',
        dispatched_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_build_id;
    RETURN 'dispatched';
END;
$function$;

ALTER FUNCTION control.port_dispatch_before_dependencies(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_dispatch_before_dependencies(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.dispatch_before_dependencies(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_dispatch_before_dependencies('owner',p_session_hash,p_site_id,p_generation,p_build_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.dispatch_before_dependencies(bytea,uuid,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.internal_link_skill_dispatch_before_dependencies(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_dispatch_before_dependencies('internal_link_skill',p_session_hash,p_site_id,p_generation,p_build_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_dispatch_before_dependencies(bytea,uuid,text,uuid) FROM PUBLIC;
-- End port group 4.
-- dispatch_candidate_build: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_dispatch_candidate_build(p_actor text, p_session bytea, p_site uuid, p_generation text, p_build uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record;b app.candidate_build_intents%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.admit_port_context('owner','authority',p_session,p_generation,p_site,NULL);
    IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN a.outcome; END IF;
    IF NOT control.port_permission('authorized',a.role_key,'owner',true) THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_build FOR UPDATE;
    IF FOUND AND EXISTS(SELECT 1 FROM app.github_pr_extensions e WHERE e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id AND (e.framework='astro' OR EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id)))
       AND NOT EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id)
    THEN RETURN 'dependency_receipt_required'; END IF;
    RETURN control.dispatch_before_dependencies(p_session,p_site,p_generation,p_build);
END;
ELSIF p_actor='internal_link_skill' THEN
DECLARE a record;b app.candidate_build_intents%%ROWTYPE;
BEGIN PERFORM control.assert_internal_link_skill_resource(p_session,p_site,p_generation,'build_id',p_build);
    SELECT * INTO a FROM control.admit_port_context('internal_link_skill','authority',p_session,p_generation,p_site,NULL);
    IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN a.outcome; END IF;
    IF NOT control.port_permission('authorized',a.role_key,'workload',true) THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_build FOR UPDATE;
    IF FOUND AND EXISTS(SELECT 1 FROM app.github_pr_extensions e WHERE e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id AND (e.framework='astro' OR EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id)))
       AND NOT EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id)
    THEN RETURN 'dependency_receipt_required'; END IF;
    RETURN control.internal_link_skill_dispatch_before_dependencies(p_session,p_site,p_generation,p_build);
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session,p_site,p_generation,jsonb_build_object('build_id',p_build::text));
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_session,p_generation,p_site,'prepare');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,'workload',false) THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_intent FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site
        AND intent.id = p_build FOR UPDATE;
    IF NOT FOUND OR v_intent.requested_by_user_id <> v_authority.user_id
       OR v_intent.membership_epoch <> v_authority.membership_epoch
       OR v_intent.site_epoch <> v_authority.site_authorization_epoch
       OR v_intent.recovery_generation <> p_generation THEN
        RETURN 'build_not_authorized';
    END IF;
    IF v_intent.status <> 'prepared' THEN RETURN 'dispatch_unknown'; END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site)
       OR NOT EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
           JOIN app.github_read_bindings AS binding
             ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
            AND binding.id = extension.binding_id AND binding.status = 'active'
          WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site
            AND extension.id = v_intent.extension_id AND extension.status = 'observed'
            AND extension.base_sha = v_intent.base_sha AND extension.tree_sha = v_intent.tree_sha) THEN
        RETURN 'extension_inactive';
    END IF;
    UPDATE app.candidate_build_intents SET status = 'dispatched',
        dispatched_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site AND id = p_build;
    RETURN 'dispatched';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_dispatch_candidate_build(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_dispatch_candidate_build(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.dispatch_candidate_build(p_session bytea, p_site uuid, p_generation text, p_build uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_dispatch_candidate_build('owner',p_session,p_site,p_generation,p_build);
END;
$function$;

REVOKE ALL ON FUNCTION control.dispatch_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.dispatch_candidate_build(bytea,uuid,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_dispatch_candidate_build(p_session bytea, p_site uuid, p_generation text, p_build uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_dispatch_candidate_build('internal_link_skill',p_session,p_site,p_generation,p_build);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_dispatch_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_dispatch_candidate_build(bytea,uuid,text,uuid) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_dispatch_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_dispatch_candidate_build('weekly_delivery',p_session_hash,p_site_id,p_generation,p_build_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_dispatch_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_dispatch_candidate_build(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 5.
-- finish_before_dependencies: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_finish_before_dependencies(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid, p_exit_class text, p_exit_code integer, p_logs_sha256 text, p_log_bytes integer, p_artifacts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
        v_item jsonb; v_total bigint := 0;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='internal_link_skill' THEN
PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'build_id',p_build_id);

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,control.port_required_role(p_actor),false) THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_intent FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
        AND intent.id = p_build_id FOR UPDATE;
    IF NOT FOUND OR v_intent.requested_by_user_id <> v_authority.user_id
       OR v_intent.membership_epoch <> v_authority.membership_epoch
       OR v_intent.site_epoch <> v_authority.site_authorization_epoch
       OR v_intent.recovery_generation <> p_generation THEN
        RETURN 'build_not_authorized';
    END IF;
    IF v_intent.status = 'completed' THEN
        IF EXISTS (SELECT 1 FROM app.candidate_build_receipts AS receipt
          WHERE receipt.tenant_id = v_authority.tenant_id AND receipt.site_id = p_site_id
            AND receipt.build_id = p_build_id AND receipt.exit_class = p_exit_class
            AND receipt.exit_code IS NOT DISTINCT FROM p_exit_code
            AND receipt.logs_sha256 = p_logs_sha256 AND receipt.log_bytes = p_log_bytes
            AND receipt.artifacts = p_artifacts) THEN RETURN 'completed'; END IF;
        RETURN 'receipt_conflict';
    END IF;
    IF v_intent.status <> 'dispatched' THEN RETURN 'dispatch_unknown'; END IF;
    IF p_exit_class IS NULL OR p_exit_class NOT IN (
        'passed', 'crash', 'timeout', 'oom', 'output_limit', 'policy_rejected')
       OR p_logs_sha256 IS NULL OR p_logs_sha256 !~ '^[0-9a-f]{64}$'
       OR p_log_bytes IS NULL OR p_log_bytes NOT BETWEEN 0 AND 65536
       OR p_artifacts IS NULL OR jsonb_typeof(p_artifacts) <> 'array'
       OR jsonb_array_length(p_artifacts) > 1000
       OR octet_length(p_artifacts::text) > 131072
       OR p_exit_code IS NOT NULL AND p_exit_code NOT BETWEEN 0 AND 255
       OR (p_exit_class = 'passed' AND (p_exit_code IS DISTINCT FROM 0
            OR jsonb_array_length(p_artifacts) = 0))
       OR (p_exit_class <> 'passed' AND jsonb_array_length(p_artifacts) <> 0) THEN
        RETURN 'invalid_receipt';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_artifacts) LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 3
           OR NOT (v_item ?& ARRAY['path', 'sha256', 'size'])
           OR jsonb_typeof(v_item->'path') <> 'string'
           OR jsonb_typeof(v_item->'sha256') <> 'string'
           OR jsonb_typeof(v_item->'size') <> 'number'
           OR length(v_item->>'path') NOT BETWEEN 1 AND 1024
           OR left(v_item->>'path', length(v_intent.artifact_root) + 1)
                <> v_intent.artifact_root || '/'
           OR position('..' in v_item->>'path') > 0
           OR (v_item->>'sha256') !~ '^[0-9a-f]{64}$'
           OR (v_item->>'size') !~ '^[0-9]{1,8}$' THEN
            RETURN 'invalid_receipt';
        END IF;
        v_total := v_total + (v_item->>'size')::bigint;
        IF v_total > 16777216 THEN RETURN 'invalid_receipt'; END IF;
    END LOOP;
    INSERT INTO app.candidate_build_receipts (
        tenant_id, site_id, build_id, base_sha, patch_sha256, toolchain,
        build_command, exit_class, exit_code, logs_sha256, log_bytes, artifacts
    ) VALUES (v_authority.tenant_id, p_site_id, p_build_id, v_intent.base_sha,
        v_intent.patch_sha256, v_intent.toolchain, v_intent.build_command,
        p_exit_class, p_exit_code, p_logs_sha256, p_log_bytes, p_artifacts);
    UPDATE app.candidate_build_intents SET status = 'completed',
        completed_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_build_id;
    RETURN 'completed';
END;
$function$;

ALTER FUNCTION control.port_finish_before_dependencies(text,bytea,uuid,text,uuid,text,integer,text,integer,jsonb) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_finish_before_dependencies(text,bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.finish_before_dependencies(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid, p_exit_class text, p_exit_code integer, p_logs_sha256 text, p_log_bytes integer, p_artifacts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_before_dependencies('owner',p_session_hash,p_site_id,p_generation,p_build_id,p_exit_class,p_exit_code,p_logs_sha256,p_log_bytes,p_artifacts);
END;
$function$;

REVOKE ALL ON FUNCTION control.finish_before_dependencies(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.internal_link_skill_finish_before_dependencies(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid, p_exit_class text, p_exit_code integer, p_logs_sha256 text, p_log_bytes integer, p_artifacts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_before_dependencies('internal_link_skill',p_session_hash,p_site_id,p_generation,p_build_id,p_exit_class,p_exit_code,p_logs_sha256,p_log_bytes,p_artifacts);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_finish_before_dependencies(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC;
-- End port group 6.
-- finish_candidate_build: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_finish_candidate_build(p_actor text, p_session bytea, p_site uuid, p_generation text, p_build uuid, p_exit text, p_code integer, p_logs text, p_bytes integer, p_artifacts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.admit_port_context('owner','authority',p_session,p_generation,p_site,NULL);
    IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN a.outcome; END IF;
    IF NOT control.port_permission('authorized',a.role_key,'owner',true) THEN RETURN 'permission_denied'; END IF;
    IF EXISTS(SELECT 1 FROM app.candidate_build_intents b JOIN app.github_pr_extensions e ON e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.id=p_build AND (e.framework='astro' OR EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id)))
    THEN RETURN 'dependency_receipt_required'; END IF;
    RETURN control.finish_before_dependencies(p_session,p_site,p_generation,p_build,p_exit,p_code,p_logs,p_bytes,p_artifacts);
END;
ELSIF p_actor='internal_link_skill' THEN
DECLARE a record;
BEGIN PERFORM control.assert_internal_link_skill_resource(p_session,p_site,p_generation,'build_id',p_build);
    SELECT * INTO a FROM control.admit_port_context('internal_link_skill','authority',p_session,p_generation,p_site,NULL);
    IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN a.outcome; END IF;
    IF NOT control.port_permission('authorized',a.role_key,'workload',true) THEN RETURN 'permission_denied'; END IF;
    IF EXISTS(SELECT 1 FROM app.candidate_build_intents b JOIN app.github_pr_extensions e ON e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.id=p_build AND (e.framework='astro' OR EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id)))
    THEN RETURN 'dependency_receipt_required'; END IF;
    RETURN control.internal_link_skill_finish_before_dependencies(p_session,p_site,p_generation,p_build,p_exit,p_code,p_logs,p_bytes,p_artifacts);
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record; v_intent app.candidate_build_intents%%ROWTYPE;
        v_item jsonb; v_total bigint := 0;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session,p_site,p_generation,jsonb_build_object('build_id',p_build::text));
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_session,p_generation,p_site,'prepare');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,'workload',false) THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_intent FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site
        AND intent.id = p_build FOR UPDATE;
    IF NOT FOUND OR v_intent.requested_by_user_id <> v_authority.user_id
       OR v_intent.membership_epoch <> v_authority.membership_epoch
       OR v_intent.site_epoch <> v_authority.site_authorization_epoch
       OR v_intent.recovery_generation <> p_generation THEN
        RETURN 'build_not_authorized';
    END IF;
    IF v_intent.status = 'completed' THEN
        IF EXISTS (SELECT 1 FROM app.candidate_build_receipts AS receipt
          WHERE receipt.tenant_id = v_authority.tenant_id AND receipt.site_id = p_site
            AND receipt.build_id = p_build AND receipt.exit_class = p_exit
            AND receipt.exit_code IS NOT DISTINCT FROM p_code
            AND receipt.logs_sha256 = p_logs AND receipt.log_bytes = p_bytes
            AND receipt.artifacts = p_artifacts) THEN RETURN 'completed'; END IF;
        RETURN 'receipt_conflict';
    END IF;
    IF v_intent.status <> 'dispatched' THEN RETURN 'dispatch_unknown'; END IF;
    IF p_exit IS NULL OR p_exit NOT IN (
        'passed', 'crash', 'timeout', 'oom', 'output_limit', 'policy_rejected')
       OR p_logs IS NULL OR p_logs !~ '^[0-9a-f]{64}$'
       OR p_bytes IS NULL OR p_bytes NOT BETWEEN 0 AND 65536
       OR p_artifacts IS NULL OR jsonb_typeof(p_artifacts) <> 'array'
       OR jsonb_array_length(p_artifacts) > 1000
       OR octet_length(p_artifacts::text) > 131072
       OR p_code IS NOT NULL AND p_code NOT BETWEEN 0 AND 255
       OR (p_exit = 'passed' AND (p_code IS DISTINCT FROM 0
            OR jsonb_array_length(p_artifacts) = 0))
       OR (p_exit <> 'passed' AND jsonb_array_length(p_artifacts) <> 0) THEN
        RETURN 'invalid_receipt';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_artifacts) LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 3
           OR NOT (v_item ?& ARRAY['path', 'sha256', 'size'])
           OR jsonb_typeof(v_item->'path') <> 'string'
           OR jsonb_typeof(v_item->'sha256') <> 'string'
           OR jsonb_typeof(v_item->'size') <> 'number'
           OR length(v_item->>'path') NOT BETWEEN 1 AND 1024
           OR left(v_item->>'path', length(v_intent.artifact_root) + 1)
                <> v_intent.artifact_root || '/'
           OR position('..' in v_item->>'path') > 0
           OR (v_item->>'sha256') !~ '^[0-9a-f]{64}$'
           OR (v_item->>'size') !~ '^[0-9]{1,8}$' THEN
            RETURN 'invalid_receipt';
        END IF;
        v_total := v_total + (v_item->>'size')::bigint;
        IF v_total > 16777216 THEN RETURN 'invalid_receipt'; END IF;
    END LOOP;
    INSERT INTO app.candidate_build_receipts (
        tenant_id, site_id, build_id, base_sha, patch_sha256, toolchain,
        build_command, exit_class, exit_code, logs_sha256, log_bytes, artifacts
    ) VALUES (v_authority.tenant_id, p_site, p_build, v_intent.base_sha,
        v_intent.patch_sha256, v_intent.toolchain, v_intent.build_command,
        p_exit, p_code, p_logs, p_bytes, p_artifacts);
    UPDATE app.candidate_build_intents SET status = 'completed',
        completed_at = transaction_timestamp()
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site AND id = p_build;
    RETURN 'completed';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_finish_candidate_build(text,bytea,uuid,text,uuid,text,integer,text,integer,jsonb) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_finish_candidate_build(text,bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.finish_candidate_build(p_session bytea, p_site uuid, p_generation text, p_build uuid, p_exit text, p_code integer, p_logs text, p_bytes integer, p_artifacts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_candidate_build('owner',p_session,p_site,p_generation,p_build,p_exit,p_code,p_logs,p_bytes,p_artifacts);
END;
$function$;

REVOKE ALL ON FUNCTION control.finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_finish_candidate_build(p_session bytea, p_site uuid, p_generation text, p_build uuid, p_exit text, p_code integer, p_logs text, p_bytes integer, p_artifacts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_candidate_build('internal_link_skill',p_session,p_site,p_generation,p_build,p_exit,p_code,p_logs,p_bytes,p_artifacts);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_finish_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid, p_exit_class text, p_exit_code integer, p_logs_sha256 text, p_log_bytes integer, p_artifacts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_candidate_build('weekly_delivery',p_session_hash,p_site_id,p_generation,p_build_id,p_exit_class,p_exit_code,p_logs_sha256,p_log_bytes,p_artifacts);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) TO signal_workflow;
-- End port group 7.
-- internal_link_sources: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_internal_link_sources(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE a record; m record; origin text;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_strategy') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_strategy' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['strategy_rebuild']);
END IF;
 SELECT * INTO a FROM control.admit_port_context(p_actor,'authority',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(a.outcome,a.role_key,control.port_required_role(p_actor),true)
 OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN NULL; END IF;
 SELECT primary_origin INTO origin FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site;
 SELECT * INTO m FROM app.crawl_manifests WHERE tenant_id=a.tenant_id AND site_id=p_site
 ORDER BY completed_at DESC,id DESC LIMIT 1;
 IF m.id IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('manifest_id',m.id,'manifest_sha256',encode(m.manifest_sha256,'hex'),
 'site_origin',origin,'coverage',m.coverage,
 'pages',coalesce((SELECT jsonb_agg(jsonb_build_object('id',p.id,'url',u.fetch_url,'title',p.title,
 'headings',p.headings,'internal_links',p.internal_links,'body_sha256',encode(p.body_sha256,'hex'),
 'output_truncated',p.output_truncated) ORDER BY u.fetch_url,p.id)
 FROM app.crawl_page_records p JOIN app.urls u ON u.tenant_id=p.tenant_id AND u.site_id=p.site_id AND u.id=p.url_id
 WHERE p.tenant_id=a.tenant_id AND p.site_id=p_site AND p.crawl_run_id=m.crawl_run_id
 AND p.http_status BETWEEN 200 AND 299 AND NOT p.output_truncated),'[]'::jsonb),
 'used_anchors',coalesce((SELECT jsonb_agg(anchor ORDER BY anchor_key) FROM app.internal_link_candidates
 WHERE tenant_id=a.tenant_id AND site_id=p_site),'[]'::jsonb),
 'candidates',coalesce((SELECT jsonb_agg(jsonb_build_object('id',revision_id,
 'target_id',l.target_id,'source_id',l.source_id,'idempotency_key',r.idempotency_key,
 'extension_id',r.extension_id,'recipe_release_id',r.recipe_release_id,
 'build_id',r.build_id,'revision_sha256',encode(r.revision_sha256,'hex'),
 'page_cap',convert_from(r.canonical_manifest,'UTF8')::jsonb#>'{internal_link,page_cap}') ORDER BY revision_id)
 FROM app.internal_link_candidates l JOIN app.candidate_recipe_revisions r
 ON r.tenant_id=l.tenant_id AND r.site_id=l.site_id AND r.id=l.revision_id
 WHERE l.tenant_id=a.tenant_id AND l.site_id=p_site AND r.created_by_user_id=a.user_id),'[]'::jsonb));
END;
$function$;

ALTER FUNCTION control.port_internal_link_sources(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_internal_link_sources(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.internal_link_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_internal_link_sources('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_sources(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_sources(bytea,text,uuid) TO signal_api;

GRANT EXECUTE ON FUNCTION control.internal_link_sources(bytea,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_internal_link_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_internal_link_sources('internal_link_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_internal_link_sources(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_internal_link_sources(bytea,text,uuid) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_skill_link_graph(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_internal_link_sources('weekly_strategy',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_link_graph(bytea,text,uuid) FROM PUBLIC;
-- End port group 8.
-- prepare_candidate_build: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_prepare_candidate_build(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid, p_build_id uuid, p_request_id uuid, p_request_hash bytea, p_base_sha text, p_tree_sha text, p_patch_sha256 text, p_toolchain text, p_build_command text, p_artifact_root text)
 RETURNS TABLE(build_id uuid, build_status text, replayed boolean, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record; e app.github_pr_extensions%%ROWTYPE;
        existing app.candidate_build_intents%%ROWTYPE;
BEGIN
    IF p_extension_id IS NULL OR p_build_id IS NULL OR p_request_id IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
       OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
       OR p_patch_sha256 IS NULL OR p_patch_sha256 !~ '^[0-9a-f]{64}$'
       OR p_toolchain IS DISTINCT FROM
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
       OR p_build_command IS DISTINCT FROM 'npm run build'
       OR p_artifact_root IS NULL OR NOT (p_artifact_root IN ('.next','_site')
           OR control.valid_astro_output_directory(p_artifact_root)) THEN
        RAISE EXCEPTION 'invalid_candidate_build_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO a FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(a.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,a.outcome; RETURN;
    END IF;
    IF NOT control.port_permission('authorized',a.role_key,'owner',false) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'permission_denied'::text; RETURN;
    END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site_id FOR UPDATE;
    IF NOT control.current_github_site_proof(a.tenant_id,p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO existing FROM app.candidate_build_intents
      WHERE tenant_id=a.tenant_id AND requested_by_user_id=a.user_id AND idempotency_key=p_request_id;
    IF FOUND AND (existing.site_id<>p_site_id OR existing.extension_id<>p_extension_id
        OR existing.request_hash<>p_request_hash) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'request_conflict'::text; RETURN;
    END IF;
    SELECT ext.* INTO e FROM app.github_pr_extensions ext
      JOIN app.github_read_bindings b ON b.tenant_id=ext.tenant_id AND b.site_id=ext.site_id
        AND b.id=ext.binding_id AND b.status='active'
      WHERE ext.tenant_id=a.tenant_id AND ext.site_id=p_site_id AND ext.id=p_extension_id
        AND ext.status='observed' AND ext.coverage='complete' AND ext.content_sha IS NOT NULL
        AND ext.framework IN ('nextjs','astro','eleventy') AND ext.repository_id=b.repository_id
        AND ext.base_sha=p_base_sha AND ext.tree_sha=p_tree_sha FOR SHARE OF ext,b;
    IF NOT FOUND OR NOT (
        (e.framework='nextjs' AND p_artifact_root IN ('.next','out'))
        OR (e.framework='astro' AND control.valid_astro_output_directory(p_artifact_root))
        OR (e.framework='eleventy' AND p_artifact_root='_site')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'extension_inactive'::text; RETURN;
    END IF;
    IF existing.id IS NOT NULL THEN
        RETURN QUERY SELECT existing.id,existing.status,true,'prepared'::text; RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id
        AND site_id=p_site_id AND extension_id=p_extension_id AND status IN ('prepared','dispatched')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'build_exists'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_build_intents (
        tenant_id,site_id,id,extension_id,requested_by_user_id,idempotency_key,request_hash,
        membership_epoch,site_epoch,recovery_generation,base_sha,tree_sha,patch_sha256,
        toolchain,build_command,artifact_root,status)
    VALUES (a.tenant_id,p_site_id,p_build_id,p_extension_id,a.user_id,p_request_id,p_request_hash,
        a.membership_epoch,a.site_authorization_epoch,p_generation,p_base_sha,p_tree_sha,
        p_patch_sha256,p_toolchain,p_build_command,p_artifact_root,'prepared');
    RETURN QUERY SELECT p_build_id,'prepared'::text,false,'prepared'::text;
END;
ELSIF p_actor='internal_link_skill' THEN
DECLARE a record; e app.github_pr_extensions%%ROWTYPE;
        existing app.candidate_build_intents%%ROWTYPE;
BEGIN PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'extension_id',p_extension_id);PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'build_key',p_request_id);
    IF p_extension_id IS NULL OR p_build_id IS NULL OR p_request_id IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
       OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
       OR p_patch_sha256 IS NULL OR p_patch_sha256 !~ '^[0-9a-f]{64}$'
       OR p_toolchain IS DISTINCT FROM
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
       OR p_build_command IS DISTINCT FROM 'npm run build'
       OR p_artifact_root IS NULL OR NOT (p_artifact_root IN ('.next','_site')
           OR control.valid_astro_output_directory(p_artifact_root)) THEN
        RAISE EXCEPTION 'invalid_candidate_build_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO a FROM control.admit_port_context('internal_link_skill','authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(a.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,a.outcome; RETURN;
    END IF;
    IF NOT control.port_permission('authorized',a.role_key,'workload',false) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'permission_denied'::text; RETURN;
    END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site_id FOR UPDATE;
    IF NOT control.current_github_site_proof(a.tenant_id,p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO existing FROM app.candidate_build_intents
      WHERE tenant_id=a.tenant_id AND requested_by_user_id=a.user_id AND idempotency_key=p_request_id;
    IF FOUND AND (existing.site_id<>p_site_id OR existing.extension_id<>p_extension_id
        OR existing.request_hash<>p_request_hash) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'request_conflict'::text; RETURN;
    END IF;
    SELECT ext.* INTO e FROM app.github_pr_extensions ext
      JOIN app.github_read_bindings b ON b.tenant_id=ext.tenant_id AND b.site_id=ext.site_id
        AND b.id=ext.binding_id AND b.status='active'
      WHERE ext.tenant_id=a.tenant_id AND ext.site_id=p_site_id AND ext.id=p_extension_id
        AND ext.status='observed' AND ext.coverage='complete' AND ext.content_sha IS NOT NULL
        AND ext.framework IN ('nextjs','astro','eleventy') AND ext.repository_id=b.repository_id
        AND ext.base_sha=p_base_sha AND ext.tree_sha=p_tree_sha FOR SHARE OF ext,b;
    IF NOT FOUND OR NOT (
        (e.framework='nextjs' AND p_artifact_root IN ('.next','out'))
        OR (e.framework='astro' AND control.valid_astro_output_directory(p_artifact_root))
        OR (e.framework='eleventy' AND p_artifact_root='_site')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'extension_inactive'::text; RETURN;
    END IF;
    IF existing.id IS NOT NULL THEN
        RETURN QUERY SELECT existing.id,existing.status,true,'prepared'::text; RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id
        AND site_id=p_site_id AND extension_id=p_extension_id AND status IN ('prepared','dispatched')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,false,'build_exists'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_build_intents (
        tenant_id,site_id,id,extension_id,requested_by_user_id,idempotency_key,request_hash,
        membership_epoch,site_epoch,recovery_generation,base_sha,tree_sha,patch_sha256,
        toolchain,build_command,artifact_root,status)
    VALUES (a.tenant_id,p_site_id,p_build_id,p_extension_id,a.user_id,p_request_id,p_request_hash,
        a.membership_epoch,a.site_authorization_epoch,p_generation,p_base_sha,p_tree_sha,
        p_patch_sha256,p_toolchain,p_build_command,p_artifact_root,'prepared');
    RETURN QUERY SELECT p_build_id,'prepared'::text,false,'prepared'::text;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record; v_extension app.github_pr_extensions%%ROWTYPE;
        v_existing app.candidate_build_intents%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('extension_id',p_extension_id::text,'build_key',p_request_id::text));
    IF p_extension_id IS NULL OR p_build_id IS NULL OR p_request_id IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
       OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
       OR p_patch_sha256 IS NULL OR p_patch_sha256 !~ '^[0-9a-f]{64}$'
       OR p_toolchain IS DISTINCT FROM
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
       OR p_build_command IS DISTINCT FROM 'npm run build'
       OR p_artifact_root IS NULL OR p_artifact_root NOT IN ('.next', 'dist', '_site') THEN
        RAISE EXCEPTION 'invalid_candidate_build_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_session_hash,p_generation,p_site_id,'prepare');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, v_authority.outcome; RETURN;
    END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,'workload',false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'permission_denied'::text; RETURN;
    END IF;
    PERFORM 1 FROM app.sites AS site WHERE site.tenant_id = v_authority.tenant_id
        AND site.id = p_site_id FOR UPDATE;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO v_existing FROM app.candidate_build_intents AS intent
      WHERE intent.tenant_id = v_authority.tenant_id
        AND intent.requested_by_user_id = v_authority.user_id
        AND intent.idempotency_key = p_request_id;
    IF FOUND THEN
        IF v_existing.site_id <> p_site_id OR v_existing.extension_id <> p_extension_id
           OR v_existing.request_hash <> p_request_hash THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'request_conflict'::text; RETURN;
        END IF;
    END IF;
    SELECT extension.* INTO v_extension FROM app.github_pr_extensions AS extension
      JOIN app.github_read_bindings AS binding
        ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
       AND binding.id = extension.binding_id AND binding.status = 'active'
     WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
       AND extension.id = p_extension_id AND extension.status = 'observed'
       AND extension.coverage = 'complete' AND extension.content_sha IS NOT NULL
       AND extension.framework IN ('nextjs', 'astro', 'eleventy')
       AND extension.repository_id = binding.repository_id
       AND extension.base_sha = p_base_sha AND extension.tree_sha = p_tree_sha
     FOR SHARE OF extension, binding;
    IF NOT FOUND OR NOT (
        (v_extension.framework = 'nextjs' AND p_artifact_root = '.next')
        OR (v_extension.framework = 'astro' AND p_artifact_root = 'dist')
        OR (v_extension.framework = 'eleventy' AND p_artifact_root = '_site')
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'extension_inactive'::text; RETURN;
    END IF;
    IF v_existing.id IS NOT NULL THEN
        RETURN QUERY SELECT v_existing.id, v_existing.status, true, 'prepared'::text; RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_build_intents AS intent
        WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
          AND intent.extension_id = p_extension_id
          AND intent.status IN ('prepared', 'dispatched')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, 'build_exists'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_build_intents (
        tenant_id, site_id, id, extension_id, requested_by_user_id, idempotency_key,
        request_hash, membership_epoch, site_epoch, recovery_generation,
        base_sha, tree_sha, patch_sha256, toolchain, build_command, artifact_root, status
    ) VALUES (v_authority.tenant_id, p_site_id, p_build_id, p_extension_id,
        v_authority.user_id, p_request_id, p_request_hash, v_authority.membership_epoch,
        v_authority.site_authorization_epoch, p_generation, p_base_sha, p_tree_sha,
        p_patch_sha256, p_toolchain, p_build_command, p_artifact_root, 'prepared');
    RETURN QUERY SELECT p_build_id, 'prepared'::text, false, 'prepared'::text;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_prepare_candidate_build(text,bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_prepare_candidate_build(text,bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.prepare_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid, p_build_id uuid, p_request_id uuid, p_request_hash bytea, p_base_sha text, p_tree_sha text, p_patch_sha256 text, p_toolchain text, p_build_command text, p_artifact_root text)
 RETURNS TABLE(build_id uuid, build_status text, replayed boolean, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_prepare_candidate_build('owner',p_session_hash,p_site_id,p_generation,p_extension_id,p_build_id,p_request_id,p_request_hash,p_base_sha,p_tree_sha,p_patch_sha256,p_toolchain,p_build_command,p_artifact_root);
END;
$function$;

REVOKE ALL ON FUNCTION control.prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_prepare_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid, p_build_id uuid, p_request_id uuid, p_request_hash bytea, p_base_sha text, p_tree_sha text, p_patch_sha256 text, p_toolchain text, p_build_command text, p_artifact_root text)
 RETURNS TABLE(build_id uuid, build_status text, replayed boolean, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_prepare_candidate_build('internal_link_skill',p_session_hash,p_site_id,p_generation,p_extension_id,p_build_id,p_request_id,p_request_hash,p_base_sha,p_tree_sha,p_patch_sha256,p_toolchain,p_build_command,p_artifact_root);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_prepare_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid, p_build_id uuid, p_request_id uuid, p_request_hash bytea, p_base_sha text, p_tree_sha text, p_patch_sha256 text, p_toolchain text, p_build_command text, p_artifact_root text)
 RETURNS TABLE(build_id uuid, build_status text, replayed boolean, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_prepare_candidate_build('weekly_delivery',p_session_hash,p_site_id,p_generation,p_extension_id,p_build_id,p_request_id,p_request_hash,p_base_sha,p_tree_sha,p_patch_sha256,p_toolchain,p_build_command,p_artifact_root);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_prepare_candidate_build(bytea,uuid,text,uuid,uuid,uuid,bytea,text,text,text,text,text,text) TO signal_workflow;
-- End port group 9.
-- read_candidate_build: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_read_candidate_build(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS TABLE(build_status text, base_sha text, patch_sha256 text, toolchain text, build_command text, exit_class text, exit_code integer, logs_sha256 text, log_bytes integer, artifacts jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='internal_link_skill' THEN
PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'build_id',p_build_id);

END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('build_id',p_build_id::text));

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'read' ELSE NULL END);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,control.port_required_role(p_actor),false) THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::jsonb, 'permission_denied'::text; RETURN;
    END IF;
    RETURN QUERY SELECT intent.status, intent.base_sha, intent.patch_sha256,
        intent.toolchain, intent.build_command, receipt.exit_class,
        receipt.exit_code, receipt.logs_sha256, receipt.log_bytes,
        receipt.artifacts, 'found'::text
      FROM app.candidate_build_intents AS intent
      LEFT JOIN app.candidate_build_receipts AS receipt
        ON receipt.tenant_id = intent.tenant_id AND receipt.site_id = intent.site_id
       AND receipt.build_id = intent.id
     WHERE intent.tenant_id = v_authority.tenant_id AND intent.site_id = p_site_id
       AND intent.id = p_build_id AND intent.requested_by_user_id = v_authority.user_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::integer, NULL::text, NULL::integer,
            NULL::jsonb, 'build_not_found'::text;
    END IF;
END;
$function$;

ALTER FUNCTION control.port_read_candidate_build(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_candidate_build(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS TABLE(build_status text, base_sha text, patch_sha256 text, toolchain text, build_command text, exit_class text, exit_code integer, logs_sha256 text, log_bytes integer, artifacts jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_candidate_build('owner',p_session_hash,p_site_id,p_generation,p_build_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_candidate_build(bytea,uuid,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_read_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS TABLE(build_status text, base_sha text, patch_sha256 text, toolchain text, build_command text, exit_class text, exit_code integer, logs_sha256 text, log_bytes integer, artifacts jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_candidate_build('internal_link_skill',p_session_hash,p_site_id,p_generation,p_build_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_read_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_read_candidate_build(bytea,uuid,text,uuid) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_read_candidate_build(p_session_hash bytea, p_site_id uuid, p_generation text, p_build_id uuid)
 RETURNS TABLE(build_status text, base_sha text, patch_sha256 text, toolchain text, build_command text, exit_class text, exit_code integer, logs_sha256 text, log_bytes integer, artifacts jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_candidate_build('weekly_delivery',p_session_hash,p_site_id,p_generation,p_build_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_candidate_build(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 10.
-- read_github_base_risk: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_read_github_base_risk(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE a record; b app.github_read_bindings%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='internal_link_skill' THEN
PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'binding_id',p_binding_id);

END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('binding_id',p_binding_id::text));

END IF;
    SELECT * INTO a FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'read' ELSE NULL END);
    IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN NULL; END IF;
    SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site_id AND id=p_binding_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('protected',b.protected,'default_branch',b.default_branch,
        'risk_monitored',b.risk_monitored,'owner_accepted',
        b.recovery_generation=p_generation AND control.github_unprotected_base_accepted(b,p_generation));
END
$function$;

ALTER FUNCTION control.port_read_github_base_risk(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_github_base_risk(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_github_base_risk(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_read_github_base_risk('owner',p_session_hash,p_site_id,p_generation,p_binding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_github_base_risk(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_github_base_risk(bytea,uuid,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_read_github_base_risk(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_read_github_base_risk('internal_link_skill',p_session_hash,p_site_id,p_generation,p_binding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_read_github_base_risk(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_read_github_base_risk(bytea,uuid,text,uuid) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_read_github_base_risk(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_read_github_base_risk('weekly_delivery',p_session_hash,p_site_id,p_generation,p_binding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_github_base_risk(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_github_base_risk(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 11.
-- read_github_pr_extension: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_read_github_pr_extension(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid)
 RETURNS TABLE(extension_id uuid, extension_status text, binding_id uuid, repository_id bigint, base_sha text, tree_sha text, framework text, content_format text, coverage text, content_sha text, marker_evidence jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='internal_link_skill' THEN
PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'extension_id',p_extension_id);

END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('extension_id',p_extension_id::text));

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'read' ELSE NULL END);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::bigint,
          NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
          NULL::text, NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::bigint,
          NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
          NULL::text, NULL::jsonb, 'site_not_verified'::text; RETURN;
    END IF;
    RETURN QUERY SELECT extension.id, extension.status, extension.binding_id,
        extension.repository_id, extension.base_sha, extension.tree_sha,
        extension.framework, extension.content_format, extension.coverage,
        extension.content_sha, extension.marker_evidence, 'found'::text
      FROM app.github_pr_extensions AS extension
      JOIN app.github_read_bindings AS binding
        ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
       AND binding.id = extension.binding_id AND binding.status = 'active'
     WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
       AND extension.id = p_extension_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::uuid, NULL::bigint,
          NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
          NULL::text, NULL::jsonb, 'extension_not_found'::text;
    END IF;
END;
$function$;

ALTER FUNCTION control.port_read_github_pr_extension(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_github_pr_extension(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_github_pr_extension(p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid)
 RETURNS TABLE(extension_id uuid, extension_status text, binding_id uuid, repository_id bigint, base_sha text, tree_sha text, framework text, content_format text, coverage text, content_sha text, marker_evidence jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_pr_extension('owner',p_session_hash,p_site_id,p_generation,p_extension_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_github_pr_extension(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_github_pr_extension(bytea,uuid,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_read_github_pr_extension(p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid)
 RETURNS TABLE(extension_id uuid, extension_status text, binding_id uuid, repository_id bigint, base_sha text, tree_sha text, framework text, content_format text, coverage text, content_sha text, marker_evidence jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_pr_extension('internal_link_skill',p_session_hash,p_site_id,p_generation,p_extension_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_read_github_pr_extension(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_read_github_pr_extension(bytea,uuid,text,uuid) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_read_github_pr_extension(p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid)
 RETURNS TABLE(extension_id uuid, extension_status text, binding_id uuid, repository_id bigint, base_sha text, tree_sha text, framework text, content_format text, coverage text, content_sha text, marker_evidence jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_pr_extension('weekly_delivery',p_session_hash,p_site_id,p_generation,p_extension_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_github_pr_extension(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_github_pr_extension(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 12.
-- read_github_read_binding: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_read_github_read_binding(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS TABLE(binding_id uuid, binding_status text, installation_id bigint, repository_owner text, repository_name text, base_branch text, content_path text, repository_id bigint, observed_full_name text, base_sha text, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='internal_link_skill' THEN
PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'binding_id',p_binding_id);

END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('binding_id',p_binding_id::text));

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'read' ELSE NULL END);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::bigint, NULL::text,
          NULL::text, NULL::text, NULL::text, NULL::bigint, NULL::text, NULL::text,
          v_authority.outcome; RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.site_origin_verifications AS verification
        JOIN control.public_origin_claims AS claim
          ON claim.origin = verification.origin
         AND claim.tenant_id = verification.tenant_id
         AND claim.site_id = verification.site_id
         AND claim.verification_id = verification.id
        JOIN app.sites AS site ON site.tenant_id = verification.tenant_id
         AND site.id = verification.site_id AND site.primary_origin = verification.origin
        WHERE verification.tenant_id = v_authority.tenant_id
          AND verification.site_id = p_site_id AND site.ownership_status = 'verified'
          AND verification.recheck_at > transaction_timestamp()
          AND claim.recheck_at > transaction_timestamp()
    ) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::bigint, NULL::text,
          NULL::text, NULL::text, NULL::text, NULL::bigint, NULL::text, NULL::text,
          'site_not_verified'::text; RETURN;
    END IF;
    RETURN QUERY SELECT binding.id, binding.status, binding.installation_id,
        binding.repository_owner, binding.repository_name, binding.base_branch,
        binding.content_path, binding.repository_id, binding.observed_full_name,
        binding.base_sha, 'found'::text
      FROM app.github_read_bindings AS binding
     WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
       AND binding.id = p_binding_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::bigint, NULL::text,
          NULL::text, NULL::text, NULL::text, NULL::bigint, NULL::text, NULL::text,
          'binding_not_found'::text;
    END IF;
END;
$function$;

ALTER FUNCTION control.port_read_github_read_binding(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_github_read_binding(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_github_read_binding(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS TABLE(binding_id uuid, binding_status text, installation_id bigint, repository_owner text, repository_name text, base_branch text, content_path text, repository_id bigint, observed_full_name text, base_sha text, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_read_binding('owner',p_session_hash,p_site_id,p_generation,p_binding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_github_read_binding(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_github_read_binding(bytea,uuid,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_read_github_read_binding(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS TABLE(binding_id uuid, binding_status text, installation_id bigint, repository_owner text, repository_name text, base_branch text, content_path text, repository_id bigint, observed_full_name text, base_sha text, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_read_binding('internal_link_skill',p_session_hash,p_site_id,p_generation,p_binding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_read_github_read_binding(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_read_github_read_binding(bytea,uuid,text,uuid) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_read_github_read_binding(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid)
 RETURNS TABLE(binding_id uuid, binding_status text, installation_id bigint, repository_owner text, repository_name text, base_branch text, content_path text, repository_id bigint, observed_full_name text, base_sha text, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_read_binding('weekly_delivery',p_session_hash,p_site_id,p_generation,p_binding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_github_read_binding(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_github_read_binding(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 13.
-- record_github_binding_state: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_record_github_binding_state(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid, p_repository bigint, p_full_name text, p_installation bigint, p_default_branch text, p_base_branch text, p_protected boolean, p_permissions bytea)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE a record; b app.github_read_bindings%%ROWTYPE; v_reason text;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='internal_link_skill' THEN
PERFORM control.assert_internal_link_skill_resource(p_session_hash,p_site_id,p_generation,'binding_id',p_binding_id);

END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('binding_id',p_binding_id::text));

END IF;
    SELECT * INTO a FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'read' ELSE NULL END);
    IF NOT control.port_permission(a.outcome,a.role_key,control.port_required_role(p_actor),true) THEN RETURN false; END IF;
    SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site_id AND id=p_binding_id FOR UPDATE;
    IF NOT FOUND OR b.status<>'active' OR b.recovery_generation<>p_generation THEN RETURN false; END IF;
    IF p_repository IS NULL THEN v_reason := 'provider_unavailable';
    ELSIF p_repository IS DISTINCT FROM b.repository_id OR p_full_name IS DISTINCT FROM b.observed_full_name
       OR p_installation IS DISTINCT FROM b.installation_id OR p_default_branch IS DISTINCT FROM b.default_branch
       OR p_base_branch IS DISTINCT FROM b.base_branch THEN v_reason := 'identity_changed';
    ELSIF b.risk_monitored AND (p_permissions IS NULL OR octet_length(p_permissions)<>32
       OR p_permissions IS DISTINCT FROM b.permissions_sha256) THEN v_reason := 'permissions_changed';
    ELSIF b.protected IS FALSE AND p_protected IS TRUE THEN v_reason := 'protection_available';
    END IF;
    IF v_reason IS NOT NULL THEN
        INSERT INTO app.github_base_risk_invalidations VALUES (b.tenant_id,b.site_id,gen_random_uuid(),b.id,b.risk_generation,v_reason,transaction_timestamp());
        UPDATE app.github_read_bindings SET risk_generation=risk_generation+1, permissions_sha256=CASE WHEN v_reason IN ('protection_available','permissions_changed') AND p_protected IS TRUE THEN p_permissions ELSE NULL END,
            protected=CASE WHEN v_reason IN ('protection_available','permissions_changed') AND p_protected IS TRUE THEN true ELSE false END
        WHERE tenant_id=b.tenant_id AND site_id=b.site_id AND id=b.id;
        RETURN v_reason IN ('protection_available','permissions_changed') AND p_protected IS TRUE;
    END IF;
    UPDATE app.github_read_bindings SET protected=p_protected
    WHERE tenant_id=b.tenant_id AND site_id=b.site_id AND id=b.id;
    b.protected := p_protected;
    RETURN p_protected IS TRUE OR control.github_unprotected_base_accepted(b,p_generation);
END
$function$;

ALTER FUNCTION control.port_record_github_binding_state(text,bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_record_github_binding_state(text,bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.record_github_binding_state(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid, p_repository bigint, p_full_name text, p_installation bigint, p_default_branch text, p_base_branch text, p_protected boolean, p_permissions bytea)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_github_binding_state('owner',p_session_hash,p_site_id,p_generation,p_binding_id,p_repository,p_full_name,p_installation,p_default_branch,p_base_branch,p_protected,p_permissions);
END;
$function$;

REVOKE ALL ON FUNCTION control.record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_record_github_binding_state(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid, p_repository bigint, p_full_name text, p_installation bigint, p_default_branch text, p_base_branch text, p_protected boolean, p_permissions bytea)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_github_binding_state('internal_link_skill',p_session_hash,p_site_id,p_generation,p_binding_id,p_repository,p_full_name,p_installation,p_default_branch,p_base_branch,p_protected,p_permissions);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_record_github_binding_state(p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid, p_repository bigint, p_full_name text, p_installation bigint, p_default_branch text, p_base_branch text, p_protected boolean, p_permissions bytea)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_github_binding_state('weekly_delivery',p_session_hash,p_site_id,p_generation,p_binding_id,p_repository,p_full_name,p_installation,p_default_branch,p_base_branch,p_protected,p_permissions);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) TO signal_workflow;
-- End port group 14.
-- seal_internal_link_revision: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_seal_internal_link_revision(p_actor text, p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_key uuid, p_canonical bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','internal_link_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record; p jsonb; e jsonb; sources jsonb; src jsonb; dst jsonb; b record; receipt record;
 release record; prior record; cap integer; baseline record; output text; shared text[];
BEGIN
 SELECT * INTO a FROM control.admit_port_context('owner','authority',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN a.outcome; END IF;
 IF NOT control.port_permission('authorized',a.role_key,'owner',true) THEN RETURN 'permission_denied'; END IF;
 IF NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'site_not_verified'; END IF;
 IF p_id IS NULL OR p_key IS NULL OR p_canonical IS NULL OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768 THEN RETURN 'invalid_revision'; END IF;
 BEGIN p:=convert_from(p_canonical,'UTF8')::jsonb;
 EXCEPTION WHEN invalid_text_representation OR character_not_in_repertoire THEN RETURN 'invalid_revision'; END;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,140));
 SELECT * INTO prior FROM app.candidate_recipe_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND created_by_user_id=a.user_id AND idempotency_key=p_key;
 IF FOUND THEN RETURN CASE WHEN prior.id=p_id AND prior.canonical_manifest=p_canonical THEN 'sealed' ELSE 'revision_conflict' END; END IF;
 sources:=control.internal_link_sources(p_hash,p_generation,p_site); e:=p->'evidence';
 SELECT value INTO src FROM jsonb_array_elements(sources->'pages') WHERE value->>'id'=e->>'page_id';
 SELECT value INTO dst FROM jsonb_array_elements(sources->'pages') WHERE value->>'id'=p#>>'{internal_link,target_id}';
 IF src IS NULL OR dst IS NULL OR src=dst
 OR e->>'manifest_id' IS DISTINCT FROM sources->>'manifest_id'
 OR e->>'manifest_sha256' IS DISTINCT FROM sources->>'manifest_sha256'
 OR e->>'site_origin' IS DISTINCT FROM sources->>'site_origin'
 OR e->>'page_url' IS DISTINCT FROM src->>'url'
 OR p#>>'{internal_link,target_url}' IS DISTINCT FROM dst->>'url'
 OR left(dst->>'url',length(sources->>'site_origin')+1) IS DISTINCT FROM (sources->>'site_origin') || '/'
 OR dst->>'url' ~ '[?#[:space:]\\]'
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(src->'internal_links') href
 WHERE split_part(href,'#',1)=dst->>'url')
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(dst->'internal_links') href
 WHERE split_part(href,'#',1)=src->>'url')
 OR p->>'source_sha256' IS DISTINCT FROM src->>'body_sha256'
 OR e#>>'{finding,key}' IS DISTINCT FROM 'links.internal.add'
 THEN RETURN 'evidence_unavailable'; END IF;
 SELECT coalesce(array_agg(term ORDER BY term),'{}'::text[]) INTO shared
 FROM unnest(control.internal_link_terms(src)) term WHERE term=ANY(control.internal_link_terms(dst));
 IF cardinality(shared)<2 OR (SELECT count(*) FROM jsonb_array_elements(sources->'pages') x
 WHERE x->>'id'<>dst->>'id' AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(x->'internal_links') href
 WHERE split_part(href,'#',1)=dst->>'url'))>1
 OR (SELECT count(DISTINCT lower(word[1])) FROM regexp_matches(p#>>'{patch,before}',
 '[a-zA-Z][a-zA-Z0-9-]{2,}','g') word WHERE lower(word[1])=ANY(shared))<2
 THEN RETURN 'relatedness_unavailable'; END IF;
 IF coalesce(p#>>'{internal_link,page_cap}','') !~ '^[1-3]$' THEN RETURN 'invalid_revision'; END IF;
 cap:=(p#>>'{internal_link,page_cap}')::integer;
 IF (SELECT count(*) FROM app.internal_link_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND cycle_start=(date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date AND source_url=src->>'url')>=cap
 THEN RETURN 'page_cap'; END IF;
 IF EXISTS(SELECT 1 FROM app.internal_link_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND (anchor_key=lower(p#>>'{patch,before}') OR (cycle_start=(date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date
 AND ((source_url=src->>'url' AND target_url=dst->>'url')
 OR (source_url=dst->>'url' AND target_url=src->>'url'))))) THEN RETURN 'duplicate_link_or_anchor'; END IF;
 SELECT * INTO release FROM control.recipe_releases WHERE id=(p->>'recipe_release_id')::uuid
 AND recipe_key='technical_internal_link_add' AND encode(content_hash,'hex')=p->>'release_content_hash' FOR SHARE;
 IF release.id IS NULL OR (SELECT status FROM control.recipe_release_events WHERE release_id=release.id
 ORDER BY sequence_number DESC LIMIT 1) IS DISTINCT FROM 'REVIEWED'
 OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=release.id)
 OR convert_from(release.canonical_body,'UTF8')::jsonb->>'approval_class' IS DISTINCT FROM 'owner_review'
 OR control.recipe_autonomy_eligible(release.id) THEN RETURN 'release_unavailable'; END IF;
 SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND id=(p->>'build_id')::uuid AND extension_id=(p->>'extension_id')::uuid AND status='completed'
 AND requested_by_user_id=a.user_id AND membership_epoch=a.membership_epoch
 AND site_epoch=a.site_authorization_epoch AND recovery_generation=p_generation;
 SELECT * INTO receipt FROM app.candidate_build_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND build_id=b.id AND exit_class='passed';
 IF b.id IS NULL OR receipt.build_id IS NULL OR receipt.patch_sha256 IS DISTINCT FROM b.patch_sha256
 OR b.patch_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
 OR NOT EXISTS(SELECT 1 FROM app.github_pr_extensions x JOIN app.github_read_bindings y
 ON y.tenant_id=x.tenant_id AND y.site_id=x.site_id AND y.id=x.binding_id AND y.status='active'
 WHERE x.tenant_id=a.tenant_id AND x.site_id=p_site AND x.id=b.extension_id AND x.status='observed'
 AND x.framework='eleventy' AND x.content_format='html' AND x.base_sha=b.base_sha AND x.tree_sha=b.tree_sha)
 THEN RETURN 'build_unavailable'; END IF;
 IF p->>'site_id' IS DISTINCT FROM p_site::text OR p->>'base_sha' IS DISTINCT FROM b.base_sha
 OR coalesce(p->>'source_path','') !~ '^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*\.html$'
 OR src->>'url' IS DISTINCT FROM (sources->>'site_origin') || '/' || (CASE
 WHEN p->>'source_path'='index.html' OR p->>'source_path' LIKE '%%/index.html'
 THEN left(p->>'source_path',length(p->>'source_path')-10) ELSE p->>'source_path' END)
 OR p->>'patch_sha256' IS DISTINCT FROM b.patch_sha256
 OR p->>'approval_class' IS DISTINCT FROM 'owner_review'
 OR p#>>'{internal_link,recipe_key}' IS DISTINCT FROM 'technical_internal_link_add'
 OR p->>'audit_report_id' IS NOT NULL
 OR p->'model_draft' IS DISTINCT FROM 'null'::jsonb
 OR p->'claim_review_required' IS DISTINCT FROM 'false'::jsonb
 OR p#>'{internal_link,autonomy_eligible}' IS DISTINCT FROM 'false'::jsonb
 OR coalesce(p->>'result_sha256','') !~ '^[0-9a-f]{64}$'
 OR coalesce(p#>>'{patch,offset}','') !~ '^(0|[1-9][0-9]*)$'
 OR coalesce(p#>>'{patch,before}','') !~ '^[A-Za-z0-9-]+( [A-Za-z0-9-]+){1,2}$'
 OR length(p#>>'{patch,before}') NOT BETWEEN 3 AND 80
 OR (SELECT count(*)<>count(DISTINCT lower(w)) FROM unnest(string_to_array(p#>>'{patch,before}',' ')) w)
 OR p#>>'{patch,after}' IS DISTINCT FROM '<a href="' || replace(replace(replace(replace(replace(dst->>'url','&','&amp;'),'"','&quot;'),'<','&lt;'),'>','&gt;'),chr(39),'&#x27;') || '">' || (p#>>'{patch,before}') || '</a>'
 OR p#>>'{build_receipt,toolchain}' IS DISTINCT FROM receipt.toolchain
 OR p#>>'{build_receipt,command}' IS DISTINCT FROM receipt.build_command
 OR p#>>'{build_receipt,exit_class}' IS DISTINCT FROM 'passed'
 OR p#>>'{build_receipt,logs_sha256}' IS DISTINCT FROM receipt.logs_sha256
 OR p#>'{build_receipt,artifacts}' IS DISTINCT FROM receipt.artifacts
 OR coalesce(length(p->>'expected_impact'),0) NOT BETWEEN 1 AND 500
 OR coalesce(length(p->>'recovery_plan'),0) NOT BETWEEN 1 AND 500 THEN RETURN 'invalid_revision'; END IF;
 output:='_site/' || (p->>'source_path');
 SELECT r.* INTO baseline FROM app.candidate_build_receipts r JOIN app.candidate_build_intents i
 ON i.tenant_id=r.tenant_id AND i.site_id=r.site_id AND i.id=r.build_id
 WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND r.build_id=(p#>>'{internal_link,baseline_build_id}')::uuid
 AND r.exit_class='passed' AND r.base_sha=b.base_sha AND i.tree_sha=b.tree_sha
 AND i.extension_id=b.extension_id AND i.requested_by_user_id=a.user_id
 AND i.membership_epoch=a.membership_epoch AND i.site_epoch=a.site_authorization_epoch
 AND i.recovery_generation=p_generation AND r.patch_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
 IF baseline.build_id IS NULL OR p#>>'{internal_link,output_path}' IS DISTINCT FROM output
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(baseline.artifacts) x WHERE x->>'path'=output AND x->>'sha256'=p->>'source_sha256')
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(receipt.artifacts) x WHERE x->>'path'=output AND x->>'sha256'=p->>'result_sha256'
 AND (x->>'size')::integer=(SELECT (base_artifact->>'size')::integer FROM jsonb_array_elements(baseline.artifacts) base_artifact WHERE base_artifact->>'path'=output)
 + octet_length(convert_to((p#>>'{patch,after}'),'UTF8'))-octet_length(convert_to((p#>>'{patch,before}'),'UTF8')))
 OR (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(baseline.artifacts) x WHERE x->>'path'<>output)
 IS DISTINCT FROM (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(receipt.artifacts) x WHERE x->>'path'<>output)
 THEN RETURN 'build_assertions_failed'; END IF;
 INSERT INTO app.candidate_recipe_revisions(tenant_id,site_id,id,extension_id,build_id,audit_report_id,
 finding_id,recipe_release_id,release_content_hash,base_sha,patch_sha256,canonical_manifest,
 created_by_user_id,idempotency_key,membership_epoch,site_epoch,recovery_generation)
 VALUES(a.tenant_id,p_site,p_id,b.extension_id,b.id,NULL,(p->>'finding_id')::uuid,release.id,release.content_hash,
 b.base_sha,b.patch_sha256,p_canonical,a.user_id,p_key,a.membership_epoch,a.site_authorization_epoch,p_generation);
 INSERT INTO app.internal_link_candidates(tenant_id,site_id,revision_id,manifest_id,source_id,target_id,anchor,source_url,target_url)
 VALUES(a.tenant_id,p_site,p_id,(sources->>'manifest_id')::uuid,(src->>'id')::uuid,(dst->>'id')::uuid,p#>>'{patch,before}',src->>'url',dst->>'url');
 RETURN 'sealed';
END;
ELSIF p_actor='internal_link_skill' THEN
DECLARE a record; p jsonb; e jsonb; sources jsonb; src jsonb; dst jsonb; b record; receipt record;
 release record; prior record; cap integer; baseline record; output text; shared text[];
BEGIN
 SELECT * INTO a FROM control.admit_port_context('internal_link_skill','authority',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN a.outcome; END IF;
 IF NOT control.port_permission('authorized',a.role_key,'workload',true) THEN RETURN 'permission_denied'; END IF;
 IF NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'site_not_verified'; END IF;
 IF p_id IS NULL OR p_key IS NULL OR p_canonical IS NULL OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768 THEN RETURN 'invalid_revision'; END IF;
 BEGIN p:=convert_from(p_canonical,'UTF8')::jsonb;
 EXCEPTION WHEN invalid_text_representation OR character_not_in_repertoire THEN RETURN 'invalid_revision'; END;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,140)); IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_hash AND u->>'revision_key'=p_key::text AND u->>'source_id'=p#>>'{evidence,page_id}' AND u->>'target_id'=p#>>'{internal_link,target_id}' AND u->>'extension_id'=p->>'extension_id' AND u->>'release_id'=p->>'recipe_release_id' AND u->>'manifest_id'=p#>>'{evidence,manifest_id}' AND EXISTS(SELECT 1 FROM app.candidate_build_intents cb JOIN app.candidate_build_intents bb ON bb.tenant_id=cb.tenant_id AND bb.site_id=cb.site_id WHERE cb.tenant_id=i.tenant_id AND cb.site_id=i.site_id AND cb.id::text=p->>'build_id' AND cb.idempotency_key::text=u->>'build_key' AND bb.id::text=p#>>'{internal_link,baseline_build_id}' AND bb.idempotency_key::text=u->>'baseline_key')) THEN RETURN 'scope_denied'; END IF;
 SELECT * INTO prior FROM app.candidate_recipe_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND created_by_user_id=a.user_id AND idempotency_key=p_key;
 IF FOUND THEN RETURN CASE WHEN prior.id=p_id AND prior.canonical_manifest=p_canonical THEN 'sealed' ELSE 'revision_conflict' END; END IF;
 sources:=control.internal_link_skill_internal_link_sources(p_hash,p_generation,p_site); e:=p->'evidence';
 SELECT value INTO src FROM jsonb_array_elements(sources->'pages') WHERE value->>'id'=e->>'page_id';
 SELECT value INTO dst FROM jsonb_array_elements(sources->'pages') WHERE value->>'id'=p#>>'{internal_link,target_id}';
 IF src IS NULL OR dst IS NULL OR src=dst
 OR e->>'manifest_id' IS DISTINCT FROM sources->>'manifest_id'
 OR e->>'manifest_sha256' IS DISTINCT FROM sources->>'manifest_sha256'
 OR e->>'site_origin' IS DISTINCT FROM sources->>'site_origin'
 OR e->>'page_url' IS DISTINCT FROM src->>'url'
 OR p#>>'{internal_link,target_url}' IS DISTINCT FROM dst->>'url'
 OR left(dst->>'url',length(sources->>'site_origin')+1) IS DISTINCT FROM (sources->>'site_origin') || '/'
 OR dst->>'url' ~ '[?#[:space:]\\]'
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(src->'internal_links') href
 WHERE split_part(href,'#',1)=dst->>'url')
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(dst->'internal_links') href
 WHERE split_part(href,'#',1)=src->>'url')
 OR p->>'source_sha256' IS DISTINCT FROM src->>'body_sha256'
 OR e#>>'{finding,key}' IS DISTINCT FROM 'links.internal.add'
 THEN RETURN 'evidence_unavailable'; END IF;
 SELECT coalesce(array_agg(term ORDER BY term),'{}'::text[]) INTO shared
 FROM unnest(control.internal_link_terms(src)) term WHERE term=ANY(control.internal_link_terms(dst));
 IF cardinality(shared)<2 OR (SELECT count(*) FROM jsonb_array_elements(sources->'pages') x
 WHERE x->>'id'<>dst->>'id' AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(x->'internal_links') href
 WHERE split_part(href,'#',1)=dst->>'url'))>1
 OR (SELECT count(DISTINCT lower(word[1])) FROM regexp_matches(p#>>'{patch,before}',
 '[a-zA-Z][a-zA-Z0-9-]{2,}','g') word WHERE lower(word[1])=ANY(shared))<2
 THEN RETURN 'relatedness_unavailable'; END IF;
 IF coalesce(p#>>'{internal_link,page_cap}','') !~ '^[1-3]$' THEN RETURN 'invalid_revision'; END IF;
 cap:=(p#>>'{internal_link,page_cap}')::integer;
 IF (SELECT count(*) FROM app.internal_link_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND cycle_start=(date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date AND source_url=src->>'url')>=cap
 THEN RETURN 'page_cap'; END IF;
 IF EXISTS(SELECT 1 FROM app.internal_link_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND (anchor_key=lower(p#>>'{patch,before}') OR (cycle_start=(date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date
 AND ((source_url=src->>'url' AND target_url=dst->>'url')
 OR (source_url=dst->>'url' AND target_url=src->>'url'))))) THEN RETURN 'duplicate_link_or_anchor'; END IF;
 SELECT * INTO release FROM control.recipe_releases WHERE id=(p->>'recipe_release_id')::uuid
 AND recipe_key='technical_internal_link_add' AND encode(content_hash,'hex')=p->>'release_content_hash' FOR SHARE;
 IF release.id IS NULL OR (SELECT status FROM control.recipe_release_events WHERE release_id=release.id
 ORDER BY sequence_number DESC LIMIT 1) IS DISTINCT FROM 'REVIEWED'
 OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=release.id)
 OR convert_from(release.canonical_body,'UTF8')::jsonb->>'approval_class' IS DISTINCT FROM 'owner_review'
 OR control.recipe_autonomy_eligible(release.id) THEN RETURN 'release_unavailable'; END IF;
 SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND id=(p->>'build_id')::uuid AND extension_id=(p->>'extension_id')::uuid AND status='completed'
 AND requested_by_user_id=a.user_id AND membership_epoch=a.membership_epoch
 AND site_epoch=a.site_authorization_epoch AND recovery_generation=p_generation;
 SELECT * INTO receipt FROM app.candidate_build_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND build_id=b.id AND exit_class='passed';
 IF b.id IS NULL OR receipt.build_id IS NULL OR receipt.patch_sha256 IS DISTINCT FROM b.patch_sha256
 OR b.patch_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
 OR NOT EXISTS(SELECT 1 FROM app.github_pr_extensions x JOIN app.github_read_bindings y
 ON y.tenant_id=x.tenant_id AND y.site_id=x.site_id AND y.id=x.binding_id AND y.status='active'
 WHERE x.tenant_id=a.tenant_id AND x.site_id=p_site AND x.id=b.extension_id AND x.status='observed'
 AND x.framework='eleventy' AND x.content_format='html' AND x.base_sha=b.base_sha AND x.tree_sha=b.tree_sha)
 THEN RETURN 'build_unavailable'; END IF;
 IF p->>'site_id' IS DISTINCT FROM p_site::text OR p->>'base_sha' IS DISTINCT FROM b.base_sha
 OR coalesce(p->>'source_path','') !~ '^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*\.html$'
 OR src->>'url' IS DISTINCT FROM (sources->>'site_origin') || '/' || (CASE
 WHEN p->>'source_path'='index.html' OR p->>'source_path' LIKE '%%/index.html'
 THEN left(p->>'source_path',length(p->>'source_path')-10) ELSE p->>'source_path' END)
 OR p->>'patch_sha256' IS DISTINCT FROM b.patch_sha256
 OR p->>'approval_class' IS DISTINCT FROM 'owner_review'
 OR p#>>'{internal_link,recipe_key}' IS DISTINCT FROM 'technical_internal_link_add'
 OR p->>'audit_report_id' IS NOT NULL
 OR p->'model_draft' IS DISTINCT FROM 'null'::jsonb
 OR p->'claim_review_required' IS DISTINCT FROM 'false'::jsonb
 OR p#>'{internal_link,autonomy_eligible}' IS DISTINCT FROM 'false'::jsonb
 OR coalesce(p->>'result_sha256','') !~ '^[0-9a-f]{64}$'
 OR coalesce(p#>>'{patch,offset}','') !~ '^(0|[1-9][0-9]*)$'
 OR coalesce(p#>>'{patch,before}','') !~ '^[A-Za-z0-9-]+( [A-Za-z0-9-]+){1,2}$'
 OR length(p#>>'{patch,before}') NOT BETWEEN 3 AND 80
 OR (SELECT count(*)<>count(DISTINCT lower(w)) FROM unnest(string_to_array(p#>>'{patch,before}',' ')) w)
 OR p#>>'{patch,after}' IS DISTINCT FROM '<a href="' || replace(replace(replace(replace(replace(dst->>'url','&','&amp;'),'"','&quot;'),'<','&lt;'),'>','&gt;'),chr(39),'&#x27;') || '">' || (p#>>'{patch,before}') || '</a>'
 OR p#>>'{build_receipt,toolchain}' IS DISTINCT FROM receipt.toolchain
 OR p#>>'{build_receipt,command}' IS DISTINCT FROM receipt.build_command
 OR p#>>'{build_receipt,exit_class}' IS DISTINCT FROM 'passed'
 OR p#>>'{build_receipt,logs_sha256}' IS DISTINCT FROM receipt.logs_sha256
 OR p#>'{build_receipt,artifacts}' IS DISTINCT FROM receipt.artifacts
 OR coalesce(length(p->>'expected_impact'),0) NOT BETWEEN 1 AND 500
 OR coalesce(length(p->>'recovery_plan'),0) NOT BETWEEN 1 AND 500 THEN RETURN 'invalid_revision'; END IF;
 output:='_site/' || (p->>'source_path');
 SELECT r.* INTO baseline FROM app.candidate_build_receipts r JOIN app.candidate_build_intents i
 ON i.tenant_id=r.tenant_id AND i.site_id=r.site_id AND i.id=r.build_id
 WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND r.build_id=(p#>>'{internal_link,baseline_build_id}')::uuid
 AND r.exit_class='passed' AND r.base_sha=b.base_sha AND i.tree_sha=b.tree_sha
 AND i.extension_id=b.extension_id AND i.requested_by_user_id=a.user_id
 AND i.membership_epoch=a.membership_epoch AND i.site_epoch=a.site_authorization_epoch
 AND i.recovery_generation=p_generation AND r.patch_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
 IF baseline.build_id IS NULL OR p#>>'{internal_link,output_path}' IS DISTINCT FROM output
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(baseline.artifacts) x WHERE x->>'path'=output AND x->>'sha256'=p->>'source_sha256')
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(receipt.artifacts) x WHERE x->>'path'=output AND x->>'sha256'=p->>'result_sha256'
 AND (x->>'size')::integer=(SELECT (base_artifact->>'size')::integer FROM jsonb_array_elements(baseline.artifacts) base_artifact WHERE base_artifact->>'path'=output)
 + octet_length(convert_to((p#>>'{patch,after}'),'UTF8'))-octet_length(convert_to((p#>>'{patch,before}'),'UTF8')))
 OR (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(baseline.artifacts) x WHERE x->>'path'<>output)
 IS DISTINCT FROM (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(receipt.artifacts) x WHERE x->>'path'<>output)
 THEN RETURN 'build_assertions_failed'; END IF;
 INSERT INTO app.candidate_recipe_revisions(tenant_id,site_id,id,extension_id,build_id,audit_report_id,
 finding_id,recipe_release_id,release_content_hash,base_sha,patch_sha256,canonical_manifest,
 created_by_user_id,idempotency_key,membership_epoch,site_epoch,recovery_generation)
 VALUES(a.tenant_id,p_site,p_id,b.extension_id,b.id,NULL,(p->>'finding_id')::uuid,release.id,release.content_hash,
 b.base_sha,b.patch_sha256,p_canonical,a.user_id,p_key,a.membership_epoch,a.site_authorization_epoch,p_generation);
 INSERT INTO app.internal_link_candidates(tenant_id,site_id,revision_id,manifest_id,source_id,target_id,anchor,source_url,target_url)
 VALUES(a.tenant_id,p_site,p_id,(sources->>'manifest_id')::uuid,(src->>'id')::uuid,(dst->>'id')::uuid,p#>>'{patch,before}',src->>'url',dst->>'url');
 RETURN 'sealed';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_seal_internal_link_revision(text,bytea,text,uuid,uuid,uuid,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_seal_internal_link_revision(text,bytea,text,uuid,uuid,uuid,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.seal_internal_link_revision(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_key uuid, p_canonical bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seal_internal_link_revision('owner',p_hash,p_generation,p_site,p_id,p_key,p_canonical);
END;
$function$;

REVOKE ALL ON FUNCTION control.seal_internal_link_revision(bytea,text,uuid,uuid,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.seal_internal_link_revision(bytea,text,uuid,uuid,uuid,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.internal_link_skill_seal_internal_link_revision(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_key uuid, p_canonical bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seal_internal_link_revision('internal_link_skill',p_hash,p_generation,p_site,p_id,p_key,p_canonical);
END;
$function$;

REVOKE ALL ON FUNCTION control.internal_link_skill_seal_internal_link_revision(bytea,text,uuid,uuid,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.internal_link_skill_seal_internal_link_revision(bytea,text,uuid,uuid,uuid,bytea) TO signal_workflow;
-- End port group 15.
-- acknowledge_github_pr_intent: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_acknowledge_github_pr_intent(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_journal_generation uuid, p_position bigint, p_body_hash bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));

END IF;
    PERFORM * FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'prepare' ELSE NULL END);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.port_github_pr_operation_eligible(CASE p_actor WHEN 'weekly_delivery' THEN 'weekly_delivery' ELSE 'owner' END,p_session_hash,p_site_id,p_generation,v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_operation.tenant_id <> v_eligible.tenant_id OR p_journal_generation IS NULL
       OR p_position < 1 OR octet_length(p_body_hash) <> 32 THEN
        RETURN 'journal_invalid';
    END IF;
    IF v_operation.journal_generation IS NOT NULL THEN
        IF (v_operation.journal_generation, v_operation.journal_position,
            v_operation.journal_body_hash) = (p_journal_generation, p_position, p_body_hash) THEN
            RETURN 'acknowledged';
        END IF;
        RETURN 'journal_conflict';
    END IF;
    UPDATE app.github_pr_operations SET journal_generation = p_journal_generation,
        journal_position = p_position, journal_body_hash = p_body_hash,
        updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        v_operation.step, 'journal_acknowledged', p_body_hash);
    RETURN 'acknowledged';
END;
$function$;

ALTER FUNCTION control.port_acknowledge_github_pr_intent(text,bytea,uuid,text,uuid,uuid,bigint,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_acknowledge_github_pr_intent(text,bytea,uuid,text,uuid,uuid,bigint,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.acknowledge_github_pr_intent(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_journal_generation uuid, p_position bigint, p_body_hash bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_acknowledge_github_pr_intent('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_journal_generation,p_position,p_body_hash);
END;
$function$;

REVOKE ALL ON FUNCTION control.acknowledge_github_pr_intent(bytea,uuid,text,uuid,uuid,bigint,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.acknowledge_github_pr_intent(bytea,uuid,text,uuid,uuid,bigint,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_acknowledge_github_pr_intent(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_journal_generation uuid, p_position bigint, p_body_hash bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_acknowledge_github_pr_intent('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_journal_generation,p_position,p_body_hash);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_acknowledge_github_pr_intent(bytea,uuid,text,uuid,uuid,bigint,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_acknowledge_github_pr_intent(bytea,uuid,text,uuid,uuid,bigint,bytea) TO signal_workflow;
-- End port group 16.
-- begin_github_delivery_observation: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_begin_github_delivery_observation(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_attempt_id uuid, p_environment text, p_actor_id bigint)
 RETURNS TABLE(canonical_manifest bytea, canonical_receipt bytea, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_previous app.github_delivery_attempts%%ROWTYPE; v_sequence integer;
BEGIN
    IF p_attempt_id IS NULL OR p_environment IS NULL OR p_environment !~
       '^[A-Za-z0-9][A-Za-z0-9_. /-]{0,99}$' OR p_actor_id IS NULL OR p_actor_id < 1 THEN
        RAISE EXCEPTION 'invalid_delivery_observation' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.github_delivery_read_authority(
        p_session_hash, p_site_id, p_generation, p_operation_id);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::bytea, NULL::bytea, v_authority.outcome; RETURN;
    END IF;
    PERFORM 1 FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    IF FOUND THEN
        IF v_attempt.operation_id <> p_operation_id OR v_attempt.environment <> p_environment
           OR v_attempt.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::bytea, receipt.canonical_receipt, 'completed'::text
          FROM app.github_delivery_receipts receipt WHERE receipt.tenant_id = v_authority.tenant_id
           AND receipt.site_id = p_site_id AND receipt.attempt_id = p_attempt_id;
        IF NOT FOUND THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'outcome_unknown'::text;
        END IF;
        RETURN;
    END IF;
    SELECT * INTO v_previous FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND operation_id = p_operation_id ORDER BY sequence_number DESC LIMIT 1;
    IF FOUND THEN
        IF v_previous.environment <> p_environment OR v_previous.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        IF v_previous.sequence_number >= 24 THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_budget_exhausted'::text; RETURN;
        END IF;
        IF v_previous.next_observe_at > transaction_timestamp()
           OR (v_previous.state = 'dispatching' AND v_previous.expires_at > transaction_timestamp()) THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_backoff'::text; RETURN;
        END IF;
        UPDATE app.github_delivery_attempts SET state = 'outcome_unknown'
         WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id
           AND id = v_previous.id AND state = 'dispatching';
    END IF;
    v_sequence := coalesce(v_previous.sequence_number, 0) + 1;
    INSERT INTO app.github_delivery_attempts
      (tenant_id, site_id, id, operation_id, sequence_number, environment, deployment_actor_id,
       recovery_generation, membership_epoch, site_epoch, state, expires_at, next_observe_at)
    VALUES (v_authority.tenant_id, p_site_id, p_attempt_id, p_operation_id, v_sequence,
      p_environment, p_actor_id, p_generation, v_authority.membership_epoch, v_authority.site_epoch,
      'dispatching', transaction_timestamp() + interval '300 seconds',
      transaction_timestamp() + make_interval(secs => least(3600, 30 * power(2, least(v_sequence - 1, 7)))::integer));
    RETURN QUERY SELECT revision.canonical_manifest, NULL::bytea, 'dispatching'::text
      FROM app.github_pr_operations operation JOIN control.pr_delivery_revisions revision
      ON revision.tenant_id = operation.tenant_id AND revision.site_id = operation.site_id
        AND revision.id = operation.candidate_revision_id
        AND revision.candidate_kind = CASE WHEN operation.authority_kind='owner_editorial' THEN 'content' ELSE 'technical' END
     WHERE operation.tenant_id = v_authority.tenant_id AND operation.site_id = p_site_id
       AND operation.id = p_operation_id;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_previous app.github_delivery_attempts%%ROWTYPE; v_sequence integer;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    IF p_attempt_id IS NULL OR p_environment IS NULL OR p_environment !~
       '^[A-Za-z0-9][A-Za-z0-9_. /-]{0,99}$' OR p_actor_id IS NULL OR p_actor_id < 1 THEN
        RAISE EXCEPTION 'invalid_delivery_observation' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.weekly_github_delivery_read_authority(
        p_session_hash, p_site_id, p_generation, p_operation_id);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::bytea, NULL::bytea, v_authority.outcome; RETURN;
    END IF;
    PERFORM 1 FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    IF FOUND THEN
        IF v_attempt.operation_id <> p_operation_id OR v_attempt.environment <> p_environment
           OR v_attempt.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::bytea, receipt.canonical_receipt, 'completed'::text
          FROM app.github_delivery_receipts receipt WHERE receipt.tenant_id = v_authority.tenant_id
           AND receipt.site_id = p_site_id AND receipt.attempt_id = p_attempt_id;
        IF NOT FOUND THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'outcome_unknown'::text;
        END IF;
        RETURN;
    END IF;
    SELECT * INTO v_previous FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND operation_id = p_operation_id ORDER BY sequence_number DESC LIMIT 1;
    IF FOUND THEN
        IF v_previous.environment <> p_environment OR v_previous.deployment_actor_id <> p_actor_id THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_conflict'::text; RETURN;
        END IF;
        IF v_previous.sequence_number >= 24 THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_budget_exhausted'::text; RETURN;
        END IF;
        IF v_previous.next_observe_at > transaction_timestamp()
           OR (v_previous.state = 'dispatching' AND v_previous.expires_at > transaction_timestamp()) THEN
            RETURN QUERY SELECT NULL::bytea, NULL::bytea, 'observation_backoff'::text; RETURN;
        END IF;
        UPDATE app.github_delivery_attempts SET state = 'outcome_unknown'
         WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id
           AND id = v_previous.id AND state = 'dispatching';
    END IF;
    v_sequence := coalesce(v_previous.sequence_number, 0) + 1;
    INSERT INTO app.github_delivery_attempts
      (tenant_id, site_id, id, operation_id, sequence_number, environment, deployment_actor_id,
       recovery_generation, membership_epoch, site_epoch, state, expires_at, next_observe_at)
    VALUES (v_authority.tenant_id, p_site_id, p_attempt_id, p_operation_id, v_sequence,
      p_environment, p_actor_id, p_generation, v_authority.membership_epoch, v_authority.site_epoch,
      'dispatching', transaction_timestamp() + interval '300 seconds',
      transaction_timestamp() + make_interval(secs => least(3600, 30 * power(2, least(v_sequence - 1, 7)))::integer));
    RETURN QUERY SELECT revision.canonical_manifest, NULL::bytea, 'dispatching'::text
      FROM app.github_pr_operations operation JOIN app.candidate_recipe_revisions revision
      ON revision.tenant_id = operation.tenant_id AND revision.site_id = operation.site_id
        AND revision.id = operation.candidate_revision_id
     WHERE operation.tenant_id = v_authority.tenant_id AND operation.site_id = p_site_id
       AND operation.id = p_operation_id;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_begin_github_delivery_observation(text,bytea,uuid,text,uuid,uuid,text,bigint) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_begin_github_delivery_observation(text,bytea,uuid,text,uuid,uuid,text,bigint) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.begin_github_delivery_observation(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_attempt_id uuid, p_environment text, p_actor_id bigint)
 RETURNS TABLE(canonical_manifest bytea, canonical_receipt bytea, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_begin_github_delivery_observation('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_attempt_id,p_environment,p_actor_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.begin_github_delivery_observation(bytea,uuid,text,uuid,uuid,text,bigint) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.begin_github_delivery_observation(bytea,uuid,text,uuid,uuid,text,bigint) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_begin_github_delivery_observation(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_attempt_id uuid, p_environment text, p_actor_id bigint)
 RETURNS TABLE(canonical_manifest bytea, canonical_receipt bytea, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_begin_github_delivery_observation('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_attempt_id,p_environment,p_actor_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_begin_github_delivery_observation(bytea,uuid,text,uuid,uuid,text,bigint) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_begin_github_delivery_observation(bytea,uuid,text,uuid,uuid,text,bigint) TO signal_workflow;
-- End port group 17.
-- begin_github_pr_step: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_begin_github_pr_step(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));

END IF;
    PERFORM * FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'prepare' ELSE NULL END);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.port_github_pr_operation_eligible(CASE p_actor WHEN 'weekly_delivery' THEN 'weekly_delivery' ELSE 'owner' END,p_session_hash,p_site_id,p_generation,v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.journal_generation IS NULL
       OR v_operation.expected_tree_sha IS NULL
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR v_operation.step <> p_step THEN RETURN 'permit_denied'; END IF;
    IF v_operation.state IN ('dispatching', 'outcome_unknown') THEN RETURN 'reconcile_required'; END IF;
    IF v_operation.state NOT IN ('planned', 'ready') THEN RETURN 'operation_closed'; END IF;
    UPDATE app.github_pr_operations SET state = 'dispatching', updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, 'dispatching');
    RETURN 'dispatching';
END;
$function$;

ALTER FUNCTION control.port_begin_github_pr_step(text,bytea,uuid,text,uuid,uuid,bigint,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_begin_github_pr_step(text,bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.begin_github_pr_step(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_begin_github_pr_step('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step);
END;
$function$;

REVOKE ALL ON FUNCTION control.begin_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.begin_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_begin_github_pr_step(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_begin_github_pr_step('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_begin_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_begin_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text) TO signal_workflow;
-- End port group 18.
-- bind_github_pr_operation_tree: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_bind_github_pr_operation_tree(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_tree_sha text, p_commit_sha text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));

END IF;
    PERFORM * FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'prepare' ELSE NULL END);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.port_github_pr_operation_eligible(CASE p_actor WHEN 'weekly_delivery' THEN 'weekly_delivery' ELSE 'owner' END,p_session_hash,p_site_id,p_generation,v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.journal_generation IS NULL
       OR p_tree_sha !~ '^[0-9a-f]{40}$' OR p_commit_sha !~ '^[0-9a-f]{40}$' THEN
        RETURN 'tree_binding_denied';
    END IF;
    IF v_operation.expected_tree_sha IS NOT NULL THEN
        IF v_operation.expected_tree_sha = p_tree_sha
           AND v_operation.expected_commit_sha = p_commit_sha THEN RETURN 'bound'; END IF;
        RETURN 'tree_binding_conflict';
    END IF;
    IF v_operation.state <> 'planned' OR v_operation.step <> 'tree' THEN
        RETURN 'tree_binding_denied';
    END IF;
    UPDATE app.github_pr_operations SET expected_tree_sha = p_tree_sha,
        expected_commit_sha = p_commit_sha, updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    RETURN 'bound';
END;
$function$;

ALTER FUNCTION control.port_bind_github_pr_operation_tree(text,bytea,uuid,text,uuid,text,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_bind_github_pr_operation_tree(text,bytea,uuid,text,uuid,text,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.bind_github_pr_operation_tree(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_tree_sha text, p_commit_sha text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_bind_github_pr_operation_tree('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_tree_sha,p_commit_sha);
END;
$function$;

REVOKE ALL ON FUNCTION control.bind_github_pr_operation_tree(bytea,uuid,text,uuid,text,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.bind_github_pr_operation_tree(bytea,uuid,text,uuid,text,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_bind_github_pr_operation_tree(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_tree_sha text, p_commit_sha text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_bind_github_pr_operation_tree('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_tree_sha,p_commit_sha);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_bind_github_pr_operation_tree(bytea,uuid,text,uuid,text,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_bind_github_pr_operation_tree(bytea,uuid,text,uuid,text,text) TO signal_workflow;
-- End port group 19.
-- claim_github_pr_operation: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_claim_github_pr_operation(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid)
 RETURNS TABLE(fence bigint, operation_state text, operation_step text, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM * FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'operation_unavailable'::text;
        RETURN;
    END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' OR v_eligible.tenant_id <> v_operation.tenant_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, v_eligible.outcome;
        RETURN;
    END IF;
    IF v_operation.journal_generation IS NULL OR p_worker_id IS NULL THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'journal_unavailable'::text;
        RETURN;
    END IF;
    IF v_operation.lease_until > transaction_timestamp()
       AND v_operation.lease_holder <> p_worker_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'lease_held'::text;
        RETURN;
    END IF;
    IF v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_until <= transaction_timestamp() THEN
        UPDATE app.github_pr_operations SET lease_holder = p_worker_id,
            lease_fence = lease_fence + 1,
            lease_until = transaction_timestamp() + interval '60 seconds',
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id
         RETURNING lease_fence INTO v_operation.lease_fence;
        INSERT INTO app.github_pr_operation_events
            (tenant_id, site_id, id, operation_id, step, event_kind)
        VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
            v_operation.step, 'lease_claimed');
    END IF;
    RETURN QUERY SELECT v_operation.lease_fence, v_operation.state,
        v_operation.step, 'claimed'::text;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.admit_port_context('weekly_delivery','authority',p_session_hash,p_generation,p_site_id,'read');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'operation_unavailable'::text;
        RETURN;
    END IF;
    IF v_operation.state IN ('dispatching','outcome_unknown') THEN
        SELECT * INTO v_eligible FROM control.weekly_github_pr_reconciliation_eligible(
            p_session_hash,p_site_id,p_generation,v_operation.candidate_revision_id);
    ELSE
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    END IF;
    IF v_eligible.outcome <> 'eligible' OR v_eligible.tenant_id <> v_operation.tenant_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, v_eligible.outcome;
        RETURN;
    END IF;
    IF v_operation.journal_generation IS NULL OR p_worker_id IS NULL THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'journal_unavailable'::text;
        RETURN;
    END IF;
    IF v_operation.lease_until > transaction_timestamp()
       AND v_operation.lease_holder <> p_worker_id THEN
        RETURN QUERY SELECT NULL::bigint, NULL::text, NULL::text, 'lease_held'::text;
        RETURN;
    END IF;
    IF v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_until <= transaction_timestamp() THEN
        UPDATE app.github_pr_operations SET lease_holder = p_worker_id,
            lease_fence = lease_fence + 1,
            lease_until = transaction_timestamp() + interval '60 seconds',
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id
         RETURNING lease_fence INTO v_operation.lease_fence;
        INSERT INTO app.github_pr_operation_events
            (tenant_id, site_id, id, operation_id, step, event_kind)
        VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
            v_operation.step, 'lease_claimed');
    END IF;
    RETURN QUERY SELECT v_operation.lease_fence, v_operation.state,
        v_operation.step, 'claimed'::text;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_claim_github_pr_operation(text,bytea,uuid,text,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_claim_github_pr_operation(text,bytea,uuid,text,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.claim_github_pr_operation(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid)
 RETURNS TABLE(fence bigint, operation_state text, operation_step text, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_claim_github_pr_operation('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.claim_github_pr_operation(bytea,uuid,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.claim_github_pr_operation(bytea,uuid,text,uuid,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_claim_github_pr_operation(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid)
 RETURNS TABLE(fence bigint, operation_state text, operation_step text, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_claim_github_pr_operation('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_claim_github_pr_operation(bytea,uuid,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_claim_github_pr_operation(bytea,uuid,text,uuid,uuid) TO signal_workflow;
-- End port group 20.
-- finish_github_delivery_observation: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_finish_github_delivery_observation(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid, p_canonical_receipt bytea, p_receipt_sha256 bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_operation app.github_pr_operations%%ROWTYPE; v_receipt jsonb; v_manifest jsonb;
        v_existing app.github_delivery_receipts%%ROWTYPE; v_item jsonb; v_egress app.egress_operations%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'observation_unavailable'; END IF;
    SELECT * INTO v_existing FROM app.github_delivery_receipts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND attempt_id = p_attempt_id;
    IF FOUND THEN
        IF v_existing.canonical_receipt = p_canonical_receipt AND v_existing.receipt_sha256 = p_receipt_sha256
          THEN RETURN 'completed'; END IF;
        RETURN 'receipt_conflict';
    END IF;
    IF control.github_delivery_read_permit(p_session_hash, p_site_id, p_generation, p_attempt_id)
        <> 'permitted' THEN RETURN 'observation_stale'; END IF;
    IF p_canonical_receipt IS NULL OR octet_length(p_canonical_receipt) NOT BETWEEN 2 AND 65536
       OR p_receipt_sha256 IS DISTINCT FROM sha256(p_canonical_receipt) THEN
        RETURN 'receipt_invalid';
    END IF;
    SELECT * INTO v_operation FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = v_attempt.operation_id;
    SELECT control.pr_delivery_manifest(v_authority.tenant_id,p_site_id,v_operation.candidate_revision_id,v_operation.authority_kind) INTO v_manifest;
    v_receipt := convert_from(p_canonical_receipt, 'UTF8')::jsonb;
    IF v_receipt->>'schema_version' IS DISTINCT FROM '1'
      OR v_receipt->>'site_id' IS DISTINCT FROM p_site_id::text
      OR v_receipt->>'attempt_id' IS DISTINCT FROM p_attempt_id::text
      OR v_receipt->>'operation_id' IS DISTINCT FROM v_operation.id::text
      OR v_receipt->>'revision_sha256' IS DISTINCT FROM encode(v_operation.revision_sha256, 'hex')
      OR v_receipt->>'environment' IS DISTINCT FROM v_attempt.environment
      OR v_receipt->>'deployment_actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
      OR coalesce(v_receipt->>'outcome', '') NOT IN ('verified', 'not_yet_deployed', 'inconclusive', 'regressed')
      OR coalesce(v_receipt->'provider'->>'stage', '') NOT IN ('pr_opened', 'checks', 'merged', 'deployed')
      OR v_receipt->>'recovery_plan' IS DISTINCT FROM v_manifest->>'recovery_plan'
      OR v_receipt->>'delivery_certified' IS DISTINCT FROM 'false'
      OR jsonb_typeof(v_receipt->'provider_evidence') IS DISTINCT FROM 'array'
      OR jsonb_array_length(v_receipt->'provider_evidence') > 20 THEN RETURN 'receipt_invalid'; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_receipt->'provider_evidence') LOOP
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_item->>'egress_operation_id')::uuid;
        IF NOT FOUND OR v_egress.purpose <> 'connector' OR v_egress.method <> 'GET'
          OR v_egress.egress_profile <> 'github_rest'
          OR v_egress.origin <> 'https://api.github.com' OR v_egress.network_outcome <> 'fetched'
          OR v_egress.request_url IS DISTINCT FROM v_item->>'url'
          OR v_egress.http_status::text IS DISTINCT FROM v_item->>'http_status'
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_item->>'body_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'provider_evidence_invalid'; END IF;
    END LOOP;
    IF v_receipt->'provider'->>'stage' <> 'pr_opened'
       AND jsonb_array_length(v_receipt->'provider_evidence') < 4 THEN RETURN 'provider_evidence_invalid'; END IF;
    IF v_receipt->'provider'->>'stage' = 'deployed' THEN
        IF v_receipt->'provider'->>'merged_tree_sha' IS DISTINCT FROM v_operation.expected_tree_sha
          OR v_receipt->'provider'->'deployment'->>'sha' IS DISTINCT FROM v_receipt->'provider'->>'merged_sha'
          OR v_receipt->'provider'->'deployment'->>'environment' IS DISTINCT FROM v_attempt.environment
          OR v_receipt->'provider'->'deployment'->>'actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
          OR v_receipt->'provider'->'deployment'->>'environment_url' IS NULL
          OR v_receipt->'provider'->'deployment'->>'environment_url' NOT IN
            (v_manifest->'evidence'->>'site_origin', (v_manifest->'evidence'->>'site_origin')||'/',
             v_manifest->'evidence'->>'page_url')
          OR jsonb_array_length(v_receipt->'provider_evidence') < 7 THEN RETURN 'deployment_identity_invalid'; END IF;
    END IF;
    IF v_receipt->>'outcome' = 'verified' THEN
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_receipt->>'live_egress_operation_id')::uuid;
        IF NOT FOUND OR v_receipt->'provider'->>'stage' IS DISTINCT FROM 'deployed'
          OR v_receipt->'live'->>'outcome' IS DISTINCT FROM 'verified'
          OR v_receipt->'live'->>'matched' IS DISTINCT FROM 'true'
          OR v_receipt->'live'->>'fetched_sha256' IS DISTINCT FROM v_manifest->>'result_sha256'
          OR v_egress.purpose <> 'crawl' OR v_egress.method <> 'GET' OR v_egress.credentialed
          OR v_egress.egress_profile <> 'crawl_page'
          OR v_egress.request_url IS DISTINCT FROM v_manifest->'evidence'->>'page_url'
          OR v_egress.state <> 'observed' OR v_egress.network_outcome <> 'fetched' OR v_egress.http_status <> 200
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_receipt->'live'->>'fetched_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'live_evidence_invalid'; END IF;
    END IF;
    INSERT INTO app.github_delivery_receipts (tenant_id, site_id, attempt_id, operation_id,
      canonical_receipt, receipt_sha256) VALUES (v_authority.tenant_id, p_site_id, p_attempt_id,
      v_operation.id, p_canonical_receipt, p_receipt_sha256);
    UPDATE app.github_delivery_attempts SET state = 'completed' WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    RETURN 'completed';
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
        v_operation app.github_pr_operations%%ROWTYPE; v_receipt jsonb; v_manifest jsonb;
        v_existing app.github_delivery_receipts%%ROWTYPE; v_item jsonb; v_egress app.egress_operations%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('attempt_id',p_attempt_id::text));
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_session_hash,p_generation,p_site_id,'read');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'observation_unavailable'; END IF;
    SELECT * INTO v_existing FROM app.github_delivery_receipts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND attempt_id = p_attempt_id;
    IF FOUND THEN
        IF v_existing.canonical_receipt = p_canonical_receipt AND v_existing.receipt_sha256 = p_receipt_sha256
          THEN RETURN 'completed'; END IF;
        RETURN 'receipt_conflict';
    END IF;
    IF control.weekly_github_delivery_read_permit(p_session_hash, p_site_id, p_generation, p_attempt_id)
        <> 'permitted' THEN RETURN 'observation_stale'; END IF;
    IF p_canonical_receipt IS NULL OR octet_length(p_canonical_receipt) NOT BETWEEN 2 AND 65536
       OR p_receipt_sha256 IS DISTINCT FROM sha256(p_canonical_receipt) THEN
        RETURN 'receipt_invalid';
    END IF;
    SELECT * INTO v_operation FROM app.github_pr_operations WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = v_attempt.operation_id;
    SELECT convert_from(canonical_manifest, 'UTF8')::jsonb INTO v_manifest
      FROM app.candidate_recipe_revisions WHERE tenant_id = v_authority.tenant_id
       AND site_id = p_site_id AND id = v_operation.candidate_revision_id;
    v_receipt := convert_from(p_canonical_receipt, 'UTF8')::jsonb;
    IF v_receipt->>'schema_version' IS DISTINCT FROM '1'
      OR v_receipt->>'site_id' IS DISTINCT FROM p_site_id::text
      OR v_receipt->>'attempt_id' IS DISTINCT FROM p_attempt_id::text
      OR v_receipt->>'operation_id' IS DISTINCT FROM v_operation.id::text
      OR v_receipt->>'revision_sha256' IS DISTINCT FROM encode(v_operation.revision_sha256, 'hex')
      OR v_receipt->>'environment' IS DISTINCT FROM v_attempt.environment
      OR v_receipt->>'deployment_actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
      OR coalesce(v_receipt->>'outcome', '') NOT IN ('verified', 'not_yet_deployed', 'inconclusive', 'regressed')
      OR coalesce(v_receipt->'provider'->>'stage', '') NOT IN ('pr_opened', 'checks', 'merged', 'deployed')
      OR v_receipt->>'recovery_plan' IS DISTINCT FROM v_manifest->>'recovery_plan'
      OR v_receipt->>'delivery_certified' IS DISTINCT FROM 'false'
      OR jsonb_typeof(v_receipt->'provider_evidence') IS DISTINCT FROM 'array'
      OR jsonb_array_length(v_receipt->'provider_evidence') > 20 THEN RETURN 'receipt_invalid'; END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_receipt->'provider_evidence') LOOP
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_item->>'egress_operation_id')::uuid;
        IF NOT FOUND OR v_egress.purpose <> 'connector' OR v_egress.method <> 'GET'
          OR v_egress.egress_profile <> 'github_rest'
          OR v_egress.origin <> 'https://api.github.com' OR v_egress.network_outcome <> 'fetched'
          OR v_egress.request_url IS DISTINCT FROM v_item->>'url'
          OR v_egress.http_status::text IS DISTINCT FROM v_item->>'http_status'
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_item->>'body_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'provider_evidence_invalid'; END IF;
    END LOOP;
    IF v_receipt->'provider'->>'stage' <> 'pr_opened'
       AND jsonb_array_length(v_receipt->'provider_evidence') < 4 THEN RETURN 'provider_evidence_invalid'; END IF;
    IF v_receipt->'provider'->>'stage' = 'deployed' THEN
        IF v_receipt->'provider'->>'merged_tree_sha' IS DISTINCT FROM v_operation.expected_tree_sha
          OR v_receipt->'provider'->'deployment'->>'sha' IS DISTINCT FROM v_receipt->'provider'->>'merged_sha'
          OR v_receipt->'provider'->'deployment'->>'environment' IS DISTINCT FROM v_attempt.environment
          OR v_receipt->'provider'->'deployment'->>'actor_id' IS DISTINCT FROM v_attempt.deployment_actor_id::text
          OR v_receipt->'provider'->'deployment'->>'environment_url' IS NULL
          OR v_receipt->'provider'->'deployment'->>'environment_url' NOT IN
            (v_manifest->'evidence'->>'site_origin', v_manifest->'evidence'->>'page_url')
          OR jsonb_array_length(v_receipt->'provider_evidence') < 7 THEN RETURN 'deployment_identity_invalid'; END IF;
    END IF;
    IF v_receipt->>'outcome' = 'verified' THEN
        SELECT * INTO v_egress FROM app.egress_operations WHERE tenant_id = v_authority.tenant_id
          AND site_id = p_site_id AND id = (v_receipt->>'live_egress_operation_id')::uuid;
        IF NOT FOUND OR v_receipt->'provider'->>'stage' IS DISTINCT FROM 'deployed'
          OR v_receipt->'live'->>'outcome' IS DISTINCT FROM 'verified'
          OR v_receipt->'live'->>'matched' IS DISTINCT FROM 'true'
          OR v_receipt->'live'->>'fetched_sha256' IS DISTINCT FROM v_manifest->>'result_sha256'
          OR v_egress.purpose <> 'crawl' OR v_egress.method <> 'GET' OR v_egress.credentialed
          OR v_egress.egress_profile <> 'crawl_page'
          OR v_egress.request_url IS DISTINCT FROM v_manifest->'evidence'->>'page_url'
          OR v_egress.state <> 'observed' OR v_egress.network_outcome <> 'fetched' OR v_egress.http_status <> 200
          OR encode(v_egress.response_sha256, 'hex') IS DISTINCT FROM v_receipt->'live'->>'fetched_sha256'
          OR v_egress.dispatched_at < v_attempt.created_at THEN RETURN 'live_evidence_invalid'; END IF;
    END IF;
    INSERT INTO app.github_delivery_receipts (tenant_id, site_id, attempt_id, operation_id,
      canonical_receipt, receipt_sha256) VALUES (v_authority.tenant_id, p_site_id, p_attempt_id,
      v_operation.id, p_canonical_receipt, p_receipt_sha256);
    UPDATE app.github_delivery_attempts SET state = 'completed' WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    RETURN 'completed';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_finish_github_delivery_observation(text,bytea,uuid,text,uuid,bytea,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_finish_github_delivery_observation(text,bytea,uuid,text,uuid,bytea,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.finish_github_delivery_observation(p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid, p_canonical_receipt bytea, p_receipt_sha256 bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_github_delivery_observation('owner',p_session_hash,p_site_id,p_generation,p_attempt_id,p_canonical_receipt,p_receipt_sha256);
END;
$function$;

REVOKE ALL ON FUNCTION control.finish_github_delivery_observation(bytea,uuid,text,uuid,bytea,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.finish_github_delivery_observation(bytea,uuid,text,uuid,bytea,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_finish_github_delivery_observation(p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid, p_canonical_receipt bytea, p_receipt_sha256 bytea)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_github_delivery_observation('weekly_delivery',p_session_hash,p_site_id,p_generation,p_attempt_id,p_canonical_receipt,p_receipt_sha256);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_finish_github_delivery_observation(bytea,uuid,text,uuid,bytea,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_finish_github_delivery_observation(bytea,uuid,text,uuid,bytea,bytea) TO signal_workflow;
-- End port group 21.
-- finish_github_pr_step: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_finish_github_pr_step(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text, p_result text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL::bigint, p_pr_url text DEFAULT NULL::text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_operation app.github_pr_operations%%ROWTYPE; v_next text;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));

END IF;
    PERFORM * FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'prepare' ELSE NULL END);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    IF control.port_github_pr_dispatch_permit(CASE p_actor WHEN 'weekly_delivery' THEN 'weekly_delivery' ELSE 'owner' END,p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step) <> 'permitted'
       OR octet_length(p_evidence_sha256) <> 32
       OR p_result NOT IN ('completed', 'outcome_unknown', 'blocked') THEN
        RETURN 'permit_denied';
    END IF;
    IF p_result = 'completed' THEN
        v_next := CASE p_step WHEN 'tree' THEN 'commit' WHEN 'commit' THEN 'branch'
            WHEN 'branch' THEN 'pr' WHEN 'pr' THEN 'done' END;
        IF v_next IS NULL OR (p_step = 'pr' AND (p_pr_number < 1 OR p_pr_url !~
            '^https://github[.]com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'))
           OR (p_step <> 'pr' AND (p_pr_number IS NOT NULL OR p_pr_url IS NOT NULL)) THEN
            RETURN 'result_invalid';
        END IF;
        UPDATE app.github_pr_operations SET step = v_next,
            state = CASE WHEN v_next = 'done' THEN 'opened' ELSE 'ready' END,
            pr_number = p_pr_number, pr_url = p_pr_url,
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    ELSE
        UPDATE app.github_pr_operations SET state = p_result,
            updated_at = transaction_timestamp()
         WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    END IF;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, p_result, p_evidence_sha256);
    RETURN p_result;
END;
$function$;

ALTER FUNCTION control.port_finish_github_pr_step(text,bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_finish_github_pr_step(text,bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.finish_github_pr_step(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text, p_result text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL::bigint, p_pr_url text DEFAULT NULL::text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_github_pr_step('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step,p_result,p_evidence_sha256,p_pr_number,p_pr_url);
END;
$function$;

REVOKE ALL ON FUNCTION control.finish_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.finish_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_finish_github_pr_step(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text, p_result text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL::bigint, p_pr_url text DEFAULT NULL::text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_github_pr_step('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step,p_result,p_evidence_sha256,p_pr_number,p_pr_url);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_finish_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_finish_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,text,bytea,bigint,text) TO signal_workflow;
-- End port group 22.
-- github_delivery_read_authority: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_github_delivery_read_authority(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid)
 RETURNS TABLE(outcome text, tenant_id uuid, membership_epoch bigint, site_epoch bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'read' ELSE NULL END);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,control.port_required_role(p_actor),false) OR NOT control.current_github_site_proof(
        v_authority.tenant_id, p_site_id) OR NOT EXISTS (
        SELECT 1 FROM app.github_pr_operations operation
        JOIN app.github_read_bindings binding ON binding.tenant_id = operation.tenant_id
          AND binding.site_id = operation.site_id AND binding.id = operation.binding_id
        JOIN app.github_pr_extensions extension ON extension.tenant_id = operation.tenant_id
          AND extension.site_id = operation.site_id AND extension.id = operation.extension_id
        WHERE operation.tenant_id = v_authority.tenant_id AND operation.site_id = p_site_id
          AND operation.id = p_operation_id AND operation.state = 'opened'
          AND binding.status = 'active' AND binding.repository_id = extension.repository_id
          AND extension.binding_id = binding.id) THEN
        RETURN QUERY SELECT 'observation_unavailable'::text, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'authorized'::text, v_authority.tenant_id,
        v_authority.membership_epoch, v_authority.site_authorization_epoch;
END;
$function$;

ALTER FUNCTION control.port_github_delivery_read_authority(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_github_delivery_read_authority(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.github_delivery_read_authority(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid)
 RETURNS TABLE(outcome text, tenant_id uuid, membership_epoch bigint, site_epoch bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_github_delivery_read_authority('owner',p_session_hash,p_site_id,p_generation,p_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.github_delivery_read_authority(bytea,uuid,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.weekly_github_delivery_read_authority(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid)
 RETURNS TABLE(outcome text, tenant_id uuid, membership_epoch bigint, site_epoch bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_github_delivery_read_authority('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_github_delivery_read_authority(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_github_delivery_read_authority(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 23.
-- github_delivery_read_permit: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_github_delivery_read_permit(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record; v_attempt app.github_delivery_attempts%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('attempt_id',p_attempt_id::text));

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'read' ELSE NULL END);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    SELECT * INTO v_attempt FROM app.github_delivery_attempts WHERE tenant_id = v_authority.tenant_id
      AND site_id = p_site_id AND id = p_attempt_id;
    IF NOT FOUND THEN RETURN 'observation_unavailable'; END IF;
    SELECT * INTO v_authority FROM control.port_github_delivery_read_authority(CASE p_actor WHEN 'weekly_delivery' THEN 'weekly_delivery' ELSE 'owner' END,p_session_hash,p_site_id,p_generation,v_attempt.operation_id);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN RETURN v_authority.outcome; END IF;
    IF v_attempt.state <> 'dispatching' OR v_attempt.expires_at <= transaction_timestamp()
      OR v_attempt.recovery_generation <> p_generation
      OR v_attempt.membership_epoch <> v_authority.membership_epoch
      OR v_attempt.site_epoch <> v_authority.site_epoch THEN RETURN 'observation_stale'; END IF;
    RETURN 'permitted';
END;
$function$;

ALTER FUNCTION control.port_github_delivery_read_permit(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_github_delivery_read_permit(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.github_delivery_read_permit(p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_github_delivery_read_permit('owner',p_session_hash,p_site_id,p_generation,p_attempt_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.github_delivery_read_permit(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.github_delivery_read_permit(bytea,uuid,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_github_delivery_read_permit(p_session_hash bytea, p_site_id uuid, p_generation text, p_attempt_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_github_delivery_read_permit('weekly_delivery',p_session_hash,p_site_id,p_generation,p_attempt_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_github_delivery_read_permit(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_github_delivery_read_permit(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 24.
-- github_pr_dispatch_permit: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_github_pr_dispatch_permit(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_eligible record; v_operation app.github_pr_operations%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));

END IF;
    PERFORM * FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'prepare' ELSE NULL END);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.port_github_pr_operation_eligible(CASE p_actor WHEN 'weekly_delivery' THEN 'weekly_delivery' ELSE 'owner' END,p_session_hash,p_site_id,p_generation,v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.journal_generation IS NULL
       OR v_operation.expected_tree_sha IS NULL
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR v_operation.step <> p_step OR v_operation.state <> 'dispatching' THEN
        RETURN 'permit_denied';
    END IF;
    RETURN 'permitted';
END;
$function$;

ALTER FUNCTION control.port_github_pr_dispatch_permit(text,bytea,uuid,text,uuid,uuid,bigint,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_github_pr_dispatch_permit(text,bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.github_pr_dispatch_permit(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_github_pr_dispatch_permit('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step);
END;
$function$;

REVOKE ALL ON FUNCTION control.github_pr_dispatch_permit(bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.github_pr_dispatch_permit(bytea,uuid,text,uuid,uuid,bigint,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_github_pr_dispatch_permit(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_github_pr_dispatch_permit('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_github_pr_dispatch_permit(bytea,uuid,text,uuid,uuid,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_github_pr_dispatch_permit(bytea,uuid,text,uuid,uuid,bigint,text) TO signal_workflow;
-- End port group 25.
-- github_pr_operation_eligible: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_github_pr_operation_eligible(p_actor text, p_token bytea, p_site uuid, p_generation text, p_revision uuid)
 RETURNS TABLE(outcome text, tenant_id uuid, user_id uuid, membership_epoch bigint, site_epoch bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record; v app.candidate_recipe_revisions%%ROWTYPE; d app.candidate_recipe_review_decisions%%ROWTYPE;
    auth_time timestamptz;
BEGIN
    SELECT * INTO a FROM control.admit_port_context('owner','authority',p_token,p_generation,p_site,NULL);
    IF NOT EXISTS(SELECT 1 FROM app.astro_candidate_impacts i WHERE i.tenant_id=a.tenant_id AND i.site_id=p_site AND i.revision_id=p_revision) THEN
        RETURN QUERY SELECT * FROM control.github_pr_eligible_before_astro(p_token,p_site,p_generation,p_revision); RETURN;
    END IF;
    SELECT s.auth_time INTO auth_time FROM app.sessions s WHERE s.session_token_hash=p_token;
    IF NOT control.port_permission(a.outcome,a.role_key,'owner',true)
      OR a.authentication_level IS DISTINCT FROM 'mfa' OR auth_time IS NULL
      OR auth_time NOT BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp()
      OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN
        RETURN QUERY SELECT 'step_up_required'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    SELECT revision.* INTO v FROM app.candidate_recipe_revisions revision WHERE revision.tenant_id=a.tenant_id AND revision.site_id=p_site AND revision.id=p_revision;
    SELECT decision.* INTO d FROM app.candidate_recipe_review_decisions decision WHERE decision.tenant_id=a.tenant_id AND decision.site_id=p_site AND decision.candidate_revision_id=p_revision;
    IF d.id IS NULL OR d.decision<>'approved' OR d.decision_channel<>'dashboard' OR d.actor_role<>'owner'
      OR d.authentication_level<>'mfa' OR d.revision_sha256<>v.revision_sha256 OR d.recovery_generation<>p_generation
      OR d.membership_epoch<>a.membership_epoch OR d.site_authorization_epoch<>a.site_authorization_epoch
      OR d.decided_at<transaction_timestamp()-interval '1 hour'
      OR v.recovery_generation<>p_generation OR v.membership_epoch<>a.membership_epoch OR v.site_epoch<>a.site_authorization_epoch
      OR NOT EXISTS(SELECT 1 FROM app.astro_review_authentication h WHERE h.tenant_id=a.tenant_id AND h.site_id=p_site
          AND h.revision_id=p_revision AND h.decision_id=d.id)
      OR EXISTS(SELECT 1 FROM app.candidate_recipe_revisions newer WHERE newer.tenant_id=v.tenant_id AND newer.site_id=v.site_id
          AND newer.audit_report_id=v.audit_report_id AND newer.finding_id=v.finding_id AND (newer.sealed_at,newer.id)>(v.sealed_at,v.id)) THEN
        RETURN QUERY SELECT 'decision_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM app.github_pr_extensions e JOIN app.github_read_bindings b
        ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id
        WHERE e.tenant_id=v.tenant_id AND e.site_id=v.site_id AND e.id=v.extension_id AND (e.framework='astro' OR (((e.framework IN ('eleventy','nextjs') AND convert_from(v.canonical_manifest,'UTF8')::jsonb->>'content_adapter'='front_matter') OR (e.framework='nextjs' AND convert_from(v.canonical_manifest,'UTF8')::jsonb->>'content_adapter'='nextjs_metadata'))))
          AND e.status='observed' AND e.coverage='complete' AND e.base_sha=v.base_sha AND e.repository_id=b.repository_id
          AND b.status='active' AND b.base_sha=v.base_sha AND (b.protected OR control.github_unprotected_base_accepted(b,p_generation)))
      OR NOT EXISTS(SELECT 1 FROM app.candidate_build_receipts b WHERE b.tenant_id=v.tenant_id AND b.site_id=v.site_id
          AND b.build_id=v.build_id AND b.exit_class='passed' AND b.base_sha=v.base_sha AND b.patch_sha256=v.patch_sha256)
      OR NOT EXISTS(SELECT 1 FROM control.recipe_releases r WHERE r.id=v.recipe_release_id AND r.content_hash=v.release_content_hash
          AND (SELECT status FROM control.recipe_release_events WHERE release_id=r.id ORDER BY sequence_number DESC LIMIT 1)='REVIEWED'
          AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=r.id)) THEN
        RETURN QUERY SELECT 'binding_build_or_release_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    RETURN QUERY SELECT 'eligible'::text,a.tenant_id,a.user_id,a.membership_epoch,a.site_authorization_epoch;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_token,p_site,p_generation,jsonb_build_object('revision_id',p_revision::text));
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_token,p_generation,p_site,'prepare');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,'workload',false) OR NOT control.current_github_site_proof(
        v_authority.tenant_id, p_site) THEN
        RETURN QUERY SELECT 'authority_denied'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT revision.* INTO v_revision FROM app.candidate_recipe_revisions revision
     WHERE revision.tenant_id = v_authority.tenant_id
       AND revision.site_id = p_site AND revision.id = p_revision;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'revision_unavailable'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT decision.* INTO v_decision FROM app.candidate_recipe_review_decisions decision
     WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site
       AND decision.candidate_revision_id = p_revision;
    IF (v_decision.id IS NULL OR v_decision.decision <> 'approved'
       OR v_decision.revision_sha256 <> v_revision.revision_sha256
       OR v_decision.recovery_generation <> p_generation
       OR v_decision.membership_epoch <> v_authority.membership_epoch
       OR v_decision.site_authorization_epoch <> v_authority.site_authorization_epoch)
       AND NOT control.standing_dispatch_current(p_token,p_site,p_generation) THEN
        RETURN QUERY SELECT 'decision_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_pr_operations operation
        WHERE operation.tenant_id=v_revision.tenant_id AND operation.site_id=p_site
          AND operation.candidate_revision_id=p_revision
          AND (operation.requested_by_user_id IS DISTINCT FROM v_authority.user_id
            OR (operation.authority_kind='owner_inbox'
                AND operation.decision_id IS DISTINCT FROM v_decision.id)
            OR (operation.authority_kind='standing_grant'
                AND NOT control.standing_dispatch_current(p_token,p_site,p_generation)))) THEN
        RETURN QUERY SELECT 'operation_authority_changed'::text,NULL::uuid,NULL::uuid,
            NULL::bigint,NULL::bigint;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.candidate_recipe_revisions newer
       WHERE newer.tenant_id = v_revision.tenant_id AND newer.site_id = v_revision.site_id
         AND newer.audit_report_id = v_revision.audit_report_id
         AND newer.finding_id = v_revision.finding_id
         AND (newer.sealed_at, newer.id) > (v_revision.sealed_at, v_revision.id)) THEN
        RETURN QUERY SELECT 'revision_superseded'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.github_pr_extensions ext
       JOIN app.github_read_bindings binding ON binding.tenant_id = ext.tenant_id
          AND binding.site_id = ext.site_id AND binding.id = ext.binding_id
       WHERE ext.tenant_id = v_revision.tenant_id AND ext.site_id = v_revision.site_id
         AND ext.id = v_revision.extension_id AND ext.status = 'observed'
         AND ext.base_sha = v_revision.base_sha AND ext.coverage = 'complete'
         AND ext.repository_id = binding.repository_id AND ext.framework = 'eleventy'
         AND ext.content_format = 'html' AND binding.status = 'active'
         AND (binding.protected OR (control.github_unprotected_base_accepted(binding,p_generation)
             AND v_decision.decision='approved' AND v_decision.decision_channel='dashboard'))
         AND binding.base_sha = v_revision.base_sha) THEN
        RETURN QUERY SELECT 'binding_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.candidate_build_receipts build
       WHERE build.tenant_id = v_revision.tenant_id AND build.site_id = v_revision.site_id
         AND build.build_id = v_revision.build_id AND build.exit_class = 'passed'
         AND build.base_sha = v_revision.base_sha
         AND build.patch_sha256 = v_revision.patch_sha256) THEN
        RETURN QUERY SELECT 'build_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM control.recipe_releases release
       WHERE release.id = v_revision.recipe_release_id
         AND release.content_hash = v_revision.release_content_hash
         AND (SELECT event.status FROM control.recipe_release_events event
              WHERE event.release_id = release.id
              ORDER BY event.sequence_number DESC LIMIT 1) = 'REVIEWED'
         AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones denial
              WHERE denial.target_kind = 'recipe_release'
                AND denial.target_id = release.id)) THEN
        RETURN QUERY SELECT 'release_inactive'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'eligible'::text, v_authority.tenant_id, v_authority.user_id,
        v_authority.membership_epoch, v_authority.site_authorization_epoch;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_github_pr_operation_eligible(text,bytea,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_github_pr_operation_eligible(text,bytea,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.github_pr_operation_eligible(p_token bytea, p_site uuid, p_generation text, p_revision uuid)
 RETURNS TABLE(outcome text, tenant_id uuid, user_id uuid, membership_epoch bigint, site_epoch bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_github_pr_operation_eligible('owner',p_token,p_site,p_generation,p_revision);
END;
$function$;

REVOKE ALL ON FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_github_pr_operation_eligible(p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid)
 RETURNS TABLE(outcome text, tenant_id uuid, user_id uuid, membership_epoch bigint, site_epoch bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_github_pr_operation_eligible('weekly_delivery',p_session_hash,p_site_id,p_generation,p_revision_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_github_pr_operation_eligible(bytea,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_github_pr_operation_eligible(bytea,uuid,text,uuid) TO signal_workflow;
-- End port group 26.
-- load_candidate_recipe_evidence: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_load_candidate_recipe_evidence(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_report_id uuid, p_finding_id uuid)
 RETURNS TABLE(evidence jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record; v_report app.crawl_audit_reports%%ROWTYPE;
        v_finding jsonb; v_page app.crawl_page_records%%ROWTYPE;
        v_source_id uuid; v_parent_url_id uuid; v_page_url text;
        v_origin text; v_images jsonb;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('report_id',p_report_id::text,'finding_id',p_finding_id::text));

END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'prepare' ELSE NULL END);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::jsonb, v_authority.outcome; RETURN;
    END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,control.port_required_role(p_actor),false) THEN
        RETURN QUERY SELECT NULL::jsonb, 'permission_denied'::text; RETURN;
    END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::jsonb, 'site_not_verified'::text; RETURN;
    END IF;
    SELECT report.* INTO v_report FROM app.crawl_audit_reports AS report
      JOIN app.crawl_manifests AS manifest
        ON manifest.tenant_id = report.tenant_id AND manifest.site_id = report.site_id
       AND manifest.id = report.manifest_id
     WHERE report.tenant_id = v_authority.tenant_id AND report.site_id = p_site_id
       AND report.id = p_report_id AND report.detector_release_id =
           '0fc2a362-f51d-4b75-9bbf-22d456f29b40'::uuid
       AND report.manifest_sha256 = manifest.manifest_sha256
     FOR SHARE OF report, manifest;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::jsonb, 'report_unavailable'::text; RETURN;
    END IF;
    SELECT item.value INTO v_finding
      FROM jsonb_array_elements(v_report.findings) AS item(value)
     WHERE item.value->>'id' = p_finding_id::text;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::jsonb, 'finding_unavailable'::text; RETURN;
    END IF;
    v_source_id := (v_finding->>'source_id')::uuid;
    IF v_finding->>'source_kind' = 'page' THEN
        SELECT page.* INTO v_page FROM app.crawl_page_records AS page
         WHERE page.tenant_id = v_authority.tenant_id AND page.site_id = p_site_id
           AND page.crawl_run_id = v_report.crawl_run_id AND page.id = v_source_id;
    ELSIF v_finding->>'source_kind' = 'settlement'
          AND v_finding->>'key' = 'links.internal.not_found' THEN
        SELECT frontier.discovered_from_url_id INTO v_parent_url_id
          FROM app.crawl_frontier_settlements AS settlement
          JOIN app.crawl_frontier AS frontier
            ON frontier.tenant_id = settlement.tenant_id
           AND frontier.site_id = settlement.site_id
           AND frontier.id = settlement.frontier_id
         WHERE settlement.tenant_id = v_authority.tenant_id
           AND settlement.site_id = p_site_id
           AND settlement.crawl_run_id = v_report.crawl_run_id
           AND settlement.id = v_source_id AND settlement.terminal_state = 'http_error';
        SELECT page.* INTO v_page FROM app.crawl_page_records AS page
         WHERE page.tenant_id = v_authority.tenant_id AND page.site_id = p_site_id
           AND page.crawl_run_id = v_report.crawl_run_id
           AND page.url_id = v_parent_url_id
           AND page.internal_links ? (v_finding->>'resource_locator');
    END IF;
    IF v_page.id IS NULL THEN
        RETURN QUERY SELECT NULL::jsonb, 'source_unavailable'::text; RETURN;
    END IF;
    SELECT url.fetch_url INTO v_page_url FROM app.urls AS url
     WHERE url.tenant_id = v_authority.tenant_id AND url.site_id = p_site_id
       AND url.id = v_page.url_id;
    SELECT site.primary_origin INTO v_origin FROM app.sites AS site
     WHERE site.tenant_id = v_authority.tenant_id AND site.id = p_site_id;
    SELECT images.missing_alt_images INTO v_images
      FROM app.crawl_page_image_evidence AS images
     WHERE images.tenant_id = v_authority.tenant_id AND images.site_id = p_site_id
       AND images.page_record_id = v_page.id
       AND images.body_sha256 = v_page.body_sha256;
    IF v_page_url IS NULL OR v_origin IS NULL THEN
        RETURN QUERY SELECT NULL::jsonb, 'source_unavailable'::text; RETURN;
    END IF;
    evidence := jsonb_build_object(
        'report_id', v_report.id::text,
        'manifest_id', v_report.manifest_id::text,
        'manifest_sha256', encode(v_report.manifest_sha256, 'hex'),
        'detector_release_id', v_report.detector_release_id::text,
        'finding', v_finding,
        'site_origin', v_origin,
        'page_id', v_page.id::text,
        'page_url', v_page_url,
        'page_body_sha256', encode(v_page.body_sha256, 'hex'),
        'page_title', v_page.title,
        'page_description', v_page.meta_description,
        'page_canonical', v_page.canonical_url,
        'page_headings', v_page.headings,
        'page_parse_error_count', v_page.parse_error_count,
        'page_output_truncated', v_page.output_truncated,
        'page_internal_links', v_page.internal_links,
        'missing_alt_images', v_images
    );
    IF octet_length(evidence::text) > 131072 THEN
        RETURN QUERY SELECT NULL::jsonb, 'source_oversized'::text; RETURN;
    END IF;
    outcome := 'found';
    RETURN NEXT;
END;
$function$;

ALTER FUNCTION control.port_load_candidate_recipe_evidence(text,bytea,uuid,text,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_load_candidate_recipe_evidence(text,bytea,uuid,text,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.load_candidate_recipe_evidence(p_session_hash bytea, p_site_id uuid, p_generation text, p_report_id uuid, p_finding_id uuid)
 RETURNS TABLE(evidence jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_load_candidate_recipe_evidence('owner',p_session_hash,p_site_id,p_generation,p_report_id,p_finding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.load_candidate_recipe_evidence(bytea,uuid,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.load_candidate_recipe_evidence(bytea,uuid,text,uuid,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_load_candidate_recipe_evidence(p_session_hash bytea, p_site_id uuid, p_generation text, p_report_id uuid, p_finding_id uuid)
 RETURNS TABLE(evidence jsonb, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_load_candidate_recipe_evidence('weekly_delivery',p_session_hash,p_site_id,p_generation,p_report_id,p_finding_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_load_candidate_recipe_evidence(bytea,uuid,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_load_candidate_recipe_evidence(bytea,uuid,text,uuid,uuid) TO signal_workflow;
-- End port group 27.
-- prepare_github_pr_operation: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_prepare_github_pr_operation(p_actor text, p_hash bytea, p_site uuid, p_generation text, p_revision uuid, p_digest bytea, p_operation uuid, p_intent bytea)
 RETURNS TABLE(operation_id uuid, operation_state text, operation_step text, canonical_manifest bytea, operation_created_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record; c app.content_candidates%%ROWTYPE; d app.content_delivery_decisions%%ROWTYPE;
        o app.github_pr_operations%%ROWTYPE; e app.github_pr_extensions%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.admit_port_context('owner','brain',p_hash,p_generation,p_site,NULL);
    SELECT * INTO c FROM app.content_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_revision;
    IF NOT FOUND THEN RETURN QUERY SELECT * FROM control.prepare_technical_github_pr_operation(p_hash,p_site,p_generation,p_revision,p_digest,p_operation,p_intent); RETURN; END IF;
    SELECT * INTO a FROM control.github_pr_operation_eligible(p_hash,p_site,p_generation,p_revision);
    IF a.outcome<>'eligible' OR c.revision_sha256 IS DISTINCT FROM p_digest OR p_operation IS NULL
      OR octet_length(p_intent) IS DISTINCT FROM 32 THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,NULL::text,NULL::bytea,NULL::timestamptz,
          CASE WHEN a.outcome<>'eligible' THEN a.outcome ELSE 'revision_stale' END; RETURN;
    END IF;
    SELECT * INTO o FROM app.github_pr_operations WHERE tenant_id=a.tenant_id AND site_id=p_site AND (id=p_operation OR candidate_revision_id=p_revision);
    IF FOUND THEN
        IF o.id<>p_operation OR o.candidate_revision_id<>p_revision OR o.revision_sha256<>p_digest OR o.intent_sha256<>p_intent OR o.authority_kind<>'owner_editorial' THEN
            RETURN QUERY SELECT NULL::uuid,NULL::text,NULL::text,NULL::bytea,NULL::timestamptz,'operation_conflict'::text; RETURN;
        END IF;
        RETURN QUERY SELECT o.id,o.state,o.step,c.canonical,o.created_at,'prepared'::text; RETURN;
    END IF;
    IF EXISTS(SELECT 1 FROM app.github_pr_operations other WHERE other.tenant_id=a.tenant_id AND other.site_id=p_site AND other.state NOT IN ('opened','blocked')) THEN
        RETURN QUERY SELECT NULL::uuid,NULL::text,NULL::text,NULL::bytea,NULL::timestamptz,'resource_conflict'::text; RETURN;
    END IF;
    SELECT * INTO d FROM app.content_delivery_decisions WHERE tenant_id=a.tenant_id AND site_id=p_site AND candidate_id=p_revision;
    SELECT * INTO e FROM app.github_pr_extensions WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=c.extension_id;
    INSERT INTO app.github_pr_operations(tenant_id,site_id,id,candidate_revision_id,revision_sha256,intent_sha256,
      extension_id,binding_id,requested_by_user_id,recovery_generation,membership_epoch,site_epoch,base_sha,branch_name,step,state,
      authority_kind,editorial_decision_id,decision_channel)
    VALUES(a.tenant_id,p_site,p_operation,p_revision,p_digest,p_intent,c.extension_id,e.binding_id,d.owner_user_id,
      p_generation,d.membership_epoch,d.site_epoch,c.manifest->>'base_sha','signal/'||replace(p_operation::text,'-',''),'tree','planned',
      'owner_editorial',d.id,'dashboard');
    INSERT INTO app.github_pr_operation_events(tenant_id,site_id,id,operation_id,step,event_kind)
      VALUES(a.tenant_id,p_site,gen_random_uuid(),p_operation,'tree','planned');
    RETURN QUERY SELECT p_operation,'planned'::text,'tree'::text,c.canonical,transaction_timestamp(),'prepared'::text;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_eligible record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
        v_extension app.github_pr_extensions%%ROWTYPE;
        v_existing app.github_pr_operations%%ROWTYPE;
        v_branch text;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_hash,p_site,p_generation,jsonb_build_object('revision_id',p_revision::text,'operation_id',p_operation::text));
    IF p_operation IS NULL OR p_revision IS NULL
       OR octet_length(p_digest) <> 32 OR octet_length(p_intent) <> 32 THEN
        RAISE EXCEPTION 'invalid_pr_operation_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_operation_eligible(
        p_hash, p_site, p_generation, p_revision);
    IF v_eligible.outcome <> 'eligible' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, v_eligible.outcome;
        RETURN;
    END IF;
    SELECT * INTO v_revision FROM app.candidate_recipe_revisions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site AND id = p_revision;
    IF v_revision.revision_sha256 <> p_digest THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, 'revision_stale'::text;
        RETURN;
    END IF;
    SELECT * INTO v_decision FROM app.candidate_recipe_review_decisions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site
       AND candidate_revision_id = p_revision;
    SELECT * INTO v_extension FROM app.github_pr_extensions
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site
       AND id = v_revision.extension_id;
    v_branch := 'signal/' || replace(p_operation::text, '-', '');
    SELECT * INTO v_existing FROM app.github_pr_operations
     WHERE tenant_id = v_eligible.tenant_id AND site_id = p_site
       AND (id = p_operation OR candidate_revision_id = p_revision);
    IF FOUND THEN
        IF v_existing.id <> p_operation OR v_existing.candidate_revision_id <> p_revision
           OR v_existing.revision_sha256 <> p_digest
           OR v_existing.intent_sha256 <> p_intent
           OR v_existing.branch_name <> v_branch THEN
            RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
                NULL::timestamptz, 'operation_conflict'::text;
            RETURN;
        END IF;
        RETURN QUERY SELECT v_existing.id, v_existing.state, v_existing.step,
            v_revision.canonical_manifest, v_existing.created_at, 'prepared'::text;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_pr_operations other
       WHERE other.tenant_id = v_eligible.tenant_id AND other.site_id = p_site
         AND other.state NOT IN ('opened', 'blocked')) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::bytea,
            NULL::timestamptz, 'resource_conflict'::text;
        RETURN;
    END IF;
    INSERT INTO app.github_pr_operations (
        tenant_id, site_id, id, candidate_revision_id, revision_sha256, intent_sha256,
        decision_id, extension_id, binding_id, recipe_release_id, requested_by_user_id,
        recovery_generation, membership_epoch, site_epoch, base_sha,
        branch_name, step, state, authority_kind, standing_dispatch_id
    ) VALUES (v_eligible.tenant_id, p_site, p_operation, p_revision,
        p_digest, p_intent, v_decision.id, v_extension.id,
        v_extension.binding_id, v_revision.recipe_release_id, v_eligible.user_id,
        p_generation, v_eligible.membership_epoch, v_eligible.site_epoch,
        v_revision.base_sha, v_branch, 'tree', 'planned',
        CASE WHEN v_decision.id IS NULL THEN 'standing_grant' ELSE 'owner_inbox' END,
        CASE WHEN v_decision.id IS NULL THEN (SELECT a.id FROM app.standing_dispatch_authorizations a
            JOIN app.weekly_delivery_workloads w ON w.tenant_id=a.tenant_id AND w.site_id=a.site_id AND w.id=a.workload_id
            WHERE w.handle_hash=p_hash AND w.site_id=p_site) ELSE NULL END);
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind)
    VALUES (v_eligible.tenant_id, p_site, gen_random_uuid(), p_operation, 'tree', 'planned');
    RETURN QUERY SELECT p_operation, 'planned'::text, 'tree'::text,
        v_revision.canonical_manifest, transaction_timestamp(), 'prepared'::text;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_prepare_github_pr_operation(text,bytea,uuid,text,uuid,bytea,uuid,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_prepare_github_pr_operation(text,bytea,uuid,text,uuid,bytea,uuid,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.prepare_github_pr_operation(p_hash bytea, p_site uuid, p_generation text, p_revision uuid, p_digest bytea, p_operation uuid, p_intent bytea)
 RETURNS TABLE(operation_id uuid, operation_state text, operation_step text, canonical_manifest bytea, operation_created_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_prepare_github_pr_operation('owner',p_hash,p_site,p_generation,p_revision,p_digest,p_operation,p_intent);
END;
$function$;

REVOKE ALL ON FUNCTION control.prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_prepare_github_pr_operation(p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid, p_revision_sha256 bytea, p_operation_id uuid, p_intent_sha256 bytea)
 RETURNS TABLE(operation_id uuid, operation_state text, operation_step text, canonical_manifest bytea, operation_created_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_prepare_github_pr_operation('weekly_delivery',p_session_hash,p_site_id,p_generation,p_revision_id,p_revision_sha256,p_operation_id,p_intent_sha256);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) TO signal_workflow;
-- End port group 28.
-- read_authenticated_candidate_recipe_inbox: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_read_authenticated_candidate_recipe_inbox(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(revision_id uuid, revision_sha256 text, canonical_manifest jsonb, sealed_at timestamp with time zone, recipe_release_id uuid, release_content_hash text, base_sha text, patch_sha256 text, review_status text, decision_id uuid, decision text, decided_by_user_id uuid, decision_channel text, decided_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
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
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object());
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_session_hash,p_generation,p_site_id,'read');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
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
       AND revision.id=(SELECT job.revision_id FROM app.weekly_delivery_workloads job WHERE job.handle_hash=p_session_hash)
     ORDER BY revision.sealed_at DESC, revision.id DESC
     LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::jsonb, NULL::timestamptz,
            NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::uuid,
            NULL::text, NULL::uuid, NULL::text, NULL::timestamptz, 'not_found'::text;
    END IF;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_read_authenticated_candidate_recipe_inbox(text,bytea,uuid,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_authenticated_candidate_recipe_inbox(text,bytea,uuid,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_authenticated_candidate_recipe_inbox(p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(revision_id uuid, revision_sha256 text, canonical_manifest jsonb, sealed_at timestamp with time zone, recipe_release_id uuid, release_content_hash text, base_sha text, patch_sha256 text, review_status text, decision_id uuid, decision text, decided_by_user_id uuid, decision_channel text, decided_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_authenticated_candidate_recipe_inbox('owner',p_session_hash,p_site_id,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_authenticated_candidate_recipe_inbox(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_authenticated_candidate_recipe_inbox(bytea,uuid,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_read_authenticated_candidate_recipe_inbox(p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(revision_id uuid, revision_sha256 text, canonical_manifest jsonb, sealed_at timestamp with time zone, recipe_release_id uuid, release_content_hash text, base_sha text, patch_sha256 text, review_status text, decision_id uuid, decision text, decided_by_user_id uuid, decision_channel text, decided_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_authenticated_candidate_recipe_inbox('weekly_delivery',p_session_hash,p_site_id,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_authenticated_candidate_recipe_inbox(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_authenticated_candidate_recipe_inbox(bytea,uuid,text) TO signal_workflow;
-- End port group 29.
-- read_authenticated_github_delivery_observations: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_read_authenticated_github_delivery_observations(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(attempt_id uuid, operation_id uuid, canonical_receipt bytea, receipt_sha256 text, attempt_state text, observed_at timestamp with time zone, next_observe_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::bytea, NULL::text, NULL::text,
          NULL::timestamptz, NULL::timestamptz, v_authority.outcome; RETURN;
    END IF;
    RETURN QUERY SELECT DISTINCT ON (attempt.operation_id) attempt.id, attempt.operation_id,
      receipt.canonical_receipt, encode(receipt.receipt_sha256, 'hex'),
      CASE WHEN attempt.state = 'dispatching' AND attempt.expires_at <= transaction_timestamp()
        THEN 'outcome_unknown' ELSE attempt.state END,
      coalesce(receipt.recorded_at, attempt.created_at), attempt.next_observe_at, 'found'::text
      FROM app.github_delivery_attempts attempt LEFT JOIN app.github_delivery_receipts receipt
        ON receipt.tenant_id = attempt.tenant_id AND receipt.site_id = attempt.site_id
          AND receipt.attempt_id = attempt.id
     WHERE attempt.tenant_id = v_authority.tenant_id AND attempt.site_id = p_site_id
     ORDER BY attempt.operation_id, attempt.sequence_number DESC LIMIT 50;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::bytea, NULL::text,
       NULL::text, NULL::timestamptz, NULL::timestamptz, 'not_found'::text; END IF;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object());
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_session_hash,p_generation,p_site_id,'read');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::bytea, NULL::text, NULL::text,
          NULL::timestamptz, NULL::timestamptz, v_authority.outcome; RETURN;
    END IF;
    RETURN QUERY SELECT DISTINCT ON (attempt.operation_id) attempt.id, attempt.operation_id,
      receipt.canonical_receipt, encode(receipt.receipt_sha256, 'hex'),
      CASE WHEN attempt.state = 'dispatching' AND attempt.expires_at <= transaction_timestamp()
        THEN 'outcome_unknown' ELSE attempt.state END,
      coalesce(receipt.recorded_at, attempt.created_at), attempt.next_observe_at, 'found'::text
      FROM app.github_delivery_attempts attempt LEFT JOIN app.github_delivery_receipts receipt
        ON receipt.tenant_id = attempt.tenant_id AND receipt.site_id = attempt.site_id
          AND receipt.attempt_id = attempt.id
     WHERE attempt.tenant_id = v_authority.tenant_id AND attempt.site_id = p_site_id AND attempt.operation_id=(SELECT job.operation_id FROM app.weekly_delivery_workloads job WHERE job.handle_hash=p_session_hash)
     ORDER BY attempt.operation_id, attempt.sequence_number DESC LIMIT 50;
    IF NOT FOUND THEN RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::bytea, NULL::text,
       NULL::text, NULL::timestamptz, NULL::timestamptz, 'not_found'::text; END IF;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_read_authenticated_github_delivery_observations(text,bytea,uuid,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_authenticated_github_delivery_observations(text,bytea,uuid,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_authenticated_github_delivery_observations(p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(attempt_id uuid, operation_id uuid, canonical_receipt bytea, receipt_sha256 text, attempt_state text, observed_at timestamp with time zone, next_observe_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_authenticated_github_delivery_observations('owner',p_session_hash,p_site_id,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_authenticated_github_delivery_observations(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_authenticated_github_delivery_observations(bytea,uuid,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_read_authenticated_github_delivery_observations(p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(attempt_id uuid, operation_id uuid, canonical_receipt bytea, receipt_sha256 text, attempt_state text, observed_at timestamp with time zone, next_observe_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_authenticated_github_delivery_observations('weekly_delivery',p_session_hash,p_site_id,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_authenticated_github_delivery_observations(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_authenticated_github_delivery_observations(bytea,uuid,text) TO signal_workflow;
-- End port group 30.
-- read_authenticated_github_pr_operations: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_read_authenticated_github_pr_operations(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(operation_id uuid, candidate_revision_id uuid, revision_sha256 text, branch_name text, base_sha text, head_sha text, expected_tree_sha text, state text, step text, pr_number bigint, pr_url text, journal_generation uuid, journal_position bigint, journal_body_hash text, created_at timestamp with time zone, updated_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;
    RETURN QUERY SELECT op.id, op.candidate_revision_id, encode(op.revision_sha256, 'hex'),
        op.branch_name, op.base_sha, op.expected_commit_sha, op.expected_tree_sha,
        op.state, op.step, op.pr_number, op.pr_url, op.journal_generation,
        op.journal_position, encode(op.journal_body_hash, 'hex'),
        op.created_at, op.updated_at, 'found'::text
      FROM app.github_pr_operations op
     WHERE op.tenant_id = v_authority.tenant_id AND op.site_id = p_site_id
     ORDER BY op.created_at DESC, op.id DESC LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, 'not_found'::text;
    END IF;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_authority record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object());
    SELECT * INTO v_authority FROM control.admit_port_context('weekly_delivery','authority',p_session_hash,p_generation,p_site_id,'read');
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, v_authority.outcome;
        RETURN;
    END IF;
    RETURN QUERY SELECT op.id, op.candidate_revision_id, encode(op.revision_sha256, 'hex'),
        op.branch_name, op.base_sha, op.expected_commit_sha, op.expected_tree_sha,
        op.state, op.step, op.pr_number, op.pr_url, op.journal_generation,
        op.journal_position, encode(op.journal_body_hash, 'hex'),
        op.created_at, op.updated_at, 'found'::text
      FROM app.github_pr_operations op
     WHERE op.tenant_id = v_authority.tenant_id AND op.site_id = p_site_id
       AND op.id=(SELECT job.operation_id FROM app.weekly_delivery_workloads job WHERE job.handle_hash=p_session_hash)
     ORDER BY op.created_at DESC, op.id DESC LIMIT 50;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, NULL::text,
            NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
            NULL::bigint, NULL::text, NULL::uuid, NULL::bigint, NULL::text,
            NULL::timestamptz, NULL::timestamptz, 'not_found'::text;
    END IF;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_read_authenticated_github_pr_operations(text,bytea,uuid,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_authenticated_github_pr_operations(text,bytea,uuid,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_authenticated_github_pr_operations(p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(operation_id uuid, candidate_revision_id uuid, revision_sha256 text, branch_name text, base_sha text, head_sha text, expected_tree_sha text, state text, step text, pr_number bigint, pr_url text, journal_generation uuid, journal_position bigint, journal_body_hash text, created_at timestamp with time zone, updated_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_authenticated_github_pr_operations('owner',p_session_hash,p_site_id,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_authenticated_github_pr_operations(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_authenticated_github_pr_operations(bytea,uuid,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_read_authenticated_github_pr_operations(p_session_hash bytea, p_site_id uuid, p_generation text)
 RETURNS TABLE(operation_id uuid, candidate_revision_id uuid, revision_sha256 text, branch_name text, base_sha text, head_sha text, expected_tree_sha text, state text, step text, pr_number bigint, pr_url text, journal_generation uuid, journal_position bigint, journal_body_hash text, created_at timestamp with time zone, updated_at timestamp with time zone, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_authenticated_github_pr_operations('weekly_delivery',p_session_hash,p_site_id,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_authenticated_github_pr_operations(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_authenticated_github_pr_operations(bytea,uuid,text) TO signal_workflow;
-- End port group 31.
-- read_github_pr_operation_authorities: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_read_github_pr_operation_authorities(p_actor text, p_session bytea, p_site uuid, p_generation text)
 RETURNS TABLE(operation_id uuid, authority_kind text, authority_id uuid, owner_user_id uuid, decision_channel text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.admit_port_context('owner','authority',p_session,p_generation,p_site,NULL);
    IF NOT control.port_permission(a.outcome,NULL,NULL,false) THEN RETURN; END IF;
    RETURN QUERY SELECT o.id,o.authority_kind,coalesce(o.decision_id,o.standing_dispatch_id,o.editorial_decision_id),
      o.requested_by_user_id,o.decision_channel FROM app.github_pr_operations o
      WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site ORDER BY o.created_at DESC,o.id DESC LIMIT 50;
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.admit_port_context('weekly_delivery','authority',p_session,p_generation,p_site,'read');
    IF NOT control.port_permission(a.outcome,NULL,NULL,false) THEN RETURN; END IF;
    RETURN QUERY SELECT o.id,o.authority_kind,coalesce(o.decision_id,o.standing_dispatch_id),
        coalesce(d.decided_by_user_id,g.owner_user_id),o.decision_channel
    FROM app.github_pr_operations o JOIN app.weekly_delivery_workloads w
        ON w.tenant_id=o.tenant_id AND w.site_id=o.site_id AND w.operation_id=o.id
    LEFT JOIN app.candidate_recipe_review_decisions d
        ON d.tenant_id=o.tenant_id AND d.site_id=o.site_id AND d.id=o.decision_id
    LEFT JOIN app.standing_dispatch_authorizations s ON s.tenant_id=o.tenant_id
        AND s.site_id=o.site_id AND s.id=o.standing_dispatch_id
    LEFT JOIN app.standing_authorizations g ON g.tenant_id=s.tenant_id AND g.site_id=s.site_id AND g.id=s.grant_id
    WHERE w.handle_hash=p_session AND w.site_id=p_site;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_read_github_pr_operation_authorities(text,bytea,uuid,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_github_pr_operation_authorities(text,bytea,uuid,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_github_pr_operation_authorities(p_session bytea, p_site uuid, p_generation text)
 RETURNS TABLE(operation_id uuid, authority_kind text, authority_id uuid, owner_user_id uuid, decision_channel text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_pr_operation_authorities('owner',p_session,p_site,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_github_pr_operation_authorities(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_github_pr_operation_authorities(bytea,uuid,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_read_github_pr_operation_authorities(p_handle bytea, p_site uuid, p_generation text)
 RETURNS TABLE(operation_id uuid, authority_kind text, authority_id uuid, owner_user_id uuid, decision_channel text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_github_pr_operation_authorities('weekly_delivery',p_handle,p_site,p_generation);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_read_github_pr_operation_authorities(bytea,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_read_github_pr_operation_authorities(bytea,uuid,text) TO signal_workflow;
-- End port group 32.
-- reconcile_github_pr_step: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_reconcile_github_pr_step(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL::bigint, p_pr_url text DEFAULT NULL::text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_operation app.github_pr_operations%%ROWTYPE; v_eligible record; v_next text;
BEGIN
    PERFORM * FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.github_pr_operation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id OR v_operation.step <> p_step
       OR v_operation.state NOT IN ('dispatching', 'outcome_unknown')
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR octet_length(p_evidence_sha256) <> 32 THEN RETURN 'permit_denied'; END IF;
    v_next := CASE p_step WHEN 'tree' THEN 'commit' WHEN 'commit' THEN 'branch'
        WHEN 'branch' THEN 'pr' WHEN 'pr' THEN 'done' END;
    IF v_next IS NULL OR (p_step = 'pr' AND (p_pr_number < 1 OR p_pr_url !~
        '^https://github[.]com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'))
       OR (p_step <> 'pr' AND (p_pr_number IS NOT NULL OR p_pr_url IS NOT NULL)) THEN
        RETURN 'result_invalid';
    END IF;
    UPDATE app.github_pr_operations SET step = v_next,
        state = CASE WHEN v_next = 'done' THEN 'opened' ELSE 'ready' END,
        pr_number = p_pr_number, pr_url = p_pr_url, updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, 'reconciled', p_evidence_sha256);
    RETURN 'reconciled';
END;
ELSIF p_actor='weekly_delivery' THEN
DECLARE v_operation app.github_pr_operations%%ROWTYPE; v_eligible record; v_next text;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('operation_id',p_operation_id::text));
    PERFORM * FROM control.admit_port_context('weekly_delivery','authority',p_session_hash,p_generation,p_site_id,'read');
    SELECT * INTO v_operation FROM app.github_pr_operations
     WHERE site_id = p_site_id AND id = p_operation_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'operation_unavailable'; END IF;
    SELECT * INTO v_eligible FROM control.weekly_github_pr_reconciliation_eligible(
        p_session_hash, p_site_id, p_generation, v_operation.candidate_revision_id);
    IF v_eligible.outcome <> 'eligible' THEN RETURN v_eligible.outcome; END IF;
    IF v_eligible.tenant_id <> v_operation.tenant_id OR v_operation.step <> p_step
       OR v_operation.state NOT IN ('dispatching', 'outcome_unknown')
       OR v_operation.recovery_generation <> p_generation
       OR v_operation.membership_epoch <> v_eligible.membership_epoch
       OR v_operation.site_epoch <> v_eligible.site_epoch
       OR v_operation.lease_holder IS DISTINCT FROM p_worker_id
       OR v_operation.lease_fence <> p_fence
       OR v_operation.lease_until <= transaction_timestamp()
       OR octet_length(p_evidence_sha256) <> 32 THEN RETURN 'permit_denied'; END IF;
    v_next := CASE p_step WHEN 'tree' THEN 'commit' WHEN 'commit' THEN 'branch'
        WHEN 'branch' THEN 'pr' WHEN 'pr' THEN 'done' END;
    IF v_next IS NULL OR (p_step = 'pr' AND (p_pr_number < 1 OR p_pr_url !~
        '^https://github[.]com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'))
       OR (p_step <> 'pr' AND (p_pr_number IS NOT NULL OR p_pr_url IS NOT NULL)) THEN
        RETURN 'result_invalid';
    END IF;
    UPDATE app.github_pr_operations SET step = v_next,
        state = CASE WHEN v_next = 'done' THEN 'opened' ELSE 'ready' END,
        pr_number = p_pr_number, pr_url = p_pr_url, updated_at = transaction_timestamp()
     WHERE tenant_id = v_operation.tenant_id AND site_id = p_site_id AND id = p_operation_id;
    INSERT INTO app.github_pr_operation_events
        (tenant_id, site_id, id, operation_id, step, event_kind, evidence_sha256)
    VALUES (v_operation.tenant_id, p_site_id, gen_random_uuid(), p_operation_id,
        p_step, 'reconciled', p_evidence_sha256);
    RETURN 'reconciled';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_reconcile_github_pr_step(text,bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_reconcile_github_pr_step(text,bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.reconcile_github_pr_step(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL::bigint, p_pr_url text DEFAULT NULL::text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_reconcile_github_pr_step('owner',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step,p_evidence_sha256,p_pr_number,p_pr_url);
END;
$function$;

REVOKE ALL ON FUNCTION control.reconcile_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.reconcile_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_reconcile_github_pr_step(p_session_hash bytea, p_site_id uuid, p_generation text, p_operation_id uuid, p_worker_id uuid, p_fence bigint, p_step text, p_evidence_sha256 bytea, p_pr_number bigint DEFAULT NULL::bigint, p_pr_url text DEFAULT NULL::text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_reconcile_github_pr_step('weekly_delivery',p_session_hash,p_site_id,p_generation,p_operation_id,p_worker_id,p_fence,p_step,p_evidence_sha256,p_pr_number,p_pr_url);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_reconcile_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_reconcile_github_pr_step(bytea,uuid,text,uuid,uuid,bigint,text,bytea,bigint,text) TO signal_workflow;
-- End port group 33.
-- seal_candidate_recipe_revision: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_seal_candidate_recipe_revision(p_actor text, p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid, p_extension_id uuid, p_build_id uuid, p_report_id uuid, p_finding_id uuid, p_release_id uuid, p_release_hash bytea, p_idempotency_key uuid, p_canonical bytea, p_revision_hash bytea)
 RETURNS TABLE(revision_id uuid, revision_sha256 bytea, replayed boolean, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_authority record; v_build app.candidate_build_intents%%ROWTYPE;
        v_receipt app.candidate_build_receipts%%ROWTYPE;
        v_release control.recipe_releases%%ROWTYPE;
        v_existing app.candidate_recipe_revisions%%ROWTYPE;
        v_report app.crawl_audit_reports%%ROWTYPE; v_manifest jsonb;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_delivery') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_delivery' THEN
PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('revision_id',p_revision_id::text,'extension_id',p_extension_id::text,'build_id',p_build_id::text,'report_id',p_report_id::text,'finding_id',p_finding_id::text,'release_id',p_release_id::text,'revision_key',p_idempotency_key::text));

END IF;
    IF p_revision_id IS NULL OR p_extension_id IS NULL OR p_build_id IS NULL
       OR p_report_id IS NULL OR p_finding_id IS NULL OR p_release_id IS NULL
       OR p_idempotency_key IS NULL OR p_release_hash IS NULL
       OR octet_length(p_release_hash) <> 32 OR p_canonical IS NULL
       OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768
       OR p_revision_hash IS NULL OR p_revision_hash <> sha256(p_canonical) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'invalid_revision'::text; RETURN;
    END IF;
    SELECT * INTO v_authority FROM control.admit_port_context(p_actor,'authority',p_session_hash,p_generation,p_site_id,CASE p_actor WHEN 'weekly_delivery' THEN 'prepare' ELSE NULL END);
    IF NOT control.port_permission(v_authority.outcome,NULL,NULL,false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, v_authority.outcome; RETURN;
    END IF;
    IF NOT control.port_permission('authorized',v_authority.role_key,control.port_required_role(p_actor),false) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'permission_denied'::text; RETURN;
    END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'site_not_verified'::text; RETURN;
    END IF;
    SELECT * INTO v_existing FROM app.candidate_recipe_revisions AS revision
     WHERE revision.tenant_id = v_authority.tenant_id AND revision.site_id = p_site_id
       AND revision.created_by_user_id = v_authority.user_id
       AND revision.idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.id = p_revision_id AND v_existing.extension_id = p_extension_id
           AND v_existing.build_id = p_build_id AND v_existing.audit_report_id = p_report_id
           AND v_existing.finding_id = p_finding_id AND v_existing.recipe_release_id = p_release_id
           AND v_existing.release_content_hash = p_release_hash
           AND v_existing.canonical_manifest = p_canonical THEN
            RETURN QUERY SELECT v_existing.id, v_existing.revision_sha256,
                true, 'sealed'::text; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'revision_conflict'::text; RETURN;
    END IF;
    SELECT * INTO v_release FROM control.recipe_releases AS release
     WHERE release.id = p_release_id AND release.content_hash = p_release_hash FOR SHARE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM control.recipe_release_events AS event
         WHERE event.release_id = p_release_id
         ORDER BY event.sequence_number DESC LIMIT 1
    ) OR (SELECT event.status FROM control.recipe_release_events AS event
         WHERE event.release_id = p_release_id
         ORDER BY event.sequence_number DESC LIMIT 1) <> 'REVIEWED'
       OR v_release.canonical_body IS NULL
       OR convert_from(v_release.canonical_body, 'UTF8')::jsonb->>'delivery_mode'
            <> 'pull_request'
       OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones AS tombstone
           WHERE tombstone.target_kind = 'recipe_release'
             AND tombstone.target_id = p_release_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'release_unavailable'::text; RETURN;
    END IF;
    SELECT * INTO v_build FROM app.candidate_build_intents AS build
     WHERE build.tenant_id = v_authority.tenant_id AND build.site_id = p_site_id
       AND build.id = p_build_id AND build.extension_id = p_extension_id
       AND build.status = 'completed' AND build.requested_by_user_id = v_authority.user_id
       AND build.membership_epoch = v_authority.membership_epoch
       AND build.site_epoch = v_authority.site_authorization_epoch
       AND build.recovery_generation = p_generation;
    SELECT * INTO v_receipt FROM app.candidate_build_receipts AS receipt
     WHERE receipt.tenant_id = v_authority.tenant_id AND receipt.site_id = p_site_id
       AND receipt.build_id = p_build_id AND receipt.exit_class = 'passed';
    IF v_build.id IS NULL OR v_receipt.build_id IS NULL
       OR v_receipt.patch_sha256 <> v_build.patch_sha256
       OR v_build.patch_sha256 =
           'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
       OR NOT EXISTS (SELECT 1 FROM app.github_pr_extensions AS extension
           JOIN app.github_read_bindings AS binding
             ON binding.tenant_id = extension.tenant_id AND binding.site_id = extension.site_id
            AND binding.id = extension.binding_id AND binding.status = 'active'
          WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
            AND extension.id = p_extension_id AND extension.status = 'observed'
            AND extension.base_sha = v_build.base_sha
            AND extension.tree_sha = v_build.tree_sha) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'build_unavailable'::text; RETURN;
    END IF;
    SELECT * INTO v_report FROM app.crawl_audit_reports AS report
     WHERE report.tenant_id = v_authority.tenant_id AND report.site_id = p_site_id
       AND report.id = p_report_id AND report.detector_release_id =
           '0fc2a362-f51d-4b75-9bbf-22d456f29b40'::uuid;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_report.findings) AS item(value)
        WHERE item.value->>'id' = p_finding_id::text) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'finding_unavailable'::text; RETURN;
    END IF;
    BEGIN
        v_manifest := convert_from(p_canonical, 'UTF8')::jsonb;
    EXCEPTION WHEN character_not_in_repertoire OR invalid_text_representation THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'invalid_revision'::text; RETURN;
    END;
    IF jsonb_typeof(v_manifest) IS DISTINCT FROM 'object'
       OR v_manifest->>'site_id' IS DISTINCT FROM p_site_id::text
       OR v_manifest->>'extension_id' IS DISTINCT FROM p_extension_id::text
       OR v_manifest->>'build_id' IS DISTINCT FROM p_build_id::text
       OR v_manifest->>'audit_report_id' IS DISTINCT FROM p_report_id::text
       OR v_manifest->>'finding_id' IS DISTINCT FROM p_finding_id::text
       OR v_manifest->>'recipe_release_id' IS DISTINCT FROM p_release_id::text
       OR v_manifest->>'release_content_hash' IS DISTINCT FROM encode(p_release_hash, 'hex')
       OR v_manifest->>'base_sha' IS DISTINCT FROM v_build.base_sha
       OR v_manifest->>'patch_sha256' IS DISTINCT FROM v_build.patch_sha256
       OR v_manifest->>'source_path' IS DISTINCT FROM 'index.html'
       OR COALESCE(v_manifest->>'source_sha256', '') !~ '^[0-9a-f]{64}$'
       OR COALESCE(v_manifest->>'result_sha256', '') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(v_manifest->'patch') IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_manifest #> '{patch,offset}') IS DISTINCT FROM 'number'
       OR COALESCE(v_manifest #>> '{patch,offset}', '') !~ '^(0|[1-9][0-9]*)$'
       OR jsonb_typeof(v_manifest #> '{patch,before}') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_manifest #> '{patch,after}') IS DISTINCT FROM 'string'
       OR v_manifest #>> '{evidence,manifest_id}' IS DISTINCT FROM v_report.manifest_id::text
       OR v_manifest #>> '{evidence,manifest_sha256}' IS DISTINCT FROM
            encode(v_report.manifest_sha256, 'hex')
       OR v_manifest #>> '{evidence,finding,id}' IS DISTINCT FROM p_finding_id::text
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_report.findings) AS item(value)
           WHERE item.value->>'id' = p_finding_id::text
             AND item.value = v_manifest #> '{evidence,finding}')
       OR v_manifest #>> '{build_receipt,toolchain}' IS DISTINCT FROM v_receipt.toolchain
       OR v_manifest #>> '{build_receipt,command}' IS DISTINCT FROM v_receipt.build_command
       OR v_manifest #>> '{build_receipt,exit_class}' IS DISTINCT FROM 'passed'
       OR v_manifest #>> '{build_receipt,logs_sha256}' IS DISTINCT FROM v_receipt.logs_sha256
       OR v_manifest #> '{build_receipt,artifacts}' IS DISTINCT FROM v_receipt.artifacts
       OR v_manifest->>'approval_class' IS DISTINCT FROM 'owner_review'
       OR COALESCE(length(v_manifest->>'expected_impact'), 0) NOT BETWEEN 1 AND 500
       OR COALESCE(length(v_manifest->>'recovery_plan'), 0) NOT BETWEEN 1 AND 500
       OR NOT EXISTS (SELECT 1 FROM app.crawl_page_records AS page
           WHERE page.tenant_id = v_authority.tenant_id AND page.site_id = p_site_id
             AND page.crawl_run_id = v_report.crawl_run_id
             AND page.id::text = v_manifest #>> '{evidence,page_id}'
             AND encode(page.body_sha256, 'hex') = v_manifest->>'source_sha256') THEN
        RETURN QUERY SELECT NULL::uuid, NULL::bytea, false, 'invalid_revision'::text; RETURN;
    END IF;
    INSERT INTO app.candidate_recipe_revisions (
        tenant_id, site_id, id, extension_id, build_id, audit_report_id, finding_id,
        recipe_release_id, release_content_hash, base_sha, patch_sha256,
        canonical_manifest, created_by_user_id, idempotency_key,
        membership_epoch, site_epoch, recovery_generation
    ) VALUES (v_authority.tenant_id, p_site_id, p_revision_id, p_extension_id,
        p_build_id, p_report_id, p_finding_id, p_release_id, p_release_hash,
        v_build.base_sha, v_build.patch_sha256, p_canonical, v_authority.user_id,
        p_idempotency_key, v_authority.membership_epoch,
        v_authority.site_authorization_epoch, p_generation);
    RETURN QUERY SELECT p_revision_id, p_revision_hash, false, 'sealed'::text;
END;
$function$;

ALTER FUNCTION control.port_seal_candidate_recipe_revision(text,bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_seal_candidate_recipe_revision(text,bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.seal_candidate_recipe_revision(p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid, p_extension_id uuid, p_build_id uuid, p_report_id uuid, p_finding_id uuid, p_release_id uuid, p_release_hash bytea, p_idempotency_key uuid, p_canonical bytea, p_revision_hash bytea)
 RETURNS TABLE(revision_id uuid, revision_sha256 bytea, replayed boolean, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_seal_candidate_recipe_revision('owner',p_session_hash,p_site_id,p_generation,p_revision_id,p_extension_id,p_build_id,p_report_id,p_finding_id,p_release_id,p_release_hash,p_idempotency_key,p_canonical,p_revision_hash);
END;
$function$;

REVOKE ALL ON FUNCTION control.seal_candidate_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.seal_candidate_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_seal_candidate_recipe_revision(p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid, p_extension_id uuid, p_build_id uuid, p_report_id uuid, p_finding_id uuid, p_release_id uuid, p_release_hash bytea, p_idempotency_key uuid, p_canonical bytea, p_revision_hash bytea)
 RETURNS TABLE(revision_id uuid, revision_sha256 bytea, replayed boolean, outcome text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_seal_candidate_recipe_revision('weekly_delivery',p_session_hash,p_site_id,p_generation,p_revision_id,p_extension_id,p_build_id,p_report_id,p_finding_id,p_release_id,p_release_hash,p_idempotency_key,p_canonical,p_revision_hash);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_seal_candidate_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_seal_candidate_recipe_revision(bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,bytea,uuid,bytea,bytea) TO signal_workflow;
-- End port group 34.
-- approved_business_brain_facts: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_approved_business_brain_facts(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS TABLE(fact_id uuid, category text, statement text, source_kind text, page_evidence_id uuid, document_id uuid, extracted_range jsonb, owner_membership_id uuid, created_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session_hash,p_generation,p_site_id,'{brain_refresh,strategy_rebuild,brief_proposals}'::text[]);

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_session_hash,p_generation,p_site_id,NULL); IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN; END IF;
 RETURN QUERY SELECT f.fact_id,f.category,f.statement,f.source_kind,f.page_evidence_id,f.document_id,f.extracted_range,f.owner_membership_id,f.created_at
 FROM control.port_list_business_brain_facts(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_session_hash,p_generation,p_site_id,'approved') f
 WHERE NOT control.docs_document_withdrawn(v.tenant_id,p_site_id,f.document_id);
END
$function$;

ALTER FUNCTION control.port_approved_business_brain_facts(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_approved_business_brain_facts(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.approved_business_brain_facts(p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS TABLE(fact_id uuid, category text, statement text, source_kind text, page_evidence_id uuid, document_id uuid, extracted_range jsonb, owner_membership_id uuid, created_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_approved_business_brain_facts('owner',p_session_hash,p_generation,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.approved_business_brain_facts(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.approved_business_brain_facts(bytea,text,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_approved_business_brain_facts(p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS TABLE(fact_id uuid, category text, statement text, source_kind text, page_evidence_id uuid, document_id uuid, extracted_range jsonb, owner_membership_id uuid, created_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_approved_business_brain_facts('weekly_skill',p_session_hash,p_generation,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_approved_business_brain_facts(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_approved_business_brain_facts(bytea,text,uuid) TO signal_workflow;
-- End port group 35.
-- business_brain_begin_extraction: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_business_brain_begin_extraction(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid, p_id uuid, p_source_kind text, p_source_id uuid, p_start integer, p_end integer, p_version text, p_input_hash bytea, p_decision_id uuid, p_model_id uuid)
 RETURNS TABLE(extraction_id uuid, decision_id uuid, model_operation_id uuid, state text, tenant_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; e app.business_brain_extractions%%ROWTYPE; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session_hash,p_generation,p_site_id,'{brain_refresh}'::text[]);PERFORM control.assert_weekly_skill_resource(p_session_hash,p_generation,p_site_id,'source_id',p_source_id);IF p_version IS DISTINCT FROM 'business-brain-v1' OR p_id IS DISTINCT FROM (SELECT control.weekly_skill_identity(cycle_id,p_source_id::text) FROM app.weekly_skill_intents WHERE handle_hash=p_session_hash) OR NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session_hash AND u->>'source_id'=p_source_id::text AND u->>'source_kind'=p_source_kind) OR NOT ((p_source_kind='page_evidence' AND p_start=0 AND p_end=0) OR (p_source_kind='brand_document' AND p_start=0 AND p_end BETWEEN 1 AND 24000)) THEN RAISE EXCEPTION 'skill_extraction_scope_denied' USING ERRCODE='42501'; END IF;

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_session_hash,p_generation,p_site_id,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RAISE EXCEPTION 'owner_access_denied' USING ERRCODE='42501'; END IF;
 IF p_source_kind='page_evidence' AND control.port_business_brain_page_source(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_session_hash,p_generation,p_site_id,p_source_id) IS NULL THEN RAISE EXCEPTION 'source_unavailable' USING ERRCODE='22023'; END IF;
 IF p_source_kind='brand_document' AND NOT EXISTS(SELECT 1 FROM app.brand_documents d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site_id AND d.id=p_source_id AND NOT d.secret_signal AND NOT control.docs_document_withdrawn(d.tenant_id,d.site_id,d.id) AND NOT EXISTS(SELECT 1 FROM app.brand_document_events b WHERE b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.document_id=d.id AND b.event_type='deleted') AND NOT EXISTS(SELECT 1 FROM app.brand_documents n WHERE n.tenant_id=d.tenant_id AND n.site_id=d.site_id AND n.supersedes_id=d.id)) THEN RAISE EXCEPTION 'source_unavailable' USING ERRCODE='22023'; END IF;
 SELECT * INTO e FROM app.business_brain_extractions x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site_id AND x.source_kind=p_source_kind AND x.source_id=p_source_id AND x.range_start=p_start AND x.range_end=p_end AND x.extraction_version=p_version;
 IF FOUND THEN
  IF e.input_sha256<>p_input_hash THEN RAISE EXCEPTION 'input_conflict' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT e.id,e.decision_id,e.model_operation_id,coalesce((SELECT r.state FROM app.business_brain_extraction_results r WHERE r.tenant_id=e.tenant_id AND r.site_id=e.site_id AND r.extraction_id=e.id),'outcome_unknown'),v.tenant_id; RETURN;
 END IF;
 INSERT INTO app.business_brain_extractions(tenant_id,site_id,id,source_kind,source_id,range_start,range_end,extraction_version,input_sha256,decision_id,model_operation_id)
 VALUES(v.tenant_id,p_site_id,p_id,p_source_kind,p_source_id,p_start,p_end,p_version,p_input_hash,p_decision_id,p_model_id);
 RETURN QUERY SELECT p_id,p_decision_id,p_model_id,'started'::text,v.tenant_id;
END;
$function$;

ALTER FUNCTION control.port_business_brain_begin_extraction(text,bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_business_brain_begin_extraction(text,bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.business_brain_begin_extraction(p_session_hash bytea, p_generation text, p_site_id uuid, p_id uuid, p_source_kind text, p_source_id uuid, p_start integer, p_end integer, p_version text, p_input_hash bytea, p_decision_id uuid, p_model_id uuid)
 RETURNS TABLE(extraction_id uuid, decision_id uuid, model_operation_id uuid, state text, tenant_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_business_brain_begin_extraction('owner',p_session_hash,p_generation,p_site_id,p_id,p_source_kind,p_source_id,p_start,p_end,p_version,p_input_hash,p_decision_id,p_model_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.business_brain_begin_extraction(bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.business_brain_begin_extraction(bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_business_brain_begin_extraction(p_session_hash bytea, p_generation text, p_site_id uuid, p_id uuid, p_source_kind text, p_source_id uuid, p_start integer, p_end integer, p_version text, p_input_hash bytea, p_decision_id uuid, p_model_id uuid)
 RETURNS TABLE(extraction_id uuid, decision_id uuid, model_operation_id uuid, state text, tenant_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_business_brain_begin_extraction('weekly_skill',p_session_hash,p_generation,p_site_id,p_id,p_source_kind,p_source_id,p_start,p_end,p_version,p_input_hash,p_decision_id,p_model_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_business_brain_begin_extraction(bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_business_brain_begin_extraction(bytea,text,uuid,uuid,text,uuid,integer,integer,text,bytea,uuid,uuid) TO signal_workflow;
-- End port group 36.
-- business_brain_finish_extraction: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_business_brain_finish_extraction(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid, p_id uuid, p_page_type text, p_canonical_output bytea, p_output_hash bytea, p_response_id text, p_model text, p_usage jsonb, p_reason text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; e app.business_brain_extractions%%ROWTYPE; item jsonb; f_id uuid; p_output jsonb; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session_hash,p_generation,p_site_id,'{brain_refresh}'::text[]);PERFORM control.assert_weekly_skill_resource(p_session_hash,p_generation,p_site_id,'extraction_id',p_id);

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_session_hash,p_generation,p_site_id,NULL); IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 SELECT * INTO e FROM app.business_brain_extractions WHERE tenant_id=v.tenant_id AND site_id=p_site_id AND id=p_id;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 IF EXISTS(SELECT 1 FROM app.business_brain_extraction_results WHERE tenant_id=e.tenant_id AND site_id=e.site_id AND extraction_id=e.id) THEN RETURN 'replayed'; END IF;
 IF e.source_kind='brand_document' AND control.docs_document_withdrawn(e.tenant_id,e.site_id,e.source_id) THEN RETURN 'source_unavailable'; END IF; IF p_reason IS NOT NULL THEN
  INSERT INTO app.business_brain_extraction_results(tenant_id,site_id,extraction_id,state,reason) VALUES(e.tenant_id,e.site_id,e.id,'failed',p_reason); RETURN 'failed';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM app.decision_records d WHERE d.tenant_id=e.tenant_id AND d.site_id=e.site_id AND d.id=e.decision_id AND d.purpose='business_brain.page_type' AND d.answer->'page_type'->>'choice'=p_page_type AND d.outcome<>'ship') THEN RETURN 'decision_unavailable'; END IF;
 IF p_canonical_output IS NULL OR octet_length(p_canonical_output)>256000 OR p_output_hash IS DISTINCT FROM sha256(p_canonical_output) THEN RETURN 'invalid'; END IF;
 p_output:=convert_from(p_canonical_output,'UTF8')::jsonb;
 IF jsonb_typeof(p_output)<>'array' OR jsonb_array_length(p_output)>40 THEN RETURN 'invalid'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_output) LOOP
  IF jsonb_typeof(item)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(item))<>2 OR item->>'category' IS NULL OR item->>'category' NOT IN ('product','pricing','audience','positioning','proof_point','competitor','claim','legal','medical','financial','product_claim') OR jsonb_typeof(item->'statement') IS DISTINCT FROM 'string' OR length(btrim(item->>'statement')) NOT BETWEEN 1 AND 4000 THEN RETURN 'invalid'; END IF;
 END LOOP;
 INSERT INTO app.business_brain_extraction_results(tenant_id,site_id,extraction_id,state,page_type,provider_response_id,model_reported,output,output_sha256,usage)
 VALUES(e.tenant_id,e.site_id,e.id,'completed',p_page_type,p_response_id,p_model,p_output,p_output_hash,p_usage);
 FOR item IN SELECT value FROM jsonb_array_elements(p_output) LOOP
  f_id:=gen_random_uuid();
  INSERT INTO app.business_brain_facts(tenant_id,site_id,id,category,statement,initial_status,source_kind,page_evidence_id,document_id,extracted_range,sensitive,created_by_user_id,extraction_id,decision_id)
  VALUES(e.tenant_id,e.site_id,f_id,item->>'category',item->>'statement','proposed',e.source_kind,CASE WHEN e.source_kind='page_evidence' THEN e.source_id END,CASE WHEN e.source_kind='brand_document' THEN e.source_id END,CASE WHEN e.source_kind='brand_document' THEN jsonb_build_object('start',e.range_start,'end',e.range_end) END,(item->>'category') IN ('product','pricing','claim','legal','medical','financial','product_claim'),v.user_id,e.id,e.decision_id);
  INSERT INTO app.business_brain_audit_records VALUES(e.tenant_id,e.site_id,gen_random_uuid(),'proposed',f_id,v.user_id,transaction_timestamp());
 END LOOP;
 RETURN 'completed';
END;
$function$;

ALTER FUNCTION control.port_business_brain_finish_extraction(text,bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_business_brain_finish_extraction(text,bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.business_brain_finish_extraction(p_session_hash bytea, p_generation text, p_site_id uuid, p_id uuid, p_page_type text, p_canonical_output bytea, p_output_hash bytea, p_response_id text, p_model text, p_usage jsonb, p_reason text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_business_brain_finish_extraction('owner',p_session_hash,p_generation,p_site_id,p_id,p_page_type,p_canonical_output,p_output_hash,p_response_id,p_model,p_usage,p_reason);
END;
$function$;

REVOKE ALL ON FUNCTION control.business_brain_finish_extraction(bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.business_brain_finish_extraction(bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_business_brain_finish_extraction(p_session_hash bytea, p_generation text, p_site_id uuid, p_id uuid, p_page_type text, p_canonical_output bytea, p_output_hash bytea, p_response_id text, p_model text, p_usage jsonb, p_reason text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_business_brain_finish_extraction('weekly_skill',p_session_hash,p_generation,p_site_id,p_id,p_page_type,p_canonical_output,p_output_hash,p_response_id,p_model,p_usage,p_reason);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_business_brain_finish_extraction(bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_business_brain_finish_extraction(bytea,text,uuid,uuid,text,bytea,bytea,text,text,jsonb,text) TO signal_workflow;
-- End port group 37.
-- business_brain_page_source: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_business_brain_page_source(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid, p_page_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; result jsonb; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session_hash,p_generation,p_site_id,'{brain_refresh}'::text[]);PERFORM control.assert_weekly_skill_resource(p_session_hash,p_generation,p_site_id,'source_id',p_page_id);

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_session_hash,p_generation,p_site_id,NULL); IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
 SELECT jsonb_build_object('tenant_id',p.tenant_id,'url',o.final_url,'title',coalesce(p.title,''),'artifact',to_jsonb(a),'body_sha256',encode(p.body_sha256,'hex')) INTO result
 FROM app.crawl_page_records p JOIN app.fetch_observations o ON o.tenant_id=p.tenant_id AND o.site_id=p.site_id AND o.id=p.observation_id
 JOIN app.artifacts a ON a.tenant_id=o.tenant_id AND a.site_id=o.site_id AND a.id=o.raw_artifact_id
 JOIN app.crawl_frontier_settlements s ON s.tenant_id=p.tenant_id AND s.site_id=p.site_id AND s.page_record_id=p.id AND s.terminal_state='fetched'
 JOIN app.crawl_manifests m ON m.tenant_id=p.tenant_id AND m.site_id=p.site_id AND m.crawl_run_id=p.crawl_run_id
 WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site_id AND p.id=p_page_id AND p.http_status BETWEEN 200 AND 299;
 RETURN result;
END;
$function$;

ALTER FUNCTION control.port_business_brain_page_source(text,bytea,text,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_business_brain_page_source(text,bytea,text,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.business_brain_page_source(p_session_hash bytea, p_generation text, p_site_id uuid, p_page_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_business_brain_page_source('owner',p_session_hash,p_generation,p_site_id,p_page_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.business_brain_page_source(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.business_brain_page_source(bytea,text,uuid,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_business_brain_page_source(p_session_hash bytea, p_generation text, p_site_id uuid, p_page_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_business_brain_page_source('weekly_skill',p_session_hash,p_generation,p_site_id,p_page_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_business_brain_page_source(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_business_brain_page_source(bytea,text,uuid,uuid) TO signal_workflow;
-- End port group 38.
-- business_brain_read: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_business_brain_read(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session_hash,p_generation,p_site_id,'{brain_refresh,strategy_rebuild,brief_proposals}'::text[]);

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_session_hash,p_generation,p_site_id,NULL); IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
 RETURN jsonb_build_object(
 'facts',coalesce((SELECT jsonb_agg(to_jsonb(f)||jsonb_build_object('decision_id',b.decision_id,'extraction_id',b.extraction_id,
   'source_review_required',control.docs_document_withdrawn(v.tenant_id,p_site_id,b.document_id)))
   FROM control.port_list_business_brain_facts(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_session_hash,p_generation,p_site_id,NULL) f JOIN app.business_brain_facts b
   ON b.tenant_id=v.tenant_id AND b.site_id=p_site_id AND b.id=f.fact_id),'[]'::jsonb),
 'voice',(SELECT to_jsonb(x) FROM (SELECT id AS profile_id,profile,supersedes_id,created_at FROM app.business_brain_voice_profiles p WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site_id AND NOT EXISTS(SELECT 1 FROM app.business_brain_voice_profiles n WHERE n.tenant_id=p.tenant_id AND n.site_id=p.site_id AND n.supersedes_id=p.id) ORDER BY created_at DESC,id DESC LIMIT 1) x),
 'extractions',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT e.id AS extraction_id,e.source_kind,e.source_id,e.decision_id,coalesce(r.state,'outcome_unknown') AS state,r.page_type,d.fallback,d.provider,r.reason FROM app.business_brain_extractions e LEFT JOIN app.business_brain_extraction_results r ON r.tenant_id=e.tenant_id AND r.site_id=e.site_id AND r.extraction_id=e.id LEFT JOIN app.decision_records d ON d.tenant_id=e.tenant_id AND d.site_id=e.site_id AND d.id=e.decision_id WHERE e.tenant_id=v.tenant_id AND e.site_id=p_site_id ORDER BY e.created_at DESC LIMIT 100) x),'[]'::jsonb));
END
$function$;

ALTER FUNCTION control.port_business_brain_read(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_business_brain_read(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.business_brain_read(p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_business_brain_read('owner',p_session_hash,p_generation,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.business_brain_read(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.business_brain_read(bytea,text,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_business_brain_read(p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_business_brain_read('weekly_skill',p_session_hash,p_generation,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_business_brain_read(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_business_brain_read(bytea,text,uuid) TO signal_workflow;
-- End port group 39.
-- content_writer_create_brief: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_content_writer_create_brief(p_actor text, p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_payload jsonb, p_origin text, p_supersedes uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; inventory jsonb; ref text; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{brief_proposals}'::text[]);PERFORM control.assert_weekly_skill_resource(p_hash,p_generation,p_site,'brief_id',p_id);IF p_origin IS DISTINCT FROM 'evidence_proposal' OR p_supersedes IS NOT NULL OR NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_hash AND u->>'brief_id'=p_id::text AND u->'payload'=p_payload) THEN RETURN 'denied'; END IF; IF EXISTS(SELECT 1 FROM app.content_briefs b JOIN app.weekly_skill_intents i ON i.tenant_id=b.tenant_id AND i.site_id=b.site_id WHERE i.handle_hash=p_hash AND b.id=p_id AND b.payload=p_payload AND b.origin='evidence_proposal') THEN RETURN 'created'; END IF;

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_hash,p_generation,p_site,NULL); IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 inventory:=control.port_content_writer_inventory(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_hash,p_generation,p_site);
 IF p_origin IS NULL OR p_payload IS NULL OR p_origin NOT IN ('owner','evidence_proposal') OR jsonb_typeof(p_payload)<>'object' OR coalesce(p_payload->>'kind','') NOT IN ('new_article','content_refresh') OR coalesce(jsonb_array_length(p_payload->'source_ids'),0) NOT BETWEEN 1 AND 8 OR coalesce(jsonb_array_length(p_payload->'fact_ids'),0) NOT BETWEEN 1 AND 20 OR coalesce(jsonb_array_length(p_payload->'internal_links'),9)>8 THEN RETURN 'invalid'; END IF;
 FOR ref IN SELECT jsonb_array_elements_text(p_payload->'source_ids') LOOP
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(inventory) x WHERE x->>'source_id'=ref) THEN RETURN 'source_unavailable'; END IF;
 END LOOP;
 FOR ref IN SELECT jsonb_array_elements_text(p_payload->'fact_ids') LOOP
  IF NOT EXISTS(SELECT 1 FROM control.port_approved_business_brain_facts(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_hash,p_generation,p_site) f WHERE f.fact_id::text=ref) THEN RETURN 'fact_unavailable'; END IF;
 END LOOP;
 FOR ref IN SELECT jsonb_array_elements_text(p_payload->'internal_links') LOOP
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(inventory) x WHERE x->>'url'=ref) THEN RETURN 'link_unavailable'; END IF;
 END LOOP;
 IF p_supersedes IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.id=p_supersedes) OR EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.supersedes_id=p_supersedes)) THEN RETURN 'conflict'; END IF;
 INSERT INTO app.content_briefs VALUES(v.tenant_id,p_site,p_id,p_payload,p_origin,p_supersedes,v.user_id,transaction_timestamp());
 IF p_origin='owner' THEN INSERT INTO app.content_brief_acceptances VALUES(v.tenant_id,p_site,p_id,v.user_id,transaction_timestamp()); END IF;
 INSERT INTO app.content_writer_audits VALUES(v.tenant_id,p_site,gen_random_uuid(),'brief_created',p_id,v.user_id,transaction_timestamp()); RETURN 'created';
END;
$function$;

ALTER FUNCTION control.port_content_writer_create_brief(text,bytea,text,uuid,uuid,jsonb,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_content_writer_create_brief(text,bytea,text,uuid,uuid,jsonb,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.content_writer_create_brief(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_payload jsonb, p_origin text, p_supersedes uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_create_brief('owner',p_hash,p_generation,p_site,p_id,p_payload,p_origin,p_supersedes);
END;
$function$;

REVOKE ALL ON FUNCTION control.content_writer_create_brief(bytea,text,uuid,uuid,jsonb,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.content_writer_create_brief(bytea,text,uuid,uuid,jsonb,text,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_content_writer_create_brief(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_payload jsonb, p_origin text, p_supersedes uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_create_brief('weekly_skill',p_hash,p_generation,p_site,p_id,p_payload,p_origin,p_supersedes);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_content_writer_create_brief(bytea,text,uuid,uuid,jsonb,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_content_writer_create_brief(bytea,text,uuid,uuid,jsonb,text,uuid) TO signal_workflow;
-- End port group 40.
-- content_writer_inventory: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_content_writer_inventory(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{strategy_rebuild,brief_proposals}'::text[]);

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
 RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('source_id',p.id,'url',o.final_url,'title',coalesce(p.title,'')))
 FROM app.crawl_page_records p JOIN app.fetch_observations o ON o.tenant_id=p.tenant_id AND o.site_id=p.site_id AND o.id=p.observation_id
 JOIN app.crawl_frontier_settlements s ON s.tenant_id=p.tenant_id AND s.site_id=p.site_id AND s.page_record_id=p.id AND s.terminal_state='fetched'
 JOIN app.crawl_manifests m ON m.tenant_id=p.tenant_id AND m.site_id=p.site_id AND m.crawl_run_id=p.crawl_run_id
 WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.http_status BETWEEN 200 AND 299),'[]'::jsonb);
END;
$function$;

ALTER FUNCTION control.port_content_writer_inventory(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_content_writer_inventory(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.content_writer_inventory(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_inventory('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.content_writer_inventory(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.content_writer_inventory(bytea,text,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_content_writer_inventory(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_inventory('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_content_writer_inventory(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_content_writer_inventory(bytea,text,uuid) TO signal_workflow;
-- End port group 41.
-- content_writer_read: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_content_writer_read(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE result jsonb; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{strategy_rebuild,brief_proposals}'::text[]);

END IF;
 result:=control.port_content_writer_read_0082(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_hash,p_generation,p_site);
 IF result IS NULL THEN RETURN NULL; END IF;
 RETURN result || jsonb_build_object('monthly_model_budget',control.port_model_budget_read(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_hash,p_generation,p_site));
END;
$function$;

ALTER FUNCTION control.port_content_writer_read(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_content_writer_read(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.content_writer_read(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_read('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.content_writer_read(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.content_writer_read(bytea,text,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_content_writer_read(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_read('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_content_writer_read(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_content_writer_read(bytea,text,uuid) TO signal_workflow;
-- End port group 42.
-- content_writer_read_0082: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_content_writer_read_0082(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE a record; result jsonb;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{strategy_rebuild,brief_proposals}'::text[]);

END IF;
    SELECT * INTO a FROM control.admit_port_context(p_actor,'brain',p_hash,p_generation,p_site,NULL);
    IF NOT control.port_permission(CASE WHEN a.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
    result:=control.content_writer_record_read(p_hash,p_generation,p_site);
    RETURN jsonb_set(result,'{candidates}',coalesce((SELECT jsonb_agg(c||jsonb_build_object(
      'delivery_approval_id',(SELECT d.id FROM app.content_delivery_decisions d WHERE d.tenant_id=a.tenant_id
        AND d.site_id=p_site AND d.candidate_id=(c->>'candidate_id')::uuid AND d.recovery_generation=p_generation)))
      FROM jsonb_array_elements(result->'candidates') c),'[]'::jsonb));
END
$function$;

ALTER FUNCTION control.port_content_writer_read_0082(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_content_writer_read_0082(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.content_writer_read_0082(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_read_0082('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.content_writer_read_0082(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.weekly_skill_content_writer_read_0082(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_content_writer_read_0082('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_content_writer_read_0082(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_content_writer_read_0082(bytea,text,uuid) TO signal_workflow;
-- End port group 43.
-- current_bing_binding: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_current_bing_binding(p_actor text, p_port_hash bytea,p_port_generation text, p_tenant_id uuid, p_site_id uuid)
 RETURNS TABLE(binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_origin record;
BEGIN
    SELECT * INTO v_origin FROM control.verified_site_origin(p_tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT event.binding_id, event.property_resource_name,
        event.secret_reference, event.verified_origin
      FROM app.bing_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.event_kind = 'bound' AND event.verified_origin = v_origin.origin
       AND NOT EXISTS (SELECT 1 FROM app.bing_binding_events AS later
          WHERE later.tenant_id = event.tenant_id AND later.site_id = event.site_id
            AND later.binding_id = event.binding_id AND later.event_kind <> 'bound')
     ORDER BY event.recorded_at DESC, event.id DESC LIMIT 1;
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v_origin record;
BEGIN PERFORM control.assert_weekly_skill_stage(p_port_hash,p_port_generation,p_site_id,ARRAY['import_bing']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.admit_port_context('weekly_skill','brain',p_port_hash,p_port_generation,p_site_id,NULL)) THEN RAISE EXCEPTION 'skill_tenant_denied' USING ERRCODE='42501'; END IF;
    SELECT * INTO v_origin FROM control.verified_site_origin(p_tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT event.binding_id, event.property_resource_name,
        event.secret_reference, event.verified_origin
      FROM app.bing_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_port_hash AND u->>'binding_id'=event.binding_id::text) AND event.event_kind = 'bound' AND event.verified_origin = v_origin.origin
       AND NOT EXISTS (SELECT 1 FROM app.bing_binding_events AS later
          WHERE later.tenant_id = event.tenant_id AND later.site_id = event.site_id
            AND later.binding_id = event.binding_id AND later.event_kind <> 'bound')
     ORDER BY event.recorded_at DESC, event.id DESC LIMIT 1;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_current_bing_binding(text,bytea,text,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_current_bing_binding(text,bytea,text,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.current_bing_binding(p_tenant_id uuid, p_site_id uuid)
 RETURNS TABLE(binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_current_bing_binding('owner',NULL,NULL,p_tenant_id,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.current_bing_binding(uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.current_bing_binding(uuid,uuid) TO signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.weekly_skill_current_bing_binding(p_hash bytea, p_generation text, p_tenant_id uuid, p_site_id uuid)
 RETURNS TABLE(binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_current_bing_binding('weekly_skill',p_hash,p_generation,p_tenant_id,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_current_bing_binding(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_current_bing_binding(bytea,text,uuid,uuid) TO signal_workflow;
-- End port group 44.
-- current_gsc_binding: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_current_gsc_binding(p_actor text, p_port_hash bytea,p_port_generation text, p_tenant_id uuid, p_site_id uuid)
 RETURNS TABLE(binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_origin record;
BEGIN
    SELECT * INTO v_origin FROM control.verified_site_origin(p_tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT event.binding_id, event.property_resource_name,
        event.secret_reference, event.verified_origin
      FROM app.gsc_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.event_kind = 'bound' AND event.verified_origin = v_origin.origin
       AND NOT EXISTS (SELECT 1 FROM app.gsc_binding_events AS later
          WHERE later.tenant_id = event.tenant_id AND later.site_id = event.site_id
            AND later.binding_id = event.binding_id AND later.event_kind <> 'bound')
     ORDER BY event.recorded_at DESC, event.id DESC LIMIT 1;
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v_origin record;
BEGIN PERFORM control.assert_weekly_skill_stage(p_port_hash,p_port_generation,p_site_id,ARRAY['import_gsc']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.admit_port_context('weekly_skill','brain',p_port_hash,p_port_generation,p_site_id,NULL)) THEN RAISE EXCEPTION 'skill_tenant_denied' USING ERRCODE='42501'; END IF;
    SELECT * INTO v_origin FROM control.verified_site_origin(p_tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT event.binding_id, event.property_resource_name,
        event.secret_reference, event.verified_origin
      FROM app.gsc_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_port_hash AND u->>'binding_id'=event.binding_id::text) AND event.event_kind = 'bound' AND event.verified_origin = v_origin.origin
       AND NOT EXISTS (SELECT 1 FROM app.gsc_binding_events AS later
          WHERE later.tenant_id = event.tenant_id AND later.site_id = event.site_id
            AND later.binding_id = event.binding_id AND later.event_kind <> 'bound')
     ORDER BY event.recorded_at DESC, event.id DESC LIMIT 1;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_current_gsc_binding(text,bytea,text,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_current_gsc_binding(text,bytea,text,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.current_gsc_binding(p_tenant_id uuid, p_site_id uuid)
 RETURNS TABLE(binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_current_gsc_binding('owner',NULL,NULL,p_tenant_id,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.current_gsc_binding(uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.current_gsc_binding(uuid,uuid) TO signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.weekly_skill_current_gsc_binding(p_hash bytea, p_generation text, p_tenant_id uuid, p_site_id uuid)
 RETURNS TABLE(binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_current_gsc_binding('weekly_skill',p_hash,p_generation,p_tenant_id,p_site_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_current_gsc_binding(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_current_gsc_binding(bytea,text,uuid,uuid) TO signal_workflow;
-- End port group 45.
-- finish_pagespeed: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_finish_pagespeed(p_actor text, p_session bytea, p_generation text, p_site uuid, p_sample uuid, p_observation jsonb, p_hash bytea, p_reason text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record; s app.pagespeed_sample%%ROWTYPE; e app.owner_connector_egress_operations%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.admit_port_context('owner','pagespeed',p_session,p_generation,p_site,NULL);
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
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE a record; s app.pagespeed_sample%%ROWTYPE; e app.owner_connector_egress_operations%%ROWTYPE;
BEGIN IF NOT EXISTS(SELECT 1 FROM control.weekly_skill_directory WHERE handle_hash=p_session AND site_id=p_site AND recovery_generation=p_generation) THEN RETURN 'denied'; END IF;
    SELECT * INTO a FROM control.admit_port_context('weekly_skill','pagespeed',p_session,p_generation,p_site,NULL);
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    SELECT * INTO s FROM app.pagespeed_sample WHERE tenant_id=a.tenant_id AND site_id=p_site
        AND id=p_sample AND verified_origin=a.origin AND EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session AND u->>'sample_id'=p_sample::text) FOR SHARE;
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
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_finish_pagespeed(text,bytea,text,uuid,uuid,jsonb,bytea,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_finish_pagespeed(text,bytea,text,uuid,uuid,jsonb,bytea,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.finish_pagespeed(p_session bytea, p_generation text, p_site uuid, p_sample uuid, p_observation jsonb, p_hash bytea, p_reason text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_pagespeed('owner',p_session,p_generation,p_site,p_sample,p_observation,p_hash,p_reason);
END;
$function$;

REVOKE ALL ON FUNCTION control.finish_pagespeed(bytea,text,uuid,uuid,jsonb,bytea,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.finish_pagespeed(bytea,text,uuid,uuid,jsonb,bytea,text) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_finish_pagespeed(p_session bytea, p_generation text, p_site uuid, p_sample uuid, p_observation jsonb, p_hash bytea, p_reason text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_pagespeed('weekly_skill',p_session,p_generation,p_site,p_sample,p_observation,p_hash,p_reason);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_finish_pagespeed(bytea,text,uuid,uuid,jsonb,bytea,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_finish_pagespeed(bytea,text,uuid,uuid,jsonb,bytea,text) TO signal_workflow;
-- End port group 46.
-- ga4_owner_binding: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_ga4_owner_binding(p_actor text, p_session bytea, p_generation text, p_site uuid)
 RETURNS TABLE(tenant_id uuid, binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE a record; BEGIN
 SELECT * INTO a FROM control.admit_port_context('owner','ga4',p_session,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN a.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN; END IF;
 RETURN QUERY SELECT a.tenant_id,b.binding_id,b.property_resource_name,b.secret_reference,b.origin
 FROM control.current_ga4_binding(a.tenant_id,p_site,p_generation) b;
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE a record; BEGIN PERFORM control.assert_weekly_skill_stage(p_session,p_generation,p_site,'{import_ga4}'::text[]);
 SELECT * INTO a FROM control.admit_port_context('weekly_skill','brain',p_session,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN a.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN; END IF;
 RETURN QUERY SELECT a.tenant_id,b.binding_id,b.property_resource_name,b.secret_reference,b.origin
 FROM control.current_ga4_binding(a.tenant_id,p_site,p_generation) b WHERE EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session AND u->>'binding_id'=b.binding_id::text);
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_ga4_owner_binding(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_ga4_owner_binding(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.ga4_owner_binding(p_session bytea, p_generation text, p_site uuid)
 RETURNS TABLE(tenant_id uuid, binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_ga4_owner_binding('owner',p_session,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.ga4_owner_binding(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.ga4_owner_binding(bytea,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_ga4_owner_binding(p_session bytea, p_generation text, p_site uuid)
 RETURNS TABLE(tenant_id uuid, binding_id uuid, property_resource_name text, secret_reference text, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_ga4_owner_binding('weekly_skill',p_session,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_ga4_owner_binding(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_ga4_owner_binding(bytea,text,uuid) TO signal_workflow;
-- End port group 47.
-- ga4_owner_reauth: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_ga4_owner_reauth(p_actor text, p_session bytea, p_generation text, p_site uuid, p_binding uuid, p_event uuid, p_egress uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE b record;a record; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session,p_generation,p_site,'{import_ga4}'::text[]);PERFORM control.assert_weekly_skill_resource(p_session,p_generation,p_site,'binding_id',p_binding);

END IF;
 SELECT * INTO a FROM control.admit_port_context(p_actor,'ga4',p_session,p_generation,p_site,NULL);
 SELECT * INTO b FROM control.port_ga4_owner_binding(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_skill' ELSE 'owner' END,p_session,p_generation,p_site);
 IF b.binding_id IS DISTINCT FROM p_binding THEN RETURN 'denied'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.egress_operations e WHERE e.tenant_id=b.tenant_id AND e.site_id=p_site AND e.id=p_egress
 AND e.state='observed' AND ((e.request_url='https://oauth2.googleapis.com/token' AND e.egress_profile='google_oauth_token' AND e.http_status IN (200,400,401,403))
 OR (e.request_url='https://analyticsdata.googleapis.com/v1beta/'||b.property_resource_name||':runReport' AND e.egress_profile='ga4_data' AND e.http_status IN (401,403)))) THEN RETURN 'evidence_unavailable'; END IF;
 RETURN control.restrict_ga4_binding(p_binding,p_event,'reauth_required',a.user_id);
END
$function$;

ALTER FUNCTION control.port_ga4_owner_reauth(text,bytea,text,uuid,uuid,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_ga4_owner_reauth(text,bytea,text,uuid,uuid,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.ga4_owner_reauth(p_session bytea, p_generation text, p_site uuid, p_binding uuid, p_event uuid, p_egress uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_ga4_owner_reauth('owner',p_session,p_generation,p_site,p_binding,p_event,p_egress);
END;
$function$;

REVOKE ALL ON FUNCTION control.ga4_owner_reauth(bytea,text,uuid,uuid,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.ga4_owner_reauth(bytea,text,uuid,uuid,uuid,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_ga4_owner_reauth(p_session bytea, p_generation text, p_site uuid, p_binding uuid, p_event uuid, p_egress uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_ga4_owner_reauth('weekly_skill',p_session,p_generation,p_site,p_binding,p_event,p_egress);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_ga4_owner_reauth(bytea,text,uuid,uuid,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_ga4_owner_reauth(bytea,text,uuid,uuid,uuid,uuid) TO signal_workflow;
-- End port group 48.
-- list_business_brain_facts: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_list_business_brain_facts(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid, p_status text DEFAULT NULL::text)
 RETURNS TABLE(fact_id uuid, category text, statement text, status text, source_kind text, page_evidence_id uuid, document_id uuid, extracted_range jsonb, owner_membership_id uuid, sensitive boolean, supersedes_id uuid, created_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session_hash,p_generation,p_site_id,'{brain_refresh,strategy_rebuild,brief_proposals}'::text[]);

END IF;
 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_session_hash,p_generation,p_site_id,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN; END IF;
 RETURN QUERY
 WITH current AS (
 SELECT f.*,CASE
 WHEN EXISTS(SELECT 1 FROM app.business_brain_facts n WHERE n.tenant_id=f.tenant_id AND n.site_id=f.site_id AND n.supersedes_id=f.id) THEN 'superseded'
 WHEN EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='removed') THEN 'removed'
 WHEN f.initial_status='approved' OR EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='approved') THEN 'approved' ELSE 'proposed' END AS view_status
 FROM app.business_brain_facts f WHERE f.tenant_id=v.tenant_id AND f.site_id=p_site_id)
 SELECT c.id,c.category,c.statement,c.view_status,c.source_kind,c.page_evidence_id,c.document_id,c.extracted_range,c.owner_membership_id,c.sensitive,c.supersedes_id,c.created_at FROM current c WHERE p_status IS NULL OR c.view_status=p_status ORDER BY c.created_at DESC,c.id DESC;
END;

$function$;

ALTER FUNCTION control.port_list_business_brain_facts(text,bytea,text,uuid,text) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_list_business_brain_facts(text,bytea,text,uuid,text) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.list_business_brain_facts(p_session_hash bytea, p_generation text, p_site_id uuid, p_status text DEFAULT NULL::text)
 RETURNS TABLE(fact_id uuid, category text, statement text, status text, source_kind text, page_evidence_id uuid, document_id uuid, extracted_range jsonb, owner_membership_id uuid, sensitive boolean, supersedes_id uuid, created_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_list_business_brain_facts('owner',p_session_hash,p_generation,p_site_id,p_status);
END;
$function$;

REVOKE ALL ON FUNCTION control.list_business_brain_facts(bytea,text,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.list_business_brain_facts(bytea,text,uuid,text) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_list_business_brain_facts(p_session_hash bytea, p_generation text, p_site_id uuid, p_status text DEFAULT NULL::text)
 RETURNS TABLE(fact_id uuid, category text, statement text, status text, source_kind text, page_evidence_id uuid, document_id uuid, extracted_range jsonb, owner_membership_id uuid, sensitive boolean, supersedes_id uuid, created_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_list_business_brain_facts('weekly_skill',p_session_hash,p_generation,p_site_id,p_status);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_list_business_brain_facts(bytea,text,uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_list_business_brain_facts(bytea,text,uuid,text) TO signal_workflow;
-- End port group 49.
-- mark_bing_reauth_required: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_mark_bing_reauth_required(p_actor text, p_port_hash bytea,p_port_generation text, p_tenant_id uuid, p_site_id uuid, p_binding_id uuid, p_event_id uuid, p_reason text, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_bound app.bing_binding_events%%ROWTYPE; v_egress record;
BEGIN
    IF p_reason NOT IN ('provider_revoked', 'reduced_scope', 'provider_rotated') THEN
        RAISE EXCEPTION 'invalid_bing_reauth_reason' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.state = 'observed'
       AND ((p_reason = 'provider_revoked' AND egress.http_status IN (400, 401, 403))
         OR (p_reason IN ('reduced_scope', 'provider_rotated') AND egress.http_status = 200))
       AND (egress.request_url = 'https://www.bing.com/webmasters/oauth/token'
         OR starts_with(egress.request_url, 'https://www.bing.com/webmaster/api.svc/json/'));
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    SELECT * INTO v_bound FROM app.bing_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.binding_id = p_binding_id AND event.event_kind = 'bound' FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable'; END IF;
    IF EXISTS (SELECT 1 FROM app.bing_binding_events AS event
       WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
         AND event.binding_id = p_binding_id AND event.event_kind <> 'bound')
    THEN RETURN 'already_restricted'; END IF;
    INSERT INTO app.bing_binding_events (
        tenant_id, site_id, id, binding_id, actor_user_id, event_kind,
        verified_origin, property_resource_name, secret_reference,
        granted_scope, reason
    ) VALUES (
        p_tenant_id, p_site_id, p_event_id, p_binding_id, v_bound.actor_user_id,
        'reauth_required', v_bound.verified_origin, v_bound.property_resource_name,
        v_bound.secret_reference, v_bound.granted_scope, p_reason
    );
    RETURN 'restricted';
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v_bound app.bing_binding_events%%ROWTYPE; v_egress record;
BEGIN PERFORM control.assert_weekly_skill_stage(p_port_hash,p_port_generation,p_site_id,ARRAY['import_bing']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.admit_port_context('weekly_skill','brain',p_port_hash,p_port_generation,p_site_id,NULL)) THEN RAISE EXCEPTION 'skill_tenant_denied' USING ERRCODE='42501'; END IF;PERFORM control.assert_weekly_skill_resource(p_port_hash,p_port_generation,p_site_id,'binding_id',p_binding_id);
    IF p_reason NOT IN ('provider_revoked', 'reduced_scope', 'provider_rotated') THEN
        RAISE EXCEPTION 'invalid_bing_reauth_reason' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.state = 'observed'
       AND ((p_reason = 'provider_revoked' AND egress.http_status IN (400, 401, 403))
         OR (p_reason IN ('reduced_scope', 'provider_rotated') AND egress.http_status = 200))
       AND (egress.request_url = 'https://www.bing.com/webmasters/oauth/token'
         OR starts_with(egress.request_url, 'https://www.bing.com/webmaster/api.svc/json/'));
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    SELECT * INTO v_bound FROM app.bing_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.binding_id = p_binding_id AND event.event_kind = 'bound' FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable'; END IF;
    IF EXISTS (SELECT 1 FROM app.bing_binding_events AS event
       WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
         AND event.binding_id = p_binding_id AND event.event_kind <> 'bound')
    THEN RETURN 'already_restricted'; END IF;
    INSERT INTO app.bing_binding_events (
        tenant_id, site_id, id, binding_id, actor_user_id, event_kind,
        verified_origin, property_resource_name, secret_reference,
        granted_scope, reason
    ) VALUES (
        p_tenant_id, p_site_id, p_event_id, p_binding_id, v_bound.actor_user_id,
        'reauth_required', v_bound.verified_origin, v_bound.property_resource_name,
        v_bound.secret_reference, v_bound.granted_scope, p_reason
    );
    RETURN 'restricted';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_mark_bing_reauth_required(text,bytea,text,uuid,uuid,uuid,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_mark_bing_reauth_required(text,bytea,text,uuid,uuid,uuid,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.mark_bing_reauth_required(p_tenant_id uuid, p_site_id uuid, p_binding_id uuid, p_event_id uuid, p_reason text, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_mark_bing_reauth_required('owner',NULL,NULL,p_tenant_id,p_site_id,p_binding_id,p_event_id,p_reason,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.mark_bing_reauth_required(uuid,uuid,uuid,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.mark_bing_reauth_required(uuid,uuid,uuid,uuid,text,uuid) TO signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.weekly_skill_mark_bing_reauth_required(p_hash bytea, p_generation text, p_tenant_id uuid, p_site_id uuid, p_binding_id uuid, p_event_id uuid, p_reason text, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_mark_bing_reauth_required('weekly_skill',p_hash,p_generation,p_tenant_id,p_site_id,p_binding_id,p_event_id,p_reason,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_mark_bing_reauth_required(bytea,text,uuid,uuid,uuid,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_mark_bing_reauth_required(bytea,text,uuid,uuid,uuid,uuid,text,uuid) TO signal_workflow;
-- End port group 50.
-- mark_gsc_reauth_required: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_mark_gsc_reauth_required(p_actor text, p_port_hash bytea,p_port_generation text, p_tenant_id uuid, p_site_id uuid, p_binding_id uuid, p_event_id uuid, p_reason text, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_bound app.gsc_binding_events%%ROWTYPE; v_egress record;
BEGIN
    IF p_reason NOT IN ('provider_revoked', 'reduced_scope', 'provider_rotated') THEN
        RAISE EXCEPTION 'invalid_gsc_reauth_reason' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.state = 'observed'
       AND ((p_reason = 'provider_revoked' AND egress.http_status IN (400, 401, 403))
         OR (p_reason IN ('reduced_scope', 'provider_rotated') AND egress.http_status = 200))
       AND (egress.request_url = 'https://oauth2.googleapis.com/token'
         OR starts_with(egress.request_url, 'https://www.googleapis.com/webmasters/v3/sites/'));
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    SELECT * INTO v_bound FROM app.gsc_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.binding_id = p_binding_id AND event.event_kind = 'bound' FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable'; END IF;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS event
       WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
         AND event.binding_id = p_binding_id AND event.event_kind <> 'bound')
    THEN RETURN 'already_restricted'; END IF;
    INSERT INTO app.gsc_binding_events (
        tenant_id, site_id, id, binding_id, actor_user_id, event_kind,
        verified_origin, property_resource_name, property_type, secret_reference,
        granted_scope, reason
    ) VALUES (
        p_tenant_id, p_site_id, p_event_id, p_binding_id, v_bound.actor_user_id,
        'reauth_required', v_bound.verified_origin, v_bound.property_resource_name,
        v_bound.property_type, v_bound.secret_reference, v_bound.granted_scope, p_reason
    );
    RETURN 'restricted';
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v_bound app.gsc_binding_events%%ROWTYPE; v_egress record;
BEGIN PERFORM control.assert_weekly_skill_stage(p_port_hash,p_port_generation,p_site_id,ARRAY['import_gsc']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.admit_port_context('weekly_skill','brain',p_port_hash,p_port_generation,p_site_id,NULL)) THEN RAISE EXCEPTION 'skill_tenant_denied' USING ERRCODE='42501'; END IF;PERFORM control.assert_weekly_skill_resource(p_port_hash,p_port_generation,p_site_id,'binding_id',p_binding_id);
    IF p_reason NOT IN ('provider_revoked', 'reduced_scope', 'provider_rotated') THEN
        RAISE EXCEPTION 'invalid_gsc_reauth_reason' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.state = 'observed'
       AND ((p_reason = 'provider_revoked' AND egress.http_status IN (400, 401, 403))
         OR (p_reason IN ('reduced_scope', 'provider_rotated') AND egress.http_status = 200))
       AND (egress.request_url = 'https://oauth2.googleapis.com/token'
         OR starts_with(egress.request_url, 'https://www.googleapis.com/webmasters/v3/sites/'));
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    SELECT * INTO v_bound FROM app.gsc_binding_events AS event
     WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
       AND event.binding_id = p_binding_id AND event.event_kind = 'bound' FOR UPDATE;
    IF NOT FOUND THEN RETURN 'unavailable'; END IF;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS event
       WHERE event.tenant_id = p_tenant_id AND event.site_id = p_site_id
         AND event.binding_id = p_binding_id AND event.event_kind <> 'bound')
    THEN RETURN 'already_restricted'; END IF;
    INSERT INTO app.gsc_binding_events (
        tenant_id, site_id, id, binding_id, actor_user_id, event_kind,
        verified_origin, property_resource_name, property_type, secret_reference,
        granted_scope, reason
    ) VALUES (
        p_tenant_id, p_site_id, p_event_id, p_binding_id, v_bound.actor_user_id,
        'reauth_required', v_bound.verified_origin, v_bound.property_resource_name,
        v_bound.property_type, v_bound.secret_reference, v_bound.granted_scope, p_reason
    );
    RETURN 'restricted';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_mark_gsc_reauth_required(text,bytea,text,uuid,uuid,uuid,uuid,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_mark_gsc_reauth_required(text,bytea,text,uuid,uuid,uuid,uuid,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.mark_gsc_reauth_required(p_tenant_id uuid, p_site_id uuid, p_binding_id uuid, p_event_id uuid, p_reason text, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_mark_gsc_reauth_required('owner',NULL,NULL,p_tenant_id,p_site_id,p_binding_id,p_event_id,p_reason,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.mark_gsc_reauth_required(uuid,uuid,uuid,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.mark_gsc_reauth_required(uuid,uuid,uuid,uuid,text,uuid) TO signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.weekly_skill_mark_gsc_reauth_required(p_hash bytea, p_generation text, p_tenant_id uuid, p_site_id uuid, p_binding_id uuid, p_event_id uuid, p_reason text, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_mark_gsc_reauth_required('weekly_skill',p_hash,p_generation,p_tenant_id,p_site_id,p_binding_id,p_event_id,p_reason,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_mark_gsc_reauth_required(bytea,text,uuid,uuid,uuid,uuid,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_mark_gsc_reauth_required(bytea,text,uuid,uuid,uuid,uuid,text,uuid) TO signal_workflow;
-- End port group 51.
-- read_brand_document_artifact: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_read_brand_document_artifact(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid, p_document_id uuid)
 RETURNS TABLE(tenant_id uuid, artifact_id uuid, object_key text, object_version text, artifact_hash bytea, byte_length bigint, media_type text, encryption_key_ref text, artifact_created_at timestamp with time zone, retain_until timestamp with time zone, legal_hold boolean, durability_state text, text_hash bytea, injection_signal boolean, secret_signal boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v_owner record;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session_hash,p_generation,p_site_id,'{brain_refresh}'::text[]);PERFORM control.assert_weekly_skill_resource(p_session_hash,p_generation,p_site_id,'source_id',p_document_id);

END IF;
    SELECT * INTO v_owner FROM control.admit_port_context(p_actor,'brand',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(CASE WHEN v_owner.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN; END IF;
    RETURN QUERY SELECT d.tenant_id, a.id, a.object_key, a.object_version, a.sha256,
        a.byte_length, a.media_type, a.encryption_key_ref, a.created_at,
        a.retain_until, a.legal_hold, a.durability_state, d.text_sha256,
        d.injection_signal, d.secret_signal
      FROM app.brand_documents d JOIN app.artifacts a ON a.tenant_id = d.tenant_id
        AND a.site_id = d.site_id AND a.id = d.artifact_id
     WHERE d.tenant_id = v_owner.tenant_id AND d.site_id = p_site_id AND d.id = p_document_id
       AND NOT EXISTS (SELECT 1 FROM app.brand_document_events e
         WHERE e.tenant_id = d.tenant_id AND e.site_id = d.site_id AND e.document_id = d.id
           AND e.event_type = 'deleted')
       AND NOT control.docs_document_withdrawn(d.tenant_id,d.site_id,d.id) AND NOT EXISTS (SELECT 1 FROM app.brand_documents next
         WHERE next.tenant_id = d.tenant_id AND next.site_id = d.site_id
           AND next.supersedes_id = d.id);
END;

$function$;

ALTER FUNCTION control.port_read_brand_document_artifact(text,bytea,text,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_read_brand_document_artifact(text,bytea,text,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.read_brand_document_artifact(p_session_hash bytea, p_generation text, p_site_id uuid, p_document_id uuid)
 RETURNS TABLE(tenant_id uuid, artifact_id uuid, object_key text, object_version text, artifact_hash bytea, byte_length bigint, media_type text, encryption_key_ref text, artifact_created_at timestamp with time zone, retain_until timestamp with time zone, legal_hold boolean, durability_state text, text_hash bytea, injection_signal boolean, secret_signal boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_brand_document_artifact('owner',p_session_hash,p_generation,p_site_id,p_document_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.read_brand_document_artifact(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.read_brand_document_artifact(bytea,text,uuid,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_read_brand_document_artifact(p_session_hash bytea, p_generation text, p_site_id uuid, p_document_id uuid)
 RETURNS TABLE(tenant_id uuid, artifact_id uuid, object_key text, object_version text, artifact_hash bytea, byte_length bigint, media_type text, encryption_key_ref text, artifact_created_at timestamp with time zone, retain_until timestamp with time zone, legal_hold boolean, durability_state text, text_hash bytea, injection_signal boolean, secret_signal boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_read_brand_document_artifact('weekly_skill',p_session_hash,p_generation,p_site_id,p_document_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_read_brand_document_artifact(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_read_brand_document_artifact(bytea,text,uuid,uuid) TO signal_workflow;
-- End port group 52.
-- record_bing_import_generation: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_record_bing_import_generation(p_actor text, p_port_hash bytea,p_port_generation text, p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_kind text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_binding record; v_egress record; v_method text;
BEGIN
    IF p_id IS NULL OR p_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_kind NOT IN (
           'performance', 'own_site_inbound_link_counts', 'own_site_inbound_link_details'
       )
       OR jsonb_typeof(p_rows) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_rows) > 5000
       OR jsonb_typeof(p_coverage) IS DISTINCT FROM 'object'
       OR p_coverage->>'complete' IS DISTINCT FROM 'false'
       OR p_coverage->>'missing_data' IS DISTINCT FROM 'unknown'
       OR p_coverage->>'source' IS DISTINCT FROM 'bing_webmaster'
       OR octet_length(p_response_sha256) IS DISTINCT FROM 32
    THEN RAISE EXCEPTION 'invalid_bing_import' USING ERRCODE = '22023'; END IF;
    v_method := CASE p_kind WHEN 'performance' THEN 'GetRankAndTrafficStats'
        WHEN 'own_site_inbound_link_counts' THEN 'GetLinkCounts'
        ELSE 'GetUrlLinks' END;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_binding FROM control.current_bing_binding(p_tenant_id, p_site_id);
    IF NOT FOUND OR v_binding.binding_id <> p_binding_id
       OR v_binding.property_resource_name <> p_property
    THEN RETURN 'binding_unavailable'; END IF;
    PERFORM 1 FROM app.bing_binding_events AS bound
     WHERE bound.tenant_id = p_tenant_id AND bound.site_id = p_site_id
       AND bound.binding_id = p_binding_id AND bound.event_kind = 'bound' FOR UPDATE;
    IF EXISTS (SELECT 1 FROM app.bing_binding_events AS restriction
       WHERE restriction.tenant_id = p_tenant_id AND restriction.site_id = p_site_id
         AND restriction.binding_id = p_binding_id AND restriction.event_kind <> 'bound')
    THEN RETURN 'binding_unavailable'; END IF;
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.method = 'GET' AND egress.state = 'observed'
       AND egress.http_status = 200 AND egress.response_sha256 = p_response_sha256
       AND starts_with(egress.request_url,
           'https://www.bing.com/webmaster/api.svc/json/' || v_method || '?');
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
ELSIF p_actor='weekly_skill' THEN
DECLARE v_binding record; v_egress record; v_method text;
BEGIN PERFORM control.assert_weekly_skill_stage(p_port_hash,p_port_generation,p_site_id,ARRAY['import_bing']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.admit_port_context('weekly_skill','brain',p_port_hash,p_port_generation,p_site_id,NULL)) THEN RAISE EXCEPTION 'skill_tenant_denied' USING ERRCODE='42501'; END IF;PERFORM control.assert_weekly_skill_resource(p_port_hash,p_port_generation,p_site_id,'binding_id',p_binding_id);
    IF p_id IS NULL OR p_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_kind NOT IN (
           'performance', 'own_site_inbound_link_counts', 'own_site_inbound_link_details'
       )
       OR jsonb_typeof(p_rows) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_rows) > 5000
       OR jsonb_typeof(p_coverage) IS DISTINCT FROM 'object'
       OR p_coverage->>'complete' IS DISTINCT FROM 'false'
       OR p_coverage->>'missing_data' IS DISTINCT FROM 'unknown'
       OR p_coverage->>'source' IS DISTINCT FROM 'bing_webmaster'
       OR octet_length(p_response_sha256) IS DISTINCT FROM 32
    THEN RAISE EXCEPTION 'invalid_bing_import' USING ERRCODE = '22023'; END IF;
    v_method := CASE p_kind WHEN 'performance' THEN 'GetRankAndTrafficStats'
        WHEN 'own_site_inbound_link_counts' THEN 'GetLinkCounts'
        ELSE 'GetUrlLinks' END;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_binding FROM control.current_bing_binding(p_tenant_id, p_site_id);
    IF NOT FOUND OR v_binding.binding_id <> p_binding_id
       OR v_binding.property_resource_name <> p_property
    THEN RETURN 'binding_unavailable'; END IF;
    PERFORM 1 FROM app.bing_binding_events AS bound
     WHERE bound.tenant_id = p_tenant_id AND bound.site_id = p_site_id
       AND bound.binding_id = p_binding_id AND bound.event_kind = 'bound' FOR UPDATE;
    IF EXISTS (SELECT 1 FROM app.bing_binding_events AS restriction
       WHERE restriction.tenant_id = p_tenant_id AND restriction.site_id = p_site_id
         AND restriction.binding_id = p_binding_id AND restriction.event_kind <> 'bound')
    THEN RETURN 'binding_unavailable'; END IF;
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.method = 'GET' AND egress.state = 'observed'
       AND egress.http_status = 200 AND egress.response_sha256 = p_response_sha256
       AND starts_with(egress.request_url,
           'https://www.bing.com/webmaster/api.svc/json/' || v_method || '?');
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
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_record_bing_import_generation(text,bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_record_bing_import_generation(text,bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.record_bing_import_generation(p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_kind text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_bing_import_generation('owner',NULL,NULL,p_tenant_id,p_site_id,p_id,p_binding_id,p_property,p_kind,p_rows,p_coverage,p_response_sha256,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.record_bing_import_generation(uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.record_bing_import_generation(uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) TO signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.weekly_skill_record_bing_import_generation(p_hash bytea, p_generation text, p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_kind text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_bing_import_generation('weekly_skill',p_hash,p_generation,p_tenant_id,p_site_id,p_id,p_binding_id,p_property,p_kind,p_rows,p_coverage,p_response_sha256,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_record_bing_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_record_bing_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) TO signal_workflow;
-- End port group 53.
-- record_bing_page_import_generation: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_record_bing_page_import_generation(p_actor text, p_port_hash bytea,p_port_generation text, p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_kind text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
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
ELSIF p_actor='weekly_skill' THEN
DECLARE v_binding record; v_row jsonb; v_field text; v_date text; v_ms numeric;
BEGIN PERFORM control.assert_weekly_skill_stage(p_port_hash,p_port_generation,p_site_id,ARRAY['import_bing']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.admit_port_context('weekly_skill','brain',p_port_hash,p_port_generation,p_site_id,NULL)) THEN RAISE EXCEPTION 'skill_tenant_denied' USING ERRCODE='42501'; END IF; PERFORM control.assert_weekly_skill_resource(p_port_hash,p_port_generation,p_site_id,'binding_id',p_binding_id);
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
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_record_bing_page_import_generation(text,bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_record_bing_page_import_generation(text,bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.record_bing_page_import_generation(p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_kind text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_bing_page_import_generation('owner',NULL,NULL,p_tenant_id,p_site_id,p_id,p_binding_id,p_property,p_kind,p_rows,p_coverage,p_response_sha256,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.record_bing_page_import_generation(uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.record_bing_page_import_generation(uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) TO signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.weekly_skill_record_bing_page_import_generation(p_hash bytea, p_generation text, p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_kind text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_bing_page_import_generation('weekly_skill',p_hash,p_generation,p_tenant_id,p_site_id,p_id,p_binding_id,p_property,p_kind,p_rows,p_coverage,p_response_sha256,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_record_bing_page_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_record_bing_page_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,jsonb,bytea,uuid) TO signal_workflow;
-- End port group 54.
-- record_ga4_owner_import: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_record_ga4_owner_import(p_actor text, p_session bytea, p_generation text, p_site uuid, p_id uuid, p_binding uuid, p_property text, p_start date, p_end date, p_rows jsonb, p_coverage jsonb, p_receipts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE a record; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_session,p_generation,p_site,'{import_ga4}'::text[]);PERFORM control.assert_weekly_skill_resource(p_session,p_generation,p_site,'binding_id',p_binding);

END IF;
 SELECT * INTO a FROM control.admit_port_context(p_actor,'ga4',p_session,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN a.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.ga4_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_binding FOR UPDATE;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 RETURN control.record_ga4_import_generation(a.tenant_id,p_site,p_generation,p_id,p_binding,p_property,p_start,p_end,p_rows,p_coverage,p_receipts);
END
$function$;

ALTER FUNCTION control.port_record_ga4_owner_import(text,bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_record_ga4_owner_import(text,bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.record_ga4_owner_import(p_session bytea, p_generation text, p_site uuid, p_id uuid, p_binding uuid, p_property text, p_start date, p_end date, p_rows jsonb, p_coverage jsonb, p_receipts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_ga4_owner_import('owner',p_session,p_generation,p_site,p_id,p_binding,p_property,p_start,p_end,p_rows,p_coverage,p_receipts);
END;
$function$;

REVOKE ALL ON FUNCTION control.record_ga4_owner_import(bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.record_ga4_owner_import(bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_record_ga4_owner_import(p_session bytea, p_generation text, p_site uuid, p_id uuid, p_binding uuid, p_property text, p_start date, p_end date, p_rows jsonb, p_coverage jsonb, p_receipts jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_ga4_owner_import('weekly_skill',p_session,p_generation,p_site,p_id,p_binding,p_property,p_start,p_end,p_rows,p_coverage,p_receipts);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_record_ga4_owner_import(bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_record_ga4_owner_import(bytea,text,uuid,uuid,uuid,text,date,date,jsonb,jsonb,jsonb) TO signal_workflow;
-- End port group 55.
-- record_gsc_import_generation: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_record_gsc_import_generation(p_actor text, p_port_hash bytea,p_port_generation text, p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_search_type text, p_dimensions jsonb, p_start_date date, p_end_date date, p_data_state text, p_aggregation_type text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_binding record; v_egress record;
BEGIN
    IF p_id IS NULL OR p_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_search_type NOT IN ('web', 'image', 'video', 'news', 'discover')
       OR p_data_state NOT IN ('all', 'final')
       OR p_aggregation_type NOT IN ('auto', 'byPage', 'byProperty')
       OR p_start_date IS NULL OR p_end_date IS NULL
       OR p_end_date < p_start_date OR p_end_date - p_start_date > 92
       OR jsonb_typeof(p_rows) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_rows) > 5000
       OR jsonb_typeof(p_coverage) IS DISTINCT FROM 'object'
       OR p_coverage->>'complete' IS DISTINCT FROM 'false'
       OR p_coverage->>'missing_data' IS NULL
       OR p_coverage->'filters' IS DISTINCT FROM '[]'::jsonb
       OR p_coverage->>'start_row' IS DISTINCT FROM '0'
       OR p_coverage->>'row_limit' IS DISTINCT FROM '5000'
       OR octet_length(p_response_sha256) IS DISTINCT FROM 32
    THEN RAISE EXCEPTION 'invalid_gsc_import' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_binding FROM control.current_gsc_binding(p_tenant_id, p_site_id);
    IF NOT FOUND OR v_binding.binding_id <> p_binding_id
       OR v_binding.property_resource_name <> p_property
    THEN RETURN 'binding_unavailable'; END IF;
    PERFORM 1 FROM app.gsc_binding_events AS bound
     WHERE bound.tenant_id = p_tenant_id AND bound.site_id = p_site_id
       AND bound.binding_id = p_binding_id AND bound.event_kind = 'bound' FOR UPDATE;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS restriction
       WHERE restriction.tenant_id = p_tenant_id AND restriction.site_id = p_site_id
         AND restriction.binding_id = p_binding_id AND restriction.event_kind <> 'bound')
    THEN RETURN 'binding_unavailable'; END IF;
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.method = 'POST' AND egress.state = 'observed'
       AND egress.http_status = 200 AND egress.response_sha256 = p_response_sha256
       AND starts_with(egress.request_url, 'https://www.googleapis.com/webmasters/v3/sites/')
       AND right(egress.request_url, length('/searchAnalytics/query')) = '/searchAnalytics/query';
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    INSERT INTO app.gsc_import_generations (
        tenant_id, site_id, id, binding_id, property_resource_name,
        search_type, dimensions, start_date, end_date, data_state,
        aggregation_type, rows, coverage, response_sha256, egress_operation_id
    ) VALUES (
        p_tenant_id, p_site_id, p_id, p_binding_id, p_property,
        p_search_type, p_dimensions, p_start_date, p_end_date, p_data_state,
        p_aggregation_type, p_rows, p_coverage, p_response_sha256, p_egress_operation_id
    );
    RETURN 'recorded';
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v_binding record; v_egress record;
BEGIN PERFORM control.assert_weekly_skill_stage(p_port_hash,p_port_generation,p_site_id,ARRAY['import_gsc']); IF p_tenant_id IS DISTINCT FROM (SELECT tenant_id FROM control.admit_port_context('weekly_skill','brain',p_port_hash,p_port_generation,p_site_id,NULL)) THEN RAISE EXCEPTION 'skill_tenant_denied' USING ERRCODE='42501'; END IF;PERFORM control.assert_weekly_skill_resource(p_port_hash,p_port_generation,p_site_id,'binding_id',p_binding_id);
    IF p_id IS NULL OR p_id::text !~
         '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_search_type NOT IN ('web', 'image', 'video', 'news', 'discover')
       OR p_data_state NOT IN ('all', 'final')
       OR p_aggregation_type NOT IN ('auto', 'byPage', 'byProperty')
       OR p_start_date IS NULL OR p_end_date IS NULL
       OR p_end_date < p_start_date OR p_end_date - p_start_date > 92
       OR jsonb_typeof(p_rows) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_rows) > 5000
       OR jsonb_typeof(p_coverage) IS DISTINCT FROM 'object'
       OR p_coverage->>'complete' IS DISTINCT FROM 'false'
       OR p_coverage->>'missing_data' IS NULL
       OR p_coverage->'filters' IS DISTINCT FROM '[]'::jsonb
       OR p_coverage->>'start_row' IS DISTINCT FROM '0'
       OR p_coverage->>'row_limit' IS DISTINCT FROM '5000'
       OR octet_length(p_response_sha256) IS DISTINCT FROM 32
    THEN RAISE EXCEPTION 'invalid_gsc_import' USING ERRCODE = '22023'; END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO v_binding FROM control.current_gsc_binding(p_tenant_id, p_site_id);
    IF NOT FOUND OR v_binding.binding_id <> p_binding_id
       OR v_binding.property_resource_name <> p_property
    THEN RETURN 'binding_unavailable'; END IF;
    PERFORM 1 FROM app.gsc_binding_events AS bound
     WHERE bound.tenant_id = p_tenant_id AND bound.site_id = p_site_id
       AND bound.binding_id = p_binding_id AND bound.event_kind = 'bound' FOR UPDATE;
    IF EXISTS (SELECT 1 FROM app.gsc_binding_events AS restriction
       WHERE restriction.tenant_id = p_tenant_id AND restriction.site_id = p_site_id
         AND restriction.binding_id = p_binding_id AND restriction.event_kind <> 'bound')
    THEN RETURN 'binding_unavailable'; END IF;
    SELECT egress.id INTO v_egress FROM app.egress_operations AS egress
     WHERE egress.tenant_id = p_tenant_id AND egress.site_id = p_site_id
       AND egress.id = p_egress_operation_id AND egress.purpose = 'connector'
       AND egress.method = 'POST' AND egress.state = 'observed'
       AND egress.http_status = 200 AND egress.response_sha256 = p_response_sha256
       AND starts_with(egress.request_url, 'https://www.googleapis.com/webmasters/v3/sites/')
       AND right(egress.request_url, length('/searchAnalytics/query')) = '/searchAnalytics/query';
    IF NOT FOUND THEN RETURN 'egress_evidence_unavailable'; END IF;
    INSERT INTO app.gsc_import_generations (
        tenant_id, site_id, id, binding_id, property_resource_name,
        search_type, dimensions, start_date, end_date, data_state,
        aggregation_type, rows, coverage, response_sha256, egress_operation_id
    ) VALUES (
        p_tenant_id, p_site_id, p_id, p_binding_id, p_property,
        p_search_type, p_dimensions, p_start_date, p_end_date, p_data_state,
        p_aggregation_type, p_rows, p_coverage, p_response_sha256, p_egress_operation_id
    );
    RETURN 'recorded';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_record_gsc_import_generation(text,bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,date,date,text,text,jsonb,jsonb,bytea,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_record_gsc_import_generation(text,bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,date,date,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.record_gsc_import_generation(p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_search_type text, p_dimensions jsonb, p_start_date date, p_end_date date, p_data_state text, p_aggregation_type text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_gsc_import_generation('owner',NULL,NULL,p_tenant_id,p_site_id,p_id,p_binding_id,p_property,p_search_type,p_dimensions,p_start_date,p_end_date,p_data_state,p_aggregation_type,p_rows,p_coverage,p_response_sha256,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.record_gsc_import_generation(uuid,uuid,uuid,uuid,text,text,jsonb,date,date,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.record_gsc_import_generation(uuid,uuid,uuid,uuid,text,text,jsonb,date,date,text,text,jsonb,jsonb,bytea,uuid) TO signal_crawl_ingest;

CREATE OR REPLACE FUNCTION control.weekly_skill_record_gsc_import_generation(p_hash bytea, p_generation text, p_tenant_id uuid, p_site_id uuid, p_id uuid, p_binding_id uuid, p_property text, p_search_type text, p_dimensions jsonb, p_start_date date, p_end_date date, p_data_state text, p_aggregation_type text, p_rows jsonb, p_coverage jsonb, p_response_sha256 bytea, p_egress_operation_id uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_record_gsc_import_generation('weekly_skill',p_hash,p_generation,p_tenant_id,p_site_id,p_id,p_binding_id,p_property,p_search_type,p_dimensions,p_start_date,p_end_date,p_data_state,p_aggregation_type,p_rows,p_coverage,p_response_sha256,p_egress_operation_id);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_record_gsc_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,date,date,text,text,jsonb,jsonb,bytea,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_record_gsc_import_generation(bytea,text,uuid,uuid,uuid,uuid,text,text,jsonb,date,date,text,text,jsonb,jsonb,bytea,uuid) TO signal_workflow;
-- End port group 56.
-- seo_strategy_base_sources: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_seo_strategy_base_sources(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v record; m record; a record; g record; b record; q uuid; facts jsonb; pages jsonb;
    gs jsonb; bs jsonb; observations jsonb; recipes jsonb; inbox jsonb;
BEGIN
    SELECT * INTO v FROM control.admit_port_context('owner','brain',p_hash,p_generation,p_site,NULL);
    IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
    SELECT * INTO m FROM app.crawl_manifests x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.completed_at DESC,x.id DESC LIMIT 1;
    SELECT * INTO a FROM app.crawl_audit_reports x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND x.manifest_id=m.id ORDER BY x.analyzed_at DESC,x.id DESC LIMIT 1;
    SELECT binding_id,event_kind INTO g FROM app.gsc_binding_events x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1;
    SELECT binding_id,event_kind INTO b FROM app.bing_binding_events x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1;
    SELECT x.id INTO q FROM app.ai_visibility_question_sets x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND NOT EXISTS(SELECT 1 FROM app.ai_visibility_question_sets n WHERE n.tenant_id=x.tenant_id AND n.site_id=x.site_id AND n.supersedes_id=x.id)
        ORDER BY x.created_at DESC,x.id DESC LIMIT 1;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.fact_id,'category',x.category,'statement',x.statement,
        'source_kind',x.source_kind,'page_evidence_id',x.page_evidence_id,'document_id',x.document_id,
        'owner_membership_id',x.owner_membership_id,'created_at',x.created_at,'sensitive',f.sensitive) ORDER BY x.fact_id),'[]'::jsonb)
        INTO facts FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) x
        JOIN app.business_brain_facts f ON f.tenant_id=v.tenant_id AND f.site_id=p_site AND f.id=x.fact_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',p.id,'url',u.fetch_url,'title',coalesce(p.title,'')) ORDER BY p.id),'[]'::jsonb)
        INTO pages FROM app.crawl_page_records p JOIN app.urls u ON u.tenant_id=p.tenant_id AND u.site_id=p.site_id AND u.id=p.url_id
        WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.crawl_run_id=m.crawl_run_id AND p.http_status BETWEEN 200 AND 299;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'dimensions',x.dimensions,'rows',x.rows,'coverage',x.coverage,
        'window',jsonb_build_object('start',x.start_date,'end',x.end_date),'imported_at',x.imported_at,
        'aggregation_type',x.aggregation_type) ORDER BY x.dimensions),'[]'::jsonb) INTO gs
        FROM (SELECT DISTINCT ON (dimensions) * FROM app.gsc_import_generations
            WHERE tenant_id=v.tenant_id AND site_id=p_site AND binding_id=g.binding_id AND g.event_kind='bound'
              AND search_type='web' AND data_state='final'
            ORDER BY dimensions,imported_at DESC,id DESC) x;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'rows',x.rows,'coverage',x.coverage,
        'window',NULL,'imported_at',x.imported_at)),'[]'::jsonb) INTO bs
        FROM (SELECT * FROM app.bing_import_generations WHERE tenant_id=v.tenant_id AND site_id=p_site
            AND binding_id=b.binding_id AND b.event_kind='bound' AND kind='performance'
            ORDER BY imported_at DESC,id DESC LIMIT 1) x;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',o.id,'question_id',x.id,'question',x.question,
        'provider',o.provider,'model',o.model,'status',o.status,'provider_evidence_id',o.provider_evidence_id,
        'cited_pages',o.cited_pages,'other_domains',o.other_domains,'observed_at',o.observed_at,'usage',o.usage)
        ORDER BY o.question_id,o.provider),'[]'::jsonb) INTO observations
        FROM (SELECT DISTINCT ON (z.question_id,z.provider) z.* FROM app.ai_visibility_observations z
            JOIN app.ai_visibility_questions y ON y.tenant_id=z.tenant_id AND y.site_id=z.site_id AND y.id=z.question_id
            WHERE z.tenant_id=v.tenant_id AND z.site_id=p_site AND y.question_set_id=q
            ORDER BY z.question_id,z.provider,z.observed_at DESC,z.id DESC) o
        JOIN app.ai_visibility_questions x ON x.tenant_id=o.tenant_id AND x.site_id=o.site_id AND x.id=o.question_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',r.id,'recipe_key',r.recipe_key,
        'autonomy_eligible',control.recipe_autonomy_eligible(r.id)) ORDER BY r.recipe_key),'[]'::jsonb) INTO recipes
        FROM (SELECT DISTINCT ON (x.recipe_key) x.* FROM control.recipe_releases x
            WHERE (SELECT e.status FROM control.recipe_release_events e WHERE e.release_id=x.id ORDER BY e.sequence_number DESC LIMIT 1)='REVIEWED'
            ORDER BY x.recipe_key,x.version_major DESC,x.version_minor DESC,x.version_patch DESC) r;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.revision_id,'finding_id',x.canonical_manifest->>'finding_id') ORDER BY x.revision_id),'[]'::jsonb)
        INTO inbox FROM control.read_authenticated_candidate_recipe_inbox(p_hash,p_site,p_generation) x WHERE x.review_status='pending';
    RETURN jsonb_build_object(
        'crawl',jsonb_build_object('reason',CASE WHEN m.id IS NULL THEN 'No completed crawl evidence.' END,'records',CASE WHEN m.id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object('id',m.id,'coverage',m.coverage,'discovered_count',m.discovered_count,'terminal_count',m.terminal_count,'state_counts',m.state_counts,'completed_at',m.completed_at)) END),
        'technical',jsonb_build_object('reason',CASE WHEN a.id IS NULL THEN 'No technical report for the pinned crawl.' END,'records',CASE WHEN a.id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object('id',a.id,'manifest_id',a.manifest_id,'findings',a.findings,'coverage',a.coverage,'analyzed_at',a.analyzed_at)) END),
        'gsc',jsonb_build_object('reason',CASE WHEN g.event_kind IS DISTINCT FROM 'bound' THEN 'GSC unbound, revoked or reauthorization required.' WHEN gs='[]'::jsonb THEN 'No final web-performance import.' END,'records',gs),
        'bing',jsonb_build_object('reason',CASE WHEN b.event_kind IS DISTINCT FROM 'bound' THEN 'Bing unbound, revoked or reauthorization required.' WHEN bs='[]'::jsonb THEN 'No performance import.' END,'records',bs),
        'ai_visibility',jsonb_build_object('reason',CASE WHEN observations='[]'::jsonb THEN 'No AI-visibility observations.' WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(observations) o WHERE o->>'status'='incomplete') THEN 'Incomplete provider observations; missing citations are not zero.' END,'records',observations),
        'business_brain',jsonb_build_object('reason',CASE WHEN facts='[]'::jsonb THEN 'No current approved business facts.' END,'records',facts),
        'content',jsonb_build_object('reason',CASE WHEN pages='[]'::jsonb THEN 'No successful pages in the pinned crawl.' END,'records',pages),
        'recipes',recipes,'inbox',inbox,
        'page_inventory',coalesce((SELECT jsonb_agg(jsonb_build_object('id',coalesce(p.id,s.id),'url',u.fetch_url,'title',coalesce(p.title,''),'state',s.terminal_state) ORDER BY u.fetch_url)
            FROM app.crawl_frontier f JOIN app.urls u ON u.tenant_id=f.tenant_id AND u.site_id=f.site_id AND u.id=f.url_id
            JOIN app.crawl_frontier_settlements s ON s.tenant_id=f.tenant_id AND s.site_id=f.site_id AND s.frontier_id=f.id
            LEFT JOIN app.crawl_page_records p ON p.tenant_id=f.tenant_id AND p.site_id=f.site_id AND p.frontier_id=f.id
            WHERE f.tenant_id=v.tenant_id AND f.site_id=p_site AND f.crawl_run_id=m.crawl_run_id),'[]'::jsonb),
        'writer_inventory',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.brief_id,'status',x.status,'kind',x.payload->>'kind','created_at',x.created_at) ORDER BY x.brief_id)
            FROM jsonb_to_recordset(control.content_writer_read(p_hash,p_generation,p_site)->'briefs') x(brief_id uuid,status text,payload jsonb,created_at timestamptz)),'[]'::jsonb)
    );
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v record; m record; a record; g record; b record; q uuid; facts jsonb; pages jsonb;
    gs jsonb; bs jsonb; observations jsonb; recipes jsonb; inbox jsonb;
BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{strategy_rebuild}'::text[]);
    SELECT * INTO v FROM control.admit_port_context('weekly_skill','brain',p_hash,p_generation,p_site,NULL);
    IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
    SELECT * INTO m FROM app.crawl_manifests x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.completed_at DESC,x.id DESC LIMIT 1;
    SELECT * INTO a FROM app.crawl_audit_reports x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND x.manifest_id=m.id ORDER BY x.analyzed_at DESC,x.id DESC LIMIT 1;
    SELECT binding_id,event_kind INTO g FROM app.gsc_binding_events x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1;
    SELECT binding_id,event_kind INTO b FROM app.bing_binding_events x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1;
    SELECT x.id INTO q FROM app.ai_visibility_question_sets x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site
        AND NOT EXISTS(SELECT 1 FROM app.ai_visibility_question_sets n WHERE n.tenant_id=x.tenant_id AND n.site_id=x.site_id AND n.supersedes_id=x.id)
        ORDER BY x.created_at DESC,x.id DESC LIMIT 1;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.fact_id,'category',x.category,'statement',x.statement,
        'source_kind',x.source_kind,'page_evidence_id',x.page_evidence_id,'document_id',x.document_id,
        'owner_membership_id',x.owner_membership_id,'created_at',x.created_at,'sensitive',f.sensitive) ORDER BY x.fact_id),'[]'::jsonb)
        INTO facts FROM control.weekly_skill_approved_business_brain_facts(p_hash,p_generation,p_site) x
        JOIN app.business_brain_facts f ON f.tenant_id=v.tenant_id AND f.site_id=p_site AND f.id=x.fact_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',p.id,'url',u.fetch_url,'title',coalesce(p.title,'')) ORDER BY p.id),'[]'::jsonb)
        INTO pages FROM app.crawl_page_records p JOIN app.urls u ON u.tenant_id=p.tenant_id AND u.site_id=p.site_id AND u.id=p.url_id
        WHERE p.tenant_id=v.tenant_id AND p.site_id=p_site AND p.crawl_run_id=m.crawl_run_id AND p.http_status BETWEEN 200 AND 299;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'dimensions',x.dimensions,'rows',x.rows,'coverage',x.coverage,
        'window',jsonb_build_object('start',x.start_date,'end',x.end_date),'imported_at',x.imported_at,
        'aggregation_type',x.aggregation_type) ORDER BY x.dimensions),'[]'::jsonb) INTO gs
        FROM (SELECT DISTINCT ON (dimensions) * FROM app.gsc_import_generations
            WHERE tenant_id=v.tenant_id AND site_id=p_site AND binding_id=g.binding_id AND g.event_kind='bound'
              AND search_type='web' AND data_state='final'
            ORDER BY dimensions,imported_at DESC,id DESC) x;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.id,'rows',x.rows,'coverage',x.coverage,
        'window',NULL,'imported_at',x.imported_at)),'[]'::jsonb) INTO bs
        FROM (SELECT * FROM app.bing_import_generations WHERE tenant_id=v.tenant_id AND site_id=p_site
            AND binding_id=b.binding_id AND b.event_kind='bound' AND kind='performance'
            ORDER BY imported_at DESC,id DESC LIMIT 1) x;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',o.id,'question_id',x.id,'question',x.question,
        'provider',o.provider,'model',o.model,'status',o.status,'provider_evidence_id',o.provider_evidence_id,
        'cited_pages',o.cited_pages,'other_domains',o.other_domains,'observed_at',o.observed_at,'usage',o.usage)
        ORDER BY o.question_id,o.provider),'[]'::jsonb) INTO observations
        FROM (SELECT DISTINCT ON (z.question_id,z.provider) z.* FROM app.ai_visibility_observations z
            JOIN app.ai_visibility_questions y ON y.tenant_id=z.tenant_id AND y.site_id=z.site_id AND y.id=z.question_id
            WHERE z.tenant_id=v.tenant_id AND z.site_id=p_site AND y.question_set_id=q
            ORDER BY z.question_id,z.provider,z.observed_at DESC,z.id DESC) o
        JOIN app.ai_visibility_questions x ON x.tenant_id=o.tenant_id AND x.site_id=o.site_id AND x.id=o.question_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',r.id,'recipe_key',r.recipe_key,
        'autonomy_eligible',control.recipe_autonomy_eligible(r.id)) ORDER BY r.recipe_key),'[]'::jsonb) INTO recipes
        FROM (SELECT DISTINCT ON (x.recipe_key) x.* FROM control.recipe_releases x
            WHERE (SELECT e.status FROM control.recipe_release_events e WHERE e.release_id=x.id ORDER BY e.sequence_number DESC LIMIT 1)='REVIEWED'
            ORDER BY x.recipe_key,x.version_major DESC,x.version_minor DESC,x.version_patch DESC) r;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id',x.revision_id,'finding_id',x.canonical_manifest->>'finding_id') ORDER BY x.revision_id),'[]'::jsonb)
        INTO inbox FROM control.weekly_skill_candidate_inbox(p_hash,p_site,p_generation) x WHERE x.review_status='pending';
    RETURN jsonb_build_object(
        'crawl',jsonb_build_object('reason',CASE WHEN m.id IS NULL THEN 'No completed crawl evidence.' END,'records',CASE WHEN m.id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object('id',m.id,'coverage',m.coverage,'discovered_count',m.discovered_count,'terminal_count',m.terminal_count,'state_counts',m.state_counts,'completed_at',m.completed_at)) END),
        'technical',jsonb_build_object('reason',CASE WHEN a.id IS NULL THEN 'No technical report for the pinned crawl.' END,'records',CASE WHEN a.id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object('id',a.id,'manifest_id',a.manifest_id,'findings',a.findings,'coverage',a.coverage,'analyzed_at',a.analyzed_at)) END),
        'gsc',jsonb_build_object('reason',CASE WHEN g.event_kind IS DISTINCT FROM 'bound' THEN 'GSC unbound, revoked or reauthorization required.' WHEN gs='[]'::jsonb THEN 'No final web-performance import.' END,'records',gs),
        'bing',jsonb_build_object('reason',CASE WHEN b.event_kind IS DISTINCT FROM 'bound' THEN 'Bing unbound, revoked or reauthorization required.' WHEN bs='[]'::jsonb THEN 'No performance import.' END,'records',bs),
        'ai_visibility',jsonb_build_object('reason',CASE WHEN observations='[]'::jsonb THEN 'No AI-visibility observations.' WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(observations) o WHERE o->>'status'='incomplete') THEN 'Incomplete provider observations; missing citations are not zero.' END,'records',observations),
        'business_brain',jsonb_build_object('reason',CASE WHEN facts='[]'::jsonb THEN 'No current approved business facts.' END,'records',facts),
        'content',jsonb_build_object('reason',CASE WHEN pages='[]'::jsonb THEN 'No successful pages in the pinned crawl.' END,'records',pages),
        'recipes',recipes,'inbox',inbox,
        'page_inventory',coalesce((SELECT jsonb_agg(jsonb_build_object('id',coalesce(p.id,s.id),'url',u.fetch_url,'title',coalesce(p.title,''),'state',s.terminal_state) ORDER BY u.fetch_url)
            FROM app.crawl_frontier f JOIN app.urls u ON u.tenant_id=f.tenant_id AND u.site_id=f.site_id AND u.id=f.url_id
            JOIN app.crawl_frontier_settlements s ON s.tenant_id=f.tenant_id AND s.site_id=f.site_id AND s.frontier_id=f.id
            LEFT JOIN app.crawl_page_records p ON p.tenant_id=f.tenant_id AND p.site_id=f.site_id AND p.frontier_id=f.id
            WHERE f.tenant_id=v.tenant_id AND f.site_id=p_site AND f.crawl_run_id=m.crawl_run_id),'[]'::jsonb),
        'writer_inventory',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.brief_id,'status',x.status,'kind',x.payload->>'kind','created_at',x.created_at) ORDER BY x.brief_id)
            FROM jsonb_to_recordset(control.weekly_skill_content_writer_read(p_hash,p_generation,p_site)->'briefs') x(brief_id uuid,status text,payload jsonb,created_at timestamptz)),'[]'::jsonb)
    );
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_seo_strategy_base_sources(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_seo_strategy_base_sources(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.seo_strategy_base_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seo_strategy_base_sources('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.seo_strategy_base_sources(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.weekly_skill_seo_strategy_base_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seo_strategy_base_sources('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_base_sources(bytea,text,uuid) FROM PUBLIC;
-- End port group 57.
-- seo_strategy_read: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_seo_strategy_read(p_actor text, p_hash bytea, p_generation text, p_site uuid, p_snapshot uuid DEFAULT NULL::uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; s app.seo_strategy_snapshots%%ROWTYPE;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{brief_proposals}'::text[]);

END IF;
    SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_hash,p_generation,p_site,NULL);
    IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
    SELECT * INTO s FROM app.seo_strategy_snapshots x WHERE x.tenant_id=v.tenant_id AND x.site_id=p_site AND (p_snapshot IS NULL OR x.id=p_snapshot) ORDER BY x.version DESC LIMIT 1;
    RETURN jsonb_build_object('snapshot',CASE WHEN s.id IS NOT NULL THEN jsonb_build_object('id',s.id,'version',s.version,'created_at',s.created_at,'sha256',encode(s.sha256,'hex'),'payload',s.payload) END,
        'decisions',coalesce((SELECT jsonb_agg(jsonb_build_object('item_id',d.item_id,'decision',d.decision,'target_id',d.target_id,'target_kind',d.target_kind,'decided_at',d.decided_at) ORDER BY d.item_id) FROM app.seo_strategy_item_decisions d WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.payload->'strategy'->'items') i WHERE i->>'id'=d.item_id::text)),'[]'::jsonb));
END;
$function$;

ALTER FUNCTION control.port_seo_strategy_read(text,bytea,text,uuid,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_seo_strategy_read(text,bytea,text,uuid,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.seo_strategy_read(p_hash bytea, p_generation text, p_site uuid, p_snapshot uuid DEFAULT NULL::uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seo_strategy_read('owner',p_hash,p_generation,p_site,p_snapshot);
END;
$function$;

REVOKE ALL ON FUNCTION control.seo_strategy_read(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.seo_strategy_read(bytea,text,uuid,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_seo_strategy_read(p_hash bytea, p_generation text, p_site uuid, p_snapshot uuid DEFAULT NULL::uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seo_strategy_read('weekly_skill',p_hash,p_generation,p_site,p_snapshot);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_read(bytea,text,uuid,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_seo_strategy_read(bytea,text,uuid,uuid) TO signal_workflow;
-- End port group 58.
-- seo_strategy_record: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_seo_strategy_record(p_actor text, p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_canonical bytea)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v record; p jsonb; current_sources jsonb; previous app.seo_strategy_snapshots%%ROWTYPE; n integer;
BEGIN
    SELECT * INTO v FROM control.admit_port_context('owner','brain',p_hash,p_generation,p_site,NULL);
    IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
    IF p_canonical IS NULL OR octet_length(p_canonical)>16000000 THEN RETURN jsonb_build_object('state','invalid'); END IF;
    p:=convert_from(p_canonical,'UTF8')::jsonb;
    IF p->>'version' IS DISTINCT FROM 'seo-baseline-strategy-v1' THEN RETURN jsonb_build_object('state','invalid'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,77));
    current_sources:=control.seo_strategy_sources(p_hash,p_generation,p_site);
    IF p->'sources' IS DISTINCT FROM current_sources THEN RETURN jsonb_build_object('state','stale'); END IF;
    SELECT * INTO previous FROM app.seo_strategy_snapshots s WHERE s.tenant_id=v.tenant_id AND s.site_id=p_site ORDER BY s.version DESC LIMIT 1;
    IF previous.sha256=sha256(p_canonical) THEN RETURN jsonb_build_object('state','replayed','snapshot_id',previous.id); END IF;
    n:=coalesce(previous.version,0)+1;
    INSERT INTO app.seo_strategy_snapshots VALUES(v.tenant_id,p_site,p_id,n,p,p_canonical,sha256(p_canonical),v.user_id,transaction_timestamp());
    RETURN jsonb_build_object('state','recorded','snapshot_id',p_id);
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v record; p jsonb; current_sources jsonb; previous app.seo_strategy_snapshots%%ROWTYPE; n integer;
BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,'{strategy_rebuild}'::text[]);IF p_id IS DISTINCT FROM (SELECT control.weekly_skill_identity(cycle_id,'strategy_rebuild') FROM app.weekly_skill_intents WHERE handle_hash=p_hash) THEN RAISE EXCEPTION 'skill_snapshot_scope_denied' USING ERRCODE='42501'; END IF; IF EXISTS(SELECT 1 FROM app.seo_strategy_snapshots s JOIN app.weekly_skill_intents i ON i.tenant_id=s.tenant_id AND i.site_id=s.site_id WHERE i.handle_hash=p_hash AND s.id=p_id) THEN IF EXISTS(SELECT 1 FROM app.seo_strategy_snapshots s JOIN app.weekly_skill_intents i ON i.tenant_id=s.tenant_id AND i.site_id=s.site_id WHERE i.handle_hash=p_hash AND s.id=p_id AND s.sha256=sha256(p_canonical)) THEN RETURN jsonb_build_object('state','replayed','snapshot_id',p_id); ELSE RETURN jsonb_build_object('state','stale'); END IF; END IF;
    SELECT * INTO v FROM control.admit_port_context('weekly_skill','brain',p_hash,p_generation,p_site,NULL);
    IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
    IF p_canonical IS NULL OR octet_length(p_canonical)>16000000 THEN RETURN jsonb_build_object('state','invalid'); END IF;
    p:=convert_from(p_canonical,'UTF8')::jsonb;
    IF p->>'version' IS DISTINCT FROM 'seo-baseline-strategy-v1' THEN RETURN jsonb_build_object('state','invalid'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,77));
    current_sources:=control.weekly_skill_seo_strategy_sources(p_hash,p_generation,p_site);
    IF p->'sources' IS DISTINCT FROM current_sources THEN RETURN jsonb_build_object('state','stale'); END IF;
    SELECT * INTO previous FROM app.seo_strategy_snapshots s WHERE s.tenant_id=v.tenant_id AND s.site_id=p_site ORDER BY s.version DESC LIMIT 1;

    n:=coalesce(previous.version,0)+1;
    INSERT INTO app.seo_strategy_snapshots VALUES(v.tenant_id,p_site,p_id,n,p,p_canonical,sha256(p_canonical),v.user_id,transaction_timestamp());
    RETURN jsonb_build_object('state','recorded','snapshot_id',p_id);
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_seo_strategy_record(text,bytea,text,uuid,uuid,bytea) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_seo_strategy_record(text,bytea,text,uuid,uuid,bytea) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.seo_strategy_record(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_canonical bytea)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seo_strategy_record('owner',p_hash,p_generation,p_site,p_id,p_canonical);
END;
$function$;

REVOKE ALL ON FUNCTION control.seo_strategy_record(bytea,text,uuid,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.seo_strategy_record(bytea,text,uuid,uuid,bytea) TO signal_api;

CREATE OR REPLACE FUNCTION control.weekly_skill_seo_strategy_record(p_hash bytea, p_generation text, p_site uuid, p_id uuid, p_canonical bytea)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_seo_strategy_record('weekly_skill',p_hash,p_generation,p_site,p_id,p_canonical);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_record(bytea,text,uuid,uuid,bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_seo_strategy_record(bytea,text,uuid,uuid,bytea) TO signal_workflow;
-- End port group 59.
-- strategy_learning_sources: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_strategy_learning_sources(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; packet jsonb; measures jsonb; history jsonb; changes jsonb; binding uuid;
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='weekly_skill' THEN
PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['strategy_rebuild']);

END IF;
    packet:='{}'::jsonb;
    IF packet IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_hash,p_generation,p_site,NULL);
    IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
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
END;
$function$;

ALTER FUNCTION control.port_strategy_learning_sources(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_strategy_learning_sources(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.strategy_learning_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_strategy_learning_sources('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.strategy_learning_sources(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.weekly_strategy_learning_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_strategy_learning_sources('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_strategy_learning_sources(bytea,text,uuid) FROM PUBLIC;
-- End port group 60.
-- strategy_link_sources: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_strategy_link_sources(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
 RETURN (SELECT jsonb_build_object('internal_links',control.port_internal_link_sources(CASE p_actor WHEN 'weekly_skill' THEN 'weekly_strategy' ELSE 'owner' END,p_hash,p_generation,p_site))); END;
$function$;

ALTER FUNCTION control.port_strategy_link_sources(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_strategy_link_sources(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.strategy_link_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_strategy_link_sources('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.strategy_link_sources(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.weekly_strategy_link_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_strategy_link_sources('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_strategy_link_sources(bytea,text,uuid) FROM PUBLIC;
-- End port group 61.
-- strategy_topic_sources: shared body after credential admission.
CREATE OR REPLACE FUNCTION control.port_strategy_topic_sources(p_actor text, p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE v record; BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;

 SELECT * INTO v FROM control.admit_port_context(p_actor,'brain',p_hash,p_generation,p_site,NULL);
 IF NOT control.port_permission(CASE WHEN v.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN NULL; END IF;
 RETURN control.keyword_topic_sources(v.tenant_id,p_site)||jsonb_build_object('bing_pages',control.strategy_bing_pages(v.tenant_id,p_site));
END
$function$;

ALTER FUNCTION control.port_strategy_topic_sources(text,bytea,text,uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_strategy_topic_sources(text,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.strategy_topic_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_strategy_topic_sources('owner',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.strategy_topic_sources(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.weekly_strategy_topic_sources(p_hash bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_strategy_topic_sources('weekly_skill',p_hash,p_generation,p_site);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_strategy_topic_sources(bytea,text,uuid) FROM PUBLIC;
-- End port group 62.
-- begin_owner_connector_egress: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_begin_owner_connector_egress(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_kind text, p_profile text, p_method text, p_request_url text, p_target_url text, p_origin text, p_request_sha256 bytea, p_body_sha256 bytea, p_request_bytes integer, p_max_response_bytes integer, p_credentialed boolean, p_robots_operation_id uuid, p_min_delay_ms integer)
 RETURNS TABLE(outcome text, bucket_id uuid, expires_at timestamp with time zone, robots_allowed boolean, robots_crawl_delay_ms integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill','strategy_provider') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
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
    IF p_profile='dataforseo' AND p_kind='provider' THEN PERFORM control.assert_strategy_provider_dispatch(p_session_hash,p_generation,p_site_id,p_operation_id,p_target_url,p_body_sha256); END IF; SELECT * INTO v_owner FROM control.admit_port_context('owner','authority',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(v_owner.outcome,v_owner.role_key,'owner',true)
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
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE
    v_now timestamptz := clock_timestamp(); v_owner record; v_verified record;
    v_existing app.owner_connector_egress_operations%%ROWTYPE;
    v_robots app.owner_connector_egress_operations%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_expires timestamptz; v_session_expires timestamptz; v_workload text; v_fingerprint bytea;
    v_rechecked record;
BEGIN IF p_profile IS DISTINCT FROM 'pagespeed' THEN RAISE EXCEPTION 'skill_profile_denied' USING ERRCODE='42501'; END IF;
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
    SELECT * INTO v_owner FROM control.admit_port_context('weekly_skill','pagespeed',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(CASE WHEN v_owner.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true)
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    SELECT * INTO v_verified FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_verified.outcome IS DISTINCT FROM 'verified'
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_session_hash AND control.pagespeed_request_url(u->>'url',u->>'strategy')=p_target_url AND (p_kind='robots' OR u->>'sample_id'=p_operation_id::text)) THEN RAISE EXCEPTION 'skill_resource_denied' USING ERRCODE='42501'; END IF; IF p_profile='pagespeed' AND control.pagespeed_admission_allowed(
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
    SELECT g.ends_at INTO v_session_expires FROM app.weekly_skill_intents i JOIN app.standing_authorizations g ON g.tenant_id=i.tenant_id AND g.site_id=i.site_id AND g.id=i.grant_id WHERE i.handle_hash=p_session_hash AND g.ends_at>v_now;
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
    v_workload:='psi:'||p_site_id::text||':'||p_operation_id::text;
    v_fingerprint:=sha256(convert_to(encode(p_session_hash,'hex')||':'||p_generation||':'||
        p_site_id::text||':'||p_operation_id::text||':'||encode(p_request_sha256,'hex'),'UTF8'));
    INSERT INTO control.admission_leases(id,bucket_id,workload_id,permit_kind,authority_fingerprint,
        min_delay_ms,requested_lease_seconds,issued_at,expires_at)
    VALUES(p_operation_id,v_bucket.id,v_workload,'shared_egress',v_fingerprint,p_min_delay_ms,30,v_now,v_expires);
    INSERT INTO app.owner_connector_egress_operations(tenant_id,site_id,id,actor_user_id,session_hash,
        recovery_generation,verified_origin,kind,profile,method,request_url,target_url,origin,
        request_sha256,request_body_sha256,request_bytes,max_response_bytes,credentialed,
        robots_operation_id,bucket_id,issued_at,expires_at,weekly_skill_handle_hash)
    VALUES(v_owner.tenant_id,p_site_id,p_operation_id,v_owner.user_id,p_session_hash,p_generation,
        v_verified.origin,p_kind,p_profile,p_method,p_request_url,p_target_url,p_origin,p_request_sha256,
        p_body_sha256,p_request_bytes,p_max_response_bytes,p_credentialed,p_robots_operation_id,v_bucket.id,v_now,v_expires,p_session_hash);
    UPDATE control.origin_buckets SET request_tokens=0,in_flight_count=1,
        next_allowed_at=GREATEST(next_allowed_at,v_now+p_min_delay_ms*interval '1 millisecond'),updated_at=v_now
     WHERE id=v_bucket.id;
    RETURN QUERY SELECT 'admitted'::text,v_bucket.id,v_expires,NULL::boolean,NULL::integer;
END;
ELSIF p_actor='strategy_provider' THEN
DECLARE
    v_now timestamptz := clock_timestamp(); v_owner record; v_verified record;
    v_existing app.owner_connector_egress_operations%%ROWTYPE;
    v_robots app.owner_connector_egress_operations%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE;
    v_expires timestamptz; v_session_expires timestamptz; v_workload text; v_fingerprint bytea;
    v_rechecked record;
BEGIN IF p_profile IS DISTINCT FROM 'dataforseo' THEN RAISE EXCEPTION 'skill_profile_denied' USING ERRCODE='42501'; END IF;
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
            CASE WHEN p_kind = 'robots' THEN CASE WHEN p_profile IN ('github_rest','gsc_api')
                AND p_target_url !~ '/access_tokens$|/searchAnalytics/query$' THEN 'GET' ELSE 'POST' END
                ELSE p_method END, p_target_url) IS NOT TRUE
       OR (p_method = 'GET' AND (p_request_bytes <> 0 OR p_body_sha256 IS NOT NULL))
       OR (p_method = 'POST' AND octet_length(p_body_sha256) IS DISTINCT FROM 32)
       OR p_method IS NULL OR p_method NOT IN ('GET', 'POST')
       OR (p_kind = 'robots' AND (p_method <> 'GET' OR p_credentialed
            OR p_request_url IS DISTINCT FROM p_origin || '/robots.txt' OR p_robots_operation_id IS NOT NULL))
       OR (p_kind = 'provider' AND (p_target_url IS DISTINCT FROM p_request_url OR p_robots_operation_id IS NULL))
    THEN RAISE EXCEPTION 'invalid_owner_connector_request' USING ERRCODE = '22023'; END IF;
    SELECT * INTO v_owner FROM control.admit_port_context('strategy_provider','provider',p_session_hash,p_generation,p_site_id,NULL);
    IF NOT control.port_permission(CASE WHEN v_owner.tenant_id IS NOT NULL THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true)
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    SELECT * INTO v_verified FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_verified.outcome IS DISTINCT FROM 'verified'
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    IF p_kind='provider' THEN PERFORM control.assert_strategy_provider_dispatch(p_session_hash,p_generation,p_site_id,p_operation_id,p_target_url,p_body_sha256); END IF; IF p_profile IS DISTINCT FROM 'dataforseo'
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
    SELECT g.ends_at INTO v_session_expires FROM app.standing_authorizations g WHERE g.tenant_id=v_owner.tenant_id AND g.site_id=p_site_id AND g.id=v_owner.grant_id AND g.ends_at>v_now;
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
    v_workload:='psi:'||p_site_id::text||':'||p_operation_id::text;
    v_fingerprint:=sha256(convert_to(encode(p_session_hash,'hex')||':'||p_generation||':'||
        p_site_id::text||':'||p_operation_id::text||':'||encode(p_request_sha256,'hex'),'UTF8'));
    INSERT INTO control.admission_leases(id,bucket_id,workload_id,permit_kind,authority_fingerprint,
        min_delay_ms,requested_lease_seconds,issued_at,expires_at)
    VALUES(p_operation_id,v_bucket.id,v_workload,'shared_egress',v_fingerprint,p_min_delay_ms,30,v_now,v_expires);
    INSERT INTO app.owner_connector_egress_operations(tenant_id,site_id,id,actor_user_id,session_hash,
        recovery_generation,verified_origin,kind,profile,method,request_url,target_url,origin,
        request_sha256,request_body_sha256,request_bytes,max_response_bytes,credentialed,
        robots_operation_id,bucket_id,issued_at,expires_at,weekly_skill_handle_hash)
    VALUES(v_owner.tenant_id,p_site_id,p_operation_id,v_owner.user_id,p_session_hash,p_generation,
        v_verified.origin,p_kind,p_profile,p_method,p_request_url,p_target_url,p_origin,p_request_sha256,
        p_body_sha256,p_request_bytes,p_max_response_bytes,p_credentialed,p_robots_operation_id,v_bucket.id,v_now,v_expires,CASE WHEN EXISTS(SELECT 1 FROM control.weekly_skill_directory WHERE handle_hash=p_session_hash) THEN p_session_hash END);
    UPDATE control.origin_buckets SET request_tokens=0,in_flight_count=1,
        next_allowed_at=GREATEST(next_allowed_at,v_now+p_min_delay_ms*interval '1 millisecond'),updated_at=v_now
     WHERE id=v_bucket.id;
    RETURN QUERY SELECT 'admitted'::text,v_bucket.id,v_expires,NULL::boolean,NULL::integer;
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_begin_owner_connector_egress(text,bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_begin_owner_connector_egress(text,bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.begin_owner_connector_egress(p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_kind text, p_profile text, p_method text, p_request_url text, p_target_url text, p_origin text, p_request_sha256 bytea, p_body_sha256 bytea, p_request_bytes integer, p_max_response_bytes integer, p_credentialed boolean, p_robots_operation_id uuid, p_min_delay_ms integer)
 RETURNS TABLE(outcome text, bucket_id uuid, expires_at timestamp with time zone, robots_allowed boolean, robots_crawl_delay_ms integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_begin_owner_connector_egress('owner',p_session_hash,p_generation,p_site_id,p_operation_id,p_kind,p_profile,p_method,p_request_url,p_target_url,p_origin,p_request_sha256,p_body_sha256,p_request_bytes,p_max_response_bytes,p_credentialed,p_robots_operation_id,p_min_delay_ms);
END;
$function$;

REVOKE ALL ON FUNCTION control.begin_owner_connector_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.begin_owner_connector_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_begin_pagespeed_egress(p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_kind text, p_profile text, p_method text, p_request_url text, p_target_url text, p_origin text, p_request_sha256 bytea, p_body_sha256 bytea, p_request_bytes integer, p_max_response_bytes integer, p_credentialed boolean, p_robots_operation_id uuid, p_min_delay_ms integer)
 RETURNS TABLE(outcome text, bucket_id uuid, expires_at timestamp with time zone, robots_allowed boolean, robots_crawl_delay_ms integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_begin_owner_connector_egress('weekly_skill',p_session_hash,p_generation,p_site_id,p_operation_id,p_kind,p_profile,p_method,p_request_url,p_target_url,p_origin,p_request_sha256,p_body_sha256,p_request_bytes,p_max_response_bytes,p_credentialed,p_robots_operation_id,p_min_delay_ms);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_begin_pagespeed_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_begin_pagespeed_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_skill_begin_dataforseo_egress(p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_kind text, p_profile text, p_method text, p_request_url text, p_target_url text, p_origin text, p_request_sha256 bytea, p_body_sha256 bytea, p_request_bytes integer, p_max_response_bytes integer, p_credentialed boolean, p_robots_operation_id uuid, p_min_delay_ms integer)
 RETURNS TABLE(outcome text, bucket_id uuid, expires_at timestamp with time zone, robots_allowed boolean, robots_crawl_delay_ms integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY SELECT * FROM control.port_begin_owner_connector_egress('strategy_provider',p_session_hash,p_generation,p_site_id,p_operation_id,p_kind,p_profile,p_method,p_request_url,p_target_url,p_origin,p_request_sha256,p_body_sha256,p_request_bytes,p_max_response_bytes,p_credentialed,p_robots_operation_id,p_min_delay_ms);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_begin_dataforseo_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_begin_dataforseo_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,bytea,bytea,integer,integer,boolean,uuid,integer) TO signal_workflow;
-- End port group 63.
-- finish_owner_connector_egress: preserved legacy operation variants; shared permission decision.
CREATE OR REPLACE FUNCTION control.port_finish_owner_connector_egress(p_actor text, p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_evidence jsonb, p_completion text, p_retry_after_ms integer, p_robots_allowed boolean, p_rules_sha256 bytea, p_crawl_delay_ms integer)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
IF p_actor IS NULL OR p_actor NOT IN ('owner','weekly_skill','strategy_provider') THEN RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
IF p_actor='owner' THEN
DECLARE v_tenant uuid; v_op app.owner_connector_egress_operations%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE; v_lease control.admission_leases%%ROWTYPE;
    v_now timestamptz:=clock_timestamp(); v_backoff integer; v_status integer; v_outcome text;
BEGIN
    IF p_evidence IS NULL OR jsonb_typeof(p_evidence)<>'object'
       OR p_evidence - 'outcome' - 'http_status' - 'media_type' - 'resolved_address'
            - 'body_sha256' - 'decoded_bytes' - 'elapsed_ms' - 'robots_artifact' <> '{}'::jsonb
       OR NOT p_evidence ?& ARRAY['outcome','http_status','media_type','resolved_address','body_sha256','decoded_bytes','elapsed_ms','robots_artifact']
       OR jsonb_typeof(p_evidence->'outcome') IS DISTINCT FROM 'string'
       OR jsonb_typeof(p_evidence->'decoded_bytes') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'elapsed_ms') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'http_status') NOT IN ('number','null')
       OR jsonb_typeof(p_evidence->'media_type') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'resolved_address') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'body_sha256') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'robots_artifact') NOT IN ('object','null')
       OR p_evidence->>'decoded_bytes' !~ '^[0-9]+$' OR p_evidence->>'elapsed_ms' !~ '^[0-9]+$'
       OR p_completion IS NULL OR p_completion NOT IN ('success','rate_limited','service_unavailable','transport_error','cancelled')
       OR (p_retry_after_ms IS NOT NULL AND p_retry_after_ms NOT BETWEEN 1000 AND 86400000)
       OR (p_crawl_delay_ms IS NOT NULL AND p_crawl_delay_ms NOT BETWEEN 1 AND 60000)
    THEN RAISE EXCEPTION 'invalid_owner_connector_completion' USING ERRCODE='22023'; END IF;
    v_outcome:=p_evidence->>'outcome'; v_status:=(p_evidence->>'http_status')::integer;
    IF v_outcome IS NULL OR v_outcome NOT IN ('fetched','redirect_rejected','unsupported_encoding',
        'unsupported_media_type','body_limit','policy_rejected','transport_error')
       OR (p_evidence->>'decoded_bytes')::integer NOT BETWEEN 0 AND 524288
       OR (p_evidence->>'elapsed_ms')::integer NOT BETWEEN 0 AND 120000
       OR ((v_outcome IN ('policy_rejected','transport_error')) IS DISTINCT FROM (v_status IS NULL))
       OR (v_status IS NOT NULL AND (v_status NOT BETWEEN 100 AND 599
            OR control.valid_crawl_public_address((p_evidence->>'resolved_address')::inet) IS NOT TRUE))
       OR (v_status IS NULL AND (p_evidence->>'resolved_address' IS NOT NULL OR p_evidence->>'media_type' IS NOT NULL))
       OR (v_outcome='fetched' AND (p_evidence->>'media_type' IS NULL
            OR control.valid_artifact_media_type(p_evidence->>'media_type') IS NOT TRUE
            OR p_evidence->>'body_sha256' IS NULL OR p_evidence->>'body_sha256' !~ '^[0-9a-f]{64}$'))
       OR (v_outcome<>'fetched' AND (p_evidence->>'body_sha256' IS NOT NULL OR (p_evidence->>'decoded_bytes')::integer<>0))
       OR (v_outcome='transport_error' AND p_completion<>'transport_error')
       OR (v_outcome='policy_rejected' AND p_completion<>'cancelled')
       OR (v_status=429 AND (p_completion<>'rate_limited' OR p_retry_after_ms IS NULL))
       OR (v_status=503 AND p_completion<>'service_unavailable')
       OR (v_status IS NOT NULL AND v_status NOT IN (429,503) AND p_completion<>'success')
       OR (p_completion IN ('success','transport_error','cancelled') AND p_retry_after_ms IS NOT NULL)
    THEN RAISE EXCEPTION 'invalid_owner_connector_evidence' USING ERRCODE='22023'; END IF;
    -- Completion records the known outcome even if the original owner has since
    -- signed out. It cannot dispatch another request or enlarge any authority.
    PERFORM set_config('signal.session_hash',encode(p_session_hash,'hex'),true);
    SELECT tenant_id INTO v_tenant FROM app.sessions WHERE session_token_hash=p_session_hash;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',v_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO v_op FROM app.owner_connector_egress_operations
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id;
    IF NOT FOUND OR v_op.session_hash IS DISTINCT FROM p_session_hash
        OR v_op.recovery_generation IS DISTINCT FROM p_generation THEN RETURN 'denied'; END IF;
    SELECT * INTO v_bucket FROM control.origin_buckets WHERE id=v_op.bucket_id FOR UPDATE;
    SELECT * INTO v_op FROM app.owner_connector_egress_operations
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id FOR UPDATE;
    SELECT * INTO v_lease FROM control.admission_leases WHERE id=p_operation_id;
    v_now:=clock_timestamp();
    IF v_op.state<>'dispatched' THEN
        IF v_op.response_evidence IS DISTINCT FROM p_evidence
           OR v_op.robots_allowed IS DISTINCT FROM p_robots_allowed
           OR v_op.robots_rules_sha256 IS DISTINCT FROM p_rules_sha256
           OR v_op.robots_crawl_delay_ms IS DISTINCT FROM p_crawl_delay_ms
           OR v_lease.completion_kind IS DISTINCT FROM p_completion
           OR v_lease.retry_after_ms IS DISTINCT FROM p_retry_after_ms
        THEN RETURN 'conflict'; END IF;
        RETURN 'replayed';
    END IF;
    IF v_lease.released_at IS NOT NULL OR v_lease.expires_at<=v_now THEN RETURN 'expired'; END IF;
    IF (p_evidence->>'decoded_bytes')::integer>v_op.max_response_bytes
       OR (v_op.kind='provider' AND (p_robots_allowed IS NOT NULL OR p_rules_sha256 IS NOT NULL
            OR p_crawl_delay_ms IS NOT NULL OR p_evidence->'robots_artifact'<>'null'::jsonb))
       OR (v_op.kind='robots' AND p_robots_allowed IS NULL)
       OR (v_op.kind='robots' AND p_robots_allowed AND (v_outcome<>'fetched'
            OR (v_status=404 OR (v_status=200 AND octet_length(p_rules_sha256)=32
                AND p_evidence->>'media_type'='text/plain'
                AND jsonb_typeof(p_evidence->'robots_artifact')='object'
                AND p_evidence->'robots_artifact'->>'sha256'=p_evidence->>'body_sha256')) IS NOT TRUE))
       OR (p_rules_sha256 IS NOT NULL AND octet_length(p_rules_sha256)<>32)
    THEN RAISE EXCEPTION 'invalid_owner_robots_evidence' USING ERRCODE='22023'; END IF;
    UPDATE app.owner_connector_egress_operations SET state=CASE WHEN v_status IS NULL THEN 'failed' ELSE 'observed' END,
        finished_at=v_now,response_evidence=p_evidence,robots_allowed=p_robots_allowed,
        robots_rules_sha256=p_rules_sha256,robots_crawl_delay_ms=p_crawl_delay_ms,
        robots_expires_at=CASE WHEN v_op.kind='robots' THEN v_now+interval '5 minutes' ELSE NULL END
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id;
    UPDATE control.admission_leases SET released_at=v_now,completion_kind=p_completion,
        observed_latency_ms=(p_evidence->>'elapsed_ms')::integer,retry_after_ms=p_retry_after_ms WHERE id=p_operation_id;
    v_backoff:=CASE p_completion WHEN 'rate_limited' THEN p_retry_after_ms
        WHEN 'service_unavailable' THEN coalesce(p_retry_after_ms,30000)
        WHEN 'transport_error' THEN 5000 WHEN 'success' THEN
            CASE WHEN (p_evidence->>'elapsed_ms')::integer>=2000 THEN LEAST((p_evidence->>'elapsed_ms')::integer,60000) END END;
    UPDATE control.origin_buckets SET in_flight_count=(SELECT count(*) FROM control.admission_leases
        WHERE bucket_id=v_bucket.id AND released_at IS NULL),
        next_allowed_at=CASE WHEN v_backoff IS NULL THEN next_allowed_at ELSE
            GREATEST(next_allowed_at,v_now+v_backoff*interval '1 millisecond') END,
        degraded_until=CASE WHEN p_completion IN ('rate_limited','service_unavailable') THEN
            GREATEST(degraded_until,v_now+v_backoff*interval '1 millisecond') ELSE degraded_until END,updated_at=v_now
     WHERE id=v_bucket.id;
    RETURN 'finished';
END;
ELSIF p_actor='weekly_skill' THEN
DECLARE v_tenant uuid; v_op app.owner_connector_egress_operations%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE; v_lease control.admission_leases%%ROWTYPE;
    v_now timestamptz:=clock_timestamp(); v_backoff integer; v_status integer; v_outcome text;
BEGIN
    IF p_evidence IS NULL OR jsonb_typeof(p_evidence)<>'object'
       OR p_evidence - 'outcome' - 'http_status' - 'media_type' - 'resolved_address'
            - 'body_sha256' - 'decoded_bytes' - 'elapsed_ms' - 'robots_artifact' <> '{}'::jsonb
       OR NOT p_evidence ?& ARRAY['outcome','http_status','media_type','resolved_address','body_sha256','decoded_bytes','elapsed_ms','robots_artifact']
       OR jsonb_typeof(p_evidence->'outcome') IS DISTINCT FROM 'string'
       OR jsonb_typeof(p_evidence->'decoded_bytes') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'elapsed_ms') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'http_status') NOT IN ('number','null')
       OR jsonb_typeof(p_evidence->'media_type') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'resolved_address') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'body_sha256') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'robots_artifact') NOT IN ('object','null')
       OR p_evidence->>'decoded_bytes' !~ '^[0-9]+$' OR p_evidence->>'elapsed_ms' !~ '^[0-9]+$'
       OR p_completion IS NULL OR p_completion NOT IN ('success','rate_limited','service_unavailable','transport_error','cancelled')
       OR (p_retry_after_ms IS NOT NULL AND p_retry_after_ms NOT BETWEEN 1000 AND 86400000)
       OR (p_crawl_delay_ms IS NOT NULL AND p_crawl_delay_ms NOT BETWEEN 1 AND 60000)
    THEN RAISE EXCEPTION 'invalid_owner_connector_completion' USING ERRCODE='22023'; END IF;
    v_outcome:=p_evidence->>'outcome'; v_status:=(p_evidence->>'http_status')::integer;
    IF v_outcome IS NULL OR v_outcome NOT IN ('fetched','redirect_rejected','unsupported_encoding',
        'unsupported_media_type','body_limit','policy_rejected','transport_error')
       OR (p_evidence->>'decoded_bytes')::integer NOT BETWEEN 0 AND 524288
       OR (p_evidence->>'elapsed_ms')::integer NOT BETWEEN 0 AND 120000
       OR ((v_outcome IN ('policy_rejected','transport_error')) IS DISTINCT FROM (v_status IS NULL))
       OR (v_status IS NOT NULL AND (v_status NOT BETWEEN 100 AND 599
            OR control.valid_crawl_public_address((p_evidence->>'resolved_address')::inet) IS NOT TRUE))
       OR (v_status IS NULL AND (p_evidence->>'resolved_address' IS NOT NULL OR p_evidence->>'media_type' IS NOT NULL))
       OR (v_outcome='fetched' AND (p_evidence->>'media_type' IS NULL
            OR control.valid_artifact_media_type(p_evidence->>'media_type') IS NOT TRUE
            OR p_evidence->>'body_sha256' IS NULL OR p_evidence->>'body_sha256' !~ '^[0-9a-f]{64}$'))
       OR (v_outcome<>'fetched' AND (p_evidence->>'body_sha256' IS NOT NULL OR (p_evidence->>'decoded_bytes')::integer<>0))
       OR (v_outcome='transport_error' AND p_completion<>'transport_error')
       OR (v_outcome='policy_rejected' AND p_completion<>'cancelled')
       OR (v_status=429 AND (p_completion<>'rate_limited' OR p_retry_after_ms IS NULL))
       OR (v_status=503 AND p_completion<>'service_unavailable')
       OR (v_status IS NOT NULL AND v_status NOT IN (429,503) AND p_completion<>'success')
       OR (p_completion IN ('success','transport_error','cancelled') AND p_retry_after_ms IS NOT NULL)
    THEN RAISE EXCEPTION 'invalid_owner_connector_evidence' USING ERRCODE='22023'; END IF;
    -- Completion records the known outcome even if the original owner has since
    -- signed out. It cannot dispatch another request or enlarge any authority.
    SELECT tenant_id INTO v_tenant FROM control.weekly_skill_directory WHERE handle_hash=p_session_hash AND site_id=p_site_id AND recovery_generation=p_generation;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',v_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO v_op FROM app.owner_connector_egress_operations
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id;
    IF NOT FOUND OR v_op.session_hash IS DISTINCT FROM p_session_hash
        OR v_op.recovery_generation IS DISTINCT FROM p_generation OR v_op.weekly_skill_handle_hash IS DISTINCT FROM p_session_hash OR v_op.profile<>'pagespeed' THEN RETURN 'denied'; END IF;
    SELECT * INTO v_bucket FROM control.origin_buckets WHERE id=v_op.bucket_id FOR UPDATE;
    SELECT * INTO v_op FROM app.owner_connector_egress_operations
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id FOR UPDATE;
    SELECT * INTO v_lease FROM control.admission_leases WHERE id=p_operation_id;
    v_now:=clock_timestamp();
    IF v_op.state<>'dispatched' THEN
        IF v_op.response_evidence IS DISTINCT FROM p_evidence
           OR v_op.robots_allowed IS DISTINCT FROM p_robots_allowed
           OR v_op.robots_rules_sha256 IS DISTINCT FROM p_rules_sha256
           OR v_op.robots_crawl_delay_ms IS DISTINCT FROM p_crawl_delay_ms
           OR v_lease.completion_kind IS DISTINCT FROM p_completion
           OR v_lease.retry_after_ms IS DISTINCT FROM p_retry_after_ms
        THEN RETURN 'conflict'; END IF;
        RETURN 'replayed';
    END IF;
    IF v_lease.released_at IS NOT NULL OR v_lease.expires_at<=v_now THEN RETURN 'expired'; END IF;
    IF (p_evidence->>'decoded_bytes')::integer>v_op.max_response_bytes
       OR (v_op.kind='provider' AND (p_robots_allowed IS NOT NULL OR p_rules_sha256 IS NOT NULL
            OR p_crawl_delay_ms IS NOT NULL OR p_evidence->'robots_artifact'<>'null'::jsonb))
       OR (v_op.kind='robots' AND p_robots_allowed IS NULL)
       OR (v_op.kind='robots' AND p_robots_allowed AND (v_outcome<>'fetched'
            OR (v_status=404 OR (v_status=200 AND octet_length(p_rules_sha256)=32
                AND p_evidence->>'media_type'='text/plain'
                AND jsonb_typeof(p_evidence->'robots_artifact')='object'
                AND p_evidence->'robots_artifact'->>'sha256'=p_evidence->>'body_sha256')) IS NOT TRUE))
       OR (p_rules_sha256 IS NOT NULL AND octet_length(p_rules_sha256)<>32)
    THEN RAISE EXCEPTION 'invalid_owner_robots_evidence' USING ERRCODE='22023'; END IF;
    UPDATE app.owner_connector_egress_operations SET state=CASE WHEN v_status IS NULL THEN 'failed' ELSE 'observed' END,
        finished_at=v_now,response_evidence=p_evidence,robots_allowed=p_robots_allowed,
        robots_rules_sha256=p_rules_sha256,robots_crawl_delay_ms=p_crawl_delay_ms,
        robots_expires_at=CASE WHEN v_op.kind='robots' THEN v_now+interval '5 minutes' ELSE NULL END
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id;
    UPDATE control.admission_leases SET released_at=v_now,completion_kind=p_completion,
        observed_latency_ms=(p_evidence->>'elapsed_ms')::integer,retry_after_ms=p_retry_after_ms WHERE id=p_operation_id;
    v_backoff:=CASE p_completion WHEN 'rate_limited' THEN p_retry_after_ms
        WHEN 'service_unavailable' THEN coalesce(p_retry_after_ms,30000)
        WHEN 'transport_error' THEN 5000 WHEN 'success' THEN
            CASE WHEN (p_evidence->>'elapsed_ms')::integer>=2000 THEN LEAST((p_evidence->>'elapsed_ms')::integer,60000) END END;
    UPDATE control.origin_buckets SET in_flight_count=(SELECT count(*) FROM control.admission_leases
        WHERE bucket_id=v_bucket.id AND released_at IS NULL),
        next_allowed_at=CASE WHEN v_backoff IS NULL THEN next_allowed_at ELSE
            GREATEST(next_allowed_at,v_now+v_backoff*interval '1 millisecond') END,
        degraded_until=CASE WHEN p_completion IN ('rate_limited','service_unavailable') THEN
            GREATEST(degraded_until,v_now+v_backoff*interval '1 millisecond') ELSE degraded_until END,updated_at=v_now
     WHERE id=v_bucket.id;
    RETURN 'finished';
END;
ELSIF p_actor='strategy_provider' THEN
DECLARE v_tenant uuid; v_op app.owner_connector_egress_operations%%ROWTYPE;
    v_bucket control.origin_buckets%%ROWTYPE; v_lease control.admission_leases%%ROWTYPE;
    v_now timestamptz:=clock_timestamp(); v_backoff integer; v_status integer; v_outcome text;
BEGIN
    IF p_evidence IS NULL OR jsonb_typeof(p_evidence)<>'object'
       OR p_evidence - 'outcome' - 'http_status' - 'media_type' - 'resolved_address'
            - 'body_sha256' - 'decoded_bytes' - 'elapsed_ms' - 'robots_artifact' <> '{}'::jsonb
       OR NOT p_evidence ?& ARRAY['outcome','http_status','media_type','resolved_address','body_sha256','decoded_bytes','elapsed_ms','robots_artifact']
       OR jsonb_typeof(p_evidence->'outcome') IS DISTINCT FROM 'string'
       OR jsonb_typeof(p_evidence->'decoded_bytes') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'elapsed_ms') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'http_status') NOT IN ('number','null')
       OR jsonb_typeof(p_evidence->'media_type') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'resolved_address') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'body_sha256') NOT IN ('string','null')
       OR jsonb_typeof(p_evidence->'robots_artifact') NOT IN ('object','null')
       OR p_evidence->>'decoded_bytes' !~ '^[0-9]+$' OR p_evidence->>'elapsed_ms' !~ '^[0-9]+$'
       OR p_completion IS NULL OR p_completion NOT IN ('success','rate_limited','service_unavailable','transport_error','cancelled')
       OR (p_retry_after_ms IS NOT NULL AND p_retry_after_ms NOT BETWEEN 1000 AND 86400000)
       OR (p_crawl_delay_ms IS NOT NULL AND p_crawl_delay_ms NOT BETWEEN 1 AND 60000)
    THEN RAISE EXCEPTION 'invalid_owner_connector_completion' USING ERRCODE='22023'; END IF;
    v_outcome:=p_evidence->>'outcome'; v_status:=(p_evidence->>'http_status')::integer;
    IF v_outcome IS NULL OR v_outcome NOT IN ('fetched','redirect_rejected','unsupported_encoding',
        'unsupported_media_type','body_limit','policy_rejected','transport_error')
       OR (p_evidence->>'decoded_bytes')::integer NOT BETWEEN 0 AND 524288
       OR (p_evidence->>'elapsed_ms')::integer NOT BETWEEN 0 AND 120000
       OR ((v_outcome IN ('policy_rejected','transport_error')) IS DISTINCT FROM (v_status IS NULL))
       OR (v_status IS NOT NULL AND (v_status NOT BETWEEN 100 AND 599
            OR control.valid_crawl_public_address((p_evidence->>'resolved_address')::inet) IS NOT TRUE))
       OR (v_status IS NULL AND (p_evidence->>'resolved_address' IS NOT NULL OR p_evidence->>'media_type' IS NOT NULL))
       OR (v_outcome='fetched' AND (p_evidence->>'media_type' IS NULL
            OR control.valid_artifact_media_type(p_evidence->>'media_type') IS NOT TRUE
            OR p_evidence->>'body_sha256' IS NULL OR p_evidence->>'body_sha256' !~ '^[0-9a-f]{64}$'))
       OR (v_outcome<>'fetched' AND (p_evidence->>'body_sha256' IS NOT NULL OR (p_evidence->>'decoded_bytes')::integer<>0))
       OR (v_outcome='transport_error' AND p_completion<>'transport_error')
       OR (v_outcome='policy_rejected' AND p_completion<>'cancelled')
       OR (v_status=429 AND (p_completion<>'rate_limited' OR p_retry_after_ms IS NULL))
       OR (v_status=503 AND p_completion<>'service_unavailable')
       OR (v_status IS NOT NULL AND v_status NOT IN (429,503) AND p_completion<>'success')
       OR (p_completion IN ('success','transport_error','cancelled') AND p_retry_after_ms IS NOT NULL)
    THEN RAISE EXCEPTION 'invalid_owner_connector_evidence' USING ERRCODE='22023'; END IF;
    -- Completion records the known outcome even if the original owner has since
    -- signed out. It cannot dispatch another request or enlarge any authority.
    SELECT tenant_id INTO v_tenant FROM control.weekly_skill_directory WHERE handle_hash=p_session_hash AND site_id=p_site_id AND recovery_generation=p_generation; IF v_tenant IS NULL THEN PERFORM set_config('signal.session_hash',encode(p_session_hash,'hex'),true); SELECT tenant_id INTO v_tenant FROM app.sessions WHERE session_token_hash=p_session_hash; END IF;
    IF NOT FOUND THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',v_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO v_op FROM app.owner_connector_egress_operations
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id;
    IF NOT FOUND OR v_op.session_hash IS DISTINCT FROM p_session_hash
        OR v_op.recovery_generation IS DISTINCT FROM p_generation OR (v_op.weekly_skill_handle_hash IS DISTINCT FROM p_session_hash AND NOT (v_op.weekly_skill_handle_hash IS NULL AND v_op.session_hash=p_session_hash)) OR v_op.profile<>'dataforseo' THEN RETURN 'denied'; END IF;
    SELECT * INTO v_bucket FROM control.origin_buckets WHERE id=v_op.bucket_id FOR UPDATE;
    SELECT * INTO v_op FROM app.owner_connector_egress_operations
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id FOR UPDATE;
    SELECT * INTO v_lease FROM control.admission_leases WHERE id=p_operation_id;
    v_now:=clock_timestamp();
    IF v_op.state<>'dispatched' THEN
        IF v_op.response_evidence IS DISTINCT FROM p_evidence
           OR v_op.robots_allowed IS DISTINCT FROM p_robots_allowed
           OR v_op.robots_rules_sha256 IS DISTINCT FROM p_rules_sha256
           OR v_op.robots_crawl_delay_ms IS DISTINCT FROM p_crawl_delay_ms
           OR v_lease.completion_kind IS DISTINCT FROM p_completion
           OR v_lease.retry_after_ms IS DISTINCT FROM p_retry_after_ms
        THEN RETURN 'conflict'; END IF;
        RETURN 'replayed';
    END IF;
    IF v_lease.released_at IS NOT NULL OR v_lease.expires_at<=v_now THEN RETURN 'expired'; END IF;
    IF (p_evidence->>'decoded_bytes')::integer>v_op.max_response_bytes
       OR (v_op.kind='provider' AND (p_robots_allowed IS NOT NULL OR p_rules_sha256 IS NOT NULL
            OR p_crawl_delay_ms IS NOT NULL OR p_evidence->'robots_artifact'<>'null'::jsonb))
       OR (v_op.kind='robots' AND p_robots_allowed IS NULL)
       OR (v_op.kind='robots' AND p_robots_allowed AND (v_outcome<>'fetched'
            OR (v_status=404 OR (v_status=200 AND octet_length(p_rules_sha256)=32
                AND p_evidence->>'media_type'='text/plain'
                AND jsonb_typeof(p_evidence->'robots_artifact')='object'
                AND p_evidence->'robots_artifact'->>'sha256'=p_evidence->>'body_sha256')) IS NOT TRUE))
       OR (p_rules_sha256 IS NOT NULL AND octet_length(p_rules_sha256)<>32)
    THEN RAISE EXCEPTION 'invalid_owner_robots_evidence' USING ERRCODE='22023'; END IF;
    UPDATE app.owner_connector_egress_operations SET state=CASE WHEN v_status IS NULL THEN 'failed' ELSE 'observed' END,
        finished_at=v_now,response_evidence=p_evidence,robots_allowed=p_robots_allowed,
        robots_rules_sha256=p_rules_sha256,robots_crawl_delay_ms=p_crawl_delay_ms,
        robots_expires_at=CASE WHEN v_op.kind='robots' THEN v_now+interval '5 minutes' ELSE NULL END
     WHERE tenant_id=v_tenant AND site_id=p_site_id AND id=p_operation_id;
    UPDATE control.admission_leases SET released_at=v_now,completion_kind=p_completion,
        observed_latency_ms=(p_evidence->>'elapsed_ms')::integer,retry_after_ms=p_retry_after_ms WHERE id=p_operation_id;
    v_backoff:=CASE p_completion WHEN 'rate_limited' THEN p_retry_after_ms
        WHEN 'service_unavailable' THEN coalesce(p_retry_after_ms,30000)
        WHEN 'transport_error' THEN 5000 WHEN 'success' THEN
            CASE WHEN (p_evidence->>'elapsed_ms')::integer>=2000 THEN LEAST((p_evidence->>'elapsed_ms')::integer,60000) END END;
    UPDATE control.origin_buckets SET in_flight_count=(SELECT count(*) FROM control.admission_leases
        WHERE bucket_id=v_bucket.id AND released_at IS NULL),
        next_allowed_at=CASE WHEN v_backoff IS NULL THEN next_allowed_at ELSE
            GREATEST(next_allowed_at,v_now+v_backoff*interval '1 millisecond') END,
        degraded_until=CASE WHEN p_completion IN ('rate_limited','service_unavailable') THEN
            GREATEST(degraded_until,v_now+v_backoff*interval '1 millisecond') ELSE degraded_until END,updated_at=v_now
     WHERE id=v_bucket.id;
    RETURN 'finished';
END;
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501';
END IF;
END;
$function$;

ALTER FUNCTION control.port_finish_owner_connector_egress(text,bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.port_finish_owner_connector_egress(text,bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.finish_owner_connector_egress(p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_evidence jsonb, p_completion text, p_retry_after_ms integer, p_robots_allowed boolean, p_rules_sha256 bytea, p_crawl_delay_ms integer)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_owner_connector_egress('owner',p_session_hash,p_generation,p_site_id,p_operation_id,p_evidence,p_completion,p_retry_after_ms,p_robots_allowed,p_rules_sha256,p_crawl_delay_ms);
END;
$function$;

REVOKE ALL ON FUNCTION control.finish_owner_connector_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.finish_owner_connector_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_skill_finish_pagespeed_egress(p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_evidence jsonb, p_completion text, p_retry_after_ms integer, p_robots_allowed boolean, p_rules_sha256 bytea, p_crawl_delay_ms integer)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_owner_connector_egress('weekly_skill',p_session_hash,p_generation,p_site_id,p_operation_id,p_evidence,p_completion,p_retry_after_ms,p_robots_allowed,p_rules_sha256,p_crawl_delay_ms);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_finish_pagespeed_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_finish_pagespeed_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.weekly_skill_finish_dataforseo_egress(p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid, p_evidence jsonb, p_completion text, p_retry_after_ms integer, p_robots_allowed boolean, p_rules_sha256 bytea, p_crawl_delay_ms integer)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    RETURN control.port_finish_owner_connector_egress('strategy_provider',p_session_hash,p_generation,p_site_id,p_operation_id,p_evidence,p_completion,p_retry_after_ms,p_robots_allowed,p_rules_sha256,p_crawl_delay_ms);
END;
$function$;

REVOKE ALL ON FUNCTION control.weekly_skill_finish_dataforseo_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.weekly_skill_finish_dataforseo_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) TO signal_workflow;
-- End port group 64.
CREATE OR REPLACE FUNCTION control.port_schedule_pagespeed_sample(p_actor text,p_tenant uuid, p_session bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$

DECLARE a record; v_week date:=(date_trunc('week',clock_timestamp() AT TIME ZONE 'UTC'))::date;
    v_generation uuid; v_rows jsonb:='[]'; v_result jsonb; v_page_dimension integer;
BEGIN
    IF p_actor='owner' THEN
SELECT * INTO a FROM control.admit_port_context('owner','pagespeed',p_session,p_generation,p_site,NULL);
ELSIF p_actor='weekly_plan' THEN
SELECT p_tenant AS tenant_id,v.origin INTO a FROM control.verified_site_origin(p_tenant,p_site) v WHERE v.outcome='verified';
ELSE RAISE EXCEPTION 'port_admission_unavailable' USING ERRCODE='42501'; END IF;
    IF NOT control.port_permission(CASE WHEN FOUND THEN 'authorized' ELSE 'authorization_denied' END,NULL,NULL,true) THEN RETURN jsonb_build_object('outcome','denied'); END IF;
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
END
$function$;

ALTER FUNCTION control.port_schedule_pagespeed_sample(text,uuid,bytea,text,uuid) OWNER TO signal_migrator;

REVOKE ALL ON FUNCTION control.port_schedule_pagespeed_sample(text,uuid,bytea,text,uuid) FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest,signal_authority_dispatcher,signal_release_manager;

CREATE OR REPLACE FUNCTION control.schedule_pagespeed_sample(p_session bytea, p_generation text, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ BEGIN RETURN control.port_schedule_pagespeed_sample('owner',NULL,p_session,p_generation,p_site); END; $function$;

REVOKE ALL ON FUNCTION control.schedule_pagespeed_sample(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.schedule_pagespeed_sample(bytea,text,uuid) TO signal_identity;

CREATE OR REPLACE FUNCTION control.weekly_pagespeed_plan(p_tenant uuid, p_site uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ BEGIN RETURN control.port_schedule_pagespeed_sample('weekly_plan',p_tenant,NULL,NULL,p_site); END; $function$;

REVOKE ALL ON FUNCTION control.weekly_pagespeed_plan(uuid,uuid) FROM PUBLIC;
-- End port group 65.
CREATE OR REPLACE FUNCTION control.assistant_scope(p_hash bytea, p_generation text, p_site uuid)
 RETURNS TABLE(tenant_id uuid, user_id uuid, role_key text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE a record; BEGIN
 IF session_user NOT IN ('signal_api','signal_identity') THEN RETURN; END IF;
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF NOT control.port_permission(a.outcome,NULL,NULL,true) THEN RETURN; END IF;
 PERFORM set_config('signal.actor_user_id',a.user_id::text,true);
 IF EXISTS(SELECT 1 FROM control.assistant_retention_scopes s WHERE s.tenant_id=a.tenant_id AND s.site_id=p_site AND s.user_id=a.user_id AND s.next_due<=transaction_timestamp()) THEN
  PERFORM control.assistant_prune_scope();
 END IF;
 RETURN QUERY SELECT a.tenant_id,a.user_id,a.role_key;
END; $function$;

REVOKE ALL ON FUNCTION control.assistant_scope(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.business_brain_owner(p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS TABLE(tenant_id uuid, user_id uuid, membership_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
 IF control.port_permission(v.outcome,v.role_key,'owner',false) THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(v.tenant_id::text||p_site_id::text,73));
  RETURN QUERY SELECT v.tenant_id,v.user_id,m.id FROM app.memberships m WHERE m.tenant_id=v.tenant_id AND m.user_id=v.user_id;
 END IF;
END; $function$;

REVOKE ALL ON FUNCTION control.business_brain_owner(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.business_brain_owner(bytea,text,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.brand_document_owner(p_session_hash bytea, p_generation text, p_site_id uuid)
 RETURNS TABLE(tenant_id uuid, user_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF control.port_permission(v_authority.outcome,v_authority.role_key,'owner',false) THEN
        RETURN QUERY SELECT v_authority.tenant_id, v_authority.user_id;
    END IF;
END;
$function$;

REVOKE ALL ON FUNCTION control.brand_document_owner(bytea,text,uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION control.brand_document_owner(bytea,text,uuid) TO signal_api;

CREATE OR REPLACE FUNCTION control.ga4_owner_context(p_session bytea, p_generation text, p_site uuid)
 RETURNS TABLE(tenant_id uuid, user_id uuid, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE a record;o record; BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
 IF NOT control.port_permission(a.outcome,a.role_key,'owner',false) OR a.authentication_level<>'mfa' THEN RETURN; END IF;
 SELECT * INTO o FROM control.verified_site_origin(a.tenant_id,p_site);
 IF o.outcome<>'verified' THEN RETURN; END IF;
 RETURN QUERY SELECT a.tenant_id,a.user_id,o.origin;
END $function$;

REVOKE ALL ON FUNCTION control.ga4_owner_context(bytea,text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION control.gsc_owner_context(p_session_hash bytea, p_recovery_generation text, p_site_id uuid)
 RETURNS TABLE(tenant_id uuid, user_id uuid, origin text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_authority record; v_origin record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_recovery_generation
    );
    IF NOT control.port_permission(v_authority.outcome,v_authority.role_key,'owner',false) THEN
        RETURN;
    END IF;
    SELECT * INTO v_origin FROM control.verified_site_origin(v_authority.tenant_id, p_site_id);
    IF v_origin.outcome <> 'verified' THEN RETURN; END IF;
    RETURN QUERY SELECT v_authority.tenant_id, v_authority.user_id, v_origin.origin;
END;
$function$;

REVOKE ALL ON FUNCTION control.gsc_owner_context(bytea,text,uuid) FROM PUBLIC;
