import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { parseChangeMeasurements, parseReportNext, parseReportDecisions, type ChangeMeasurement, type NextWork, type NeedsDecision } from "./change-measurement-api";

const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const CODE = /^[A-Z][A-Z0-9_]{0,127}$/;
const STAGES = ["observe", "analyze", "plan", "prepare", "gate", "handoff", "verify", "measure", "report"];
const SKILLS = ["import_gsc", "import_bing", "import_ga4", "pagespeed_refresh", "visibility_reobserve", "brain_refresh", "strategy_rebuild", "internal_link_proposals", "brief_proposals", "report_delivery", "chat_report_delivery"];
type ObjectValue = Record<string, unknown>;
export interface WeeklyDeliveryItem {
  workload_id: string; finding_id: string; recipe_release_id: string;
  revision_id: string; revision_sha256: string | null;
  operation_id: string | null; operation_state: string | null; pr_url: string | null;
  authority_kind: "owner_inbox" | "standing_grant" | null; authority_id: string | null;
  authorization_owner_id: string | null; review_status: string | null;
  decision_channel: "dashboard" | "slack" | "telegram" | null;
  observation_id: string | null; observation_sha256: string | null;
  delivery_outcome: string | null; delivery_reason: string | null; delivery_stage: string | null;
}
export interface WeeklyReport {
  cycleId: string; weekStart: string; status: string;
  stages: { stage: string; outcome: string; detailCode: string; evidenceRefs: string[] }[];
  skills: { stage: string; outcome: string; detailCode: string; reservedCents: number; units: number; spendStatus: string; evidenceRefs: string[] }[];
  delivery: WeeklyDeliveryItem[];
  measurements: ChangeMeasurement[];
  next: NextWork | null;
  decisions: NeedsDecision | null;
}
export type WeeklyReportState = { state: "available"; report: WeeklyReport } | { state: "empty" } | { state: "unavailable" } | { state: "rejected" };
const exact = (value: unknown, fields: string[]): value is ObjectValue =>
  !!value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).sort().join(",") === [...fields].sort().join(",");
const nullable = (value: unknown, pattern: RegExp) => value === null || typeof value === "string" && pattern.test(value);
const oneOf = (value: unknown, values: string[]) => value === null || typeof value === "string" && values.includes(value);
const boundedArray = (value: unknown, maximum = 64): value is unknown[] => Array.isArray(value) && value.length <= maximum;

