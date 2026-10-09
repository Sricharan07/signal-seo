CREATE TABLE app.ai_visibility_question_sets (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    crawl_manifest_id uuid NOT NULL,
    supersedes_id uuid,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id, crawl_manifest_id)
        REFERENCES app.crawl_manifests (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, supersedes_id)
        REFERENCES app.ai_visibility_question_sets (tenant_id, site_id, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);

CREATE TABLE app.ai_visibility_questions (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    question_set_id uuid NOT NULL,
    question text NOT NULL CHECK (length(question) BETWEEN 8 AND 512),
    source_kind text NOT NULL CHECK (source_kind IN ('crawl', 'owner')),
    source_evidence_id uuid,
    PRIMARY KEY (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, question_set_id, question),
    FOREIGN KEY (tenant_id, site_id, question_set_id)
        REFERENCES app.ai_visibility_question_sets (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, source_evidence_id)
        REFERENCES app.crawl_page_records (tenant_id, site_id, id),
    CHECK ((source_kind = 'crawl' AND source_evidence_id IS NOT NULL)
        OR (source_kind = 'owner' AND source_evidence_id IS NULL))
);

CREATE TABLE app.ai_visibility_observations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    question_id uuid NOT NULL,
    provider text NOT NULL CHECK (provider IN ('openai', 'perplexity', 'gemini')),
    model text NOT NULL CHECK (length(model) BETWEEN 1 AND 128),
    status text NOT NULL CHECK (status IN ('complete', 'incomplete')),
    provider_evidence_id uuid,
    cited_pages jsonb NOT NULL CHECK (jsonb_typeof(cited_pages) = 'array'
        AND jsonb_array_length(cited_pages) <= 32),
    other_domains jsonb NOT NULL CHECK (jsonb_typeof(other_domains) = 'array'
        AND jsonb_array_length(other_domains) <= 32),
    usage jsonb NOT NULL CHECK (jsonb_typeof(usage) = 'object'),
    observed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, question_id)
        REFERENCES app.ai_visibility_questions (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, provider_evidence_id)
        REFERENCES app.assistant_provider_evidence (tenant_id, site_id, id),
    CHECK ((status = 'complete' AND provider_evidence_id IS NOT NULL)
        OR (status = 'incomplete' AND provider_evidence_id IS NULL)),
    CHECK ((status = 'incomplete' AND cited_pages = '[]'::jsonb AND other_domains = '[]'::jsonb)
        OR status = 'complete')
);
CREATE INDEX ai_visibility_observations_site_date
ON app.ai_visibility_observations (tenant_id, site_id, observed_at DESC, id);

