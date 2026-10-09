CREATE TABLE app.wordpress_bindings (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,origin text NOT NULL,
 user_id bigint NOT NULL CHECK(user_id>0),observed jsonb NOT NULL,
 origin_generation bigint NOT NULL,recovery_generation text NOT NULL,created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),UNIQUE(id),
 FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 CHECK(octet_length(observed::text)<=8192)
);
CREATE TABLE app.wordpress_candidates (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,binding_id uuid NOT NULL,draft_id uuid NOT NULL,
 payload jsonb NOT NULL,canonical bytea NOT NULL,revision_sha256 bytea NOT NULL CHECK(octet_length(revision_sha256)=32),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),UNIQUE(tenant_id,site_id,binding_id,draft_id),
 FOREIGN KEY(tenant_id,site_id,binding_id) REFERENCES app.wordpress_bindings(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,draft_id) REFERENCES app.content_draft_results(tenant_id,site_id,draft_id),
 CHECK(payload ?& ARRAY['status','title','content','excerpt'] AND payload->>'status'='draft' AND payload-'status'-'title'-'content'-'excerpt'='{}'::jsonb
  AND jsonb_typeof(payload->'title')='string' AND jsonb_typeof(payload->'content')='string'
  AND jsonb_typeof(payload->'excerpt')='string' AND octet_length(canonical)<=60000
  AND sha256(canonical)=revision_sha256 AND convert_from(canonical,'UTF8')::jsonb=payload)
);
CREATE TABLE app.wordpress_reviews (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,candidate_id uuid NOT NULL,
 decision text NOT NULL CHECK(decision IN ('approved','rejected')),actor_id uuid NOT NULL,membership_epoch bigint NOT NULL,site_epoch bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),PRIMARY KEY(tenant_id,site_id,candidate_id),
 FOREIGN KEY(tenant_id,site_id,candidate_id) REFERENCES app.wordpress_candidates(tenant_id,site_id,id)
);
CREATE TABLE app.wordpress_intents (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL,candidate_id uuid NOT NULL,
 marker text NOT NULL,prior_intent_id uuid,actor_id uuid NOT NULL,recovery_generation text NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','dispatching','retry','recorded','outcome_unknown','escalated','failed')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 2),
 journal_receipt jsonb,dispatch_session_hash bytea CHECK(dispatch_session_hash IS NULL OR octet_length(dispatch_session_hash)=32),post_id bigint,post_url text,content_sha256 text,
 reconcile_count integer NOT NULL DEFAULT 0 CHECK(reconcile_count BETWEEN 0 AND 12),
 next_read_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),UNIQUE(id),UNIQUE(tenant_id,site_id,marker),
 FOREIGN KEY(tenant_id,site_id,candidate_id) REFERENCES app.wordpress_candidates(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,prior_intent_id) REFERENCES app.wordpress_intents(tenant_id,site_id,id),
 CHECK(marker='signal-s'||replace(id::text,'-',''))
);
CREATE TABLE app.wordpress_receipts (
 tenant_id uuid NOT NULL,site_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),
 intent_id uuid,subject_id uuid NOT NULL,outcome text NOT NULL CHECK(outcome IN
 ('bound','sealed','approved','rejected','queued','dispatching','not_transmitted','unapplied_rejection',
 'recorded','outcome_unknown','escalated','failed','edited_before_publish','published_unverified','verified','observation_unavailable','revoked')),
 message_sha256 bytea,post_id bigint,observed_sha256 text,created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant_id,site_id,id),FOREIGN KEY(tenant_id,site_id) REFERENCES app.sites(tenant_id,id),
 FOREIGN KEY(tenant_id,site_id,intent_id) REFERENCES app.wordpress_intents(tenant_id,site_id,id),
 CHECK(message_sha256 IS NULL OR octet_length(message_sha256)=32)
);
CREATE TABLE control.wordpress_revocations (
 binding_id uuid PRIMARY KEY,event_id uuid NOT NULL UNIQUE,actor_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
REVOKE ALL ON control.wordpress_revocations FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,
 signal_workflow,signal_scheduler,signal_crawl_admission,signal_crawl_ingest;
CREATE TRIGGER wordpress_revocations_immutable BEFORE UPDATE OR DELETE ON control.wordpress_revocations
 FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['wordpress_bindings','wordpress_candidates','wordpress_reviews','wordpress_intents','wordpress_receipts'] LOOP
  EXECUTE format('ALTER TABLE app.%%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY wordpress_scope ON app.%%I USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id()) WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())',t);
  EXECUTE format('REVOKE ALL ON app.%%I FROM PUBLIC,signal_api,signal_identity,signal_bootstrap,signal_workflow,signal_scheduler,signal_crawl_admission,signal_crawl_ingest',t);
  IF t<>'wordpress_intents' THEN
   EXECUTE format('CREATE TRIGGER wordpress_immutable BEFORE UPDATE OR DELETE ON app.%%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
  END IF;
 END LOOP;
END $$;

CREATE FUNCTION control.wordpress_origin(p_hash bytea,p_generation text,p_site uuid,p_origin text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 RETURN v.tenant_id IS NOT NULL AND EXISTS(SELECT 1 FROM control.public_origin_claims o
  WHERE o.tenant_id=v.tenant_id AND o.site_id=p_site AND o.origin=p_origin AND o.recheck_at>transaction_timestamp());
END $$;
CREATE FUNCTION control.wordpress_secret_scope(p_hash bytea,p_generation text,p_site uuid,p_origin text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL OR NOT control.wordpress_origin(p_hash,p_generation,p_site,p_origin) THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('tenant_id',v.tenant_id,'site_id',p_site,'origin',p_origin);
END $$;
CREATE FUNCTION control.wordpress_bind(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_origin text,p_user bigint,p_observed jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; g bigint; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF NOT control.wordpress_origin(p_hash,p_generation,p_site,p_origin) OR p_user IS NULL OR p_user<1
  OR p_observed->>'id' IS DISTINCT FROM p_user::text
  OR NOT control.wordpress_user_valid(p_observed) THEN RETURN 'denied'; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR UPDATE;
 SELECT claim_generation INTO g FROM control.public_origin_claims WHERE origin=p_origin;
 IF EXISTS(SELECT 1 FROM app.wordpress_bindings b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site
  AND NOT EXISTS(SELECT 1 FROM control.wordpress_revocations r WHERE r.binding_id=b.id)
  AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='wordpress_binding' AND d.target_id=b.id)) THEN RETURN 'already_bound'; END IF;
 INSERT INTO app.wordpress_bindings(tenant_id,site_id,id,origin,user_id,observed,origin_generation,recovery_generation,created_by)
 VALUES(v.tenant_id,p_site,p_id,p_origin,p_user,p_observed,g,p_generation,v.user_id);
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,subject_id,outcome) VALUES(v.tenant_id,p_site,p_id,'bound');
 RETURN 'bound'; END $$;

CREATE FUNCTION control.wordpress_user_valid(p_observed jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT coalesce(jsonb_typeof(p_observed->'roles')='array' AND jsonb_array_length(p_observed->'roles')=1
 AND p_observed->'roles'->>0 NOT IN ('administrator','editor')
 AND p_observed->'roles'->>0 ~ '^[a-z][a-z0-9_]{0,63}$'
 AND p_observed->'roles'->>0 !~ '(plugin|theme|user|option|delete_others|manage_|edit_others)'
 AND jsonb_typeof(p_observed->'capabilities')='object'
 AND p_observed->'capabilities'->'read'='true'::jsonb AND p_observed->'capabilities'->'edit_posts'='true'::jsonb
 AND NOT EXISTS(SELECT 1 FROM jsonb_each(p_observed->'capabilities') c WHERE jsonb_typeof(c.value)<>'boolean'
 OR (c.value='true'::jsonb AND c.key NOT IN ('read','edit_posts','edit_published_posts','publish_posts','upload_files',
 'delete_posts','delete_published_posts','level_0','level_1','level_2','author',p_observed->'roles'->>0))),false)
$$;
CREATE FUNCTION control.wordpress_binding_current(p_id uuid,p_generation text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.wordpress_bindings b JOIN control.public_origin_claims o
 ON o.origin=b.origin AND o.tenant_id=b.tenant_id AND o.site_id=b.site_id
 WHERE b.id=p_id AND b.recovery_generation=p_generation AND b.origin_generation=o.claim_generation
 AND o.recheck_at>transaction_timestamp()
 AND NOT EXISTS(SELECT 1 FROM control.wordpress_revocations r WHERE r.binding_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones d WHERE d.target_kind='wordpress_binding' AND d.target_id=b.id))
$$;
CREATE FUNCTION control.wordpress_read(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('bindings',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM
 (SELECT b.id,b.origin,b.user_id,b.observed,control.wordpress_binding_current(b.id,p_generation) AS current
 FROM app.wordpress_bindings b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site ORDER BY b.created_at DESC LIMIT 20)x),'[]'::jsonb),
 'candidates',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT c.id,c.draft_id,c.binding_id,c.payload,
 encode(c.revision_sha256,'hex') AS revision_sha256,coalesce(r.decision,'pending') AS decision
 FROM app.wordpress_candidates c LEFT JOIN app.wordpress_reviews r ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.candidate_id=c.id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site
 ORDER BY c.created_at DESC LIMIT 100)x),'[]'::jsonb),
 'intents',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT i.id,i.candidate_id,i.marker,i.state,i.post_id,i.post_url,
 i.reconcile_count,i.next_read_at,i.prior_intent_id,(SELECT r.outcome FROM app.wordpress_receipts r
 WHERE r.tenant_id=i.tenant_id AND r.site_id=i.site_id AND r.intent_id=i.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1) AS observation
 FROM app.wordpress_intents i WHERE i.tenant_id=v.tenant_id AND i.site_id=p_site ORDER BY i.created_at DESC LIMIT 100)x),'[]'::jsonb));
