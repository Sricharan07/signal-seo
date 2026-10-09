import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME, validatedDashboardOrigin } from "./browser-auth";

const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_JSON_BYTES = 32 * 1024;

export interface DashboardFinding {
  findingId: string;
  evidenceId: string;
  commandId: string;
  manifestId: string;
  findingKey: "metadata.meta_description.missing";
  title: "Missing meta description";
  summary: string;
  resourceLocator: string;
  severity: "medium";
  status: "open";
  confidenceClass: "deterministic";
  sourceKind: "synthetic_fixture" | "verified_origin";
  sourceIdentifier: string;
  contentSha256: string;
  evidenceObservedAt: string;
  firstSeenAt: string;
  lastSeenAt: string;
  reused: boolean;
}

export interface DashboardPageObservation {
  intentId: string;
  evidenceId: string;
  findingId: string | null;
  commandId: string;
  manifestId: string;
  origin: string;
  finalUrl: string;
  httpStatus: number;
  mediaType: "text/html" | "application/xhtml+xml";
  title: string | null;
  heading: string | null;
  metaDescription: string | null;
  bodySha256: string;
  observedAt: string;
  reused: boolean;
}

export type DashboardFindings =
  | { state: "available"; findings: readonly DashboardFinding[] }
  | { state: "not_authenticated" | "unavailable" | "invalid" };

export type DashboardPageObservationState =
  | { state: "available"; observation: DashboardPageObservation | null }
  | { state: "not_authenticated" | "unavailable" | "invalid" | "conflict" };

type CsrfFailure = { state: "not_authenticated" | "unavailable" | "invalid" };

interface FindingOptions {
  tenantToken: string;
  siteId: string;
  dashboardOrigin?: string;
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
}

interface HomepageAnalysisOptions extends FindingOptions {
  idempotencyKey: string;
}

export async function loadLatestPageObservation({
  tenantToken,
  siteId,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: Omit<FindingOptions, "dashboardOrigin">): Promise<DashboardPageObservationState> {
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (prepared === null) return { state: "invalid" };
  let response: Response;
  try {
    response = await relayJson(
      new URL(`/v1/sites/${siteId}/observations/homepage/latest`, prepared.baseUrl),
      {
        cache: "no-store",
        redirect: "error",
        headers: { Accept: "application/json", Cookie: prepared.cookie },
        signal: AbortSignal.timeout(2500),
      }, fetcher, 32768);
  } catch {
    return { state: "unavailable" };
  }
  if (response.status === 401 || response.status === 403) {
    return { state: "not_authenticated" };
  }
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };
  try {
    const payload = await readJson(response);
    if (
      !hasExactFields(payload, ["correlation_id", "observation", "schema_version", "site_id"]) ||
      payload.schema_version !== 1 ||
      payload.site_id !== siteId
    ) {
      throw new Error("Invalid page observation response");
    }
    return {
      state: "available",
      observation: payload.observation === null ? null : validatePageObservation(payload.observation),
    };
  } catch {
    return { state: "invalid" };
  }
}

