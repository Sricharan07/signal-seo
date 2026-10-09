ALTER TABLE app.content_candidates ADD UNIQUE (tenant_id,site_id,id,revision_sha256);
CREATE TABLE app.content_delivery_decisions (
    tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL,
    candidate_id uuid NOT NULL, revision_sha256 bytea NOT NULL,
    owner_user_id uuid NOT NULL, recovery_generation text NOT NULL,
    membership_epoch bigint NOT NULL, site_epoch bigint NOT NULL,
    decision_channel text NOT NULL CHECK (decision_channel='dashboard'),
    acknowledged_sentences jsonb NOT NULL CHECK (jsonb_typeof(acknowledged_sentences)='array'),
    authenticated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id,site_id,id), UNIQUE (tenant_id,site_id,candidate_id),
    FOREIGN KEY (tenant_id,site_id,candidate_id,revision_sha256)
        REFERENCES app.content_candidates(tenant_id,site_id,id,revision_sha256),
    FOREIGN KEY (tenant_id,owner_user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TRIGGER content_delivery_decisions_immutable BEFORE UPDATE OR DELETE
ON app.content_delivery_decisions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.content_delivery_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.content_delivery_decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY content_delivery_decision_scope ON app.content_delivery_decisions
USING (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
WITH CHECK (tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
REVOKE ALL ON app.content_delivery_decisions FROM PUBLIC,signal_api,signal_identity,
signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest;

-- Both candidate kinds retain exact composite foreign keys; no synthetic recipe is minted.
DO $$ DECLARE c record; BEGIN
    FOR c IN SELECT conname FROM pg_constraint
      WHERE conrelid='app.github_pr_operations'::regclass AND
       ((contype='f' AND confrelid='app.candidate_recipe_revisions'::regclass)
        OR (contype='c' AND pg_get_constraintdef(oid) LIKE '%%authority_kind%%')) LOOP
        EXECUTE format('ALTER TABLE app.github_pr_operations DROP CONSTRAINT %%I',c.conname);
    END LOOP;
END $$;
ALTER TABLE app.github_pr_operations
    ALTER COLUMN recipe_release_id DROP NOT NULL,
    ADD COLUMN editorial_decision_id uuid,
    ADD COLUMN technical_revision_id uuid GENERATED ALWAYS AS
        (CASE WHEN authority_kind<>'owner_editorial' THEN candidate_revision_id END) STORED,
    ADD COLUMN content_candidate_id uuid GENERATED ALWAYS AS
        (CASE WHEN authority_kind='owner_editorial' THEN candidate_revision_id END) STORED,
    ADD FOREIGN KEY (tenant_id,site_id,technical_revision_id,revision_sha256)
        REFERENCES app.candidate_recipe_revisions(tenant_id,site_id,id,revision_sha256),
    ADD FOREIGN KEY (tenant_id,site_id,content_candidate_id,revision_sha256)
        REFERENCES app.content_candidates(tenant_id,site_id,id,revision_sha256),
    ADD FOREIGN KEY (tenant_id,site_id,editorial_decision_id)
        REFERENCES app.content_delivery_decisions(tenant_id,site_id,id),
    ADD CHECK (
      (authority_kind='owner_inbox' AND decision_id IS NOT NULL AND standing_dispatch_id IS NULL
        AND editorial_decision_id IS NULL AND recipe_release_id IS NOT NULL AND decision_channel IS NOT NULL)
      OR (authority_kind='standing_grant' AND decision_id IS NULL AND standing_dispatch_id IS NOT NULL
        AND editorial_decision_id IS NULL AND recipe_release_id IS NOT NULL AND decision_channel IS NULL)
      OR (authority_kind='owner_editorial' AND decision_id IS NULL AND standing_dispatch_id IS NULL
        AND editorial_decision_id IS NOT NULL AND recipe_release_id IS NULL AND decision_channel='dashboard'));

CREATE OR REPLACE FUNCTION control.bind_github_pr_decision_channel() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_channel text;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF (NEW.authority_kind,NEW.decision_id,NEW.standing_dispatch_id,NEW.editorial_decision_id,
            NEW.decision_channel,NEW.requested_by_user_id,NEW.candidate_revision_id,
            NEW.revision_sha256,NEW.recovery_generation,NEW.membership_epoch,NEW.site_epoch,
            NEW.extension_id,NEW.binding_id,NEW.recipe_release_id,NEW.base_sha,NEW.intent_sha256,
            NEW.tenant_id,NEW.site_id,NEW.id,NEW.branch_name,NEW.created_at) IS DISTINCT FROM
           (OLD.authority_kind,OLD.decision_id,OLD.standing_dispatch_id,OLD.editorial_decision_id,
            OLD.decision_channel,OLD.requested_by_user_id,OLD.candidate_revision_id,
            OLD.revision_sha256,OLD.recovery_generation,OLD.membership_epoch,OLD.site_epoch,
            OLD.extension_id,OLD.binding_id,OLD.recipe_release_id,OLD.base_sha,OLD.intent_sha256,
            OLD.tenant_id,OLD.site_id,OLD.id,OLD.branch_name,OLD.created_at) THEN
            RAISE EXCEPTION 'operation_authority_immutable' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.authority_kind='owner_editorial' THEN
        IF NOT EXISTS (SELECT 1 FROM app.content_delivery_decisions d
          WHERE d.tenant_id=NEW.tenant_id AND d.site_id=NEW.site_id AND d.id=NEW.editorial_decision_id
            AND d.candidate_id=NEW.candidate_revision_id AND d.revision_sha256=NEW.revision_sha256
            AND d.owner_user_id=NEW.requested_by_user_id AND d.recovery_generation=NEW.recovery_generation
            AND d.membership_epoch=NEW.membership_epoch AND d.site_epoch=NEW.site_epoch
            AND d.decision_channel=NEW.decision_channel) THEN
            RAISE EXCEPTION 'exact_editorial_decision_required' USING ERRCODE='42501';
        END IF;
    ELSIF NEW.authority_kind='owner_inbox' THEN
        SELECT d.decision_channel INTO v_channel FROM app.candidate_recipe_review_decisions d
        WHERE d.tenant_id=NEW.tenant_id AND d.site_id=NEW.site_id AND d.id=NEW.decision_id
            AND d.candidate_revision_id=NEW.candidate_revision_id
            AND d.revision_sha256=NEW.revision_sha256 AND d.decision='approved';
        IF v_channel IS NULL OR (NEW.decision_channel IS NOT NULL AND NEW.decision_channel<>v_channel) THEN
            RAISE EXCEPTION 'exact_owner_decision_required' USING ERRCODE='42501';
        END IF;
        NEW.decision_channel:=v_channel;
    ELSIF NEW.decision_channel IS NOT NULL THEN
        RAISE EXCEPTION 'standing_authority_has_no_decision_channel' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION control.content_candidate_current(p_hash bytea,p_site uuid,p_generation text,p_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; c app.content_candidates%%ROWTYPE; d app.content_draft_intents%%ROWTYPE;
        snapshot jsonb; current_fact record;
BEGIN
    SELECT * INTO a FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF a.tenant_id IS NULL OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN false; END IF;
    SELECT * INTO c FROM app.content_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id;
    IF NOT FOUND OR c.revision_sha256<>sha256(c.canonical) OR c.manifest<>convert_from(c.canonical,'UTF8')::jsonb
      OR c.manifest->>'approval_class' IS DISTINCT FROM 'A2'
      OR c.manifest->>'autonomy_eligible' IS DISTINCT FROM 'false'
      OR coalesce(c.manifest->>'work_type','') NOT IN ('new_article','content_refresh')
      OR jsonb_array_length(c.manifest->'changed_files') IS DISTINCT FROM 1
      OR c.manifest->>'draft_id' IS DISTINCT FROM c.draft_id::text
      OR c.manifest->>'extension_id' IS DISTINCT FROM c.extension_id::text
      OR c.manifest->>'build_id' IS DISTINCT FROM c.build_id::text
      OR c.manifest->>'site_id' IS DISTINCT FROM p_site::text
      OR c.manifest->'originality'->>'state' IS DISTINCT FROM 'original' THEN RETURN false; END IF;
    SELECT * INTO d FROM app.content_draft_intents WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=c.draft_id;
    IF NOT FOUND OR jsonb_typeof(d.fact_snapshot)<>'array' OR jsonb_array_length(d.fact_snapshot)=0
      OR NOT EXISTS(SELECT 1 FROM app.content_brief_acceptances b WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.brief_id=d.brief_id)
      OR EXISTS(SELECT 1 FROM app.content_briefs b WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.supersedes_id=d.brief_id)
      OR NOT EXISTS(SELECT 1 FROM app.content_draft_results r WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site
        AND r.draft_id=d.id AND r.payload->>'state' IN ('grounded','owner_required')
        AND r.payload->'originality'=c.manifest->'originality') THEN RETURN false; END IF;
    IF coalesce(c.manifest->'grounding'->>'state','') NOT IN ('grounded','owner_required')
      OR EXISTS(SELECT 1 FROM app.content_draft_results r,
        jsonb_array_elements(r.payload->'grounding'->'sentences') original
        WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND r.draft_id=d.id
          AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c.manifest->'grounding'->'sentences') sealed
            WHERE sealed->>'path'=original->>'path' AND sealed->>'sentence'=original->>'sentence'
              AND (sealed->'reasons') @> (original->'reasons'))) THEN RETURN false; END IF;
    FOR snapshot IN SELECT value FROM jsonb_array_elements(d.fact_snapshot) LOOP
        SELECT * INTO current_fact FROM control.approved_business_brain_facts(p_hash,p_generation,p_site)
        WHERE fact_id::text=snapshot->>'fact_id';
        IF NOT FOUND OR current_fact.category IS DISTINCT FROM snapshot->>'category'
           OR current_fact.statement IS DISTINCT FROM snapshot->>'statement' THEN RETURN false; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM app.content_briefs b, jsonb_array_elements_text(b.payload->'fact_ids') f
      WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.id=d.brief_id
        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(d.fact_snapshot) s WHERE s->>'fact_id'=f)) THEN RETURN false; END IF;
    RETURN EXISTS(SELECT 1 FROM app.candidate_build_intents b
      JOIN app.candidate_build_receipts r ON r.tenant_id=b.tenant_id AND r.site_id=b.site_id AND r.build_id=b.id
      JOIN app.github_pr_extensions e ON e.tenant_id=b.tenant_id AND e.site_id=b.site_id AND e.id=b.extension_id
      JOIN app.github_read_bindings binding ON binding.tenant_id=e.tenant_id AND binding.site_id=e.site_id AND binding.id=e.binding_id
      WHERE b.tenant_id=a.tenant_id AND b.site_id=p_site AND b.id=c.build_id AND b.extension_id=c.extension_id
        AND b.base_sha=c.manifest->>'base_sha' AND b.patch_sha256=c.manifest->>'patch_sha256'
        AND b.status='completed' AND r.exit_class='passed' AND b.recovery_generation=p_generation
        AND r.base_sha=b.base_sha AND r.patch_sha256=b.patch_sha256
        AND c.manifest->'build_receipt'->>'id'=b.id::text
        AND c.manifest->'build_receipt'->>'site_id'=p_site::text
        AND c.manifest->'build_receipt'->>'status'=b.status
        AND c.manifest->'build_receipt'->>'base_sha'=r.base_sha
        AND c.manifest->'build_receipt'->>'patch_sha256'=r.patch_sha256
        AND c.manifest->'build_receipt'->>'toolchain'=r.toolchain
        AND c.manifest->'build_receipt'->>'command'=r.build_command
        AND c.manifest->'build_receipt'->>'exit_class'=r.exit_class
        AND c.manifest->'build_receipt'->>'exit_code'=r.exit_code::text
        AND c.manifest->'build_receipt'->>'log_bytes'=r.log_bytes::text
        AND r.logs_sha256=c.manifest->'build_receipt'->>'logs_sha256'
        AND r.artifacts=(SELECT jsonb_agg(jsonb_build_object('path',v->>0,'sha256',v->>1,'size',(v->>2)::integer))
          FROM jsonb_array_elements(c.manifest->'build_receipt'->'artifacts') v)
        AND e.status='observed' AND e.coverage='complete' AND e.framework='eleventy' AND e.content_format='html'
        AND e.base_sha=b.base_sha AND binding.base_sha=b.base_sha AND binding.status='active'
        AND binding.protected AND e.repository_id=binding.repository_id);
