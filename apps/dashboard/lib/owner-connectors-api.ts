import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { SLACK_UUID } from "./slack-api";
import { validatedDashboardOrigin } from "./browser-auth";

export type GithubScope = {
  github_installation_id: number; github_owner: string; github_repository: string;
  github_base_branch: string; github_content_path: string;
};
export type OwnerConnectorConfiguration = { property: string; github: GithubScope };
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
export type OwnerConnector = "gsc" | "github" | "github-pr" | "bing";
export type GithubPrState = { availability: "unavailable" | "ungranted" } | {
  availability: "prepared" | "observed" | "failed" | "stale";
  extension_id: string; idempotency_key: string; binding_id: string; owner: string; repository: string;
  base_branch: string; content_path: string;
};
export type BingState = { availability: "unavailable" | "unbound" } |
  { availability: "bound" | "reauth_required"; binding_id: string; site_url: string } |
  { availability: "selecting"; attempt_id: string; sites: { url: string }[] };
export type GscState = { availability: "unavailable" | "unbound" } |
  { availability: "bound" | "reauth_required"; binding_id: string; property_resource_name: string } |
  { availability: "selecting"; attempt_id: string; properties: { resource_name: string; property_type: "url_prefix" }[] };
export type GithubState = { availability: "unavailable" | "unbound" } | {
  availability: "prepared" | "active" | "failed" | "stale";
  binding_id: string; installation_id: number; owner: string; repository: string;
  base_branch: string; content_path: string; repository_id: number | null;
  base_sha: string | null; failure_code: string | null;
  base_protection: "protected" | "owner_accepted_unprotected" | "unprotected_not_accepted";
};


async function json(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.has("set-cookie")) throw new Error("Unexpected cookie");
  const text = await boundedRelayText(response, 16384);
  if (new TextEncoder().encode(text).byteLength > 4096) throw new Error("Oversized response");
  const value: unknown = JSON.parse(text);
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Invalid response");
  return value as Record<string, unknown>;
}

function uuid(value: unknown): value is string { return typeof value === "string" && SLACK_UUID.test(value); }
function keys(value: Record<string, unknown>, expected: string): boolean { return Object.keys(value).sort().join(",") === expected; }

export function ownerConnectorConfiguration(): OwnerConnectorConfiguration | null {
  try {
    const origin = validatedDashboardOrigin();
    if (!origin.startsWith("https://")) return null;
    const target: unknown = JSON.parse(process.env.SIGNAL_OWNER_CONNECTOR_SCOPE ?? "null");
    if (target === null || typeof target !== "object" || Array.isArray(target)) return null;
    const value = target as Record<string, unknown>;
    if (!keys(value, "github_base_branch,github_content_path,github_installation_id,github_owner,github_repository") ||
        typeof value.github_installation_id !== "number" || !Number.isSafeInteger(value.github_installation_id) || value.github_installation_id < 1 ||
        typeof value.github_owner !== "string" || !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(value.github_owner) ||
        typeof value.github_repository !== "string" || !/^[A-Za-z0-9_.-]{1,100}$/.test(value.github_repository) ||
        typeof value.github_base_branch !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$/.test(value.github_base_branch) ||
        typeof value.github_content_path !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_./-]{0,200}$/.test(value.github_content_path) ||
        value.github_content_path.split("/").includes("..")) return null;
    return { property: origin + "/", github: value as GithubScope };
  } catch { return null; }
}

export function gscState(value: Record<string, unknown>, property: string | null = null): GscState {
  if (property === null) return { availability: "unavailable" };
  if (value.availability === "unbound" && keys(value, "availability")) return { availability: "unbound" };
  if (["bound", "reauth_required"].includes(String(value.availability)) &&
      keys(value, "availability,binding_id,property_resource_name") && uuid(value.binding_id) &&
      value.property_resource_name === property) return value as GscState;
  if (value.availability === "selecting" && keys(value, "attempt_id,availability,properties") && uuid(value.attempt_id) &&
      Array.isArray(value.properties) && value.properties.length === 1 && value.properties.every(item =>
        item !== null && typeof item === "object" && !Array.isArray(item) &&
        keys(item, "property_type,resource_name") && item.resource_name === property && item.property_type === "url_prefix")) return value as GscState;
  return { availability: "unavailable" };
}

export function githubState(value: Record<string, unknown>, target: GithubScope | null = null): GithubState {
  if (target === null) return { availability: "unavailable" };
  if (value.availability === "unbound" && keys(value, "availability")) return { availability: "unbound" };
  if (!["prepared", "active", "failed", "stale"].includes(String(value.availability)) ||
      !keys(value, "availability,base_branch,base_protection,base_sha,binding_id,content_path,failure_code,installation_id,owner,repository,repository_id") ||
      !["protected", "owner_accepted_unprotected", "unprotected_not_accepted"].includes(String(value.base_protection)) ||
      value.base_protection === "owner_accepted_unprotected" && value.availability !== "active" ||
      !uuid(value.binding_id) || value.installation_id !== target.github_installation_id || value.owner !== target.github_owner ||
      value.repository !== target.github_repository || value.base_branch !== target.github_base_branch || value.content_path !== target.github_content_path ||
      !(value.repository_id === null || typeof value.repository_id === "number" && Number.isSafeInteger(value.repository_id) && value.repository_id > 0) ||
      !(value.base_sha === null || typeof value.base_sha === "string" && /^[a-f0-9]{40}$/.test(value.base_sha)) ||
      !(value.failure_code === null || typeof value.failure_code === "string" && /^[A-Z0-9_]{1,80}$/.test(value.failure_code)) ||
      value.availability === "active" && (value.repository_id === null || value.base_sha === null)) return { availability: "unavailable" };
  return value as GithubState;
}

