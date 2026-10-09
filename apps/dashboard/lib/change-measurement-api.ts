export type MeasurementMetrics = { clicks: number | null; impressions: number | null; ctr: number | null; position: number | null };
export type MeasurementSource = { state: string; reason: string; generation_id: string | null; coverage: Record<string, unknown> | null; metrics: MeasurementMetrics | null };
export type MeasurementSources = { gsc_page: MeasurementSource; bing_site_context: MeasurementSource; bing_page: MeasurementSource };
export type NewPageBaseline = MeasurementSources & { state: "new_page"; reason: "NO_PRE_CHANGE_WINDOW" };
export interface ChangeMeasurement {
  operation_id: string; horizon: number; page_url: string; verified_live_at: string; due_at: string;
  baseline_start: string | null; baseline_end: string | null; baseline: MeasurementSources | NewPageBaseline;
  post_start: string; post_end: string; evidence_url: string; verification_attempt_id: string;
  observation: { state: string; reason: string; post: MeasurementSources | null;
    observed_change: { gsc_page: MeasurementMetrics; bing_site_context: MeasurementMetrics; bing_page?: MeasurementMetrics | null } | null;
    confounders: { operation_id: string; verified_live_at: string; page_url: string }[] };
  recorded_at: string | null;
}
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: unknown, keys: string[]): v is Record<string, unknown> => object(v) && Object.keys(v).sort().join(",") === [...keys].sort().join(",");
const identifier = (v: unknown) => typeof v === "string" && ID.test(v);
const timestamp = (v: unknown) => typeof v === "string" && Number.isFinite(Date.parse(v));
const metrics = (v: unknown) => exact(v, ["clicks", "impressions", "ctr", "position"]) && Object.values(v).every(n => n === null || typeof n === "number" && Number.isFinite(n));
const source = (v: unknown) => {
  if (!exact(v, ["state", "reason", "generation_id", "coverage", "metrics"]) ||
    !["awaiting_data", "measured_as_reported", "unavailable", "failed"].includes(String(v.state)) ||
    typeof v.reason !== "string" || !(v.generation_id === null || identifier(v.generation_id)) ||
    !(v.coverage === null || object(v.coverage)) || !(v.metrics === null || metrics(v.metrics))) return false;
  return v.state === "measured_as_reported" ? v.generation_id !== null && object(v.coverage) && v.coverage.complete === false && v.metrics !== null : v.metrics === null;
};
const sources = (v: unknown) => exact(v, ["gsc_page", "bing_site_context", "bing_page"]) && Object.values(v).every(source) && object(v.bing_page) &&
  (v.bing_page.metrics === null || object(v.bing_page.metrics) && v.bing_page.metrics.ctr === null && v.bing_page.metrics.position === null && object(v.bing_page.coverage) && v.bing_page.coverage.coverage === "provider_returned_top_pages" && v.bing_page.coverage.date_granularity === "unknown") &&
  (!object(v.bing_site_context) || v.bing_site_context.metrics === null || object(v.bing_site_context.metrics) && v.bing_site_context.metrics.position === null && v.bing_site_context.metrics.ctr === null);
const newPageBaseline = (v: unknown) => exact(v, ["state", "reason", "gsc_page", "bing_site_context", "bing_page"]) &&
  v.state === "new_page" && v.reason === "NO_PRE_CHANGE_WINDOW" && [v.gsc_page, v.bing_site_context, v.bing_page].every(s =>
    source(s) && object(s) && s.state === "unavailable" && s.reason === "NO_PRE_CHANGE_WINDOW" && s.generation_id === null && s.coverage === null && s.metrics === null);
const baseline = (v: Record<string, unknown>) => newPageBaseline(v.baseline) ? v.baseline_start === null && v.baseline_end === null :
  sources(v.baseline) && [v.baseline_start, v.baseline_end].every(d => typeof d === "string" && DATE.test(d));

