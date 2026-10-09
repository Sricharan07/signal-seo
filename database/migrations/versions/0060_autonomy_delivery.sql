CREATE TABLE control.recipe_autonomy_attestations (
    id uuid PRIMARY KEY,
    release_id uuid NOT NULL UNIQUE REFERENCES control.recipe_releases(id),
    content_hash bytea NOT NULL CHECK (octet_length(content_hash)=32),
    policy_version text NOT NULL CHECK (policy_version='technical-a2-1'),
    work_type text NOT NULL CHECK (work_type='metadata_pr'),
    actor_user_id uuid NOT NULL REFERENCES control.users(id),
    attested_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TRIGGER recipe_autonomy_attestations_immutable BEFORE UPDATE OR DELETE
ON control.recipe_autonomy_attestations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.recipe_autonomy_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.recipe_autonomy_attestations FORCE ROW LEVEL SECURITY;
CREATE POLICY recipe_autonomy_attestation_owner ON control.recipe_autonomy_attestations
TO signal_migrator USING (true) WITH CHECK (true);
REVOKE ALL ON control.recipe_autonomy_attestations FROM PUBLIC;

CREATE FUNCTION control.technical_a2_release_shape(p_release_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT EXISTS (
    SELECT 1 FROM control.recipe_releases r
    WHERE r.id=p_release_id
      AND r.recipe_key IN ('technical_title','technical_description','technical_alt',
                           'technical_structured_data','technical_broken_link')
      AND r.contract_version=1 AND r.version_major=1 AND r.version_minor=0
      AND convert_from(r.canonical_body,'UTF8')::jsonb = jsonb_build_object(
        'schema_version',1,'kind','recipe','release_id',r.id::text,
        'recipe_key',r.recipe_key,
        'version','1.0.' || r.version_patch::text,'contract_version',1,
        'delivery_mode','pull_request','max_resources_per_revision',1,
        'allowed_fields',jsonb_build_array(CASE r.recipe_key
            WHEN 'technical_title' THEN 'title'
            WHEN 'technical_description' THEN 'meta_description'
            WHEN 'technical_alt' THEN 'image_alt'
            WHEN 'technical_structured_data' THEN 'json_ld'
            WHEN 'technical_broken_link' THEN 'internal_link' END),
        'allowed_resource_types',jsonb_build_array('static_html_homepage'),
        'required_evidence',jsonb_build_array('committed_crawl_finding','exact_source_body_digest'),
        'steps',jsonb_build_array('one_scoped_patch','isolated_candidate_build'),
        'verification_assertions',jsonb_build_array('exact_base','protected_path_preflight','build_passed'),
        'purpose','Resolve one committed technical SEO finding on one exact static page.',
        'approval_class','owner_review','recovery_mode','revert_exact_patch')
);
$$;
REVOKE ALL ON FUNCTION control.technical_a2_release_shape(uuid) FROM PUBLIC;

CREATE FUNCTION control.recipe_autonomy_eligible(p_release_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT control.technical_a2_release_shape(p_release_id) AND EXISTS (
    SELECT 1 FROM control.recipe_autonomy_attestations a
    JOIN control.recipe_releases r ON r.id=a.release_id AND r.content_hash=a.content_hash
    WHERE a.release_id=p_release_id AND a.policy_version='technical-a2-1'
      AND (SELECT e.status FROM control.recipe_release_events e
           WHERE e.release_id=r.id ORDER BY e.sequence_number DESC LIMIT 1)='REVIEWED'
      AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
          WHERE t.target_kind='recipe_release' AND t.target_id=r.id)
);
$$;
REVOKE ALL ON FUNCTION control.recipe_autonomy_eligible(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.recipe_autonomy_eligible(uuid)
TO signal_release_manager,signal_api,signal_workflow;

CREATE FUNCTION control.attest_recipe_autonomy(
    p_release_id uuid,p_content_hash bytea,p_actor_id uuid,p_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user != 'signal_release_manager' OR p_id IS NULL
       OR NOT control.technical_a2_release_shape(p_release_id)
       OR NOT EXISTS (SELECT 1 FROM control.users WHERE id=p_actor_id AND disabled_at IS NULL)
       OR NOT EXISTS (SELECT 1 FROM control.recipe_releases r
           WHERE r.id=p_release_id AND r.content_hash=p_content_hash
             AND (SELECT e.status FROM control.recipe_release_events e
                  WHERE e.release_id=r.id ORDER BY e.sequence_number DESC LIMIT 1)='REVIEWED'
             AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                 WHERE t.target_kind='recipe_release' AND t.target_id=r.id)) THEN
        RETURN 'attestation_denied';
    END IF;
    INSERT INTO control.recipe_autonomy_attestations
        (id,release_id,content_hash,policy_version,work_type,actor_user_id)
    VALUES (p_id,p_release_id,p_content_hash,'technical-a2-1','metadata_pr',p_actor_id)
    ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM control.recipe_autonomy_attestations
        WHERE id=p_id AND release_id=p_release_id AND content_hash=p_content_hash
          AND actor_user_id=p_actor_id) THEN RETURN 'attestation_conflict'; END IF;
    RETURN 'attested';
END $$;

CREATE FUNCTION control.read_weekly_delivery_report(
    p_session_hash bytea,p_site_id uuid,p_generation text,p_week date
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_report jsonb; v_tenant uuid;
BEGIN
    v_report := control.read_weekly_cycle_report(p_session_hash,p_site_id,p_generation,p_week);
    IF v_report IS NULL THEN RETURN NULL; END IF;
    v_tenant := current_setting('signal.tenant_id')::uuid;
    RETURN v_report || jsonb_build_object('delivery',COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'workload_id',j.id,'finding_id',j.finding_id,'recipe_release_id',j.recipe_release_id,
            'revision_id',j.revision_id,'revision_sha256',encode(r.revision_sha256,'hex'),
            'operation_id',o.id,'operation_state',o.state,'pr_url',o.pr_url,
            'authority_kind',o.authority_kind,
            'authority_id',COALESCE(o.decision_id,o.standing_dispatch_id),
            'decision_channel',o.decision_channel,
            'authorization_owner_id',o.requested_by_user_id,
            'review_status',d.decision,'observation_id',receipt.attempt_id,
            'observation_sha256',encode(receipt.receipt_sha256,'hex'),
            'delivery_outcome',receipt.document->>'outcome',
            'delivery_reason',receipt.document->>'reason',
            'delivery_stage',receipt.document->'provider'->>'stage'
        ) ORDER BY j.created_at,j.id)
        FROM app.weekly_delivery_workloads j
        LEFT JOIN app.candidate_recipe_revisions r
          ON r.tenant_id=j.tenant_id AND r.site_id=j.site_id AND r.id=j.revision_id
        LEFT JOIN app.github_pr_operations o
          ON o.tenant_id=j.tenant_id AND o.site_id=j.site_id AND o.id=j.operation_id
        LEFT JOIN app.candidate_recipe_review_decisions d
          ON d.tenant_id=j.tenant_id AND d.site_id=j.site_id AND d.candidate_revision_id=j.revision_id
        LEFT JOIN LATERAL (
            SELECT x.attempt_id,x.receipt_sha256,
                convert_from(x.canonical_receipt,'UTF8')::jsonb AS document
            FROM app.github_delivery_receipts x
            WHERE x.tenant_id=j.tenant_id AND x.site_id=j.site_id AND x.operation_id=j.operation_id
            ORDER BY x.recorded_at DESC,x.attempt_id DESC LIMIT 1
        ) receipt ON true
        WHERE j.tenant_id=v_tenant AND j.site_id=p_site_id
          AND (j.cycle_id=(v_report->>'cycle_id')::uuid OR EXISTS (
              SELECT 1 FROM app.weekly_stage_results stage
              WHERE stage.tenant_id=j.tenant_id AND stage.site_id=j.site_id AND stage.week_start=p_week
                AND ('operation:' || j.operation_id::text)=ANY(stage.evidence_refs)))
    ),'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION control.read_weekly_delivery_report(bytea,uuid,text,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_weekly_delivery_report(bytea,uuid,text,date) TO signal_api;
REVOKE ALL ON FUNCTION control.attest_recipe_autonomy(uuid,bytea,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.attest_recipe_autonomy(uuid,bytea,uuid,uuid)
TO signal_release_manager;

CREATE FUNCTION control.standing_release_matches_work(p_release_id uuid,p_work_type text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT CASE
    WHEN r.recipe_key LIKE 'technical_%%' THEN
        p_work_type='metadata_pr' AND control.recipe_autonomy_eligible(r.id)
    ELSE convert_from(r.canonical_body,'UTF8')::jsonb->>'approval_class' =
        CASE WHEN p_work_type='research_audit' THEN 'A0'
             WHEN p_work_type='draft_patch' THEN 'A1' ELSE 'A2' END
        AND (p_work_type IN ('research_audit','draft_patch') OR
            (convert_from(r.canonical_body,'UTF8')::jsonb->>'delivery_mode'='pull_request'
             AND convert_from(r.canonical_body,'UTF8')::jsonb->>'standing_work_type'=p_work_type))
    END FROM control.recipe_releases r WHERE r.id=p_release_id;
$$;
REVOKE ALL ON FUNCTION control.standing_release_matches_work(uuid,text) FROM PUBLIC;

CREATE TABLE app.weekly_delivery_workloads (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    cycle_id uuid NOT NULL REFERENCES app.weekly_cycles(id),
    grant_id uuid NOT NULL,
    report_id uuid NOT NULL,
    finding_id uuid NOT NULL,
    recipe_release_id uuid NOT NULL REFERENCES control.recipe_releases(id),
    extension_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    gate_decision_id uuid NOT NULL,
    recipe_key text NOT NULL,
    revision_key uuid NOT NULL,
    build_key uuid NOT NULL,
    handle_hash bytea NOT NULL CHECK (octet_length(handle_hash)=32),
    recovery_generation text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,id),
    UNIQUE (handle_hash),
    UNIQUE (tenant_id,site_id,cycle_id,finding_id),
    UNIQUE (tenant_id,site_id,operation_id),
    FOREIGN KEY (tenant_id,site_id,grant_id)
        REFERENCES app.standing_authorizations(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,report_id)
        REFERENCES app.crawl_audit_reports(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,extension_id)
        REFERENCES app.github_pr_extensions(tenant_id,site_id,id)
);
CREATE TRIGGER weekly_delivery_workloads_immutable BEFORE UPDATE OR DELETE
ON app.weekly_delivery_workloads FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE TABLE control.weekly_delivery_directory (
    handle_hash bytea PRIMARY KEY CHECK (octet_length(handle_hash)=32),
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    workload_id uuid NOT NULL,
    recovery_generation text NOT NULL,
    FOREIGN KEY (tenant_id,site_id,workload_id)
        REFERENCES app.weekly_delivery_workloads(tenant_id,site_id,id)
);
CREATE TRIGGER weekly_delivery_directory_immutable BEFORE UPDATE OR DELETE
ON control.weekly_delivery_directory FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE control.weekly_delivery_directory ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.weekly_delivery_directory FORCE ROW LEVEL SECURITY;
CREATE POLICY weekly_delivery_directory_owner ON control.weekly_delivery_directory
TO signal_migrator USING (true) WITH CHECK (true);
REVOKE ALL ON control.weekly_delivery_directory FROM PUBLIC;

CREATE TABLE app.standing_dispatch_authorizations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    workload_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    grant_version_sha256 bytea NOT NULL CHECK (octet_length(grant_version_sha256)=32),
    recovery_generation text NOT NULL,
    gate_decision_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    revision_sha256 bytea NOT NULL CHECK (octet_length(revision_sha256)=32),
    recipe_release_id uuid NOT NULL REFERENCES control.recipe_releases(id),
    autonomy_attestation_id uuid NOT NULL REFERENCES control.recipe_autonomy_attestations(id),
    reservation_operation_id uuid NOT NULL,
    threshold numeric NOT NULL CHECK (threshold BETWEEN 0 AND 1),
    confidence numeric NOT NULL CHECK (confidence>=threshold AND confidence<=1),
    policy_version text NOT NULL CHECK (policy_version='technical-a2-1'),
    authorized_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,id),
    UNIQUE (tenant_id,site_id,workload_id),
    UNIQUE (tenant_id,site_id,reservation_operation_id),
    FOREIGN KEY (tenant_id,site_id,workload_id)
        REFERENCES app.weekly_delivery_workloads(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,grant_id)
        REFERENCES app.standing_authorizations(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,gate_decision_id)
        REFERENCES app.autonomy_gate_records(tenant_id,site_id,id),
    FOREIGN KEY (tenant_id,site_id,reservation_operation_id)
        REFERENCES app.autonomy_reservations(tenant_id,site_id,operation_id),
    FOREIGN KEY (tenant_id,site_id,revision_id,revision_sha256)
        REFERENCES app.candidate_recipe_revisions(tenant_id,site_id,id,revision_sha256)
);
CREATE TRIGGER standing_dispatch_authorizations_immutable BEFORE UPDATE OR DELETE
ON app.standing_dispatch_authorizations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['weekly_delivery_workloads','standing_dispatch_authorizations'] LOOP
        EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY %%I_scope ON app.%%I USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',name,name);
        EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_workflow,signal_scheduler,signal_identity,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',name);
    END LOOP;