export function parseWeeklyReport(value: unknown, siteId: string, weekStart: string): WeeklyReport | null {
  const extended = !!value && typeof value === "object" && "measurements" in value;
  const hasSkills = !!value && typeof value === "object" && "skill_stages" in value;
  if (!exact(value, ["schema_version", "cycle_id", "site_id", "week_start", "status", "stop_reason", "started_at", "closed_at", "observation_command", "stages", "gate_decisions", "waiting_for_owner", "handoffs", "deferred", "delivery", ...(extended ? ["measurements", "what_is_next", "needs_decision"] : []), ...(hasSkills ? ["skill_stages"] : [])]) ||
    (value.schema_version !== 1 && value.schema_version !== 2) || value.schema_version === 2 && !extended || value.site_id !== siteId || value.week_start !== weekStart ||
    typeof value.cycle_id !== "string" || !ID.test(value.cycle_id) ||
    !["running", "completed", "failed", "stopped"].includes(String(value.status)) ||
    !nullable(value.stop_reason, CODE) || typeof value.started_at !== "string" ||
    !(value.closed_at === null || typeof value.closed_at === "string") ||
    !boundedArray(value.stages, 9) || !boundedArray(value.delivery, 64) ||
    !boundedArray(value.gate_decisions, 32) || !boundedArray(value.waiting_for_owner, 32) ||
    !boundedArray(value.handoffs, 32) || !boundedArray(value.deferred, 32)) return null;
  const stages: WeeklyReport["stages"] = [];
  for (const row of value.stages) {
    if (!exact(row, ["stage", "outcome", "detail_code", "evidence_refs", "recorded_at"]) ||
      !STAGES.includes(String(row.stage)) || stages.some(s => s.stage === row.stage) ||
      !["completed", "unavailable", "deferred", "waiting_owner", "stopped", "failed"].includes(String(row.outcome)) ||
      typeof row.detail_code !== "string" || !CODE.test(row.detail_code) ||
      typeof row.recorded_at !== "string" || !boundedArray(row.evidence_refs) ||
      !row.evidence_refs.every(ref => typeof ref === "string" && /^[A-Za-z0-9:/._#-]{1,512}$/.test(ref))) return null;
    stages.push({ stage: String(row.stage), outcome: String(row.outcome), detailCode: row.detail_code, evidenceRefs: row.evidence_refs as string[] });
  }
  const delivery: WeeklyDeliveryItem[] = [];
  const skills: WeeklyReport["skills"] = [];
  if (hasSkills) {
    if (!boundedArray(value.skill_stages, 11)) return null;
    for (const row of value.skill_stages) {
      if (!exact(row, ["stage", "outcome", "detail_code", "work_type", "budget_source", "cap_source", "reserved_cents", "units", "spend_status", "evidence_refs", "recorded_at"]) ||
        !SKILLS.includes(String(row.stage)) || skills.some(s => s.stage === row.stage) ||
        !["completed", "unavailable", "failed"].includes(String(row.outcome)) ||
        typeof row.detail_code !== "string" || !CODE.test(row.detail_code) ||
        row.work_type !== (["brief_proposals", "internal_link_proposals"].includes(String(row.stage)) ? "draft_patch" : "research_audit") ||
        row.budget_source !== "standing_authorization" ||
        row.cap_source !== (row.stage === "brief_proposals" ? "standing_authorization_and_content_writer" : row.stage === "report_delivery" ? "standing_authorization_and_email" : row.stage === "pagespeed_refresh" ? "standing_authorization_and_pagespeed" : row.stage === "visibility_reobserve" ? "standing_authorization_and_assistants" : row.stage === "chat_report_delivery" ? "standing_authorization_and_chat" : "standing_authorization") ||
        !Number.isSafeInteger(row.reserved_cents) || typeof row.reserved_cents !== "number" || row.reserved_cents < 0 || row.reserved_cents > 100000000 ||
        !Number.isSafeInteger(row.units) || typeof row.units !== "number" || row.units < 0 || row.units > 64 ||
        row.spend_status !== (row.reserved_cents > 0 ? "upper_bound_reserved" : "no_paid_io") || row.reserved_cents > 0 && row.units === 0 ||
        !(row.recorded_at === null || typeof row.recorded_at === "string") || !boundedArray(row.evidence_refs) ||
        !row.evidence_refs.every(ref => typeof ref === "string" && /^[A-Za-z0-9:/._#-]{1,512}$/.test(ref))) return null;
      skills.push({ stage: String(row.stage), outcome: String(row.outcome), detailCode: row.detail_code, reservedCents: row.reserved_cents, units: row.units, spendStatus: String(row.spend_status), evidenceRefs: row.evidence_refs as string[] });
    }
  }
  for (const row of value.delivery) {
    if (!exact(row, ["workload_id", "finding_id", "recipe_release_id", "revision_id", "revision_sha256", "operation_id", "operation_state", "pr_url", "authority_kind", "authority_id", "authorization_owner_id", "decision_channel", "review_status", "observation_id", "observation_sha256", "delivery_outcome", "delivery_reason", "delivery_stage"]) ||
      !["workload_id", "finding_id", "recipe_release_id", "revision_id"].every(k => typeof row[k] === "string" && ID.test(row[k] as string)) ||
      !["operation_id", "authority_id", "authorization_owner_id", "observation_id"].every(k => nullable(row[k], ID)) ||
      !nullable(row.revision_sha256, HASH) || !nullable(row.observation_sha256, HASH) ||
      !oneOf(row.authority_kind, ["owner_inbox", "standing_grant"]) ||
      (row.authority_kind === "owner_inbox" ? !["dashboard", "slack", "telegram"].includes(String(row.decision_channel)) : row.decision_channel !== null) ||
      !oneOf(row.operation_state, ["planned", "dispatching", "outcome_unknown", "ready", "opened", "blocked"]) ||
      !oneOf(row.review_status, ["approved", "rejected", "changes_requested"]) ||
      !oneOf(row.delivery_outcome, ["verified", "not_yet_deployed", "inconclusive", "regressed"]) ||
      !oneOf(row.delivery_stage, ["pr_opened", "checks", "merged", "deployed"]) ||
      !nullable(row.delivery_reason, CODE) ||
      !(row.pr_url === null || typeof row.pr_url === "string" && /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*$/.test(row.pr_url)) ||
      (row.operation_id === null) !== (row.authority_id === null) ||
      (row.operation_id === null) !== (row.authority_kind === null) ||
      (row.operation_id === null) !== (row.authorization_owner_id === null) ||
      (row.pr_url !== null && row.operation_state !== "opened") ||
      (row.delivery_outcome === "verified" && (row.delivery_stage !== "deployed" || row.observation_sha256 === null))) return null;
    delivery.push(row as unknown as WeeklyDeliveryItem);
  }
  const measurements = extended ? parseChangeMeasurements(value.measurements) : [];
  const next = extended && value.what_is_next !== null ? parseReportNext(value.what_is_next) : null;
  const decisions = extended && value.needs_decision !== null ? parseReportDecisions(value.needs_decision) : null;
  if (measurements === null || value.schema_version === 2 && (next === null || decisions === null)) return null;
  return { cycleId: value.cycle_id, weekStart, status: String(value.status), stages, skills, delivery, measurements, next, decisions };
}

export async function loadWeeklyReport({ tenantToken, siteId, weekStart, fetcher = globalThis.fetch }: { tenantToken: string; siteId: string; weekStart?: string; fetcher?: typeof globalThis.fetch }): Promise<WeeklyReportState> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(tenantToken) || !ID.test(siteId) || weekStart !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(weekStart)) return { state: "rejected" };
  try {
    const response = await relayJson(new URL(`/v1/sites/${siteId}/weekly-cycles/${weekStart ?? "latest"}`, validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000")), {
      cache: "no-store", headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(5000),
    }, fetcher, 262144);
    if (response.status === 404) return { state: "empty" };
    if (response.status !== 200 || !response.headers.get("content-type")?.startsWith("application/json")) return { state: "unavailable" };
    const body = JSON.parse(await boundedRelayText(response, 256 * 1024));
    if (body === null) return { state: "empty" };
    const selectedWeek = weekStart ?? body.week_start;
    if (typeof selectedWeek !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(selectedWeek) || !Number.isFinite(Date.parse(selectedWeek))) return { state: "unavailable" };
    const report = parseWeeklyReport(body, siteId, selectedWeek);
    return report ? { state: "available", report } : { state: "unavailable" };
  } catch { return { state: "unavailable" }; }
}
