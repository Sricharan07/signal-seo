-- Owner onboarding is not a crawl or a standing grant. Both paths share the
-- same locked origin buckets, pinned HTTP implementation and robots parser.
ALTER TABLE control.admission_leases
    DROP CONSTRAINT admission_leases_workload_id_check,
    ADD CONSTRAINT admission_leases_workload_id_check CHECK (
        workload_id ~ '^(crawl|egress|owner):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    );

CREATE TABLE app.owner_connector_egress_operations (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    session_hash bytea NOT NULL CHECK (octet_length(session_hash) = 32),
    recovery_generation text NOT NULL,
    verified_origin text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('robots', 'provider')),
    profile text NOT NULL CHECK (profile IN (
        'github_rest', 'google_oauth_token', 'google_oauth_revoke',
        'gsc_api', 'slack_oauth', 'slack_bot'
    )),
    method text NOT NULL CHECK (method IN ('GET', 'POST')),
    request_url text NOT NULL,
    target_url text NOT NULL,
    origin text NOT NULL,
    request_sha256 bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
    request_body_sha256 bytea CHECK (octet_length(request_body_sha256) = 32),
    request_bytes integer NOT NULL CHECK (request_bytes BETWEEN 0 AND 262144),
    max_response_bytes integer NOT NULL CHECK (max_response_bytes BETWEEN 1 AND 524288),
    credentialed boolean NOT NULL,
    robots_operation_id uuid,
    bucket_id uuid NOT NULL REFERENCES control.origin_buckets(id),
    state text NOT NULL DEFAULT 'dispatched' CHECK (state IN ('dispatched', 'observed', 'failed')),
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    finished_at timestamptz,
    response_evidence jsonb,
    robots_allowed boolean,
    robots_rules_sha256 bytea CHECK (octet_length(robots_rules_sha256) = 32),
    robots_crawl_delay_ms integer CHECK (robots_crawl_delay_ms BETWEEN 1 AND 60000),
    robots_expires_at timestamptz,
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites(tenant_id, id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app.memberships(tenant_id, user_id),
    FOREIGN KEY (tenant_id, site_id, robots_operation_id)
        REFERENCES app.owner_connector_egress_operations(tenant_id, site_id, id),
    FOREIGN KEY (id) REFERENCES control.admission_leases(id),
    CHECK (control.valid_crawl_url_identity(request_url, request_url, request_url, origin, 1)),
    CHECK (control.valid_crawl_url_identity(target_url, target_url, target_url, origin, 1)),
    CHECK ((kind = 'robots' AND method = 'GET' AND request_url = origin || '/robots.txt'
            AND request_bytes = 0 AND request_body_sha256 IS NULL AND NOT credentialed
            AND robots_operation_id IS NULL)
        OR (kind = 'provider' AND target_url = request_url AND robots_operation_id IS NOT NULL)),
    CHECK ((method = 'GET' AND request_bytes = 0 AND request_body_sha256 IS NULL)
        OR (method = 'POST' AND request_body_sha256 IS NOT NULL)),
    CHECK ((state = 'dispatched' AND finished_at IS NULL AND response_evidence IS NULL
            AND robots_allowed IS NULL AND robots_expires_at IS NULL)
        OR (state IN ('observed', 'failed') AND finished_at >= issued_at
            AND response_evidence IS NOT NULL)),
    CHECK (robots_expires_at IS NULL OR (kind = 'robots' AND robots_expires_at > finished_at
        AND robots_expires_at <= finished_at + interval '5 minutes'))
);
ALTER TABLE app.owner_connector_egress_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.owner_connector_egress_operations FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_connector_egress_scope ON app.owner_connector_egress_operations
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

CREATE FUNCTION app.guard_owner_connector_egress_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.state <> 'dispatched' OR NEW.state NOT IN ('observed', 'failed')
       OR (to_jsonb(OLD) - 'state' - 'finished_at' - 'response_evidence' - 'robots_allowed'
            - 'robots_rules_sha256' - 'robots_crawl_delay_ms' - 'robots_expires_at')
          IS DISTINCT FROM
          (to_jsonb(NEW) - 'state' - 'finished_at' - 'response_evidence' - 'robots_allowed'
            - 'robots_rules_sha256' - 'robots_crawl_delay_ms' - 'robots_expires_at')
    THEN RAISE EXCEPTION 'immutable_owner_connector_egress' USING ERRCODE = '55000'; END IF;
    RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION app.guard_owner_connector_egress_mutation() FROM PUBLIC;
CREATE TRIGGER owner_connector_egress_guard
BEFORE UPDATE OR DELETE ON app.owner_connector_egress_operations
FOR EACH ROW EXECUTE FUNCTION app.guard_owner_connector_egress_mutation();

CREATE FUNCTION control.owner_connector_request_allowed(p_profile text, p_method text, p_url text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
SELECT coalesce(CASE p_profile
    WHEN 'github_rest' THEN
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
REVOKE ALL ON FUNCTION control.owner_connector_request_allowed(text, text, text) FROM PUBLIC;

CREATE FUNCTION control.begin_owner_connector_egress(
    p_session_hash bytea, p_generation text, p_site_id uuid, p_operation_id uuid,
    p_kind text, p_profile text, p_method text, p_request_url text, p_target_url text,
    p_origin text, p_request_sha256 bytea, p_body_sha256 bytea, p_request_bytes integer,
    p_max_response_bytes integer, p_credentialed boolean, p_robots_operation_id uuid,
    p_min_delay_ms integer
) RETURNS TABLE (outcome text, bucket_id uuid, expires_at timestamptz,
    robots_allowed boolean, robots_crawl_delay_ms integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
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
    SELECT * INTO v_owner FROM control.resolve_snapshot_authority(p_session_hash,p_site_id,p_generation);
    IF v_owner.outcome IS DISTINCT FROM 'authorized' OR v_owner.role_key IS DISTINCT FROM 'owner'
       OR v_owner.authentication_level IS DISTINCT FROM 'mfa'
    THEN RETURN QUERY SELECT 'denied'::text,NULL::uuid,NULL::timestamptz,NULL::boolean,NULL::integer; RETURN; END IF;
    SELECT * INTO v_verified FROM control.verified_site_origin(v_owner.tenant_id,p_site_id);
    IF v_verified.outcome IS DISTINCT FROM 'verified'
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
END; $$;

CREATE FUNCTION control.finish_owner_connector_egress(
    p_session_hash bytea,p_generation text,p_site_id uuid,p_operation_id uuid,
    p_evidence jsonb,p_completion text,p_retry_after_ms integer,p_robots_allowed boolean,
    p_rules_sha256 bytea,p_crawl_delay_ms integer
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
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
END; $$;

REVOKE ALL ON FUNCTION control.begin_owner_connector_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,
    bytea,bytea,integer,integer,boolean,uuid,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.finish_owner_connector_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.begin_owner_connector_egress(bytea,text,uuid,uuid,text,text,text,text,text,text,
    bytea,bytea,integer,integer,boolean,uuid,integer) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.finish_owner_connector_egress(bytea,text,uuid,uuid,jsonb,text,integer,boolean,bytea,integer) TO signal_identity;
