CREATE TABLE app.assistant_conversations (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL,
 request_id uuid NOT NULL, title text CHECK(length(title)<=120),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,user_id,id), UNIQUE(tenant_id,site_id,user_id,request_id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id)
);
CREATE TABLE app.assistant_messages (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,user_id uuid NOT NULL,conversation_id uuid NOT NULL,
 id uuid NOT NULL,request_id uuid NOT NULL,role text NOT NULL CHECK(role IN ('owner','signal')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
 CHECK(payload ?& ARRAY['message_id','role','text','created_at','state','citations','actions','remembered']),
 CHECK(payload - ARRAY['message_id','role','text','created_at','state','citations','actions','remembered']='{}'::jsonb),
 CHECK(payload->>'message_id'=id::text AND payload->>'role'=role),
 CHECK(jsonb_typeof(payload->'text')='string' AND length(payload->>'text') BETWEEN 1 AND CASE WHEN role='owner' THEN 2000 ELSE 1200 END),
 CHECK(payload->>'state' IN ('answered','no_record','unavailable','failed','outcome_unknown')),
 CHECK(jsonb_typeof(payload->'citations')='array' AND jsonb_array_length(payload->'citations')<=4),
 CHECK(jsonb_typeof(payload->'actions')='array' AND jsonb_array_length(payload->'actions')<=3),
 CHECK(jsonb_typeof(payload->'remembered')='array' AND jsonb_array_length(payload->'remembered')<=2),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 finalized boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,site_id,user_id,id), UNIQUE(tenant_id,site_id,user_id,request_id,role),
 FOREIGN KEY(tenant_id,site_id,user_id,conversation_id) REFERENCES app.assistant_conversations(tenant_id,site_id,user_id,id)
);
CREATE TABLE app.assistant_memories (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,
 request_id uuid NOT NULL,kind text NOT NULL CHECK(kind IN ('preference','context','summary')),
 text text NOT NULL CHECK(length(text) BETWEEN 1 AND 500),
 source_conversation_id uuid,source_message_id uuid,
 search tsvector GENERATED ALWAYS AS (to_tsvector('pg_catalog.simple',text)) STORED,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,user_id,id), UNIQUE(tenant_id,site_id,user_id,request_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES app.memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 FOREIGN KEY(tenant_id,site_id,user_id,source_conversation_id) REFERENCES app.assistant_conversations(tenant_id,site_id,user_id,id),
 FOREIGN KEY(tenant_id,site_id,user_id,source_message_id) REFERENCES app.assistant_messages(tenant_id,site_id,user_id,id),
 CHECK(kind<>'summary' OR source_conversation_id IS NOT NULL)
);
CREATE INDEX assistant_memory_search ON app.assistant_memories USING gin(search);
CREATE TABLE control.assistant_retention_scopes (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,user_id uuid NOT NULL,
 next_due timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '365 days',
 PRIMARY KEY(tenant_id,site_id,user_id)
);
ALTER TABLE control.assistant_retention_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE control.assistant_retention_scopes FORCE ROW LEVEL SECURITY;
CREATE POLICY assistant_retention_owner ON control.assistant_retention_scopes TO signal_migrator USING(true) WITH CHECK(true);
REVOKE ALL ON control.assistant_retention_scopes FROM PUBLIC,signal_api,signal_identity,signal_scheduler,signal_workflow,signal_bootstrap,signal_crawl_admission,signal_crawl_ingest;
CREATE TABLE app.assistant_memory_forgettings (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,user_id uuid NOT NULL,memory_id uuid NOT NULL,
 reason text NOT NULL CHECK(reason IN ('owner','summary_refresh')),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,user_id,memory_id),
 FOREIGN KEY(tenant_id,site_id,user_id,memory_id) REFERENCES app.assistant_memories(tenant_id,site_id,user_id,id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['assistant_conversations','assistant_messages','assistant_memories','assistant_memory_forgettings'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY assistant_private ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id() AND user_id=nullif(current_setting(''signal.actor_user_id'',true),'''')::uuid) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id() AND user_id=nullif(current_setting(''signal.actor_user_id'',true),'''')::uuid)',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_workflow,signal_scheduler,signal_bootstrap,signal_crawl_ingest,signal_crawl_admission',t);
 END LOOP;
END; $$;

CREATE FUNCTION control.assistant_prune_scope() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM app.assistant_memory_forgettings f USING app.assistant_memories m WHERE f.memory_id=m.id AND (m.created_at<=transaction_timestamp()-interval '365 days' OR EXISTS(SELECT 1 FROM app.assistant_conversations c WHERE c.id=m.source_conversation_id AND c.created_at<=transaction_timestamp()-interval '365 days'));
 DELETE FROM app.assistant_memories m WHERE m.created_at<=transaction_timestamp()-interval '365 days' OR EXISTS(SELECT 1 FROM app.assistant_conversations c WHERE c.id=m.source_conversation_id AND c.created_at<=transaction_timestamp()-interval '365 days');
 DELETE FROM app.assistant_messages m USING app.assistant_conversations c WHERE m.conversation_id=c.id AND c.created_at<=transaction_timestamp()-interval '365 days';
 DELETE FROM app.assistant_conversations WHERE created_at<=transaction_timestamp()-interval '365 days';
 DELETE FROM control.assistant_retention_scopes WHERE tenant_id=app.current_tenant_id() AND site_id=app.current_site_id() AND user_id=nullif(current_setting('signal.actor_user_id',true),'')::uuid AND NOT EXISTS(SELECT 1 FROM app.assistant_conversations) AND NOT EXISTS(SELECT 1 FROM app.assistant_memories);
 UPDATE control.assistant_retention_scopes SET next_due=(SELECT min(created_at)+interval '365 days' FROM (SELECT created_at FROM app.assistant_conversations UNION ALL SELECT created_at FROM app.assistant_memories) remaining) WHERE tenant_id=app.current_tenant_id() AND site_id=app.current_site_id() AND user_id=nullif(current_setting('signal.actor_user_id',true),'')::uuid;
END; $$;
REVOKE ALL ON FUNCTION control.assistant_prune_scope() FROM PUBLIC;

CREATE FUNCTION control.assistant_scope(p_hash bytea,p_generation text,p_site uuid)
RETURNS TABLE(tenant_id uuid,user_id uuid,role_key text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; BEGIN
 IF session_user NOT IN ('signal_api','signal_identity') THEN RETURN; END IF;
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN; END IF;
 PERFORM set_config('signal.actor_user_id',a.user_id::text,true);
 IF EXISTS(SELECT 1 FROM control.assistant_retention_scopes s WHERE s.tenant_id=a.tenant_id AND s.site_id=p_site AND s.user_id=a.user_id AND s.next_due<=transaction_timestamp()) THEN
  PERFORM control.assistant_prune_scope();
 END IF;
 RETURN QUERY SELECT a.tenant_id,a.user_id,a.role_key;
END; $$;
REVOKE ALL ON FUNCTION control.assistant_scope(bytea,text,uuid) FROM PUBLIC;

-- The model's monthly account is shared with all other roles. Only this new role
-- can spend through the member-scoped ports; cap management stays owner-only.
ALTER TABLE app.model_budget_calls DROP CONSTRAINT model_budget_calls_role_check;
ALTER TABLE app.model_budget_calls ADD CONSTRAINT model_budget_calls_role_check CHECK(role IN (
 'page_type','fact_extraction','metadata_draft','claim_check','article_outline','article_draft',
 'article_critique','article_revise','report_text','topic_ideas','owner_answers'));
DO $$ DECLARE n text; d text; BEGIN
 FOREACH n IN ARRAY ARRAY['read','reserve','dispatch','finish'] LOOP
  SELECT pg_get_functiondef(p.oid) INTO d FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace
   WHERE s.nspname='control' AND p.proname='model_budget_'||n;
  d:=replace(d,'control.model_budget_','control.assistant_budget_');
  d:=replace(d,'control.business_brain_owner','control.assistant_scope');
  IF n='reserve' THEN
   d:=replace(d,'IF p_id IS NULL','IF p_role IS DISTINCT FROM ''owner_answers'' OR p_id IS NULL');
  END IF;
  IF n IN ('dispatch','finish') THEN
   d:=replace(d,'c.id=p_id','c.id=p_id AND c.role=''owner_answers''');
   d:=replace(d,'AND id=p_id;', 'AND id=p_id AND role=''owner_answers'';');
  END IF;
  IF n<>'read' THEN
   d:=replace(d,'IF v.tenant_id IS NULL THEN', 'IF NOT EXISTS(SELECT 1 FROM app.assistant_messages WHERE id=p_id AND role=''signal'' AND NOT finalized) THEN RETURN ''denied''; END IF; IF v.tenant_id IS NULL THEN');
  END IF;
  EXECUTE d;
 END LOOP;
END; $$;
REVOKE ALL ON FUNCTION control.assistant_budget_read(bytea,text,uuid),control.assistant_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint),control.assistant_budget_dispatch(bytea,text,uuid,uuid,bytea),control.assistant_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.assistant_budget_read(bytea,text,uuid),control.assistant_budget_reserve(bytea,text,uuid,uuid,bytea,text,jsonb,bigint),control.assistant_budget_dispatch(bytea,text,uuid,uuid,bytea),control.assistant_budget_finish(bytea,text,uuid,uuid,bigint,bytea,jsonb) TO signal_api,signal_identity;

CREATE FUNCTION control.assistant_store(p_hash bytea,p_generation text,p_site uuid,p_action text,p_args jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;v_conversation app.assistant_conversations%%ROWTYPE;v_message app.assistant_messages%%ROWTYPE;
 r uuid;cid uuid;mid uuid;v jsonb;lock_key bigint;memory jsonb;saved jsonb; BEGIN
 SELECT * INTO a FROM control.assistant_scope(p_hash,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN NULL; END IF;
 IF p_action='list' THEN
  RETURN jsonb_build_object('role',a.role_key,'conversations',coalesce((SELECT jsonb_agg(x.v ORDER BY x.updated_at DESC,x.id DESC) FROM (
   SELECT c.id,c.updated_at,jsonb_build_object('conversation_id',c.id,'title',c.title,'updated_at',c.updated_at,
    'message_count',(SELECT count(*) FROM app.assistant_messages m WHERE m.conversation_id=c.id)) v
   FROM app.assistant_conversations c WHERE c.created_at>transaction_timestamp()-interval '365 days'
   ORDER BY c.updated_at DESC,c.id DESC LIMIT 20) x),'[]'::jsonb));
 END IF;
 IF p_action IN ('create','begin','add_memory') THEN r:=(p_args->>'request_id')::uuid; END IF;
 IF p_action='create' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(a.user_id::text||p_site::text,162));
  SELECT * INTO v_conversation FROM app.assistant_conversations WHERE request_id=r;
  IF FOUND THEN RETURN jsonb_build_object('conversation_id',v_conversation.id,'created_at',v_conversation.created_at); END IF;
  IF (SELECT count(*) FROM app.assistant_conversations WHERE created_at>transaction_timestamp()-interval '365 days')>=100 THEN RETURN '{"state":"limit"}'; END IF;
  INSERT INTO app.assistant_conversations(tenant_id,site_id,user_id,id,request_id) VALUES(a.tenant_id,p_site,a.user_id,gen_random_uuid(),r) RETURNING * INTO v_conversation;
  INSERT INTO control.assistant_retention_scopes(tenant_id,site_id,user_id) VALUES(a.tenant_id,p_site,a.user_id) ON CONFLICT DO NOTHING;
  RETURN jsonb_build_object('conversation_id',v_conversation.id,'created_at',v_conversation.created_at);
 END IF;
 IF p_action IN ('read','begin','finish','retrieve','summary') THEN
  cid:=(p_args->>'conversation_id')::uuid;
  SELECT * INTO v_conversation FROM app.assistant_conversations WHERE id=cid AND created_at>transaction_timestamp()-interval '365 days' FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
 END IF;
 IF p_action='read' THEN
  RETURN jsonb_build_object('conversation_id',v_conversation.id,'title',v_conversation.title,'messages',coalesce((SELECT jsonb_agg(payload ORDER BY created_at,CASE role WHEN 'owner' THEN 0 ELSE 1 END,id) FROM app.assistant_messages WHERE conversation_id=cid),'[]'::jsonb));
 END IF;
 IF p_action='begin' THEN
  lock_key:=hashtextextended(a.tenant_id::text||p_site::text||a.user_id::text||r::text,162);
  SELECT * INTO v_message FROM app.assistant_messages WHERE request_id=r AND role='owner';
  IF FOUND THEN
   IF v_message.conversation_id<>cid OR v_message.payload->>'text' IS DISTINCT FROM p_args->>'text' THEN RETURN '{"state":"conflict"}'; END IF;
   IF EXISTS(SELECT 1 FROM app.assistant_messages WHERE request_id=r AND role='signal' AND NOT finalized) THEN
    IF NOT pg_try_advisory_lock(lock_key) THEN RETURN '{"state":"conflict"}'; END IF;
    -- No live connection owns this request: seal the crash outcome, never retry.
    UPDATE app.assistant_messages SET finalized=true WHERE request_id=r AND role='signal';
    PERFORM pg_advisory_unlock(lock_key);
   END IF;
   RETURN jsonb_build_object('state','replayed','owner_message',v_message.payload,'reply',(SELECT payload FROM app.assistant_messages WHERE request_id=r AND role='signal'));
  END IF;
  IF length(p_args->>'text') NOT BETWEEN 1 AND 2000 OR length(btrim(p_args->>'text'))=0 THEN RETURN '{"state":"invalid"}'; END IF;
 END IF;
 IF p_action='begin' THEN
  IF (SELECT count(*) FROM app.assistant_messages WHERE conversation_id=cid)>=200 OR
   (SELECT coalesce(sum(octet_length(payload::text)),0) FROM app.assistant_messages WHERE conversation_id=cid)>160000 THEN RETURN '{"state":"limit"}'; END IF;
  r:=(p_args->>'request_id')::uuid;
  IF NOT pg_try_advisory_lock(lock_key) THEN RETURN '{"state":"conflict"}'; END IF;
  mid:=gen_random_uuid();
  v:=jsonb_build_object('message_id',mid,'role','owner','text',p_args->>'text','created_at',transaction_timestamp(),'state','answered','citations','[]'::jsonb,'actions','[]'::jsonb,'remembered','[]'::jsonb);
  INSERT INTO app.assistant_messages VALUES(a.tenant_id,p_site,a.user_id,cid,mid,r,'owner',v,transaction_timestamp(),true);
  mid:=gen_random_uuid();
  v:=jsonb_build_object('message_id',mid,'role','signal','text','The answer outcome is unknown. This request will not be sent again.','created_at',transaction_timestamp(),'state','outcome_unknown','citations','[]'::jsonb,'actions','[]'::jsonb,'remembered','[]'::jsonb);
  INSERT INTO app.assistant_messages VALUES(a.tenant_id,p_site,a.user_id,cid,mid,r,'signal',v,transaction_timestamp(),false);
  UPDATE app.assistant_conversations SET title=coalesce(title,left(p_args->>'text',120)),updated_at=transaction_timestamp() WHERE id=cid;
  RETURN jsonb_build_object('state','started','owner_message',(SELECT payload FROM app.assistant_messages WHERE request_id=r AND role='owner'),'reply',v,'role',a.role_key);
 END IF;
 IF p_action='finish' THEN
  r:=(p_args->>'request_id')::uuid;
  SELECT * INTO v_message FROM app.assistant_messages WHERE request_id=r AND conversation_id=cid AND role='signal' FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF NOT v_message.finalized THEN
   v:=(p_args->'reply')||jsonb_build_object('message_id',v_message.id,'role','signal','created_at',v_message.created_at);
   PERFORM 1 FROM app.assistant_memories WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(coalesce(p_args->'memory_ids','[]'::jsonb))) ORDER BY id FOR UPDATE;
   IF EXISTS(SELECT 1 FROM app.assistant_memory_forgettings WHERE reason='owner' AND memory_id IN (SELECT value::uuid FROM jsonb_array_elements_text(coalesce(p_args->'memory_ids','[]'::jsonb)))) THEN
    v:=v_message.payload||jsonb_build_object('text','Memory was forgotten while answering. Please ask again with a new request.','state','failed');
   END IF;
   IF (SELECT coalesce(sum(octet_length(payload::text)),0) FROM app.assistant_messages WHERE conversation_id=cid)-octet_length(v_message.payload::text)+octet_length(v::text)>180000 THEN
    v:=v_message.payload||jsonb_build_object('text','The conversation has reached its storage limit. Start a new conversation.','state','failed');
   END IF;
   IF v->>'state'='answered' AND jsonb_array_length(coalesce(p_args->'memories','[]'::jsonb))>0 THEN
    IF a.role_key<>'owner' OR jsonb_array_length(p_args->'memories')>2 THEN RETURN '{"state":"invalid"}'; END IF;
    IF (SELECT count(*) FROM app.assistant_memories WHERE created_at>transaction_timestamp()-interval '365 days')+jsonb_array_length(p_args->'memories')>2000 THEN
     v:=v_message.payload||jsonb_build_object('text','The memory limit was reached. Nothing new was remembered.','state','failed');
    ELSE
     FOR memory IN SELECT value FROM jsonb_array_elements(p_args->'memories') LOOP
      IF memory->>'kind' NOT IN ('preference','context') OR length(memory->>'text') NOT BETWEEN 1 AND 500 OR NOT EXISTS(SELECT 1 FROM app.assistant_messages WHERE request_id=r AND role='owner' AND strpos(payload->>'text',memory->>'text')>0) THEN RAISE check_violation; END IF;
      saved:=control.assistant_store(p_hash,p_generation,p_site,'add_memory',memory||jsonb_build_object('conversation_id',cid,'message_id',(SELECT id FROM app.assistant_messages WHERE request_id=r AND role='owner')));
      IF saved->>'memory_id' IS NULL THEN RAISE check_violation; END IF;
      v:=jsonb_set(v,'{remembered}',(v->'remembered')||jsonb_build_array(jsonb_build_object('memory_id',saved->'memory_id','text',saved->'text')));
     END LOOP;
    END IF;
   END IF;
   IF (SELECT coalesce(sum(octet_length(payload::text)),0) FROM app.assistant_messages WHERE conversation_id=cid)-octet_length(v_message.payload::text)+octet_length(v::text)>180000 THEN RAISE check_violation; END IF;
   UPDATE app.assistant_messages SET payload=v,finalized=true WHERE id=v_message.id RETURNING * INTO v_message;
   PERFORM pg_advisory_unlock(hashtextextended(a.tenant_id::text||p_site::text||a.user_id::text||r::text,162));
  END IF;
  UPDATE app.assistant_conversations SET updated_at=transaction_timestamp() WHERE id=cid;
  RETURN jsonb_build_object('owner_message',(SELECT payload FROM app.assistant_messages WHERE request_id=r AND role='owner'),'reply',v_message.payload);
 END IF;
 IF p_action IN ('memory','retrieve') THEN
  SELECT coalesce(jsonb_agg(x.v ORDER BY x.rank DESC,x.created_at DESC,x.id DESC),'[]'::jsonb) INTO v FROM (
   SELECT m.id,m.created_at,CASE WHEN p_action='retrieve' THEN ts_rank_cd(m.search,plainto_tsquery('pg_catalog.simple',coalesce(p_args->>'text','')))+0.05/(1+extract(epoch FROM transaction_timestamp()-m.created_at)/86400) ELSE 0 END rank,
   jsonb_build_object('memory_id',m.id,'kind',m.kind,'text',m.text,'created_at',m.created_at,'source_conversation_id',m.source_conversation_id,'source_message_id',m.source_message_id) v
   FROM app.assistant_memories m WHERE m.created_at>transaction_timestamp()-interval '365 days'
    AND NOT EXISTS(SELECT 1 FROM app.assistant_memory_forgettings f WHERE f.memory_id=m.id)
    AND (p_action='memory' OR m.kind<>'summary')
   ORDER BY rank DESC,m.created_at DESC,m.id DESC LIMIT CASE WHEN p_action='memory' THEN 200 ELSE 10 END) x;
  IF p_action='memory' THEN RETURN jsonb_build_object('memories',v); END IF;
  RETURN jsonb_build_object('memories',v,'summary',(SELECT jsonb_build_object('memory_id',m.id,'text',m.text) FROM app.assistant_memories m WHERE m.kind='summary' AND m.source_conversation_id=cid AND NOT EXISTS(SELECT 1 FROM app.assistant_memory_forgettings f WHERE f.memory_id=m.id) ORDER BY created_at DESC LIMIT 1),
   'forgotten',coalesce((SELECT jsonb_agg(m.text) FROM app.assistant_memories m JOIN app.assistant_memory_forgettings f ON f.memory_id=m.id WHERE f.reason='owner'),'[]'::jsonb),
   'message_count',(SELECT count(*) FROM app.assistant_messages WHERE conversation_id=cid),
   'turns',coalesce((SELECT jsonb_agg(x.payload ORDER BY x.created_at,CASE x.role WHEN 'owner' THEN 0 ELSE 1 END) FROM (SELECT * FROM app.assistant_messages WHERE conversation_id=cid ORDER BY created_at DESC,CASE role WHEN 'signal' THEN 0 ELSE 1 END,id DESC LIMIT 24) x),'[]'::jsonb));
 END IF;
 IF p_action IN ('add_memory','summary') THEN
  IF a.role_key<>'owner' AND p_action='add_memory' THEN RETURN NULL; END IF;
  IF length(p_args->>'text') NOT BETWEEN 1 AND 500 THEN RETURN '{"state":"invalid"}'; END IF;
  IF p_args->>'message_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM app.assistant_messages WHERE id=(p_args->>'message_id')::uuid AND conversation_id=(p_args->>'conversation_id')::uuid AND role='owner') THEN RETURN '{"state":"invalid"}'; END IF;
  IF (SELECT count(*) FROM app.assistant_memories WHERE created_at>transaction_timestamp()-interval '365 days')>=2000 THEN RETURN '{"state":"limit"}'; END IF;
  IF p_action='summary' THEN
   INSERT INTO app.assistant_memory_forgettings SELECT tenant_id,site_id,user_id,id,'summary_refresh',transaction_timestamp() FROM app.assistant_memories m WHERE kind='summary' AND source_conversation_id=cid ON CONFLICT DO NOTHING;
   r:=gen_random_uuid();
  END IF;
  INSERT INTO app.assistant_memories(tenant_id,site_id,user_id,id,request_id,kind,text,source_conversation_id,source_message_id)
   VALUES(a.tenant_id,p_site,a.user_id,gen_random_uuid(),r,CASE WHEN p_action='summary' THEN 'summary' ELSE p_args->>'kind' END,p_args->>'text',(p_args->>'conversation_id')::uuid,(p_args->>'message_id')::uuid)
   ON CONFLICT(tenant_id,site_id,user_id,request_id) DO NOTHING;
  INSERT INTO control.assistant_retention_scopes(tenant_id,site_id,user_id) VALUES(a.tenant_id,p_site,a.user_id) ON CONFLICT DO NOTHING;
  SELECT jsonb_build_object('memory_id',id,'kind',kind,'text',text,'created_at',created_at,'source_conversation_id',source_conversation_id,'source_message_id',source_message_id) INTO v FROM app.assistant_memories WHERE request_id=r;
  IF v->>'text' IS DISTINCT FROM p_args->>'text' OR v->>'kind' IS DISTINCT FROM (CASE WHEN p_action='summary' THEN 'summary' ELSE p_args->>'kind' END) THEN RETURN '{"state":"conflict"}'; END IF;
  RETURN v;
 END IF;
 IF p_action='forget' THEN
  IF a.role_key<>'owner' THEN RETURN NULL; END IF;
  mid:=(p_args->>'memory_id')::uuid;
  PERFORM 1 FROM app.assistant_memories WHERE id=mid FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  INSERT INTO app.assistant_memory_forgettings VALUES(a.tenant_id,p_site,a.user_id,mid,'owner',transaction_timestamp()) ON CONFLICT DO NOTHING;
  RETURN '{"state":"forgotten"}';
 END IF;
 RETURN '{"state":"invalid"}';
END; $$;
REVOKE ALL ON FUNCTION control.assistant_store(bytea,text,uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.assistant_store(bytea,text,uuid,text,jsonb) TO signal_api,signal_identity;

CREATE FUNCTION control.assistant_purge() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;n integer:=0; BEGIN
 IF session_user<>'signal_scheduler' THEN RETURN NULL; END IF;
 -- Only the migrator can enumerate private expired scopes. Runtime roles have
 -- neither table privileges nor a bypass-RLS capability.
 FOR a IN SELECT * FROM control.assistant_retention_scopes WHERE next_due<=transaction_timestamp() ORDER BY next_due,tenant_id,site_id,user_id LIMIT 100 FOR UPDATE SKIP LOCKED LOOP
  PERFORM set_config('signal.tenant_id',a.tenant_id::text,true);
  PERFORM set_config('signal.site_id',a.site_id::text,true);
  PERFORM set_config('signal.actor_user_id',a.user_id::text,true);
  PERFORM control.assistant_prune_scope();
  n:=n+1;
 END LOOP;
 RETURN n;
END; $$;
REVOKE ALL ON FUNCTION control.assistant_purge() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.assistant_purge() TO signal_scheduler;

CREATE FUNCTION control.assistant_records(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record;v jsonb; BEGIN
 SELECT * INTO a FROM control.assistant_scope(p_hash,p_generation,p_site);
 IF a.tenant_id IS NULL THEN RETURN NULL; END IF;
 WITH latest AS (SELECT * FROM app.weekly_cycles WHERE tenant_id=a.tenant_id AND site_id=p_site ORDER BY week_start DESC LIMIT 1),
 snapshot AS (SELECT * FROM app.seo_strategy_snapshots WHERE tenant_id=a.tenant_id AND site_id=p_site ORDER BY version DESC LIMIT 1),
 records AS (
  SELECT 'weekly_report' kind,id::text id,'Weekly report' label,'/changes' href,
   jsonb_build_object('week',week_start,'status',status,'reason',stop_reason,'stages',coalesce((SELECT jsonb_agg(jsonb_build_object('stage',s.stage,'outcome',s.outcome,'reason',s.detail_code)) FROM app.weekly_stage_results s WHERE s.tenant_id=a.tenant_id AND s.site_id=p_site AND s.week_start=latest.week_start),'[]'::jsonb)) data,started_at recorded_at FROM latest
  UNION ALL SELECT 'revision',r.id::text,'Site change','/approvals?revision='||r.id,
   jsonb_build_object('title',coalesce(convert_from(r.canonical_manifest,'UTF8')::jsonb#>>'{evidence,finding,title}',convert_from(r.canonical_manifest,'UTF8')::jsonb#>>'{evidence,finding,code}','Site change'),'summary',convert_from(r.canonical_manifest,'UTF8')::jsonb->'expected_impact','page',convert_from(r.canonical_manifest,'UTF8')::jsonb#>'{evidence,page_url}','status',CASE WHEN d.decision IS NOT NULL THEN d.decision WHEN EXISTS(SELECT 1 FROM app.candidate_recipe_revisions n WHERE n.tenant_id=r.tenant_id AND n.site_id=r.site_id AND n.audit_report_id=r.audit_report_id AND n.finding_id=r.finding_id AND (n.sealed_at,n.id)>(r.sealed_at,r.id)) THEN 'superseded' WHEN NOT EXISTS(SELECT 1 FROM app.github_pr_extensions e JOIN app.github_read_bindings b ON b.tenant_id=e.tenant_id AND b.site_id=e.site_id AND b.id=e.binding_id AND b.status='active' WHERE e.tenant_id=r.tenant_id AND e.site_id=r.site_id AND e.id=r.extension_id AND e.status='observed' AND e.base_sha=r.base_sha) THEN 'stale_base' ELSE 'pending' END),r.sealed_at
   FROM app.candidate_recipe_revisions r LEFT JOIN app.candidate_recipe_review_decisions d ON d.tenant_id=r.tenant_id AND d.site_id=r.site_id AND d.candidate_revision_id=r.id WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site
  UNION ALL SELECT 'article',c.id::text,'Article','/approvals?article='||c.id,
   jsonb_build_object('title',coalesce(d.payload#>>'{article,title,text}','Article'),'summary',c.manifest->'expected_impact','page',c.manifest#>'{changed_files,0,path}','status',coalesce(r.decision,'pending')),c.created_at
   FROM app.content_candidates c JOIN app.content_draft_results d ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.draft_id=c.draft_id LEFT JOIN app.content_candidate_reviews r ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.candidate_id=c.id WHERE c.tenant_id=a.tenant_id AND c.site_id=p_site
  UNION ALL SELECT 'operation',id::text,'Pull request','/changes',jsonb_build_object('state',state,'revision',candidate_revision_id),created_at FROM app.github_pr_operations WHERE tenant_id=a.tenant_id AND site_id=p_site
  UNION ALL SELECT 'observation',attempt_id::text,'Delivery observation','/changes',jsonb_build_object('outcome',convert_from(canonical_receipt,'UTF8')::jsonb->'outcome','page',convert_from(canonical_receipt,'UTF8')::jsonb->'live_url'),recorded_at FROM app.github_delivery_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site
  UNION ALL SELECT 'measurement',o.operation_id::text||':'||o.horizon||':'||o.sequence_number,'Measured change','/changes',jsonb_build_object('horizon',o.horizon,'page',p.page_url,'observation',o.document),o.recorded_at FROM app.change_measurement_observations o JOIN app.change_measurement_plans p ON p.tenant_id=o.tenant_id AND p.site_id=o.site_id AND p.operation_id=o.operation_id AND p.horizon=o.horizon WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site
  UNION ALL SELECT 'strategy_item',x->>'id','Strategy item','/strategy',jsonb_build_object('title',x->'title','phase',x->'phase','priority',x->'priority','unavailable_reason',x->'unavailable_reason'),created_at FROM snapshot CROSS JOIN LATERAL jsonb_array_elements(payload#>'{strategy,items}') x
  UNION ALL SELECT 'topic',x->>'id','Keyword topic','/strategy',jsonb_build_object('title',x->'title','metrics',x->'metrics','gaps',x->'gaps'),created_at FROM snapshot CROSS JOIN LATERAL jsonb_array_elements(payload#>'{topics,clusters}') x
  UNION ALL SELECT 'visibility_question',o.id::text,'AI visibility','/visibility',jsonb_build_object('question',q.question,'status',o.status,'provider',o.provider,'cited_pages',o.cited_pages),o.observed_at FROM app.ai_visibility_observations o JOIN app.ai_visibility_questions q ON q.tenant_id=o.tenant_id AND q.site_id=o.site_id AND q.id=o.question_id WHERE o.tenant_id=a.tenant_id AND o.site_id=p_site
  UNION ALL SELECT 'health_check',id::text,'Health check','/settings',jsonb_build_object('check',check_key,'state',state,'reason',reason),checked_at FROM app.health_observations WHERE tenant_id=a.tenant_id AND site_id=p_site
  UNION ALL SELECT 'fact',f.id::text,'Approved business fact','/business-brain',jsonb_build_object('statement',f.statement,'category',f.category,'status','approved'),f.created_at FROM app.business_brain_facts f WHERE f.tenant_id=a.tenant_id AND f.site_id=p_site
   AND (f.initial_status='approved' OR EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='approved'))
   AND NOT EXISTS(SELECT 1 FROM app.business_brain_fact_events e WHERE e.tenant_id=f.tenant_id AND e.site_id=f.site_id AND e.fact_id=f.id AND e.event_type='removed')
   AND NOT EXISTS(SELECT 1 FROM app.business_brain_facts n WHERE n.tenant_id=f.tenant_id AND n.site_id=f.site_id AND n.supersedes_id=f.id)
 ), ranked AS (SELECT *,row_number() OVER(PARTITION BY kind ORDER BY recorded_at DESC,id DESC) rn FROM records WHERE octet_length(data::text)<=4096)
 SELECT coalesce(jsonb_agg(jsonb_build_object('kind',kind,'id',id,'label',label,'href',href,'data',data) ORDER BY kind,rn),'[]'::jsonb) INTO v FROM ranked WHERE rn<=10;
 RETURN v;
END; $$;
REVOKE ALL ON FUNCTION control.assistant_records(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.assistant_records(bytea,text,uuid) TO signal_api,signal_identity;

CREATE TRIGGER assistant_memory_immutable BEFORE UPDATE ON app.assistant_memories FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER assistant_forgetting_immutable BEFORE UPDATE ON app.assistant_memory_forgettings FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
