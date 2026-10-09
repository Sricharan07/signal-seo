import { boundedRelayText } from "./relay-json";
import { relayJson, RelayResponseRejected } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import {
  TENANT_COOKIE_NAME,
  validatedDashboardOrigin,
  validSiteOrigin,
} from "./browser-auth";

const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const CSRF_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_JSON_BYTES = 16 * 1024;
const CHALLENGE_PATH = "/.well-known/signal-site-verification.txt";

export interface DashboardOriginChallenge {
  siteId: string;
  challengeId: string;
  origin: string;
  proofUrl: string;
  proofContent: string;
  issuedAt: string;
  expiresAt: string;
  replayed: boolean;
}

export interface DashboardOriginVerification {
  siteId: string;
  challengeId: string;
  origin: string;
  verifiedAt: string;
  recheckAt: string;
  replayed: boolean;
}

export type OriginChallengeResult =
  | { state: "issued"; challenge: DashboardOriginChallenge }
  | OriginMutationFailure;

export type OriginVerificationResult =
  | { state: "verified"; verification: DashboardOriginVerification }
  | OriginMutationFailure;

export type OriginMutationFailure = {
  state: "not_ready" | "rejected" | "conflict" | "failed";
};

interface OriginMutationOptions {
  tenantToken: string;
  siteId: string;
  origin: string;
  idempotencyKey: string;
  dashboardOrigin: string;
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
}

interface VerifyOriginOptions extends OriginMutationOptions {
  challengeId: string;
}

