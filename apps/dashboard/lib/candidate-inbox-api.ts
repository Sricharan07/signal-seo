import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME, validatedDashboardOrigin } from "./browser-auth";

const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const SHA1 = /^[0-9a-f]{40}$/;
const MAX_JSON_BYTES = 48 * 1024;

export type CandidateDecision = "approved" | "rejected" | "changes_requested";
export type CandidateReviewStatus = CandidateDecision | "pending" | "superseded" | "stale_base";

export interface DashboardCandidateRevision {
  revisionId: string;
  revisionSha256: string;
  sealedAt: string;
  recipeReleaseId: string;
  releaseContentHash: string;
  baseSha: string;
  patchSha256: string;
  reviewStatus: CandidateReviewStatus;
  decisionId: string | null;
  decision: CandidateDecision | null;
  decidedAt: string | null;
  sourcePath: string;
  before: string;
  after: string;
  finding: { id: string; title: string; summary: string; resourceLocator: string };
  evidence: { kind?: "crawl" | "indexnow_key"; manifestId: string; manifestSha256: string; pageUrl: string };
  build: { toolchain: string; command: string; logsSha256: string; artifacts: readonly { path: string; sha256: string; size: number }[] };
  expectedImpact: string;
  recoveryPlan: string;
  claimReviewRequired: boolean;
  reused: boolean;
  approvalClass?: "A2" | "A4";
  builtImpact?: { pageCount: number; pages: readonly string[]; scopeSha256: string; lockfileSha256: string; samples: readonly { path: string; before: string; after: string }[] };
}

export type DashboardCandidateInbox =
  | { state: "available"; revisions: readonly DashboardCandidateRevision[] }
  | { state: "not_authenticated" | "unavailable" | "invalid" | "conflict" };

interface Options { tenantToken: string; siteId: string; baseUrl?: string; fetcher?: typeof globalThis.fetch; dashboardOrigin?: string; }

export async function loadDashboardCandidateInbox({ tenantToken, siteId, baseUrl, fetcher = globalThis.fetch }: Options): Promise<DashboardCandidateInbox> {
  const prepared = prepare(tenantToken, siteId, baseUrl);
  if (prepared === null) return { state: "invalid" };
  let response: Response;
  try {
    response = await relayJson(new URL(`/v1/sites/${siteId}/candidate-recipe-inbox`, prepared.baseUrl), {
      cache: "no-store", redirect: "error", headers: { Accept: "application/json", Cookie: prepared.cookie }, signal: AbortSignal.timeout(2500),
    }, fetcher, 49152);
  } catch { return { state: "unavailable" }; }
  const state = responseState(response);
  if (state !== null) return state;
  try {
    const payload = await readJson(response);
    if (!exact(payload, ["correlation_id", "revisions", "schema_version", "site_id"]) || payload.schema_version !== 1 || payload.site_id !== siteId || !Array.isArray(payload.revisions) || payload.revisions.length > 50) throw new Error("invalid");
    return { state: "available", revisions: payload.revisions.map(validateRevision) };
  } catch { return { state: "invalid" }; }
}

export async function decideDashboardCandidateRevision({ tenantToken, siteId, revisionId, revisionSha256, decisionId, decision, baseUrl, fetcher = globalThis.fetch, dashboardOrigin = validatedDashboardOrigin() }: Options & { revisionId: string; revisionSha256: string; decisionId: string; decision: CandidateDecision }): Promise<DashboardCandidateInbox> {
  const prepared = prepare(tenantToken, siteId, baseUrl);
  if (prepared === null || !UUID.test(revisionId) || !SHA256.test(revisionSha256) || !UUID_V4.test(decisionId) || !["approved", "rejected", "changes_requested"].includes(decision)) return { state: "invalid" };
  try {
    const csrfResponse = await relayJson(new URL("/v1/session/tenant-csrf", prepared.baseUrl), { cache: "no-store", redirect: "error", headers: { Accept: "application/json", Cookie: prepared.cookie }, signal: AbortSignal.timeout(2500) }, fetcher, 49152);
    const csrfState = responseState(csrfResponse);
    if (csrfState !== null) return csrfState;
    const csrfPayload = await readJson(csrfResponse);
    if (!exact(csrfPayload, ["csrf_token", "schema_version"]) || csrfPayload.schema_version !== 1 || typeof csrfPayload.csrf_token !== "string" || !TOKEN.test(csrfPayload.csrf_token)) return { state: "invalid" };
    const response = await relayJson(new URL(`/v1/sites/${siteId}/candidate-recipe-revisions/${revisionId}/decision`, prepared.baseUrl), {
      cache: "no-store", redirect: "error", method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json", Cookie: prepared.cookie, Origin: dashboardOrigin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrfPayload.csrf_token },
      body: JSON.stringify({ schema_version: 1, revision_sha256: revisionSha256, decision_id: decisionId, decision }), signal: AbortSignal.timeout(15_000),
    }, fetcher, 49152);
    const state = responseState(response);
    if (state !== null) return state;
    const payload = await readJson(response);
    if (!exact(payload, ["correlation_id", "revision", "schema_version", "site_id"]) || payload.schema_version !== 1 || payload.site_id !== siteId) return { state: "invalid" };
    validateRevision(payload.revision);
    return loadDashboardCandidateInbox({ tenantToken, siteId, baseUrl: prepared.baseUrl.href, fetcher });
  } catch { return { state: "unavailable" }; }
}

