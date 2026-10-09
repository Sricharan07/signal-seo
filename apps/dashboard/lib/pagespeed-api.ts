import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { validBrandDocumentId as uuid } from "./brand-document-api";

export interface SpeedMetric {
  state: "available" | "unavailable"; reason: string | null; value: number | null;
  unit: "ms" | "score"; rating?: "good" | "needs_improvement" | "poor" | null;
}
export interface FieldExperience {
  source: "url" | "origin"; locator: string; percentile: 75;
  collection_period: { state: "available" | "unavailable"; reason: string | null; first_date: string | null; last_date: string | null };
  metrics: Record<"lcp" | "inp" | "cls", SpeedMetric>;
  status: "good" | "needs_improvement" | "poor" | "unavailable";
}
export interface SpeedObservation {
  evidence_id: string; url: string; strategy: "mobile" | "desktop"; lighthouse_version: string;
  lab: { source: "psi_lighthouse"; status: string; reason: string | null; run_at: string; performance_score: number | null; metrics: Record<"lcp" | "fcp" | "tbt" | "speed_index" | "cls", SpeedMetric> };
  field_url: FieldExperience; field_origin: FieldExperience; fetched_at: string; response_sha256: string;
  findings: { id: string; key: string; title: string; summary: string; severity: "medium"; resource_locator: string; source_kind: "pagespeed_observation"; source_id: string }[];
}
export interface SpeedSample {
  sample_id: string; url: string; strategy: "mobile" | "desktop"; week_start: string;
  selection_source: "gsc_clicks" | "crawl_order"; observation: SpeedObservation | null;
  reason: string | null; state: "observed" | "scheduled" | "unavailable" | "outcome_unknown";
}
export interface PageSpeedData {
  schema_version: 1; site_id: string; outcome: "found"; samples: SpeedSample[];
  daily_request_cap: 4; weekly_page_cap: 5; local_lighthouse: "unavailable";
}
function exact(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}
const text = (value: unknown, max: number) => typeof value === "string" && value.length > 0 && value.length <= max && !/[\x00-\x1f\x7f]/.test(value);
const timestamp = (value: unknown) => typeof value === "string" && /^\d{4}-\d\d-\d\dT.*(?:Z|[+]00:00)$/.test(value) && Number.isFinite(Date.parse(value));
const day = (value: unknown) => typeof value === "string" && /^\d{4}-\d\d-\d\d$/.test(value) && new Date(value).toISOString().slice(0, 10) === value;
function page(value: unknown): value is string {
  try { if (!text(value, 2048)) return false; const url = new URL(value as string); return url.protocol === "https:" && !url.username && !url.password && !url.hash && url.href === value; } catch { return false; }
}
function metric(value: unknown, field: boolean, unit: string): boolean {
  if (!exact(value, ["state", "reason", "value", "unit", ...(field ? ["rating"] : [])]) || value.unit !== unit) return false;
  if (value.state === "unavailable") return value.value === null && text(value.reason, 64) && (!field || value.rating === null);
  return value.state === "available" && value.reason === null && typeof value.value === "number" && Number.isFinite(value.value) && value.value >= 0 && (!field || ["good", "needs_improvement", "poor"].includes(String(value.rating)));
}
function field(value: unknown, source: string, locator: string): boolean {
  if (!exact(value, ["source", "locator", "percentile", "collection_period", "metrics", "status"]) || value.source !== source || value.locator !== locator || value.percentile !== 75 || !["good", "needs_improvement", "poor", "unavailable"].includes(String(value.status))) return false;
  const period = value.collection_period;
  if (!exact(period, ["state", "reason", "first_date", "last_date"])) return false;
  if (period.state === "unavailable" ? period.reason !== "collection_period_not_reported" || period.first_date !== null || period.last_date !== null : period.state !== "available" || period.reason !== null || !day(period.first_date) || !day(period.last_date) || String(period.first_date) > String(period.last_date)) return false;
  if (!exact(value.metrics, ["lcp", "inp", "cls"])) return false;
  return Object.entries(value.metrics).every(([name, item]) => metric(item, true, name === "cls" ? "score" : "ms"));
}
function observation(value: unknown, sample: Record<string, unknown>): boolean {
  if (!exact(value, ["evidence_id", "url", "strategy", "lighthouse_version", "lab", "field_url", "field_origin", "fetched_at", "response_sha256", "findings"]) || value.evidence_id !== sample.sample_id || value.url !== sample.url || value.strategy !== sample.strategy || typeof value.lighthouse_version !== "string" || !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(value.lighthouse_version) || !timestamp(value.fetched_at) || typeof value.response_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.response_sha256)) return false;
  if (!field(value.field_url, "url", String(sample.url)) || !field(value.field_origin, "origin", new URL(String(sample.url)).origin)) return false;
  const lab = value.lab;
  if (!exact(lab, ["source", "status", "reason", "run_at", "performance_score", "metrics"]) || lab.source !== "psi_lighthouse" || !timestamp(lab.run_at) || !["available", "unavailable"].includes(String(lab.status)) || !(lab.reason === null || lab.reason === "lighthouse_runtime_error") || !(lab.performance_score === null || typeof lab.performance_score === "number" && Number.isFinite(lab.performance_score) && lab.performance_score >= 0 && lab.performance_score <= 1) || !exact(lab.metrics, ["lcp", "fcp", "tbt", "speed_index", "cls"]) || !Object.entries(lab.metrics).every(([name, item]) => metric(item, false, name === "cls" ? "score" : "ms"))) return false;
  return Array.isArray(value.findings) && value.findings.length <= 6 && value.findings.every((item) => exact(item, ["id", "key", "title", "summary", "severity", "resource_locator", "source_kind", "source_id"]) && uuid(item.id) && item.source_id === value.evidence_id && item.source_kind === "pagespeed_observation" && item.severity === "medium" && typeof item.key === "string" && /^performance\.cwv\.(url|origin)\.(lcp|inp|cls)\.poor$/.test(item.key) && text(item.title, 160) && text(item.summary, 500) && page(item.resource_locator + (item.resource_locator === new URL(String(sample.url)).origin ? "/" : "")));
}
export function validPageSpeed(value: unknown, siteId: string): value is PageSpeedData {
  try {
    if (!exact(value, ["schema_version", "site_id", "outcome", "samples", "daily_request_cap", "weekly_page_cap", "local_lighthouse"]) || value.schema_version !== 1 || value.site_id !== siteId || !uuid(siteId) || value.outcome !== "found" || value.daily_request_cap !== 4 || value.weekly_page_cap !== 5 || value.local_lighthouse !== "unavailable" || !Array.isArray(value.samples) || value.samples.length > 10) return false;
    return new Set(value.samples.map((item) => item.sample_id)).size === value.samples.length && value.samples.every((sample) => exact(sample, ["sample_id", "url", "strategy", "week_start", "selection_source", "observation", "reason", "state"]) && uuid(sample.sample_id) && page(sample.url) && ["mobile", "desktop"].includes(String(sample.strategy)) && day(sample.week_start) && ["gsc_clicks", "crawl_order"].includes(String(sample.selection_source)) && ["observed", "scheduled", "unavailable", "outcome_unknown"].includes(String(sample.state)) && (sample.reason === null || ["PSI_RESPONSE_REJECTED", "PSI_RATE_LIMITED", "PSI_PROVIDER_UNAVAILABLE", "PSI_CREDENTIAL_REJECTED", "PSI_TRANSPORT_UNAVAILABLE"].includes(String(sample.reason))) && (sample.state === "observed" ? sample.reason === null && observation(sample.observation, sample) : sample.observation === null));
  } catch { return false; }
}
export async function relayPageSpeed(token: string, siteId: string): Promise<Response> {
  const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
  if (!/^[A-Za-z0-9_-]{43}$/.test(token) || !uuid(siteId)) return Response.json({ state: "rejected" }, { status: 403, headers });
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const response = await relayJson(new URL(`/v1/sites/${siteId}/performance`, base), { cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000), headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${token}` } }, globalThis.fetch, 400000);
    if (!response.ok) return Response.json({ state: response.status === 403 || response.status === 401 ? "rejected" : "unavailable" }, { status: response.status === 403 || response.status === 401 ? 403 : 503, headers });
    if (response.headers.getSetCookie().length || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") throw new Error();
    const raw = await boundedRelayText(response, 400_000);
    if (new TextEncoder().encode(raw).length > 400_000) throw new Error();
    const value: unknown = JSON.parse(raw);
    if (!validPageSpeed(value, siteId)) throw new Error();
    return Response.json(value, { headers });
  } catch { return Response.json({ state: "unavailable" }, { status: 503, headers }); }
}
