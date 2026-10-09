import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { createHash } from "node:crypto";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA = /^[0-9a-f]{64}$/;
const SHA1 = /^[0-9a-f]{40}$/;

export interface DashboardDeliveryObservation {
  attemptId: string;
  operationId: string;
  state: "dispatching" | "completed" | "outcome_unknown";
  revisionSha256: string | null;
  receiptSha256: string | null;
  canonicalReceipt: string | null;
  outcome: "verified" | "not_yet_deployed" | "inconclusive" | "regressed" | null;
  reason: string | null;
  stage: "pr_opened" | "checks" | "merged" | "deployed" | null;
  checksCount: number | null;
  passedCount: number | null;
  mergedSha: string | null;
  deploymentId: number | null;
  fetchedSha256: string | null;
  postconditions: readonly { field: string; matched: boolean; expected: unknown; observed: unknown }[];
  recoveryPlan: string | null;
  observedAt: string;
  nextObserveAt: string;
}

export type DashboardDeliveryObservations =
  | { state: "available"; observations: readonly DashboardDeliveryObservation[] }
  | { state: "unavailable" | "invalid" | "not_authenticated" };

function exact(value: unknown, fields: string[]): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("|") === fields.sort().join("|");
}
function text(value: unknown, max: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max || value.includes("\0")) throw new Error("invalid");
  return value;
}
function count(value: unknown, max: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0 || Number(value) > max) throw new Error("invalid");
  return Number(value);
}
function digest(value: unknown, pattern = SHA): string {
  const parsed = text(value, 64);
  if (!pattern.test(parsed)) throw new Error("invalid");
  return parsed;
}
function date(value: unknown): string {
  const parsed = text(value, 40);
  if (!Number.isFinite(Date.parse(parsed)) || !/(Z|[+-]\d\d:\d\d)$/.test(parsed)) throw new Error("invalid");
  return parsed;
}