END $$;
CREATE FUNCTION control.wordpress_dispatch_current(p_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.wordpress_intents i
 JOIN app.wordpress_reviews r ON r.tenant_id=i.tenant_id AND r.site_id=i.site_id AND r.candidate_id=i.candidate_id
 JOIN app.memberships m ON m.tenant_id=r.tenant_id AND m.user_id=r.actor_id
 JOIN app.site_memberships sm ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id AND sm.site_id=i.site_id
 CROSS JOIN LATERAL control.resolve_snapshot_authority(i.dispatch_session_hash,i.site_id,i.recovery_generation) a
 WHERE i.id=p_id AND i.state='dispatching' AND r.decision='approved'
 AND m.state='active' AND m.role_key='owner' AND m.authorization_epoch=r.membership_epoch
 AND sm.state='active' AND sm.authorization_epoch=r.site_epoch
 AND a.outcome='authorized' AND a.role_key='owner' AND a.user_id=i.actor_id
 AND NOT EXISTS(SELECT 1 FROM app.site_weekly_control w WHERE w.tenant_id=i.tenant_id AND w.site_id=i.site_id AND w.paused))
$$;
CREATE FUNCTION control.wordpress_seal(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_binding uuid,p_draft uuid,p_canonical bytea,p_digest bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; payload jsonb; r app.content_draft_results%%ROWTYPE; existing uuid; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL OR NOT EXISTS(SELECT 1 FROM app.wordpress_bindings b WHERE b.id=p_binding AND b.tenant_id=v.tenant_id AND b.site_id=p_site)
 OR NOT control.wordpress_binding_current(p_binding,p_generation) THEN RETURN jsonb_build_object('state','denied'); END IF;
 SELECT * INTO r FROM app.content_draft_results WHERE tenant_id=v.tenant_id AND site_id=p_site AND draft_id=p_draft;
 IF NOT FOUND OR r.payload->>'state' NOT IN ('grounded','owner_required') OR r.payload->'originality'->>'state' IS DISTINCT FROM 'original'
 OR NOT EXISTS(SELECT 1 FROM app.content_draft_intents d JOIN app.content_briefs b ON b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.id=d.brief_id
 WHERE d.tenant_id=v.tenant_id AND d.site_id=p_site AND d.id=p_draft AND b.payload->>'kind'='new_article'
 AND EXISTS(SELECT 1 FROM app.content_brief_acceptances a WHERE a.tenant_id=b.tenant_id AND a.site_id=b.site_id AND a.brief_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM app.content_briefs n WHERE n.tenant_id=b.tenant_id AND n.site_id=b.site_id AND n.supersedes_id=b.id)) THEN RETURN jsonb_build_object('state','draft_unavailable'); END IF;
 payload:=convert_from(p_canonical,'UTF8')::jsonb;
 IF sha256(p_canonical) IS DISTINCT FROM p_digest OR payload->>'title' IS DISTINCT FROM r.payload->'article'->'title'->>'text'
 OR payload->>'excerpt' IS DISTINCT FROM r.payload->'article'->'meta_description'->>'text' THEN RETURN jsonb_build_object('state','invalid'); END IF;
 SELECT id INTO existing FROM app.wordpress_candidates WHERE tenant_id=v.tenant_id AND site_id=p_site AND binding_id=p_binding AND draft_id=p_draft;
 IF existing IS NOT NULL THEN RETURN jsonb_build_object('state','replayed','candidate_id',existing); END IF;
 INSERT INTO app.wordpress_candidates(tenant_id,site_id,id,binding_id,draft_id,payload,canonical,revision_sha256)
 VALUES(v.tenant_id,p_site,p_id,p_binding,p_draft,payload,p_canonical,p_digest);
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,subject_id,outcome,message_sha256) VALUES(v.tenant_id,p_site,p_id,'sealed',p_digest);
 RETURN jsonb_build_object('state','sealed','candidate_id',p_id);