export async function analyzeDashboardVerifiedHomepage({
  tenantToken,
  siteId,
  idempotencyKey,
  dashboardOrigin = validatedDashboardOrigin(),
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: HomepageAnalysisOptions): Promise<DashboardPageObservationState> {
  if (!UUID.test(idempotencyKey)) return { state: "invalid" };
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (prepared === null) return { state: "invalid" };
  const csrfToken = await loadCsrf(prepared, fetcher);
  if (typeof csrfToken !== "string") return csrfToken;

  let response: Response;
  try {
    response = await relayJson(
      new URL(`/v1/sites/${siteId}/analysis/verified-homepage`, prepared.baseUrl),
      {
        cache: "no-store",
        redirect: "error",
        method: "POST",
        headers: mutationHeaders(prepared.cookie, dashboardOrigin, csrfToken),
        body: JSON.stringify({ idempotency_key: idempotencyKey, schema_version: 1 }),
        signal: AbortSignal.timeout(25_000),
      }, fetcher, 32768);
  } catch {
    return { state: "unavailable" };
  }
  if (response.status === 401 || response.status === 403) {
    return { state: "not_authenticated" };
  }
  if (response.status === 409) return { state: "conflict" };
  if (response.status === 502 || response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };
  try {
    const payload = await readJson(response);
    if (
      !hasExactFields(payload, ["correlation_id", "finding", "observation", "schema_version", "site_id"]) ||
      payload.schema_version !== 1 ||
      payload.site_id !== siteId
    ) {
      throw new Error("Invalid page analysis response");
    }
    const observation = validatePageObservation(payload.observation);
    if (payload.finding !== null) {
      const finding = validateFinding(payload.finding);
      if (finding.evidenceId !== observation.evidenceId || finding.findingId !== observation.findingId) {
        throw new Error("Page analysis evidence mismatch");
      }
    } else if (observation.findingId !== null) {
      throw new Error("Page analysis finding mismatch");
    }
    return { state: "available", observation };
  } catch {
    return { state: "invalid" };
  }
}

export async function loadDashboardFindings({
  tenantToken,
  siteId,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: Omit<FindingOptions, "dashboardOrigin">): Promise<DashboardFindings> {
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (prepared === null) return { state: "invalid" };
  let response: Response;
  try {
    response = await relayJson(new URL(`/v1/sites/${siteId}/findings`, prepared.baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json", Cookie: prepared.cookie },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 32768);
  } catch {
    return { state: "unavailable" };
  }
  if (response.status === 401 || response.status === 403) {
    return { state: "not_authenticated" };
  }
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };
  try {
    const payload = await readJson(response);
    if (
      !hasExactFields(payload, ["correlation_id", "findings", "schema_version", "site_id"]) ||
      payload.schema_version !== 1 ||
      payload.site_id !== siteId ||
      !Array.isArray(payload.findings) ||
      payload.findings.length > 50
    ) {
      throw new Error("Invalid findings response");
    }
    return { state: "available", findings: payload.findings.map(validateFinding) };
  } catch {
    return { state: "invalid" };
  }
}

function prepare(
  tenantToken: string,
  siteId: string,
  configuredBaseUrl?: string,
): { baseUrl: URL; cookie: string } | null {
  if (!SESSION_TOKEN.test(tenantToken) || !UUID.test(siteId)) return null;
  try {
    const baseUrl = validatedSignalApiBaseUrl(
      configuredBaseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000",
    );
    return { baseUrl, cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` };
  } catch {
    return null;
  }
}

function validateFinding(value: unknown): DashboardFinding {
  if (
    !hasExactFields(value, [
      "command_id",
      "confidence_class",
      "content_sha256",
      "evidence_id",
      "evidence_observed_at",
      "finding_id",
      "finding_key",
      "first_seen_at",
      "last_seen_at",
      "manifest_id",
      "resource_locator",
      "reused",
      "schema_version",
      "severity",
      "source_identifier",
      "source_kind",
      "status",
      "summary",
      "title",
    ]) ||
    value.schema_version !== 1 ||
    ![value.finding_id, value.evidence_id, value.command_id, value.manifest_id].every(
      (candidate) => typeof candidate === "string" && UUID.test(candidate),
    ) ||
    value.finding_key !== "metadata.meta_description.missing" ||
    value.title !== "Missing meta description" ||
    typeof value.summary !== "string" ||
    value.summary.length < 1 ||
    value.summary.length > 500 ||
    typeof value.resource_locator !== "string" ||
    value.severity !== "medium" ||
    value.status !== "open" ||
    value.confidence_class !== "deterministic" ||
    (value.source_kind !== "synthetic_fixture" && value.source_kind !== "verified_origin") ||
    typeof value.source_identifier !== "string" ||
    typeof value.content_sha256 !== "string" ||
    !SHA256.test(value.content_sha256) ||
    typeof value.reused !== "boolean"
  ) {
    throw new Error("Invalid finding");
  }
  const fixtureSource =
    value.source_kind === "synthetic_fixture" &&
    value.resource_locator === "/fixture/missing-meta-description" &&
    value.source_identifier === "fixture:local-pilot/missing-meta-description/v1";
  const verifiedSource =
    value.source_kind === "verified_origin" &&
    value.resource_locator === value.source_identifier &&
    validPageUrl(value.resource_locator);
  if (!fixtureSource && !verifiedSource) throw new Error("Invalid finding source");
  const evidenceObservedAt = exactTimestamp(String(value.evidence_observed_at));
  const firstSeenAt = exactTimestamp(String(value.first_seen_at));
  const lastSeenAt = exactTimestamp(String(value.last_seen_at));
  if (lastSeenAt < firstSeenAt) throw new Error("Invalid finding lifecycle");
  return {
    findingId: String(value.finding_id),
    evidenceId: String(value.evidence_id),
    commandId: String(value.command_id),
    manifestId: String(value.manifest_id),
    findingKey: value.finding_key,
    title: value.title,
    summary: value.summary,
    resourceLocator: value.resource_locator,
    severity: value.severity,
    status: value.status,
    confidenceClass: value.confidence_class,
    sourceKind: value.source_kind,
    sourceIdentifier: value.source_identifier,
    contentSha256: value.content_sha256,
    evidenceObservedAt,
    firstSeenAt,
    lastSeenAt,
    reused: value.reused,
  };
}

function validatePageObservation(value: unknown): DashboardPageObservation {
  if (
    !hasExactFields(value, [
      "body_sha256",
      "command_id",
      "evidence_id",
      "final_url",
      "finding_id",
      "heading",
      "http_status",
      "intent_id",
      "manifest_id",
      "media_type",
      "meta_description",
      "observed_at",
      "origin",
      "reused",
      "schema_version",
      "title",
    ]) ||
    value.schema_version !== 1 ||
    ![value.intent_id, value.evidence_id, value.command_id, value.manifest_id].every(
      (candidate) => typeof candidate === "string" && UUID.test(candidate),
    ) ||
    (value.finding_id !== null && (typeof value.finding_id !== "string" || !UUID.test(value.finding_id))) ||
    typeof value.origin !== "string" ||
    !validOrigin(value.origin) ||
    typeof value.final_url !== "string" ||
    !validPageUrl(value.final_url) ||
    !value.final_url.startsWith(`${value.origin}/`) ||
    typeof value.http_status !== "number" ||
    !Number.isInteger(value.http_status) ||
    value.http_status < 200 ||
    value.http_status > 299 ||
    (value.media_type !== "text/html" && value.media_type !== "application/xhtml+xml") ||
    !validNullableText(value.title, 300) ||
    !validNullableText(value.heading, 500) ||
    !validNullableText(value.meta_description, 500) ||
    typeof value.body_sha256 !== "string" ||
    !SHA256.test(value.body_sha256) ||
    typeof value.reused !== "boolean"
  ) {
    throw new Error("Invalid page observation");
  }
  return {
    intentId: String(value.intent_id),
    evidenceId: String(value.evidence_id),
    findingId: value.finding_id === null ? null : String(value.finding_id),
    commandId: String(value.command_id),
    manifestId: String(value.manifest_id),
    origin: value.origin,
    finalUrl: value.final_url,
    httpStatus: value.http_status,
    mediaType: value.media_type,
    title: value.title,
    heading: value.heading,
    metaDescription: value.meta_description,
    bodySha256: value.body_sha256,
    observedAt: exactTimestamp(String(value.observed_at)),
    reused: value.reused,
  };
}

async function loadCsrf(
  prepared: { baseUrl: URL; cookie: string },
  fetcher: typeof globalThis.fetch,
): Promise<string | CsrfFailure> {
  let response: Response;
  try {
    response = await relayJson(new URL("/v1/session/tenant-csrf", prepared.baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json", Cookie: prepared.cookie },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 32768);
  } catch {
    return { state: "unavailable" };
  }
  if (response.status === 401 || response.status === 403) return { state: "not_authenticated" };
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };
  try {
    const csrf = await readJson(response);
    if (
      !hasExactFields(csrf, ["csrf_token", "schema_version"]) ||
      csrf.schema_version !== 1 ||
      typeof csrf.csrf_token !== "string" ||
      !TOKEN.test(csrf.csrf_token)
    ) {
      throw new Error("Invalid CSRF response");
    }
    return csrf.csrf_token;
  } catch {
    return { state: "invalid" };
  }
}

function mutationHeaders(cookie: string, origin: string, csrfToken: string) {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    Cookie: cookie,
    Origin: origin,
    "Sec-Fetch-Site": "same-origin",
    "X-CSRF-Token": csrfToken,
  };
}

function validNullableText(value: unknown, maximum: number): value is string | null {
  return value === null || (typeof value === "string" && value.length >= 1 && value.length <= maximum);
}

function validOrigin(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.origin === value && url.pathname === "/" && !url.search && !url.hash;
  } catch {
    return false;
  }
}

function validPageUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.hash && url.href === value;
  } catch {
    return false;
  }
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.get("content-type")?.split(";", 1)[0]?.trim() !== "application/json") {
    throw new Error("Response content type invalid");
  }
  const body = await boundedRelayText(response, MAX_JSON_BYTES);
  if (new TextEncoder().encode(body).byteLength > MAX_JSON_BYTES) {
    throw new Error("Response too large");
  }
  const value: unknown = JSON.parse(body);
  if (!isRecord(value)) throw new Error("Response body invalid");
  return value;
}

function exactTimestamp(value: string): string {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.valueOf())) throw new Error("Timestamp invalid");
  return parsed.toISOString();
}

function hasExactFields(
  value: unknown,
  fields: readonly string[],
): value is Record<string, unknown> {
  return isRecord(value) && Object.keys(value).sort().join(",") === fields.join(",");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
