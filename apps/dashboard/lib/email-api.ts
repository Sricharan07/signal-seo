import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
export type EmailState = { availability: "unavailable" } | {
  availability: "available" | "identity_unverified" | "stale_session";
  address: string | null; can_enable: boolean; enabled: boolean;
  last_delivery_state: string | null;
};
const DELIVERY = new Set(["queued", "retry", "dispatching", "accepted", "failed", "unknown", "suppressed"]);


async function json(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.has("set-cookie") || !response.headers.get("content-type")?.startsWith("application/json")) throw new Error();
  const text = await boundedEmailText(response.body, 2048);
  const data: unknown = JSON.parse(text);
  if (typeof data !== "object" || data === null || Array.isArray(data)) throw new Error();
  return data as Record<string, unknown>;
}

export async function boundedEmailText(body: ReadableStream<Uint8Array> | null, maximum: number): Promise<string> {
  return boundedRelayText({ body }, maximum);
}

export async function loadEmail({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<EmailState> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId)) return { availability: "unavailable" };
  try {
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/email`), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(5000) }, fetcher, 131072);
    if (response.status !== 200) throw new Error();
    const data = await json(response);
    if (data.availability === "unavailable") return { availability: "unavailable" };
    if (Object.keys(data).sort().join(",") !== "address,availability,can_enable,enabled,last_delivery_state" ||
        !["available", "identity_unverified", "stale_session"].includes(String(data.availability)) ||
        typeof data.can_enable !== "boolean" || typeof data.enabled !== "boolean" ||
        !(data.address === null || typeof data.address === "string" && data.address.length <= 320 &&
          /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+$/.test(data.address)) ||
        !(data.last_delivery_state === null || typeof data.last_delivery_state === "string" && DELIVERY.has(data.last_delivery_state))) throw new Error();
    return data as EmailState;
  } catch { return { availability: "unavailable" }; }
}

export async function mutateEmail({ tenantToken, siteId, origin, enabled, address, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; origin: string; enabled: boolean; address: string | null; fetcher?: typeof globalThis.fetch;
}): Promise<string | null> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId)) return null;
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(5000) }, fetcher, 131072);
    const csrf = await json(proof);
    if (proof.status !== 200 || csrf.schema_version !== 1 || typeof csrf.csrf_token !== "string" ||
        !TOKEN.test(csrf.csrf_token) || Object.keys(csrf).sort().join(",") !== "csrf_token,schema_version") return null;
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/email`), { method: "POST", cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: cookie, "Content-Type": "application/json", Origin: origin,
        "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token },
      body: JSON.stringify({ enabled, address }), signal: AbortSignal.timeout(5000) }, fetcher, 131072);
    if (response.status !== 200) return null;
    const result = await json(response);
    return Object.keys(result).join(",") === "state" && ["enabled", "disabled", "identity_unverified"].includes(String(result.state)) ? String(result.state) : null;
  } catch { return null; }
}