END $$;
CREATE FUNCTION control.wordpress_review(p_hash bytea,p_generation text,p_site uuid,p_candidate uuid,p_digest bytea,p_decision text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; e bigint; se bigint; prior text; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 IF p_decision IS NULL OR p_decision NOT IN ('approved','rejected') OR NOT EXISTS(SELECT 1 FROM app.wordpress_candidates c
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_candidate AND c.revision_sha256=p_digest
 AND control.wordpress_binding_current(c.binding_id,p_generation)) THEN RETURN 'stale'; END IF;
 SELECT decision INTO prior FROM app.wordpress_reviews WHERE tenant_id=v.tenant_id AND site_id=p_site AND candidate_id=p_candidate;
 IF prior IS NOT NULL THEN RETURN CASE WHEN prior=p_decision THEN 'replayed' ELSE 'conflict' END; END IF;
 SELECT authorization_epoch INTO e FROM app.memberships WHERE tenant_id=v.tenant_id AND id=v.membership_id;
 SELECT authorization_epoch INTO se FROM app.site_memberships WHERE tenant_id=v.tenant_id AND site_id=p_site AND user_id=v.user_id;
 INSERT INTO app.wordpress_reviews VALUES(v.tenant_id,p_site,p_candidate,p_decision,v.user_id,e,se,transaction_timestamp());
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,subject_id,outcome,message_sha256) VALUES(v.tenant_id,p_site,p_candidate,p_decision,p_digest);
 RETURN 'reviewed'; END $$;
