import { validatedSignalApiBaseUrl } from "./api-origin";

export function browserCookieConfiguration(
  environment = process.env.NODE_ENV,
  localPilot = process.env.SIGNAL_LOCAL_PILOT,
  localPilotGeneration = process.env.SIGNAL_LOCAL_PILOT_COOKIE_GENERATION,
) {
  if (environment === "development" && localPilot === "1") {
    const namespace =
      localPilotGeneration !== undefined && /^[0-9a-f]{8}$/.test(localPilotGeneration)
        ? `${localPilotGeneration}_`
        : "";
    return {
      identity: `signal_local_${namespace}identity`,
      tenant: `signal_local_${namespace}session`,
      oidcBinding: `signal_local_${namespace}oidc_binding`,
      invitationIdentity: `signal_local_${namespace}invitation_identity`,
      secure: false,
    } as const;
  }
  return {
    identity: "__Host-signal_identity",
    tenant: "__Host-signal_session",
    oidcBinding: "__Host-signal_oidc_binding",
    invitationIdentity: "__Host-signal_invitation_identity",
    secure: true,
  } as const;
}

const COOKIE_CONFIGURATION = browserCookieConfiguration();

export const IDENTITY_COOKIE_NAME = COOKIE_CONFIGURATION.identity;
export const TENANT_COOKIE_NAME = COOKIE_CONFIGURATION.tenant;
export const OIDC_BINDING_COOKIE_NAME = COOKIE_CONFIGURATION.oidcBinding;
export const INVITATION_IDENTITY_COOKIE_NAME = COOKIE_CONFIGURATION.invitationIdentity;

const AUTH_COOKIE_NAMES = [
  IDENTITY_COOKIE_NAME,
  TENANT_COOKIE_NAME,
  OIDC_BINDING_COOKIE_NAME,
  INVITATION_IDENTITY_COOKIE_NAME,
] as const;
const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const AUTHORIZATION_CODE = /^[\x21-\x7e]{1,4096}$/;
const CSRF_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const ROLES = new Set(["viewer", "analyst", "editor", "approver", "admin", "owner"]);
const MAX_JSON_BYTES = 16 * 1024;
const MAX_COOKIE_BYTES = 4096;
const CALLBACK_DESTINATION = "/?auth=identity-ready";

type AuthCookieName = (typeof AUTH_COOKIE_NAMES)[number];
type CookieDisposition = "clear" | "set";

export type AuthRelayResult =
  | { state: "redirect"; location: string; cookies: string[] }
  | { state: "not_ready" | "rejected" | "conflict" | "failed"; cookies: string[] };

interface BrowserAuthOptions {
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
}

interface BeginLoginOptions extends BrowserAuthOptions {
  identityProviderOrigin?: string;
  environment?: string;
  invitation?: boolean;
  returnPath?: string;
}

interface CompleteLoginOptions extends BrowserAuthOptions {
  state: string;
  code: string;
  browserBinding: string;
}

interface SelectOrganizationOptions extends BrowserAuthOptions {
  identityToken: string;
  tenantId: string;
  dashboardOrigin: string;
  now?: () => Date;
}

interface LogoutOptions extends BrowserAuthOptions {
  cookieHeader: string;
  dashboardOrigin: string;
}

interface SelectSiteOptions extends BrowserAuthOptions {
  tenantToken: string;
  siteId: string;
  expectedSessionVersion: number;
  dashboardOrigin: string;
}

interface OnboardSiteOptions extends BrowserAuthOptions {
  tenantToken: string;
  idempotencyKey: string;
  name: string;
  primaryOrigin: string;
  timezone: string;
  reportingCurrency: string;
  expectedSessionVersion: number;
  dashboardOrigin: string;
}

export function validatedDashboardOrigin(
  configured = process.env.SIGNAL_DASHBOARD_ORIGIN,
  environment = process.env.NODE_ENV,
): string {
  const candidate = configured ?? (environment === "production" ? "" : "http://localhost:3000");
  return validatedOrigin(candidate, {
    allowLocalHttp: environment !== "production",
    label: "dashboard",
  });
}

export function dashboardMutationAccepted(request: Request, dashboardOrigin: string): boolean {
  if (request.method !== "POST" || request.headers.get("sec-fetch-site") !== "same-origin") {
    return false;
  }
  const origin = request.headers.get("origin");
  if (origin === dashboardOrigin) return true;
  if (origin !== "null") return false;
  try {
    const dashboard = new URL(dashboardOrigin);
    return dashboard.protocol === "http:" && isLoopbackHost(dashboard.hostname);
  } catch {
    return false;
  }
}