END $$;
REVOKE ALL ON FUNCTION control.content_candidate_current(bytea,uuid,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.content_writer_approve_delivery(p_hash bytea,p_generation text,p_site uuid,
    p_id uuid,p_digest bytea,p_acknowledgements jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; auth record; c app.content_candidates%%ROWTYPE; d app.content_delivery_decisions%%ROWTYPE;
        flags jsonb; auth_at timestamptz;
BEGIN
    SELECT * INTO a FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF a.tenant_id IS NULL THEN RETURN 'denied'; END IF;
    SELECT * INTO auth FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    SELECT s.auth_time INTO auth_at FROM app.sessions s WHERE s.session_token_hash=p_hash;
    IF auth.authentication_level<>'mfa' OR auth_at IS NULL OR auth_at<=transaction_timestamp()-interval '5 minutes'
      OR auth_at>transaction_timestamp() THEN RETURN 'step_up_required'; END IF;
    SELECT * INTO c FROM app.content_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=p_id;
    IF NOT FOUND OR c.revision_sha256 IS DISTINCT FROM p_digest THEN RETURN 'conflict'; END IF;
    IF NOT control.content_candidate_current(p_hash,p_site,p_generation,p_id) THEN RETURN 'candidate_stale'; END IF;
    SELECT coalesce(jsonb_agg(s->>'path' ORDER BY s->>'path'),'[]'::jsonb) INTO flags
      FROM jsonb_array_elements(c.manifest->'grounding'->'sentences') s WHERE jsonb_array_length(s->'reasons')>0;
    IF p_acknowledgements IS NULL OR jsonb_typeof(p_acknowledgements)<>'array'
      OR jsonb_array_length(p_acknowledgements)>120 THEN RETURN 'acknowledgements_required'; END IF;
    IF flags IS DISTINCT FROM (SELECT coalesce(jsonb_agg(x ORDER BY x),'[]'::jsonb)
       FROM jsonb_array_elements_text(p_acknowledgements) x)
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_acknowledgements) x WHERE jsonb_typeof(x)<>'string')
      THEN RETURN 'acknowledgements_required'; END IF;
    IF EXISTS(SELECT 1 FROM app.content_candidate_reviews r WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site
      AND r.candidate_id=p_id AND r.decision<>'approved') THEN RETURN 'conflict'; END IF;
    SELECT * INTO d FROM app.content_delivery_decisions WHERE tenant_id=a.tenant_id AND site_id=p_site AND candidate_id=p_id;
    IF FOUND THEN RETURN CASE WHEN d.owner_user_id=a.user_id AND d.revision_sha256=p_digest
      AND d.recovery_generation=p_generation AND d.acknowledged_sentences=flags THEN 'replayed' ELSE 'conflict' END; END IF;
    INSERT INTO app.content_delivery_decisions VALUES(a.tenant_id,p_site,gen_random_uuid(),p_id,p_digest,
      a.user_id,p_generation,auth.membership_epoch,auth.site_authorization_epoch,'dashboard',flags,auth_at,transaction_timestamp());
    INSERT INTO app.content_writer_audits VALUES(a.tenant_id,p_site,gen_random_uuid(),'delivery_approved',p_id,a.user_id,transaction_timestamp());
    RETURN 'approved';