END $$;

ALTER TABLE app.github_pr_operations
    ALTER COLUMN decision_id DROP NOT NULL,
    ADD COLUMN authority_kind text NOT NULL DEFAULT 'owner_inbox'
        CHECK (authority_kind IN ('owner_inbox','standing_grant')),
    ADD COLUMN standing_dispatch_id uuid,
    ADD COLUMN decision_channel text CHECK (decision_channel IN ('dashboard','slack')),
    ADD FOREIGN KEY (tenant_id,site_id,standing_dispatch_id)
        REFERENCES app.standing_dispatch_authorizations(tenant_id,site_id,id),
    ADD CHECK ((authority_kind='owner_inbox' AND decision_id IS NOT NULL AND standing_dispatch_id IS NULL)
        OR (authority_kind='standing_grant' AND decision_id IS NULL AND standing_dispatch_id IS NOT NULL));

DO $$ DECLARE v_scope record;
BEGIN
    FOR v_scope IN SELECT tenant_id,site_id FROM control.tenant_site_routes LOOP
        PERFORM set_config('signal.tenant_id',v_scope.tenant_id::text,true);
        PERFORM set_config('signal.site_id',v_scope.site_id::text,true);
        UPDATE app.github_pr_operations o SET decision_channel=d.decision_channel
        FROM app.candidate_recipe_review_decisions d
        WHERE d.tenant_id=o.tenant_id AND d.site_id=o.site_id AND d.id=o.decision_id;
    END LOOP;
    PERFORM set_config('signal.tenant_id','',true);
    PERFORM set_config('signal.site_id','',true);
END $$;
ALTER TABLE app.github_pr_operations ADD CHECK (
    (authority_kind='owner_inbox' AND decision_channel IS NOT NULL)
    OR (authority_kind='standing_grant' AND decision_channel IS NULL));

CREATE FUNCTION control.bind_github_pr_decision_channel() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_channel text;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF (NEW.authority_kind,NEW.decision_id,NEW.standing_dispatch_id,
            NEW.decision_channel,NEW.requested_by_user_id) IS DISTINCT FROM
            (OLD.authority_kind,OLD.decision_id,OLD.standing_dispatch_id,
            OLD.decision_channel,OLD.requested_by_user_id) THEN
            RAISE EXCEPTION 'operation_authority_immutable' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.authority_kind='owner_inbox' THEN
        SELECT d.decision_channel INTO v_channel FROM app.candidate_recipe_review_decisions d
        WHERE d.tenant_id=NEW.tenant_id AND d.site_id=NEW.site_id AND d.id=NEW.decision_id
            AND d.candidate_revision_id=NEW.candidate_revision_id
            AND d.revision_sha256=NEW.revision_sha256 AND d.decision='approved';
        IF v_channel IS NULL OR (NEW.decision_channel IS NOT NULL
            AND NEW.decision_channel IS DISTINCT FROM v_channel) THEN
            RAISE EXCEPTION 'exact_owner_decision_required' USING ERRCODE='42501';
        END IF;
        NEW.decision_channel := v_channel;
    ELSIF NEW.decision_channel IS NOT NULL THEN
        RAISE EXCEPTION 'standing_authority_has_no_decision_channel' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION control.bind_github_pr_decision_channel() FROM PUBLIC;
CREATE TRIGGER github_pr_authority_immutable BEFORE INSERT OR UPDATE
ON app.github_pr_operations FOR EACH ROW EXECUTE FUNCTION control.bind_github_pr_decision_channel();

