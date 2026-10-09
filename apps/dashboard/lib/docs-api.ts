import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { SLACK_UUID } from "./slack-api";

export const NOTION_REASON = "Notion read-only capability cannot be verified from provider documentation; pending live qualification";
export type DocsSource = { source_id: string; file_id: string; document_id: string | null;
  provider_version: string | null; modified_time: string | null; observed_at: string | null;
  state: "pending" | "version" | "withdrawn"; review_fact_ids: string[] };
export type DocsState = { availability: "unavailable"; sources: DocsSource[] } | { availability:"unbound"; sources:DocsSource[]; extraction_availability:"available"|"unavailable" } | {
  availability: "ready" | "degraded" | "revoked"; binding_id: string; reason: string | null; sources: DocsSource[]; extraction_availability:"available"|"unavailable" };
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const unavailable: DocsState = { availability: "unavailable", sources: [] };
const object = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);
const exact = (x: Record<string, unknown>, keys: string[]) => Object.keys(x).sort().join(",") === keys.sort().join(",");
const nullableId = (x: unknown) => x === null || typeof x === "string" && SLACK_UUID.test(x);
const date = (x: unknown) => x === null || typeof x === "string" && x.length <= 40 && !Number.isNaN(Date.parse(x));

export function docsState(value: unknown): DocsState | null {
  if (!object(value) || !Array.isArray(value.sources) || value.sources.length > 20) return null;
  for (const s of value.sources) {
    if (!object(s) || !exact(s, ["source_id", "file_id", "document_id", "provider_version", "modified_time", "observed_at", "state", "review_fact_ids"]) ||
      typeof s.source_id !== "string" || !SLACK_UUID.test(s.source_id) || typeof s.file_id !== "string" || !/^[A-Za-z0-9_-]{10,200}$/.test(s.file_id) ||
      !nullableId(s.document_id) || !(s.provider_version === null || typeof s.provider_version === "string" && /^[0-9]{1,100}$/.test(s.provider_version)) ||
      !date(s.modified_time) || !date(s.observed_at) || !["pending", "version", "withdrawn"].includes(String(s.state)) ||
      !Array.isArray(s.review_fact_ids) || s.review_fact_ids.length > 10000 || s.review_fact_ids.some(x => typeof x !== "string" || !SLACK_UUID.test(x))) return null;
  }
  if (!["available","unavailable"].includes(String(value.extraction_availability))) return null;
  if (value.availability === "unbound" && exact(value, ["availability", "sources","extraction_availability"]) && value.sources.length === 0) return value as DocsState;
  if (!exact(value, ["availability", "binding_id", "reason", "sources","extraction_availability"]) || !["ready", "degraded", "revoked"].includes(String(value.availability)) ||
    typeof value.binding_id !== "string" || !SLACK_UUID.test(value.binding_id) || !(value.reason === null || ["owner_disconnect", "provider_reauthorization", "scope_changed", "refresh_failed"].includes(String(value.reason)))) return null;
  return value as DocsState;
}

async function json(response: Response): Promise<unknown> {
  if (response.headers.has("set-cookie") || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") throw new Error();
  const raw = await boundedBody(response, 262144);
  return JSON.parse(raw);
}

export async function boundedBody(message: Request | Response, maximum: number): Promise<string> {
  return boundedRelayText(message, maximum);
}

export async function loadDocs({ tenantToken, siteId, fetcher = globalThis.fetch }: { tenantToken: string; siteId: string; fetcher?: typeof fetch }): Promise<DocsState> {
  if (!TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId)) return unavailable;
  try {
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/google-docs`), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(5000) }, fetcher, 262144);
    return response.status === 200 ? docsState(await json(response)) ?? unavailable : unavailable;
  } catch { return unavailable; }
}

export async function mutateDocs({ tenantToken, siteId, origin, command, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; origin: string; command: Record<string, unknown>; fetcher?: typeof fetch;
}): Promise<Record<string, unknown> | null> {
  if (!TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId)) return null;
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), { cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(2500) }, fetcher, 3000000);
    const csrf = await json(proof);
    if (proof.status !== 200 || !object(csrf) || !exact(csrf, ["schema_version", "csrf_token"]) || csrf.schema_version !== 1 ||
      typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token)) return null;
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/google-docs`), { method: "POST", cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(90000), headers: { Accept: "application/json", Cookie: cookie, "Content-Type": "application/json",
        Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token }, body: JSON.stringify(command) }, fetcher, 3000000);
    const value = await json(response);
    if (response.status !== 200 || !object(value)) return null;
    if (command.operation === "connect") return exact(value, ["attempt_id", "authorization_url"]) &&
      typeof value.attempt_id === "string" && SLACK_UUID.test(value.attempt_id) && docsAuthorization(value.authorization_url, origin) !== null ? value : null;
    if (command.operation === "complete") return exact(value, ["binding_id"]) && typeof value.binding_id === "string" && SLACK_UUID.test(value.binding_id) ? value : null;
    if (command.operation === "sync") return docsState(value) ? value : null;
    return command.operation === "disconnect" && exact(value, ["state", "secret_removed"]) &&
      ["revoked_pending", "revoked_durable"].includes(String(value.state)) && typeof value.secret_removed === "boolean" ? value : null;
  } catch { return null; }
}

export function docsAuthorization(value: unknown, origin: string): string | null {
  if (typeof value !== "string" || value.length > 4096) return null;
  try {
    const url = new URL(value);
    if (url.origin !== "https://accounts.google.com" || url.pathname !== "/o/oauth2/v2/auth" || url.hash || url.username || url.password ||
      [...url.searchParams.keys()].sort().join(",") !== "access_type,allow_multiple,client_id,code_challenge,code_challenge_method,include_granted_scopes,mimetypes,prompt,redirect_uri,response_type,scope,state,trigger_onepick" ||
      url.searchParams.get("scope") !== "https://www.googleapis.com/auth/drive.file" || url.searchParams.get("redirect_uri") !== origin+"/auth/google-docs/callback" ||
      !TOKEN.test(url.searchParams.get("state") ?? "") || !TOKEN.test(url.searchParams.get("code_challenge") ?? "") || url.searchParams.get("code_challenge_method") !== "S256" ||
      url.searchParams.get("response_type") !== "code" || url.searchParams.get("access_type") !== "offline" || url.searchParams.get("prompt") !== "consent" ||
      url.searchParams.get("include_granted_scopes") !== "false" || url.searchParams.get("trigger_onepick") !== "true" || url.searchParams.get("allow_multiple") !== "true" ||
      url.searchParams.get("mimetypes") !== "application/vnd.google-apps.document" || !/^[A-Za-z0-9._-]{16,256}\.apps\.googleusercontent\.com$/.test(url.searchParams.get("client_id") ?? "")) return null;
    return url.href;
  } catch { return null; }
}