CREATE FUNCTION control.wordpress_queue(p_hash bytea,p_generation text,p_site uuid,p_candidate uuid,p_id uuid,p_prior uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; existing uuid; n integer; cap integer; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 PERFORM 1 FROM app.sites WHERE tenant_id=v.tenant_id AND id=p_site FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM app.wordpress_candidates c JOIN app.wordpress_reviews r ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.candidate_id=c.id
 JOIN app.memberships m ON m.tenant_id=r.tenant_id AND m.user_id=r.actor_id
 JOIN app.site_memberships sm ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id AND sm.site_id=c.site_id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=p_candidate AND r.decision='approved'
 AND m.state='active' AND m.role_key='owner' AND m.authorization_epoch=r.membership_epoch
 AND sm.state='active' AND sm.authorization_epoch=r.site_epoch
 AND control.wordpress_binding_current(c.binding_id,p_generation)) THEN RETURN jsonb_build_object('state','approval_required'); END IF;
 SELECT id INTO existing FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id AND candidate_id=p_candidate;
 IF existing IS NOT NULL THEN RETURN jsonb_build_object('state','replayed','intent_id',existing); END IF;
 IF p_prior IS NULL THEN
  SELECT id INTO existing FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND candidate_id=p_candidate ORDER BY created_at LIMIT 1;
  IF existing IS NOT NULL THEN RETURN jsonb_build_object('state','replayed','intent_id',existing); END IF;
 ELSIF NOT EXISTS(SELECT 1 FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_prior
  AND candidate_id=p_candidate AND state IN ('outcome_unknown','escalated')) THEN RETURN jsonb_build_object('state','fresh_intent_denied'); END IF;
 SELECT coalesce((SELECT w.cap FROM app.content_writer_caps w WHERE w.tenant_id=v.tenant_id AND w.site_id=p_site ORDER BY w.created_at DESC,w.id DESC LIMIT 1),2) INTO cap;
 SELECT count(*) INTO n FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND created_at>transaction_timestamp()-interval '7 days';
 IF n>=cap THEN RETURN jsonb_build_object('state','cap_reached'); END IF;
 INSERT INTO app.wordpress_intents(tenant_id,site_id,id,candidate_id,marker,prior_intent_id,actor_id,recovery_generation)
 VALUES(v.tenant_id,p_site,p_id,p_candidate,'signal-s'||replace(p_id::text,'-',''),p_prior,v.user_id,p_generation);
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,intent_id,subject_id,outcome) VALUES(v.tenant_id,p_site,p_id,p_candidate,'queued');
 RETURN jsonb_build_object('state','queued','intent_id',p_id);
END $$;
CREATE FUNCTION control.wordpress_packet(p_hash bytea,p_generation text,p_site uuid,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN NULL; END IF;
 RETURN (SELECT jsonb_build_object('intent',to_jsonb(i),'binding',to_jsonb(b),'candidate',to_jsonb(c))
 FROM app.wordpress_intents i JOIN app.wordpress_candidates c ON c.tenant_id=i.tenant_id AND c.site_id=i.site_id AND c.id=i.candidate_id
 JOIN app.wordpress_bindings b ON b.tenant_id=c.tenant_id AND b.site_id=c.site_id AND b.id=c.binding_id
 WHERE i.tenant_id=v.tenant_id AND i.site_id=p_site AND i.id=p_id AND control.wordpress_binding_current(b.id,p_generation));
END $$;
CREATE FUNCTION control.wordpress_claim(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_journal jsonb,p_message bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; i app.wordpress_intents%%ROWTYPE; BEGIN
 IF session_user<>'signal_identity' THEN RAISE EXCEPTION 'wordpress_dispatch_denied'; END IF;
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 SELECT * INTO i FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR i.recovery_generation<>p_generation OR control.wordpress_packet(p_hash,p_generation,p_site,p_id) IS NULL THEN RETURN 'denied'; END IF;
 IF i.state NOT IN ('queued','retry') THEN RETURN i.state; END IF;
 IF EXISTS(SELECT 1 FROM app.site_weekly_control WHERE tenant_id=v.tenant_id AND site_id=p_site AND paused)
 OR NOT EXISTS(SELECT 1 FROM app.wordpress_candidates c
 JOIN app.content_draft_intents d ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.id=c.draft_id
 JOIN app.content_briefs b ON b.tenant_id=d.tenant_id AND b.site_id=d.site_id AND b.id=d.brief_id
 WHERE c.tenant_id=v.tenant_id AND c.site_id=p_site AND c.id=i.candidate_id
 AND NOT EXISTS(SELECT 1 FROM app.content_briefs n WHERE n.tenant_id=b.tenant_id AND n.site_id=b.site_id AND n.supersedes_id=b.id)
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(d.fact_snapshot) f
 WHERE NOT EXISTS(SELECT 1 FROM control.approved_business_brain_facts(p_hash,p_generation,p_site) a
 WHERE a.fact_id::text=f->>'fact_id' AND a.statement=f->>'statement' AND a.category=f->>'category')))
 THEN RETURN 'stale'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.wordpress_reviews r JOIN app.memberships m ON m.tenant_id=r.tenant_id AND m.user_id=r.actor_id
 JOIN app.site_memberships sm ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id AND sm.site_id=r.site_id
 WHERE r.tenant_id=i.tenant_id AND r.site_id=i.site_id AND r.candidate_id=i.candidate_id AND r.decision='approved'
 AND m.state='active' AND m.role_key='owner' AND m.authorization_epoch=r.membership_epoch AND sm.state='active' AND sm.authorization_epoch=r.site_epoch)
 OR p_journal IS NULL OR p_journal->>'write_intent_id' IS DISTINCT FROM p_id::text
 OR p_journal->>'attempt' IS DISTINCT FROM (i.attempts+1)::text
 OR p_journal->>'operation_id' IS NULL OR p_message IS NULL OR octet_length(p_message)<>32
 OR coalesce((p_journal->>'position')::bigint,0)<1 THEN RETURN 'denied'; END IF;
 UPDATE app.wordpress_intents SET state='dispatching',attempts=attempts+1,journal_receipt=p_journal,dispatch_session_hash=p_hash WHERE tenant_id=i.tenant_id AND site_id=i.site_id AND id=i.id;
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,intent_id,subject_id,outcome,message_sha256) VALUES(i.tenant_id,i.site_id,i.id,i.candidate_id,'dispatching',p_message);
 RETURN 'claimed'; END $$;
