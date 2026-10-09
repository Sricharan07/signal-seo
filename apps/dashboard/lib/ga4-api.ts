import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { validBrandDocumentId as uuid } from "./brand-document-api";

export const GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly";
export const GA4_ATTEMPT_COOKIE = "__Host-signal-ga4-attempt";
const tokenPattern = /^[A-Za-z0-9_-]{43}$/;
const propertyPattern = /^properties\/[1-9][0-9]{0,19}$/;
export type Ga4State = { state: "unavailable" } | {
  state: "disconnected" | "read_only"; binding_id: string | null;
  property_resource_name: string | null; scope: string; generation_id: string | null;
  coverage: Record<string, unknown> | null; imported_at: string | null;
  selection: { attempt_id: string; properties: { resource_name: string; display_name: string }[] } | null;
};

function exact(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}

export function validGa4State(value: unknown): value is Ga4State {
  if (!exact(value, ["state", "binding_id", "property_resource_name", "scope", "generation_id", "coverage", "imported_at", "selection"]) ||
      !["disconnected", "read_only"].includes(String(value.state)) || value.scope !== GA4_SCOPE ||
      !(value.binding_id === null || uuid(value.binding_id)) ||
      !(value.property_resource_name === null || typeof value.property_resource_name === "string" && propertyPattern.test(value.property_resource_name)) ||
      !(value.generation_id === null || uuid(value.generation_id)) ||
      !(value.imported_at === null || typeof value.imported_at === "string" && !Number.isNaN(Date.parse(value.imported_at))) ||
      (value.state === "read_only") !== (value.binding_id !== null && value.property_resource_name !== null)) return false;
  if (value.coverage !== null && (typeof value.coverage !== "object" || Array.isArray(value.coverage) ||
      (value.coverage as Record<string, unknown>).complete !== false ||
      (value.coverage as Record<string, unknown>).missing_data !== "unknown_not_zero")) return false;
  if (value.selection !== null) {
    if (!exact(value.selection, ["attempt_id", "properties"]) || !uuid(value.selection.attempt_id) ||
        !Array.isArray(value.selection.properties) || value.selection.properties.length > 100 ||
        !value.selection.properties.every(item => exact(item, ["resource_name", "display_name"]) &&
          typeof item.resource_name === "string" && propertyPattern.test(item.resource_name) &&
          typeof item.display_name === "string" && item.display_name.length <= 256)) return false;
  }
  return true;
}

export function ga4AuthorizationUrl(value: unknown, origin: string): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.origin !== "https://accounts.google.com" || url.pathname !== "/o/oauth2/v2/auth" ||
        url.username || url.password || url.hash ||
        [...url.searchParams.keys()].sort().join(",") !== "access_type,client_id,code_challenge,code_challenge_method,include_granted_scopes,prompt,redirect_uri,response_type,scope,state" ||
        url.searchParams.get("scope") !== GA4_SCOPE || url.searchParams.get("access_type") !== "offline" ||
        url.searchParams.get("prompt") !== "consent" || url.searchParams.get("response_type") !== "code" ||
        url.searchParams.get("include_granted_scopes") !== "false" || url.searchParams.get("code_challenge_method") !== "S256" ||
        url.searchParams.get("redirect_uri") !== origin + "/auth/ga4/callback" ||
        !tokenPattern.test(url.searchParams.get("state") ?? "") || !tokenPattern.test(url.searchParams.get("code_challenge") ?? "") ||
        !/^[A-Za-z0-9._-]{16,256}\.apps\.googleusercontent\.com$/.test(url.searchParams.get("client_id") ?? "")) return null;
    return url.href;
  } catch { return null; }
}

async function boundedJson(response: Response): Promise<unknown> {
  if (response.headers.has("set-cookie") || response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error();
  return JSON.parse(await boundedRelayText(response, 65536));
}

export async function relayGa4(token: string, site: string, origin: string, command?: Record<string, unknown>, fetcher = globalThis.fetch): Promise<Record<string, unknown> | null> {
  if (!tokenPattern.test(token) || !uuid(site)) return null;
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const cookie = `${TENANT_COOKIE_NAME}=${token}`;
    let csrf = "";
    if (command) {
      const response = await relayJson(new URL("/v1/session/tenant-csrf", base), {
        cache: "no-store", redirect: "error", signal: AbortSignal.timeout(2500), headers: { Accept: "application/json", Cookie: cookie },
      }, fetcher, 256000);
      const data = await boundedJson(response);
      if (response.status !== 200 || !exact(data, ["schema_version", "csrf_token"]) || data.schema_version !== 1 ||
          typeof data.csrf_token !== "string" || !tokenPattern.test(data.csrf_token)) return null;
      csrf = data.csrf_token;
    }
    const response = await relayJson(new URL(`/v1/sites/${site}/ga4`, base), {
      method: command ? "POST" : "GET", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(90000),
      headers: { Accept: "application/json", Cookie: cookie, ...(command ? {
        "Content-Type": "application/json", Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf,
      } : {}) }, ...(command ? { body: JSON.stringify(command) } : {}),
    }, fetcher, 256000);
    if (response.status !== 200) return null;
    const value = await boundedJson(response);
    if (!command) return validGa4State(value) ? value as Record<string, unknown> : null;
    if (command.operation === "connect") return exact(value, ["attempt_id", "authorization_url"]) && uuid(value.attempt_id) &&
      ga4AuthorizationUrl(value.authorization_url, origin) ? value : null;
    if (command.operation === "complete") return exact(value, ["attempt_id", "properties"]) && uuid(value.attempt_id) && Array.isArray(value.properties) ? { state: "selecting" } : null;
    if (command.operation === "select") return exact(value, ["binding_id", "property_resource_name", "state"]) && uuid(value.binding_id) &&
      value.property_resource_name === command.property_resource_name && value.state === "read_only" ? value : null;
    if (command.operation === "import") return exact(value, ["generation_id", "coverage", "state"]) && uuid(value.generation_id) && value.state === "imported" ? { state: "imported" } : null;
    if (command.operation === "disconnect") return exact(value, ["state", "upstream_revoked"]) &&
      ["revoked", "AUTHORITY_DURABILITY_PENDING"].includes(String(value.state)) && typeof value.upstream_revoked === "boolean" ? value : null;
    return null;
  } catch { return null; }
}

export function ga4Command(value: unknown): { site: string; command: Record<string, unknown> } | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  if (!uuid(body.site_id)) return null;
  const base = ["site_id", "operation"];
  if (body.operation === "connect") { if (!exact(body, base)) return null; }
  else if (body.operation === "select") {
    if (!exact(body, [...base, "attempt_id", "property_resource_name"]) || !uuid(body.attempt_id) ||
        typeof body.property_resource_name !== "string" || !propertyPattern.test(body.property_resource_name)) return null;
  } else if (body.operation === "disconnect") {
    if (!exact(body, [...base, "binding_id"]) || !uuid(body.binding_id)) return null;
  } else if (body.operation === "import") {
    if (!exact(body, [...base, "start_date", "end_date"]) || ![body.start_date, body.end_date].every(v => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v))) return null;
    const days = (Date.parse(String(body.end_date)) - Date.parse(String(body.start_date))) / 86400000;
    if (!Number.isFinite(days) || days < 0 || days > 92) return null;
  } else return null;
  const { site_id, ...command } = body;
  return { site: site_id as string, command };
}
