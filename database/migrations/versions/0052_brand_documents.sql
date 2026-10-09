CREATE TABLE app.brand_documents (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 120),
    media_type text NOT NULL CHECK (media_type IN (
        'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'text/markdown', 'text/plain')),
    text_sha256 bytea NOT NULL CHECK (octet_length(text_sha256) = 32),
    injection_signal boolean NOT NULL,
    secret_signal boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    supersedes_id uuid,
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, artifact_id),
    UNIQUE (tenant_id, site_id, supersedes_id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, artifact_id) REFERENCES app.artifacts (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, supersedes_id)
        REFERENCES app.brand_documents (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, owner_user_id) REFERENCES app.memberships (tenant_id, user_id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE TABLE app.brand_document_events (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('deleted', 'retention_released')),
    actor_user_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, document_id, event_type),
    FOREIGN KEY (tenant_id, site_id, document_id)
        REFERENCES app.brand_documents (tenant_id, site_id, id)
);
CREATE INDEX brand_documents_site_created ON app.brand_documents
    (tenant_id, site_id, created_at DESC, id);
ALTER TABLE app.brand_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.brand_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE app.brand_document_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.brand_document_events FORCE ROW LEVEL SECURITY;
CREATE POLICY brand_documents_scope ON app.brand_documents
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY brand_document_events_scope ON app.brand_document_events
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE TRIGGER brand_documents_immutable BEFORE UPDATE OR DELETE ON app.brand_documents
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER brand_document_events_immutable BEFORE UPDATE OR DELETE ON app.brand_document_events
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.brand_document_owner(
    p_session_hash bytea, p_generation text, p_site_id uuid
) RETURNS TABLE (tenant_id uuid, user_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_authority record;
BEGIN
    SELECT * INTO v_authority FROM control.resolve_snapshot_authority(
        p_session_hash, p_site_id, p_generation);
    IF v_authority.outcome = 'authorized' AND v_authority.role_key = 'owner' THEN
        RETURN QUERY SELECT v_authority.tenant_id, v_authority.user_id;
    END IF;
END;
$$;

CREATE FUNCTION control.prepare_brand_document_upload(
    p_session_hash bytea, p_generation text, p_site_id uuid, p_supersedes_id uuid
) RETURNS TABLE (outcome text, tenant_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record; v_active_count integer;
BEGIN
    SELECT * INTO v_owner FROM control.brand_document_owner(p_session_hash, p_generation, p_site_id);
    IF v_owner.tenant_id IS NULL THEN
        RETURN QUERY SELECT 'denied'::text, NULL::uuid;
        RETURN;
    END IF;
    SELECT count(*) INTO v_active_count FROM app.brand_documents d
     WHERE d.tenant_id = v_owner.tenant_id AND d.site_id = p_site_id
       AND NOT EXISTS (SELECT 1 FROM app.brand_document_events e WHERE e.tenant_id = d.tenant_id
           AND e.site_id = d.site_id AND e.document_id = d.id AND e.event_type = 'deleted')
       AND NOT EXISTS (SELECT 1 FROM app.brand_documents next WHERE next.tenant_id = d.tenant_id
           AND next.site_id = d.site_id AND next.supersedes_id = d.id);
    IF v_active_count >= 20 AND p_supersedes_id IS NULL THEN
        RETURN QUERY SELECT 'count_limit'::text, v_owner.tenant_id;
        RETURN;
    END IF;
    IF p_supersedes_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM app.brand_documents d WHERE d.tenant_id = v_owner.tenant_id
          AND d.site_id = p_site_id AND d.id = p_supersedes_id
          AND NOT EXISTS (SELECT 1 FROM app.brand_document_events e
              WHERE e.tenant_id = d.tenant_id AND e.site_id = d.site_id
                AND e.document_id = d.id AND e.event_type = 'deleted')
          AND NOT EXISTS (SELECT 1 FROM app.brand_documents next
              WHERE next.tenant_id = d.tenant_id AND next.site_id = d.site_id
                AND next.supersedes_id = d.id)) THEN
        RETURN QUERY SELECT 'supersede_unavailable'::text, v_owner.tenant_id;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'ready'::text, v_owner.tenant_id;
END;
$$;

CREATE FUNCTION control.register_brand_document(
    p_session_hash bytea, p_generation text, p_site_id uuid, p_document_id uuid,
    p_artifact_id uuid, p_name text, p_media_type text, p_text_hash bytea,
    p_injection boolean, p_secret boolean, p_object_key text, p_object_version text,
    p_artifact_hash bytea, p_byte_length bigint, p_key_ref text,
    p_created_at timestamptz, p_retain_until timestamptz, p_supersedes_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record;
BEGIN
    SELECT * INTO v_owner FROM control.brand_document_owner(p_session_hash, p_generation, p_site_id);
    IF v_owner.tenant_id IS NULL THEN RETURN 'denied'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(v_owner.tenant_id::text || p_site_id::text, 72));
    IF (SELECT count(*) FROM app.brand_documents d WHERE d.tenant_id = v_owner.tenant_id
        AND d.site_id = p_site_id AND NOT EXISTS (
            SELECT 1 FROM app.brand_document_events e WHERE e.tenant_id = d.tenant_id
              AND e.site_id = d.site_id AND e.document_id = d.id AND e.event_type = 'deleted')
        AND NOT EXISTS (SELECT 1 FROM app.brand_documents next WHERE next.tenant_id = d.tenant_id
            AND next.site_id = d.site_id AND next.supersedes_id = d.id)) >= 20
        AND p_supersedes_id IS NULL THEN
        RETURN 'count_limit';
    END IF;
    IF p_supersedes_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM app.brand_documents d WHERE d.tenant_id = v_owner.tenant_id
          AND d.site_id = p_site_id AND d.id = p_supersedes_id
          AND NOT EXISTS (SELECT 1 FROM app.brand_document_events e
              WHERE e.tenant_id = d.tenant_id AND e.site_id = d.site_id
                AND e.document_id = d.id AND e.event_type = 'deleted')
          AND NOT EXISTS (SELECT 1 FROM app.brand_documents next
              WHERE next.tenant_id = d.tenant_id AND next.site_id = d.site_id
                AND next.supersedes_id = d.id)) THEN RETURN 'supersede_unavailable'; END IF;
    INSERT INTO app.artifacts (tenant_id, site_id, id, object_key, object_version, sha256,
        byte_length, media_type, encryption_key_ref, created_at, retain_until)
    VALUES (v_owner.tenant_id, p_site_id, p_artifact_id, p_object_key, p_object_version,
        p_artifact_hash, p_byte_length, p_media_type, p_key_ref, p_created_at, p_retain_until);
    INSERT INTO app.artifact_attestations (tenant_id, site_id, id, artifact_id, check_type,
        result, verified_hash, verified_at)
    VALUES (v_owner.tenant_id, p_site_id, p_document_id, p_artifact_id, 'upload_readback',
        'verified', p_artifact_hash, transaction_timestamp());
    INSERT INTO app.brand_documents (tenant_id, site_id, id, artifact_id, owner_user_id,
        display_name, media_type, text_sha256, injection_signal, secret_signal, created_at,
        supersedes_id)
    VALUES (v_owner.tenant_id, p_site_id, p_document_id, p_artifact_id, v_owner.user_id,
        p_name, p_media_type, p_text_hash, p_injection, p_secret, p_created_at, p_supersedes_id);
    RETURN 'registered';