CREATE FUNCTION control.wordpress_finish(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_outcome text,p_post bigint,p_url text,p_digest text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; i app.wordpress_intents%%ROWTYPE; v_state text; BEGIN
 IF session_user<>'signal_identity' THEN RAISE EXCEPTION 'wordpress_completion_denied'; END IF;
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 SELECT * INTO i FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR p_outcome IS NULL OR p_outcome NOT IN ('recorded','outcome_unknown','escalated','not_transmitted','unapplied_rejection') THEN RETURN 'denied'; END IF;
 IF i.state='recorded' THEN RETURN i.state; END IF;
 IF i.state NOT IN ('dispatching','outcome_unknown','escalated') THEN RETURN i.state; END IF;
 IF p_outcome IN ('not_transmitted','unapplied_rejection') THEN
  IF i.state<>'dispatching' THEN RETURN i.state; END IF;
  v_state:=CASE WHEN i.attempts<2 THEN 'retry' ELSE 'failed' END;
 ELSIF p_outcome='recorded' THEN
  IF p_post IS NULL OR p_post<1 OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' OR p_url IS NULL OR length(p_url)>2048 THEN RETURN 'denied'; END IF;
  v_state:='recorded';
 ELSE v_state:=CASE WHEN i.state='escalated' AND p_outcome='outcome_unknown' THEN 'escalated' ELSE p_outcome END; END IF;
 UPDATE app.wordpress_intents SET state=v_state,post_id=CASE WHEN v_state='recorded' THEN p_post ELSE post_id END,
 post_url=CASE WHEN v_state='recorded' THEN p_url ELSE post_url END,content_sha256=CASE WHEN v_state='recorded' THEN p_digest ELSE content_sha256 END
 WHERE tenant_id=i.tenant_id AND site_id=i.site_id AND id=i.id;
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,intent_id,subject_id,outcome,post_id,observed_sha256)
 VALUES(i.tenant_id,i.site_id,i.id,i.candidate_id,p_outcome,p_post,p_digest);
 RETURN v_state;
END $$;

ALTER TABLE app.egress_operations DROP CONSTRAINT egress_operations_egress_profile_check;
ALTER TABLE app.egress_operations ADD CONSTRAINT egress_operations_egress_profile_check CHECK(egress_profile IN
 ('legacy_unqualified','crawl_page','crawl_robots','browser_read','browser_worker_read','github_rest','slack_bot','slack_oauth','telegram_bot','dataforseo','crawl_key_file','indexnow_submit','ga4_admin','ga4_data',
 'github_repository_write','google_oauth_token','google_oauth_revoke','gsc_api','bing_oauth_token','bing_api',
 'jev','model_json','openai_model','openai_assistant','perplexity_assistant','gemini_assistant','wordpress_rest'));
