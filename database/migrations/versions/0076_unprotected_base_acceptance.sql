ALTER TABLE app.github_read_bindings
    ADD COLUMN risk_generation bigint NOT NULL DEFAULT 1 CHECK (risk_generation > 0),
    ADD COLUMN risk_monitored boolean NOT NULL DEFAULT false,
    ADD COLUMN permissions_sha256 bytea CHECK (octet_length(permissions_sha256) = 32);

CREATE TABLE app.github_unprotected_base_acceptances (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    binding_id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    authentication_time timestamptz NOT NULL,
    repository_id bigint NOT NULL CHECK (repository_id > 0),
    full_name text NOT NULL,
    installation_id bigint NOT NULL CHECK (installation_id > 0),
    default_branch text NOT NULL,
    protection_state text NOT NULL CHECK (protection_state IN ('none', 'unavailable_on_plan')),
    permissions_sha256 bytea NOT NULL CHECK (octet_length(permissions_sha256) = 32),
    recovery_generation text NOT NULL,
    risk_generation bigint NOT NULL,
    membership_epoch bigint NOT NULL,
    site_epoch bigint NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, binding_id, risk_generation),
    FOREIGN KEY (tenant_id, site_id, binding_id)
        REFERENCES app.github_read_bindings (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, owner_user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK (authentication_time BETWEEN accepted_at - interval '5 minutes' AND accepted_at)
);
CREATE TABLE app.github_base_risk_invalidations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    binding_id uuid NOT NULL,
    risk_generation bigint NOT NULL,
    reason text NOT NULL CHECK (reason IN ('identity_changed', 'permissions_changed', 'provider_unavailable', 'protection_available')),
    recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, binding_id)
        REFERENCES app.github_read_bindings (tenant_id, site_id, id)
);
CREATE TRIGGER github_unprotected_base_acceptances_immutable BEFORE UPDATE OR DELETE
ON app.github_unprotected_base_acceptances FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER github_base_risk_invalidations_immutable BEFORE UPDATE OR DELETE
ON app.github_base_risk_invalidations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.github_unprotected_base_acceptances ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_unprotected_base_acceptances FORCE ROW LEVEL SECURITY;
CREATE POLICY github_unprotected_base_acceptance_scope ON app.github_unprotected_base_acceptances
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
ALTER TABLE app.github_base_risk_invalidations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.github_base_risk_invalidations FORCE ROW LEVEL SECURITY;
CREATE POLICY github_base_risk_invalidation_scope ON app.github_base_risk_invalidations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
REVOKE ALL ON app.github_unprotected_base_acceptances, app.github_base_risk_invalidations
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler,
     signal_workflow, signal_crawl_admission, signal_crawl_ingest;

