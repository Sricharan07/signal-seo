import { boundedRelayText } from "./relay-json";
import { relayJson, signalApiEndpoint } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

export type IndexNowState =
  | { state: "available"; keyStatus: "not_created" | "pr_open" | "deployed" | "mismatch"; canCreate?: boolean; reason: string; submissions: readonly { changeId: string; urls: readonly string[]; state: string; providerStatus: number | null; reason: string; recordedAt: string }[] }
  | { state: "unavailable" | "invalid" | "rejected" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const REASON = /^[A-Z][A-Z0-9_]{0,99}$/;
function exact(v: unknown, fields: string[]): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).sort().join("|") === fields.sort().join("|");
}

export async function loadIndexNow({ tenantToken, siteId, baseUrl, fetcher = globalThis.fetch }: { tenantToken: string; siteId: string; baseUrl?: string; fetcher?: typeof fetch }): Promise<IndexNowState> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(tenantToken) || !UUID.test(siteId)) return { state: "invalid" };
  try {
    const origin = validatedSignalApiBaseUrl(baseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const response = await relayJson(new URL(`/v1/sites/${siteId}/indexnow`, origin), { cache: "no-store", redirect: "error", headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(2500) }, fetcher, 131072);
    if ([401, 403].includes(response.status)) return { state: "rejected" };
    if (response.status === 503) return { state: "unavailable" };
    if (response.status !== 200) return { state: "invalid" };
    try {
    const raw = await boundedRelayText(response, 128 * 1024);
    if (Buffer.byteLength(raw) > 128 * 1024) return { state: "invalid" };
    const p: unknown = JSON.parse(raw);
    const fields = ["schema_version", "site_id", "correlation_id", "key_status", "key_id", "reason", "submissions"];
    const extended = exact(p, [...fields, "key_creation_available"]) && typeof p.key_creation_available === "boolean";
    if ((!exact(p, fields) && !extended) || p.schema_version !== 1 || p.site_id !== siteId || typeof p.correlation_id !== "string" || p.correlation_id.length < 1 || p.correlation_id.length > 64 || !["not_created", "pr_open", "deployed", "mismatch"].includes(String(p.key_status)) || (p.key_id !== null && !UUID.test(String(p.key_id))) || !REASON.test(String(p.reason)) || !Array.isArray(p.submissions) || p.submissions.length > 20) return { state: "invalid" };
    const submissions = p.submissions.map(s => {
      if (!exact(s, ["change_id", "urls", "state", "provider_status", "reason", "recorded_at"]) || !UUID.test(String(s.change_id)) || !Array.isArray(s.urls) || s.urls.length < 1 || s.urls.length > 32 || !["accepted", "rejected", "skipped", "retry", "outcome_unknown", "exhausted"].includes(String(s.state)) || !REASON.test(String(s.reason)) || typeof s.recorded_at !== "string" || !Number.isFinite(Date.parse(s.recorded_at)) || (s.provider_status !== null && (!Number.isInteger(s.provider_status) || Number(s.provider_status) < 100 || Number(s.provider_status) > 599))) throw new Error("invalid");
      const urls = s.urls.map(url => { if (typeof url !== "string" || url.length > 2048) throw new Error("invalid"); const u = new URL(url); if (u.protocol !== "https:" || u.username || u.password || u.hash) throw new Error("invalid"); return url; });
      return { changeId: String(s.change_id), urls, state: String(s.state), providerStatus: s.provider_status === null ? null : Number(s.provider_status), reason: String(s.reason), recordedAt: s.recorded_at };
    });
    return { state: "available", keyStatus: p.key_status as "not_created" | "pr_open" | "deployed" | "mismatch", canCreate: p.key_creation_available === true, reason: String(p.reason), submissions };
    } catch { return { state: "invalid" }; }
  } catch { return { state: "unavailable" }; }
}

export async function createIndexNowKey(token: string, siteId: string, requestId: string, origin: string, fetcher = globalThis.fetch): Promise<Response> {
  const failed = (status: number, state = "unavailable") => Response.json({ state }, { status, headers: { "Cache-Control": "no-store" } });
  if (!/^[A-Za-z0-9_-]{43}$/.test(token) || !UUID.test(siteId) || !UUID.test(requestId) || requestId[14] !== "4") return failed(403);
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${token}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), { headers: { Cookie: cookie, Accept: "application/json" }, signal: AbortSignal.timeout(2500) }, fetcher, 4096);
    const csrf: unknown = await proof.json();
    if (proof.status !== 200 || !exact(csrf, ["schema_version", "csrf_token"]) || csrf.schema_version !== 1 || typeof csrf.csrf_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(csrf.csrf_token)) throw new Error();
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/indexnow/key`), { method: "POST", headers: { Cookie: cookie, Accept: "application/json", "Content-Type": "application/json", Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token }, body: JSON.stringify({ schema_version: 1, request_id: requestId }), signal: AbortSignal.timeout(90000) }, fetcher, 4096);
    const value: unknown = await response.json();
    if (response.status !== 200) return failed(response.status, exact(value, ["code"]) && value.code === "INDEXNOW_STEP_UP_REQUIRED" ? "step_up_required" : "unavailable");
    if (!exact(value, ["schema_version", "state", "revision_id", "revision_sha256"]) || value.schema_version !== 1 || value.state !== "sealed" || typeof value.revision_id !== "string" || !UUID.test(value.revision_id) || typeof value.revision_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.revision_sha256)) throw new Error();
    return Response.json(value, { headers: { "Cache-Control": "no-store" } });
  } catch { return failed(503); }
}
