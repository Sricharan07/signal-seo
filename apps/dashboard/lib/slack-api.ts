import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";

export const SLACK_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
export type SlackState = { availability: "unavailable" | "unbound" } | {
  availability: "bound"; binding_id: string; workspace_id: string; channel_id: string;
  max_risk: number; link_id: string | null; slack_user_id: string | null;
};


async function json(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.has("set-cookie")) throw new Error("Unexpected cookie");
  const text = await boundedRelayText(response, 4096);
  if (new TextEncoder().encode(text).byteLength > 4096) throw new Error("Oversized response");
  const body: unknown = JSON.parse(text);
  if (typeof body !== "object" || body === null || Array.isArray(body)) throw new Error("Invalid response");
  return body as Record<string, unknown>;
}

export async function loadSlack({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<SlackState> {
  if (!TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId)) return { availability: "unavailable" };
  try {
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/slack`), {
      cache: "no-store", headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` },
      signal: AbortSignal.timeout(5000),
    }, fetcher, 4096);
    if (response.status !== 200) return { availability: "unavailable" };
    const data = await json(response);
    if (data.availability === "unbound" && Object.keys(data).length === 1) return { availability: "unbound" };
    if (Object.keys(data).sort().join(",") !== "availability,binding_id,channel_id,link_id,max_risk,slack_user_id,workspace_id" ||
        data.availability !== "bound" || typeof data.binding_id !== "string" || !SLACK_UUID.test(data.binding_id) ||
        typeof data.workspace_id !== "string" || !/^T[A-Z0-9]{7,63}$/.test(data.workspace_id) ||
        typeof data.channel_id !== "string" || !/^[CG][A-Z0-9]{7,63}$/.test(data.channel_id) ||
        typeof data.max_risk !== "number" || !Number.isInteger(data.max_risk) || data.max_risk < 0 || data.max_risk > 2 ||
        !(data.link_id === null || typeof data.link_id === "string" && SLACK_UUID.test(data.link_id)) ||
        !(data.slack_user_id === null || typeof data.slack_user_id === "string" && /^[UW][A-Z0-9]{7,63}$/.test(data.slack_user_id)) ||
        (data.link_id === null) !== (data.slack_user_id === null)) return { availability: "unavailable" };
    return data as SlackState;
  } catch { return { availability: "unavailable" }; }
}

export async function mutateSlack({ tenantToken, siteId, origin, command, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; origin: string; command: Record<string, unknown>; fetcher?: typeof globalThis.fetch;
}): Promise<Record<string, unknown> | null> {
  if (!TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId)) return null;
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), {
      cache: "no-store", headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(2500),
    }, fetcher, 4096);
    const csrf = await json(proof);
    if (proof.status !== 200 || csrf.schema_version !== 1 || typeof csrf.csrf_token !== "string" ||
        !TOKEN.test(csrf.csrf_token) || Object.keys(csrf).sort().join(",") !== "csrf_token,schema_version") return null;
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/slack`), {
      method: "POST", cache: "no-store", signal: AbortSignal.timeout(15000),
      headers: { Accept: "application/json", Cookie: cookie, "Content-Type": "application/json",
        Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token },
      body: JSON.stringify(command),
    }, fetcher, 4096);
    return response.status === 200 ? await json(response) : null;
  } catch { return null; }
}

export function slackAuthorizationUrl(value: unknown, origin: string): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.origin !== "https://slack.com" || url.pathname !== "/oauth/v2/authorize" || url.hash || url.username || url.password ||
        [...url.searchParams.keys()].sort().join(",") !== "client_id,redirect_uri,scope,state,team" ||
        url.searchParams.get("scope") !== "chat:write" ||
        url.searchParams.get("redirect_uri") !== origin+"/auth/slack/callback" ||
        !TOKEN.test(url.searchParams.get("state") ?? "") || !/^T[A-Z0-9]{7,63}$/.test(url.searchParams.get("team") ?? "")) return null;
    return url.href;
  } catch { return null; }
}
