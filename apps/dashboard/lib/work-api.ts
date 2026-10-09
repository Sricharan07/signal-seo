import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { randomUUID } from "node:crypto";

import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME, validatedDashboardOrigin } from "./browser-auth";

const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_JSON_BYTES = 16 * 1024;
const STATUSES = new Set([
  "accepted",
  "workflow_admitted",
  "processing",
  "succeeded",
  "failed",
  "cancelled",
]);

export interface DashboardWorkResult {
  commandId: string;
  status:
    | "accepted"
    | "workflow_admitted"
    | "processing"
    | "succeeded"
    | "failed"
    | "cancelled";
  acceptedAt: string;
  workflowId: string | null;
  workflowState: string | null;
  projectedAt: string | null;
  result: {
    manifestId: string;
    manifestSha256: string;
    coverage: "complete" | "partial";
    discoveredCount: number;
    terminalCount: number;
    scopeVersion: number;
    crawlPolicyVersion: number;
  } | null;
  terminalReason: "crawl_activity_failed" | "crawl_cancelled" | null;
}

export type DashboardWork =
  | { state: "no_work" }
  | { state: "available"; work: DashboardWorkResult }
  | { state: "not_authenticated" | "unavailable" | "invalid" };

interface WorkOptions {
  tenantToken: string;
  siteId: string;
  dashboardOrigin?: string;
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
}

export async function loadDashboardWork({
  tenantToken,
  siteId,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: Omit<WorkOptions, "dashboardOrigin">): Promise<DashboardWork> {
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (prepared === null) return { state: "invalid" };
  let response: Response;
  try {
    response = await relayJson(
      new URL(`/v1/sites/${siteId}/commands/latest`, prepared.baseUrl),
      {
        cache: "no-store",
        redirect: "error",
        headers: { Accept: "application/json", Cookie: prepared.cookie },
        signal: AbortSignal.timeout(2500),
      }, fetcher, 16384);
  } catch {
    return { state: "unavailable" };
  }
  if (response.status === 404) return { state: "no_work" };
  if (response.status === 401 || response.status === 403) {
    return { state: "not_authenticated" };
  }
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };
  try {
    return { state: "available", work: validateWork(await readJson(response), siteId) };
  } catch {
    return { state: "invalid" };
  }
}