ALTER FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) RENAME TO bind_before_wordpress_egress_profile;
REVOKE ALL ON FUNCTION control.bind_before_wordpress_egress_profile(uuid,uuid,uuid,bytea,text) FROM signal_crawl_admission;
CREATE FUNCTION control.bind_shared_egress_profile(p_tenant_id uuid,p_site_id uuid,p_operation_id uuid,p_request_sha256 bytea,p_profile text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o app.egress_operations%%ROWTYPE; suffix text; BEGIN
 IF p_profile<>'wordpress_rest' THEN RETURN control.bind_before_wordpress_egress_profile(p_tenant_id,p_site_id,p_operation_id,p_request_sha256,p_profile); END IF;
 PERFORM set_config('signal.tenant_id',p_tenant_id::text,true); PERFORM set_config('signal.site_id',p_site_id::text,true);
 SELECT * INTO o FROM app.egress_operations WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id FOR UPDATE;
 IF NOT FOUND OR o.state<>'dispatched' OR o.request_sha256 IS DISTINCT FROM p_request_sha256 OR o.purpose<>'connector'
 OR o.method NOT IN ('GET','POST') OR NOT o.credentialed OR o.request_bytes>65536 OR o.max_response_bytes>131072
 OR o.egress_profile NOT IN ('legacy_unqualified',p_profile) THEN RAISE EXCEPTION 'wordpress_profile_denied' USING ERRCODE='22023'; END IF;
 suffix:=substr(o.request_url,length(o.origin)+1);
 IF o.method='POST' THEN
  IF suffix<>'/wp-json/wp/v2/posts' OR NOT EXISTS(SELECT 1 FROM app.wordpress_intents i
   JOIN app.wordpress_candidates c ON c.tenant_id=i.tenant_id AND c.site_id=i.site_id AND c.id=i.candidate_id
   JOIN app.wordpress_bindings b ON b.tenant_id=c.tenant_id AND b.site_id=c.site_id AND b.id=c.binding_id
   JOIN app.wordpress_receipts r ON r.tenant_id=i.tenant_id AND r.site_id=i.site_id AND r.intent_id=i.id AND r.outcome='dispatching'
   WHERE i.tenant_id=p_tenant_id AND i.site_id=p_site_id AND control.wordpress_dispatch_current(i.id) AND i.journal_receipt IS NOT NULL
   AND r.message_sha256=o.request_body_sha256 AND b.origin=o.origin AND control.wordpress_binding_current(b.id,i.recovery_generation)) THEN
    RAISE EXCEPTION 'wordpress_write_denied' USING ERRCODE='22023'; END IF;
 ELSE
  IF NOT (suffix='/wp-json/wp/v2/users/me?context=edit' AND EXISTS(SELECT 1 FROM control.public_origin_claims c WHERE c.tenant_id=p_tenant_id AND c.site_id=p_site_id AND c.origin=o.origin AND c.recheck_at>transaction_timestamp()))
  AND NOT EXISTS(SELECT 1 FROM app.wordpress_bindings b WHERE b.tenant_id=p_tenant_id AND b.site_id=p_site_id AND b.origin=o.origin
   AND control.wordpress_binding_current(b.id,b.recovery_generation) AND (
    EXISTS(SELECT 1 FROM app.wordpress_intents i JOIN app.wordpress_candidates c ON c.tenant_id=i.tenant_id AND c.site_id=i.site_id AND c.id=i.candidate_id
     WHERE c.binding_id=b.id AND suffix='/wp-json/wp/v2/posts?slug='||i.marker||'&status=draft&context=edit&author='||b.user_id::text||'&per_page=100')
    OR EXISTS(SELECT 1 FROM app.wordpress_intents i JOIN app.wordpress_candidates c ON c.tenant_id=i.tenant_id AND c.site_id=i.site_id AND c.id=i.candidate_id
     WHERE c.binding_id=b.id AND i.state='recorded' AND suffix='/wp-json/wp/v2/posts/'||i.post_id::text||'?context=edit'))) THEN
    RAISE EXCEPTION 'wordpress_read_denied' USING ERRCODE='22023'; END IF;
 END IF;
 IF o.egress_profile='legacy_unqualified' THEN UPDATE app.egress_operations SET egress_profile=p_profile WHERE tenant_id=p_tenant_id AND site_id=p_site_id AND id=p_operation_id; END IF;
 RETURN 'bound'; END $$;
REVOKE ALL ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text) TO signal_crawl_admission;

ALTER TABLE control.authority_restriction_outbox DROP CONSTRAINT authority_restriction_outbox_target_kind_check,DROP CONSTRAINT authority_restriction_outbox_restriction_kind_check;
ALTER TABLE control.authority_restriction_outbox ADD CONSTRAINT authority_restriction_outbox_target_kind_check CHECK(target_kind IN ('identity_session','recipe_release','standing_grant','slack_binding','slack_link','ga4_binding','telegram_binding','telegram_link','wordpress_binding')),
 ADD CONSTRAINT authority_restriction_outbox_restriction_kind_check CHECK((target_kind='identity_session' AND restriction_kind='session_revoked') OR (target_kind IN ('recipe_release','standing_grant','slack_binding','slack_link','ga4_binding','telegram_binding','telegram_link','wordpress_binding') AND restriction_kind=target_kind||'_revoked'));
