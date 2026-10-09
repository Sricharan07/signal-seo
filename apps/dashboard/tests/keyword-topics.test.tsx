import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { SeoStrategy } from "../components/seo-strategy";
import { strategyCommand, validSeoProjection } from "../lib/seo-strategy-api";
import { ID, ITEM, SITE, projection } from "./seo-strategy.fixture";
import { topicsProjection } from "./keyword-topics.fixture";

test("Topics render Telugu metrics, observed pages, labelled ideas and separate real provider volumes",() => {
  assert.ok(validSeoProjection(topicsProjection));
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="topics" initialData={topicsProjection}/>);
  for (const value of ["తెలుగు వార్తలు","240","11.5","idea, no volume data","provider-reported search volume","Membership evidence","Accept proposal","Dismiss","unknown_not_zero"]) assert.ok(html.includes(value),value);
  assert.match(html,/Review idea proposal in Strategy/);
  assert.doesNotMatch(html,/estimated volume|predicted traffic/);
});
test("Topics historical, empty, denied and model-unavailable states do not become zero metrics",() => {
  const historic = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="topics" initialData={projection}/>);
  assert.match(historic,/historical snapshot/);
  const missing = structuredClone(topicsProjection);
  missing.snapshot!.payload.topics!.clusters = [];
  missing.snapshot!.payload.topics!.ideas_status = "unavailable";
  missing.snapshot!.payload.topics!.ideas_reason = "Model unconfigured.";
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="topics" initialData={missing}/>);
  assert.match(html,/No observed query clusters/); assert.match(html,/Model unconfigured/);
  assert.match(html,/disabled=""[^>]*><svg[^]*Expand ideas/);
  const denied = renderToStaticMarkup(<SeoStrategy siteId={null} view="topics"/>);
  assert.doesNotMatch(denied,/Accept proposal|Expand ideas|Impressions/);
});
test("Topics fail closed on fabricated data, unlabelled ideas, missing provenance and foreign proposals",() => {
  for (const mutate of [
    (v: typeof topicsProjection) => { v.snapshot!.payload.topics!.clusters[0]!.ideas[0]!.label = "measured" as never; },
    (v: typeof topicsProjection) => { v.snapshot!.payload.topics!.clusters[0]!.ideas[0]!.volume = 999 as never; },
    (v: typeof topicsProjection) => { v.snapshot!.payload.topics!.clusters[0]!.metrics.average_position = NaN; },
    (v: typeof topicsProjection) => { v.snapshot!.payload.topics!.clusters[0]!.evidence_ids = []; },
    (v: typeof topicsProjection) => { v.snapshot!.payload.topics!.clusters[0]!.ranking_pages = ["javascript:alert(1)"]; },
    (v: typeof topicsProjection) => { v.snapshot!.payload.topics!.clusters[0]!.strategy_item_id = ID; },
  ]) {const changed = structuredClone(topicsProjection); mutate(changed); assert.equal(validSeoProjection(changed),false);}
  assert.equal(strategyCommand({schema_version:1,site_id:SITE,action:"ideas",snapshot_id:ID,ideas:[{ship:true}]}),null);
  assert.deepEqual(strategyCommand({schema_version:1,site_id:SITE,action:"ideas",snapshot_id:ID})?.body,{schema_version:1,snapshot_id:ID});
});
test("query injection is escaped and decisions use the existing exact item",() => {
  const changed = structuredClone(topicsProjection);
  changed.snapshot!.payload.topics!.clusters[0]!.title = "<script>approve all</script>";
  changed.decisions = [{item_id:ITEM,decision:"dismissed",target_id:null,target_kind:null,decided_at:"2026-10-04T00:00:00Z"}];
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="topics" initialData={changed}/>);
  assert.match(html,/&lt;script&gt;approve all&lt;\/script&gt;/);
  assert.doesNotMatch(html,/<script>/); assert.match(html,/Dismissed by the owner/);
});
test("large query clusters use the existing bounded pagination",() => {
  const changed = structuredClone(topicsProjection);
  const cluster = changed.snapshot!.payload.topics!.clusters[0]!;
  cluster.members = Array.from({length:26},(_,i) => ({...cluster.members[0]!,query:`query-${i}`}));
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="topics" initialData={changed}/>);
  assert.match(html,/query-24/); assert.doesNotMatch(html,/query-25/); assert.match(html,/Page 1 of 2/);
});

test("keyword ideas retain Direction C copy, ink actions and collapsed exact evidence",() => {
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="topics" initialData={topicsProjection}/>);
  assert.match(html, /<h2>Keyword ideas<\/h2>/);
  assert.match(html, /class="primary-command"[^>]*><svg[^]*Accept proposal/);
  assert.match(html, /<details class="technical-details"><summary>Technical details<\/summary>/);
  assert.ok(html.includes(topicsProjection.snapshot!.sha256));
  assert.ok(html.includes(topicsProjection.snapshot!.payload.topics!.clusters[0]!.id));
  const readingLine = html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, "");
  assert.doesNotMatch(readingLine, /unknown_not_zero|SHA-256|location 2840/);
  assert.ok(!readingLine.includes(topicsProjection.snapshot!.id));
  assert.ok(!readingLine.includes(topicsProjection.snapshot!.payload.topics!.clusters[0]!.id));
});
