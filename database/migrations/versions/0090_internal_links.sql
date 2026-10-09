CREATE TABLE app.internal_link_candidates (
 tenant_id uuid NOT NULL, site_id uuid NOT NULL, revision_id uuid NOT NULL,
 manifest_id uuid NOT NULL, source_id uuid NOT NULL, target_id uuid NOT NULL,
 source_url text NOT NULL, target_url text NOT NULL,
 cycle_start date NOT NULL DEFAULT (date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date,
 anchor text NOT NULL CHECK(length(anchor) BETWEEN 3 AND 80),
 anchor_key text GENERATED ALWAYS AS (lower(anchor)) STORED,
 PRIMARY KEY(tenant_id,site_id,revision_id),
 UNIQUE(tenant_id,site_id,cycle_start,source_url,target_url),
 FOREIGN KEY(tenant_id,site_id,revision_id) REFERENCES app.candidate_recipe_revisions(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,manifest_id) REFERENCES app.crawl_manifests(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,source_id) REFERENCES app.crawl_page_records(tenant_id,site_id,id),
 FOREIGN KEY(tenant_id,site_id,target_id) REFERENCES app.crawl_page_records(tenant_id,site_id,id),
 CHECK(source_id<>target_id)
);
ALTER TABLE app.internal_link_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.internal_link_candidates FORCE ROW LEVEL SECURITY;
CREATE POLICY internal_link_scope ON app.internal_link_candidates
 USING(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id())
 WITH CHECK(tenant_id=app.current_tenant_id() AND site_id=app.current_site_id());
CREATE TRIGGER internal_link_immutable BEFORE UPDATE OR DELETE ON app.internal_link_candidates
 FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
REVOKE ALL ON app.internal_link_candidates FROM PUBLIC,signal_api,signal_identity,signal_workflow,
 signal_scheduler,signal_bootstrap,signal_crawl_ingest,signal_crawl_admission;
CREATE FUNCTION control.internal_link_terms(p_page jsonb) RETURNS text[] LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog AS $$
 SELECT coalesce(array_agg(term ORDER BY n DESC,term),'{}'::text[]) FROM (
 SELECT lower(word[1]) term,count(*) n FROM regexp_matches(
 coalesce(p_page->>'title','') || ' ' || coalesce((SELECT string_agg(h->>'text',' ')
 FROM jsonb_array_elements(p_page->'headings') h),''),'[a-zA-Z][a-zA-Z0-9-]{2,}','g') word
 WHERE lower(word[1])<>ALL(string_to_array('the and for with from this that have your our more about into are was were you not but can all page home guide learn read click here',' '))
 GROUP BY lower(word[1]) ORDER BY n DESC,term LIMIT 32) terms;
$$;
REVOKE ALL ON FUNCTION control.internal_link_terms(jsonb) FROM PUBLIC;
ALTER TABLE app.candidate_recipe_revisions DROP CONSTRAINT candidate_recipe_evidence_kind;
ALTER TABLE app.candidate_recipe_revisions ADD CONSTRAINT candidate_recipe_evidence_kind CHECK (
 audit_report_id IS NOT NULL OR coalesce((convert_from(canonical_manifest,'UTF8')::jsonb
 #>> '{evidence,finding,key}' IN ('indexnow.key.required','links.internal.add')
 AND convert_from(canonical_manifest,'UTF8')::jsonb->>'approval_class'='owner_review'),false)
);

CREATE FUNCTION control.internal_link_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; m record; origin text;
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' OR a.role_key IS DISTINCT FROM 'owner'
 OR NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN NULL; END IF;
 SELECT primary_origin INTO origin FROM app.sites WHERE tenant_id=a.tenant_id AND id=p_site;
 SELECT * INTO m FROM app.crawl_manifests WHERE tenant_id=a.tenant_id AND site_id=p_site
 ORDER BY completed_at DESC,id DESC LIMIT 1;
 IF m.id IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('manifest_id',m.id,'manifest_sha256',encode(m.manifest_sha256,'hex'),
 'site_origin',origin,'coverage',m.coverage,
 'pages',coalesce((SELECT jsonb_agg(jsonb_build_object('id',p.id,'url',u.fetch_url,'title',p.title,
 'headings',p.headings,'internal_links',p.internal_links,'body_sha256',encode(p.body_sha256,'hex'),
 'output_truncated',p.output_truncated) ORDER BY u.fetch_url,p.id)
 FROM app.crawl_page_records p JOIN app.urls u ON u.tenant_id=p.tenant_id AND u.site_id=p.site_id AND u.id=p.url_id
 WHERE p.tenant_id=a.tenant_id AND p.site_id=p_site AND p.crawl_run_id=m.crawl_run_id
 AND p.http_status BETWEEN 200 AND 299 AND NOT p.output_truncated),'[]'::jsonb),
 'used_anchors',coalesce((SELECT jsonb_agg(anchor ORDER BY anchor_key) FROM app.internal_link_candidates
 WHERE tenant_id=a.tenant_id AND site_id=p_site),'[]'::jsonb),
 'candidates',coalesce((SELECT jsonb_agg(jsonb_build_object('id',revision_id,
 'target_id',l.target_id,'source_id',l.source_id,'idempotency_key',r.idempotency_key,
 'extension_id',r.extension_id,'recipe_release_id',r.recipe_release_id,
 'build_id',r.build_id,'revision_sha256',encode(r.revision_sha256,'hex'),
 'page_cap',convert_from(r.canonical_manifest,'UTF8')::jsonb#>'{internal_link,page_cap}') ORDER BY revision_id)
 FROM app.internal_link_candidates l JOIN app.candidate_recipe_revisions r
 ON r.tenant_id=l.tenant_id AND r.site_id=l.site_id AND r.id=l.revision_id
 WHERE l.tenant_id=a.tenant_id AND l.site_id=p_site AND r.created_by_user_id=a.user_id),'[]'::jsonb));
END; $$;
ALTER FUNCTION control.seo_strategy_sources(bytea,text,uuid) RENAME TO seo_strategy_sources_before_internal_links;
REVOKE ALL ON FUNCTION control.seo_strategy_sources_before_internal_links(bytea,text,uuid) FROM PUBLIC,signal_api;
CREATE FUNCTION control.seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT control.seo_strategy_sources_before_internal_links(p_hash,p_generation,p_site)
 || jsonb_build_object('internal_links',control.internal_link_sources(p_hash,p_generation,p_site));
$$;

CREATE FUNCTION control.seal_internal_link_revision(p_hash bytea,p_generation text,p_site uuid,
 p_id uuid,p_key uuid,p_canonical bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a record; p jsonb; e jsonb; sources jsonb; src jsonb; dst jsonb; b record; receipt record;
 release record; prior record; cap integer; baseline record; output text; shared text[];
BEGIN
 SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);
 IF a.outcome IS DISTINCT FROM 'authorized' THEN RETURN a.outcome; END IF;
 IF a.role_key IS DISTINCT FROM 'owner' THEN RETURN 'permission_denied'; END IF;
 IF NOT control.current_github_site_proof(a.tenant_id,p_site) THEN RETURN 'site_not_verified'; END IF;
 IF p_id IS NULL OR p_key IS NULL OR p_canonical IS NULL OR octet_length(p_canonical) NOT BETWEEN 1 AND 32768 THEN RETURN 'invalid_revision'; END IF;
 BEGIN p:=convert_from(p_canonical,'UTF8')::jsonb;
 EXCEPTION WHEN invalid_text_representation OR character_not_in_repertoire THEN RETURN 'invalid_revision'; END;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,140));
 SELECT * INTO prior FROM app.candidate_recipe_revisions WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND created_by_user_id=a.user_id AND idempotency_key=p_key;
 IF FOUND THEN RETURN CASE WHEN prior.id=p_id AND prior.canonical_manifest=p_canonical THEN 'sealed' ELSE 'revision_conflict' END; END IF;
 sources:=control.internal_link_sources(p_hash,p_generation,p_site); e:=p->'evidence';
 SELECT value INTO src FROM jsonb_array_elements(sources->'pages') WHERE value->>'id'=e->>'page_id';
 SELECT value INTO dst FROM jsonb_array_elements(sources->'pages') WHERE value->>'id'=p#>>'{internal_link,target_id}';
 IF src IS NULL OR dst IS NULL OR src=dst
 OR e->>'manifest_id' IS DISTINCT FROM sources->>'manifest_id'
 OR e->>'manifest_sha256' IS DISTINCT FROM sources->>'manifest_sha256'
 OR e->>'site_origin' IS DISTINCT FROM sources->>'site_origin'
 OR e->>'page_url' IS DISTINCT FROM src->>'url'
 OR p#>>'{internal_link,target_url}' IS DISTINCT FROM dst->>'url'
 OR left(dst->>'url',length(sources->>'site_origin')+1) IS DISTINCT FROM (sources->>'site_origin') || '/'
 OR dst->>'url' ~ '[?#[:space:]\\]'
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(src->'internal_links') href
 WHERE split_part(href,'#',1)=dst->>'url')
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(dst->'internal_links') href
 WHERE split_part(href,'#',1)=src->>'url')
 OR p->>'source_sha256' IS DISTINCT FROM src->>'body_sha256'
 OR e#>>'{finding,key}' IS DISTINCT FROM 'links.internal.add'
 THEN RETURN 'evidence_unavailable'; END IF;
 SELECT coalesce(array_agg(term ORDER BY term),'{}'::text[]) INTO shared
 FROM unnest(control.internal_link_terms(src)) term WHERE term=ANY(control.internal_link_terms(dst));
 IF cardinality(shared)<2 OR (SELECT count(*) FROM jsonb_array_elements(sources->'pages') x
 WHERE x->>'id'<>dst->>'id' AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(x->'internal_links') href
 WHERE split_part(href,'#',1)=dst->>'url'))>1
 OR (SELECT count(DISTINCT lower(word[1])) FROM regexp_matches(p#>>'{patch,before}',
 '[a-zA-Z][a-zA-Z0-9-]{2,}','g') word WHERE lower(word[1])=ANY(shared))<2
 THEN RETURN 'relatedness_unavailable'; END IF;
 IF coalesce(p#>>'{internal_link,page_cap}','') !~ '^[1-3]$' THEN RETURN 'invalid_revision'; END IF;
 cap:=(p#>>'{internal_link,page_cap}')::integer;
 IF (SELECT count(*) FROM app.internal_link_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND cycle_start=(date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date AND source_url=src->>'url')>=cap
 THEN RETURN 'page_cap'; END IF;
 IF EXISTS(SELECT 1 FROM app.internal_link_candidates WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND (anchor_key=lower(p#>>'{patch,before}') OR (cycle_start=(date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date
 AND ((source_url=src->>'url' AND target_url=dst->>'url')
 OR (source_url=dst->>'url' AND target_url=src->>'url'))))) THEN RETURN 'duplicate_link_or_anchor'; END IF;
 SELECT * INTO release FROM control.recipe_releases WHERE id=(p->>'recipe_release_id')::uuid
 AND recipe_key='technical_internal_link_add' AND encode(content_hash,'hex')=p->>'release_content_hash' FOR SHARE;
 IF release.id IS NULL OR (SELECT status FROM control.recipe_release_events WHERE release_id=release.id
 ORDER BY sequence_number DESC LIMIT 1) IS DISTINCT FROM 'REVIEWED'
 OR EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=release.id)
 OR convert_from(release.canonical_body,'UTF8')::jsonb->>'approval_class' IS DISTINCT FROM 'owner_review'
 OR control.recipe_autonomy_eligible(release.id) THEN RETURN 'release_unavailable'; END IF;
 SELECT * INTO b FROM app.candidate_build_intents WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND id=(p->>'build_id')::uuid AND extension_id=(p->>'extension_id')::uuid AND status='completed'
 AND requested_by_user_id=a.user_id AND membership_epoch=a.membership_epoch
 AND site_epoch=a.site_authorization_epoch AND recovery_generation=p_generation;
 SELECT * INTO receipt FROM app.candidate_build_receipts WHERE tenant_id=a.tenant_id AND site_id=p_site
 AND build_id=b.id AND exit_class='passed';
 IF b.id IS NULL OR receipt.build_id IS NULL OR receipt.patch_sha256 IS DISTINCT FROM b.patch_sha256
 OR b.patch_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
 OR NOT EXISTS(SELECT 1 FROM app.github_pr_extensions x JOIN app.github_read_bindings y
 ON y.tenant_id=x.tenant_id AND y.site_id=x.site_id AND y.id=x.binding_id AND y.status='active'
 WHERE x.tenant_id=a.tenant_id AND x.site_id=p_site AND x.id=b.extension_id AND x.status='observed'
 AND x.framework='eleventy' AND x.content_format='html' AND x.base_sha=b.base_sha AND x.tree_sha=b.tree_sha)
 THEN RETURN 'build_unavailable'; END IF;
 IF p->>'site_id' IS DISTINCT FROM p_site::text OR p->>'base_sha' IS DISTINCT FROM b.base_sha
 OR coalesce(p->>'source_path','') !~ '^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*\.html$'
 OR src->>'url' IS DISTINCT FROM (sources->>'site_origin') || '/' || (CASE
 WHEN p->>'source_path'='index.html' OR p->>'source_path' LIKE '%%/index.html'
 THEN left(p->>'source_path',length(p->>'source_path')-10) ELSE p->>'source_path' END)
 OR p->>'patch_sha256' IS DISTINCT FROM b.patch_sha256
 OR p->>'approval_class' IS DISTINCT FROM 'owner_review'
 OR p#>>'{internal_link,recipe_key}' IS DISTINCT FROM 'technical_internal_link_add'
 OR p->>'audit_report_id' IS NOT NULL
 OR p->'model_draft' IS DISTINCT FROM 'null'::jsonb
 OR p->'claim_review_required' IS DISTINCT FROM 'false'::jsonb
 OR p#>'{internal_link,autonomy_eligible}' IS DISTINCT FROM 'false'::jsonb
 OR coalesce(p->>'result_sha256','') !~ '^[0-9a-f]{64}$'
 OR coalesce(p#>>'{patch,offset}','') !~ '^(0|[1-9][0-9]*)$'
 OR coalesce(p#>>'{patch,before}','') !~ '^[A-Za-z0-9-]+( [A-Za-z0-9-]+){1,2}$'
 OR length(p#>>'{patch,before}') NOT BETWEEN 3 AND 80
 OR (SELECT count(*)<>count(DISTINCT lower(w)) FROM unnest(string_to_array(p#>>'{patch,before}',' ')) w)
 OR p#>>'{patch,after}' IS DISTINCT FROM '<a href="' || replace(replace(replace(replace(replace(dst->>'url','&','&amp;'),'"','&quot;'),'<','&lt;'),'>','&gt;'),chr(39),'&#x27;') || '">' || (p#>>'{patch,before}') || '</a>'
 OR p#>>'{build_receipt,toolchain}' IS DISTINCT FROM receipt.toolchain
 OR p#>>'{build_receipt,command}' IS DISTINCT FROM receipt.build_command
 OR p#>>'{build_receipt,exit_class}' IS DISTINCT FROM 'passed'
 OR p#>>'{build_receipt,logs_sha256}' IS DISTINCT FROM receipt.logs_sha256
 OR p#>'{build_receipt,artifacts}' IS DISTINCT FROM receipt.artifacts
 OR coalesce(length(p->>'expected_impact'),0) NOT BETWEEN 1 AND 500
 OR coalesce(length(p->>'recovery_plan'),0) NOT BETWEEN 1 AND 500 THEN RETURN 'invalid_revision'; END IF;
 output:='_site/' || (p->>'source_path');
 SELECT r.* INTO baseline FROM app.candidate_build_receipts r JOIN app.candidate_build_intents i
 ON i.tenant_id=r.tenant_id AND i.site_id=r.site_id AND i.id=r.build_id
 WHERE r.tenant_id=a.tenant_id AND r.site_id=p_site AND r.build_id=(p#>>'{internal_link,baseline_build_id}')::uuid
 AND r.exit_class='passed' AND r.base_sha=b.base_sha AND i.tree_sha=b.tree_sha
 AND i.extension_id=b.extension_id AND i.requested_by_user_id=a.user_id
 AND i.membership_epoch=a.membership_epoch AND i.site_epoch=a.site_authorization_epoch
 AND i.recovery_generation=p_generation AND r.patch_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
 IF baseline.build_id IS NULL OR p#>>'{internal_link,output_path}' IS DISTINCT FROM output
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(baseline.artifacts) x WHERE x->>'path'=output AND x->>'sha256'=p->>'source_sha256')
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(receipt.artifacts) x WHERE x->>'path'=output AND x->>'sha256'=p->>'result_sha256'
 AND (x->>'size')::integer=(SELECT (base_artifact->>'size')::integer FROM jsonb_array_elements(baseline.artifacts) base_artifact WHERE base_artifact->>'path'=output)
 + octet_length(convert_to((p#>>'{patch,after}'),'UTF8'))-octet_length(convert_to((p#>>'{patch,before}'),'UTF8')))
 OR (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(baseline.artifacts) x WHERE x->>'path'<>output)
 IS DISTINCT FROM (SELECT jsonb_agg(x ORDER BY x->>'path') FROM jsonb_array_elements(receipt.artifacts) x WHERE x->>'path'<>output)
 THEN RETURN 'build_assertions_failed'; END IF;
 INSERT INTO app.candidate_recipe_revisions(tenant_id,site_id,id,extension_id,build_id,audit_report_id,
 finding_id,recipe_release_id,release_content_hash,base_sha,patch_sha256,canonical_manifest,
 created_by_user_id,idempotency_key,membership_epoch,site_epoch,recovery_generation)
 VALUES(a.tenant_id,p_site,p_id,b.extension_id,b.id,NULL,(p->>'finding_id')::uuid,release.id,release.content_hash,
 b.base_sha,b.patch_sha256,p_canonical,a.user_id,p_key,a.membership_epoch,a.site_authorization_epoch,p_generation);
 INSERT INTO app.internal_link_candidates(tenant_id,site_id,revision_id,manifest_id,source_id,target_id,anchor,source_url,target_url)
 VALUES(a.tenant_id,p_site,p_id,(sources->>'manifest_id')::uuid,(src->>'id')::uuid,(dst->>'id')::uuid,p#>>'{patch,before}',src->>'url',dst->>'url');
 RETURN 'sealed';
END; $$;
REVOKE ALL ON FUNCTION control.internal_link_sources(bytea,text,uuid),control.seal_internal_link_revision(bytea,text,uuid,uuid,uuid,bytea),control.seo_strategy_sources(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.internal_link_sources(bytea,text,uuid) TO signal_identity,signal_api;
GRANT EXECUTE ON FUNCTION control.seal_internal_link_revision(bytea,text,uuid,uuid,uuid,bytea) TO signal_identity;
GRANT EXECUTE ON FUNCTION control.seo_strategy_sources(bytea,text,uuid) TO signal_api;

-- Add one A1 candidate skill to 0135's admission/reservation machinery. It
-- consumes the existing draft_patch grant, not the reviewed link release's A2 scope.
ALTER TABLE app.weekly_skill_intents DROP CONSTRAINT weekly_skill_intents_stage_check;
ALTER TABLE app.weekly_skill_intents ADD CHECK(stage IN ('import_gsc','import_bing','import_ga4','brain_refresh','strategy_rebuild','internal_link_proposals','brief_proposals','report_delivery'));
ALTER TABLE app.weekly_skill_results DROP CONSTRAINT weekly_skill_results_stage_check;
ALTER TABLE app.weekly_skill_results ADD CHECK(stage IN ('import_gsc','import_bing','import_ga4','brain_refresh','strategy_rebuild','internal_link_proposals','brief_proposals','report_delivery'));
DO $$ DECLARE definition text; branch text; BEGIN
 SELECT pg_get_functiondef('control.admit_weekly_skill(uuid,uuid,uuid,uuid,text,text,bytea,bigint,boolean)'::regprocedure) INTO definition;
 definition:=replace(definition,'''strategy_rebuild'',''brief_proposals''','''strategy_rebuild'',''internal_link_proposals'',''brief_proposals''');
 definition:=replace(definition,$old$p_stage='brief_proposals' THEN 'draft_patch'$old$, $new$p_stage IN ('brief_proposals','internal_link_proposals') THEN 'draft_patch'$new$);
 branch:=$branch$
 ELSIF p_stage='internal_link_proposals' THEN
   IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_results WHERE tenant_id=p_tenant AND site_id=p_site AND cycle_id=p_cycle AND stage='strategy_rebuild' AND outcome='completed') THEN
     RETURN jsonb_build_object('state','unavailable','reason','DEPENDENCY_UNAVAILABLE'); END IF;
   SELECT coalesce(jsonb_agg(jsonb_build_object(
     'source_id',x.item#>>'{priority,inputs,source_id}','target_id',x.item#>>'{priority,inputs,target_id}',
     'manifest_id',s.payload#>>'{sources,internal_links,manifest_id}',
     'resource_path',coalesce(substring(x.item#>>'{priority,inputs,source_url}' FROM '^https?://[^/]+(/[^?#]*)'),'/'),
     'target_resource_path',coalesce(substring(x.item#>>'{priority,inputs,target_url}' FROM '^https?://[^/]+(/[^?#]*)'),'/'),
     'extension_id',ext.id,'binding_id',ext.binding_id,'release_id',rel.id,
     'revision_key',control.weekly_skill_identity(p_cycle,x.item->>'id'||':link-revision'),
     'build_key',control.weekly_skill_identity(p_cycle,x.item->>'id'||':link-build'),
     'baseline_key',control.weekly_skill_identity(p_cycle,x.item->>'id'||':link-baseline')) ORDER BY x.item->>'id'),'[]') INTO plan
   FROM app.seo_strategy_snapshots s CROSS JOIN LATERAL (
     SELECT item FROM jsonb_array_elements(s.payload->'strategy'->'items') item
     WHERE item->>'kind'='internal_link' AND item->'action'='null'::jsonb
     AND NOT EXISTS(SELECT 1 FROM app.internal_link_candidates prior WHERE prior.tenant_id=p_tenant AND prior.site_id=p_site
       AND prior.source_url=item#>>'{priority,inputs,source_url}' AND prior.target_url=item#>>'{priority,inputs,target_url}'
       AND prior.cycle_start=(date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC'))::date)
     ORDER BY item->>'id' LIMIT 32) x
   CROSS JOIN LATERAL (SELECT link_ext.* FROM app.github_pr_extensions link_ext JOIN app.github_read_bindings binding
     ON binding.tenant_id=link_ext.tenant_id AND binding.site_id=link_ext.site_id AND binding.id=link_ext.binding_id AND binding.status='active'
     WHERE link_ext.tenant_id=p_tenant AND link_ext.site_id=p_site AND link_ext.status='observed' AND link_ext.framework='eleventy' AND link_ext.content_format='html'
     ORDER BY link_ext.observed_at DESC,link_ext.id DESC LIMIT 1) ext
   CROSS JOIN LATERAL (SELECT y.id FROM control.recipe_releases y WHERE y.recipe_key='technical_internal_link_add'
     AND (SELECT status FROM control.recipe_release_events WHERE release_id=y.id ORDER BY sequence_number DESC LIMIT 1)='REVIEWED'
     AND NOT EXISTS(SELECT 1 FROM control.authority_denial_tombstones WHERE target_kind='recipe_release' AND target_id=y.id)
     ORDER BY y.version_major DESC,y.version_minor DESC,y.version_patch DESC LIMIT 1) rel
   WHERE s.tenant_id=p_tenant AND s.site_id=p_site AND s.id=(SELECT id FROM app.seo_strategy_snapshots WHERE tenant_id=p_tenant AND site_id=p_site ORDER BY version DESC LIMIT 1);
   FOR unit IN SELECT value FROM jsonb_array_elements(plan) LOOP
     SELECT * INTO e FROM control.standing_grant_eligibility(p_tenant,p_site,p_grant,p_generation,r,kind,unit->>'target_resource_path');
     IF NOT e.eligible THEN RETURN jsonb_build_object('state','unavailable','reason','AUTHORITY_CHANGED'); END IF;
   END LOOP;
 $branch$;
 definition:=replace(definition,$old$ELSIF p_stage='brief_proposals' THEN$old$,branch||$old$ELSIF p_stage='brief_proposals' THEN$old$);
 definition:=replace(definition,$old$WHEN p_stage='brain_refresh' THEN 'NO_CHANGED_SOURCES'$old$, $new$WHEN p_stage='internal_link_proposals' THEN 'NO_ELIGIBLE_INTERNAL_LINKS' WHEN p_stage='brain_refresh' THEN 'NO_CHANGED_SOURCES'$new$);
 EXECUTE definition;
END $$;

CREATE FUNCTION control.resolve_internal_link_skill_authority(p_hash bytea,p_site uuid,p_generation text)
RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,role_key text,authentication_level text,
 membership_epoch bigint,site_authorization_epoch bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c record; u jsonb; i record; e record;
BEGIN
 PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY['internal_link_proposals']);
 SELECT * INTO c FROM control.weekly_skill_context(p_hash,p_generation,p_site);
 SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
 FOR u IN SELECT value FROM jsonb_array_elements(i.plan) LOOP
   SELECT * INTO e FROM control.standing_grant_eligibility(i.tenant_id,p_site,i.grant_id,p_generation,i.recipe_release_id,i.work_type,u->>'target_resource_path');
   IF NOT e.eligible THEN RAISE EXCEPTION 'internal_link_authority_changed' USING ERRCODE='42501'; END IF;
 END LOOP;
 RETURN QUERY SELECT 'authorized'::text,c.tenant_id,c.user_id,'workload'::text,NULL::text,m.authorization_epoch,sm.authorization_epoch
 FROM app.memberships m JOIN app.site_memberships sm ON sm.tenant_id=m.tenant_id AND sm.user_id=m.user_id AND sm.site_id=p_site
 WHERE m.tenant_id=c.tenant_id AND m.user_id=c.user_id AND m.role_key='owner' AND m.state='active' AND sm.state='active';
END $$;
REVOKE ALL ON FUNCTION control.resolve_internal_link_skill_authority(bytea,uuid,text) FROM PUBLIC;

CREATE FUNCTION control.assert_internal_link_skill_resource(p_hash bytea,p_site uuid,p_generation text,p_kind text,p_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i record;
BEGIN
 PERFORM control.resolve_internal_link_skill_authority(p_hash,p_site,p_generation);
 SELECT * INTO i FROM app.weekly_skill_intents WHERE handle_hash=p_hash;
 IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(i.plan) u WHERE
   (p_kind IN ('binding_id','extension_id') AND u->>p_kind=p_id::text)
   OR (p_kind='build_key' AND p_id::text IN (u->>'build_key',u->>'baseline_key'))
   OR (p_kind='build_id' AND EXISTS(SELECT 1 FROM app.candidate_build_intents b WHERE b.tenant_id=i.tenant_id AND b.site_id=p_site
      AND b.id=p_id AND b.idempotency_key::text IN (u->>'build_key',u->>'baseline_key'))))
 THEN RAISE EXCEPTION 'internal_link_resource_denied' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION control.assert_internal_link_skill_resource(bytea,uuid,text,text,uuid) FROM PUBLIC;

-- Copy only existing read/build implementations, substituting a workload
-- authorizer and exact admitted resources. No approval or write port is exposed.
DO $$ DECLARE f record; definition text; guard text; args text; parameters text[]; BEGIN
 FOR f IN SELECT * FROM (VALUES
 ('read_github_read_binding','binding_id','p_binding_id'),('read_github_base_risk','binding_id','p_binding_id'),
 ('record_github_binding_state','binding_id','p_binding_id'),
 ('read_github_pr_extension','extension_id','p_extension_id'),
 ('prepare_candidate_build','extension_id','p_extension_id'),
 ('dispatch_before_dependencies','build_id','p_build_id'),('finish_before_dependencies','build_id','p_build_id'),
 ('dispatch_candidate_build','build_id','p_build_id'),('finish_candidate_build','build_id','p_build_id'),
 ('read_candidate_build','build_id','p_build_id')) v(name,kind,parameter) LOOP
   SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid),p.proargnames INTO definition,args,parameters
   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname=f.name;
   definition:=replace(definition,'FUNCTION control.'||f.name||'(', 'FUNCTION control.internal_link_skill_'||f.name||'(');
   definition:=replace(definition,'control.resolve_snapshot_authority(', 'control.resolve_internal_link_skill_authority(');
   definition:=replace(definition,'control.dispatch_before_dependencies(', 'control.internal_link_skill_dispatch_before_dependencies(');
   definition:=replace(definition,'control.finish_before_dependencies(', 'control.internal_link_skill_finish_before_dependencies(');
   definition:=replace(definition,'''owner''','''workload''');
   guard:=format('PERFORM control.assert_internal_link_skill_resource(%%I,%%I,%%I,%%L,%%I);',parameters[1],parameters[2],parameters[3],f.kind,parameters[4]);
   IF f.name='prepare_candidate_build' THEN guard:=guard||format('PERFORM control.assert_internal_link_skill_resource(%%I,%%I,%%I,''build_key'',%%I);',parameters[1],parameters[2],parameters[3],parameters[6]); END IF;
   definition:=regexp_replace(definition,'BEGIN','BEGIN '||guard);
   EXECUTE definition;
   EXECUTE format('REVOKE ALL ON FUNCTION control.internal_link_skill_%%I(%%s) FROM PUBLIC',f.name,args);
   IF f.name NOT IN ('dispatch_before_dependencies','finish_before_dependencies') THEN
     EXECUTE format('GRANT EXECUTE ON FUNCTION control.internal_link_skill_%%I(%%s) TO signal_workflow',f.name,args);
   END IF;
 END LOOP;
 FOR f IN SELECT * FROM (VALUES ('internal_link_sources'),('seal_internal_link_revision')) v(name) LOOP
   SELECT pg_get_functiondef(p.oid),pg_get_function_identity_arguments(p.oid) INTO definition,args
   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' AND p.proname=f.name;
   definition:=replace(definition,'FUNCTION control.'||f.name||'(', 'FUNCTION control.internal_link_skill_'||f.name||'(');
   definition:=replace(definition,'control.resolve_snapshot_authority(', 'control.resolve_internal_link_skill_authority(');
   definition:=replace(definition,'''owner''','''workload''');
   definition:=replace(definition,'control.internal_link_sources(', 'control.internal_link_skill_internal_link_sources(');
   IF f.name='seal_internal_link_revision' THEN
     definition:=replace(definition,'PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,140));',
       'PERFORM pg_advisory_xact_lock(hashtextextended(p_site::text,140)); IF NOT EXISTS(SELECT 1 FROM app.weekly_skill_intents i CROSS JOIN LATERAL jsonb_array_elements(i.plan) u WHERE i.handle_hash=p_hash AND u->>''revision_key''=p_key::text AND u->>''source_id''=p#>>''{evidence,page_id}'' AND u->>''target_id''=p#>>''{internal_link,target_id}'' AND u->>''extension_id''=p->>''extension_id'' AND u->>''release_id''=p->>''recipe_release_id'' AND u->>''manifest_id''=p#>>''{evidence,manifest_id}'' AND EXISTS(SELECT 1 FROM app.candidate_build_intents cb JOIN app.candidate_build_intents bb ON bb.tenant_id=cb.tenant_id AND bb.site_id=cb.site_id WHERE cb.tenant_id=i.tenant_id AND cb.site_id=i.site_id AND cb.id::text=p->>''build_id'' AND cb.idempotency_key::text=u->>''build_key'' AND bb.id::text=p#>>''{internal_link,baseline_build_id}'' AND bb.idempotency_key::text=u->>''baseline_key'')) THEN RETURN ''scope_denied''; END IF;');
   END IF;
   EXECUTE definition;
   EXECUTE format('REVOKE ALL ON FUNCTION control.internal_link_skill_%%I(%%s) FROM PUBLIC',f.name,args);
   EXECUTE format('GRANT EXECUTE ON FUNCTION control.internal_link_skill_%%I(%%s) TO signal_workflow',f.name,args);
 END LOOP;
END $$;

-- Preserve 0137's learning packet and the existing strategy stage context.
ALTER FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) RENAME TO weekly_skill_sources_before_internal_links;
REVOKE ALL ON FUNCTION control.weekly_skill_sources_before_internal_links(bytea,text,uuid) FROM PUBLIC,signal_workflow;
DO $$ DECLARE definition text; BEGIN
 SELECT pg_get_functiondef('control.internal_link_sources(bytea,text,uuid)'::regprocedure) INTO definition;
 definition:=replace(definition,'FUNCTION control.internal_link_sources(', 'FUNCTION control.weekly_skill_link_graph(');
 definition:=replace(definition,'SELECT * INTO a FROM control.resolve_snapshot_authority(p_hash,p_site,p_generation);',
 'PERFORM control.assert_weekly_skill_stage(p_hash,p_generation,p_site,ARRAY[''strategy_rebuild'']); SELECT ''authorized''::text outcome,''workload''::text role_key,c.* INTO a FROM control.weekly_skill_context(p_hash,p_generation,p_site) c;');
 definition:=replace(definition,'''owner''','''workload''');
 EXECUTE definition;
END $$;
REVOKE ALL ON FUNCTION control.weekly_skill_link_graph(bytea,text,uuid) FROM PUBLIC;
CREATE OR REPLACE FUNCTION control.weekly_skill_seo_strategy_sources(p_hash bytea,p_generation text,p_site uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT control.weekly_skill_sources_before_internal_links(p_hash,p_generation,p_site)
 || jsonb_build_object('internal_links',control.weekly_skill_link_graph(p_hash,p_generation,p_site));
$$;
REVOKE ALL ON FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.weekly_skill_seo_strategy_sources(bytea,text,uuid) TO signal_workflow;
DO $$ DECLARE definition text; BEGIN
 SELECT pg_get_functiondef('control.weekly_report_projection(uuid,uuid,date)'::regprocedure) INTO definition;
 definition:=replace(definition,'''strategy_rebuild'',''brief_proposals''','''strategy_rebuild'',''internal_link_proposals'',''brief_proposals''');
 definition:=replace(definition,$old$s.stage='brief_proposals' THEN 'draft_patch'$old$, $new$s.stage IN ('brief_proposals','internal_link_proposals') THEN 'draft_patch'$new$);
 EXECUTE definition;
END $$;