export function bingState(value: Record<string, unknown>, siteUrl: string | null): BingState {
  if (siteUrl === null) return { availability: "unavailable" };
  if (value.availability === "unbound" && keys(value, "availability")) return { availability: "unbound" };
  if (["bound", "reauth_required"].includes(String(value.availability)) &&
      keys(value, "availability,binding_id,site_url") && uuid(value.binding_id) && value.site_url === siteUrl) return value as BingState;
  if (value.availability === "selecting" && keys(value, "attempt_id,availability,sites") && uuid(value.attempt_id) &&
      Array.isArray(value.sites) && value.sites.length === 1 && value.sites.every(s => s !== null &&
        typeof s === "object" && !Array.isArray(s) && keys(s, "url") && s.url === siteUrl)) return value as BingState;
  return { availability: "unavailable" };
}

export async function loadOwnerConnector({ connector, tenantToken, siteId, fetcher = globalThis.fetch,
  configuration = ownerConnectorConfiguration() }: {
  connector: OwnerConnector; tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
  configuration?: OwnerConnectorConfiguration | null;
}): Promise<GscState | GithubState | GithubPrState | BingState> {
  if (configuration === null || !TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId)) return { availability: "unavailable" };
  try {
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/${connector}`), {
      cache: "no-store", redirect: "error", headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` },
      signal: AbortSignal.timeout(5000),
    }, fetcher, 16384);
    if (response.status !== 200) return { availability: "unavailable" };
    const data = await json(response);
    return connector === "gsc" ? gscState(data, configuration.property) : connector === "bing" ? bingState(data, configuration.property) : connector === "github" ? githubState(data, configuration.github) : githubPrState(data, configuration.github);
  } catch { return { availability: "unavailable" }; }
}

export function githubPrState(value: Record<string, unknown>, target: GithubScope | null): GithubPrState {
  if (target === null) return { availability: "unavailable" };
  if (value.availability === "ungranted" && keys(value, "availability")) return { availability: "ungranted" };
  if (!["prepared", "observed", "failed", "stale"].includes(String(value.availability)) ||
      !keys(value, "availability,base_branch,binding_id,content_path,extension_id,idempotency_key,owner,repository") ||
      !uuid(value.binding_id) || !uuid(value.extension_id) || !uuid(value.idempotency_key) || value.owner !== target.github_owner ||
      value.repository !== target.github_repository || value.base_branch !== target.github_base_branch ||
      value.content_path !== target.github_content_path) return { availability: "unavailable" };
  return value as GithubPrState;
}

export async function mutateOwnerConnector({ connector, tenantToken, siteId, origin, command, fetcher = globalThis.fetch }: {
  connector: OwnerConnector; tenantToken: string; siteId: string; origin: string;
  command: Record<string, unknown>; fetcher?: typeof globalThis.fetch;
}): Promise<Record<string, unknown> | null> {
  if (!TOKEN.test(tenantToken) || !SLACK_UUID.test(siteId)) return null;
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), {
      cache: "no-store", redirect: "error", headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(2500),
    }, fetcher, 16384);
    const csrf = await json(proof);
    if (proof.status !== 200 || !keys(csrf, "csrf_token,schema_version") || csrf.schema_version !== 1 ||
        typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token)) return null;
    const response = await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/${connector}`), {
      method: "POST", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
      headers: { Accept: "application/json", Cookie: cookie, "Content-Type": "application/json",
        Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token },
      body: JSON.stringify(command),
    }, fetcher, 16384);
    return response.status === 200 ? await json(response) : null;
  } catch { return null; }
}

export function gscAuthorizationUrl(value: unknown, origin: string): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.origin !== "https://accounts.google.com" || url.pathname !== "/o/oauth2/v2/auth" || url.hash || url.username || url.password ||
        [...url.searchParams.keys()].sort().join(",") !== "access_type,client_id,code_challenge,code_challenge_method,include_granted_scopes,prompt,redirect_uri,response_type,scope,state" ||
        !/^[A-Za-z0-9_-]{16,256}\.apps\.googleusercontent\.com$/.test(url.searchParams.get("client_id") ?? "") ||
        url.searchParams.get("scope") !== "https://www.googleapis.com/auth/webmasters.readonly" ||
        url.searchParams.get("redirect_uri") !== origin+"/auth/gsc/callback" ||
        url.searchParams.get("response_type") !== "code" || url.searchParams.get("access_type") !== "offline" ||
        url.searchParams.get("prompt") !== "consent" || url.searchParams.get("include_granted_scopes") !== "false" ||
        url.searchParams.get("code_challenge_method") !== "S256" ||
        !TOKEN.test(url.searchParams.get("state") ?? "") || !TOKEN.test(url.searchParams.get("code_challenge") ?? "")) return null;
    return url.href;
  } catch { return null; }
}

export function bingAuthorizationUrl(value: unknown, origin: string): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.origin !== "https://www.bing.com" || url.pathname !== "/webmasters/oauth/authorize" || url.hash || url.username || url.password ||
        [...url.searchParams.keys()].sort().join(",") !== "client_id,redirect_uri,response_type,scope,state" ||
        !/^[\x21-\x7e]{16,256}$/.test(url.searchParams.get("client_id") ?? "") ||
        url.searchParams.get("redirect_uri") !== origin + "/auth/bing/callback" ||
        url.searchParams.get("response_type") !== "code" || url.searchParams.get("scope") !== "webmaster.read" ||
        !TOKEN.test(url.searchParams.get("state") ?? "")) return null;
    return url.href;
  } catch { return null; }
}