END $$;
REVOKE ALL ON FUNCTION control.content_writer_approve_delivery(bytea,text,uuid,uuid,bytea,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.content_writer_approve_delivery(bytea,text,uuid,uuid,bytea,jsonb) TO signal_api;

ALTER FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) RENAME TO technical_github_pr_operation_eligible;
CREATE FUNCTION control.github_pr_operation_eligible(p_hash bytea,p_site uuid,p_generation text,p_id uuid)
RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,membership_epoch bigint,site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; d app.content_delivery_decisions%%ROWTYPE; cap integer; used integer;
BEGIN
    SELECT * INTO a FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF a.tenant_id IS NULL OR NOT EXISTS(SELECT 1 FROM app.content_candidates c WHERE c.tenant_id=a.tenant_id AND c.site_id=p_site AND c.id=p_id) THEN
        RETURN QUERY SELECT * FROM control.technical_github_pr_operation_eligible(p_hash,p_site,p_generation,p_id); RETURN;
    END IF;
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    SELECT * INTO d FROM app.content_delivery_decisions WHERE app.content_delivery_decisions.tenant_id=a.tenant_id
      AND site_id=p_site AND candidate_id=p_id AND recovery_generation=p_generation;
    IF NOT FOUND OR d.owner_user_id<>a.user_id OR d.membership_epoch<>a.membership_epoch
      OR d.site_epoch<>a.site_authorization_epoch OR NOT control.content_candidate_current(p_hash,p_site,p_generation,p_id)
      OR EXISTS(SELECT 1 FROM app.content_candidate_reviews review WHERE review.tenant_id=a.tenant_id
        AND review.site_id=p_site AND review.candidate_id=p_id AND review.decision<>'approved')
      OR EXISTS(SELECT 1 FROM app.github_pr_operations old WHERE old.tenant_id=a.tenant_id
        AND old.site_id=p_site AND old.candidate_revision_id=p_id AND old.authority_kind='owner_editorial'
        AND old.state<>'opened' AND old.created_at<=transaction_timestamp()-interval '7 days')
      OR EXISTS(SELECT 1 FROM app.site_weekly_control w WHERE w.tenant_id=a.tenant_id AND w.site_id=p_site AND w.paused) THEN
        RETURN QUERY SELECT 'editorial_authority_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    SELECT c.cap INTO cap FROM app.content_writer_caps c WHERE c.tenant_id=a.tenant_id AND c.site_id=p_site ORDER BY c.created_at DESC,c.id DESC LIMIT 1;
    SELECT count(*) INTO used FROM app.github_pr_operations o WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site
      AND o.authority_kind='owner_editorial'
      AND CASE WHEN o.state='opened' THEN o.updated_at ELSE o.created_at END
        >transaction_timestamp()-interval '7 days'
      AND o.candidate_revision_id<>p_id;
    IF used>=coalesce(cap,2) THEN
        RETURN QUERY SELECT 'article_cap_exhausted'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;
    RETURN QUERY SELECT 'eligible'::text,a.tenant_id,a.user_id,a.membership_epoch,a.site_authorization_epoch;