export async function startDashboardSnapshot({
  tenantToken,
  siteId,
  dashboardOrigin = validatedDashboardOrigin(),
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: WorkOptions): Promise<DashboardWork> {
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (prepared === null) return { state: "invalid" };
  let csrfResponse: Response;
  try {
    csrfResponse = await relayJson(new URL("/v1/session/tenant-csrf", prepared.baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json", Cookie: prepared.cookie },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 16384);
  } catch {
    return { state: "unavailable" };
  }
  if (csrfResponse.status === 401 || csrfResponse.status === 403) {
    return { state: "not_authenticated" };
  }
  if (csrfResponse.status === 503) return { state: "unavailable" };
  if (csrfResponse.status !== 200) return { state: "invalid" };

  let csrfToken: string;
  try {
    const csrf = await readJson(csrfResponse);
    if (
      !hasExactFields(csrf, ["csrf_token", "schema_version"]) ||
      csrf.schema_version !== 1 ||
      typeof csrf.csrf_token !== "string" ||
      !TOKEN.test(csrf.csrf_token)
    ) {
      throw new Error("Invalid CSRF response");
    }
    csrfToken = csrf.csrf_token;
  } catch {
    return { state: "invalid" };
  }

  let response: Response;
  try {
    response = await relayJson(
      new URL(`/v1/sites/${siteId}/commands/snapshot`, prepared.baseUrl),
      {
        cache: "no-store",
        redirect: "error",
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          Cookie: prepared.cookie,
          "Idempotency-Key": `dashboard-${randomUUID()}`,
          Origin: dashboardOrigin,
          "Sec-Fetch-Site": "same-origin",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ schema_version: 1 }),
        signal: AbortSignal.timeout(15_000),
      }, fetcher, 16384);
  } catch {
    return { state: "unavailable" };
  }
  if (response.status === 401 || response.status === 403) {
    return { state: "not_authenticated" };
  }
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 202) return { state: "invalid" };
  try {
    const accepted = await readJson(response);
    if (
      !hasExactFields(accepted, [
        "accepted_at",
        "command_id",
        "correlation_id",
        "reused",
        "schema_version",
        "site_id",
        "status",
        "status_url",
      ]) ||
      accepted.schema_version !== 1 ||
      accepted.site_id !== siteId ||
      typeof accepted.command_id !== "string" ||
      !UUID.test(accepted.command_id) ||
      accepted.status !== "accepted"
    ) {
      throw new Error("Invalid command acceptance");
    }
  } catch {
    return { state: "invalid" };
  }
  return loadDashboardWork({
    tenantToken,
    siteId,
    baseUrl: prepared.baseUrl.href,
    fetcher,
  });
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

function validateWork(value: unknown, siteId: string): DashboardWorkResult {
  const commonFields = [
    "accepted_at",
    "actor_user_id",
    "command_id",
    "correlation_id",
    "kind",
    "schema_version",
    "site_id",
    "status",
  ];
  if (!isRecord(value) || value.schema_version !== 1 || value.site_id !== siteId) {
    throw new Error("Invalid work response");
  }
  const allowedFields = new Set([
    ...commonFields,
    "first_run_id",
    "projected_at",
    "result_reference",
    "terminal_reason",
    "workflow_id",
    "workflow_state",
    "workflow_type",
  ]);
  if (
    Object.keys(value).some((field) => !allowedFields.has(field)) ||
    commonFields.some((field) => !(field in value)) ||
    typeof value.command_id !== "string" ||
    !UUID.test(value.command_id) ||
    value.kind !== "site.snapshot" ||
    typeof value.status !== "string" ||
    !STATUSES.has(value.status) ||
    typeof value.accepted_at !== "string"
  ) {
    throw new Error("Invalid work response");
  }
  const acceptedAt = exactTimestamp(value.accepted_at);
  const workflowId = optionalString(value.workflow_id);
  const workflowState = optionalString(value.workflow_state);
  const projectedAt =
    value.projected_at === undefined ? null : exactTimestamp(String(value.projected_at));
  const result = value.result_reference === undefined ? null : validateResult(value.result_reference);
  const terminalReason = value.terminal_reason ?? null;
  if (
    terminalReason !== null &&
    terminalReason !== "crawl_activity_failed" &&
    terminalReason !== "crawl_cancelled"
  ) {
    throw new Error("Invalid terminal reason");
  }
  if (
    value.status === "accepted" &&
    (workflowId !== null || workflowState !== null || projectedAt !== null || result !== null)
  ) {
    throw new Error("Invalid accepted projection");
  }
  if (
    value.status === "succeeded" &&
    (workflowState !== "succeeded" || workflowId === null || result === null)
  ) {
    throw new Error("Invalid terminal projection");
  }
  return {
    commandId: value.command_id,
    status: value.status as DashboardWorkResult["status"],
    acceptedAt,
    workflowId,
    workflowState,
    projectedAt,
    result,
    terminalReason,
  };
}

function validateResult(value: unknown): DashboardWorkResult["result"] {
  if (
    !hasExactFields(value, [
      "coverage",
      "crawl_policy_version",
      "discovered_count",
      "manifest_id",
      "manifest_sha256",
      "schema_version",
      "scope_version",
      "terminal_count",
    ]) ||
    value.schema_version !== 1 ||
    typeof value.manifest_id !== "string" ||
    !UUID.test(value.manifest_id) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    (value.coverage !== "complete" && value.coverage !== "partial") ||
    !boundedCount(value.discovered_count) ||
    !boundedCount(value.terminal_count) ||
    !boundedVersion(value.scope_version) ||
    !boundedVersion(value.crawl_policy_version) ||
    value.terminal_count > value.discovered_count
  ) {
    throw new Error("Invalid crawl result");
  }
  return {
    manifestId: value.manifest_id,
    manifestSha256: value.manifest_sha256,
    coverage: value.coverage,
    discoveredCount: value.discovered_count,
    terminalCount: value.terminal_count,
    scopeVersion: value.scope_version,
    crawlPolicyVersion: value.crawl_policy_version,
  };
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

function optionalString(value: unknown): string | null {
  if (value === undefined) return null;
  if (typeof value !== "string" || value.length < 1 || value.length > 200) {
    throw new Error("Invalid string field");
  }
  return value;
}

function boundedCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 1_000_000;
}

function boundedVersion(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 1 &&
    value <= 2_147_483_647
  );
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