function parseObservation(value: unknown, siteId: string): DashboardDeliveryObservation {
  if (!exact(value, ["attempt_id", "operation_id", "state", "canonical_receipt", "receipt_sha256", "observed_at", "next_observe_at"]) || !["dispatching", "completed", "outcome_unknown"].includes(String(value.state))) throw new Error("invalid");
  const attemptId = text(value.attempt_id, 36), operationId = text(value.operation_id, 36);
  if (!UUID.test(attemptId) || !UUID.test(operationId)) throw new Error("invalid");
  const result: DashboardDeliveryObservation = { attemptId, operationId, state: value.state as DashboardDeliveryObservation["state"], observedAt: date(value.observed_at), nextObserveAt: date(value.next_observe_at), revisionSha256: null, receiptSha256: null, canonicalReceipt: null, outcome: null, reason: null, stage: null, checksCount: null, passedCount: null, mergedSha: null, deploymentId: null, fetchedSha256: null, postconditions: [], recoveryPlan: null };
  if (result.state !== "completed") {
    if (value.canonical_receipt !== null || value.receipt_sha256 !== null) throw new Error("invalid");
    return result;
  }
  const canonicalReceipt = text(value.canonical_receipt, 65536), receiptSha256 = digest(value.receipt_sha256);
  if (Buffer.byteLength(canonicalReceipt, "utf8") > 65536 || createHash("sha256").update(canonicalReceipt).digest("hex") !== receiptSha256) throw new Error("invalid");
  const receipt: unknown = JSON.parse(canonicalReceipt);
  if (!exact(receipt, ["schema_version", "site_id", "attempt_id", "operation_id", "revision_sha256", "environment", "deployment_actor_id", "provider", "provider_evidence", "live", "live_egress_operation_id", "outcome", "reason", "recovery_plan", "delivery_certified"]) || receipt.schema_version !== 1 || receipt.site_id !== siteId || receipt.attempt_id !== attemptId || receipt.operation_id !== operationId || receipt.delivery_certified !== false || !["verified", "not_yet_deployed", "inconclusive", "regressed"].includes(String(receipt.outcome))) throw new Error("invalid");
  text(receipt.environment, 100); count(receipt.deployment_actor_id, Number.MAX_SAFE_INTEGER);
  const provider = receipt.provider;
  if (!exact(provider, ["stage", "reason", "checks", "merged_sha", "merged_tree_sha", "merged_at", "deployment"]) || !["pr_opened", "checks", "merged", "deployed"].includes(String(provider.stage))) throw new Error("invalid");
  text(provider.reason, 100);
  let checksCount = null, passedCount = null;
  if (provider.checks !== null) {
    if (!exact(provider.checks, ["head_sha", "observed_count", "passed_count", "records"]) || !Array.isArray(provider.checks.records)) throw new Error("invalid");
    digest(provider.checks.head_sha, SHA1);
    checksCount = count(provider.checks.observed_count, 200); passedCount = count(provider.checks.passed_count, checksCount);
    if (checksCount !== provider.checks.records.length) throw new Error("invalid");
    for (const record of provider.checks.records) {
      if (!exact(record, ["id", "kind", "state", "conclusion"]) || !["check_run", "commit_status"].includes(String(record.kind))) throw new Error("invalid");
      count(record.id, Number.MAX_SAFE_INTEGER); text(record.state, 40);
      if (record.conclusion !== null) text(record.conclusion, 40);
    }
  }
  const mergedSha = provider.merged_sha === null ? null : digest(provider.merged_sha, SHA1);
  if (mergedSha === null) {
    if (provider.merged_tree_sha !== null || provider.merged_at !== null || ["merged", "deployed"].includes(String(provider.stage))) throw new Error("invalid");
  } else { digest(provider.merged_tree_sha, SHA1); date(provider.merged_at); }
  let deploymentId = null;
  if (provider.deployment !== null) {
    const d = provider.deployment;
    if (!exact(d, ["id", "status_id", "sha", "environment", "actor_id", "environment_url", "created_at", "status_created_at"]) || provider.stage !== "deployed" || d.sha !== mergedSha || d.environment !== receipt.environment || d.actor_id !== receipt.deployment_actor_id) throw new Error("invalid");
    deploymentId = count(d.id, Number.MAX_SAFE_INTEGER); count(d.status_id, Number.MAX_SAFE_INTEGER);
    const url = new URL(text(d.environment_url, 2048));
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("invalid");
    date(d.created_at); date(d.status_created_at);
  } else if (provider.stage === "deployed") throw new Error("invalid");
  if (!Array.isArray(receipt.provider_evidence) || receipt.provider_evidence.length > 20) throw new Error("invalid");
  for (const e of receipt.provider_evidence) {
    if (!exact(e, ["egress_operation_id", "url", "http_status", "body_sha256"]) || !UUID.test(String(e.egress_operation_id))) throw new Error("invalid");
    const url = new URL(text(e.url, 2048));
    if (url.origin !== "https://api.github.com" || url.username || url.password || url.hash) throw new Error("invalid");
    count(e.http_status, 599); digest(e.body_sha256);
  }
  const live = receipt.live;
  let fetchedSha256 = null;
  const postconditions: DashboardDeliveryObservation["postconditions"][number][] = [];
  if (receipt.live_egress_operation_id !== null && !UUID.test(String(receipt.live_egress_operation_id))) throw new Error("invalid");
  if (live !== null) {
    if (!exact(live, ["outcome", "reason", "fetched_sha256", "http_status", "observed", "postconditions", "matched"]) || !["verified", "inconclusive", "regressed"].includes(String(live.outcome)) || typeof live.matched !== "boolean" || !Array.isArray(live.postconditions) || live.postconditions.length > 6 || live.observed === null || typeof live.observed !== "object" || Array.isArray(live.observed)) throw new Error("invalid");
    text(live.reason, 100); if (live.http_status !== null) count(live.http_status, 599);
    fetchedSha256 = live.fetched_sha256 === null ? null : digest(live.fetched_sha256);
    for (const condition of live.postconditions) {
      if (!exact(condition, ["field", "expected", "observed", "matched"]) || !["title", "meta_description", "canonical", "image_alt", "json_ld", "removed_link"].includes(String(condition.field)) || typeof condition.matched !== "boolean") throw new Error("invalid");
      postconditions.push({ field: String(condition.field), matched: condition.matched, expected: condition.expected, observed: condition.observed });
    }
  }
  if (receipt.outcome === "verified" && (provider.stage !== "deployed" || live === null || live.outcome !== "verified" || live.matched !== true || fetchedSha256 === null || receipt.live_egress_operation_id === null || postconditions.length === 0 || postconditions.some(p => !p.matched))) throw new Error("invalid");
  return { ...result, revisionSha256: digest(receipt.revision_sha256), receiptSha256, canonicalReceipt, outcome: receipt.outcome as DashboardDeliveryObservation["outcome"], reason: text(receipt.reason, 100), stage: provider.stage as DashboardDeliveryObservation["stage"], checksCount, passedCount, mergedSha, deploymentId, fetchedSha256, postconditions, recoveryPlan: text(receipt.recovery_plan, 8192) };
}

export async function loadDashboardDeliveryObservations({ tenantToken, siteId, baseUrl, fetcher = globalThis.fetch }: { tenantToken: string; siteId: string; baseUrl?: string; fetcher?: typeof globalThis.fetch }): Promise<DashboardDeliveryObservations> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(tenantToken) || !UUID.test(siteId)) return { state: "invalid" };
  let origin: URL;
  try { origin = validatedSignalApiBaseUrl(baseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000"); } catch { return { state: "invalid" }; }
  let response: Response;
  try { response = await relayJson(new URL(`/v1/sites/${siteId}/github-delivery-observations`, origin), { cache: "no-store", redirect: "error", headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(2500) }, fetcher, 4194304); } catch { return { state: "unavailable" }; }
  if ([401, 403].includes(response.status)) return { state: "not_authenticated" };
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };
  try {
    const raw = await boundedRelayText(response, 4 * 1024 * 1024);
    if (Buffer.byteLength(raw, "utf8") > 4 * 1024 * 1024) throw new Error("oversized");
    const payload: unknown = JSON.parse(raw);
    if (!exact(payload, ["schema_version", "site_id", "correlation_id", "observations"]) || payload.schema_version !== 1 || payload.site_id !== siteId || !Array.isArray(payload.observations) || payload.observations.length > 50) throw new Error("invalid");
    text(payload.correlation_id, 64);
    const observations = payload.observations.map(value => parseObservation(value, siteId));
    if (new Set(observations.map(item => item.operationId)).size !== observations.length) throw new Error("invalid");
    return { state: "available", observations };
  } catch { return { state: "invalid" }; }
}