export async function issueDashboardOriginChallenge({
  tenantToken,
  siteId,
  origin,
  idempotencyKey,
  dashboardOrigin,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: OriginMutationOptions): Promise<OriginChallengeResult> {
  const prepared = prepareMutation({
    tenantToken,
    siteId,
    origin,
    idempotencyKey,
    dashboardOrigin,
    configuredBaseUrl,
  });
  if (prepared === null) return { state: "rejected" };
  const response = await authenticatedPost({
    ...prepared,
    path: `/v1/sites/${siteId}/origin-challenges`,
    body: {
      schema_version: 1,
      idempotency_key: idempotencyKey,
      origin,
    },
    fetcher,
  });
  if (!(response instanceof Response)) return response;
  if (response.status !== 201) return responseFailure(response);
  try {
    const challenge = validateChallenge(await readJson(response), {
      siteId,
      origin,
    });
    assertNoCookies(response);
    return { state: "issued", challenge };
  } catch {
    return { state: "failed" };
  }
}

export async function verifyDashboardOrigin({
  tenantToken,
  siteId,
  challengeId,
  origin,
  idempotencyKey,
  dashboardOrigin,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: VerifyOriginOptions): Promise<OriginVerificationResult> {
  if (!UUID_V4.test(challengeId)) return { state: "rejected" };
  const prepared = prepareMutation({
    tenantToken,
    siteId,
    origin,
    idempotencyKey,
    dashboardOrigin,
    configuredBaseUrl,
  });
  if (prepared === null) return { state: "rejected" };
  const response = await authenticatedPost({
    ...prepared,
    path: `/v1/sites/${siteId}/verify-origin`,
    body: {
      schema_version: 1,
      idempotency_key: idempotencyKey,
      challenge_id: challengeId,
      origin,
    },
    fetcher,
  });
  if (!(response instanceof Response)) return response;
  if (response.status !== 200) return responseFailure(response);
  try {
    const verification = validateVerification(await readJson(response), {
      siteId,
      challengeId,
      origin,
    });
    assertNoCookies(response);
    return { state: "verified", verification };
  } catch {
    return { state: "failed" };
  }
}

function prepareMutation({
  tenantToken,
  siteId,
  origin,
  idempotencyKey,
  dashboardOrigin,
  configuredBaseUrl,
}: Omit<OriginMutationOptions, "baseUrl" | "fetcher"> & {
  configuredBaseUrl?: string;
}): { baseUrl: URL; cookie: string; dashboardOrigin: string } | null {
  if (
    !SESSION_TOKEN.test(tenantToken) ||
    !UUID_V4.test(siteId) ||
    !UUID_V4.test(idempotencyKey) ||
    !validSiteOrigin(origin)
  ) {
    return null;
  }
  try {
    const baseUrl = validatedSignalApiBaseUrl(
      configuredBaseUrl ??
        process.env.SIGNAL_API_BASE_URL ??
        "http://127.0.0.1:8000",
    );
    const validatedOrigin = validatedDashboardOrigin(
      dashboardOrigin,
      dashboardOrigin.startsWith("http://") ? "development" : "production",
    );
    if (validatedOrigin !== dashboardOrigin) return null;
    return {
      baseUrl,
      cookie: `${TENANT_COOKIE_NAME}=${tenantToken}`,
      dashboardOrigin,
    };
  } catch {
    return null;
  }
}

async function authenticatedPost({
  baseUrl,
  cookie,
  dashboardOrigin,
  path,
  body,
  fetcher,
}: {
  baseUrl: URL;
  cookie: string;
  dashboardOrigin: string;
  path: string;
  body: Record<string, unknown>;
  fetcher: typeof globalThis.fetch;
}): Promise<Response | OriginMutationFailure> {
  let csrfResponse: Response;
  try {
    csrfResponse = await relayJson(new URL("/v1/session/tenant-csrf", baseUrl), {
      ...boundedRequest(2500),
      headers: { Accept: "application/json", Cookie: cookie },
    }, fetcher, 131072);
  } catch (error) {
    return { state: error instanceof RelayResponseRejected ? "failed" : "not_ready" };
  }
  if (csrfResponse.status !== 200) return responseFailure(csrfResponse);

  let csrfToken: string;
  try {
    const document = await readJson(csrfResponse);
    if (
      !hasExactFields(document, ["csrf_token", "schema_version"]) ||
      document.schema_version !== 1 ||
      typeof document.csrf_token !== "string" ||
      !CSRF_TOKEN.test(document.csrf_token)
    ) {
      throw new Error("Invalid tenant CSRF response");
    }
    assertNoCookies(csrfResponse);
    csrfToken = document.csrf_token;
  } catch {
    return { state: "failed" };
  }

  try {
    return await relayJson(new URL(path, baseUrl), {
      ...boundedRequest(15_000),
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Cookie: cookie,
        Origin: dashboardOrigin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(body),
    }, fetcher, 131072);
  } catch (error) {
    return { state: error instanceof RelayResponseRejected ? "failed" : "not_ready" };
  }
}

function validateChallenge(
  value: unknown,
  expected: { siteId: string; origin: string },
): DashboardOriginChallenge {
  if (
    !hasExactFields(value, [
      "challenge_id",
      "expires_at",
      "issued_at",
      "origin",
      "proof_content",
      "proof_method",
      "proof_url",
      "replayed",
      "schema_version",
      "site_id",
    ]) ||
    value.schema_version !== 1 ||
    value.site_id !== expected.siteId ||
    typeof value.challenge_id !== "string" ||
    !UUID_V4.test(value.challenge_id) ||
    value.origin !== expected.origin ||
    value.proof_method !== "http_well_known" ||
    value.proof_url !== `${expected.origin}${CHALLENGE_PATH}` ||
    value.proof_content !==
      `signal-site-verification=${value.challenge_id}\n` ||
    typeof value.issued_at !== "string" ||
    typeof value.expires_at !== "string" ||
    typeof value.replayed !== "boolean"
  ) {
    throw new Error("Invalid origin challenge response");
  }
  const issuedAt = exactTimestamp(value.issued_at);
  const expiresAt = exactTimestamp(value.expires_at);
  if (expiresAt.valueOf() - issuedAt.valueOf() !== 30 * 60 * 1000) {
    throw new Error("Invalid origin challenge lifetime");
  }
  return {
    siteId: value.site_id,
    challengeId: value.challenge_id,
    origin: value.origin,
    proofUrl: value.proof_url,
    proofContent: value.proof_content,
    issuedAt: issuedAt.toISOString(),
    expiresAt: expiresAt.toISOString(),
    replayed: value.replayed,
  };
}

function validateVerification(
  value: unknown,
  expected: { siteId: string; challengeId: string; origin: string },
): DashboardOriginVerification {
  if (
    !hasExactFields(value, [
      "challenge_id",
      "origin",
      "ownership_status",
      "permitted_origins",
      "proof_method",
      "recheck_at",
      "replayed",
      "schema_version",
      "site_id",
      "verified_at",
    ]) ||
    value.schema_version !== 1 ||
    value.site_id !== expected.siteId ||
    value.challenge_id !== expected.challengeId ||
    value.origin !== expected.origin ||
    value.ownership_status !== "verified" ||
    value.proof_method !== "http_well_known" ||
    !Array.isArray(value.permitted_origins) ||
    value.permitted_origins.length !== 1 ||
    value.permitted_origins[0] !== expected.origin ||
    typeof value.verified_at !== "string" ||
    typeof value.recheck_at !== "string" ||
    typeof value.replayed !== "boolean"
  ) {
    throw new Error("Invalid origin verification response");
  }
  const verifiedAt = exactTimestamp(value.verified_at);
  const recheckAt = exactTimestamp(value.recheck_at);
  if (recheckAt.valueOf() - verifiedAt.valueOf() !== 30 * 24 * 60 * 60 * 1000) {
    throw new Error("Invalid origin verification lifetime");
  }
  return {
    siteId: value.site_id,
    challengeId: value.challenge_id,
    origin: value.origin,
    verifiedAt: verifiedAt.toISOString(),
    recheckAt: recheckAt.toISOString(),
    replayed: value.replayed,
  };
}

function responseFailure(response: Response): OriginMutationFailure {
  try {
    assertNoCookies(response);
  } catch {
    return { state: "failed" };
  }
  if (response.status === 503) return { state: "not_ready" };
  if (response.status === 404 || response.status === 409) {
    return { state: "conflict" };
  }
  if (response.status === 401 || response.status === 403 || response.status === 422) {
    return { state: "rejected" };
  }
  return { state: "failed" };
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.get("content-type")?.split(";", 1)[0]?.trim() !== "application/json") {
    throw new Error("Response content type invalid");
  }
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_JSON_BYTES)
  ) {
    throw new Error("Response length invalid");
  }
  const body = await boundedRelayText(response, MAX_JSON_BYTES);
  if (new TextEncoder().encode(body).byteLength > MAX_JSON_BYTES) {
    throw new Error("Response too large");
  }
  const value: unknown = JSON.parse(body);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Response body invalid");
  }
  return value as Record<string, unknown>;
}

function exactTimestamp(value: string): Date {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)) {
    throw new Error("Timestamp invalid");
  }
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.valueOf())) throw new Error("Timestamp invalid");
  return parsed;
}

function hasExactFields(
  value: unknown,
  fields: readonly string[],
): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === fields.join(",")
  );
}

function assertNoCookies(response: Response): void {
  if (response.headers.getSetCookie().length !== 0) {
    throw new Error("Origin verification attempted to replace browser authority");
  }
}

function boundedRequest(timeout: number): RequestInit {
  return {
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(timeout),
  };
}
