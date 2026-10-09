ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check CHECK (egress_profile IN (
    'legacy_unqualified','crawl_page','crawl_robots','browser_read','github_rest','slack_bot','slack_oauth',
    'github_repository_write','google_oauth_token','google_oauth_revoke','gsc_api','bing_oauth_token','bing_api',
    'jev','model_json','openai_model','openai_assistant','perplexity_assistant','gemini_assistant','npm_registry',
    'crawl_key_file','indexnow_submit','dataforseo','ga4_admin','ga4_data','telegram_bot',
    'browser_worker_read','wordpress_rest','drive_metadata','drive_export','webflow','webflow_oauth','webflow_revoke'
));
ALTER FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) RENAME TO bind_before_npm_egress_profile;
REVOKE ALL ON FUNCTION control.bind_before_npm_egress_profile(uuid,uuid,uuid,bytea,text) FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(p_tenant_id uuid,p_site_id uuid,p_operation_id uuid,p_request_sha256 bytea,p_profile text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE;
BEGIN
    IF p_profile IS DISTINCT FROM 'npm_registry' THEN RETURN control.bind_before_npm_egress_profile(p_tenant_id,p_site_id,p_operation_id,p_request_sha256,p_profile); END IF;
    PERFORM set_config('signal.tenant_id',p_tenant_id::text,true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id FOR UPDATE;
    IF NOT FOUND OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_request_sha256
       OR o.purpose<>'connector' OR o.method<>'GET' OR o.credentialed OR o.request_bytes<>0 OR o.request_body_sha256 IS NOT NULL
       OR o.origin<>'https://registry.npmjs.org' OR o.max_response_bytes>5242880
       OR o.request_url !~ '^https://registry[.]npmjs[.]org/(@[a-z0-9][a-z0-9._-]*/)?([a-z0-9][a-z0-9._-]*)/-/\2-[0-9]+[.][0-9]+[.][0-9]+(-[A-Za-z0-9.-]+)?([+][A-Za-z0-9.-]+)?[.]tgz$'
       OR length(o.request_url)>1024 OR o.egress_profile NOT IN ('legacy_unqualified','npm_registry')
    THEN RAISE EXCEPTION 'npm_registry_profile_denied' USING ERRCODE='22023'; END IF;
    IF o.egress_profile='legacy_unqualified' THEN UPDATE app.egress_operations SET egress_profile=p_profile WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id; END IF;
    RETURN 'bound';
END $$;

ALTER FUNCTION control.dispatch_candidate_build(bytea,uuid,text,uuid) RENAME TO dispatch_before_dependencies;
REVOKE ALL ON FUNCTION control.dispatch_before_dependencies(bytea,uuid,text,uuid) FROM signal_identity;
CREATE FUNCTION control.dispatch_candidate_build(p_session bytea,p_site uuid,p_generation text,p_build uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.candidate_build_intents%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
    IF a.role_key IS DISTINCT FROM 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_build FOR UPDATE;
    IF FOUND AND EXISTS(SELECT 1 FROM app.github_pr_extensions e WHERE e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id AND e.framework='astro')
       AND NOT EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id)
    THEN RETURN 'dependency_receipt_required'; END IF;
    RETURN control.dispatch_before_dependencies(p_session,p_site,p_generation,p_build);
END $$;
REVOKE ALL ON FUNCTION control.dispatch_candidate_build(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.dispatch_candidate_build(bytea,uuid,text,uuid) TO signal_identity;

ALTER FUNCTION control.finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) RENAME TO finish_before_dependencies;
REVOKE ALL ON FUNCTION control.finish_before_dependencies(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM signal_identity;
CREATE FUNCTION control.finish_candidate_build(p_session bytea,p_site uuid,p_generation text,p_build uuid,p_exit text,p_code integer,p_logs text,p_bytes integer,p_artifacts jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
    IF a.role_key IS DISTINCT FROM 'owner' THEN RETURN 'permission_denied'; END IF;
    IF EXISTS(SELECT 1 FROM app.candidate_build_intents b JOIN app.github_pr_extensions e ON e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.id=p_build AND e.framework='astro')
    THEN RETURN 'dependency_receipt_required'; END IF;
    RETURN control.finish_before_dependencies(p_session,p_site,p_generation,p_build,p_exit,p_code,p_logs,p_bytes,p_artifacts);
END $$;
REVOKE ALL ON FUNCTION control.finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.finish_candidate_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb) TO signal_identity;
REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;

CREATE FUNCTION control.valid_astro_output_directory(p_path text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT coalesce(length(p_path) BETWEEN 1 AND 120 AND p_path ~ '^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$' AND p_path !~ '(^|/)node_modules(/|$)',false);
$$;
REVOKE ALL ON FUNCTION control.valid_astro_output_directory(text) FROM PUBLIC;
CREATE OR REPLACE FUNCTION control.prepare_candidate_build(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_extension_id uuid,
    p_build_id uuid, p_request_id uuid, p_request_hash bytea,
    p_base_sha text, p_tree_sha text, p_patch_sha256 text,
    p_toolchain text, p_build_command text, p_artifact_root text
) RETURNS TABLE (build_id uuid, build_status text, replayed boolean, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_extension app.github_pr_extensions%%ROWTYPE;
        v_existing app.candidate_build_intents%%ROWTYPE;
BEGIN
    IF p_extension_id IS NULL OR p_build_id IS NULL OR p_request_id IS NULL
       OR p_request_hash IS NULL OR octet_length(p_request_hash) <> 32
       OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
       OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
       OR p_patch_sha256 IS NULL OR p_patch_sha256 !~ '^[0-9a-f]{64}$'
       OR p_toolchain IS DISTINCT FROM
        'node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
       OR p_build_command IS DISTINCT FROM 'npm run build'
       OR p_artifact_root IS NULL OR NOT (p_artifact_root IN ('.next','_site') OR control.valid_astro_output_directory(p_artifact_root)) THEN
        RAISE EXCEPTION 'invalid_candidate_build_input' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, false, v_authority.outcome; RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' THEN
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
        OR (v_extension.framework = 'astro' AND control.valid_astro_output_directory(p_artifact_root)
            AND p_patch_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
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
END; $$;
ALTER TABLE app.candidate_build_intents DROP CONSTRAINT candidate_build_intents_artifact_root_check;
ALTER TABLE app.candidate_build_intents ADD CONSTRAINT candidate_build_intents_artifact_root_check CHECK (artifact_root IN ('.next','_site') OR control.valid_astro_output_directory(artifact_root));

CREATE TABLE app.candidate_dependency_inputs (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,build_id uuid NOT NULL,
    lockfile_sha256 text NOT NULL CHECK (lockfile_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,build_id),
    FOREIGN KEY(tenant_id,site_id,build_id) REFERENCES app.candidate_build_intents(tenant_id,site_id,id)
);
CREATE TABLE app.candidate_dependency_receipts (
    tenant_id uuid NOT NULL,site_id uuid NOT NULL,build_id uuid NOT NULL,
    lockfile_sha256 text NOT NULL CHECK (lockfile_sha256 ~ '^[0-9a-f]{64}$'),
    install_command text NOT NULL DEFAULT 'npm ci --ignore-scripts --offline' CHECK (install_command='npm ci --ignore-scripts --offline'),
    build_command text NOT NULL DEFAULT 'npm run build --ignore-scripts --offline' CHECK (build_command='npm run build --ignore-scripts --offline'),
    built_pages jsonb NOT NULL CHECK (jsonb_typeof(built_pages)='array' AND jsonb_array_length(built_pages)<=1000 AND octet_length(built_pages::text)<=131072),
    unavailable_reason text CHECK (unavailable_reason IN ('NPM_OFFLINE_INSTALL_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_LIMIT_EXCEEDED','ASTRO_BUILT_OUTPUT_REJECTED')),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(tenant_id,site_id,build_id),
    FOREIGN KEY(tenant_id,site_id,build_id) REFERENCES app.candidate_build_receipts(tenant_id,site_id,build_id),
    FOREIGN KEY(tenant_id,site_id,build_id) REFERENCES app.candidate_dependency_inputs(tenant_id,site_id,build_id)
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['candidate_dependency_inputs','candidate_dependency_receipts'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY dependency_scope ON app.%%I USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
        EXECUTE format('CREATE TRIGGER dependency_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_identity,signal_api,signal_bootstrap,signal_scheduler,signal_workflow,signal_crawl_admission,signal_crawl_ingest',t);
    END LOOP;
END $$;

CREATE FUNCTION control.bind_candidate_dependencies(p_session bytea,p_site uuid,p_generation text,p_build uuid,p_lock text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.candidate_build_intents%%ROWTYPE;v_existing text;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_build FOR UPDATE;
    IF NOT FOUND OR b.requested_by_user_id<>a.user_id OR b.membership_epoch<>a.membership_epoch OR b.site_epoch<>a.site_authorization_epoch OR b.recovery_generation<>p_generation
       OR b.status NOT IN ('prepared','completed') OR NOT control.current_github_site_proof(a.tenant_id,p_site)
       OR NOT EXISTS(SELECT 1 FROM app.github_pr_extensions e WHERE e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id AND e.framework='astro')
       OR p_lock IS NULL OR p_lock !~ '^[0-9a-f]{64}$' THEN RETURN 'build_not_authorized'; END IF;
    SELECT lockfile_sha256 INTO v_existing FROM app.candidate_dependency_inputs WHERE tenant_id=b.tenant_id AND site_id=b.site_id AND build_id=b.id;
    IF FOUND THEN RETURN CASE WHEN v_existing=p_lock THEN 'bound' ELSE 'receipt_conflict' END; END IF;
    IF b.status<>'prepared' THEN RETURN 'dispatch_unknown'; END IF;
    INSERT INTO app.candidate_dependency_inputs(tenant_id,site_id,build_id,lockfile_sha256) VALUES(b.tenant_id,b.site_id,b.id,p_lock);
    RETURN 'bound';
END $$;
REVOKE ALL ON FUNCTION control.bind_candidate_dependencies(bytea,uuid,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_candidate_dependencies(bytea,uuid,text,uuid,text) TO signal_identity;

CREATE FUNCTION control.finish_candidate_dependency_build(p_session bytea,p_site uuid,p_generation text,p_build uuid,p_exit text,p_code integer,p_logs text,p_bytes integer,p_artifacts jsonb,p_lock text,p_pages jsonb,p_reason text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;b app.candidate_build_intents%%ROWTYPE;d app.candidate_dependency_receipts%%ROWTYPE;item jsonb;v_outcome text;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_build FOR UPDATE;
    IF NOT FOUND OR b.requested_by_user_id<>a.user_id OR b.membership_epoch<>a.membership_epoch OR b.site_epoch<>a.site_authorization_epoch OR b.recovery_generation<>p_generation
       OR NOT EXISTS(SELECT 1 FROM app.candidate_dependency_inputs i WHERE i.tenant_id=b.tenant_id AND i.site_id=b.site_id AND i.build_id=b.id AND i.lockfile_sha256=p_lock)
    THEN RETURN 'build_not_authorized'; END IF;
    IF p_pages IS NULL OR jsonb_typeof(p_pages)<>'array' OR jsonb_array_length(p_pages)>1000 OR octet_length(p_pages::text)>131072 OR p_artifacts IS NULL OR jsonb_typeof(p_artifacts)<>'array'
       OR (p_exit='passed' AND (jsonb_array_length(p_pages)=0 OR p_reason IS NOT NULL))
       OR (p_exit<>'passed' AND jsonb_array_length(p_pages)<>0)
       OR (p_reason IS NOT NULL AND p_reason NOT IN ('NPM_OFFLINE_INSTALL_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED','ASTRO_BUILD_LIMIT_EXCEEDED','ASTRO_BUILT_OUTPUT_REJECTED'))
    THEN RETURN 'invalid_receipt'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(p_pages) LOOP
        IF jsonb_typeof(item)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(item))<>4 OR NOT(item ?& ARRAY['path','sha256','title','description'])
           OR jsonb_typeof(item->'path')<>'string' OR jsonb_typeof(item->'sha256')<>'string'
           OR jsonb_typeof(item->'title') NOT IN ('null','string') OR jsonb_typeof(item->'description') NOT IN ('null','string')
           OR octet_length(coalesce(item->>'title',''))>4096 OR octet_length(coalesce(item->>'description',''))>4096
           OR right(item->>'path',5)<>'.html'
           OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_artifacts) f WHERE f->>'path'=item->>'path' AND f->>'sha256'=item->>'sha256')
        THEN RETURN 'invalid_receipt'; END IF;
    END LOOP;
    IF p_exit='passed' AND ((SELECT count(DISTINCT page.value->>'path') FROM jsonb_array_elements(p_pages) AS page(value))<>jsonb_array_length(p_pages)
       OR (SELECT count(*) FROM jsonb_array_elements(p_artifacts) f WHERE right(f->>'path',5)='.html')<>jsonb_array_length(p_pages))
    THEN RETURN 'invalid_receipt'; END IF;
    SELECT * INTO d FROM app.candidate_dependency_receipts WHERE tenant_id=b.tenant_id AND site_id=b.site_id AND build_id=b.id;
    IF FOUND AND (d.lockfile_sha256<>p_lock OR d.built_pages<>p_pages OR d.unavailable_reason IS DISTINCT FROM p_reason) THEN RETURN 'receipt_conflict'; END IF;
    v_outcome := control.finish_before_dependencies(p_session,p_site,p_generation,p_build,p_exit,p_code,p_logs,p_bytes,p_artifacts);
    IF v_outcome<>'completed' THEN RETURN v_outcome; END IF;
    IF d.build_id IS NULL THEN INSERT INTO app.candidate_dependency_receipts(tenant_id,site_id,build_id,lockfile_sha256,built_pages,unavailable_reason) VALUES(b.tenant_id,b.site_id,b.id,p_lock,p_pages,p_reason); END IF;
    RETURN 'completed';
END $$;
REVOKE ALL ON FUNCTION control.finish_candidate_dependency_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.finish_candidate_dependency_build(bytea,uuid,text,uuid,text,integer,text,integer,jsonb,text,jsonb,text) TO signal_identity;

CREATE FUNCTION control.read_candidate_dependency_receipt(p_session bytea,p_site uuid,p_generation text,p_build uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;v_result jsonb;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
    SELECT jsonb_build_object('lockfile_sha256',d.lockfile_sha256,'built_pages',d.built_pages,'unavailable_reason',d.unavailable_reason) INTO v_result
    FROM app.candidate_dependency_receipts d JOIN app.candidate_build_intents b ON b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.id=d.build_id
    WHERE d.tenant_id=a.tenant_id AND d.site_id=p_site AND d.build_id=p_build AND b.requested_by_user_id=a.user_id;
    RETURN v_result;
END $$;
REVOKE ALL ON FUNCTION control.read_candidate_dependency_receipt(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_candidate_dependency_receipt(bytea,uuid,text,uuid) TO signal_identity;
