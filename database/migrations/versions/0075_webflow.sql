CREATE TABLE app.webflow_oauth_attempts (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, actor_id uuid NOT NULL,
 state_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(state_sha256)=32), redirect_uri text NOT NULL,
 origin text NOT NULL, provider_site text NOT NULL CHECK(provider_site ~ '^[0-9a-f]{24}$'),
 collection_id text NOT NULL CHECK(collection_id ~ '^[0-9a-f]{24}$'), recovery_generation text NOT NULL,
 consumed_at timestamptz, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id), FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
CREATE TABLE app.webflow_bindings (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, attempt_id uuid NOT NULL,
 actor_id uuid NOT NULL, origin text NOT NULL, provider_site text NOT NULL, collection_id text NOT NULL,
 field_mapping jsonb NOT NULL, field_schema jsonb NOT NULL, schema_sha256 bytea NOT NULL CHECK(octet_length(schema_sha256)=32),
 secret_reference text NOT NULL CHECK(secret_reference='secret://webflow/'||id::text),
 recovery_generation text NOT NULL, revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id), FOREIGN KEY(tenant_id,site_id,attempt_id) REFERENCES app.webflow_oauth_attempts(tenant_id,site_id,id),
 UNIQUE(tenant_id,site_id,attempt_id)
);
CREATE UNIQUE INDEX webflow_one_site ON app.webflow_bindings(tenant_id,site_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX webflow_global_site_claim ON app.webflow_bindings(provider_site) WHERE revoked_at IS NULL;
CREATE TABLE app.webflow_revisions (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, id uuid NOT NULL, binding_id uuid NOT NULL,
 candidate_id uuid NOT NULL, source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32),
 canonical bytea NOT NULL CHECK(octet_length(canonical)<=65536), revision_sha256 bytea NOT NULL CHECK(revision_sha256=sha256(canonical)),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(), PRIMARY KEY(tenant_id,site_id,id),
 UNIQUE(tenant_id,site_id,candidate_id),
 FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.webflow_bindings(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,candidate_id) REFERENCES app.content_candidates(tenant_id,site_id,id)
);
CREATE TABLE app.webflow_reviews (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,revision_id uuid NOT NULL,actor_id uuid NOT NULL,
 decision text NOT NULL CHECK(decision IN ('approved','rejected','changes_requested')),
 revision_sha256 bytea NOT NULL,recovery_generation text NOT NULL,membership_epoch bigint NOT NULL,site_epoch bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,revision_id),
 FOREIGN KEY(tenant_id,site_id,revision_id) REFERENCES app.webflow_revisions(tenant_id,site_id,id)
);
CREATE TABLE app.webflow_operations (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,revision_id uuid NOT NULL,
 state text NOT NULL CHECK(state IN ('PLANNED','AUTHORIZED','OUTCOME_UNKNOWN','DRAFT_RECORDED','ESCALATED')),
 nonce uuid,permit_until timestamptz,journal_generation uuid,journal_position bigint,journal_hash bytea,
 provider_item text CHECK(provider_item ~ '^[0-9a-f]{24}$'),created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),UNIQUE(tenant_id,site_id,revision_id),
 FOREIGN KEY(tenant_id,site_id,revision_id) REFERENCES app.webflow_revisions(tenant_id,site_id,id)
);
CREATE TABLE app.webflow_events (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,subject_id uuid NOT NULL,kind text NOT NULL,
 actor_id uuid NOT NULL,evidence_sha256 bytea CHECK(octet_length(evidence_sha256)=32),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['webflow_oauth_attempts','webflow_bindings','webflow_revisions','webflow_reviews','webflow_operations','webflow_events'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY webflow_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest',t);
  IF t IN ('webflow_revisions','webflow_reviews','webflow_events') THEN
   EXECUTE format('CREATE TRIGGER webflow_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
  END IF;
 END LOOP;
END; $$;

CREATE FUNCTION control.webflow_escape(p text) RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT replace(replace(replace(replace(replace(p,'&','&amp;'),'<','&lt;'),'>','&gt;'),'"','&quot;'),'''','&#x27;');
$$;
REVOKE ALL ON FUNCTION control.webflow_escape(text) FROM PUBLIC;
CREATE FUNCTION control.webflow_render(p jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE s jsonb;sentence jsonb;l jsonb;result text:=''; BEGIN
 FOR s IN SELECT jsonb_array_elements(p->'sections') LOOP
  result:=result||'<h2>'||control.webflow_escape(s->'heading'->>'text')||'</h2>';
  FOR sentence IN SELECT jsonb_array_elements(s->'sentences') LOOP
   result:=result||'<p>'||control.webflow_escape(sentence->>'text')||'</p>';
  END LOOP;
 END LOOP;
 FOR l IN SELECT jsonb_array_elements(p->'internal_links') LOOP
  result:=result||'<p><a href="'||control.webflow_escape(l->>'url')||'">'||control.webflow_escape(l->>'anchor')||'</a></p>';
 END LOOP; RETURN result;
END; $$;
REVOKE ALL ON FUNCTION control.webflow_render(jsonb) FROM PUBLIC;

CREATE FUNCTION control.webflow_command(p_hash bytea,p_generation text,p_site uuid,p_action text,p jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a record;o record;attempt app.webflow_oauth_attempts%%ROWTYPE;b app.webflow_bindings%%ROWTYPE;
 r app.webflow_revisions%%ROWTYPE;op app.webflow_operations%%ROWTYPE;review app.webflow_reviews%%ROWTYPE;
 c app.content_candidates%%ROWTYPE;id uuid;payload jsonb;article jsonb;result jsonb;event uuid;ref text;f jsonb;mapping jsonb;schema jsonb;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
 IF p_action='send' THEN
  -- Hold value-update locks through the bounded credential-bearing fetch, then
  -- resolve again so a restriction racing lock acquisition cannot be missed.
  PERFORM 1 FROM app.sessions s
   JOIN control.identity_sessions i ON i.id=s.identity_session_id
   JOIN control.users u ON u.id=s.user_id
   JOIN app.memberships m ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id
   JOIN app.site_memberships sm ON sm.tenant_id=s.tenant_id AND sm.user_id=s.user_id AND sm.site_id=p_site
   JOIN app.tenants t ON t.tenant_id=s.tenant_id
   JOIN app.sites st ON st.tenant_id=s.tenant_id AND st.id=p_site
   WHERE s.session_token_hash=p_hash FOR SHARE OF s,i,u,m,sm,t,st;
  PERFORM 1 FROM app.site_weekly_control WHERE tenant_id=a.tenant_id AND site_id=p_site FOR SHARE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
  IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner' THEN RETURN NULL; END IF;
 END IF;
 SELECT * INTO o FROM control.verified_site_origin(a.tenant_id,p_site);
 IF p_action='read' THEN
  RETURN jsonb_build_object('bindings',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT id,origin,provider_site,collection_id,field_mapping,encode(schema_sha256,'hex') AS schema_sha256,revoked_at FROM app.webflow_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site ORDER BY created_at DESC LIMIT 100) x),'[]'::jsonb),
   'inbox',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT r.id,r.binding_id,r.candidate_id,convert_from(r.canonical,'UTF8')::jsonb AS payload,encode(r.revision_sha256,'hex') AS revision_sha256,coalesce(v.decision,'pending') AS decision,coalesce(op.state,'PLANNED') AS state,op.provider_item FROM app.webflow_revisions r LEFT JOIN app.webflow_reviews v ON v.tenant_id=r.tenant_id AND v.site_id=r.site_id AND v.revision_id=r.id LEFT JOIN app.webflow_operations op ON op.tenant_id=r.tenant_id AND op.site_id=r.site_id AND op.revision_id=r.id WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site ORDER BY r.created_at DESC LIMIT 100) x),'[]'::jsonb));
 END IF;
 IF p_action='revoke' THEN
  SELECT * INTO b FROM app.webflow_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=(p->>'id')::uuid FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE app.webflow_bindings SET revoked_at=coalesce(revoked_at,transaction_timestamp()) WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=b.id;
  SELECT event_id INTO event FROM control.webflow_revocations WHERE target_id=b.id;
  IF event IS NULL THEN
   event:=gen_random_uuid();INSERT INTO control.webflow_revocations VALUES(event,b.id,a.user_id,a.tenant_id,p_site);
   INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason) VALUES(event,'webflow.binding.revoked',a.user_id,'webflow_binding',b.id,jsonb_build_object('schema_version',1,'restriction_kind','webflow_binding_revoked','target_id',b.id),'owner_revocation');
  END IF;
  RETURN jsonb_build_object('state',CASE WHEN EXISTS(SELECT 1 FROM control.platform_events WHERE event_type='authority.restriction.acknowledged' AND object_id=event) THEN 'revoked' ELSE 'AUTHORITY_DURABILITY_PENDING' END,'secret_reference',b.secret_reference,'event_id',event);
 END IF;
 IF o.outcome IS DISTINCT FROM 'verified' OR EXISTS(SELECT 1 FROM app.site_weekly_control WHERE tenant_id=a.tenant_id AND site_id=p_site AND paused) THEN RETURN NULL; END IF;
 IF p_action='begin_oauth' THEN
  id:=(p->>'id')::uuid;
  IF p->>'redirect_uri' !~ '^https://[^/?#]+/auth/webflow/callback$' OR p->>'provider_site' !~ '^[0-9a-f]{24}$' OR p->>'collection_id' !~ '^[0-9a-f]{24}$' THEN RETURN NULL; END IF;
  INSERT INTO app.webflow_oauth_attempts(tenant_id,site_id,id,actor_id,state_sha256,redirect_uri,origin,provider_site,collection_id,recovery_generation) VALUES(a.tenant_id,p_site,id,a.user_id,decode(p->>'state_sha256','hex'),p->>'redirect_uri',o.origin,p->>'provider_site',p->>'collection_id',p_generation);
  result:=jsonb_build_object('state','created','id',id);
 ELSIF p_action IN ('consume_oauth','bind') THEN
  SELECT * INTO attempt FROM app.webflow_oauth_attempts WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=(p->>'attempt_id')::uuid FOR UPDATE;
  IF NOT FOUND OR attempt.actor_id<>a.user_id OR attempt.origin<>o.origin OR attempt.recovery_generation<>p_generation OR attempt.created_at<transaction_timestamp()-interval '10 minutes' THEN RETURN NULL; END IF;
  IF p_action='consume_oauth' THEN
   IF attempt.consumed_at IS NOT NULL OR attempt.state_sha256 IS DISTINCT FROM decode(p->>'state_sha256','hex') OR attempt.redirect_uri IS DISTINCT FROM p->>'redirect_uri' THEN RETURN NULL; END IF;
   UPDATE app.webflow_oauth_attempts SET consumed_at=transaction_timestamp() WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=attempt.id;
   result:=jsonb_build_object('state','consumed','origin',o.origin,'provider_site',attempt.provider_site,'collection_id',attempt.collection_id);
  ELSE
   id:=(p->>'id')::uuid;
   IF attempt.consumed_at IS NULL OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='webflow_binding' AND target_id=id) OR EXISTS(SELECT 1 FROM control.webflow_revocations WHERE target_id=id) THEN RETURN NULL; END IF;
   mapping:=p->'field_mapping';schema:=p->'field_schema';
   IF jsonb_typeof(mapping) IS DISTINCT FROM 'object' OR mapping-'title'-'description'-'body'<>'{}'::jsonb OR mapping->>'title' IS DISTINCT FROM 'name' OR coalesce(mapping->>'description','') !~ '^[a-z][a-z0-9-]{0,63}$' OR coalesce(mapping->>'body','') !~ '^[a-z][a-z0-9-]{0,63}$' OR mapping->>'description' IN ('name','slug') OR mapping->>'body' IN ('name','slug',mapping->>'description') OR jsonb_typeof(schema) IS DISTINCT FROM 'array' OR jsonb_array_length(schema) NOT BETWEEN 4 AND 100 THEN RETURN NULL; END IF;
   IF (SELECT count(DISTINCT x->>'slug') FROM jsonb_array_elements(schema) x)<>jsonb_array_length(schema) THEN RETURN NULL; END IF;
   FOR f IN SELECT value FROM jsonb_array_elements(schema) LOOP
    IF jsonb_typeof(f->'isRequired') IS DISTINCT FROM 'boolean' OR (f->>'isRequired'='true' AND f->>'slug' NOT IN ('slug','name',mapping->>'description',mapping->>'body')) THEN RETURN NULL; END IF;
   END LOOP;
   FOR ref IN SELECT value FROM jsonb_each_text(mapping) LOOP
    IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(schema) x WHERE x->>'slug'=ref AND x->>'type'=CASE WHEN ref=mapping->>'body' THEN 'RichText' ELSE 'PlainText' END AND x->>'isEditable'='true' AND coalesce(x->'validations','{}'::jsonb) IN ('{}'::jsonb,'null'::jsonb)) THEN RETURN NULL; END IF;
   END LOOP;
   IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(schema) x WHERE x->>'slug'='slug' AND x->>'type'='PlainText') THEN RETURN NULL; END IF;
   INSERT INTO app.webflow_bindings(tenant_id,site_id,id,attempt_id,actor_id,origin,provider_site,collection_id,field_mapping,field_schema,schema_sha256,secret_reference,recovery_generation) VALUES(a.tenant_id,p_site,id,attempt.id,a.user_id,o.origin,attempt.provider_site,attempt.collection_id,p->'field_mapping',p->'field_schema',decode(p->>'schema_sha256','hex'),'secret://webflow/'||id::text,p_generation);
   INSERT INTO app.site_weekly_control(tenant_id,site_id) VALUES(a.tenant_id,p_site) ON CONFLICT DO NOTHING;
   result:=jsonb_build_object('state','bound','id',id);
  END IF;
 ELSE
  IF p_action IN ('source','seal','binding') THEN
   SELECT * INTO b FROM app.webflow_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=(p->>'binding_id')::uuid FOR UPDATE;
  ELSE
   SELECT * INTO r FROM app.webflow_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=(p->>'id')::uuid;
   SELECT * INTO b FROM app.webflow_bindings WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=r.binding_id FOR UPDATE;
  END IF;
  IF b.id IS NULL OR b.revoked_at IS NOT NULL OR b.origin<>o.origin OR b.recovery_generation<>p_generation OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='webflow_binding' AND target_id=b.id) OR EXISTS(SELECT 1 FROM control.webflow_revocations WHERE target_id=b.id) THEN RETURN NULL; END IF;
  IF p_action='binding' THEN RETURN to_jsonb(b)-'actor_id'-'created_at'-'revoked_at'; END IF;
  IF p_action IN ('source','seal') THEN
   id:=(p->>'id')::uuid;
   SELECT * INTO c FROM app.content_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=(p->>'candidate_id')::uuid;
   IF NOT FOUND OR c.revision_sha256 IS DISTINCT FROM decode(p->>'source_sha256','hex') OR c.manifest->>'work_type' IS DISTINCT FROM 'new_article' OR c.manifest->>'autonomy_eligible' IS DISTINCT FROM 'false' OR c.manifest->>'approval_class' IS DISTINCT FROM 'A2' OR c.manifest->>'threshold' IS DISTINCT FROM '0.95' THEN RETURN NULL; END IF;
   IF EXISTS(SELECT 1 FROM app.webflow_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site AND candidate_id=c.id) THEN RETURN jsonb_build_object('state','conflict'); END IF;
   SELECT dr.payload->'article' INTO article FROM app.content_draft_results dr WHERE dr.tenant_id=a.tenant_id AND dr.site_id=p_site AND dr.draft_id=c.draft_id AND dr.payload->'originality'->>'state'='original' AND dr.payload->>'state' IN ('grounded','owner_required');
   IF article IS NULL OR EXISTS(SELECT 1 FROM app.content_draft_intents d JOIN app.content_briefs newer ON newer.tenant_id=d.tenant_id AND newer.site_id=d.site_id AND newer.supersedes_id=d.brief_id WHERE d.tenant_id=a.tenant_id AND d.site_id=p_site AND d.id=c.draft_id) OR EXISTS(SELECT 1 FROM app.content_draft_intents d CROSS JOIN LATERAL jsonb_array_elements(d.fact_snapshot) x WHERE d.tenant_id=a.tenant_id AND d.site_id=p_site AND d.id=c.draft_id AND NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f WHERE f.fact_id::text=x->>'fact_id' AND f.statement=x->>'statement' AND f.category=x->>'category')) THEN RETURN NULL; END IF;
   IF p_action='source' THEN RETURN jsonb_build_object('article',article,'revision_sha256',encode(c.revision_sha256,'hex')); END IF;
   payload:=p->'payload';
   IF article IS NULL OR payload->'items'->0->'fieldData' IS DISTINCT FROM jsonb_build_object(b.field_mapping->>'title',article->'title'->>'text',b.field_mapping->>'description',article->'meta_description'->>'text',b.field_mapping->>'body',control.webflow_render(article),'slug',payload->'items'->0->'fieldData'->>'slug') OR payload->'items'->0->>'isDraft' IS DISTINCT FROM 'true' OR jsonb_array_length(payload->'items')<>1 OR payload- 'items'<>'{}'::jsonb OR (payload->'items'->0)-'isDraft'-'fieldData'<>'{}'::jsonb OR payload->'items'->0->'fieldData'->>'slug' !~ ('^[a-z0-9-]{1,80}-signal-'||replace(id::text,'-','')||'$') THEN RETURN NULL; END IF;
   INSERT INTO app.webflow_revisions VALUES(a.tenant_id,p_site,id,b.id,c.id,c.revision_sha256,decode(p->>'canonical_hex','hex'),decode(p->>'revision_sha256','hex'),transaction_timestamp());
   IF convert_from(decode(p->>'canonical_hex','hex'),'UTF8')::jsonb IS DISTINCT FROM payload THEN RAISE EXCEPTION 'webflow_revision_invalid'; END IF;
   INSERT INTO app.webflow_operations(tenant_id,site_id,id,revision_id,state) VALUES(a.tenant_id,p_site,id,id,'PLANNED');
   result:=jsonb_build_object('state','sealed','id',id);
  ELSE
   SELECT * INTO c FROM app.content_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=r.candidate_id;
   IF c.id IS NULL OR c.revision_sha256<>r.source_sha256 OR EXISTS(SELECT 1 FROM app.content_draft_intents d JOIN app.content_briefs newer ON newer.tenant_id=d.tenant_id AND newer.site_id=d.site_id AND newer.supersedes_id=d.brief_id WHERE d.tenant_id=a.tenant_id AND d.site_id=p_site AND d.id=c.draft_id) OR EXISTS(SELECT 1 FROM app.content_draft_intents d CROSS JOIN LATERAL jsonb_array_elements(d.fact_snapshot) x WHERE d.tenant_id=a.tenant_id AND d.site_id=p_site AND d.id=c.draft_id AND NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) f WHERE f.fact_id::text=x->>'fact_id' AND f.statement=x->>'statement' AND f.category=x->>'category')) THEN RETURN NULL; END IF;
   SELECT * INTO op FROM app.webflow_operations WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=r.id FOR UPDATE;
   IF p_action='review' THEN
    IF r.revision_sha256 IS DISTINCT FROM decode(p->>'revision_sha256','hex') OR p->>'decision' NOT IN ('approved','rejected','changes_requested') THEN RETURN NULL; END IF;
    SELECT * INTO review FROM app.webflow_reviews WHERE tenant_id=a.tenant_id AND site_id=p_site AND revision_id=r.id;
    IF FOUND THEN RETURN jsonb_build_object('state',CASE WHEN review.decision=p->>'decision' THEN 'replayed' ELSE 'conflict' END); END IF;
    INSERT INTO app.webflow_reviews VALUES(a.tenant_id,p_site,r.id,a.user_id,p->>'decision',r.revision_sha256,p_generation,a.membership_epoch,a.site_authorization_epoch,transaction_timestamp());
    result:=jsonb_build_object('state','reviewed');
   ELSIF p_action='packet' THEN
    RETURN jsonb_build_object('state',op.state,'id',r.id,'payload',convert_from(r.canonical,'UTF8')::jsonb,'revision_sha256',encode(r.revision_sha256,'hex'),'candidate_id',r.candidate_id,'binding',to_jsonb(b)-'actor_id'-'created_at'-'revoked_at','journal_generation',op.journal_generation);
   ELSIF p_action='quarantine' THEN
    IF op.state NOT IN ('PLANNED','AUTHORIZED','OUTCOME_UNKNOWN') THEN RETURN jsonb_build_object('state',op.state); END IF;
    UPDATE app.webflow_operations SET state='OUTCOME_UNKNOWN' WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=op.id;
    result:=jsonb_build_object('state','OUTCOME_UNKNOWN');
   ELSIF p_action='reconcile' THEN
    IF op.state NOT IN ('OUTCOME_UNKNOWN','ESCALATED') OR p->>'state' NOT IN ('OUTCOME_UNKNOWN','ESCALATED','DRAFT_RECORDED') THEN RETURN NULL; END IF;
    UPDATE app.webflow_operations SET state=CASE WHEN op.state='ESCALATED' THEN 'ESCALATED' ELSE p->>'state' END,provider_item=coalesce(provider_item,p->>'provider_item') WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=op.id;
    result:=jsonb_build_object('state',CASE WHEN op.state='ESCALATED' THEN 'ESCALATED' ELSE p->>'state' END);
   ELSIF p_action IN ('eligibility','authorize','send') THEN
    SELECT * INTO review FROM app.webflow_reviews WHERE tenant_id=a.tenant_id AND site_id=p_site AND revision_id=r.id;
    IF NOT FOUND OR review.decision<>'approved' OR review.recovery_generation<>p_generation OR review.membership_epoch<>a.membership_epoch OR review.site_epoch<>a.site_authorization_epoch OR review.created_at<transaction_timestamp()-interval '24 hours' OR review.actor_id<>a.user_id OR review.revision_sha256<>r.revision_sha256 THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM app.webflow_operations x WHERE x.tenant_id=a.tenant_id AND x.site_id=p_site AND x.id<>op.id AND x.state IN ('AUTHORIZED','OUTCOME_UNKNOWN','ESCALATED')) THEN RETURN jsonb_build_object('state','quarantined'); END IF;
    IF p_action='eligibility' THEN RETURN jsonb_build_object('state','eligible');
    ELSIF p_action='authorize' THEN
     IF op.state<>'PLANNED' OR op.journal_generation IS NOT NULL THEN RETURN jsonb_build_object('state',op.state); END IF;
     IF p->>'journal_hash' !~ '^[0-9a-f]{64}$' OR (p->>'journal_position')::bigint<1 OR p->>'journal_generation' IS NULL THEN RETURN NULL; END IF;
     UPDATE app.webflow_operations SET state='AUTHORIZED',nonce=(p->>'nonce')::uuid,permit_until=transaction_timestamp()+interval '30 seconds',journal_generation=(p->>'journal_generation')::uuid,journal_position=(p->>'journal_position')::bigint,journal_hash=decode(p->>'journal_hash','hex') WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=op.id;
     result:=jsonb_build_object('state','AUTHORIZED');
    ELSE
     IF op.state<>'AUTHORIZED' OR op.nonce IS DISTINCT FROM (p->>'nonce')::uuid OR op.permit_until<clock_timestamp() OR op.journal_generation IS NULL OR r.revision_sha256 IS DISTINCT FROM decode(p->>'revision_sha256','hex') THEN RETURN NULL; END IF;
     UPDATE app.webflow_operations SET state='OUTCOME_UNKNOWN' WHERE tenant_id=a.tenant_id AND site_id=p_site AND id=op.id;
     result:=jsonb_build_object('state','send_once');
    END IF;
   ELSE RETURN NULL; END IF;
  END IF;
 END IF;
 INSERT INTO app.webflow_events VALUES(a.tenant_id,p_site,gen_random_uuid(),coalesce(id,r.id,attempt.id),p_action,a.user_id,CASE WHEN p_action='reconcile' THEN decode(p->>'evidence_sha256','hex') END,transaction_timestamp());
 RETURN result;
END; $$;
REVOKE ALL ON FUNCTION control.webflow_command(bytea,text,uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.webflow_command(bytea,text,uuid,text,jsonb) TO signal_api;

CREATE TABLE control.webflow_revocations (
 event_id uuid PRIMARY KEY,target_id uuid NOT NULL UNIQUE,actor_user_id uuid NOT NULL,
 tenant_id uuid NOT NULL,site_id uuid NOT NULL
);
REVOKE ALL ON control.webflow_revocations FROM PUBLIC;
CREATE TRIGGER webflow_revocation_immutable BEFORE UPDATE OR DELETE ON control.webflow_revocations
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

-- Preserve prior event contracts exactly while adding one closed restrictive kind.
DO $$ DECLARE t text;c text;expr text;extra text; BEGIN
 FOREACH t IN ARRAY ARRAY['authority_restriction_outbox','authority_denial_tombstones'] LOOP
  FOREACH c IN ARRAY ARRAY['target_kind','restriction_kind'] LOOP
   SELECT pg_get_expr(conbin,conrelid) INTO expr FROM pg_constraint
   WHERE conrelid=('control.'||t)::regclass AND conname=t||'_'||c||'_check';
   extra:=CASE WHEN c='target_kind' THEN 'target_kind=''webflow_binding'''
    ELSE '(target_kind=''webflow_binding'' AND restriction_kind=''webflow_binding_revoked'')' END;
   EXECUTE format('ALTER TABLE control.%%I DROP CONSTRAINT %%I',t,t||'_'||c||'_check');
   EXECUTE format('ALTER TABLE control.%%I ADD CONSTRAINT %%I CHECK((%%s) OR %%s)',t,t||'_'||c||'_check',expr,extra);
  END LOOP;
 END LOOP;
 SELECT pg_get_expr(conbin,conrelid) INTO expr FROM pg_constraint WHERE conrelid='control.platform_events'::regclass AND conname='platform_events_contract_check';
 ALTER TABLE control.platform_events DROP CONSTRAINT platform_events_contract_check;
 EXECUTE 'ALTER TABLE control.platform_events ADD CONSTRAINT platform_events_contract_check CHECK(('||expr||') OR (event_type=''webflow.binding.revoked'' AND actor_user_id IS NOT NULL AND object_kind=''webflow_binding'' AND reason=''owner_revocation'' AND facts=jsonb_build_object(''schema_version'',1,''restriction_kind'',''webflow_binding_revoked'',''target_id'',object_id)))';
END; $$;
DROP TRIGGER platform_event_reference_guard ON control.platform_events;
CREATE TRIGGER platform_event_reference_guard BEFORE INSERT ON control.platform_events
FOR EACH ROW WHEN(NEW.event_type<>'webflow.binding.revoked') EXECUTE FUNCTION control.validate_platform_event_reference();
CREATE FUNCTION control.webflow_restriction_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM control.webflow_revocations WHERE event_id=NEW.id AND target_id=NEW.object_id AND actor_user_id=NEW.actor_user_id) THEN
  RAISE EXCEPTION 'webflow_restriction_invalid' USING ERRCODE='23503'; END IF;
 INSERT INTO control.authority_restriction_outbox(event_id,scope_kind,actor_user_id,target_kind,target_id,restriction_kind,effective_epoch,event_time,original_facts)
 VALUES(NEW.id,'platform',NEW.actor_user_id,'webflow_binding',NEW.object_id,'webflow_binding_revoked',1,NEW.created_at,NEW.facts);
 RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION control.webflow_restriction_event() FROM PUBLIC;
CREATE TRIGGER webflow_restriction_event AFTER INSERT ON control.platform_events
FOR EACH ROW WHEN(NEW.event_type='webflow.binding.revoked') EXECUTE FUNCTION control.webflow_restriction_event();
CREATE POLICY platform_webflow_revocation_insert ON control.platform_events FOR INSERT TO signal_migrator WITH CHECK(
 event_type='webflow.binding.revoked' AND EXISTS(SELECT 1 FROM control.webflow_revocations WHERE event_id=platform_events.id AND target_id=platform_events.object_id AND actor_user_id=platform_events.actor_user_id));
CREATE FUNCTION control.apply_webflow_authority_denial(p_event uuid,p_target uuid,p_epoch bigint,p_stream uuid,p_position bigint,p_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'signal_authority_dispatcher' OR p_event IS NULL OR p_target IS NULL OR p_epoch<>1 OR p_stream IS NULL OR p_position<1 OR p_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'webflow_replay_denied' USING ERRCODE='42501'; END IF;
 INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,effective_epoch,stream_generation,stream_position,payload_hash)
 VALUES(p_event,'webflow_binding',p_target,'webflow_binding_revoked',1,p_stream,p_position,p_hash) ON CONFLICT(event_id) DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event AND target_kind='webflow_binding' AND target_id=p_target AND effective_epoch=1 AND stream_generation=p_stream AND stream_position=p_position AND payload_hash=p_hash) THEN RAISE EXCEPTION 'webflow_replay_conflict' USING ERRCODE='23505'; END IF;
END; $$;
REVOKE ALL ON FUNCTION control.apply_webflow_authority_denial(uuid,uuid,bigint,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_webflow_authority_denial(uuid,uuid,bigint,uuid,bigint,text) TO signal_authority_dispatcher;

ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check CHECK(egress_profile IN (
 'legacy_unqualified','crawl_page','crawl_robots','browser_read','github_rest','github_repository_write',
 'google_oauth_token','google_oauth_revoke','gsc_api','bing_oauth_token','bing_api','jev','model_json',
 'openai_model','openai_assistant','perplexity_assistant','gemini_assistant','slack_bot','slack_oauth',
 'crawl_key_file','indexnow_submit','dataforseo','ga4_admin','ga4_data','telegram_bot',
 'browser_worker_read','wordpress_rest','drive_metadata','drive_export',
 'webflow','webflow_oauth','webflow_revoke'));
ALTER FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) RENAME TO bind_before_webflow_egress_profile;
REVOKE ALL ON FUNCTION control.bind_before_webflow_egress_profile(uuid,uuid,uuid,bytea,text) FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(p_tenant uuid,p_site uuid,p_id uuid,p_hash bytea,p_profile text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE; BEGIN
 IF p_profile NOT IN ('webflow_oauth','webflow_revoke') THEN RETURN control.bind_before_webflow_egress_profile(p_tenant,p_site,p_id,p_hash,p_profile); END IF;
 PERFORM set_config('signal.tenant_id',p_tenant::text,true);PERFORM set_config('signal.site_id',p_site::text,true);
 SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_hash OR o.purpose<>'connector' OR o.method<>'POST' OR NOT o.credentialed OR o.request_bytes>8192 OR o.max_response_bytes>16384 OR o.request_url<>(CASE WHEN p_profile='webflow_oauth' THEN 'https://api.webflow.com/oauth/access_token' ELSE 'https://webflow.com/oauth/revoke_authorization' END) OR o.egress_profile NOT IN ('legacy_unqualified',p_profile) THEN RAISE EXCEPTION 'webflow_oauth_profile_denied' USING ERRCODE='22023'; END IF;
 IF o.egress_profile='legacy_unqualified' THEN UPDATE app.egress_operations SET egress_profile=p_profile WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id; END IF;RETURN 'bound';
END; $$;
REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;
CREATE FUNCTION control.bind_webflow_egress_profile(p_tenant uuid,p_site uuid,p_id uuid,p_hash bytea,p_provider_site text,p_collection text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE;valid boolean; BEGIN
 PERFORM set_config('signal.tenant_id',p_tenant::text,true);PERFORM set_config('signal.site_id',p_site::text,true);
 SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR p_provider_site !~ '^[0-9a-f]{24}$' OR p_collection !~ '^[0-9a-f]{24}$' OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_hash OR o.purpose<>'connector' OR NOT o.credentialed OR o.origin<>'https://api.webflow.com' OR o.request_bytes>65536 OR o.max_response_bytes>131072 OR o.egress_profile NOT IN ('legacy_unqualified','webflow') THEN RAISE EXCEPTION 'webflow_profile_denied' USING ERRCODE='22023'; END IF;
 IF o.method='POST' THEN
  SELECT EXISTS(SELECT 1 FROM app.webflow_operations op JOIN app.webflow_revisions r ON r.tenant_id=op.tenant_id AND r.site_id=op.site_id AND r.id=op.revision_id JOIN app.webflow_bindings b ON b.tenant_id=r.tenant_id AND b.site_id=r.site_id AND b.id=r.binding_id WHERE op.tenant_id=p_tenant AND op.site_id=p_site AND op.id=p_id AND op.state='AUTHORIZED' AND b.revoked_at IS NULL AND b.provider_site=p_provider_site AND b.collection_id=p_collection AND r.revision_sha256=o.request_body_sha256 AND o.request_url='https://api.webflow.com/v2/collections/'||p_collection||'/items/insert') INTO valid;
 ELSE
  valid:=o.method='GET' AND (o.request_url IN ('https://api.webflow.com/v2/token/introspect','https://api.webflow.com/v2/sites/'||p_provider_site||'/custom_domains','https://api.webflow.com/v2/sites/'||p_provider_site||'/collections','https://api.webflow.com/v2/collections/'||p_collection) OR o.request_url ~ ('^https://api.webflow.com/v2/collections/'||p_collection||'/items[?]slug=[a-z0-9-]{1,80}-signal-[0-9a-f]{32}&limit=2&offset=0$'));
 END IF;
 IF NOT coalesce(valid,false) THEN RAISE EXCEPTION 'webflow_profile_denied' USING ERRCODE='22023'; END IF;
 IF o.egress_profile='legacy_unqualified' THEN UPDATE app.egress_operations SET egress_profile='webflow' WHERE tenant_id=p_tenant AND site_id=p_site AND id=p_id;END IF;RETURN 'bound';
END; $$;
REVOKE ALL ON FUNCTION control.bind_webflow_egress_profile(uuid,uuid,uuid,bytea,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_webflow_egress_profile(uuid,uuid,uuid,bytea,text,text) TO signal_crawl_admission;