END $$;
REVOKE ALL ON FUNCTION control.github_pr_operation_eligible(bytea,uuid,text,uuid) FROM PUBLIC;

CREATE FUNCTION control.read_content_delivery_candidate(p_hash bytea,p_site uuid,p_generation text,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
    IF a.outcome<>'authorized' OR a.role_key<>'owner' THEN RETURN NULL; END IF;
    RETURN (SELECT jsonb_build_object('manifest',c.manifest,'revision_sha256',encode(c.revision_sha256,'hex'),
      'authority_id',d.id,'owner_user_id',d.owner_user_id,'site_origin',s.primary_origin)
      FROM app.content_candidates c JOIN app.content_delivery_decisions d ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id
        AND d.candidate_id=c.id AND d.revision_sha256=c.revision_sha256 AND d.recovery_generation=p_generation
      JOIN app.sites s ON s.tenant_id=c.tenant_id AND s.id=c.site_id
      WHERE c.tenant_id=a.tenant_id AND c.site_id=p_site AND c.id=p_id);
END $$;
REVOKE ALL ON FUNCTION control.read_content_delivery_candidate(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_content_delivery_candidate(bytea,uuid,text,uuid) TO signal_identity;

ALTER FUNCTION control.content_writer_read(bytea,text,uuid) RENAME TO content_writer_record_read;
REVOKE ALL ON FUNCTION control.content_writer_record_read(bytea,text,uuid) FROM signal_api;
CREATE FUNCTION control.content_writer_read(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; result jsonb;
BEGIN
    SELECT * INTO a FROM control.business_brain_owner(p_hash,p_generation,p_site);
    IF a.tenant_id IS NULL THEN RETURN NULL; END IF;
    result:=control.content_writer_record_read(p_hash,p_generation,p_site);
    RETURN jsonb_set(result,'{candidates}',coalesce((SELECT jsonb_agg(c||jsonb_build_object(
      'delivery_approval_id',(SELECT d.id FROM app.content_delivery_decisions d WHERE d.tenant_id=a.tenant_id
        AND d.site_id=p_site AND d.candidate_id=(c->>'candidate_id')::uuid AND d.recovery_generation=p_generation)))
      FROM jsonb_array_elements(result->'candidates') c),'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION control.content_writer_read(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.content_writer_read(bytea,text,uuid) TO signal_api;

ALTER FUNCTION control.prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea)
RENAME TO prepare_technical_github_pr_operation;
REVOKE ALL ON FUNCTION control.prepare_technical_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) FROM signal_identity;
CREATE FUNCTION control.prepare_github_pr_operation(p_hash bytea,p_site uuid,p_generation text,
 p_revision uuid,p_digest bytea,p_operation uuid,p_intent bytea)
RETURNS TABLE(operation_id uuid,operation_state text,operation_step text,canonical_manifest bytea,operation_created_at timestamptz,outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; c app.content_candidates%%ROWTYPE; d app.content_delivery_decisions%%ROWTYPE;
        o app.github_pr_operations%%ROWTYPE; e app.github_pr_extensions%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.business_brain_owner(p_hash,p_generation,p_site);
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
END $$;
REVOKE ALL ON FUNCTION control.prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_github_pr_operation(bytea,uuid,text,uuid,bytea,uuid,bytea) TO signal_identity;

CREATE OR REPLACE FUNCTION control.read_github_pr_operation_authorities(p_session bytea,p_site uuid,p_generation text)
RETURNS TABLE(operation_id uuid,authority_kind text,authority_id uuid,owner_user_id uuid,decision_channel text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session,p_site,p_generation);
    IF a.outcome<>'authorized' THEN RETURN; END IF;
    RETURN QUERY SELECT o.id,o.authority_kind,coalesce(o.decision_id,o.standing_dispatch_id,o.editorial_decision_id),
      o.requested_by_user_id,o.decision_channel FROM app.github_pr_operations o
      WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site ORDER BY o.created_at DESC,o.id DESC LIMIT 50;
END $$;
