import { relayJson } from "./relay-json";
import { validBrandDocumentId as uuid } from "./brand-document-api";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { boundedWebflowJson } from "./webflow-api";

const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).length === fields.length && fields.every(k => k in v);
const slug = (v: unknown): v is string => typeof v === "string" && /^[a-z][a-z0-9-]{0,63}$/.test(v);
const digest = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
export interface PublishingOptions {
  schema_version: 1;
  drafts: {draft_id: string; title: string}[];
  articles: {candidate_id: string; source_sha256: string; title: string}[];
}
export function validPublishingOptions(v: unknown): v is PublishingOptions {
  const title = (x: unknown) => typeof x === "string" && x.length > 0 && x.length <= 60000 && !x.includes("\0");
  return object(v) && exact(v, ["schema_version", "drafts", "articles"]) && v.schema_version === 1 &&
    Array.isArray(v.drafts) && v.drafts.length <= 100 && v.drafts.every(d => object(d) && exact(d, ["draft_id", "title"]) && uuid(d.draft_id) && title(d.title)) &&
    Array.isArray(v.articles) && v.articles.length <= 100 && v.articles.every(a => object(a) && exact(a, ["candidate_id", "source_sha256", "title"]) && uuid(a.candidate_id) && digest(a.source_sha256) && title(a.title));
}
export function validWebflowMapping(v: unknown): v is {title: "name"; description: string; body: string} {
  return object(v) && exact(v, ["title", "description", "body"]) && v.title === "name" && slug(v.description) && slug(v.body) &&
    !["name", "slug"].includes(v.description) && !["name", "slug", v.description].includes(v.body);
}
export function webflowOwnerCommand(v: unknown): {siteId: string; operation: string; body: Record<string, unknown>; mapping?: {title: "name"; description: string; body: string}} | null {
  if (!object(v) || v.schema_version !== 1 || !uuid(v.site_id) || typeof v.operation !== "string") return null;
  const fields: Record<string, string[]> = {begin: ["provider_site", "collection_id", "field_mapping"], complete: ["attempt_id", "state", "code", "field_mapping"], seal: ["binding_id", "candidate_id", "source_sha256"], revoke: ["binding_id"]};
  const required = fields[v.operation];
  if (!required || !exact(v, ["schema_version", "site_id", "operation", ...required])) return null;
  if (v.operation === "begin" && ![v.provider_site, v.collection_id].every(x => typeof x === "string" && /^[0-9a-f]{24}$/.test(x))) return null;
  if (["begin", "complete"].includes(v.operation) && !validWebflowMapping(v.field_mapping)) return null;
  if (v.operation === "complete" && (!uuid(v.attempt_id) || typeof v.state !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(v.state) || typeof v.code !== "string" || !/^[!-~]{1,2048}$/.test(v.code))) return null;
  if (["seal", "revoke"].includes(v.operation) && !uuid(v.binding_id)) return null;
  if (v.operation === "seal" && (!uuid(v.candidate_id) || !digest(v.source_sha256))) return null;
  const {site_id: siteId, operation, ...body} = v;
  if (operation === "begin") delete body.field_mapping;
  return {siteId, operation, body, ...(operation === "begin" ? {mapping: v.field_mapping as {title: "name"; description: string; body: string}} : {})};
}

export function webflowAuthorization(value: unknown, origin: string): string | null {
  try {
    if (typeof value !== "string" || value.length > 4096) return null;
    const u = new URL(value);
    const q = u.searchParams;
    if (u.origin !== "https://webflow.com" || u.pathname !== "/oauth/authorize" || u.username || u.password || u.hash ||
      [...q.keys()].sort().join(",") !== "client_id,redirect_uri,response_type,scope,state" || q.get("response_type") !== "code" ||
      q.get("redirect_uri") !== origin + "/auth/webflow/callback" || !/^[A-Za-z0-9_-]{43}$/.test(q.get("state") ?? "") ||
      !/^[!-~]{1,256}$/.test(q.get("client_id") ?? "") || q.get("scope") !== "cms:read cms:write sites:read") return null;
    return u.href;
  } catch { return null; }
}

export async function relayWebflowOwner(token: string, siteId: string, origin: string, operation = "options", body?: Record<string, unknown>): Promise<Response> {
  const failed = (status: number, state = "unavailable") => Response.json({state}, {status, headers: {"Cache-Control": "no-store"}});
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const cookie = `${TENANT_COOKIE_NAME}=${token}`;
    let csrf = "";
    if (body) {
      const r = await relayJson(new URL("/v1/session/tenant-csrf", base), {cache: "no-store", redirect: "error", signal: AbortSignal.timeout(2500), headers: {Cookie: cookie, Accept: "application/json"}}, globalThis.fetch, 6100000);
      const p = await boundedWebflowJson(r, 4096);
      if (!r.ok || r.headers.getSetCookie().length || !object(p) || p.schema_version !== 1 || typeof p.csrf_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(p.csrf_token)) throw new Error();
      csrf = p.csrf_token;
    }
    const r = await relayJson(new URL(`/v1/sites/${siteId}/webflow/${operation}`, base), {cache: "no-store", redirect: "error", signal: AbortSignal.timeout(body ? 60000 : 15000), method: body ? "POST" : "GET", headers: {Cookie: cookie, Accept: "application/json", ...(body ? {Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf, "Content-Type": "application/json"} : {})}, ...(body ? {body: JSON.stringify(body)} : {})}, globalThis.fetch, 6100000);
    if (r.headers.getSetCookie().length) throw new Error();
    const result = await boundedWebflowJson(r, body ? 8192 : 6_100_000);
    if (!r.ok) return failed(r.status, object(result) && object(result.error) && result.error.code === "WEBFLOW_STEP_UP_REQUIRED" ? "step_up_required" : "unavailable");
    if (!body) { if (!validPublishingOptions(result)) throw new Error(); }
    else {
      if (!object(result) || result.schema_version !== 1) throw new Error();
      if (operation === "begin") {
        if (!exact(result, ["schema_version", "attempt_id", "authorization_url", "expires_in_seconds"]) || !uuid(result.attempt_id) || result.expires_in_seconds !== 600 || !webflowAuthorization(result.authorization_url, origin)) throw new Error();
      } else if (operation === "revoke") {
        if (!exact(result, ["schema_version", "state", "event_id", "upstream"]) || !["revoked", "AUTHORITY_DURABILITY_PENDING"].includes(String(result.state)) || !uuid(result.event_id) || !["NOT_EXECUTED", "accepted", "OUTCOME_UNKNOWN"].includes(String(result.upstream))) throw new Error();
      } else if (!exact(result, ["schema_version", "state", "id"]) || !uuid(result.id) || !(operation === "complete" ? result.state === "bound" : ["sealed", "replayed"].includes(String(result.state)))) throw new Error();
    }
    return Response.json(result, {headers: {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}});
  } catch { return failed(503); }
}
