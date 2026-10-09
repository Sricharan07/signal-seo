import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";

export const TELEGRAM_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
export type TelegramState = { availability: "unavailable" | "unbound" } | {
  availability: "bound" | "failed"; binding_id: string; bot_username: string | null;
  max_risk: number; link_id: string | null; telegram_user_id: string | null;
};
async function json(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.has("set-cookie") || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") throw new Error();
  const text = await boundedRelayText(response, 4096);
  if (new TextEncoder().encode(text).byteLength > 4096) throw new Error();
  const body: unknown = JSON.parse(text);
  if (typeof body !== "object" || body === null || Array.isArray(body)) throw new Error();
  return body as Record<string, unknown>;
}
export function telegramPairingUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 150) return null;
  try {
    const url = new URL(value);
    if (url.origin !== "https://t.me" || url.username || url.password || url.hash ||
        !/^\/[A-Za-z0-9_]{5,32}$/.test(url.pathname) || [...url.searchParams.keys()].join(",") !== "start" ||
        !TOKEN.test(url.searchParams.get("start") ?? "")) return null;
    return url.href;
  } catch { return null; }
}
export async function loadTelegram({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<TelegramState> {
  if (!TOKEN.test(tenantToken) || !TELEGRAM_UUID.test(siteId)) return { availability: "unavailable" };
  try {
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/telegram`), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(5000) }, fetcher, 4096);
    if (response.status !== 200) return { availability: "unavailable" };
    const data = await json(response);
    if (data.availability === "unbound" && Object.keys(data).length === 1) return { availability: "unbound" };
    if (Object.keys(data).sort().join(",") !== "availability,binding_id,bot_username,link_id,max_risk,telegram_user_id" ||
        !["bound", "failed"].includes(String(data.availability)) || typeof data.binding_id !== "string" || !TELEGRAM_UUID.test(data.binding_id) ||
        !(data.bot_username === null && data.availability === "failed" || typeof data.bot_username === "string" && /^[A-Za-z0-9_]{5,32}$/.test(data.bot_username)) ||
        typeof data.max_risk !== "number" || !Number.isInteger(data.max_risk) || data.max_risk < 0 || data.max_risk > 2 ||
        !(data.link_id === null || typeof data.link_id === "string" && TELEGRAM_UUID.test(data.link_id)) ||
        !(data.telegram_user_id === null || typeof data.telegram_user_id === "string" && /^[1-9][0-9]{0,15}$/.test(data.telegram_user_id) && Number(data.telegram_user_id) <= 2**52 - 1) ||
        (data.link_id === null) !== (data.telegram_user_id === null)) return { availability: "unavailable" };
    return data as TelegramState;
  } catch { return { availability: "unavailable" }; }
}
export async function mutateTelegram({ tenantToken, siteId, origin, command, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; origin: string; command: Record<string, unknown>; fetcher?: typeof globalThis.fetch;
}): Promise<Record<string, unknown> | null> {
  if (!TOKEN.test(tenantToken) || !TELEGRAM_UUID.test(siteId)) return null;
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(2500) }, fetcher, 4096);
    const csrf = await json(proof);
    if (proof.status !== 200 || csrf.schema_version !== 1 || typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token) ||
        Object.keys(csrf).sort().join(",") !== "csrf_token,schema_version") return null;
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/telegram`), { method: "POST", cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(20000), headers: { Accept: "application/json", Cookie: cookie, "Content-Type": "application/json",
        Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token }, body: JSON.stringify(command) }, fetcher, 4096);
    if (response.status !== 200) return null;
    const result = await json(response);
    if (command.operation === "install") return typeof result.binding_id === "string" && TELEGRAM_UUID.test(result.binding_id) && Object.keys(result).length === 1 ? result : null;
    if (command.operation === "link") {
      const url = telegramPairingUrl(result.pairing_url);
      return url !== null && result.expires_in_seconds === 300 && typeof result.pairing_id === "string" && TELEGRAM_UUID.test(result.pairing_id) && Object.keys(result).length === 3
        ? { pairing_url: url, pairing_id: result.pairing_id, expires_in_seconds: 300 } : null;
    }
    if (command.operation === "request") return typeof result.outbox_id === "string" && TELEGRAM_UUID.test(result.outbox_id) &&
      ["queued", "accepted", "unknown", "dispatching"].includes(String(result.state)) && Object.keys(result).length === 2 ? result : null;
    if (command.operation === "revoke") return ["revoked", "AUTHORITY_DURABILITY_PENDING"].includes(String(result.outcome)) &&
      typeof result.restriction_event_id === "string" && TELEGRAM_UUID.test(result.restriction_event_id) &&
      ["not_required", "not_executed", "accepted", "unknown"].includes(String(result.upstream)) && Object.keys(result).length === 3 ? result : null;
    return null;
  } catch { return null; }
}