END;
$$;

CREATE FUNCTION control.list_brand_documents(
    p_session_hash bytea, p_generation text, p_site_id uuid
) RETURNS TABLE (document_id uuid, display_name text, media_type text, created_at timestamptz,
    supersedes_id uuid, injection_signal boolean, secret_signal boolean, deleted boolean,
    retained_for_evidence boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record;
BEGIN
    SELECT * INTO v_owner FROM control.brand_document_owner(p_session_hash, p_generation, p_site_id);
    IF v_owner.tenant_id IS NULL THEN RETURN; END IF;
    RETURN QUERY SELECT d.id, d.display_name, d.media_type, d.created_at,
        d.supersedes_id, d.injection_signal, d.secret_signal,
        EXISTS (SELECT 1 FROM app.brand_document_events e WHERE e.tenant_id = d.tenant_id
            AND e.site_id = d.site_id AND e.document_id = d.id AND e.event_type = 'deleted'),
        TRUE
      FROM app.brand_documents d JOIN app.artifacts a ON a.tenant_id = d.tenant_id
        AND a.site_id = d.site_id AND a.id = d.artifact_id
     WHERE d.tenant_id = v_owner.tenant_id AND d.site_id = p_site_id
     ORDER BY d.created_at DESC, d.id DESC LIMIT 100;
END;
$$;

CREATE FUNCTION control.delete_brand_document(
    p_session_hash bytea, p_generation text, p_site_id uuid, p_document_id uuid, p_event_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record;
BEGIN
    SELECT * INTO v_owner FROM control.brand_document_owner(p_session_hash, p_generation, p_site_id);
    IF v_owner.tenant_id IS NULL THEN RETURN 'denied'; END IF;
    IF NOT EXISTS (SELECT 1 FROM app.brand_documents WHERE tenant_id = v_owner.tenant_id
        AND site_id = p_site_id AND id = p_document_id) THEN RETURN 'missing'; END IF;
    INSERT INTO app.brand_document_events (tenant_id, site_id, id, document_id, event_type, actor_user_id)
    VALUES (v_owner.tenant_id, p_site_id, p_event_id, p_document_id, 'deleted', v_owner.user_id)
    ON CONFLICT (tenant_id, site_id, document_id, event_type) DO NOTHING;
    RETURN 'deleted_retained';
END;
$$;

CREATE FUNCTION control.read_brand_document_artifact(
    p_session_hash bytea, p_generation text, p_site_id uuid, p_document_id uuid
) RETURNS TABLE (tenant_id uuid, artifact_id uuid, object_key text, object_version text,
    artifact_hash bytea, byte_length bigint, media_type text, encryption_key_ref text,
    artifact_created_at timestamptz, retain_until timestamptz, legal_hold boolean,
    durability_state text, text_hash bytea, injection_signal boolean, secret_signal boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_owner record;
BEGIN
    SELECT * INTO v_owner FROM control.brand_document_owner(p_session_hash, p_generation, p_site_id);
    IF v_owner.tenant_id IS NULL THEN RETURN; END IF;
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
       AND NOT EXISTS (SELECT 1 FROM app.brand_documents next
         WHERE next.tenant_id = d.tenant_id AND next.site_id = d.site_id
           AND next.supersedes_id = d.id);
END;
$$;

REVOKE ALL ON app.brand_documents, app.brand_document_events FROM PUBLIC;
REVOKE ALL ON FUNCTION control.brand_document_owner(bytea, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.brand_document_owner(bytea, text, uuid) TO signal_api;
REVOKE ALL ON FUNCTION control.prepare_brand_document_upload(bytea, text, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.prepare_brand_document_upload(bytea, text, uuid, uuid)
TO signal_api;
REVOKE ALL ON FUNCTION control.register_brand_document(bytea, text, uuid, uuid, uuid, text,
    text, bytea, boolean, boolean, text, text, bytea, bigint, text, timestamptz,
    timestamptz, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.list_brand_documents(bytea, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.delete_brand_document(bytea, text, uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.read_brand_document_artifact(bytea, text, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.register_brand_document(bytea, text, uuid, uuid, uuid, text,
    text, bytea, boolean, boolean, text, text, bytea, bigint, text, timestamptz,
    timestamptz, uuid) TO signal_api;
GRANT EXECUTE ON FUNCTION control.list_brand_documents(bytea, text, uuid) TO signal_api;
GRANT EXECUTE ON FUNCTION control.delete_brand_document(bytea, text, uuid, uuid, uuid) TO signal_api;
GRANT EXECUTE ON FUNCTION control.read_brand_document_artifact(bytea, text, uuid, uuid)
TO signal_api;
