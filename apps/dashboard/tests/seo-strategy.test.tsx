import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { SeoStrategy } from "../components/seo-strategy";
import { GET, POST } from "../app/actions/seo-strategy/route";
import { strategyCommand, validSeoProjection } from "../lib/seo-strategy-api";
import { ID, ITEM, SITE, projection } from "./seo-strategy.fixture";

const ORIGIN = "http://localhost:3000", TOKEN = "synthetic-" + "t".repeat(33);
const originalFetch = globalThis.fetch, originalOrigin = process.env.SIGNAL_DASHBOARD_ORIGIN;

test("provider research shows dated provenance and no ranking prediction", () => {
  const data=structuredClone(projection);
  data.snapshot!.payload.evidence[ITEM]={kind:"dataforseo_serp",record:{id:ITEM,recorded_at:"2026-10-04T00:00:00Z",
    result:{kind:"serp",subject:"widgets",competitors:[{rank:1,domain:"competitor.example"}]}}};
  assert.ok(validSeoProjection(data));
  const html=renderToStaticMarkup(<SeoStrategy siteId={SITE} view="strategy" initialData={data}/>);
  assert.match(html,/As reported by DataForSEO/); assert.match(html,/2026-10-04/);
  assert.match(html,/Position 1: competitor.example/); assert.match(html,/not a ranking prediction/);
});

