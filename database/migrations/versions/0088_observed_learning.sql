-- Preserve the existing evidence/proposal boundary, extending only its read packet.
ALTER FUNCTION control.seo_strategy_sources(bytea,text,uuid) RENAME TO seo_strategy_base_sources;
REVOKE ALL ON FUNCTION control.seo_strategy_base_sources(bytea,text,uuid) FROM PUBLIC,signal_api;

CREATE FUNCTION control.seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; packet jsonb; measures jsonb; history jsonb; changes jsonb; binding uuid;
BEGIN
    packet:=control.seo_strategy_base_sources(p_hash,p_generation,p_site);
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
END; $$;
REVOKE ALL ON FUNCTION control.seo_strategy_sources(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.seo_strategy_sources(bytea,text,uuid) TO signal_api;

-- The existing workload base remains stage/handle-bound; extend its read packet
-- through the same implementation without borrowing an owner session.
ALTER FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid)
    RENAME TO weekly_skill_seo_strategy_base_sources;
REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_base_sources(bytea,text,uuid)
    FROM PUBLIC,signal_workflow;
DO $$ DECLARE definition text; BEGIN
    SELECT pg_get_functiondef('control.seo_strategy_sources(bytea,text,uuid)'::regprocedure) INTO definition;
    definition:=replace(definition,'FUNCTION control.seo_strategy_sources(',
        'FUNCTION control.weekly_skill_seo_strategy_sources(');
    definition:=replace(definition,'control.seo_strategy_base_sources(',
        'control.weekly_skill_seo_strategy_base_sources(');
    definition:=replace(definition,'control.business_brain_owner(', 'control.weekly_skill_context(');
    definition:=regexp_replace(definition,'BEGIN',
        'BEGIN PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY[''strategy_rebuild'']);');
    EXECUTE definition;
END $$;
REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) TO signal_workflow;