ALTER TABLE app.ai_visibility_question_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ai_visibility_question_sets FORCE ROW LEVEL SECURITY;
ALTER TABLE app.ai_visibility_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ai_visibility_questions FORCE ROW LEVEL SECURITY;
ALTER TABLE app.ai_visibility_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ai_visibility_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_visibility_question_set_scope ON app.ai_visibility_question_sets
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY ai_visibility_question_scope ON app.ai_visibility_questions
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE POLICY ai_visibility_observation_scope ON app.ai_visibility_observations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());
CREATE TRIGGER ai_visibility_question_sets_immutable BEFORE UPDATE OR DELETE
ON app.ai_visibility_question_sets FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER ai_visibility_questions_immutable BEFORE UPDATE OR DELETE
ON app.ai_visibility_questions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER ai_visibility_observations_immutable BEFORE UPDATE OR DELETE
ON app.ai_visibility_observations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION control.record_ai_visibility_question_set(
    p_tenant_id uuid, p_site_id uuid, p_id uuid, p_manifest_id uuid, p_questions jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_previous uuid; v_question jsonb; v_count integer;
BEGIN
    IF p_id IS NULL OR p_manifest_id IS NULL OR jsonb_typeof(p_questions) <> 'array'
       OR jsonb_array_length(p_questions) NOT BETWEEN 1 AND 25 THEN
        RAISE EXCEPTION 'invalid_ai_visibility_question_set' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    PERFORM pg_advisory_xact_lock(hashtextextended(p_site_id::text, 75));
    IF NOT EXISTS (SELECT 1 FROM app.crawl_manifests WHERE tenant_id = p_tenant_id
                   AND site_id = p_site_id AND id = p_manifest_id) THEN
        RETURN 'crawl_evidence_unavailable';
    END IF;
    SELECT prior.id INTO v_previous FROM app.ai_visibility_question_sets AS prior
     WHERE prior.tenant_id = p_tenant_id AND prior.site_id = p_site_id
       AND NOT EXISTS (SELECT 1 FROM app.ai_visibility_question_sets AS later
           WHERE later.tenant_id = prior.tenant_id AND later.site_id = prior.site_id
             AND later.supersedes_id = prior.id)
     FOR UPDATE;
    FOR v_question IN SELECT value FROM jsonb_array_elements(p_questions) LOOP
        IF jsonb_typeof(v_question) <> 'object'
           OR v_question->>'id' !~ '^[0-9a-f-]{36}$'
           OR length(v_question->>'question') NOT BETWEEN 8 AND 512
           OR v_question->>'source_kind' NOT IN ('crawl', 'owner')
           OR (v_question->>'source_kind' = 'crawl' AND NOT EXISTS (
                SELECT 1 FROM app.crawl_page_records AS page
                JOIN app.crawl_manifests AS manifest ON manifest.tenant_id = page.tenant_id
                 AND manifest.site_id = page.site_id AND manifest.crawl_run_id = page.crawl_run_id
                WHERE page.tenant_id = p_tenant_id AND page.site_id = p_site_id
                  AND manifest.id = p_manifest_id AND page.id = (v_question->>'source_evidence_id')::uuid))
           OR (v_question->>'source_kind' = 'owner' AND v_question->>'source_evidence_id' IS NOT NULL)
        THEN RAISE EXCEPTION 'invalid_ai_visibility_question' USING ERRCODE = '22023'; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM (SELECT lower(value->>'question') AS question
      FROM jsonb_array_elements(p_questions)) AS values GROUP BY question HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'duplicate_ai_visibility_question' USING ERRCODE = '22023';
    END IF;
    INSERT INTO app.ai_visibility_question_sets (tenant_id, site_id, id, crawl_manifest_id, supersedes_id)
    VALUES (p_tenant_id, p_site_id, p_id, p_manifest_id, v_previous);
    INSERT INTO app.ai_visibility_questions (tenant_id, site_id, id, question_set_id, question,
        source_kind, source_evidence_id)
    SELECT p_tenant_id, p_site_id, (value->>'id')::uuid, p_id, value->>'question',
           value->>'source_kind', NULLIF(value->>'source_evidence_id', '')::uuid
      FROM jsonb_array_elements(p_questions);
    RETURN 'recorded';
END;
$$;

CREATE FUNCTION control.load_ai_visibility_crawl_sources(
    p_tenant_id uuid, p_site_id uuid, p_manifest_id uuid
) RETURNS TABLE (page_evidence_id uuid, fetch_url text, title text, headings jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    RETURN QUERY SELECT page.id, url.fetch_url, page.title, page.headings
      FROM app.crawl_manifests AS manifest
      JOIN app.crawl_page_records AS page ON page.tenant_id = manifest.tenant_id
       AND page.site_id = manifest.site_id AND page.crawl_run_id = manifest.crawl_run_id
      JOIN app.urls AS url ON url.tenant_id = page.tenant_id AND url.site_id = page.site_id
       AND url.id = page.url_id
     WHERE manifest.tenant_id = p_tenant_id AND manifest.site_id = p_site_id
       AND manifest.id = p_manifest_id
     ORDER BY page.id;
END;
$$;

CREATE FUNCTION control.record_ai_visibility_observation(
    p_tenant_id uuid, p_site_id uuid, p_id uuid, p_question_id uuid, p_provider text,
    p_model text, p_status text, p_provider_evidence_id uuid, p_cited_pages jsonb,
    p_other_domains jsonb, p_usage jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF p_id IS NULL OR p_question_id IS NULL OR p_provider NOT IN ('openai', 'perplexity', 'gemini')
       OR length(p_model) NOT BETWEEN 1 AND 128 OR p_status NOT IN ('complete', 'incomplete')
       OR jsonb_typeof(p_cited_pages) <> 'array' OR jsonb_typeof(p_other_domains) <> 'array'
       OR jsonb_array_length(p_cited_pages) > 32 OR jsonb_array_length(p_other_domains) > 32
       OR jsonb_typeof(p_usage) <> 'object' THEN
        RAISE EXCEPTION 'invalid_ai_visibility_observation' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('signal.tenant_id', p_tenant_id::text, true);
    PERFORM set_config('signal.site_id', p_site_id::text, true);
    IF NOT EXISTS (SELECT 1 FROM app.ai_visibility_questions WHERE tenant_id = p_tenant_id
                   AND site_id = p_site_id AND id = p_question_id) THEN RETURN 'question_unavailable'; END IF;
    IF p_status = 'complete' AND NOT EXISTS (SELECT 1 FROM app.assistant_provider_evidence
       WHERE tenant_id = p_tenant_id AND site_id = p_site_id AND id = p_provider_evidence_id
       AND provider = p_provider AND model_reported = p_model) THEN RETURN 'provider_evidence_unavailable'; END IF;
    IF (p_status = 'complete' AND p_provider_evidence_id IS NULL)
       OR (p_status = 'incomplete' AND (p_provider_evidence_id IS NOT NULL
           OR p_cited_pages <> '[]'::jsonb OR p_other_domains <> '[]'::jsonb)) THEN
        RAISE EXCEPTION 'invalid_ai_visibility_coverage' USING ERRCODE = '22023'; END IF;
    INSERT INTO app.ai_visibility_observations (tenant_id, site_id, id, question_id, provider,
        model, status, provider_evidence_id, cited_pages, other_domains, usage)
    VALUES (p_tenant_id, p_site_id, p_id, p_question_id, p_provider, p_model, p_status,
        p_provider_evidence_id, p_cited_pages, p_other_domains, p_usage);
    RETURN 'recorded';
END;
$$;

REVOKE ALL ON app.ai_visibility_question_sets, app.ai_visibility_questions,
    app.ai_visibility_observations FROM PUBLIC, signal_identity, signal_bootstrap,
    signal_api, signal_scheduler, signal_workflow, signal_crawl_admission, signal_crawl_ingest;
REVOKE ALL ON FUNCTION control.record_ai_visibility_question_set(uuid, uuid, uuid, uuid, jsonb),
    control.record_ai_visibility_observation(uuid, uuid, uuid, uuid, text, text, text, uuid, jsonb, jsonb, jsonb),
    control.load_ai_visibility_crawl_sources(uuid, uuid, uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.record_ai_visibility_question_set(uuid, uuid, uuid, uuid, jsonb)
TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.record_ai_visibility_observation(
    uuid, uuid, uuid, uuid, text, text, text, uuid, jsonb, jsonb, jsonb
) TO signal_crawl_ingest;
GRANT EXECUTE ON FUNCTION control.load_ai_visibility_crawl_sources(uuid, uuid, uuid)
TO signal_crawl_ingest;
