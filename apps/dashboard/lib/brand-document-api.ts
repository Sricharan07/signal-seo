import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const CSRF = /^[A-Za-z0-9_-]{43}$/;

export function validBrandDocumentId(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}

export async function relayBrandDocuments(
  tenantToken: string,
  siteId: string,
  dashboardOrigin: string,
  method: "GET" | "POST" | "DELETE",
  body?: unknown,
  documentId?: string,
  fetcher: typeof globalThis.fetch = globalThis.fetch,
): Promise<Response> {
  if (!TOKEN.test(tenantToken) || !validBrandDocumentId(siteId) ||
      (documentId !== undefined && !validBrandDocumentId(documentId)) ||
      (method === "DELETE" && documentId === undefined)) {
    return Response.json({ state: "rejected" }, { status: 403 });
  }
  let base: URL;
  try {
    base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
  } catch {
    return Response.json({ state: "not_ready" }, { status: 503 });
  }
  const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
  const path = `/v1/sites/${siteId}/brand-documents${documentId ? `/${documentId}` : ""}`;
  let csrf: string | null = null;
  if (method !== "GET") {
    try {
      const response = await relayJson(new URL("/v1/session/tenant-csrf", base), {
        cache: "no-store", redirect: "error", signal: AbortSignal.timeout(2500),
        headers: { Accept: "application/json", Cookie: cookie },
      }, fetcher, 800000);
      if (response.status !== 200 || response.headers.getSetCookie().length !== 0) throw new Error();
      const value: unknown = await response.json();
      if (!exactKeys(value, ["csrf_token", "schema_version"]) ||
          value.schema_version !== 1 || typeof value.csrf_token !== "string" ||
          !CSRF.test(value.csrf_token)) throw new Error();
      csrf = value.csrf_token;
    } catch {
      return Response.json({ state: "not_ready" }, { status: 503 });
    }
  }
  try {
    const response = await relayJson(new URL(path, base), {
      method, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
      headers: {
        Accept: "application/json", Cookie: cookie,
        ...(method === "GET" ? {} : {
          Origin: dashboardOrigin, "Sec-Fetch-Site": "same-origin",
          "X-CSRF-Token": csrf ?? "", "Content-Type": "application/json",
        }),
      },
      ...(method === "POST" ? { body: JSON.stringify(body) } : {}),
    }, fetcher, 800000);
    if (response.headers.getSetCookie().length !== 0) throw new Error();
    const raw = await boundedRelayText(response, 800_000);
    if (new TextEncoder().encode(raw).length > (method === "GET" && documentId ? 800_000 : 65_536)) {
      throw new Error();
    }
    const value: unknown = JSON.parse(raw);
    if (response.ok && !validDocumentResponse(value, method, documentId !== undefined)) throw new Error();
    if (!response.ok && !validError(value)) throw new Error();
    return Response.json(value, {
      status: response.status,
      headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
    });
  } catch {
    return Response.json({ state: "not_ready" }, { status: 503 });
  }
}

function exactKeys(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}

function validDocument(value: unknown): boolean {
  if (!exactKeys(value, ["schema_version", "document_id", "display_name", "media_type",
    "created_at", "supersedes_id", "injection_signal", "secret_signal", "deleted",
    "retained_for_evidence"])) return false;
  return value.schema_version === 1 && validBrandDocumentId(value.document_id) &&
    typeof value.display_name === "string" && value.display_name.length >= 1 &&
    value.display_name.length <= 120 && !/[\x00-\x1f]/.test(value.display_name) &&
    typeof value.media_type === "string" && [
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "text/markdown", "text/plain",
    ].includes(value.media_type) &&
    typeof value.created_at === "string" && !Number.isNaN(Date.parse(value.created_at)) &&
    (value.supersedes_id === null || validBrandDocumentId(value.supersedes_id)) &&
    typeof value.injection_signal === "boolean" && typeof value.secret_signal === "boolean" &&
    typeof value.deleted === "boolean" && typeof value.retained_for_evidence === "boolean";
}

function validError(value: unknown): boolean {
  if (!exactKeys(value, ["error"]) || !exactKeys(value.error, [
    "code", "message", "retryable", "correlation_id",
  ])) return false;
  const error = value.error;
  return typeof error.code === "string" && /^[A-Z_]{3,64}$/.test(error.code) &&
    typeof error.message === "string" && error.message.length <= 200 &&
    !/[\x00-\x1f]/.test(error.message) && typeof error.retryable === "boolean" &&
    typeof error.correlation_id === "string" && error.correlation_id.length <= 64;
}

function validDocumentResponse(
  value: unknown, method: "GET" | "POST" | "DELETE", hasDocumentId: boolean,
): boolean {
  if (method === "POST") return validDocument(value);
  if (method === "DELETE") return exactKeys(value, ["schema_version", "outcome", "retained_for_evidence"])
    && value.schema_version === 1 && value.outcome === "deleted_retained" && value.retained_for_evidence === true;
  if (hasDocumentId) return exactKeys(value, ["schema_version", "document_id", "trust_label", "text", "text_sha256", "injection_signal"])
    && value.schema_version === 1 && validBrandDocumentId(value.document_id) &&
    value.trust_label === "owner_upload_untrusted_data" && typeof value.text === "string" &&
    value.text.length <= 524_288 && typeof value.text_sha256 === "string" &&
    /^[0-9a-f]{64}$/.test(value.text_sha256) && typeof value.injection_signal === "boolean";
  return exactKeys(value, ["schema_version", "documents"]) && value.schema_version === 1 &&
    Array.isArray(value.documents) && value.documents.length <= 100 && value.documents.every(validDocument);
}