export function parseChangeMeasurements(value: unknown): ChangeMeasurement[] | null {
  if (!Array.isArray(value) || value.length > 1000) return null;
  for (const v of value) {
    if (!exact(v, ["operation_id", "horizon", "page_url", "verified_live_at", "due_at", "baseline_start", "baseline_end", "baseline", "post_start", "post_end", "evidence_url", "verification_attempt_id", "observation", "recorded_at"]) ||
      !identifier(v.operation_id) || !identifier(v.verification_attempt_id) || typeof v.horizon !== "number" || ![7, 28, 90].includes(v.horizon) ||
      typeof v.page_url !== "string" || !/^https:\/\/[^\s]+$/.test(v.page_url) ||
      !timestamp(v.verified_live_at) || !timestamp(v.due_at) || !(v.recorded_at === null || timestamp(v.recorded_at)) ||
      ![v.post_start, v.post_end].every(d => typeof d === "string" && DATE.test(d)) ||
      !baseline(v) || v.evidence_url !== `/changes#operation-${v.operation_id}` ||
      !exact(v.observation, ["state", "reason", "post", "observed_change", "confounders"])) return null;
    const o = v.observation;
    if (!["not_yet_due", "awaiting_data", "measured_as_reported", "unavailable", "failed"].includes(String(o.state)) ||
      typeof o.reason !== "string" || !(o.post === null || sources(o.post)) ||
      !(o.observed_change === null || (exact(o.observed_change, ["gsc_page", "bing_site_context"]) || exact(o.observed_change, ["gsc_page", "bing_site_context", "bing_page"])) && Object.entries(o.observed_change).every(([key, value]) => key === "bing_page" && value === null || metrics(value))) ||
      !Array.isArray(o.confounders) || !o.confounders.every(c => exact(c, ["operation_id", "verified_live_at", "page_url"]) && identifier(c.operation_id) && timestamp(c.verified_live_at) && typeof c.page_url === "string") ||
      o.state === "measured_as_reported" && (!object(o.post) || !object(o.post.gsc_page) || o.post.gsc_page.state !== "measured_as_reported")) return null;
    if (newPageBaseline(v.baseline) && o.observed_change !== null && object(o.observed_change) &&
      Object.values(o.observed_change).some(m => object(m) && Object.values(m).some(n => n !== null))) return null;
  }
  return value as ChangeMeasurement[];
}

export interface NextWork { state: "available" | "unavailable"; source: "weekly_backlog"; reason: string | null; items: { revision_sha256: string; next_week: string; reason: string; resolved_week: string | null }[] }
export interface NeedsDecision { count: number; url: "/approvals"; items: { revision_id: string; kind: "technical" | "editorial"; url: string }[] }
export function parseReportNext(value: unknown): NextWork | null {
  if (!exact(value, ["state", "source", "reason", "items"]) || !["available", "unavailable"].includes(String(value.state)) || value.source !== "weekly_backlog" || !(value.reason === null || typeof value.reason === "string") ||
    !Array.isArray(value.items) || !value.items.every(i => exact(i, ["revision_sha256", "next_week", "reason", "resolved_week"]) && typeof i.revision_sha256 === "string" && /^[0-9a-f]{64}$/.test(i.revision_sha256) && typeof i.next_week === "string" && DATE.test(i.next_week) && typeof i.reason === "string" && (i.resolved_week === null || typeof i.resolved_week === "string" && DATE.test(i.resolved_week)))) return null;
  return value as unknown as NextWork;
}
export function parseReportDecisions(value: unknown): NeedsDecision | null {
  if (!exact(value, ["count", "url", "items"]) || value.url !== "/approvals" || !Array.isArray(value.items) || value.count !== value.items.length ||
    !value.items.every(i => exact(i, ["revision_id", "kind", "url"]) && identifier(i.revision_id) && ["technical", "editorial"].includes(String(i.kind)) && i.url === (i.kind === "technical" ? `/approvals?revision=${i.revision_id}` : "/approvals"))) return null;
  return value as unknown as NeedsDecision;
}
