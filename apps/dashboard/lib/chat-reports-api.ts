import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { boundedEmailText } from "./email-api";

export const CHAT_CHANNELS = ["slack_channel", "slack_dm", "telegram"] as const;
export type ChatChannel = typeof CHAT_CHANNELS[number];
export type ChatReportState = { availability: "unavailable" } | {
  channels: { channel: ChatChannel; availability: "available" | "unavailable"; enabled: boolean }[];
  history: { id: string; channel: ChatChannel; category: string; state: string; attempt_count: number; created_at: string; outcome: string | null }[];
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const STATES = ["queued", "retry", "dispatching", "accepted", "unknown", "suppressed", "failed"];
const CATEGORIES = ["weekly_report", "pause", "revocation", "failed_delivery", "stale_binding"];
const OUTCOMES = ["dispatching", "accepted", "unknown", "deferred", "retry_exhausted", "opted_out", "authority_denied", "stale_binding", "destination_unavailable", "provider_unavailable", "cap_reached", "render_rejected"];
const keys = (value: Record<string, unknown>, expected: string) => Object.keys(value).sort().join(",") === expected;
function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error();
  return value as Record<string, unknown>;
}
async function json(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.has("set-cookie") || !response.headers.get("content-type")?.startsWith("application/json")) throw new Error();
  return object(JSON.parse(await boundedEmailText(response.body, 16384)));
}

export async function loadChatReports({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<ChatReportState> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId)) return { availability: "unavailable" };
  try {
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/chat-reports`), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(5000) }, fetcher, 131072);
    if (response.status !== 200) throw new Error();
    const data = await json(response);
    if (!keys(data, "channels,history") || !Array.isArray(data.channels) || data.channels.length !== 3 ||
      !Array.isArray(data.history) || data.history.length > 20) throw new Error();
    data.channels.forEach((value, index) => {
      const row = object(value);
      if (!keys(row, "availability,channel,enabled") || row.channel !== CHAT_CHANNELS[index] ||
        !["available", "unavailable"].includes(String(row.availability)) || typeof row.enabled !== "boolean") throw new Error();
    });
    data.history.forEach(value => {
      const row = object(value);
      if (!keys(row, "attempt_count,category,channel,created_at,id,outcome,state") || typeof row.id !== "string" || !UUID.test(row.id) ||
        !CHAT_CHANNELS.includes(row.channel as ChatChannel) || !CATEGORIES.includes(String(row.category)) || !STATES.includes(String(row.state)) ||
        !Number.isInteger(row.attempt_count) || Number(row.attempt_count) < 0 || Number(row.attempt_count) > 3 ||
        typeof row.created_at !== "string" || row.created_at.length > 40 || !/^\d{4}-\d{2}-\d{2}T/.test(row.created_at) || !Number.isFinite(Date.parse(row.created_at)) ||
        !(row.outcome === null || OUTCOMES.includes(String(row.outcome)))) throw new Error();
    });
    return data as ChatReportState;
  } catch { return { availability: "unavailable" }; }
}

export async function mutateChatReports({ tenantToken, siteId, origin, channel, enabled, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; origin: string; channel: ChatChannel; enabled: boolean; fetcher?: typeof globalThis.fetch;
}): Promise<string | null> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId) || !CHAT_CHANNELS.includes(channel) || typeof enabled !== "boolean") return null;
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(5000) }, fetcher, 131072);
    const csrf = await json(proof);
    if (proof.status !== 200 || !keys(csrf, "csrf_token,schema_version") || csrf.schema_version !== 1 ||
      typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token)) return null;
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/chat-reports`), { method: "POST", cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: cookie, "Content-Type": "application/json", Origin: origin,
        "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token }, body: JSON.stringify({ channel, enabled }), signal: AbortSignal.timeout(5000) }, fetcher, 131072);
    if (response.status !== 200) return null;
    const result = await json(response);
    return keys(result, "state") && ["enabled", "disabled", "unavailable", "destination_unavailable"].includes(String(result.state)) ? String(result.state) : null;
  } catch { return null; }
}