function prepare(tenantToken: string, siteId: string, configuredBaseUrl?: string): { baseUrl: URL; cookie: string } | null {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId)) return null;
  try { const baseUrl = validatedSignalApiBaseUrl(configuredBaseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000"); return { baseUrl, cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }; } catch { return null; }
}
function responseState(response: Response): Exclude<DashboardCandidateInbox, { state: "available" }> | null { if (response.status === 401 || response.status === 403) return { state: "not_authenticated" }; if (response.status === 404 || response.status === 409) return { state: "conflict" }; if (response.status === 503) return { state: "unavailable" }; return response.status === 200 ? null : { state: "invalid" }; }
async function readJson(response: Response): Promise<unknown> { const text = await boundedRelayText(response, MAX_JSON_BYTES); if (new TextEncoder().encode(text).byteLength > MAX_JSON_BYTES) throw new Error("oversized"); return JSON.parse(text) as unknown; }
function exact(value: unknown, fields: readonly string[]): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join("|") === [...fields].sort().join("|"); }
function exactOptional(value: unknown, fields: readonly string[], optional: readonly string[]): value is Record<string, unknown> { if (value === null || typeof value !== "object" || Array.isArray(value)) return false; return exact(value, [...fields, ...optional.filter(key => key in value)]); }
function text(value: unknown, max: number, minimum = 1): string { if (typeof value !== "string" || value.length < minimum || value.length > max || value.includes("\0")) throw new Error("invalid"); return value; }
function fragment(value: unknown): string { if (typeof value !== "string" || new TextEncoder().encode(value).length > 4096 || value.includes("\0")) throw new Error("invalid"); return value; }
function uuid(value: unknown): string { const result = text(value, 36); if (!UUID.test(result)) throw new Error("invalid"); return result; }
function digest(value: unknown, length = 64): string { const result = text(value, length); if ((length === 64 ? SHA256 : SHA1).test(result) === false) throw new Error("invalid"); return result; }
function validateRevision(value: unknown): DashboardCandidateRevision {
  if (!exact(value, ["base_sha", "decided_at", "decided_by_user_id", "decision", "decision_channel", "decision_id", "manifest", "patch_sha256", "recipe_release_id", "release_content_hash", "review_status", "reused", "revision_id", "revision_sha256", "schema_version", "sealed_at"])) throw new Error("invalid");
  if (value.schema_version !== 1 || !["pending", "approved", "rejected", "changes_requested", "superseded", "stale_base"].includes(String(value.review_status)) || typeof value.reused !== "boolean") throw new Error("invalid");
  if (value.decision !== null && value.decision !== value.review_status) throw new Error("invalid");
  const manifest = value.manifest;
  const astro = manifest !== null && typeof manifest === "object" && !Array.isArray(manifest) && (manifest as Record<string, unknown>).framework === "astro";
  const frontMatter = manifest !== null && typeof manifest === "object" && !Array.isArray(manifest) && (manifest as Record<string, unknown>).content_adapter === "front_matter";
  const nextjs = manifest !== null && typeof manifest === "object" && !Array.isArray(manifest) && (manifest as Record<string, unknown>).content_adapter === "nextjs_metadata";
  const built = astro || frontMatter || nextjs;
  if (!exactOptional(manifest, ["approval_class", "audit_report_id", "base_sha", "build_id", "build_receipt", "claim_review_required", "evidence", "expected_impact", "extension_id", "finding_id", "model_draft", "patch", "patch_sha256", "recipe_release_id", "recovery_plan", "release_content_hash", "result_sha256", "schema_version", "site_id", "source_path", "source_sha256", ...(built ? ["framework", "recipe_key", "autonomy_eligible", "built_impact"] : []), ...(frontMatter || nextjs ? ["content_adapter"] : [])], ["structured_data", "internal_link"]) || manifest.schema_version !== 1 || (built ? !["A2", "A4"].includes(String(manifest.approval_class)) || manifest.autonomy_eligible !== false || !(nextjs ? ["nextjs_title", "nextjs_description"] : frontMatter ? ["front_matter_title", "front_matter_description"] : ["astro_title", "astro_description", "astro_alt", "astro_json_ld"]).includes(String(manifest.recipe_key)) : manifest.approval_class !== "owner_review") || typeof manifest.claim_review_required !== "boolean" || (frontMatter && (!["astro", "eleventy", "nextjs"].includes(String(manifest.framework)) || manifest.claim_review_required !== true || manifest.model_draft !== null)) || (nextjs && (manifest.framework !== "nextjs" || manifest.claim_review_required !== true || manifest.model_draft !== null))) throw new Error("invalid");
  const internal = manifest.internal_link;
  if (internal != null) {
    if (built || manifest.structured_data != null || !exact(internal, ["recipe_key", "page_cap", "autonomy_eligible", "baseline_build_id", "output_path", "target_id", "target_url", "graph", "coverage"]) || internal.recipe_key !== "technical_internal_link_add" || internal.autonomy_eligible !== false || manifest.audit_report_id !== null || manifest.claim_review_required !== false || manifest.model_draft !== null || !Number.isInteger(internal.page_cap) || Number(internal.page_cap) < 1 || Number(internal.page_cap) > 3 || internal.output_path !== "_site/" + manifest.source_path || !["complete", "partial"].includes(String(internal.coverage))) throw new Error("invalid");
    uuid(internal.baseline_build_id); uuid(internal.target_id); text(internal.target_url, 2048);
    if (internal.graph === null || typeof internal.graph !== "object" || Array.isArray(internal.graph)) throw new Error("invalid");
  }
  const structured = manifest.structured_data;
  if (built && structured != null) throw new Error("invalid");
  if (structured !== undefined && structured !== null) {
    if (!exact(structured, ["recipe_key", "json_ld", "fact_refs", "owner_required", "autonomy_eligible", "output_path", "baseline_build_id"]) || structured.recipe_key !== "structured_data_grounded" || structured.autonomy_eligible !== false || structured.output_path !== "_site/" + manifest.source_path || typeof structured.owner_required !== "boolean" || structured.owner_required !== manifest.claim_review_required) throw new Error("invalid");
    uuid(structured.baseline_build_id);
    const document = structured.json_ld;
    if (document === null || typeof document !== "object" || Array.isArray(document) || !("@type" in document) || !["FAQPage", "Article", "BlogPosting", "Organization", "Product", "BreadcrumbList"].includes(String(document["@type"])) || structured.owner_required !== ["Organization", "Product"].includes(String(document["@type"]))) throw new Error("invalid");
    const refs = structured.fact_refs;
    if (refs === null || typeof refs !== "object" || Array.isArray(refs) || Object.keys(refs).some(key => !["name", "description", "url"].includes(key))) throw new Error("invalid");
    Object.values(refs).forEach(uuid);
  }
  const patch = manifest.patch; const evidence = manifest.evidence; const finding = evidence && typeof evidence === "object" && !Array.isArray(evidence) ? (evidence as Record<string, unknown>).finding : null; const receipt = manifest.build_receipt;
  const keyEvidence = exact(evidence, ["finding", "key_id", "key_sha256", "site_origin", "page_url"]);
  if (!exact(patch, ["after", "before", "offset"]) || !Number.isInteger(patch.offset) || Number(patch.offset) < 0 || (!keyEvidence && !exactOptional(evidence, ["finding", "manifest_id", "manifest_sha256", "page_id", "page_url", ...(built ? ["site_origin"] : [])], (structured || internal) && !built ? ["site_origin"] : [])) || finding === null || typeof finding !== "object" || Array.isArray(finding) || !exact(receipt, ["artifacts", "command", "exit_class", "logs_sha256", "toolchain"]) || receipt.exit_class !== "passed" || !Array.isArray(receipt.artifacts) || receipt.artifacts.length > 1000) throw new Error("invalid");
  if (internal != null) {
    if ((finding as Record<string, unknown>).key !== "links.internal.add" || typeof evidence.site_origin !== "string" || typeof internal.target_url !== "string" || !/^[A-Za-z0-9-]+(?: [A-Za-z0-9-]+){1,2}$/.test(String(patch.before))) throw new Error("invalid");
    const origin = new URL(evidence.site_origin), target = new URL(internal.target_url);
    if (origin.protocol !== "https:" || origin.origin !== evidence.site_origin || target.origin !== origin.origin || target.username || target.password || target.search || target.hash || /[\x00-\x20\\]/.test(internal.target_url) || patch.after !== `<a href="${internal.target_url.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("'", "&#x27;")}">${patch.before}</a>`) throw new Error("invalid");
  }
  if (keyEvidence && (manifest.audit_report_id !== null || patch.before !== "" || patch.offset !== 0 || manifest.source_path !== patch.after + ".txt" || manifest.result_sha256 !== evidence.key_sha256 || (finding as Record<string, unknown>).key !== "indexnow.key.required")) throw new Error("invalid");
  let builtImpact: DashboardCandidateRevision["builtImpact"];
  if (built) {
    const impact = manifest.built_impact;
    if (!exact(impact, ["baseline_build_id", "lockfile_sha256", "scope_sha256", "page_count", "pages", "samples"]) || !Number.isInteger(impact.page_count) || Number(impact.page_count) < 1 || Number(impact.page_count) > 128 || !Array.isArray(impact.pages) || impact.pages.length !== impact.page_count || !Array.isArray(impact.samples) || impact.samples.length < 1 || impact.samples.length > 3) throw new Error("invalid");
    uuid(impact.baseline_build_id);
    const pages = impact.pages.map(page => text(page, 2048));
    if (new Set(pages).size !== pages.length || [...pages].sort().join("|") !== pages.join("|")) throw new Error("invalid");
    const samples = impact.samples.map(sample => {
      if (!exact(sample, ["path", "before", "after"]) || !pages.includes(String(sample.path)) || typeof sample.before !== "string" || typeof sample.after !== "string" || sample.before.length > 4096 || sample.after.length > 4096) throw new Error("invalid");
      return { path: text(sample.path, 2048), before: sample.before, after: sample.after };
    });
    builtImpact = { pageCount: Number(impact.page_count), pages, samples, scopeSha256: digest(impact.scope_sha256), lockfileSha256: digest(impact.lockfile_sha256) };
  }
  const findingRecord = finding as Record<string, unknown>; const artifacts = receipt.artifacts.map((artifact) => { if (!exact(artifact, ["path", "sha256", "size"]) || !Number.isInteger(artifact.size) || Number(artifact.size) < 0) throw new Error("invalid"); return { path: text(artifact.path, 2048), sha256: digest(artifact.sha256), size: Number(artifact.size) }; });
  const evidenceIds = keyEvidence
    ? { id: evidence.key_id, sha256: evidence.key_sha256 }
    : { id: (evidence as Record<string, unknown>).manifest_id, sha256: (evidence as Record<string, unknown>).manifest_sha256 };
  return { ...(built ? { approvalClass: manifest.approval_class as "A2" | "A4", builtImpact } : {}), revisionId: uuid(value.revision_id), revisionSha256: digest(value.revision_sha256), sealedAt: text(value.sealed_at, 40), recipeReleaseId: uuid(value.recipe_release_id), releaseContentHash: digest(value.release_content_hash), baseSha: digest(value.base_sha, 40), patchSha256: digest(value.patch_sha256), reviewStatus: value.review_status as CandidateReviewStatus, decisionId: value.decision_id === null ? null : uuid(value.decision_id), decision: value.decision as CandidateDecision | null, decidedAt: value.decided_at === null ? null : text(value.decided_at, 40), sourcePath: text(manifest.source_path, 2048), before: keyEvidence ? "" : built ? fragment(patch.before) : text(patch.before, 4096, structured ? 0 : 1), after: built ? fragment(patch.after) : text(patch.after, 4096), finding: { id: uuid(findingRecord.id), title: text(findingRecord.title, 500), summary: text(findingRecord.summary, 1000), resourceLocator: text(findingRecord.resource_locator, 2048) }, evidence: { kind: keyEvidence ? "indexnow_key" : "crawl", manifestId: uuid(evidenceIds.id), manifestSha256: digest(evidenceIds.sha256), pageUrl: text(evidence.page_url, 2048) }, build: { toolchain: text(receipt.toolchain, 512), command: text(receipt.command, 512), logsSha256: digest(receipt.logs_sha256), artifacts }, expectedImpact: text(manifest.expected_impact, 500), recoveryPlan: text(manifest.recovery_plan, 500), claimReviewRequired: manifest.claim_review_required, reused: value.reused };
}
