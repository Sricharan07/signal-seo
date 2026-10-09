import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { validBrandDocumentId as uuid } from "./brand-document-api";

export const brainCategories = ["product", "pricing", "audience", "positioning", "proof_point", "competitor", "claim", "legal", "medical", "financial", "product_claim"] as const;
export const brainStatuses = ["proposed", "approved", "superseded", "removed"] as const;
export interface BrainFact {
  fact_id: string; category: string; statement: string; status: string;
  source_kind: string; page_evidence_id: string | null; document_id: string | null;
  extracted_range: { start: number; end: number } | null; owner_membership_id: string | null;
  sensitive: boolean; supersedes_id: string | null; created_at: string;
  decision_id: string | null; extraction_id: string | null; provenance_url: string;
  source_review_required?: boolean;
}
export interface BrainVoice {
  profile_id: string; profile: { tone: string; audience: string; guidelines: string };
  supersedes_id: string | null; created_at: string;
}
export interface BrainData {
  facts: BrainFact[]; voice: BrainVoice | null;
  extraction: { state: "available" | "unavailable"; reason: string | null; extractions: unknown[] };
}
function exact(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}
const text = (value: unknown, max = 4000) => typeof value === "string" && value.length <= max && !value.includes("\0");
const nullableId = (value: unknown) => value === null || uuid(value);
const date = (value: unknown) => typeof value === "string" && !Number.isNaN(Date.parse(value));
function validFact(value: unknown, siteId: string): boolean {
  const keys = ["fact_id", "category", "statement", "status", "source_kind", "page_evidence_id", "document_id", "extracted_range", "owner_membership_id", "sensitive", "supersedes_id", "created_at", "decision_id", "extraction_id", "provenance_url"];
  if (typeof value === "object" && value !== null && "source_review_required" in value) {
    if (typeof value.source_review_required !== "boolean") return false;
    keys.push("source_review_required");
  }
  if (!exact(value, keys)) return false;
  return uuid(value.fact_id) && brainCategories.some((item) => item === value.category) && text(value.statement) && value.statement !== "" && brainStatuses.some((item) => item === value.status) && ["page_evidence", "brand_document", "owner_statement"].includes(String(value.source_kind)) && [value.page_evidence_id, value.document_id, value.owner_membership_id, value.supersedes_id, value.decision_id, value.extraction_id].every(nullableId) && typeof value.sensitive === "boolean" && date(value.created_at) && (value.extracted_range === null || validRange(value.extracted_range)) && value.provenance_url === `/v1/sites/${siteId}/business-brain/facts/${value.fact_id}/provenance`;
}
function validRange(value: unknown): boolean {
  return exact(value, ["start", "end"]) && Number.isSafeInteger(value.start) && Number.isSafeInteger(value.end) && Number(value.start) >= 0 && Number(value.end) > Number(value.start);
}
export function validBrainResponse(value: unknown, resource: string, siteId: string): boolean {
  if (resource === "provenance") return validFact(value, siteId);
  if (!exact(value, ["schema_version", resource === "facts" ? "facts" : resource === "voice" ? "voice" : "state", ...(resource === "extraction" ? ["reason", "extractions"] : [])]) || value.schema_version !== 1) return false;
  if (resource === "facts") return Array.isArray(value.facts) && value.facts.every((item) => validFact(item, siteId));
  if (resource === "voice") {
    if (value.voice === null) return true;
    const voice = value.voice;
    return exact(voice, ["profile_id", "profile", "supersedes_id", "created_at"]) && uuid(voice.profile_id) && nullableId(voice.supersedes_id) && date(voice.created_at) && exact(voice.profile, ["tone", "audience", "guidelines"]) && Object.values(voice.profile).every((item) => text(item));
  }
  return ["available", "unavailable"].includes(String(value.state)) && (value.reason === null || value.reason === "MODEL_UNCONFIGURED") && Array.isArray(value.extractions) && value.extractions.length <= 100 && value.extractions.every((item) => exact(item, ["extraction_id", "source_kind", "source_id", "decision_id", "state", "page_type", "fallback", "provider", "reason"]) && uuid(item.extraction_id) && uuid(item.source_id) && uuid(item.decision_id) && ["page_evidence", "brand_document"].includes(String(item.source_kind)) && ["completed", "failed", "outcome_unknown"].includes(String(item.state)) && (item.page_type === null || ["product", "pricing", "blog", "docs", "legal", "other"].includes(String(item.page_type))) && (item.fallback === null || typeof item.fallback === "boolean") && (item.provider === null || ["typesafe", "openai_fallback", "deterministic_fallback"].includes(String(item.provider))) && (item.reason === null || text(item.reason, 128)));
}
export function brainCommand(value: unknown): { siteId: string; path: string; method: "POST" | "PUT"; body: Record<string, unknown> } | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const input = value as Record<string, unknown>;
  if (!uuid(input.site_id) || input.schema_version !== 1) return null;
  const base = ["schema_version", "site_id", "action"];
  let path = "facts", method: "POST" | "PUT" = "POST", keys: string[] = [];
  if (input.action === "propose") {
    keys = ["category", "statement"];
    if (!brainCategories.some((item) => item === input.category) || !text(input.statement) || input.statement === "") return null;
  } else if (["approve", "correct", "remove"].includes(String(input.action))) {
    keys = ["fact_id", ...(input.action === "correct" ? ["statement"] : [])];
    if (!uuid(input.fact_id) || (input.action === "correct" && (!text(input.statement) || input.statement === ""))) return null;
    path = `facts/${input.fact_id}/${input.action}`;
  } else if (input.action === "voice") {
    keys = ["profile", "supersedes_id"]; path = "voice"; method = "PUT";
    if (!nullableId(input.supersedes_id) || !exact(input.profile, ["tone", "audience", "guidelines"]) || !Object.values(input.profile).every((item) => text(item))) return null;
  } else if (input.action === "extract") {
    keys = ["source_kind", "source_id", "extracted_range"]; path = "extraction";
    if (!uuid(input.source_id) || !["page_evidence", "brand_document"].includes(String(input.source_kind)) || (input.source_kind === "page_evidence" ? input.extracted_range !== null : !validRange(input.extracted_range))) return null;
  } else return null;
  if (!exact(input, [...base, ...keys])) return null;
  const body = Object.fromEntries(Object.entries(input).filter(([key]) => !["site_id", "action", "fact_id"].includes(key)));
  return { siteId: input.site_id, path, method, body };
}
export async function relayBrain(token: string, siteId: string, origin: string, resource: string, command?: ReturnType<typeof brainCommand>): Promise<Response> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token) || !uuid(siteId)) return Response.json({ state: "rejected" }, { status: 403 });
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const cookie = `${TENANT_COOKIE_NAME}=${token}`;
    let csrf = "";
    if (command) {
      const response = await relayJson(new URL("/v1/session/tenant-csrf", base), { cache: "no-store", redirect: "error", signal: AbortSignal.timeout(2500), headers: { Cookie: cookie, Accept: "application/json" } }, globalThis.fetch, 3000000);
      const value: unknown = await response.json();
      if (response.status !== 200 || response.headers.getSetCookie().length || !exact(value, ["schema_version", "csrf_token"]) || value.schema_version !== 1 || typeof value.csrf_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.csrf_token)) throw new Error();
      csrf = value.csrf_token;
    }
    const response = await relayJson(new URL(`/v1/sites/${siteId}/business-brain/${command?.path ?? resource}`, base), { method: command?.method ?? "GET", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(command?.path === "extraction" ? 90000 : 15000), headers: { Cookie: cookie, Accept: "application/json", ...(command ? { Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf, "Content-Type": "application/json" } : {}) }, ...(command ? { body: JSON.stringify(command.body) } : {}) }, globalThis.fetch, 3000000);
    if (response.headers.getSetCookie().length) throw new Error();
    const raw = await boundedRelayText(response, 3_000_000);
    if (new TextEncoder().encode(raw).length > 3_000_000) throw new Error();
    const value: unknown = JSON.parse(raw);
    if (response.ok) {
      if (!command && !validBrainResponse(value, resource.startsWith("facts/") ? "provenance" : resource, siteId)) throw new Error();
      if (command && command.path !== "extraction" && (!exact(value, ["schema_version", "outcome"]) || value.schema_version !== 1 || !["approved", "corrected", "removed", "recorded", "proposed"].includes(String(value.outcome)))) throw new Error();
      if (command?.path === "extraction" && (!exact(value, ["schema_version", "state", "extraction_id", "decision_id", "reason"]) || value.schema_version !== 1 || !["unavailable", "completed", "failed", "outcome_unknown"].includes(String(value.state)) || !nullableId(value.extraction_id) || !nullableId(value.decision_id) || !(value.reason === null || text(value.reason,128)))) throw new Error();
    } else if (!exact(value, ["error"]) || !exact(value.error, ["code", "message", "retryable", "correlation_id"]) || !text(value.error.code,64) || !text(value.error.message,200) || typeof value.error.retryable !== "boolean" || !text(value.error.correlation_id,64)) throw new Error();
    return Response.json(value, { status: response.status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  } catch { return Response.json({ state: "unavailable" }, { status: 503 }); }
}
