import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { validBrandDocumentId as uuid } from "./brand-document-api";

type ObjectValue = Record<string, unknown>;
export interface EvidenceNumber { value: number; evidence_ids: string[] }
export interface PerformanceRow { labels: Record<string, string>; metrics: Record<string, EvidenceNumber> }
export interface PerformanceCohort {
  source: "gsc" | "bing"; evidence_id: string; dimensions: string[];
  window: { start: string; end: string } | null; coverage: ObjectValue;
  totals: Record<string, EvidenceNumber>; rows: PerformanceRow[]; total_scope: string;
}
export interface StrategyItem {
  id: string; kind: string; title: string; evidence_ids: string[]; target: string | null;
  action: { kind: "brief"; payload: ObjectValue } | { kind: "inbox"; revision_id: string } | null;
  unavailable_reason: string | null; autonomy: string; measurement: string; phase: string;
  priority: { score: number; impact_proxy: number; confidence: number; effort_days: number; effort_factor: number; inputs: ObjectValue; formula: string };
}
export interface EffectivenessGroup {
  work_type: string; recipe_key: string | null; horizon: number; sample_size: number;
  effective_sample_size: number; shrinkage: number; factor: number; evidence_ids: string[];
  metrics: Record<string, {sample_size: number; effective_sample_size: number; mean_delta: number | null; shrunk_delta: number | null}>;
}
interface DecayWindow {
  state: string; reason: string; window: {start: string; end: string}; evidence_ids: string[];
  coverage: ObjectValue | null; daily: {clicks: number; impressions: number}[] | null;
}
interface DecayMetric {baseline: number; recent: number; relative_decline: number | null; noise: number; minimum_volume: number; declining: boolean}
interface DecayComparison {state: string; baseline: DecayWindow; recent: DecayWindow; metrics: Record<string, DecayMetric> | null}
export interface ObservedLearning {
  version: "site-observed-learning-v1";
  effectiveness: {state: string; label: string; reason: string; excluded_measurements: number; groups: EffectivenessGroup[]};
  decay: {state: string; label: string; reason: string; excluded_pages: string[]; pages: {
    url: string; state: string; basis: string; seasonality_possible: boolean;
    year_over_year_reason: string | null; declining: boolean; evidence_ids: string[];
    prior_28_days: DecayComparison; year_over_year: DecayComparison | null;
  }[]};
}
export interface TopicVolume {
  value: number | null; provider: "dataforseo"; label: string;
  location_code: number; language_code: string; recorded_at: string; evidence_ids: string[];
}
export interface TopicCluster {
  id: string; title: string; source: "gsc" | "bing"; evidence_ids: string[];
  window: PerformanceCohort["window"]; coverage: ObjectValue; dimensions: string[];
  members: {query: string; variants: string[]; tokens: string[]; membership: {shared_terms: string[]; shared_bigrams: string[][]; term_jaccard: number}; volumes: TopicVolume[]}[];
  metrics: {clicks: number; impressions: number; average_position: number | null; evidence_ids: string[]};
  ranking_pages: string[] | null; targeting_pages: string[]; targeting_assessed: boolean;
  gaps: string[]; ideas: {cluster_id: string; query: string; label: "idea, no volume data"; volume: null; evidence_ids: string[]; volumes: TopicVolume[]; strategy_item_id: string | null}[];
  strategy_item_id: string | null;
}
export interface KeywordTopics {
  version: "keyword-topics-v1"; clusters: TopicCluster[];
  unavailable: {source: string; reason: string}[];
  ideas_status: "unavailable" | "available"; ideas_reason: string;
  volume_status: "available" | "unavailable"; scope: string;
  policy: {high_impressions_minimum: number; weak_position_minimum: number; clustering: string};
}
export interface SeoSnapshot {
  id: string; version: number; created_at: string; sha256: string;
  payload: {
    version: string; headline: Record<string, EvidenceNumber>;
    unavailable: { source: string; reason: string }[];
    evidence: Record<string, { kind: string; record: ObjectValue }>;
    sources: ObjectValue;
    pages: { url: string; title: string; evidence_id: string; findings: { id: string; title: string; severity: string; source_id: string }[]; metrics: { source: string; window: PerformanceCohort["window"]; row: PerformanceRow }[] }[];
    performance: PerformanceCohort[];
    learning?: ObservedLearning;
    topics?: KeywordTopics;
    strategy: { horizon_days: number; items: StrategyItem[]; decision: { provider: string; fallback: boolean; reason: string; version: string } };
  };
}
export interface SeoProjection {
  schema_version: 1; snapshot: SeoSnapshot | null;
  decisions: { item_id: string; decision: "accepted" | "dismissed"; target_id: string | null; target_kind: "brief" | "inbox" | null; decided_at: string }[];
}
const object = (v: unknown): v is ObjectValue => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: unknown, keys: string[]): v is ObjectValue => object(v) && Object.keys(v).sort().join(",") === [...keys].sort().join(",");
const text = (v: unknown, max = 4000): v is string => typeof v === "string" && v.length <= max && !v.includes("\0");
const finite = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0;
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.length <= 10000 && v.every(uuid);
const array = (v: unknown, max = 10000): v is unknown[] => Array.isArray(v) && v.length <= max;
const timestamp = (v: unknown) => text(v, 128) && !Number.isNaN(Date.parse(v));
const windowValid = (v: unknown) => v === null || exact(v, ["start", "end"]) && [v.start,v.end].every(x => text(x, 10) && /^\d{4}-\d{2}-\d{2}$/.test(x));
function metrics(v: unknown, refs: Set<string>): boolean {
  return object(v) && Object.values(v).every(m => exact(m,["value","evidence_ids"]) && finite(m.value) && ids(m.evidence_ids) && m.evidence_ids.length > 0 && m.evidence_ids.every(id => refs.has(id)));
}
function row(v: unknown, refs: Set<string>): boolean {
  return exact(v,["labels","metrics"]) && object(v.labels) && Object.values(v.labels).every(x => text(x,2048)) && object(v.metrics) && "clicks" in v.metrics && "impressions" in v.metrics && metrics(v.metrics,refs);
}
function validUrl(v: unknown): boolean {
  if (!text(v,2048)) return false;
  try { const u = new URL(v); return ["https:","http:"].includes(u.protocol) && !u.username && !u.password; } catch { return false; }
}
function validLearning(v: unknown, refs: Set<string>): boolean {
  const references = (value: unknown) => ids(value) && value.every(id => refs.has(id));
  const signed = (value: unknown) => typeof value === "number" && Number.isFinite(value);
  const nullable = (value: unknown) => value === null || signed(value);
  const count = (value: unknown) => finite(value) && Number.isSafeInteger(value);
  const state = (value: unknown) => ["partial","unavailable","as_reported"].includes(String(value));
  function window(value: unknown): boolean {
    return exact(value,["state","reason","window","evidence_ids","coverage","daily"]) && state(value.state) && text(value.reason,512) && value.window !== null && windowValid(value.window) && references(value.evidence_ids) && (value.coverage === null || object(value.coverage)) && (value.daily === null || array(value.daily,28) && value.daily.length === 28 && value.daily.every(d => exact(d,["clicks","impressions"]) && finite(d.clicks) && finite(d.impressions))) && (value.state === "as_reported" ? value.daily !== null : value.daily === null);
  }
  function comparison(value: unknown): boolean {
    return exact(value,["state","baseline","recent","metrics"]) && state(value.state) && window(value.baseline) && window(value.recent) && (value.metrics === null || exact(value.metrics,["clicks","impressions"]) && Object.values(value.metrics).every(m => exact(m,["baseline","recent","relative_decline","noise","minimum_volume","declining"]) && [m.baseline,m.recent,m.noise,m.minimum_volume].every(finite) && nullable(m.relative_decline) && typeof m.declining === "boolean")) && (value.state === "as_reported" ? value.metrics !== null : value.metrics === null);
  }
  if (!exact(v,["version","effectiveness","decay"]) || v.version !== "site-observed-learning-v1") return false;
  const e = v.effectiveness, d = v.decay;
  if (!exact(e,["state","label","reason","excluded_measurements","groups"]) || !state(e.state) || !text(e.label,512) || !text(e.reason,512) || !count(e.excluded_measurements) || !array(e.groups)) return false;
  if (!e.groups.every(g => exact(g,["work_type","recipe_key","horizon","sample_size","effective_sample_size","shrinkage","factor","metrics","evidence_ids"]) && text(g.work_type,64) && (g.recipe_key === null || text(g.recipe_key,128)) && [28,90].includes(Number(g.horizon)) && count(g.sample_size) && finite(g.effective_sample_size) && finite(g.shrinkage) && Number(g.shrinkage) <= 1 && signed(g.factor) && Number(g.factor) >= .8 && Number(g.factor) <= 1.2 && references(g.evidence_ids) && exact(g.metrics,["clicks","impressions","ctr","position"]) && Object.values(g.metrics).every(m => exact(m,["sample_size","effective_sample_size","mean_delta","shrunk_delta"]) && count(m.sample_size) && finite(m.effective_sample_size) && nullable(m.mean_delta) && nullable(m.shrunk_delta)))) return false;
  return exact(d,["state","label","reason","excluded_pages","pages"]) && state(d.state) && text(d.label,512) && text(d.reason,512) && array(d.excluded_pages) && d.excluded_pages.every(validUrl) && array(d.pages) && d.pages.every(p => {
    if (!(exact(p,["url","state","basis","seasonality_possible","year_over_year_reason","declining","evidence_ids","prior_28_days","year_over_year"]) && validUrl(p.url) && state(p.state) && ["year_over_year","prior_28_days"].includes(String(p.basis)) && typeof p.seasonality_possible === "boolean" && (p.year_over_year_reason === null || text(p.year_over_year_reason,512)) && typeof p.declining === "boolean" && references(p.evidence_ids) && comparison(p.prior_28_days) && (p.year_over_year === null || comparison(p.year_over_year)))) return false;
    const chosen = p.basis === "year_over_year" ? p.year_over_year : p.prior_28_days;
    return object(chosen) && (p.basis !== "year_over_year" || chosen.state === "as_reported") && p.state === chosen.state && p.seasonality_possible === (p.basis === "prior_28_days") && p.declining === (object(chosen.metrics) && Object.values(chosen.metrics).some(m => object(m) && m.declining === true));
  });
}
function validTopics(v: unknown, refs: Set<string>, items: Set<string>): boolean {
  const strings = (x: unknown, maximum = 5000): x is string[] => array(x,maximum) && x.every(s => text(s,2048));
  const evidence = (x: unknown) => ids(x) && x.length > 0 && x.every(id => refs.has(id));
  const pages = (x: unknown) => array(x,1000) && x.every(validUrl);
  if (!exact(v,["version","clusters","unavailable","ideas_status","ideas_reason","volume_status","scope","policy"]) || v.version !== "keyword-topics-v1" || !["available","unavailable"].includes(String(v.ideas_status)) || !text(v.ideas_reason,512) || !["available","unavailable"].includes(String(v.volume_status)) || !text(v.scope,1024) || !exact(v.policy,["high_impressions_minimum","weak_position_minimum","clustering"]) || !finite(v.policy.high_impressions_minimum) || !finite(v.policy.weak_position_minimum) || !text(v.policy.clustering,1024) || !array(v.unavailable,32) || !v.unavailable.every(u => exact(u,["source","reason"]) && text(u.source,64) && text(u.reason,512)) || !array(v.clusters,5000)) return false;
  const clusterIds = new Set<string>();
  return v.clusters.every(c => {
    if (!exact(c,["id","title","source","evidence_ids","window","coverage","dimensions","members","metrics","ranking_pages","targeting_pages","targeting_assessed","gaps","ideas","strategy_item_id"]) || !uuid(c.id) || clusterIds.has(c.id) || !text(c.title,4000) || !["gsc","bing"].includes(String(c.source)) || !evidence(c.evidence_ids) || !windowValid(c.window) || !object(c.coverage) || !strings(c.dimensions,5) || !c.dimensions.includes("query") || !exact(c.metrics,["clicks","impressions","average_position","evidence_ids"]) || !finite(c.metrics.clicks) || !finite(c.metrics.impressions) || !(c.metrics.average_position === null || finite(c.metrics.average_position)) || !evidence(c.metrics.evidence_ids) || !(c.ranking_pages === null || pages(c.ranking_pages)) || !pages(c.targeting_pages) || typeof c.targeting_assessed !== "boolean" || !strings(c.gaps,2) || !c.gaps.every(g => ["high_impressions_weak_position","no_observed_page_targeting"].includes(g)) || !(c.strategy_item_id === null || uuid(c.strategy_item_id) && items.has(c.strategy_item_id)) || !array(c.members,5000) || !c.members.length || !array(c.ideas,20)) return false;
    clusterIds.add(c.id);
    const volumes = (v: unknown) => array(v,100) && v.every(vol => exact(vol,["value","provider","label","location_code","language_code","recorded_at","evidence_ids"]) && (vol.value === null || finite(vol.value)) && vol.provider === "dataforseo" && vol.label === "provider-reported search volume, not site impressions" && Number.isSafeInteger(vol.location_code) && text(vol.language_code,8) && timestamp(vol.recorded_at) && evidence(vol.evidence_ids));
    if (!c.ideas.every(i => exact(i,["cluster_id","query","label","volume","evidence_ids","volumes","strategy_item_id"]) && i.cluster_id === c.id && text(i.query,200) && i.label === "idea, no volume data" && i.volume === null && evidence(i.evidence_ids) && volumes(i.volumes) && (i.strategy_item_id === null || uuid(i.strategy_item_id) && items.has(i.strategy_item_id)))) return false;
    return c.members.every(m => exact(m,["query","variants","tokens","membership","volumes"]) && text(m.query,4000) && strings(m.variants) && m.variants.length > 0 && strings(m.tokens) && exact(m.membership,["shared_terms","shared_bigrams","term_jaccard"]) && strings(m.membership.shared_terms) && array(m.membership.shared_bigrams,5000) && m.membership.shared_bigrams.every(g => strings(g,2) && g.length === 2) && finite(m.membership.term_jaccard) && Number(m.membership.term_jaccard) <= 1 && array(m.volumes,100) && m.volumes.every(vol => exact(vol,["value","provider","label","location_code","language_code","recorded_at","evidence_ids"]) && (vol.value === null || finite(vol.value)) && vol.provider === "dataforseo" && vol.label === "provider-reported search volume, not site impressions" && Number.isSafeInteger(vol.location_code) && text(vol.language_code,8) && timestamp(vol.recorded_at) && evidence(vol.evidence_ids)));
  });
}
export function validSeoProjection(v: unknown): v is SeoProjection {
  if (!exact(v,["schema_version","snapshot","decisions"]) || v.schema_version !== 1 || !array(v.decisions)) return false;
  if (!v.decisions.every(d => exact(d,["item_id","decision","target_id","target_kind","decided_at"]) && uuid(d.item_id) && ["accepted","dismissed"].includes(String(d.decision)) && (d.target_id === null || uuid(d.target_id)) && (d.target_kind === null || ["brief","inbox"].includes(String(d.target_kind))) && timestamp(d.decided_at))) return false;
  if (v.snapshot === null) return v.decisions.length === 0;
  const s = v.snapshot;
  if (!exact(s,["id","version","created_at","sha256","payload"]) || !uuid(s.id) || !Number.isSafeInteger(s.version) || Number(s.version) < 1 || !timestamp(s.created_at) || !text(s.sha256,64) || !/^[0-9a-f]{64}$/.test(s.sha256)) return false;
  const p = s.payload;
  if (!object(p) || !exact(p,["version","headline","unavailable","sources","evidence","pages","performance","strategy", ...("learning" in p ? ["learning"] : []), ...("topics" in p ? ["topics"] : [])]) || p.version !== "seo-baseline-strategy-v1" || !object(p.sources) || !object(p.evidence)) return false;
  const refs = new Set(Object.keys(p.evidence));
  if (![...refs].every(uuid) || !Object.values(p.evidence).every(r => exact(r,["kind","record"]) && text(r.kind,64) && object(r.record)) || !metrics(p.headline,refs)) return false;
  if ("learning" in p && !validLearning(p.learning,refs)) return false;
  if (!Object.values(p.sources).every(source => !object(source) || !("records" in source) || exact(source,["reason","records"]) && (source.reason === null || text(source.reason,512)) && array(source.records,10000) && source.records.every(r => object(r) && uuid(r.id) && refs.has(r.id)))) return false;
  if (!array(p.unavailable,32) || !p.unavailable.every(u => exact(u,["source","reason"]) && text(u.source,64) && text(u.reason,512))) return false;
  if (!array(p.performance,32) || !p.performance.every(c => exact(c,["source","evidence_id","window","coverage","dimensions","totals","rows","total_scope"]) && ["gsc","bing"].includes(String(c.source)) && uuid(c.evidence_id) && refs.has(c.evidence_id) && windowValid(c.window) && object(c.coverage) && array(c.dimensions,5) && c.dimensions.every(d => ["query","page","date","country","device"].includes(String(d))) && metrics(c.totals,refs) && array(c.rows,5000) && c.rows.every(r => row(r,refs)) && text(c.total_scope))) return false;
  if (!array(p.pages,1000) || !p.pages.every(page => exact(page,["url","title","evidence_id","findings","metrics"]) && validUrl(page.url) && text(page.title,512) && uuid(page.evidence_id) && refs.has(page.evidence_id) && array(page.findings) && page.findings.every(f => object(f) && uuid(f.id) && text(f.title,512) && ["critical","high","medium","low","info"].includes(String(f.severity)) && uuid(f.source_id) && refs.has(f.source_id)) && array(page.metrics) && page.metrics.every(m => exact(m,["source","window","row"]) && ["gsc","bing"].includes(String(m.source)) && windowValid(m.window) && row(m.row,refs)))) return false;
  const strategy = p.strategy;
  if (!exact(strategy,["horizon_days","items","decision"]) || strategy.horizon_days !== 90 || !exact(strategy.decision,["provider","fallback","reason","version"]) || strategy.decision.provider !== "deterministic_fallback" || strategy.decision.fallback !== true || strategy.decision.reason !== "MODEL_REORDERING_UNCONFIGURED" || strategy.decision.version !== p.version || !array(strategy.items,20000)) return false;
  if ("topics" in p && !validTopics(p.topics,refs,new Set(strategy.items.flatMap(i => object(i) && typeof i.id === "string" ? [i.id] : [])))) return false;
  return strategy.items.every(i => {
    if (!exact(i,["id","kind","title","evidence_ids","priority","target","action","unavailable_reason","autonomy","measurement","phase"]) || !uuid(i.id) || !["technical","content","business_gap","ai_visibility","decay","internal_link"].includes(String(i.kind)) || !text(i.title) || !ids(i.evidence_ids) || !i.evidence_ids.length || !i.evidence_ids.every(id => refs.has(id)) || !(i.target === null || validUrl(i.target)) || !(i.unavailable_reason === null || text(i.unavailable_reason,512)) || !text(i.autonomy,512) || !text(i.measurement,512) || !["days_1_30","days_31_60","days_61_90","backlog"].includes(String(i.phase))) return false;
    const priority = i.priority;
    if (!exact(priority,["score","impact_proxy","confidence","effort_days","effort_factor","inputs","formula"]) || ![priority.score,priority.impact_proxy,priority.confidence,priority.effort_days,priority.effort_factor].every(finite) || !object(priority.inputs) || !text(priority.formula,128)) return false;
    if ("observed_effectiveness" in priority.inputs) {
      const o = priority.inputs.observed_effectiveness;
      if (!exact(o,["label","factor","groups","bounds","basis"]) || !text(o.label,512) || !finite(o.factor) || Number(o.factor) < .8 || Number(o.factor) > 1.2 || !array(o.groups) || !array(o.bounds,2) || o.bounds[0] !== .8 || o.bounds[1] !== 1.2 || !text(o.basis,512)) return false;
    }
    return i.action === null || exact(i.action,["kind","revision_id"]) && i.action.kind === "inbox" && uuid(i.action.revision_id) || exact(i.action,["kind","payload"]) && i.action.kind === "brief" && object(i.action.payload);
  });
}
export function strategyCommand(v: unknown): { siteId: string; action: string; body: ObjectValue } | null {
  if (!object(v) || !uuid(v.site_id) || v.schema_version !== 1) return null;
  if (v.action === "refresh" && exact(v,["site_id","schema_version","action"])) return { siteId:v.site_id, action:"refresh", body:{schema_version:1} };
  if (v.action === "ideas" && exact(v,["site_id","schema_version","action","snapshot_id"]) && uuid(v.snapshot_id)) return { siteId:v.site_id, action:"ideas", body:{schema_version:1,snapshot_id:v.snapshot_id} };
  if (v.action === "decide" && exact(v,["site_id","schema_version","action","snapshot_id","item_id","decision"]) && uuid(v.snapshot_id) && uuid(v.item_id) && ["accepted","dismissed"].includes(String(v.decision))) return { siteId:v.site_id, action:"decide", body:{schema_version:1,snapshot_id:v.snapshot_id,item_id:v.item_id,decision:v.decision} };
  return null;
}
export async function readStrategyJson(message: Pick<Response,"body">, maximum: number): Promise<unknown> {
  return JSON.parse(await boundedRelayText(message, maximum));
}
export async function relayStrategy(token: string, siteId: string, origin: string, command?: ReturnType<typeof strategyCommand>, snapshotId?: string | null, evidenceId?: string | null): Promise<Response> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token) || !uuid(siteId) || snapshotId && !uuid(snapshotId) || evidenceId && (!uuid(evidenceId) || !snapshotId)) return Response.json({state:"rejected"},{status:403});
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const cookie = `${TENANT_COOKIE_NAME}=${token}`;
    let csrf = "";
    if (command) {
      const r = await relayJson(new URL("/v1/session/tenant-csrf",base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(5000),headers:{Cookie:cookie,Accept:"application/json"}}, globalThis.fetch, 3000000);
      const v = await readStrategyJson(r,4096);
      if (r.status !== 200 || r.headers.getSetCookie().length || !exact(v,["schema_version","csrf_token"]) || v.schema_version !== 1 || !text(v.csrf_token,43) || !/^[A-Za-z0-9_-]{43}$/.test(v.csrf_token)) throw new Error();
      csrf = v.csrf_token;
    }
    const path = `/v1/sites/${siteId}/seo-strategy${command ? `/${command.action}` : evidenceId ? `/${snapshotId}/evidence/${evidenceId}` : snapshotId ? `?snapshot_id=${snapshotId}` : ""}`;
    const r = await relayJson(new URL(path,base),{method:command ? "POST":"GET",cache:"no-store",redirect:"error",signal:AbortSignal.timeout(15000),headers:{Cookie:cookie,Accept:"application/json",...(command ? {Origin:origin,"Sec-Fetch-Site":"same-origin","X-CSRF-Token":csrf,"Content-Type":"application/json"}:{})},...(command ? {body:JSON.stringify(command.body)}:{})}, globalThis.fetch, 3000000);
    if (r.headers.getSetCookie().length || !r.headers.get("content-type")?.startsWith("application/json")) throw new Error();
    const v = await readStrategyJson(r,16_000_000);
    if (r.ok) {
      if (command ? !object(v) || v.schema_version !== 1 || !["recorded","replayed","accepted","dismissed"].includes(String(v.state)) : evidenceId ? !exact(v,["schema_version","snapshot_id","evidence_id","kind","record"]) || v.schema_version !== 1 || v.snapshot_id !== snapshotId || v.evidence_id !== evidenceId || !text(v.kind,64) || !object(v.record) : !validSeoProjection(v)) throw new Error();
    } else if (!exact(v,["error"]) || !exact(v.error,["code","message","retryable","correlation_id"]) || !text(v.error.code,64) || !text(v.error.message,200) || typeof v.error.retryable !== "boolean" || !text(v.error.correlation_id,64)) throw new Error();
    return Response.json(v,{status:r.status,headers:{"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
  } catch { return Response.json({state:"unavailable"},{status:503}); }
}