export function exactCookie(cookieHeader: string | null, name: AuthCookieName): string | null {
  if (cookieHeader === null || cookieHeader.length > 16 * 1024) return null;
  const values: string[] = [];
  for (const segment of cookieHeader.split(";")) {
    const separator = segment.indexOf("=");
    if (separator < 1) continue;
    if (segment.slice(0, separator).trim() === name) {
      values.push(segment.slice(separator + 1).trim());
    }
  }
  return values.length === 1 && SESSION_TOKEN.test(values[0] ?? "") ? values[0] : null;
}

export function clearBrowserAuthCookies(): string[] {
  return AUTH_COOKIE_NAMES.map(
    (name) => `${name}=""; Path=/; Max-Age=0; HttpOnly${secureAttribute()}; SameSite=Lax`,
  );
}

export async function beginSignalLogin({
  baseUrl: configuredBaseUrl,
  identityProviderOrigin: configuredProviderOrigin,
  environment = process.env.NODE_ENV,
  fetcher = globalThis.fetch,
  invitation = false,
  returnPath = CALLBACK_DESTINATION,
}: BeginLoginOptions = {}): Promise<AuthRelayResult> {
  if (invitation ? returnPath !== "/invitations/accept" :
    returnPath !== CALLBACK_DESTINATION && !invitationFinishPath(returnPath)) {
    return { state: "rejected", cookies: [] };
  }
  let baseUrl: URL;
  let providerOrigin: string;
  try {
    baseUrl = signalApiUrl(configuredBaseUrl);
    providerOrigin = validatedOrigin(
      configuredProviderOrigin ?? process.env.SIGNAL_IDENTITY_PROVIDER_ORIGIN ?? "",
      { allowLocalHttp: environment !== "production", label: "identity provider" },
    );
  } catch {
    return { state: "not_ready", cookies: [] };
  }

  let response: Response;
  try {
    const loginUrl = new URL(invitation ? "/v1/invitations/verify" : "/v1/session/login", baseUrl);
    loginUrl.searchParams.set("return_path", returnPath);
    response = await fetcher(loginUrl, boundedRequest());
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (response.status === 503) return safeError(response, "not_ready");
  if (response.status !== 303) return safeError(response, "failed");

  try {
    const location = validatedProviderRedirect(response.headers.get("location"), providerOrigin);
    const cookies = validatedResponseCookies(response, invitation ? {
      [OIDC_BINDING_COOKIE_NAME]: "set",
      [INVITATION_IDENTITY_COOKIE_NAME]: "clear",
    } as const : {
      [IDENTITY_COOKIE_NAME]: "clear",
      [TENANT_COOKIE_NAME]: "clear",
      [OIDC_BINDING_COOKIE_NAME]: "set",
      [INVITATION_IDENTITY_COOKIE_NAME]: "clear",
    } as const);
    return { state: "redirect", location, cookies };
  } catch {
    return { state: "failed", cookies: [] };
  }
}

export async function completeSignalLogin({
  baseUrl: configuredBaseUrl,
  state,
  code,
  browserBinding,
  fetcher = globalThis.fetch,
}: CompleteLoginOptions): Promise<AuthRelayResult> {
  if (
    !SESSION_TOKEN.test(state) ||
    !AUTHORIZATION_CODE.test(code) ||
    !SESSION_TOKEN.test(browserBinding)
  ) {
    return {
      state: "rejected",
      cookies: [clearCookie(OIDC_BINDING_COOKIE_NAME)],
    };
  }

  let baseUrl: URL;
  try {
    baseUrl = signalApiUrl(configuredBaseUrl);
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  let response: Response;
  try {
    const callbackUrl = new URL("/v1/session/callback", baseUrl);
    callbackUrl.searchParams.set("state", state);
    callbackUrl.searchParams.set("code", code);
    response = await fetcher(callbackUrl, {
      ...boundedRequest(),
      headers: {
        Accept: "application/json",
        Cookie: `${OIDC_BINDING_COOKIE_NAME}=${browserBinding}`,
      },
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (response.status === 401) return safeError(response, "rejected");
  if (response.status === 503) return safeError(response, "not_ready");
  if (response.status !== 303) return safeError(response, "failed");

  try {
    const location = validatedLocalRedirect(response.headers.get("location"));
    const invitation = location === "/invitations/accept";
    if (location !== CALLBACK_DESTINATION && !invitation && !invitationFinishPath(location)) {
      throw new Error("Unexpected callback destination");
    }
    const cookies = validatedResponseCookies(response, invitation ? {
      [OIDC_BINDING_COOKIE_NAME]: "clear",
      [INVITATION_IDENTITY_COOKIE_NAME]: "set",
    } as const : {
      [IDENTITY_COOKIE_NAME]: "set",
      [TENANT_COOKIE_NAME]: "clear",
      [OIDC_BINDING_COOKIE_NAME]: "clear",
      [INVITATION_IDENTITY_COOKIE_NAME]: "clear",
    } as const);
    return { state: "redirect", location, cookies };
  } catch {
    return { state: "failed", cookies: [] };
  }
}

export function invitationFinishPath(path: string): boolean {
  return /^\/invitations\/finish\?tenant=[0-9a-f-]{36}&site=[0-9a-f-]{36}$/.test(path) &&
    UUID_V4.test(new URL(path, "https://local.invalid").searchParams.get("tenant") ?? "") &&
    UUID_V4.test(new URL(path, "https://local.invalid").searchParams.get("site") ?? "");
}

export async function acceptSignalInvitation({
  identityProof, invitationId, token, displayName, dashboardOrigin,
  fetcher = globalThis.fetch, baseUrl: configuredBaseUrl,
}: BrowserAuthOptions & {
  identityProof: string; invitationId: string; token: string; displayName: string; dashboardOrigin: string;
}): Promise<AuthRelayResult> {
  if (!SESSION_TOKEN.test(identityProof) || !SESSION_TOKEN.test(token) || !UUID_V4.test(invitationId) ||
    displayName.length < 1 || displayName.length > 200 || displayName !== displayName.trim() ||
    /[\u0000-\u001f\u007f]/.test(displayName)) return { state: "rejected", cookies: [] };
  try {
    const baseUrl = signalApiUrl(configuredBaseUrl);
    const cookie = `${INVITATION_IDENTITY_COOKIE_NAME}=${identityProof}`;
    const csrfResponse = await fetcher(new URL("/v1/invitations/csrf", baseUrl), {
      ...boundedRequest(), headers: { Accept: "application/json", Cookie: cookie },
    });
    const csrf = await readJson(csrfResponse);
    if (csrfResponse.status !== 200 || !hasExactFields(csrf, ["csrf_token", "schema_version"]) ||
      csrf.schema_version !== 1 || typeof csrf.csrf_token !== "string" || !CSRF_TOKEN.test(csrf.csrf_token) ||
      csrfResponse.headers.has("set-cookie")) throw new Error();
    const response = await fetcher(new URL("/v1/invitations/accept", baseUrl), {
      ...boundedRequest(), method: "POST", headers: {
        Accept: "application/json", "Content-Type": "application/json", Cookie: cookie,
        Origin: dashboardOrigin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token,
      }, body: JSON.stringify({ invitation_id: invitationId, token, display_name: displayName }),
    });
    if (response.status === 403 || response.status === 401) return { state: "rejected", cookies: [] };
    if (response.status !== 200) return { state: "failed", cookies: [] };
    const body = await readJson(response);
    if (!hasExactFields(body, ["accepted_at", "invitation_id", "role_key", "schema_version", "site_id", "tenant_id", "user_id"]) ||
      body.schema_version !== 1 || body.invitation_id !== invitationId ||
      ![body.tenant_id, body.site_id, body.user_id].every(value => typeof value === "string" && UUID_V4.test(value)) ||
      typeof body.role_key !== "string" || !ROLES.has(body.role_key) || body.role_key === "owner" ||
      typeof body.accepted_at !== "string" || !Number.isFinite(Date.parse(body.accepted_at))) throw new Error();
    const cookies = validatedResponseCookies(response, { [INVITATION_IDENTITY_COOKIE_NAME]: "clear" } as const);
    return { state: "redirect", location: `/invitations/accepted?tenant=${body.tenant_id}&site=${body.site_id}`, cookies };
  } catch { return { state: "not_ready", cookies: [] }; }
}

export async function selectSignalOrganization({
  baseUrl: configuredBaseUrl,
  identityToken,
  tenantId,
  dashboardOrigin,
  fetcher = globalThis.fetch,
  now = () => new Date(),
}: SelectOrganizationOptions): Promise<AuthRelayResult> {
  if (!SESSION_TOKEN.test(identityToken) || !UUID.test(tenantId)) {
    return { state: "rejected", cookies: [] };
  }
  let baseUrl: URL;
  try {
    baseUrl = signalApiUrl(configuredBaseUrl);
    validatedOrigin(dashboardOrigin, { allowLocalHttp: true, label: "dashboard" });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  const cookie = `${IDENTITY_COOKIE_NAME}=${identityToken}`;
  let csrfResponse: Response;
  try {
    csrfResponse = await fetcher(new URL("/v1/session/csrf", baseUrl), {
      ...boundedRequest(),
      headers: { Accept: "application/json", Cookie: cookie },
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (csrfResponse.status === 401) return safeError(csrfResponse, "rejected");
  if (csrfResponse.status === 503) return safeError(csrfResponse, "not_ready");
  if (csrfResponse.status !== 200) return safeError(csrfResponse, "failed");

  let csrfToken: string;
  try {
    const document = await readJson(csrfResponse);
    if (
      !hasExactFields(document, ["csrf_token", "schema_version"]) ||
      document.schema_version !== 1 ||
      typeof document.csrf_token !== "string" ||
      !CSRF_TOKEN.test(document.csrf_token)
    ) {
      throw new Error("Invalid CSRF response");
    }
    csrfToken = document.csrf_token;
  } catch {
    return { state: "failed", cookies: [] };
  }

  let response: Response;
  try {
    response = await fetcher(new URL("/v1/session/switch-tenant", baseUrl), {
      ...boundedRequest(),
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Cookie: cookie,
        Origin: dashboardOrigin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ tenant_id: tenantId }),
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (response.status === 401 || response.status === 403) {
    return safeError(response, "rejected");
  }
  if (response.status === 503) return safeError(response, "not_ready");
  if (response.status !== 200) return safeError(response, "failed");

  try {
    validateTenantSessionResponse(await readJson(response), tenantId, now());
    const cookies = validatedResponseCookies(response, {
      [TENANT_COOKIE_NAME]: "set",
    } as const);
    return { state: "redirect", location: "/?auth=signed-in", cookies };
  } catch {
    return { state: "failed", cookies: [] };
  }
}

export async function logoutSignalSession({
  baseUrl: configuredBaseUrl,
  cookieHeader,
  dashboardOrigin,
  fetcher = globalThis.fetch,
}: LogoutOptions): Promise<AuthRelayResult> {
  const tenantToken = exactCookie(cookieHeader, TENANT_COOKIE_NAME);
  const identityToken = exactCookie(cookieHeader, IDENTITY_COOKIE_NAME);
  if (tenantToken === null && identityToken === null) {
    return { state: "rejected", cookies: [] };
  }
  let baseUrl: URL;
  try {
    baseUrl = signalApiUrl(configuredBaseUrl);
    validatedOrigin(dashboardOrigin, { allowLocalHttp: true, label: "dashboard" });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  const cookie = [
    tenantToken === null ? null : `${TENANT_COOKIE_NAME}=${tenantToken}`,
    identityToken === null ? null : `${IDENTITY_COOKIE_NAME}=${identityToken}`,
  ]
    .filter((value): value is string => value !== null)
    .join("; ");

  let csrfResponse: Response;
  try {
    csrfResponse = await fetcher(new URL("/v1/session/logout-csrf", baseUrl), {
      ...boundedRequest(),
      headers: { Accept: "application/json", Cookie: cookie },
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (csrfResponse.status === 401) return safeError(csrfResponse, "rejected");
  if (csrfResponse.status === 503) return safeError(csrfResponse, "not_ready");
  if (csrfResponse.status !== 200) return safeError(csrfResponse, "failed");

  let csrfToken: string;
  try {
    const document = await readJson(csrfResponse);
    if (
      !hasExactFields(document, ["csrf_token", "schema_version"]) ||
      document.schema_version !== 1 ||
      typeof document.csrf_token !== "string" ||
      !CSRF_TOKEN.test(document.csrf_token)
    ) {
      throw new Error("Invalid CSRF response");
    }
    csrfToken = document.csrf_token;
  } catch {
    return { state: "failed", cookies: [] };
  }

  let response: Response;
  try {
    response = await fetcher(new URL("/v1/session/logout", baseUrl), {
      ...boundedRequest(),
      method: "POST",
      headers: {
        Cookie: cookie,
        Origin: dashboardOrigin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrfToken,
      },
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (response.status === 401 || response.status === 403) {
    return safeError(response, "rejected");
  }
  if (response.status === 503) return safeError(response, "not_ready");
  if (response.status !== 202) return safeError(response, "failed");
  try {
    const document = await readJson(response);
    if (
      !hasExactFields(document, ["schema_version", "status"]) ||
      document.schema_version !== 1 ||
      document.status !== "AUTHORITY_DURABILITY_PENDING"
    ) {
      throw new Error("Invalid logout durability response");
    }
  } catch {
    return { state: "failed", cookies: [] };
  }

  try {
    const cookies = validatedResponseCookies(response, {
      [IDENTITY_COOKIE_NAME]: "clear",
      [TENANT_COOKIE_NAME]: "clear",
      [OIDC_BINDING_COOKIE_NAME]: "clear",
      [INVITATION_IDENTITY_COOKIE_NAME]: "clear",
    } as const);
    return { state: "redirect", location: "/?auth=logged-out", cookies };
  } catch {
    return { state: "failed", cookies: [] };
  }
}

export async function selectSignalSite({
  baseUrl: configuredBaseUrl,
  tenantToken,
  siteId,
  expectedSessionVersion,
  dashboardOrigin,
  fetcher = globalThis.fetch,
}: SelectSiteOptions): Promise<AuthRelayResult> {
  if (
    !SESSION_TOKEN.test(tenantToken) ||
    !UUID.test(siteId) ||
    !Number.isSafeInteger(expectedSessionVersion) ||
    expectedSessionVersion < 1
  ) {
    return { state: "rejected", cookies: [] };
  }
  let baseUrl: URL;
  try {
    baseUrl = signalApiUrl(configuredBaseUrl);
    validatedOrigin(dashboardOrigin, { allowLocalHttp: true, label: "dashboard" });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
  let csrfResponse: Response;
  try {
    csrfResponse = await fetcher(new URL("/v1/session/tenant-csrf", baseUrl), {
      ...boundedRequest(),
      headers: { Accept: "application/json", Cookie: cookie },
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (csrfResponse.status === 401) return safeError(csrfResponse, "rejected");
  if (csrfResponse.status === 503) return safeError(csrfResponse, "not_ready");
  if (csrfResponse.status !== 200) return safeError(csrfResponse, "failed");

  let csrfToken: string;
  try {
    const document = await readJson(csrfResponse);
    if (
      !hasExactFields(document, ["csrf_token", "schema_version"]) ||
      document.schema_version !== 1 ||
      typeof document.csrf_token !== "string" ||
      !CSRF_TOKEN.test(document.csrf_token) ||
      csrfResponse.headers.getSetCookie().length !== 0
    ) {
      throw new Error("Invalid tenant CSRF response");
    }
    csrfToken = document.csrf_token;
  } catch {
    return { state: "failed", cookies: [] };
  }

  let response: Response;
  try {
    response = await fetcher(new URL("/v1/session/site", baseUrl), {
      ...boundedRequest(),
      method: "PUT",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Cookie: cookie,
        Origin: dashboardOrigin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        site_id: siteId,
        expected_session_version: expectedSessionVersion,
      }),
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (response.status === 401 || response.status === 403) {
    return safeError(response, "rejected");
  }
  if (response.status === 409) return safeError(response, "conflict");
  if (response.status === 503) return safeError(response, "not_ready");
  if (response.status !== 200) return safeError(response, "failed");

  try {
    validateSiteSelectionResponse(
      await readJson(response),
      siteId,
      expectedSessionVersion,
    );
    if (response.headers.getSetCookie().length !== 0) {
      throw new Error("Site selection attempted to replace browser authority");
    }
    return { state: "redirect", location: "/?auth=site-selected", cookies: [] };
  } catch {
    return { state: "failed", cookies: [] };
  }
}

export async function onboardSignalSite({
  baseUrl: configuredBaseUrl,
  tenantToken,
  idempotencyKey,
  name,
  primaryOrigin,
  timezone,
  reportingCurrency,
  expectedSessionVersion,
  dashboardOrigin,
  fetcher = globalThis.fetch,
}: OnboardSiteOptions): Promise<AuthRelayResult> {
  if (
    !SESSION_TOKEN.test(tenantToken) ||
    !UUID_V4.test(idempotencyKey) ||
    !validDisplayText(name, 200) ||
    !validSiteOrigin(primaryOrigin) ||
    !validTimezone(timezone) ||
    !/^[A-Z]{3}$/.test(reportingCurrency) ||
    !Number.isSafeInteger(expectedSessionVersion) ||
    expectedSessionVersion < 1
  ) {
    return { state: "rejected", cookies: [] };
  }
  let baseUrl: URL;
  try {
    baseUrl = signalApiUrl(configuredBaseUrl);
    validatedOrigin(dashboardOrigin, { allowLocalHttp: true, label: "dashboard" });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
  let csrfResponse: Response;
  try {
    csrfResponse = await fetcher(new URL("/v1/session/tenant-csrf", baseUrl), {
      ...boundedRequest(),
      headers: { Accept: "application/json", Cookie: cookie },
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (csrfResponse.status === 401) return safeError(csrfResponse, "rejected");
  if (csrfResponse.status === 503) return safeError(csrfResponse, "not_ready");
  if (csrfResponse.status !== 200) return safeError(csrfResponse, "failed");

  let csrfToken: string;
  try {
    const document = await readJson(csrfResponse);
    if (
      !hasExactFields(document, ["csrf_token", "schema_version"]) ||
      document.schema_version !== 1 ||
      typeof document.csrf_token !== "string" ||
      !CSRF_TOKEN.test(document.csrf_token) ||
      csrfResponse.headers.getSetCookie().length !== 0
    ) {
      throw new Error("Invalid tenant CSRF response");
    }
    csrfToken = document.csrf_token;
  } catch {
    return { state: "failed", cookies: [] };
  }

  let response: Response;
  try {
    response = await fetcher(new URL("/v1/sites", baseUrl), {
      ...boundedRequest(),
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Cookie: cookie,
        Origin: dashboardOrigin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        idempotency_key: idempotencyKey,
        name,
        primary_origin: primaryOrigin,
        timezone,
        reporting_currency: reportingCurrency,
        expected_session_version: expectedSessionVersion,
      }),
    });
  } catch {
    return { state: "not_ready", cookies: [] };
  }
  if (response.status === 401 || response.status === 403) {
    return safeError(response, "rejected");
  }
  if (response.status === 409) return safeError(response, "conflict");
  if (response.status === 503) return safeError(response, "not_ready");
  if (response.status !== 200) return safeError(response, "failed");

  try {
    validateSiteOnboardingResponse(await readJson(response), {
      name,
      primaryOrigin,
      timezone,
      reportingCurrency,
      expectedSessionVersion,
    });
    if (response.headers.getSetCookie().length !== 0) {
      throw new Error("Site onboarding attempted to replace browser authority");
    }
    return { state: "redirect", location: "/?auth=site-created", cookies: [] };
  } catch {
    return { state: "failed", cookies: [] };
  }
}

function signalApiUrl(configured: string | undefined): URL {
  return validatedSignalApiBaseUrl(
    configured ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000",
  );
}

function boundedRequest(): RequestInit {
  return {
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(2500),
  };
}

async function safeError(
  response: Response,
  state: "not_ready" | "rejected" | "conflict" | "failed",
): Promise<AuthRelayResult> {
  try {
    return { state, cookies: validatedClearingCookies(response) };
  } catch {
    return { state: "failed", cookies: [] };
  }
}

function validatedClearingCookies(response: Response): string[] {
  const values = response.headers.getSetCookie();
  if (values.length > AUTH_COOKIE_NAMES.length) throw new Error("Too many cookies");
  const seen = new Set<AuthCookieName>();
  for (const value of values) {
    const parsed = parseSetCookie(value);
    if (seen.has(parsed.name) || parsed.disposition !== "clear") {
      throw new Error("Unsafe error cookie");
    }
    seen.add(parsed.name);
  }
  return values;
}

function validatedProviderRedirect(value: string | null, expectedOrigin: string): string {
  if (value === null || value.length > 8192) throw new Error("Missing redirect");
  const destination = new URL(value);
  if (
    destination.origin !== expectedOrigin ||
    destination.username !== "" ||
    destination.password !== "" ||
    destination.hash !== ""
  ) {
    throw new Error("Unsafe provider redirect");
  }
  return destination.href;
}

function validatedLocalRedirect(value: string | null): string {
  if (
    value === null ||
    value.length > 1024 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    throw new Error("Unsafe local redirect");
  }
  return value;
}

function validatedOrigin(
  value: string,
  { allowLocalHttp, label }: { allowLocalHttp: boolean; label: string },
): string {
  try {
    const url = new URL(value);
    const local = isLoopbackHost(url.hostname);
    if (
      value !== url.origin ||
      url.username !== "" ||
      url.password !== "" ||
      (url.protocol !== "https:" && !(allowLocalHttp && local && url.protocol === "http:"))
    ) {
      throw new Error();
    }
    return url.origin;
  } catch {
    throw new Error(`The ${label} origin is invalid.`);
  }
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

function validatedResponseCookies(
  response: Response,
  expected: Partial<Record<AuthCookieName, CookieDisposition>>,
): string[] {
  const values = response.headers.getSetCookie();
  if (values.length > AUTH_COOKIE_NAMES.length) throw new Error("Too many cookies");
  const seen = new Map<AuthCookieName, CookieDisposition>();
  for (const value of values) {
    const parsed = parseSetCookie(value);
    if (seen.has(parsed.name) || expected[parsed.name] !== parsed.disposition) {
      throw new Error("Unexpected cookie");
    }
    seen.set(parsed.name, parsed.disposition);
  }
  for (const [name, disposition] of Object.entries(expected)) {
    if (seen.get(name as AuthCookieName) !== disposition) {
      throw new Error("Missing cookie");
    }
  }
  return values;
}

function parseSetCookie(value: string): {
  name: AuthCookieName;
  disposition: CookieDisposition;
} {
  if (new TextEncoder().encode(value).byteLength > MAX_COOKIE_BYTES) {
    throw new Error("Cookie too large");
  }
  const segments = value.split(";").map((segment) => segment.trim());
  const separator = segments[0]?.indexOf("=") ?? -1;
  if (separator < 1) throw new Error("Invalid cookie");
  const name = segments[0]?.slice(0, separator) as AuthCookieName;
  const rawValue = segments[0]?.slice(separator + 1) ?? "";
  if (!AUTH_COOKIE_NAMES.includes(name)) throw new Error("Unknown cookie");
  const attributes = new Map<string, string | true>();
  for (const segment of segments.slice(1)) {
    const attributeSeparator = segment.indexOf("=");
    const key = (
      attributeSeparator === -1 ? segment : segment.slice(0, attributeSeparator)
    ).toLowerCase();
    const attributeValue =
      attributeSeparator === -1 ? true : segment.slice(attributeSeparator + 1);
    if (
      attributes.has(key) ||
      !["expires", "httponly", "max-age", "path", "samesite", "secure"].includes(key)
    ) {
      throw new Error("Invalid cookie attributes");
    }
    attributes.set(key, attributeValue);
  }
  if (
    attributes.get("httponly") !== true ||
    attributes.has("secure") !== COOKIE_CONFIGURATION.secure ||
    attributes.get("path") !== "/" ||
    String(attributes.get("samesite")).toLowerCase() !== "lax"
  ) {
    throw new Error("Unsafe cookie attributes");
  }
  const maxAge = attributes.get("max-age");
  if (typeof maxAge !== "string" || !/^\d+$/.test(maxAge)) {
    throw new Error("Invalid cookie lifetime");
  }
  if (rawValue === '""' || rawValue === "") {
    if (maxAge !== "0") throw new Error("Invalid cleared cookie");
    const expires = attributes.get("expires");
    if (expires !== undefined && (typeof expires !== "string" || Date.parse(expires) !== 0)) {
      throw new Error("Invalid cleared cookie expiry");
    }
    return { name, disposition: "clear" };
  }
  const maximum =
    name === OIDC_BINDING_COOKIE_NAME || name === INVITATION_IDENTITY_COOKIE_NAME
      ? 600
      : 86_400;
  if (!SESSION_TOKEN.test(rawValue) || Number(maxAge) < 1 || Number(maxAge) > maximum) {
    throw new Error("Invalid issued cookie");
  }
  if (attributes.has("expires")) throw new Error("Issued cookie has expiry ambiguity");
  return { name, disposition: "set" };
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim();
  if (contentType !== "application/json") throw new Error("Invalid content type");
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_JSON_BYTES)
  ) {
    throw new Error("Invalid response length");
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_JSON_BYTES) {
    throw new Error("Response too large");
  }
  const parsed: unknown = JSON.parse(body);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("Invalid JSON response");
  }
  return parsed as Record<string, unknown>;
}

function validateTenantSessionResponse(
  value: Record<string, unknown>,
  tenantId: string,
  currentTime: Date,
): void {
  if (
    !hasExactFields(value, [
      "authentication_level",
      "csrf_token",
      "expires_at",
      "role_key",
      "schema_version",
      "tenant_id",
      "user_id",
    ]) ||
    value.schema_version !== 1 ||
    value.tenant_id !== tenantId ||
    typeof value.user_id !== "string" ||
    !UUID.test(value.user_id) ||
    typeof value.role_key !== "string" ||
    !ROLES.has(value.role_key) ||
    (value.authentication_level !== "primary" && value.authentication_level !== "mfa") ||
    typeof value.csrf_token !== "string" ||
    !CSRF_TOKEN.test(value.csrf_token) ||
    typeof value.expires_at !== "string"
  ) {
    throw new Error("Invalid tenant session");
  }
  const expiry = new Date(value.expires_at);
  const remaining = expiry.valueOf() - currentTime.valueOf();
  if (!Number.isFinite(remaining) || remaining <= 0 || remaining > 86_400_000) {
    throw new Error("Invalid tenant session expiry");
  }
}

function validateSiteSelectionResponse(
  value: Record<string, unknown>,
  siteId: string,
  expectedSessionVersion: number,
): void {
  if (
    !hasExactFields(value, [
      "changed",
      "schema_version",
      "session_version",
      "site_id",
      "tenant_id",
      "user_id",
    ]) ||
    value.schema_version !== 1 ||
    value.site_id !== siteId ||
    typeof value.tenant_id !== "string" ||
    !UUID.test(value.tenant_id) ||
    typeof value.user_id !== "string" ||
    !UUID.test(value.user_id) ||
    typeof value.session_version !== "number" ||
    !Number.isSafeInteger(value.session_version) ||
    typeof value.changed !== "boolean" ||
    (value.changed && value.session_version !== expectedSessionVersion + 1) ||
    (!value.changed && value.session_version !== expectedSessionVersion)
  ) {
    throw new Error("Invalid site selection response");
  }
}

function validateSiteOnboardingResponse(
  value: Record<string, unknown>,
  expected: {
    name: string;
    primaryOrigin: string;
    timezone: string;
    reportingCurrency: string;
    expectedSessionVersion: number;
  },
): void {
  if (
    !hasExactFields(value, [
      "name",
      "ownership_status",
      "primary_origin",
      "replayed",
      "reporting_currency",
      "schema_version",
      "session_version",
      "site_id",
      "state",
      "tenant_id",
      "timezone",
      "user_id",
    ]) ||
    value.schema_version !== 1 ||
    value.name !== expected.name ||
    value.primary_origin !== expected.primaryOrigin ||
    value.timezone !== expected.timezone ||
    value.reporting_currency !== expected.reportingCurrency ||
    value.state !== "onboarding" ||
    value.ownership_status !== "unverified" ||
    typeof value.tenant_id !== "string" ||
    !UUID.test(value.tenant_id) ||
    typeof value.user_id !== "string" ||
    !UUID.test(value.user_id) ||
    typeof value.site_id !== "string" ||
    !UUID.test(value.site_id) ||
    typeof value.session_version !== "number" ||
    !Number.isSafeInteger(value.session_version) ||
    value.session_version !== expected.expectedSessionVersion + 1 ||
    typeof value.replayed !== "boolean"
  ) {
    throw new Error("Invalid site onboarding response");
  }
}

function validDisplayText(value: string, maximum: number): boolean {
  return (
    value.length >= 1 &&
    value.length <= maximum &&
    value === value.trim() &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}

function validTimezone(value: string): boolean {
  return (
    value.length >= 1 &&
    value.length <= 128 &&
    /^[A-Za-z0-9_+.-]+(?:\/[A-Za-z0-9_+.-]+)*$/.test(value)
  );
}

export function validSiteOrigin(value: string): boolean {
  if (value.length < 9 || value.length > 2048) return false;
  try {
    const origin = new URL(value);
    return (
      origin.protocol === "https:" &&
      origin.origin === value &&
      origin.pathname === "/" &&
      origin.search === "" &&
      origin.hash === "" &&
      origin.username === "" &&
      origin.password === "" &&
      origin.hostname.includes(".") &&
      !/^\d{1,3}(?:\.\d{1,3}){3}$/.test(origin.hostname) &&
      !origin.hostname.startsWith("[")
    );
  } catch {
    return false;
  }
}

function hasExactFields(
  value: Record<string, unknown>,
  fields: readonly string[],
): boolean {
  return Object.keys(value).sort().join(",") === fields.join(",");
}

function clearCookie(name: AuthCookieName): string {
  return `${name}=""; Path=/; Max-Age=0; HttpOnly${secureAttribute()}; SameSite=Lax`;
}

function secureAttribute(): string {
  return COOKIE_CONFIGURATION.secure ? "; Secure" : "";
}