CREATE FUNCTION control.resolve_weekly_delivery_authority(
    p_handle bytea,p_site_id uuid,p_generation text,p_mode text
) RETURNS TABLE (outcome text,tenant_id uuid,user_id uuid,role_key text,
    authentication_level text,membership_epoch bigint,site_authorization_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w app.weekly_delivery_workloads%%ROWTYPE; g app.standing_authorizations%%ROWTYPE;
        v_week date; v_cycle_status text; v_owner record; v_tenant uuid;
BEGIN
    IF session_user != 'signal_workflow' OR p_mode NOT IN ('prepare','read') THEN
        RETURN QUERY SELECT 'authorization_denied'::text,NULL::uuid,NULL::uuid,
            NULL::text,NULL::text,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    SELECT directory.tenant_id INTO v_tenant FROM control.weekly_delivery_directory directory
        WHERE directory.handle_hash=p_handle AND directory.site_id=p_site_id AND directory.recovery_generation=p_generation;
    PERFORM set_config('signal.tenant_id',coalesce(v_tenant::text,''),true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT job.* INTO w FROM app.weekly_delivery_workloads job
    WHERE job.handle_hash=p_handle AND job.site_id=p_site_id;
    IF NOT FOUND OR w.recovery_generation IS DISTINCT FROM p_generation THEN
        RETURN QUERY SELECT 'authorization_denied'::text,NULL::uuid,NULL::uuid,
            NULL::text,NULL::text,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    PERFORM set_config('signal.tenant_id',w.tenant_id::text,true);
    PERFORM set_config('signal.site_id',w.site_id::text,true);
    SELECT grant_row.* INTO g FROM app.standing_authorizations grant_row
    WHERE grant_row.tenant_id=w.tenant_id AND grant_row.site_id=w.site_id AND grant_row.id=w.grant_id;
    SELECT cycle_row.week_start,cycle_row.status INTO v_week,v_cycle_status FROM app.weekly_cycles cycle_row
    WHERE cycle_row.tenant_id=w.tenant_id AND cycle_row.site_id=w.site_id AND cycle_row.id=w.cycle_id;
    SELECT d.decided_by_user_id AS user_id,m.authorization_epoch AS membership_epoch,
        sm.authorization_epoch AS site_epoch INTO v_owner
    FROM app.candidate_recipe_review_decisions d
    JOIN app.candidate_recipe_revisions r ON r.tenant_id=d.tenant_id AND r.site_id=d.site_id
        AND r.id=d.candidate_revision_id AND r.revision_sha256=d.revision_sha256
    JOIN app.memberships m ON m.tenant_id=d.tenant_id AND m.user_id=d.decided_by_user_id
    JOIN app.site_memberships sm ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id
        AND sm.site_id=d.site_id
    WHERE d.tenant_id=w.tenant_id AND d.site_id=w.site_id AND d.candidate_revision_id=w.revision_id
        AND d.decision='approved' AND d.recovery_generation=p_generation
        AND m.role_key='owner' AND m.state='active' AND sm.state='active'
        AND m.authorization_epoch=d.membership_epoch AND sm.authorization_epoch=d.site_authorization_epoch;
    IF NOT control.current_github_site_proof(w.tenant_id,w.site_id)
       OR NOT EXISTS (SELECT 1 FROM app.github_pr_extensions e
           JOIN app.github_read_bindings b ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id
             AND b.id=e.binding_id WHERE e.tenant_id=w.tenant_id AND e.site_id=w.site_id
             AND e.id=w.extension_id AND b.status='active' AND e.status='observed')
       OR (p_mode='prepare' AND (EXISTS (SELECT 1 FROM app.site_weekly_control
           WHERE app.site_weekly_control.tenant_id=w.tenant_id AND site_id=w.site_id AND paused)
           OR (v_owner.user_id IS NULL AND (
           control.weekly_cycle_authority(w.tenant_id,w.site_id,v_week,w.cycle_id,w.grant_id,p_generation) != 'active'
           OR NOT EXISTS (SELECT 1 FROM control.standing_grant_eligibility(
               w.tenant_id,w.site_id,w.grant_id,p_generation,w.recipe_release_id,'metadata_pr','/index.html') WHERE eligible))))) THEN
        RETURN QUERY SELECT 'authority_changed'::text,NULL::uuid,NULL::uuid,
            NULL::text,NULL::text,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    RETURN QUERY SELECT 'authorized'::text,w.tenant_id,
        CASE WHEN p_mode='prepare' THEN coalesce(v_owner.user_id,g.owner_user_id) ELSE g.owner_user_id END,
        'workload'::text,'workload'::text,
        CASE WHEN p_mode='prepare' THEN coalesce(v_owner.membership_epoch,g.membership_epoch) ELSE g.membership_epoch END,
        CASE WHEN p_mode='prepare' THEN coalesce(v_owner.site_epoch,g.site_epoch) ELSE g.site_epoch END;
END $$;
REVOKE ALL ON FUNCTION control.resolve_weekly_delivery_authority(bytea,uuid,text,text) FROM PUBLIC;

CREATE FUNCTION control.assert_weekly_delivery_scope(
    p_handle bytea,p_site_id uuid,p_generation text,p_arguments jsonb
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w app.weekly_delivery_workloads%%ROWTYPE; item record; v_tenant uuid;
BEGIN
    IF session_user != 'signal_workflow' THEN
        RAISE EXCEPTION 'workload_scope_denied' USING ERRCODE='42501'; END IF;
    SELECT tenant_id INTO v_tenant FROM control.weekly_delivery_directory
        WHERE handle_hash=p_handle AND site_id=p_site_id AND recovery_generation=p_generation;
    PERFORM set_config('signal.tenant_id',coalesce(v_tenant::text,''),true);
    PERFORM set_config('signal.site_id',p_site_id::text,true);
    SELECT * INTO w FROM app.weekly_delivery_workloads
    WHERE handle_hash=p_handle AND site_id=p_site_id AND recovery_generation=p_generation;
    IF NOT FOUND THEN RAISE EXCEPTION 'workload_scope_denied' USING ERRCODE='42501'; END IF;
    PERFORM set_config('signal.tenant_id',w.tenant_id::text,true);
    PERFORM set_config('signal.site_id',w.site_id::text,true);
    FOR item IN SELECT * FROM jsonb_each_text(p_arguments) LOOP
        IF item.value IS NULL OR (CASE item.key
            WHEN 'extension_id' THEN item.value IS DISTINCT FROM w.extension_id::text
            WHEN 'report_id' THEN item.value IS DISTINCT FROM w.report_id::text
            WHEN 'finding_id' THEN item.value IS DISTINCT FROM w.finding_id::text
            WHEN 'release_id' THEN item.value IS DISTINCT FROM w.recipe_release_id::text
            WHEN 'revision_id' THEN item.value IS DISTINCT FROM w.revision_id::text
            WHEN 'revision_key' THEN item.value IS DISTINCT FROM w.revision_key::text
            WHEN 'build_key' THEN item.value IS DISTINCT FROM w.build_key::text
            WHEN 'operation_id' THEN item.value IS DISTINCT FROM w.operation_id::text
            WHEN 'binding_id' THEN NOT EXISTS (SELECT 1 FROM app.github_pr_extensions
                WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id=w.extension_id
                  AND binding_id::text=item.value)
            WHEN 'build_id' THEN NOT EXISTS (SELECT 1 FROM app.candidate_build_intents
                WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id::text=item.value
                  AND extension_id=w.extension_id AND idempotency_key=w.build_key)
            WHEN 'attempt_id' THEN NOT EXISTS (SELECT 1 FROM app.github_delivery_attempts
                WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id::text=item.value
                  AND operation_id=w.operation_id)
            ELSE true END) THEN
            RAISE EXCEPTION 'workload_scope_denied' USING ERRCODE='42501';
        END IF;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION control.assert_weekly_delivery_scope(bytea,uuid,text,jsonb) FROM PUBLIC;

CREATE FUNCTION control.weekly_revision_a2_policy(p_tenant uuid,p_site uuid,p_revision uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT EXISTS (SELECT 1 FROM app.candidate_recipe_revisions v
    JOIN control.recipe_releases r ON r.id=v.recipe_release_id AND r.content_hash=v.release_content_hash
    WHERE v.tenant_id=p_tenant AND v.site_id=p_site AND v.id=p_revision
      AND control.recipe_autonomy_eligible(r.id)
      AND convert_from(v.canonical_manifest,'UTF8')::jsonb->>'source_path'='index.html'
      AND convert_from(v.canonical_manifest,'UTF8')::jsonb->>'claim_review_required'='false'
      AND convert_from(v.canonical_manifest,'UTF8')::jsonb #>> '{evidence,finding,key}' = ANY(
          CASE r.recipe_key WHEN 'technical_title' THEN ARRAY['metadata.title.missing','metadata.title.duplicate']
            WHEN 'technical_description' THEN ARRAY['metadata.meta_description.missing','metadata.meta_description.duplicate']
            WHEN 'technical_alt' THEN ARRAY['images.alt.missing']
            WHEN 'technical_structured_data' THEN ARRAY['structured_data.invalid_json_ld']
            WHEN 'technical_broken_link' THEN ARRAY['links.internal.not_found'] ELSE ARRAY[]::text[] END)
      AND lower((convert_from(v.canonical_manifest,'UTF8')::jsonb #>> '{patch,before}') || ' ' ||
                (convert_from(v.canonical_manifest,'UTF8')::jsonb #>> '{patch,after}'))
          !~ '(pricing|price|legal|medical|financial|product|robots|redirect|noindex|canonical|sitemap)');
$$;
REVOKE ALL ON FUNCTION control.weekly_revision_a2_policy(uuid,uuid,uuid) FROM PUBLIC;

CREATE FUNCTION control.list_weekly_delivery_inputs(
    p_tenant uuid,p_site uuid,p_cycle uuid,p_generation text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c app.weekly_cycles%%ROWTYPE; v_report app.crawl_audit_reports%%ROWTYPE;
        v_extension app.github_pr_extensions%%ROWTYPE; g app.standing_authorizations%%ROWTYPE;
BEGIN
    IF session_user != 'signal_workflow' THEN RETURN NULL; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO c FROM app.weekly_cycles WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_cycle;
    IF NOT FOUND OR control.weekly_cycle_authority(p_tenant,p_site,c.week_start,c.id,c.grant_id,p_generation) != 'active' THEN RETURN NULL; END IF;
    SELECT report.* INTO v_report FROM app.crawl_audit_reports report
    JOIN app.commands command ON command.tenant_id=report.tenant_id AND command.site_id=report.site_id
    WHERE report.tenant_id=p_tenant AND report.site_id=p_site
      AND command.id=c.observe_command_id AND command.status='succeeded'
      AND command.result_reference->>'manifest_id'=report.manifest_id::text
    ORDER BY report.analyzed_at DESC,report.id DESC LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('unavailable','CRAWL_EVIDENCE_UNAVAILABLE'); END IF;
    SELECT * INTO v_extension FROM app.github_pr_extensions WHERE tenant_id=p_tenant AND site_id=p_site
      AND status='observed' ORDER BY observed_at DESC,id DESC LIMIT 1;
    IF NOT FOUND OR v_extension.framework != 'eleventy' OR v_extension.content_format != 'html'
       OR v_extension.coverage != 'complete' THEN
        RETURN jsonb_build_object('unavailable','RECIPE_FORMAT_UNAVAILABLE'); END IF;
    SELECT * INTO g FROM app.standing_authorizations WHERE tenant_id=p_tenant AND site_id=p_site AND id=c.grant_id;
    RETURN jsonb_build_object('report_id',v_report.id,'findings',v_report.findings,
        'extension_id',v_extension.id,'release_ids',g.recipe_release_ids);
END $$;
REVOKE ALL ON FUNCTION control.list_weekly_delivery_inputs(uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.list_weekly_delivery_inputs(uuid,uuid,uuid,text) TO signal_workflow;

CREATE FUNCTION control.open_weekly_delivery_workload(
    p_tenant uuid,p_site uuid,p_cycle uuid,p_generation text,p_id uuid,
    p_report uuid,p_finding uuid,p_release uuid,p_extension uuid,p_revision uuid,
    p_operation uuid,p_gate uuid,p_revision_key uuid,p_build_key uuid,p_handle bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_inputs jsonb; v_key text; v_finding jsonb; c app.weekly_cycles%%ROWTYPE;
BEGIN
    v_inputs := control.list_weekly_delivery_inputs(p_tenant,p_site,p_cycle,p_generation);
    IF v_inputs IS NULL OR v_inputs ? 'unavailable' OR p_id IS NULL
       OR p_revision IS NULL OR p_operation IS NULL OR p_gate IS NULL
       OR p_revision_key IS NULL OR p_build_key IS NULL OR octet_length(p_handle) IS DISTINCT FROM 32
       OR v_inputs->>'report_id' IS DISTINCT FROM p_report::text
       OR v_inputs->>'extension_id' IS DISTINCT FROM p_extension::text
       OR NOT (v_inputs->'release_ids') ? p_release::text
       OR NOT control.recipe_autonomy_eligible(p_release) THEN RETURN 'workload_denied'; END IF;
    SELECT item INTO v_finding FROM jsonb_array_elements(v_inputs->'findings') item
    WHERE item->>'id'=p_finding::text;
    SELECT recipe_key INTO v_key FROM control.recipe_releases WHERE id=p_release;
    IF v_finding IS NULL OR v_finding->>'key' != ALL(CASE v_key
        WHEN 'technical_title' THEN ARRAY['metadata.title.missing','metadata.title.duplicate']
        WHEN 'technical_description' THEN ARRAY['metadata.meta_description.missing','metadata.meta_description.duplicate']
        WHEN 'technical_alt' THEN ARRAY['images.alt.missing']
        WHEN 'technical_structured_data' THEN ARRAY['structured_data.invalid_json_ld']
        WHEN 'technical_broken_link' THEN ARRAY['links.internal.not_found'] ELSE ARRAY[]::text[] END)
    THEN RETURN 'workload_denied'; END IF;
    SELECT * INTO c FROM app.weekly_cycles WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_cycle;
    IF NOT EXISTS (SELECT 1 FROM control.standing_grant_eligibility(p_tenant,p_site,c.grant_id,
        p_generation,p_release,'metadata_pr','/index.html') WHERE eligible) THEN RETURN 'workload_denied'; END IF;
    INSERT INTO app.weekly_delivery_workloads (tenant_id,site_id,id,cycle_id,grant_id,
        report_id,finding_id,recipe_release_id,extension_id,revision_id,operation_id,
        gate_decision_id,recipe_key,revision_key,build_key,handle_hash,recovery_generation)
    VALUES (p_tenant,p_site,p_id,p_cycle,c.grant_id,p_report,p_finding,p_release,p_extension,
        p_revision,p_operation,p_gate,v_key,p_revision_key,p_build_key,p_handle,p_generation)
    ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM app.weekly_delivery_workloads WHERE tenant_id=p_tenant
        AND site_id=p_site AND id=p_id AND cycle_id=p_cycle AND grant_id=c.grant_id
        AND report_id=p_report AND finding_id=p_finding AND recipe_release_id=p_release
        AND extension_id=p_extension AND revision_id=p_revision AND operation_id=p_operation
        AND gate_decision_id=p_gate AND revision_key=p_revision_key AND build_key=p_build_key
        AND handle_hash=p_handle AND recovery_generation=p_generation) THEN RETURN 'workload_conflict'; END IF;
    INSERT INTO control.weekly_delivery_directory(handle_hash,tenant_id,site_id,workload_id,recovery_generation)
    VALUES (p_handle,p_tenant,p_site,p_id,p_generation) ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM control.weekly_delivery_directory WHERE handle_hash=p_handle
        AND tenant_id=p_tenant AND site_id=p_site AND workload_id=p_id AND recovery_generation=p_generation)
    THEN RETURN 'workload_conflict'; END IF;
    RETURN 'opened';
END $$;
REVOKE ALL ON FUNCTION control.open_weekly_delivery_workload(
    uuid,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.open_weekly_delivery_workload(
    uuid,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,bytea) TO signal_workflow;

CREATE FUNCTION control.standing_dispatch_current(p_handle bytea,p_site uuid,p_generation text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w app.weekly_delivery_workloads%%ROWTYPE; d app.standing_dispatch_authorizations%%ROWTYPE;
        g app.standing_authorizations%%ROWTYPE; gate app.autonomy_gate_records%%ROWTYPE;
        reservation app.autonomy_reservations%%ROWTYPE; usage app.autonomy_weekly_usage%%ROWTYPE;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM control.resolve_weekly_delivery_authority(p_handle,p_site,p_generation,'prepare') WHERE outcome='authorized') THEN RETURN false; END IF;
    SELECT * INTO w FROM app.weekly_delivery_workloads WHERE handle_hash=p_handle AND site_id=p_site;
    IF NOT EXISTS (SELECT 1 FROM app.weekly_cycles c WHERE c.tenant_id=w.tenant_id
        AND c.site_id=w.site_id AND c.id=w.cycle_id AND control.weekly_cycle_authority(
        c.tenant_id,c.site_id,c.week_start,c.id,c.grant_id,p_generation)='active') THEN RETURN false; END IF;
    SELECT * INTO d FROM app.standing_dispatch_authorizations WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND workload_id=w.id;
    IF NOT FOUND OR NOT control.weekly_revision_a2_policy(w.tenant_id,w.site_id,w.revision_id) THEN RETURN false; END IF;
    SELECT * INTO g FROM app.standing_authorizations WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id=w.grant_id;
    SELECT * INTO gate FROM app.autonomy_gate_records WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id=w.gate_decision_id;
    SELECT * INTO reservation FROM app.autonomy_reservations WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND operation_id=w.operation_id;
    SELECT * INTO usage FROM app.autonomy_weekly_usage WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND week_start=reservation.week_start;
    RETURN d.grant_id=g.id AND d.grant_version_sha256=sha256(convert_to(to_jsonb(g)::text,'UTF8'))
       AND d.recovery_generation=p_generation AND d.recipe_release_id=w.recipe_release_id
       AND d.revision_id=w.revision_id AND d.gate_decision_id=gate.id
       AND d.reservation_operation_id=w.operation_id AND gate.operation_id=w.operation_id
       AND gate.outcome='ship' AND gate.policy_outcome='eligible' AND gate.provider='typesafe'
       AND NOT gate.fallback AND gate.work_type='metadata_pr' AND gate.resource_path='/index.html'
       AND gate.recipe_release_id=w.recipe_release_id AND gate.grant_id=g.id
       AND gate.recovery_generation=p_generation AND gate.reserved_operation_id=w.operation_id
       AND gate.sealed_revision_sha256=d.revision_sha256 AND gate.confidence>=gate.threshold
       AND gate.threshold=(g.thresholds->>'metadata_pr')::numeric AND d.threshold=gate.threshold
       AND d.confidence=gate.confidence AND reservation.grant_id=g.id
       AND reservation.recipe_release_id=w.recipe_release_id AND reservation.work_type='metadata_pr'
       AND reservation.resource_path='/index.html' AND reservation.sealed_revision_sha256=d.revision_sha256
       AND reservation.cost_cents=gate.cost_cents
       AND reservation.week_start=date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC')::date
       AND usage.total_count<=g.weekly_total_cap
       AND (usage.per_type_counts->>'metadata_pr')::integer<=(g.weekly_volume_caps->>'metadata_pr')::integer
       AND usage.spend_cents<=g.weekly_spend_cents
       AND NOT EXISTS (SELECT 1 FROM app.candidate_recipe_review_decisions owner_decision
           WHERE owner_decision.tenant_id=w.tenant_id AND owner_decision.site_id=w.site_id
             AND owner_decision.candidate_revision_id=w.revision_id);
END $$;

CREATE FUNCTION control.requeue_weekly_delivery_revision(
    p_tenant uuid,p_site uuid,p_cycle uuid,p_generation text,p_revision_hash bytea,
    p_id uuid,p_operation uuid,p_gate uuid,p_handle bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source app.weekly_delivery_workloads%%ROWTYPE; c app.weekly_cycles%%ROWTYPE;
BEGIN
    IF session_user!='signal_workflow' OR p_id IS NULL OR p_operation IS NULL OR p_gate IS NULL
       OR octet_length(p_handle) IS DISTINCT FROM 32 THEN RETURN 'denied'; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    SELECT * INTO c FROM app.weekly_cycles x WHERE x.tenant_id=p_tenant AND x.site_id=p_site AND x.id=p_cycle;
    IF NOT FOUND OR control.weekly_cycle_authority(p_tenant,p_site,c.week_start,c.id,c.grant_id,p_generation)!='active'
       OR NOT EXISTS (SELECT 1 FROM control.due_weekly_revisions(p_tenant,p_site,c.week_start,c.id) d
            WHERE d=p_revision_hash) THEN RETURN 'deferral_unavailable'; END IF;
    SELECT w.* INTO source FROM app.weekly_delivery_workloads w
    JOIN app.candidate_recipe_revisions r ON r.tenant_id=w.tenant_id AND r.site_id=w.site_id AND r.id=w.revision_id
    WHERE w.tenant_id=p_tenant AND w.site_id=p_site AND r.revision_sha256=p_revision_hash
    ORDER BY w.created_at DESC,w.id DESC LIMIT 1;
    IF NOT FOUND OR source.recovery_generation!=p_generation
       OR NOT control.weekly_revision_a2_policy(p_tenant,p_site,source.revision_id)
       OR NOT EXISTS (SELECT 1 FROM control.standing_grant_eligibility(p_tenant,p_site,c.grant_id,
            p_generation,source.recipe_release_id,'metadata_pr','/index.html') WHERE eligible)
       OR EXISTS (SELECT 1 FROM app.github_pr_operations o WHERE o.tenant_id=p_tenant
            AND o.site_id=p_site AND o.candidate_revision_id=source.revision_id)
       OR EXISTS (SELECT 1 FROM app.autonomy_reservations r JOIN app.weekly_delivery_workloads w
            ON w.tenant_id=r.tenant_id AND w.site_id=r.site_id AND w.operation_id=r.operation_id
            WHERE w.tenant_id=p_tenant AND w.site_id=p_site AND w.revision_id=source.revision_id)
       OR EXISTS (SELECT 1 FROM app.candidate_recipe_review_decisions d WHERE d.tenant_id=p_tenant
            AND d.site_id=p_site AND d.candidate_revision_id=source.revision_id)
    THEN RETURN 'exact_revision_requires_owner'; END IF;
    IF source.cycle_id=p_cycle THEN RETURN 'requeued'; END IF;
    INSERT INTO app.weekly_delivery_workloads(tenant_id,site_id,id,cycle_id,grant_id,report_id,
        finding_id,recipe_release_id,extension_id,revision_id,operation_id,gate_decision_id,
        recipe_key,revision_key,build_key,handle_hash,recovery_generation)
    VALUES(p_tenant,p_site,p_id,p_cycle,c.grant_id,source.report_id,source.finding_id,
        source.recipe_release_id,source.extension_id,source.revision_id,p_operation,p_gate,
        source.recipe_key,source.revision_key,source.build_key,p_handle,p_generation)
    ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM app.weekly_delivery_workloads w WHERE w.tenant_id=p_tenant
        AND w.site_id=p_site AND w.id=p_id AND w.revision_id=source.revision_id
        AND w.operation_id=p_operation AND w.gate_decision_id=p_gate AND w.handle_hash=p_handle)
    THEN RETURN 'workload_conflict'; END IF;
    INSERT INTO control.weekly_delivery_directory VALUES(p_handle,p_tenant,p_site,p_id,p_generation)
    ON CONFLICT DO NOTHING;
    RETURN 'requeued';
END $$;
REVOKE ALL ON FUNCTION control.requeue_weekly_delivery_revision(uuid,uuid,uuid,text,bytea,uuid,uuid,uuid,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.requeue_weekly_delivery_revision(uuid,uuid,uuid,text,bytea,uuid,uuid,uuid,bytea) TO signal_workflow;
REVOKE ALL ON FUNCTION control.standing_dispatch_current(bytea,uuid,text) FROM PUBLIC;

CREATE FUNCTION control.authorize_weekly_dispatch(p_handle bytea,p_site uuid,p_generation text,p_id uuid)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w app.weekly_delivery_workloads%%ROWTYPE; g app.standing_authorizations%%ROWTYPE;
        gate app.autonomy_gate_records%%ROWTYPE; v_revision_hash bytea; v_attestation uuid;
BEGIN
    IF session_user != 'signal_workflow' OR p_id IS NULL
       OR NOT EXISTS (SELECT 1 FROM control.resolve_weekly_delivery_authority(p_handle,p_site,p_generation,'prepare') WHERE outcome='authorized') THEN RETURN NULL; END IF;
    SELECT * INTO w FROM app.weekly_delivery_workloads WHERE handle_hash=p_handle AND site_id=p_site;
    IF NOT control.weekly_revision_a2_policy(w.tenant_id,w.site_id,w.revision_id) THEN RETURN NULL; END IF;
    SELECT * INTO g FROM app.standing_authorizations WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id=w.grant_id;
    SELECT * INTO gate FROM app.autonomy_gate_records WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id=w.gate_decision_id;
    SELECT revision_sha256 INTO v_revision_hash FROM app.candidate_recipe_revisions WHERE tenant_id=w.tenant_id AND site_id=w.site_id AND id=w.revision_id;
    SELECT id INTO v_attestation FROM control.recipe_autonomy_attestations WHERE release_id=w.recipe_release_id;
    IF gate.id IS NULL OR gate.outcome!='ship' OR gate.policy_outcome!='eligible'
       OR gate.provider!='typesafe' OR gate.fallback OR gate.work_type!='metadata_pr'
       OR gate.confidence<gate.threshold OR gate.threshold!=(g.thresholds->>'metadata_pr')::numeric
       OR gate.grant_id!=g.id OR gate.operation_id!=w.operation_id
       OR gate.sealed_revision_sha256!=v_revision_hash OR gate.recipe_release_id!=w.recipe_release_id
       OR gate.recovery_generation!=p_generation OR gate.resource_path!='/index.html'
       OR gate.reserved_operation_id!=w.operation_id THEN RETURN NULL; END IF;
    INSERT INTO app.standing_dispatch_authorizations (tenant_id,site_id,id,workload_id,grant_id,
        grant_version_sha256,recovery_generation,gate_decision_id,revision_id,revision_sha256,
        recipe_release_id,autonomy_attestation_id,reservation_operation_id,threshold,confidence,policy_version)
    VALUES (w.tenant_id,w.site_id,p_id,w.id,g.id,sha256(convert_to(to_jsonb(g)::text,'UTF8')),
        p_generation,gate.id,w.revision_id,v_revision_hash,w.recipe_release_id,v_attestation,
        w.operation_id,gate.threshold,gate.confidence,'technical-a2-1') ON CONFLICT DO NOTHING;
    IF control.standing_dispatch_current(p_handle,p_site,p_generation) AND EXISTS (
        SELECT 1 FROM app.standing_dispatch_authorizations WHERE tenant_id=w.tenant_id AND site_id=w.site_id
        AND id=p_id AND workload_id=w.id) THEN RETURN p_id; END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION control.authorize_weekly_dispatch(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.authorize_weekly_dispatch(bytea,uuid,text,uuid) TO signal_workflow;

CREATE FUNCTION control.weekly_github_pr_reconciliation_eligible(
    p_session_hash bytea,p_site_id uuid,p_generation text,p_revision_id uuid
) RETURNS TABLE (outcome text,tenant_id uuid,user_id uuid,membership_epoch bigint,site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,
        jsonb_build_object('revision_id',p_revision_id::text));
    SELECT * INTO a FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF a.outcome!='authorized' OR NOT EXISTS (SELECT 1 FROM app.github_pr_operations o
        JOIN app.weekly_delivery_workloads w ON w.tenant_id=o.tenant_id AND w.site_id=o.site_id AND w.operation_id=o.id
        WHERE w.handle_hash=p_session_hash AND o.candidate_revision_id=p_revision_id
          AND o.recovery_generation=p_generation AND o.state IN ('dispatching','outcome_unknown')
          AND o.journal_generation IS NOT NULL AND o.expected_tree_sha IS NOT NULL) THEN
        RETURN QUERY SELECT 'reconciliation_denied'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    RETURN QUERY SELECT 'eligible'::text,a.tenant_id,a.user_id,a.membership_epoch,a.site_authorization_epoch;
END $$;
REVOKE ALL ON FUNCTION control.weekly_github_pr_reconciliation_eligible(bytea,uuid,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.weekly_reconciliation_read_permit(
    p_handle bytea,p_site uuid,p_generation text,p_operation uuid,p_worker uuid,p_fence bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w app.weekly_delivery_workloads%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_handle,p_site,p_generation,
        jsonb_build_object('operation_id',p_operation::text));
    SELECT * INTO w FROM app.weekly_delivery_workloads WHERE handle_hash=p_handle AND site_id=p_site;
    IF NOT EXISTS (SELECT 1 FROM control.weekly_github_pr_reconciliation_eligible(
        p_handle,p_site,p_generation,w.revision_id) WHERE outcome='eligible') THEN RETURN false; END IF;
    RETURN EXISTS (SELECT 1 FROM app.github_pr_operations WHERE tenant_id=w.tenant_id
        AND site_id=p_site AND id=p_operation AND lease_holder=p_worker
        AND lease_fence=p_fence AND lease_until>transaction_timestamp());
END $$;
REVOKE ALL ON FUNCTION control.weekly_reconciliation_read_permit(bytea,uuid,text,uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_reconciliation_read_permit(bytea,uuid,text,uuid,uuid,bigint) TO signal_workflow;

CREATE TABLE app.weekly_pr_execution_receipts (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    canonical_receipt bytea NOT NULL CHECK (octet_length(canonical_receipt) BETWEEN 1 AND 196608),
    receipt_sha256 bytea GENERATED ALWAYS AS (sha256(canonical_receipt)) STORED,
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,operation_id),
    FOREIGN KEY (tenant_id,site_id,operation_id) REFERENCES app.github_pr_operations(tenant_id,site_id,id)
);
CREATE TRIGGER weekly_pr_execution_receipts_immutable BEFORE UPDATE OR DELETE
ON app.weekly_pr_execution_receipts FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.weekly_pr_execution_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.weekly_pr_execution_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY weekly_pr_execution_receipt_scope ON app.weekly_pr_execution_receipts
USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
REVOKE ALL ON app.weekly_pr_execution_receipts FROM PUBLIC,signal_api,signal_workflow,
    signal_identity,signal_bootstrap,signal_scheduler,signal_crawl_admission,signal_crawl_ingest;

CREATE FUNCTION control.record_weekly_pr_execution(p_handle bytea,p_site uuid,p_generation text,p_operation uuid,p_canonical bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; o app.github_pr_operations%%ROWTYPE; v_body jsonb; v_manifest jsonb;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_handle,p_site,p_generation,jsonb_build_object('operation_id',p_operation::text));
    SELECT * INTO a FROM control.resolve_weekly_delivery_authority(p_handle,p_site,p_generation,'prepare');
    IF a.outcome!='authorized' THEN RETURN 'authority_changed'; END IF;
    IF EXISTS (SELECT 1 FROM app.weekly_pr_execution_receipts WHERE tenant_id=a.tenant_id
        AND site_id=p_site AND operation_id=p_operation AND canonical_receipt=p_canonical) THEN
        RETURN 'recorded'; END IF;
    SELECT * INTO o FROM app.github_pr_operations WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_operation;
    SELECT convert_from(canonical_manifest,'UTF8')::jsonb INTO v_manifest FROM app.candidate_recipe_revisions
    WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=o.candidate_revision_id;
    v_body := convert_from(p_canonical,'UTF8')::jsonb;
    IF o.id IS NULL OR o.journal_generation IS NULL OR o.state!='planned'
       OR v_body->>'tree_sha' IS DISTINCT FROM o.expected_tree_sha
       OR v_body->>'commit_sha' IS DISTINCT FROM o.expected_commit_sha
       OR v_body->>'base_sha' IS DISTINCT FROM o.base_sha
       OR v_body->>'authority_kind' IS DISTINCT FROM o.authority_kind
       OR v_body->>'authority_id' IS DISTINCT FROM COALESCE(o.decision_id,o.standing_dispatch_id)::text
       OR v_body->>'decision_channel' IS DISTINCT FROM o.decision_channel
       OR v_body->>'revision_sha256' IS DISTINCT FROM encode(o.revision_sha256,'hex')
       OR v_body->>'path' IS DISTINCT FROM v_manifest->>'source_path'
       OR encode(sha256(convert_to(v_body->>'content','UTF8')),'hex') IS DISTINCT FROM v_manifest->>'result_sha256'
       OR COALESCE(length(v_body->>'pr_body'),0) NOT BETWEEN 1 AND 8192 THEN RETURN 'execution_receipt_invalid'; END IF;
    INSERT INTO app.weekly_pr_execution_receipts(tenant_id,site_id,operation_id,canonical_receipt)
    VALUES (a.tenant_id,p_site,p_operation,p_canonical) ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM app.weekly_pr_execution_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site
        AND operation_id=p_operation AND canonical_receipt=p_canonical) THEN RETURN 'execution_receipt_conflict'; END IF;
    RETURN 'recorded';
END $$;
REVOKE ALL ON FUNCTION control.record_weekly_pr_execution(bytea,uuid,text,uuid,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_weekly_pr_execution(bytea,uuid,text,uuid,bytea) TO signal_workflow;

CREATE FUNCTION control.read_weekly_pr_execution(p_handle bytea,p_site uuid,p_generation text)
RETURNS TABLE (canonical_receipt bytea,receipt_sha256 bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_weekly_delivery_authority(p_handle,p_site,p_generation,'read');
    IF a.outcome!='authorized' THEN RETURN; END IF;
    RETURN QUERY SELECT e.canonical_receipt,e.receipt_sha256 FROM app.weekly_pr_execution_receipts e
    JOIN app.weekly_delivery_workloads w ON w.tenant_id=e.tenant_id AND w.site_id=e.site_id AND w.operation_id=e.operation_id
    WHERE w.handle_hash=p_handle AND w.site_id=p_site;
END $$;
REVOKE ALL ON FUNCTION control.read_weekly_pr_execution(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_weekly_pr_execution(bytea,uuid,text) TO signal_workflow;

CREATE FUNCTION control.read_weekly_dispatch_authority(p_handle bytea,p_site uuid,p_generation text,
    p_revision uuid,p_revision_hash bytea,p_operation uuid)
RETURNS TABLE (authority_kind text,authority_id uuid,owner_user_id uuid,decision_channel text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; w app.weekly_delivery_workloads%%ROWTYPE; v_owner_decision uuid; v_channel text;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_handle,p_site,p_generation,
        jsonb_build_object('revision_id',p_revision::text,'operation_id',p_operation::text));
    SELECT * INTO a FROM control.resolve_weekly_delivery_authority(p_handle,p_site,p_generation,'prepare');
    IF a.outcome!='authorized' OR NOT EXISTS (SELECT 1 FROM app.candidate_recipe_revisions
        WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_revision AND revision_sha256=p_revision_hash) THEN RETURN; END IF;
    SELECT * INTO w FROM app.weekly_delivery_workloads WHERE handle_hash=p_handle;
    SELECT d.id,d.decision_channel INTO v_owner_decision,v_channel FROM app.candidate_recipe_review_decisions d
    WHERE d.tenant_id=w.tenant_id AND d.site_id=w.site_id AND d.candidate_revision_id=p_revision
      AND d.decision='approved' AND d.revision_sha256=p_revision_hash
      AND d.recovery_generation=p_generation AND d.membership_epoch=a.membership_epoch
      AND d.site_authorization_epoch=a.site_authorization_epoch;
    IF v_owner_decision IS NOT NULL THEN
        RETURN QUERY SELECT 'owner_inbox'::text,v_owner_decision,a.user_id,v_channel; RETURN;
    END IF;
    IF control.standing_dispatch_current(p_handle,p_site,p_generation) THEN
        RETURN QUERY SELECT 'standing_grant'::text,d.id,a.user_id,NULL::text
        FROM app.standing_dispatch_authorizations d WHERE d.tenant_id=w.tenant_id
          AND d.site_id=w.site_id AND d.workload_id=w.id;
    END IF;
END $$;
REVOKE ALL ON FUNCTION control.read_weekly_dispatch_authority(bytea,uuid,text,uuid,bytea,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_weekly_dispatch_authority(bytea,uuid,text,uuid,bytea,uuid) TO signal_workflow;

CREATE FUNCTION control.list_weekly_delivery_workloads(p_tenant uuid,p_site uuid,p_cycle uuid)
RETURNS SETOF jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user!='signal_workflow' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    RETURN QUERY SELECT (to_jsonb(w)-'handle_hash') || jsonb_build_object(
        'revision_sha256',encode(r.revision_sha256,'hex'),
        'manifest',convert_from(r.canonical_manifest,'UTF8')::jsonb)
    FROM app.weekly_delivery_workloads w LEFT JOIN app.candidate_recipe_revisions r
    ON r.tenant_id=w.tenant_id AND r.site_id=w.site_id AND r.id=w.revision_id
    WHERE w.tenant_id=p_tenant AND w.site_id=p_site AND w.cycle_id=p_cycle
    ORDER BY w.finding_id LIMIT 32;
END $$;
REVOKE ALL ON FUNCTION control.list_weekly_delivery_workloads(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.list_weekly_delivery_workloads(uuid,uuid,uuid) TO signal_workflow;

CREATE FUNCTION control.list_weekly_delivery_backlog(p_tenant uuid,p_site uuid)
RETURNS SETOF jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user!='signal_workflow' THEN RETURN; END IF;
    PERFORM set_config('signal.tenant_id',p_tenant::text,true);
    PERFORM set_config('signal.site_id',p_site::text,true);
    RETURN QUERY SELECT (to_jsonb(w)-'handle_hash') || jsonb_build_object(
        'revision_sha256',encode(r.revision_sha256,'hex'),
        'manifest',convert_from(r.canonical_manifest,'UTF8')::jsonb)
    FROM app.weekly_delivery_workloads w JOIN app.candidate_recipe_revisions r
        ON r.tenant_id=w.tenant_id AND r.site_id=w.site_id AND r.id=w.revision_id
    LEFT JOIN app.github_pr_operations o ON o.tenant_id=w.tenant_id AND o.site_id=w.site_id AND o.id=w.operation_id
    WHERE w.tenant_id=p_tenant AND w.site_id=p_site
        AND NOT EXISTS (SELECT 1 FROM app.weekly_delivery_workloads newer
            WHERE newer.tenant_id=w.tenant_id AND newer.site_id=w.site_id AND newer.revision_id=w.revision_id
              AND (newer.created_at,newer.id)>(w.created_at,w.id))
        AND (o.id IS NULL OR o.state IN ('planned','ready','dispatching','outcome_unknown','opened'))
        AND NOT EXISTS (SELECT 1 FROM app.github_delivery_receipts v WHERE v.tenant_id=w.tenant_id
            AND v.site_id=w.site_id AND v.operation_id=w.operation_id
            AND convert_from(v.canonical_receipt,'UTF8')::jsonb->>'outcome'='verified')
    ORDER BY w.created_at,w.id LIMIT 32;
END $$;
REVOKE ALL ON FUNCTION control.list_weekly_delivery_backlog(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.list_weekly_delivery_backlog(uuid,uuid) TO signal_workflow;

CREATE FUNCTION control.read_github_pr_operation_authorities(p_session bytea,p_site uuid,p_generation text)
RETURNS TABLE(operation_id uuid,authority_kind text,authority_id uuid,owner_user_id uuid,decision_channel text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome!='authorized' THEN RETURN; END IF;
    RETURN QUERY SELECT o.id,o.authority_kind,coalesce(o.decision_id,o.standing_dispatch_id),
        coalesce(d.decided_by_user_id,g.owner_user_id),o.decision_channel
    FROM app.github_pr_operations o LEFT JOIN app.candidate_recipe_review_decisions d
        ON d.tenant_id=o.tenant_id AND d.site_id=o.site_id AND d.id=o.decision_id
    LEFT JOIN app.standing_dispatch_authorizations s ON s.tenant_id=o.tenant_id
        AND s.site_id=o.site_id AND s.id=o.standing_dispatch_id
    LEFT JOIN app.standing_authorizations g ON g.tenant_id=s.tenant_id AND g.site_id=s.site_id AND g.id=s.grant_id
    WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site ORDER BY o.created_at DESC,o.id DESC LIMIT 50;
END $$;
REVOKE ALL ON FUNCTION control.read_github_pr_operation_authorities(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_github_pr_operation_authorities(bytea,uuid,text) TO signal_identity;

CREATE FUNCTION control.weekly_read_github_pr_operation_authorities(p_handle bytea,p_site uuid,p_generation text)
RETURNS TABLE(operation_id uuid,authority_kind text,authority_id uuid,owner_user_id uuid,decision_channel text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_weekly_delivery_authority(p_handle,p_site,p_generation,'read');
    IF a.outcome!='authorized' THEN RETURN; END IF;
    RETURN QUERY SELECT o.id,o.authority_kind,coalesce(o.decision_id,o.standing_dispatch_id),
        coalesce(d.decided_by_user_id,g.owner_user_id),o.decision_channel
    FROM app.github_pr_operations o JOIN app.weekly_delivery_workloads w
        ON w.tenant_id=o.tenant_id AND w.site_id=o.site_id AND w.operation_id=o.id
    LEFT JOIN app.candidate_recipe_review_decisions d
        ON d.tenant_id=o.tenant_id AND d.site_id=o.site_id AND d.id=o.decision_id
    LEFT JOIN app.standing_dispatch_authorizations s ON s.tenant_id=o.tenant_id
        AND s.site_id=o.site_id AND s.id=o.standing_dispatch_id
    LEFT JOIN app.standing_authorizations g ON g.tenant_id=s.tenant_id AND g.site_id=s.site_id AND g.id=s.grant_id
    WHERE w.handle_hash=p_handle AND w.site_id=p_site;
END $$;
REVOKE ALL ON FUNCTION control.weekly_read_github_pr_operation_authorities(bytea,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_github_pr_operation_authorities(bytea,uuid,text) TO signal_workflow;

CREATE OR REPLACE FUNCTION control.grant_standing_authorization(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_grant_id uuid,
    p_release_ids uuid[], p_work_types text[], p_thresholds jsonb,
    p_volume_caps jsonb, p_total_cap integer, p_spend_cents bigint,
    p_excluded_paths text[], p_starts_at timestamptz, p_ends_at timestamptz,
    p_recovery_window_hours integer
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_type text; v_path text; v_release uuid;
BEGIN
    IF session_user != 'signal_api' OR p_grant_id IS NULL OR p_site_id IS NULL
       OR cardinality(p_release_ids) NOT BETWEEN 1 AND 64
       OR cardinality(p_work_types) NOT BETWEEN 1 AND 16
       OR cardinality(p_excluded_paths) > 64
       OR p_total_cap NOT BETWEEN 1 AND 1000
       OR p_spend_cents NOT BETWEEN 0 AND 100000000
       OR p_recovery_window_hours NOT BETWEEN 1 AND 720
       OR p_starts_at IS NULL OR p_ends_at IS NULL
       OR p_starts_at < transaction_timestamp() - interval '5 minutes'
       OR p_ends_at <= p_starts_at OR p_ends_at > p_starts_at + interval '366 days'
       OR jsonb_typeof(p_thresholds) != 'object'
       OR jsonb_typeof(p_volume_caps) != 'object'
       OR (SELECT count(DISTINCT id) FROM unnest(p_release_ids) id) != cardinality(p_release_ids)
       OR (SELECT count(DISTINCT kind) FROM unnest(p_work_types) kind) != cardinality(p_work_types)
    THEN RETURN 'invalid_grant'; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_thresholds)) != cardinality(p_work_types)
       OR (SELECT count(*) FROM jsonb_object_keys(p_volume_caps)) != cardinality(p_work_types)
    THEN RETURN 'invalid_grant'; END IF;
    FOREACH v_type IN ARRAY p_work_types LOOP
        IF NOT control.valid_standing_work_type(v_type)
           OR jsonb_typeof(p_thresholds -> v_type) != 'number'
           OR (p_thresholds ->> v_type)::numeric NOT BETWEEN 0 AND 1
           OR jsonb_typeof(p_volume_caps -> v_type) != 'number'
           OR (p_volume_caps ->> v_type) !~ '^[1-9][0-9]{0,3}$'
           OR (p_volume_caps ->> v_type)::integer > p_total_cap
        THEN RETURN 'invalid_grant'; END IF;
    END LOOP;
    FOREACH v_path IN ARRAY p_excluded_paths LOOP
        IF v_path IS NULL OR length(v_path) NOT BETWEEN 1 AND 1024
           OR v_path !~ '^/[A-Za-z0-9_./-]*$' OR v_path LIKE '%%..%%'
           OR v_path LIKE '%%//%%' OR position(chr(92) in v_path) > 0
        THEN RETURN 'invalid_grant'; END IF;
    END LOOP;
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome != 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key != 'owner' THEN RETURN 'permission_denied'; END IF;
    PERFORM 1 FROM app.sites WHERE tenant_id = v_authority.tenant_id
        AND id = p_site_id AND state = 'active' AND ownership_status = 'verified' FOR UPDATE;
    IF NOT FOUND THEN RETURN 'site_unavailable'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        v_authority.tenant_id::text || ':' || p_site_id::text, 43));
    IF EXISTS (SELECT 1 FROM app.standing_authorizations g WHERE
        g.tenant_id = v_authority.tenant_id AND g.site_id = p_site_id
        AND g.ends_at > transaction_timestamp()
        AND g.recovery_generation = p_generation
        AND NOT EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
            WHERE r.tenant_id = g.tenant_id AND r.site_id = g.site_id AND r.grant_id = g.id)
        AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
            WHERE t.target_kind = 'standing_grant' AND t.target_id = g.id))
    THEN RETURN 'grant_exists'; END IF;
    FOREACH v_release IN ARRAY p_release_ids LOOP
        IF NOT EXISTS (
            SELECT 1 FROM control.recipe_releases r
            JOIN LATERAL (SELECT e.status FROM control.recipe_release_events e
                WHERE e.release_id = r.id ORDER BY e.sequence_number DESC LIMIT 1) current ON true
            WHERE r.id = v_release AND current.status = 'REVIEWED'
              AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind = 'recipe_release' AND t.target_id = r.id)
        ) THEN
            RETURN 'recipe_unavailable';
        END IF;
    END LOOP;
    FOREACH v_type IN ARRAY p_work_types LOOP
        IF NOT EXISTS (
            SELECT 1 FROM control.recipe_releases r
            WHERE r.id = ANY(p_release_ids)
              AND control.standing_release_matches_work(r.id,v_type)
        ) THEN RETURN 'recipe_unavailable'; END IF;
    END LOOP;
    INSERT INTO app.standing_authorizations (
        tenant_id, site_id, id, owner_user_id, membership_epoch, site_epoch,
        recovery_generation, recipe_release_ids, work_types, thresholds,
        weekly_volume_caps, weekly_total_cap, weekly_spend_cents, excluded_paths,
        starts_at, ends_at, recovery_window_hours)
    VALUES (v_authority.tenant_id, p_site_id, p_grant_id, v_authority.user_id,
        v_authority.membership_epoch, v_authority.site_authorization_epoch,
        p_generation, p_release_ids, p_work_types, p_thresholds, p_volume_caps,
        p_total_cap, p_spend_cents, p_excluded_paths, p_starts_at, p_ends_at,
        p_recovery_window_hours);
    RETURN 'granted';
END $$;

CREATE OR REPLACE FUNCTION control.standing_grant_eligibility_unpaused(
    p_tenant_id uuid, p_site_id uuid, p_grant_id uuid, p_generation text,
    p_recipe_release_id uuid, p_work_type text, p_resource_path text
) RETURNS TABLE (eligible boolean, reason text, threshold numeric)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE g app.standing_authorizations%%ROWTYPE;
BEGIN
    IF session_user NOT IN ('signal_api','signal_workflow') OR p_tenant_id IS NULL
       OR p_site_id IS NULL OR p_grant_id IS NULL OR p_recipe_release_id IS NULL
       OR p_generation IS NULL OR p_work_type IS NULL
       OR NOT control.valid_standing_work_type(p_work_type)
       OR p_resource_path IS NULL
       OR length(p_resource_path) NOT BETWEEN 1 AND 1024
       OR p_resource_path !~ '^/[A-Za-z0-9_./-]*$'
       OR p_resource_path LIKE '%%..%%'
       OR p_resource_path LIKE '%%//%%' OR position(chr(92) in p_resource_path) > 0 THEN
        RETURN QUERY SELECT false, 'invalid_scope'::text, NULL::numeric; RETURN;
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    SELECT * INTO g FROM app.standing_authorizations a
    WHERE a.tenant_id = p_tenant_id AND a.site_id = p_site_id AND a.id = p_grant_id
    FOR SHARE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'grant_unavailable'::text, NULL::numeric; RETURN;
    END IF;
    IF g.recovery_generation != p_generation
       OR transaction_timestamp() NOT BETWEEN g.starts_at AND g.ends_at
       OR NOT p_work_type = ANY(g.work_types)
       OR NOT p_recipe_release_id = ANY(g.recipe_release_ids)
       OR EXISTS (SELECT 1 FROM app.standing_authorization_revocations r
                  WHERE r.tenant_id = p_tenant_id AND r.site_id = p_site_id
                    AND r.grant_id = p_grant_id)
       OR EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind = 'standing_grant' AND t.target_id = p_grant_id)
       OR EXISTS (SELECT 1 FROM unnest(g.excluded_paths) x
                  WHERE p_resource_path = x OR p_resource_path LIKE rtrim(x, '/') || '/%%')
       OR NOT EXISTS (SELECT 1 FROM app.memberships m JOIN app.site_memberships sm
                      ON sm.tenant_id = m.tenant_id AND sm.user_id = m.user_id
                      JOIN app.tenants t ON t.tenant_id = m.tenant_id
                      JOIN app.sites s ON s.tenant_id = sm.tenant_id AND s.id = sm.site_id
                      WHERE m.tenant_id = p_tenant_id AND m.user_id = g.owner_user_id
                        AND m.role_key = 'owner' AND m.state = 'active'
                        AND m.authorization_epoch = g.membership_epoch
                        AND sm.site_id = p_site_id AND sm.state = 'active'
                        AND sm.authorization_epoch = g.site_epoch
                        AND t.lifecycle = 'active' AND s.state = 'active'
                        AND s.ownership_status = 'verified')
       OR NOT EXISTS (
            SELECT 1 FROM control.recipe_releases r
            JOIN LATERAL (SELECT e.status FROM control.recipe_release_events e
                WHERE e.release_id = r.id ORDER BY e.sequence_number DESC LIMIT 1) current ON true
            WHERE r.id = p_recipe_release_id AND current.status = 'REVIEWED'
              AND NOT EXISTS (SELECT 1 FROM control.authority_denial_tombstones t
                  WHERE t.target_kind = 'recipe_release' AND t.target_id = r.id)
              AND control.standing_release_matches_work(r.id,p_work_type))
    THEN RETURN QUERY SELECT false, 'authority_unavailable'::text, NULL::numeric; RETURN;
    END IF;
    RETURN QUERY SELECT true, 'eligible'::text,
        (g.thresholds ->> p_work_type)::numeric;
END $$;
