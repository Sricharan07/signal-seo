import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

const MAX_SESSION_BYTES = 16 * 1024;
const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SESSION_FIELDS = [
  "active_site_id",
  "authentication_level",
  "expires_at",
  "role_key",
  "schema_version",
  "session_version",
  "tenant_id",
  "user_id",
];
const ROLES = new Set(["viewer", "analyst", "editor", "approver", "admin", "owner"]);

export const SESSION_COOKIE_NAME = TENANT_COOKIE_NAME;

export type DashboardSession =
  | { state: "signed_out" }
  | { state: "unavailable" }
  | { state: "invalid" }
  | {
      state: "authenticated";
      tenantId: string;
      role: "viewer" | "analyst" | "editor" | "approver" | "admin" | "owner";
      authenticationLevel: "primary" | "mfa";
      expiresAt: string;
      sessionVersion: number;
      activeSiteId: string | null;
    };

interface LoadSessionOptions {
  sessionTokens: readonly string[];
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
  now?: () => Date;
}

export async function loadDashboardSession({
  sessionTokens,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
  now = () => new Date(),
}: LoadSessionOptions): Promise<DashboardSession> {
  if (sessionTokens.length === 0) return { state: "signed_out" };
  if (sessionTokens.length !== 1 || !SESSION_TOKEN.test(sessionTokens[0] ?? "")) {
    return { state: "invalid" };
  }

  let baseUrl: URL;
  try {
    baseUrl = validatedSignalApiBaseUrl(
      configuredBaseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000",
    );
  } catch {
    return { state: "unavailable" };
  }

  let response: Response;
  try {
    response = await relayJson(new URL("/v1/session", baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        Cookie: `${SESSION_COOKIE_NAME}=${sessionTokens[0]}`,
      },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 16384);
  } catch {
    return { state: "unavailable" };
  }

  if (response.status === 401) return { state: "signed_out" };
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };

  try {
    return await readCurrentSession(response, now());
  } catch {
    return { state: "invalid" };
  }
}

async function readCurrentSession(response: Response, currentTime: Date): Promise<DashboardSession> {
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim();
  if (contentType !== "application/json") throw new Error("Session content type invalid");
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_SESSION_BYTES)
  ) {
    throw new Error("Session length invalid");
  }
  const body = await boundedRelayText(response, MAX_SESSION_BYTES);
  if (new TextEncoder().encode(body).byteLength > MAX_SESSION_BYTES) {
    throw new Error("Session response too large");
  }
  return validateCurrentSession(JSON.parse(body), currentTime);
}

function validateCurrentSession(value: unknown, currentTime: Date): DashboardSession {
  if (!isRecord(value) || Object.keys(value).sort().join(",") !== SESSION_FIELDS.join(",")) {
    throw new Error("Session response invalid");
  }
  if (
    value.schema_version !== 2 ||
    typeof value.tenant_id !== "string" ||
    !UUID.test(value.tenant_id) ||
    typeof value.user_id !== "string" ||
    !UUID.test(value.user_id) ||
    typeof value.role_key !== "string" ||
    !ROLES.has(value.role_key) ||
    (value.authentication_level !== "primary" && value.authentication_level !== "mfa") ||
    typeof value.expires_at !== "string" ||
    typeof value.session_version !== "number" ||
    !Number.isSafeInteger(value.session_version) ||
    value.session_version < 1 ||
    (value.active_site_id !== null &&
      (typeof value.active_site_id !== "string" || !UUID.test(value.active_site_id)))
  ) {
    throw new Error("Session response invalid");
  }
  const expiresAt = new Date(value.expires_at);
  const remaining = expiresAt.valueOf() - currentTime.valueOf();
  if (!Number.isFinite(remaining) || remaining <= 0 || remaining > 86_400_000) {
    throw new Error("Session expiry invalid");
  }
  return {
    state: "authenticated",
    tenantId: value.tenant_id,
    role: value.role_key as Extract<DashboardSession, { state: "authenticated" }>["role"],
    authenticationLevel: value.authentication_level,
    expiresAt: expiresAt.toISOString(),
    sessionVersion: value.session_version,
    activeSiteId: value.active_site_id,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
