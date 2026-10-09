import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { SLACK_UUID } from "./slack-api";

export type WeeklyPauseState = { state: "available"; paused: boolean } | { state: "unavailable" };
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const exact = (value: unknown, fields: string): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join(",") === fields;
async function json(response: Response): Promise<unknown> {
  if (response.headers.has("set-cookie")) throw new Error();
  const text = await boundedRelayText(response, 4096);
  if (new TextEncoder().encode(text).byteLength > 4096) throw new Error();
  return JSON.parse(text);
}
export async function loadWeeklyPause({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<WeeklyPauseState> {
  if (!TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId)) return { state: "unavailable" };
  try {
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/weekly-loop`), {
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
      headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` },
    }, fetcher, 4096);
    const value = await json(response);
    return response.status === 200 && exact(value, "paused,site_id") && value.site_id === siteId && typeof value.paused === "boolean"
      ? { state: "available", paused: value.paused } : { state: "unavailable" };
  } catch { return { state: "unavailable" }; }
}
export async function mutateWeeklyLoop({ tenantToken, siteId, operation, origin, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; operation: "pause" | "resume"; origin: string; fetcher?: typeof globalThis.fetch;
}): Promise<Record<string, unknown> | null> {
  if (!TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId) || !["pause", "resume"].includes(operation)) return null;
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), {
      cache: "no-store", redirect: "error", headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(2500),
    }, fetcher, 4096);
    const csrf = await json(proof);
    if (proof.status !== 200 || !exact(csrf, "csrf_token,schema_version") || csrf.schema_version !== 1 ||
        typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token)) return null;
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/weekly-loop/${operation}`), {
      method: "POST", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
      headers: { Accept: "application/json", "Content-Type": "application/json", Cookie: cookie,
        Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token }, body: "{}",
    }, fetcher, 4096);
    const value = await json(response);
    if (response.status !== 200 || !exact(value, "draining_observations,durability,epoch,state") ||
        value.state !== (operation === "pause" ? "paused" : "pause_cleared") ||
        !Number.isSafeInteger(value.epoch) || Number(value.epoch) < 0 ||
        !Number.isSafeInteger(value.draining_observations) || Number(value.draining_observations) < 0 ||
        !["ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"].includes(String(value.durability))) return null;
    return value;
  } catch { return null; }
}
