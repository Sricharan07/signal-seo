CREATE FUNCTION control.read_owner_gsc_connector(
    p_session_hash bytea,p_generation text,p_site_id uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_owner record; v_origin record; v_attempt app.gsc_oauth_attempts%%ROWTYPE;
    v_bound record; v_state text; v_properties jsonb;
BEGIN
    SELECT * INTO v_owner FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF v_owner.outcome IS DISTINCT FROM 'authorized' OR v_owner.role_key IS DISTINCT FROM 'owner'
       OR v_owner.authentication_level IS DISTINCT FROM 'mfa'
    THEN RETURN jsonb_build_object('availability','denied'); END IF;
    SELECT * INTO v_origin FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_origin.outcome IS DISTINCT FROM 'verified'
    THEN RETURN jsonb_build_object('availability','denied'); END IF;
    SELECT bound.binding_id,bound.property_resource_name,later.event_kind INTO v_bound
      FROM app.gsc_binding_events AS bound
      CROSS JOIN LATERAL (SELECT CASE WHEN EXISTS (
            SELECT 1 FROM app.gsc_binding_events AS event
             WHERE event.tenant_id=bound.tenant_id AND event.site_id=bound.site_id
               AND event.binding_id=bound.binding_id AND event.event_kind='revoked') THEN 'revoked'
          WHEN EXISTS (SELECT 1 FROM app.gsc_binding_events AS event
             WHERE event.tenant_id=bound.tenant_id AND event.site_id=bound.site_id
               AND event.binding_id=bound.binding_id AND event.event_kind='reauth_required') THEN 'reauth_required'
          ELSE 'bound' END AS event_kind) AS later
     WHERE bound.tenant_id=v_owner.tenant_id AND bound.site_id=p_site_id
       AND bound.event_kind='bound' AND bound.verified_origin=v_origin.origin
     ORDER BY (later.event_kind='bound') DESC,bound.recorded_at DESC,bound.id DESC LIMIT 1;
    IF FOUND AND v_bound.event_kind='bound' THEN
        RETURN jsonb_build_object('availability','bound','binding_id',v_bound.binding_id,
            'property_resource_name',v_bound.property_resource_name);
    END IF;
    SELECT * INTO v_attempt FROM app.gsc_oauth_attempts AS attempt
     WHERE attempt.tenant_id=v_owner.tenant_id AND attempt.site_id=p_site_id
       AND attempt.user_id=v_owner.user_id AND attempt.verified_origin=v_origin.origin
       AND attempt.status='selecting' AND attempt.expires_at>clock_timestamp()
     ORDER BY attempt.staged_at DESC,attempt.id DESC LIMIT 1;
    IF FOUND THEN
        SELECT coalesce(jsonb_agg(jsonb_build_object('resource_name',candidate.resource_name,
                'property_type',candidate.property_type)), '[]'::jsonb) INTO v_properties
          FROM (SELECT entry.value->>'resource_name' AS resource_name,
                    entry.value->>'property_type' AS property_type
                 FROM jsonb_array_elements(v_attempt.candidates) AS entry(value)
                WHERE entry.value->'eligible'='true'::jsonb
                  AND entry.value->>'property_type' IN ('url_prefix','domain')
                  AND length(entry.value->>'resource_name') BETWEEN 1 AND 2048
                  AND ((entry.value->>'property_type'='url_prefix'
                        AND entry.value->>'resource_name'=v_origin.origin||'/')
                    OR (entry.value->>'property_type'='domain'
                        AND entry.value->>'resource_name' ~ '^sc-domain:[a-z0-9.-]+$'
                        AND (split_part(split_part(v_origin.origin,'://',2),':',1)=substring(entry.value->>'resource_name' FROM 11)
                          OR right(split_part(split_part(v_origin.origin,'://',2),':',1),length(substring(entry.value->>'resource_name' FROM 11))+1)
                            ='.'||substring(entry.value->>'resource_name' FROM 11))))
                ORDER BY resource_name LIMIT 8) AS candidate;
        IF jsonb_array_length(v_properties)>0 THEN
            RETURN jsonb_build_object('availability','selecting','attempt_id',v_attempt.id,
                'properties',v_properties);
        END IF;
    END IF;
    IF v_bound.event_kind='reauth_required' THEN
        RETURN jsonb_build_object('availability','reauth_required','binding_id',v_bound.binding_id,
            'property_resource_name',v_bound.property_resource_name);
    END IF;
    RETURN jsonb_build_object('availability','unbound');
END; $$;

CREATE FUNCTION control.read_owner_github_connector(
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
        'base_sha',v_binding.base_sha,'failure_code',v_binding.failure_code);
END; $$;

REVOKE ALL ON FUNCTION control.read_owner_gsc_connector(bytea,text,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_owner_github_connector(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.read_owner_gsc_connector(bytea,text,uuid) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.read_owner_github_connector(bytea,text,uuid) TO signal_identity;