CREATE FUNCTION control.github_unprotected_base_accepted(p_binding app.github_read_bindings, p_generation text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
SELECT p_binding.recovery_generation = p_generation AND p_binding.status = 'active' AND p_binding.protected IS FALSE
  AND p_binding.base_branch = p_binding.default_branch
  AND EXISTS (SELECT 1 FROM app.github_unprotected_base_acceptances a
    JOIN app.memberships m ON m.tenant_id=a.tenant_id AND m.user_id=a.owner_user_id
    JOIN app.site_memberships sm ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id AND sm.site_id=a.site_id
    WHERE a.tenant_id=p_binding.tenant_id AND a.site_id=p_binding.site_id AND a.binding_id=p_binding.id
      AND a.risk_generation=p_binding.risk_generation AND a.recovery_generation=p_binding.recovery_generation
      AND a.repository_id=p_binding.repository_id AND a.full_name=p_binding.observed_full_name
      AND a.default_branch=p_binding.default_branch AND a.installation_id=p_binding.installation_id
      AND a.permissions_sha256=p_binding.permissions_sha256
      AND m.state='active' AND m.role_key='owner' AND m.authorization_epoch=a.membership_epoch
      AND sm.state='active' AND sm.authorization_epoch=a.site_epoch);
$$;
REVOKE ALL ON FUNCTION control.github_unprotected_base_accepted(app.github_read_bindings,text) FROM PUBLIC;

CREATE FUNCTION control.read_github_base_risk(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN NULL; END IF;
    SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site_id AND id=p_binding_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('protected',b.protected,'default_branch',b.default_branch,
        'risk_monitored',b.risk_monitored,'owner_accepted',
        b.recovery_generation=p_generation AND control.github_unprotected_base_accepted(b,p_generation));
END $$;
REVOKE ALL ON FUNCTION control.read_github_base_risk(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_github_base_risk(bytea,uuid,text,uuid) TO signal_identity;

CREATE FUNCTION control.record_github_binding_state(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid,
    p_repository bigint, p_full_name text, p_installation bigint, p_default_branch text,
    p_base_branch text, p_protected boolean, p_permissions bytea
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE; v_reason text;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN false; END IF;
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
END $$;
REVOKE ALL ON FUNCTION control.record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) TO signal_identity;

CREATE FUNCTION control.accept_github_unprotected_base(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid,
    p_acceptance_id uuid, p_repository bigint, p_full_name text, p_default_branch text,
    p_base_sha text, p_private boolean, p_protected boolean, p_permissions bytea
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE; v_auth_time timestamptz;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
    IF a.role_key IS DISTINCT FROM 'owner' OR a.authentication_level IS DISTINCT FROM 'mfa'
    THEN RETURN 'permission_denied'; END IF;
    SELECT s.auth_time INTO v_auth_time FROM app.sessions s WHERE s.session_token_hash=p_session_hash;
    IF v_auth_time IS NULL OR v_auth_time NOT BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp()
    THEN RETURN 'step_up_required'; END IF;
    SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site_id AND id=p_binding_id FOR UPDATE;
    IF NOT FOUND OR b.status NOT IN ('prepared','failed','active') OR b.requested_by_user_id<>a.user_id
       OR b.membership_epoch<>a.membership_epoch OR b.site_epoch<>a.site_authorization_epoch
       OR b.recovery_generation<>p_generation OR NOT control.current_github_site_proof(a.tenant_id,p_site_id)
    THEN RETURN 'binding_not_authorized'; END IF;
    IF p_acceptance_id IS NULL OR p_repository IS NULL OR p_repository<=0 OR p_full_name IS NULL
       OR lower(p_full_name)<>lower(b.repository_owner || '/' || b.repository_name)
       OR p_default_branch IS DISTINCT FROM b.base_branch OR p_private IS NULL OR p_protected IS DISTINCT FROM false
       OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
       OR p_permissions IS NULL OR octet_length(p_permissions)<>32
       OR (b.repository_id IS NOT NULL AND (b.repository_id<>p_repository OR b.default_branch<>p_default_branch))
    THEN RETURN 'invalid_observation'; END IF;
    IF control.github_unprotected_base_accepted(b,p_generation) THEN RETURN 'active'; END IF;
    IF EXISTS (SELECT 1 FROM app.github_read_bindings other WHERE other.tenant_id=b.tenant_id
        AND other.site_id=b.site_id AND other.id<>b.id AND other.status IN ('prepared','active'))
    THEN RETURN 'binding_exists'; END IF;
    INSERT INTO app.github_unprotected_base_acceptances (
        tenant_id,site_id,id,binding_id,owner_user_id,authentication_time,repository_id,full_name,
        installation_id,default_branch,protection_state,permissions_sha256,recovery_generation,
        risk_generation,membership_epoch,site_epoch
    ) VALUES (b.tenant_id,b.site_id,p_acceptance_id,b.id,a.user_id,v_auth_time,p_repository,p_full_name,
        b.installation_id,p_default_branch,'none',p_permissions,p_generation,b.risk_generation,
        a.membership_epoch,a.site_authorization_epoch);
    UPDATE app.github_read_bindings SET status='active',repository_id=p_repository,observed_full_name=p_full_name,
        default_branch=p_default_branch,base_sha=p_base_sha,private=p_private,protected=false,
        failure_code=NULL,observed_at=transaction_timestamp(),risk_monitored=true,permissions_sha256=p_permissions
    WHERE tenant_id=b.tenant_id AND site_id=b.site_id AND id=b.id;
    INSERT INTO app.github_read_binding_events (tenant_id,site_id,id,binding_id,actor_user_id,event_kind)
    VALUES (b.tenant_id,b.site_id,gen_random_uuid(),b.id,a.user_id,'activated');
    RETURN 'active';
END $$;
REVOKE ALL ON FUNCTION control.accept_github_unprotected_base(bytea,uuid,text,uuid,uuid,bigint,text,text,text,boolean,boolean,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.accept_github_unprotected_base(bytea,uuid,text,uuid,uuid,bigint,text,text,text,boolean,boolean,bytea) TO signal_identity;

-- Existing authority, release, receipt, and fencing checks remain in force.
CREATE OR REPLACE FUNCTION control.finish_github_pr_extension(
    p_session_hash bytea, p_site_id uuid, p_generation text,
    p_extension_id uuid, p_event_id uuid, p_outcome text,
    p_repository_id bigint, p_base_sha text, p_tree_sha text,
    p_framework text, p_content_format text, p_coverage text,
    p_content_sha text, p_marker_evidence jsonb, p_protected boolean
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_extension app.github_pr_extensions%%ROWTYPE;
        v_binding app.github_read_bindings%%ROWTYPE; v_item jsonb;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN RETURN v_authority.outcome; END IF;
    IF v_authority.role_key <> 'owner' THEN RETURN 'permission_denied'; END IF;
    SELECT * INTO v_extension FROM app.github_pr_extensions AS extension
      WHERE extension.tenant_id = v_authority.tenant_id AND extension.site_id = p_site_id
        AND extension.id = p_extension_id FOR UPDATE;
    IF NOT FOUND OR v_extension.requested_by_user_id <> v_authority.user_id
       OR v_extension.membership_epoch <> v_authority.membership_epoch
       OR v_extension.site_epoch <> v_authority.site_authorization_epoch
       OR v_extension.recovery_generation <> p_generation THEN
        RETURN 'extension_not_authorized';
    END IF;
    SELECT * INTO v_binding FROM app.github_read_bindings AS binding
      WHERE binding.tenant_id = v_authority.tenant_id AND binding.site_id = p_site_id
        AND binding.id = v_extension.binding_id AND binding.status = 'active' FOR SHARE;
    IF NOT FOUND THEN RETURN 'binding_inactive'; END IF;
    IF NOT control.current_github_site_proof(v_authority.tenant_id, p_site_id) THEN
        RETURN 'site_not_verified';
    END IF;
    IF v_extension.status <> 'prepared' THEN RETURN 'extension_not_prepared'; END IF;
    IF p_outcome = 'observed' THEN
        IF p_repository_id IS DISTINCT FROM v_binding.repository_id
           OR p_base_sha IS NULL OR p_base_sha !~ '^[0-9a-f]{40}$'
           OR p_tree_sha IS NULL OR p_tree_sha !~ '^[0-9a-f]{40}$'
           OR NOT (p_protected IS TRUE OR (p_protected IS FALSE AND control.github_unprotected_base_accepted(v_binding,p_generation)))
           OR p_framework IS NULL OR p_content_format IS NULL OR p_coverage IS NULL
           OR p_framework NOT IN ('nextjs', 'astro', 'hugo', 'eleventy', 'unknown', 'ambiguous')
           OR p_content_format NOT IN (
               'tsx', 'jsx', 'markdown', 'mdx', 'astro', 'html', 'liquid', 'nunjucks', 'unknown'
           ) OR p_coverage NOT IN ('complete', 'partial')
           OR (p_coverage = 'complete' AND (p_content_sha IS NULL
               OR p_content_sha !~ '^[0-9a-f]{40}$'))
           OR (p_content_format <> 'unknown' AND p_content_sha IS NULL)
           OR (p_content_sha IS NOT NULL AND p_content_sha !~ '^[0-9a-f]{40}$')
           OR p_marker_evidence IS NULL OR jsonb_typeof(p_marker_evidence) <> 'array'
           OR jsonb_array_length(p_marker_evidence) > 16
           OR (p_framework IN ('nextjs', 'astro', 'hugo', 'eleventy')
               AND jsonb_array_length(p_marker_evidence) < 1)
           OR (p_framework = 'ambiguous' AND jsonb_array_length(p_marker_evidence) < 2)
           OR octet_length(p_marker_evidence::text) > 8192 THEN
            RETURN 'invalid_observation';
        END IF;
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_marker_evidence) LOOP
            IF jsonb_typeof(v_item) <> 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 2
               OR NOT (v_item ?& ARRAY['path', 'sha'])
               OR jsonb_typeof(v_item->'path') <> 'string'
               OR jsonb_typeof(v_item->'sha') <> 'string'
               OR length(v_item->>'path') NOT BETWEEN 1 AND 1024
               OR (v_item->>'sha') !~ '^[0-9a-f]{40}$' THEN
                RETURN 'invalid_observation';
            END IF;
        END LOOP;
        UPDATE app.github_pr_extensions SET status = 'observed',
            repository_id = p_repository_id, base_sha = p_base_sha, tree_sha = p_tree_sha,
            framework = p_framework, content_format = p_content_format, coverage = p_coverage,
            content_sha = p_content_sha, marker_evidence = p_marker_evidence,
            observed_at = transaction_timestamp()
         WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_extension_id;
        INSERT INTO app.github_pr_extension_events
            (tenant_id, site_id, id, extension_id, actor_user_id, event_kind)
        VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_extension_id,
                v_authority.user_id, 'observed');
        RETURN 'observed';
    END IF;
    IF p_outcome IS NULL OR p_outcome NOT IN (
        'GITHUB_AUTHORIZATION_REJECTED', 'GITHUB_REPOSITORY_STATE_REJECTED',
        'GITHUB_SCOPE_REJECTED', 'GITHUB_PROVIDER_RESPONSE_REJECTED',
        'GITHUB_PROVIDER_UNAVAILABLE', 'GITHUB_CREDENTIALS_REJECTED',
        'GITHUB_CREDENTIAL_UNAVAILABLE', 'GITHUB_BINDING_CHANGED')
       OR p_repository_id IS NOT NULL OR p_base_sha IS NOT NULL OR p_tree_sha IS NOT NULL
       OR p_framework IS NOT NULL OR p_content_format IS NOT NULL OR p_coverage IS NOT NULL
       OR p_content_sha IS NOT NULL OR p_marker_evidence IS NOT NULL OR p_protected IS NOT NULL
    THEN RETURN 'invalid_observation'; END IF;
    UPDATE app.github_pr_extensions SET status = 'failed', failure_code = p_outcome
     WHERE tenant_id = v_authority.tenant_id AND site_id = p_site_id AND id = p_extension_id;
    INSERT INTO app.github_pr_extension_events
        (tenant_id, site_id, id, extension_id, actor_user_id, event_kind, detail_code)
    VALUES (v_authority.tenant_id, p_site_id, p_event_id, p_extension_id,
            v_authority.user_id, 'failed', p_outcome);
    RETURN 'failed';
END; $$;

CREATE FUNCTION control.weekly_read_github_base_risk(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('binding_id',p_binding_id::text));
    SELECT * INTO a FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN NULL; END IF;
    SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site_id AND id=p_binding_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('protected',b.protected,'default_branch',b.default_branch,
        'risk_monitored',b.risk_monitored,'owner_accepted',
        b.recovery_generation=p_generation AND control.github_unprotected_base_accepted(b,p_generation));
END $$;
REVOKE ALL ON FUNCTION control.weekly_read_github_base_risk(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_read_github_base_risk(bytea,uuid,text,uuid) TO signal_workflow;

CREATE FUNCTION control.weekly_record_github_binding_state(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_binding_id uuid,
    p_repository bigint, p_full_name text, p_installation bigint, p_default_branch text,
    p_base_branch text, p_protected boolean, p_permissions bytea
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE; v_reason text;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('binding_id',p_binding_id::text));
    SELECT * INTO a FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'read');
    IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'workload' THEN RETURN false; END IF;
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
END $$;
REVOKE ALL ON FUNCTION control.weekly_record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_record_github_binding_state(bytea,uuid,text,uuid,bigint,text,bigint,text,text,boolean,bytea) TO signal_workflow;

CREATE FUNCTION control.github_base_acceptance_preflight(p_session_hash bytea,p_site_id uuid,p_generation text,p_binding_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; b app.github_read_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO a FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
    IF a.role_key IS DISTINCT FROM 'owner' OR a.authentication_level IS DISTINCT FROM 'mfa' THEN RETURN 'permission_denied'; END IF;
    IF NOT EXISTS (SELECT 1 FROM app.sessions s WHERE s.session_token_hash=p_session_hash
        AND s.auth_time BETWEEN transaction_timestamp()-interval '5 minutes' AND transaction_timestamp())
    THEN RETURN 'step_up_required'; END IF;
    SELECT * INTO b FROM app.github_read_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site_id AND id=p_binding_id FOR SHARE;
    IF NOT FOUND OR b.status NOT IN ('prepared','failed','active') OR b.requested_by_user_id<>a.user_id
       OR b.membership_epoch<>a.membership_epoch OR b.site_epoch<>a.site_authorization_epoch
       OR b.recovery_generation<>p_generation OR NOT control.current_github_site_proof(a.tenant_id,p_site_id)
    THEN RETURN 'binding_not_authorized'; END IF;
    RETURN 'authorized';
END $$;
REVOKE ALL ON FUNCTION control.github_base_acceptance_preflight(bytea,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.github_base_acceptance_preflight(bytea,uuid,text,uuid) TO signal_identity;

-- Keep the article and IndexNow wrappers; amend their underlying technical port.
CREATE OR REPLACE FUNCTION control.github_pr_eligible_before_indexnow(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid
) RETURNS TABLE (outcome text, tenant_id uuid, user_id uuid,
    membership_epoch bigint, site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'owner' OR NOT control.current_github_site_proof(
        v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT 'authority_denied'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT revision.* INTO v_revision FROM app.candidate_recipe_revisions revision
     WHERE revision.tenant_id = v_authority.tenant_id
       AND revision.site_id = p_site_id AND revision.id = p_revision_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'revision_unavailable'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT decision.* INTO v_decision FROM app.candidate_recipe_review_decisions decision
     WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
       AND decision.candidate_revision_id = p_revision_id;
    IF NOT FOUND OR v_decision.decision <> 'approved'
       OR v_decision.revision_sha256 <> v_revision.revision_sha256
       OR v_decision.recovery_generation <> p_generation
       OR v_decision.membership_epoch <> v_authority.membership_epoch
       OR v_decision.site_authorization_epoch <> v_authority.site_authorization_epoch THEN
        RETURN QUERY SELECT 'decision_stale'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
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
END; $$;

CREATE OR REPLACE FUNCTION control.weekly_github_pr_operation_eligible(
    p_session_hash bytea, p_site_id uuid, p_generation text, p_revision_id uuid
) RETURNS TABLE (outcome text, tenant_id uuid, user_id uuid,
    membership_epoch bigint, site_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record; v_revision app.candidate_recipe_revisions%%ROWTYPE;
        v_decision app.candidate_recipe_review_decisions%%ROWTYPE;
BEGIN
    PERFORM control.assert_weekly_delivery_scope(p_session_hash,p_site_id,p_generation,jsonb_build_object('revision_id',p_revision_id::text));
    SELECT * INTO v_authority FROM control.resolve_weekly_delivery_authority(p_session_hash,p_site_id,p_generation,'prepare');
    IF v_authority.outcome <> 'authorized' THEN
        RETURN QUERY SELECT v_authority.outcome, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    IF v_authority.role_key <> 'workload' OR NOT control.current_github_site_proof(
        v_authority.tenant_id, p_site_id) THEN
        RETURN QUERY SELECT 'authority_denied'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT revision.* INTO v_revision FROM app.candidate_recipe_revisions revision
     WHERE revision.tenant_id = v_authority.tenant_id
       AND revision.site_id = p_site_id AND revision.id = p_revision_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'revision_unavailable'::text, NULL::uuid, NULL::uuid, NULL::bigint, NULL::bigint;
        RETURN;
    END IF;
    SELECT decision.* INTO v_decision FROM app.candidate_recipe_review_decisions decision
     WHERE decision.tenant_id = v_authority.tenant_id AND decision.site_id = p_site_id
       AND decision.candidate_revision_id = p_revision_id;
    IF (v_decision.id IS NULL OR v_decision.decision <> 'approved'
       OR v_decision.revision_sha256 <> v_revision.revision_sha256
       OR v_decision.recovery_generation <> p_generation
       OR v_decision.membership_epoch <> v_authority.membership_epoch
       OR v_decision.site_authorization_epoch <> v_authority.site_authorization_epoch)
       AND NOT control.standing_dispatch_current(p_session_hash,p_site_id,p_generation) THEN
        RETURN QUERY SELECT 'decision_stale'::text,NULL::uuid,NULL::uuid,NULL::bigint,NULL::bigint;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM app.github_pr_operations operation
        WHERE operation.tenant_id=v_revision.tenant_id AND operation.site_id=p_site_id
          AND operation.candidate_revision_id=p_revision_id
          AND (operation.requested_by_user_id IS DISTINCT FROM v_authority.user_id
            OR (operation.authority_kind='owner_inbox'
                AND operation.decision_id IS DISTINCT FROM v_decision.id)
            OR (operation.authority_kind='standing_grant'
                AND NOT control.standing_dispatch_current(p_session_hash,p_site_id,p_generation)))) THEN
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
END; $$;

CREATE OR REPLACE FUNCTION control.weekly_revision_a2_policy(p_tenant uuid,p_site uuid,p_revision uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT EXISTS (SELECT 1 FROM app.candidate_recipe_revisions v
    JOIN control.recipe_releases r ON r.id=v.recipe_release_id AND r.content_hash=v.release_content_hash
    WHERE v.tenant_id=p_tenant AND v.site_id=p_site AND v.id=p_revision
      AND EXISTS (SELECT 1 FROM app.github_pr_extensions e JOIN app.github_read_bindings b
        ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id
        WHERE e.tenant_id=v.tenant_id AND e.site_id=v.site_id AND e.id=v.extension_id
          AND b.status='active' AND b.protected IS TRUE)
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

CREATE OR REPLACE FUNCTION control.owner_connector_request_allowed(p_profile text, p_method text, p_url text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
SELECT coalesce(CASE p_profile
    WHEN 'github_rest' THEN
        (p_method = 'GET' AND p_url ~ '^https://api[.]github[.]com/app/installations/[1-9][0-9]*$') OR
        (p_method = 'POST' AND p_url ~ '^https://api[.]github[.]com/app/installations/[1-9][0-9]*/access_tokens$')
        OR (p_method = 'GET' AND p_url ~ '^https://api[.]github[.]com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/branches/[^?#[:space:]]+|/git/(commits|blobs)/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}[?]recursive=1)?$')
    WHEN 'google_oauth_token' THEN p_method = 'POST' AND p_url = 'https://oauth2.googleapis.com/token'
    WHEN 'google_oauth_revoke' THEN p_method = 'POST' AND p_url = 'https://oauth2.googleapis.com/revoke'
    WHEN 'gsc_api' THEN
        (p_method = 'GET' AND p_url = 'https://www.googleapis.com/webmasters/v3/sites')
        OR (p_method = 'POST' AND p_url ~ '^https://www[.]googleapis[.]com/webmasters/v3/sites/[^/?#[:space:]]+/searchAnalytics/query$')
    WHEN 'slack_oauth' THEN p_method = 'POST' AND p_url = 'https://slack.com/api/oauth.v2.access'
    WHEN 'slack_bot' THEN p_method = 'POST' AND p_url IN (
        'https://slack.com/api/chat.postMessage', 'https://slack.com/api/auth.revoke'
    )
    ELSE false END, false);
$$;

CREATE OR REPLACE FUNCTION control.read_owner_github_connector(
    p_session_hash bytea,p_generation text,p_site_id uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_owner record; v_origin record; v_binding app.github_read_bindings%%ROWTYPE;
BEGIN
    SELECT * INTO v_owner FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF v_owner.outcome IS DISTINCT FROM 'authorized' OR v_owner.role_key IS DISTINCT FROM 'owner'
       OR v_owner.authentication_level IS DISTINCT FROM 'mfa'
    THEN RETURN jsonb_build_object('availability','denied'); END IF;
    SELECT * INTO v_origin FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_origin.outcome IS DISTINCT FROM 'verified'
    THEN RETURN jsonb_build_object('availability','denied'); END IF;
    SELECT * INTO v_binding FROM app.github_read_bindings AS binding
     WHERE binding.tenant_id=v_owner.tenant_id AND binding.site_id=p_site_id
     ORDER BY binding.prepared_at DESC,binding.id DESC LIMIT 1;
    IF NOT FOUND OR v_binding.status='revoked'
    THEN RETURN jsonb_build_object('availability','unbound'); END IF;
    RETURN jsonb_build_object('availability',CASE WHEN v_binding.recovery_generation<>p_generation
          OR v_binding.membership_epoch<>v_owner.membership_epoch
          OR v_binding.site_epoch<>v_owner.site_authorization_epoch THEN 'stale' ELSE v_binding.status END,
        'binding_id',v_binding.id,
        'installation_id',v_binding.installation_id,'owner',v_binding.repository_owner,
        'repository',v_binding.repository_name,'base_branch',v_binding.base_branch,
        'content_path',v_binding.content_path,'repository_id',v_binding.repository_id,
        'base_sha',v_binding.base_sha,'failure_code',v_binding.failure_code,
        'base_protection',CASE WHEN v_binding.protected IS TRUE THEN 'protected'
          WHEN v_binding.recovery_generation=p_generation AND control.github_unprotected_base_accepted(v_binding,p_generation)
          THEN 'owner_accepted_unprotected' ELSE 'unprotected_not_accepted' END);
END; $$;