test("Bing page analytics show incomplete coverage and two non-comparable positions", () => {
  const data=structuredClone(projection), metric=(value:number)=>({value,evidence_ids:[ID]});
  data.snapshot!.payload.performance.push({source:"bing",evidence_id:ID,dimensions:["page","date"],window:null,
    coverage:{complete:false,date_granularity:"unknown"},total_scope:"returned top-page cohort only",
    totals:{clicks:metric(2),impressions:metric(100)},rows:[{labels:{page:"https://example.invalid/",date:"2026-09-01"},
      metrics:{clicks:metric(2),impressions:metric(100),avg_click_position:metric(2),avg_impression_position:metric(4)}}]});
  assert.ok(validSeoProjection(data));
  const html=renderToStaticMarkup(<SeoStrategy siteId={SITE} view="analytics" initialData={data}/>);
  assert.match(html,/As reported by Bing/);assert.match(html,/date granularity is unknown/);
  assert.match(html,/Average click position/);assert.match(html,/Average impression position/);
  assert.match(html,/Observed top-page rows; cohorts are not combined/);
});
afterEach(() => { globalThis.fetch = originalFetch; if (originalOrigin === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN = originalOrigin; });
function request(body: unknown,origin=ORIGIN) { return new Request(`${ORIGIN}/actions/seo-strategy`,{method:"POST",headers:{Cookie:`__Host-signal_session=${TOKEN}`,Origin:origin,"Sec-Fetch-Site":"same-origin","Content-Type":"application/json"},body:JSON.stringify(body)}); }

for (const view of ["overview","pages","strategy","analytics"] as const) test(`0077 ${view} renders evidence and honest unavailable states`,() => {
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view={view} initialData={projection}/>);
  assert.match(html,/Version 1/); assert.match(html,/DataForSEO/); assert.match(html,/evidence_id=/); assert.match(html,/Refresh baseline/);
  if (view === "strategy") { assert.match(html,/Accept proposal/); assert.match(html,/Dismiss/); assert.match(html,/Deterministic fallback/); assert.match(html,/Rationale and inputs/); }
  if (view === "analytics") { assert.match(html,/Observed daily trend/); assert.match(html,/unknown_not_zero/); assert.match(html,/not complete site totals/); }
});
test("unavailable sources do not become zero measurements or accepted work",() => {
  const empty = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="overview" initialData={{schema_version:1,snapshot:null,decisions:[]}}/>);
  assert.match(empty,/No baseline has been recorded/); assert.doesNotMatch(empty,/Fetched count|Evidence /);
  const denied = renderToStaticMarkup(<SeoStrategy siteId={null} view="strategy"/>); assert.match(denied,/Owner access/); assert.doesNotMatch(denied,/Accept proposal/);
  const changed = structuredClone(projection); changed.snapshot!.payload.strategy.items[0]!.action = null; changed.snapshot!.payload.strategy.items[0]!.unavailable_reason = "No sealed Inbox revision.";
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="strategy" initialData={changed}/>); assert.match(html,/disabled=""[^>]*><svg[^]*Accept proposal/); assert.match(html,/No sealed Inbox/);
});
test("strict projection rejects unsupported provenance, nonfinite measurements and unsafe page URLs",() => {
  assert.ok(validSeoProjection(projection));
  const missing = structuredClone(projection); missing.snapshot!.payload.headline.fetched_count!.evidence_ids = []; assert.equal(validSeoProjection(missing),false);
  const unsafe = structuredClone(projection); unsafe.snapshot!.payload.pages[0]!.url = "javascript:alert(1)"; assert.equal(validSeoProjection(unsafe),false);
  const inf = structuredClone(projection); inf.snapshot!.payload.headline.fetched_count!.value = Infinity; assert.equal(validSeoProjection(inf),false);
  const noMetrics = structuredClone(projection); noMetrics.snapshot!.payload.performance[0]!.rows[0]!.metrics = {}; assert.equal(validSeoProjection(noMetrics),false);
  const malformedSources = structuredClone(projection); malformedSources.snapshot!.payload.sources.crawl = {reason:null,records:"not an array"}; assert.equal(validSeoProjection(malformedSources),false);
  assert.equal(strategyCommand({schema_version:1,site_id:SITE,action:"refresh",items:[{ship:true}]}),null);
});
test("same-origin proposal command uses CSRF and exact revision without widening authority",async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  const calls: Request[] = [];
  globalThis.fetch = async (input,init) => { const r = new Request(input,init); calls.push(r); return r.url.endsWith("/tenant-csrf") ? Response.json({schema_version:1,csrf_token:"c".repeat(43)}) : Response.json({schema_version:1,state:"accepted",target_id:ID,target_kind:"brief"}); };
  assert.equal((await POST(request({schema_version:1,site_id:SITE,action:"decide",snapshot_id:ID,item_id:ITEM,decision:"accepted"}))).status,200);
  assert.equal(calls[1]?.headers.get("x-csrf-token"),"c".repeat(43));
  assert.deepEqual(await calls[1]?.json(),{schema_version:1,snapshot_id:ID,item_id:ITEM,decision:"accepted"});
});
test("cross-origin, invalid scope and model-provided candidate lists are denied before I/O",async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN; let calls=0;
  globalThis.fetch = async () => { calls++; throw new Error(); };
  const body = {schema_version:1,site_id:SITE,action:"refresh"};
  assert.equal((await POST(request(body,"https://evil.invalid"))).status,403);
  assert.equal((await POST(request({...body,model_items:[{ship:true}]}))).status,403);
  assert.equal((await GET(new Request(`${ORIGIN}/actions/seo-strategy?site_id=invalid`))).status,403); assert.equal(calls,0);
});
test("failed provider read and injected cookies stay unavailable",async () => {
  const get = () => GET(new Request(`${ORIGIN}/actions/seo-strategy?site_id=${SITE}`,{headers:{Cookie:`__Host-signal_session=${TOKEN}`}}));
  globalThis.fetch = async () => Response.json(projection); assert.equal((await get()).status,200);
  globalThis.fetch = async () => Response.json(projection,{headers:{"Set-Cookie":"unsafe=value"}}); assert.equal((await get()).status,503);
  globalThis.fetch = async () => { throw new Error(); }; assert.equal((await get()).status,503);
});
test("oversized command and CSRF streams fail closed before a planning mutation",async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN; let calls=0;
  globalThis.fetch = async () => { calls++; return Response.json({schema_version:1,csrf_token:"c".repeat(5000)}); };
  const body = {schema_version:1,site_id:SITE,action:"refresh"};
  assert.equal((await POST(request({...body,extra:"x".repeat(3000)}))).status,403);
  assert.equal(calls,0);
  assert.equal((await POST(request(body))).status,503);
  assert.equal(calls,1);
});
