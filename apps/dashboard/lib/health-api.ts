import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { boundedEmailText } from "./email-api";

export const HEALTH_CHECKS = ["temporal", "weekly_schedule", "measurement_schedule", "outbox_backlog", "outbox_age",
  "binding_gsc", "binding_bing", "binding_ga4", "binding_github", "binding_slack", "binding_telegram", "binding_email",
  "egress_pins", "openbao", "disk", "database_size", "write_intents", "budget_model", "budget_dataforseo", "budget_assistants"] as const;
export type HealthCheck = { check: typeof HEALTH_CHECKS[number]; state: "ok" | "warning" | "critical" | "unknown";
  reason: string; checked_at: string; remediation: string };
export type HealthState = { state: "unavailable" } | { state: "rejected" } | { state: "available"; checks: HealthCheck[] };
const REASONS = new Set(["within_threshold", "approaching_threshold", "threshold_exceeded", "probe_unavailable", "not_configured",
  "revoked", "expired", "import_failing", "stale_evidence", "reachable", "unreachable", "sealed", "unsealed", "paused", "stuck",
  "healthy", "budget_exhausted", "pin_expired", "pin_expiring", "no_pin_evidence"]);
const STATE_REASONS: Record<HealthCheck["state"], Set<string>> = {
  ok: new Set(["within_threshold", "reachable", "unsealed", "healthy"]),
  warning: new Set(["approaching_threshold", "import_failing", "paused", "pin_expiring"]),
  critical: new Set(["threshold_exceeded", "revoked", "expired", "unreachable", "sealed", "stuck", "budget_exhausted", "pin_expired"]),
  unknown: new Set(["probe_unavailable", "not_configured", "stale_evidence", "no_pin_evidence"]),
};

export async function loadHealth({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<HealthState> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(tenantToken) || !/^[0-9a-f-]{36}$/.test(siteId)) return { state: "rejected" };
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const response = await relayJson(new URL(`/v1/sites/${siteId}/health`, base), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(5000) }, fetcher, 131072);
    if (response.status === 403) return { state: "rejected" };
    if (response.status !== 200 || response.headers.has("set-cookie") || !response.headers.get("content-type")?.startsWith("application/json")) throw new Error();
    const data: unknown = JSON.parse(await boundedEmailText(response.body, 16384));
    if (!data || typeof data !== "object" || Object.keys(data).join(",") !== "checks") throw new Error();
    const checks = (data as { checks: unknown }).checks;
    if (!Array.isArray(checks) || checks.length !== 0 && checks.length !== 20) throw new Error();
    const seen = new Set();
    for (const item of checks) {
      if (!item || Object.keys(item).sort().join(",") !== "check,checked_at,reason,remediation,state" ||
        !HEALTH_CHECKS.includes(item.check) || seen.has(item.check) || !["ok", "warning", "critical", "unknown"].includes(item.state) ||
        !REASONS.has(item.reason) || !STATE_REASONS[item.state as HealthCheck["state"]].has(item.reason) ||
        typeof item.checked_at !== "string" || !Number.isFinite(Date.parse(item.checked_at)) ||
        typeof item.remediation !== "string" || item.remediation.length > 256 || /[\u0000-\u001f]/.test(item.remediation)) throw new Error();
      seen.add(item.check);
      if (Date.now() - Date.parse(item.checked_at) > 300000 || Date.parse(item.checked_at) > Date.now() + 30000) {
        item.state = "unknown"; item.reason = "stale_evidence";
      }
    }
    return { state: "available", checks };
  } catch { return { state: "unavailable" }; }
}