ALTER TABLE control.authority_denial_tombstones DROP CONSTRAINT authority_denial_tombstones_target_kind_check,DROP CONSTRAINT authority_denial_tombstones_restriction_kind_check;
ALTER TABLE control.authority_denial_tombstones ADD CONSTRAINT authority_denial_tombstones_target_kind_check CHECK(target_kind IN ('identity_session','recipe_release','standing_grant','slack_binding','slack_link','ga4_binding','telegram_binding','telegram_link','wordpress_binding')),
 ADD CONSTRAINT authority_denial_tombstones_restriction_kind_check CHECK((target_kind='identity_session' AND restriction_kind='session_revoked') OR (target_kind IN ('recipe_release','standing_grant','slack_binding','slack_link','ga4_binding','telegram_binding','telegram_link','wordpress_binding') AND restriction_kind=target_kind||'_revoked'));

DO $$ DECLARE expr text; BEGIN
 SELECT pg_get_expr(conbin,conrelid) INTO expr FROM pg_constraint WHERE conrelid='control.platform_events'::regclass AND conname='platform_events_contract_check';
 ALTER TABLE control.platform_events DROP CONSTRAINT platform_events_contract_check;
 EXECUTE 'ALTER TABLE control.platform_events ADD CONSTRAINT platform_events_contract_check CHECK (('||expr||') OR
 (event_type=''wordpress.binding.revoked'' AND object_kind=''wordpress_binding'' AND actor_user_id IS NOT NULL AND reason=''owner_revocation''
 AND facts=jsonb_build_object(''schema_version'',1,''restriction_kind'',''wordpress_binding_revoked'',''target_id'',object_id)))';
END $$;
CREATE OR REPLACE FUNCTION control.validate_platform_event_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.event_type='identity.session.issued' THEN
  IF NOT EXISTS(SELECT 1 FROM control.identity_sessions s WHERE s.id=NEW.object_id AND s.user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='identity.session.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM control.identity_sessions s WHERE s.id=NEW.object_id AND s.user_id=NEW.actor_user_id AND s.revoked_at IS NOT NULL) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='identity.login.failed' THEN
  IF NOT EXISTS(SELECT 1 FROM control.oidc_login_attempts a WHERE a.id=NEW.object_id AND a.consumed_at IS NOT NULL) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='authority.restriction.acknowledged' THEN
  IF NOT EXISTS(SELECT 1 FROM control.authority_restriction_outbox o WHERE o.event_id=NEW.object_id AND o.actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='recipe.release.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM control.recipe_release_events e WHERE e.id=NEW.id AND e.release_id=NEW.object_id AND e.actor_user_id=NEW.actor_user_id AND e.status='REVOKED' AND e.source='operator') THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='standing.grant.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM app.standing_authorization_revocations r WHERE r.id=NEW.id AND r.grant_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type IN ('slack.binding.revoked','slack.link.revoked') THEN
  IF NOT EXISTS(SELECT 1 FROM control.slack_revocations r WHERE r.event_id=NEW.id AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id AND r.target_kind=NEW.object_kind) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='ga4.binding.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM control.ga4_binding_restrictions r WHERE r.event_id=NEW.id AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type IN ('telegram.binding.revoked','telegram.link.revoked') THEN
  IF NOT EXISTS(SELECT 1 FROM control.telegram_revocations r WHERE r.event_id=NEW.id AND r.target_id=NEW.object_id AND r.actor_user_id=NEW.actor_user_id AND r.target_kind=NEW.object_kind) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSIF NEW.event_type='wordpress.binding.revoked' THEN
  IF NOT EXISTS(SELECT 1 FROM control.wordpress_revocations r WHERE r.event_id=NEW.id AND r.binding_id=NEW.object_id AND r.actor_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 ELSE RAISE EXCEPTION 'platform event object reference is invalid' USING ERRCODE='23503'; END IF;
 RETURN NEW; END $$;
CREATE POLICY platform_wordpress_revocation_insert ON control.platform_events FOR INSERT TO signal_migrator
 WITH CHECK(event_type='wordpress.binding.revoked' AND EXISTS(SELECT 1 FROM control.wordpress_revocations r WHERE r.event_id=platform_events.id AND r.binding_id=platform_events.object_id AND r.actor_id=platform_events.actor_user_id));
CREATE FUNCTION control.wordpress_revoke(p_hash bytea,p_generation text,p_site uuid,p_binding uuid,p_event uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; facts jsonb; BEGIN
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site);
 IF v.tenant_id IS NULL OR NOT EXISTS(SELECT 1 FROM app.wordpress_bindings b WHERE b.tenant_id=v.tenant_id AND b.site_id=p_site AND b.id=p_binding) THEN RETURN 'denied'; END IF;
 IF EXISTS(SELECT 1 FROM control.wordpress_revocations WHERE binding_id=p_binding) THEN RETURN 'revoked'; END IF;
 INSERT INTO control.wordpress_revocations(binding_id,event_id,actor_id) VALUES(p_binding,p_event,v.user_id);
 facts:=jsonb_build_object('schema_version',1,'restriction_kind','wordpress_binding_revoked','target_id',p_binding);
 INSERT INTO control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts,reason) VALUES(p_event,'wordpress.binding.revoked',v.user_id,'wordpress_binding',p_binding,facts,'owner_revocation');
 INSERT INTO control.authority_restriction_outbox(event_id,scope_kind,actor_user_id,target_kind,target_id,restriction_kind,effective_epoch,event_time,original_facts)
 VALUES(p_event,'platform',v.user_id,'wordpress_binding',p_binding,'wordpress_binding_revoked',1,transaction_timestamp(),facts);
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,subject_id,outcome) VALUES(v.tenant_id,p_site,p_binding,'revoked');
 RETURN 'AUTHORITY_DURABILITY_PENDING'; END $$;
REVOKE ALL ON FUNCTION control.wordpress_revoke(bytea,text,uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.wordpress_revoke(bytea,text,uuid,uuid,uuid) TO signal_api,signal_identity;
CREATE FUNCTION control.apply_wordpress_authority_denial(p_event uuid,p_target uuid,p_epoch bigint,p_stream uuid,p_position bigint,p_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'signal_authority_dispatcher' OR p_event IS NULL OR p_target IS NULL OR p_epoch<>1 OR p_stream IS NULL OR p_position<1 OR p_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'wordpress_replay_denied' USING ERRCODE='42501'; END IF;
 INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,restriction_kind,effective_epoch,stream_generation,stream_position,payload_hash)
 VALUES(p_event,'wordpress_binding',p_target,'wordpress_binding_revoked',p_epoch,p_stream,p_position,p_hash) ON CONFLICT(event_id) DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE event_id=p_event AND target_kind='wordpress_binding' AND target_id=p_target AND effective_epoch=p_epoch AND stream_generation=p_stream AND stream_position=p_position AND payload_hash=p_hash) THEN RAISE EXCEPTION 'wordpress_replay_conflict' USING ERRCODE='23505'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.apply_wordpress_authority_denial(uuid,uuid,bigint,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.apply_wordpress_authority_denial(uuid,uuid,bigint,uuid,bigint,text) TO signal_authority_dispatcher;
CREATE FUNCTION control.wordpress_read_claim(p_hash bytea,p_generation text,p_site uuid,p_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; i app.wordpress_intents%%ROWTYPE; BEGIN
 IF session_user<>'signal_identity' THEN RAISE EXCEPTION 'wordpress_read_denied'; END IF;
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 SELECT * INTO i FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id FOR UPDATE;
 IF NOT FOUND OR control.wordpress_packet(p_hash,p_generation,p_site,p_id) IS NULL THEN RETURN 'denied'; END IF;
 IF i.reconcile_count>=12 THEN RETURN 'read_exhausted'; END IF;
 IF i.state NOT IN ('outcome_unknown','dispatching','escalated','recorded')
 OR i.next_read_at>transaction_timestamp() THEN RETURN 'read_deferred'; END IF;
 UPDATE app.wordpress_intents SET state=CASE WHEN state='dispatching' THEN 'outcome_unknown' ELSE state END,
 reconcile_count=reconcile_count+1,next_read_at=transaction_timestamp()+interval '5 minutes'
 WHERE tenant_id=i.tenant_id AND site_id=i.site_id AND id=i.id;
 RETURN 'read_claimed';
END $$;
CREATE FUNCTION control.wordpress_observation(p_hash bytea,p_generation text,p_site uuid,p_id uuid,p_outcome text,p_digest text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; i app.wordpress_intents%%ROWTYPE; BEGIN
 IF session_user<>'signal_identity' THEN RAISE EXCEPTION 'wordpress_observation_denied'; END IF;
 SELECT * INTO v FROM control.business_brain_owner(p_hash,p_generation,p_site); IF v.tenant_id IS NULL THEN RETURN 'denied'; END IF;
 SELECT * INTO i FROM app.wordpress_intents WHERE tenant_id=v.tenant_id AND site_id=p_site AND id=p_id AND state='recorded';
 IF NOT FOUND OR p_outcome NOT IN ('edited_before_publish','published_unverified','verified','observation_unavailable') THEN RETURN 'denied'; END IF;
 INSERT INTO app.wordpress_receipts(tenant_id,site_id,intent_id,subject_id,outcome,post_id,observed_sha256)
 VALUES(i.tenant_id,i.site_id,i.id,i.candidate_id,p_outcome,i.post_id,p_digest);
 RETURN p_outcome;
END $$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS signature,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='control' AND p.proname LIKE 'wordpress_%%' LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %%s FROM PUBLIC',f.signature);
  IF f.proname NOT IN ('wordpress_binding_current','wordpress_user_valid','wordpress_dispatch_current') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %%s TO signal_identity',f.signature);
   IF f.proname NOT IN ('wordpress_claim','wordpress_finish','wordpress_read_claim','wordpress_observation') THEN
    EXECUTE format('GRANT EXECUTE ON FUNCTION %%s TO signal_api',f.signature);
   END IF;
  END IF;
 END LOOP;
END $$;
