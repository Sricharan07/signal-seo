import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME, validatedDashboardOrigin } from "./browser-auth";

const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_JSON_BYTES = 48 * 1024;

export type DashboardApprovalDecision = "approved" | "rejected" | "changes_requested";
export type DashboardApprovalStatus =
  | "pending"
  | "expired"
  | DashboardApprovalDecision;
export type DashboardProposalNotice =
  | "prepared"
  | DashboardApprovalDecision
  | "request_rejected"
  | "not_authenticated"
  | "unavailable"
  | "invalid"
  | "conflict";

export interface DashboardRoleContribution {
  role: "technical_seo" | "content_strategy" | "independent_reviewer" | "coordinator";
  release:
    | "local-deterministic-v1"
    | "local-gpt-6-luna-metadata-v2"
    | "verified-gpt-6-luna-metadata-v2"
    | "local-gpt-5.6-luna-metadata-v1"
    | "verified-gpt-5.6-luna-metadata-v1";
  result:
    | "finding_supported"
    | "metadata_draft_prepared"
    | "scope_checks_passed"
    | "approval_requested";
}

export interface DashboardModelRecord {
  callId: string;
  release:
    | "local-gpt-6-luna-metadata-v2"
    | "verified-gpt-6-luna-metadata-v2"
    | "local-gpt-5.6-luna-metadata-v1"
    | "verified-gpt-5.6-luna-metadata-v1";
  modelRequested: string;
  modelReported: string;
  providerResponseId: string;
  promptSha256: string;
  inputSha256: string;
  outputSha256: string;
  store: false;
  usage: {
    inputTokens: number;
    outputTokens: number;
    cachedInputTokens: number;
    totalTokens: number;
  };
}

export interface DashboardProposal {
  proposalKind:
    | "local_fixture_metadata_draft"
    | "model_fixture_metadata_draft"
    | "model_verified_homepage_metadata_draft";
  proposalId: string;
  revisionId: string;
  revisionNumber: number;
  revisionSha256: string;
  siteId: string;
  findingId: string;
  evidenceId: string;
  commandId: string;
  evidenceObservedAt: string;
  resourceLocator: string;
  beforeValue: null;
  afterValue: string;
  impact:
    | "One synthetic fixture metadata field"
    | "One owner-verified homepage metadata field";
  risk: "low";
  confidenceBasis:
    | "Deterministic HTML metadata parser"
    | "Model draft constrained by deterministic fixture evidence"
    | "Model draft constrained by verified homepage metadata";
  rationale: string | null;
  tests: readonly [string, string, string, string];
  roleContributions: readonly DashboardRoleContribution[];
  model: DashboardModelRecord | null;
  approvalClass: "A1";
  requestedAuthority:
    | "accept_local_fixture_draft"
    | "accept_model_fixture_draft"
    | "accept_verified_homepage_metadata_draft";
  externalWrite: false;
  maximumCostMinorUnits: 0 | 1;
  currency: "USD";
  recoveryMode: "discard_local_draft";
  recoverySummary:
    | "Discard the draft; no external state has changed."
    | "Discard the proposed draft; no external state has changed.";
  createdByUserId: string;
  createdAt: string;
  approvalRequestId: string;
  approvalStatus: DashboardApprovalStatus;
  approvalRequestedAt: string;
  approvalExpiresAt: string;
  decisionId: string | null;
  decision: DashboardApprovalDecision | null;
  decidedByUserId: string | null;
  decisionChannel: "dashboard" | null;
  decidedAt: string | null;
  reused: boolean;
}

export type DashboardProposals =
  | { state: "available"; proposals: readonly DashboardProposal[] }
  | { state: "not_authenticated" | "unavailable" | "invalid" | "conflict" };

interface ProposalOptions {
  tenantToken: string;
  siteId: string;
  dashboardOrigin?: string;
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
}

interface DecisionOptions extends ProposalOptions {
  approvalRequestId: string;
  revisionSha256: string;
  decisionId: string;
  decision: DashboardApprovalDecision;
}

export async function loadDashboardProposals({
  tenantToken,
  siteId,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: Omit<ProposalOptions, "dashboardOrigin">): Promise<DashboardProposals> {
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (prepared === null) return { state: "invalid" };
  let response: Response;
  try {
    response = await relayJson(new URL(`/v1/sites/${siteId}/proposals`, prepared.baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json", Cookie: prepared.cookie },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 49152);
  } catch {
    return { state: "unavailable" };
  }
  return parseProposalList(response, siteId);
}

export async function prepareDashboardProposal({
  tenantToken,
  siteId,
  dashboardOrigin = validatedDashboardOrigin(),
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: ProposalOptions): Promise<DashboardProposals> {
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (prepared === null) return { state: "invalid" };
  const csrf = await acquireCsrf(prepared, fetcher);
  if (typeof csrf !== "string") return csrf;
  let response: Response;
  try {
    response = await relayJson(
      new URL(`/v1/sites/${siteId}/proposals/verified-homepage`, prepared.baseUrl),
      mutationRequest(prepared.cookie, dashboardOrigin, csrf, { schema_version: 1 }), fetcher, 49152);
  } catch {
    return { state: "unavailable" };
  }
  const result = await parseSingleProposal(response, siteId);
  if (result.state !== "available") return result;
  return loadDashboardProposals({
    tenantToken,
    siteId,
    baseUrl: prepared.baseUrl.href,
    fetcher,
  });
}

export async function decideDashboardProposal({
  tenantToken,
  siteId,
  approvalRequestId,
  revisionSha256,
  decisionId,
  decision,
  dashboardOrigin = validatedDashboardOrigin(),
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: DecisionOptions): Promise<DashboardProposals> {
  const prepared = prepare(tenantToken, siteId, configuredBaseUrl);
  if (
    prepared === null ||
    !UUID.test(approvalRequestId) ||
    !SHA256.test(revisionSha256) ||
    !UUID_V4.test(decisionId) ||
    !["approved", "rejected", "changes_requested"].includes(decision)
  ) {
    return { state: "invalid" };
  }
  const csrf = await acquireCsrf(prepared, fetcher);
  if (typeof csrf !== "string") return csrf;
  let response: Response;
  try {
    response = await relayJson(
      new URL(
        `/v1/sites/${siteId}/approval-requests/${approvalRequestId}/decision`,
        prepared.baseUrl,
      ),
      mutationRequest(prepared.cookie, dashboardOrigin, csrf, {
        schema_version: 1,
        revision_sha256: revisionSha256,
        decision_id: decisionId,
        decision,
      }), fetcher, 49152);
  } catch {
    return { state: "unavailable" };
  }
  const result = await parseSingleProposal(response, siteId);
  if (result.state !== "available") return result;
  return loadDashboardProposals({
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

async function acquireCsrf(
  prepared: { baseUrl: URL; cookie: string },
  fetcher: typeof globalThis.fetch,
): Promise<string | Exclude<DashboardProposals, { state: "available" }>> {
  let response: Response;
  try {
    response = await relayJson(new URL("/v1/session/tenant-csrf", prepared.baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json", Cookie: prepared.cookie },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 49152);
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
      !hasExactFields(payload, ["csrf_token", "schema_version"]) ||
      payload.schema_version !== 1 ||
      typeof payload.csrf_token !== "string" ||
      !TOKEN.test(payload.csrf_token)
    ) {
      throw new Error("Invalid CSRF response");
    }
    return payload.csrf_token;
  } catch {
    return { state: "invalid" };
  }
}

function mutationRequest(
  cookie: string,
  dashboardOrigin: string,
  csrf: string,
  body: Record<string, unknown>,
): RequestInit {
  return {
    cache: "no-store",
    redirect: "error",
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Cookie: cookie,
      Origin: dashboardOrigin,
      "Sec-Fetch-Site": "same-origin",
      "X-CSRF-Token": csrf,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  };
}

async function parseProposalList(
  response: Response,
  siteId: string,
): Promise<DashboardProposals> {
  const state = responseState(response);
  if (state !== null) return state;
  try {
    const payload = await readJson(response);
    if (
      !hasExactFields(payload, ["correlation_id", "proposals", "schema_version", "site_id"]) ||
      payload.schema_version !== 1 ||
      payload.site_id !== siteId ||
      !Array.isArray(payload.proposals) ||
      payload.proposals.length > 50
    ) {
      throw new Error("Invalid proposals response");
    }
    return { state: "available", proposals: payload.proposals.map(validateProposal) };
  } catch {
    return { state: "invalid" };
  }
}

async function parseSingleProposal(
  response: Response,
  siteId: string,
): Promise<DashboardProposals> {
  const state = responseState(response);
  if (state !== null) return state;
  try {
    const payload = await readJson(response);
    if (
      !hasExactFields(payload, ["correlation_id", "proposal", "schema_version", "site_id"]) ||
      payload.schema_version !== 1 ||
      payload.site_id !== siteId
    ) {
      throw new Error("Invalid proposal response");
    }
    return { state: "available", proposals: [validateProposal(payload.proposal)] };
  } catch {
    return { state: "invalid" };
  }
}

function responseState(
  response: Response,
): Exclude<DashboardProposals, { state: "available" }> | null {
  if (response.status === 401 || response.status === 403) {
    return { state: "not_authenticated" };
  }
  if (response.status === 409 || response.status === 404) return { state: "conflict" };
  if (response.status === 503) return { state: "unavailable" };
  return response.status === 200 ? null : { state: "invalid" };
}

function validateProposal(value: unknown): DashboardProposal {
  if (
    !hasExactFields(value, [
      "approval_expires_at",
      "approval_request_id",
      "approval_requested_at",
      "approval_status",
      "created_at",
      "created_by_user_id",
      "decided_at",
      "decided_by_user_id",
      "decision",
      "decision_channel",
      "decision_id",
      "manifest",
      "proposal_id",
      "reused",
      "revision_id",
      "revision_number",
      "revision_sha256",
      "schema_version",
    ]) ||
    value.schema_version !== 1 ||
    ![value.proposal_id, value.revision_id, value.created_by_user_id, value.approval_request_id].every(
      (candidate) => typeof candidate === "string" && UUID.test(candidate),
    ) ||
    typeof value.revision_number !== "number" ||
    !Number.isInteger(value.revision_number) ||
    value.revision_number < 1 ||
    value.revision_number > 10000 ||
    typeof value.revision_sha256 !== "string" ||
    !SHA256.test(value.revision_sha256) ||
    typeof value.reused !== "boolean"
  ) {
    throw new Error("Invalid proposal");
  }
  const manifest = validateManifest(value.manifest);
  const approvalStatus = approvalStatusValue(value.approval_status);
  const decision = decisionValue(value.decision);
  const decisionId = nullableUuid(value.decision_id);
  const decidedByUserId = nullableUuid(value.decided_by_user_id);
  const decisionChannel = value.decision_channel === null ? null : value.decision_channel;
  const decidedAt = value.decided_at === null ? null : exactTimestamp(String(value.decided_at));
  const noDecision =
    decision === null &&
    decisionId === null &&
    decidedByUserId === null &&
    decisionChannel === null &&
    decidedAt === null &&
    ["pending", "expired"].includes(approvalStatus);
  const completeDecision =
    decision !== null &&
    decisionId !== null &&
    decidedByUserId !== null &&
    decisionChannel === "dashboard" &&
    decidedAt !== null &&
    approvalStatus === decision;
  if (!noDecision && !completeDecision) throw new Error("Invalid decision projection");
  const requestedAt = exactTimestamp(String(value.approval_requested_at));
  const expiresAt = exactTimestamp(String(value.approval_expires_at));
  if (expiresAt <= requestedAt) throw new Error("Invalid approval lifetime");
  return {
    proposalId: String(value.proposal_id),
    revisionId: String(value.revision_id),
    revisionNumber: value.revision_number,
    revisionSha256: value.revision_sha256,
    ...manifest,
    createdByUserId: String(value.created_by_user_id),
    createdAt: exactTimestamp(String(value.created_at)),
    approvalRequestId: String(value.approval_request_id),
    approvalStatus,
    approvalRequestedAt: requestedAt,
    approvalExpiresAt: expiresAt,
    decisionId,
    decision,
    decidedByUserId,
    decisionChannel,
    decidedAt,
    reused: value.reused,
  };
}

function validateManifest(value: unknown): Omit<
  DashboardProposal,
  | "proposalId"
  | "revisionId"
  | "revisionNumber"
  | "revisionSha256"
  | "createdByUserId"
  | "createdAt"
  | "approvalRequestId"
  | "approvalStatus"
  | "approvalRequestedAt"
  | "approvalExpiresAt"
  | "decisionId"
  | "decision"
  | "decidedByUserId"
  | "decisionChannel"
  | "decidedAt"
  | "reused"
> {
  if (!isRecord(value)) throw new Error("Invalid proposal manifest");
  const proposalKind = String(value.proposal_kind);
  const isVerified = proposalKind === "model_verified_homepage_metadata_draft";
  const isModel = isVerified || proposalKind === "model_fixture_metadata_draft";
  const fields = [
    "assessment",
    "authority",
    "cost",
    "finding",
    "proposal_kind",
    "recipe",
    "recovery",
    "role_contributions",
    "schema_version",
    "site_id",
    "target",
    "tests",
    ...(isModel ? ["model"] : []),
  ];
  if (
    !hasExactFields(value, fields) ||
    value.schema_version !== 1 ||
    ![
      "local_fixture_metadata_draft",
      "model_fixture_metadata_draft",
      "model_verified_homepage_metadata_draft",
    ].includes(proposalKind) ||
    typeof value.site_id !== "string" ||
    !UUID.test(value.site_id)
  ) {
    throw new Error("Invalid proposal manifest");
  }
  const finding = value.finding;
  const target = value.target;
  const assessment = value.assessment;
  const authority = value.authority;
  const cost = value.cost;
  const recovery = value.recovery;
  const recipe = value.recipe;
  if (
    !hasExactFields(finding, [
      "command_id",
      "confidence_class",
      "evidence_id",
      "id",
      "observed_at",
      "resource_locator",
    ]) ||
    ![finding.id, finding.evidence_id, finding.command_id].every(
      (candidate) => typeof candidate === "string" && UUID.test(candidate),
    ) ||
    (isVerified
      ? !validHttpsResource(finding.resource_locator)
      : finding.resource_locator !== "/fixture/missing-meta-description") ||
    finding.confidence_class !== "deterministic" ||
    !hasExactFields(target, ["after", "before", "field", "resource_locator"]) ||
    target.resource_locator !== finding.resource_locator ||
    target.field !== "meta_description" ||
    target.before !== null ||
    typeof target.after !== "string" ||
    !hasExactFields(recipe, ["id", "qualification", "version"]) ||
    recipe.id !== "title_description_improvement" ||
    recipe.qualification !==
      (isVerified ? "verified_homepage_proposal_only" : "fixture_only") ||
    !isRecord(assessment) ||
    assessment.impact !==
      (isVerified
        ? "One owner-verified homepage metadata field"
        : "One synthetic fixture metadata field") ||
    assessment.risk !== "low" ||
    assessment.customer_origin_read !== isVerified ||
    !hasExactFields(authority, ["approval_class", "external_write", "requested"]) ||
    authority.approval_class !== "A1" ||
    authority.external_write !== false ||
    !hasExactFields(cost, ["currency", "maximum_minor_units"]) ||
    cost.currency !== "USD" ||
    !hasExactFields(recovery, ["external_state_changed", "mode", "summary"]) ||
    recovery.mode !== "discard_local_draft" ||
    recovery.external_state_changed !== false ||
    recovery.summary !==
      (isVerified
        ? "Discard the proposed draft; no external state has changed."
        : "Discard the draft; no external state has changed.")
  ) {
    throw new Error("Invalid proposal manifest values");
  }
  const model = isModel ? validateModelRecord(value.model, isVerified) : null;
  const rationale = isModel ? modelRationale(assessment) : null;
  const deterministicValues =
    !isModel &&
    target.after === "Explore the Signal test fixture and its durable SEO evidence." &&
    recipe.version === "local-fixture-0.1.0" &&
    hasExactFields(assessment, [
      "confidence_basis",
      "customer_origin_read",
      "impact",
      "risk",
    ]) &&
    assessment.confidence_basis === "Deterministic HTML metadata parser" &&
    authority.requested === "accept_local_fixture_draft" &&
    cost.maximum_minor_units === 0;
  const modelValues =
    isModel &&
    !isVerified &&
    target.after.length >= 70 &&
    target.after.length <= 160 &&
    target.after === target.after.trim() &&
    !target.after.includes("\0") &&
    recipe.version === "local-model-0.1.0" &&
    assessment.confidence_basis ===
      "Model draft constrained by deterministic fixture evidence" &&
    authority.requested === "accept_model_fixture_draft" &&
    cost.maximum_minor_units === 1;
  const verifiedModelValues =
    isVerified &&
    target.after.length >= 70 &&
    target.after.length <= 160 &&
    target.after === target.after.trim() &&
    !target.after.includes("\0") &&
    recipe.version === "verified-homepage-model-1.0.0" &&
    assessment.confidence_basis ===
      "Model draft constrained by verified homepage metadata" &&
    authority.requested === "accept_verified_homepage_metadata_draft" &&
    cost.maximum_minor_units === 1;
  if (!deterministicValues && !modelValues && !verifiedModelValues) {
    throw new Error("Invalid proposal release values");
  }
  const tests = value.tests;
  const roles = value.role_contributions;
  const expectedTests = isVerified
    ? "finding_evidence_bound|model_output_schema_valid|verified_homepage_target_scoped|external_write_disabled"
    : isModel
    ? "finding_evidence_bound|model_output_schema_valid|target_field_scoped|external_write_disabled"
    : "finding_evidence_bound|target_field_scoped|customer_origin_not_read|external_write_disabled";
  if (
    !Array.isArray(tests) ||
    tests.join("|") !== expectedTests ||
    !Array.isArray(roles) ||
    roles.length !== 4
  ) {
    throw new Error("Invalid proposal checks");
  }
  const roleContributions = roles.map((role) =>
    validateRoleContribution(role, isModel, isVerified),
  );
  const expectedRoleResults = [
    ["technical_seo", "finding_supported"],
    ["content_strategy", "metadata_draft_prepared"],
    ["independent_reviewer", "scope_checks_passed"],
    ["coordinator", "approval_requested"],
  ] as const;
  if (roleContributions.some((entry, index) =>
    entry.role !== expectedRoleResults[index]?.[0] ||
    entry.result !== expectedRoleResults[index]?.[1] ||
    entry.release !== (isModel && index === 1 ? model?.release : "local-deterministic-v1")
  )) {
    throw new Error("Invalid proposal roles");
  }
  return {
    proposalKind: proposalKind as DashboardProposal["proposalKind"],
    siteId: value.site_id,
    findingId: String(finding.id),
    evidenceId: String(finding.evidence_id),
    commandId: String(finding.command_id),
    evidenceObservedAt: exactTimestamp(String(finding.observed_at)),
    resourceLocator: target.resource_locator,
    beforeValue: null,
    afterValue: target.after,
    impact: assessment.impact as DashboardProposal["impact"],
    risk: assessment.risk,
    confidenceBasis: assessment.confidence_basis as DashboardProposal["confidenceBasis"],
    rationale,
    tests: [
      tests[0],
      tests[1],
      tests[2],
      tests[3],
    ] as DashboardProposal["tests"],
    roleContributions,
    model,
    approvalClass: authority.approval_class,
    requestedAuthority: authority.requested,
    externalWrite: false,
    maximumCostMinorUnits: cost.maximum_minor_units,
    currency: "USD",
    recoveryMode: recovery.mode,
    recoverySummary: recovery.summary,
  };
}

function validateModelRecord(
  value: unknown,
  verifiedHomepage: boolean,
): DashboardModelRecord {
  if (
    !hasExactFields(value, [
      "call_id",
      "input_sha256",
      "model_reported",
      "model_requested",
      "provider_response_id",
      "output_sha256",
      "prompt_sha256",
      "release",
      "store",
      "usage",
    ]) ||
    typeof value.call_id !== "string" ||
    !UUID.test(value.call_id) ||
    !(verifiedHomepage
      ? ["verified-gpt-6-luna-metadata-v2", "verified-gpt-5.6-luna-metadata-v1"]
      : ["local-gpt-6-luna-metadata-v2", "local-gpt-5.6-luna-metadata-v1"]
    ).includes(String(value.release)) ||
    typeof value.model_requested !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value.model_requested) ||
    (String(value.release).endsWith("-v1") && value.model_requested !== "gpt-5.6-luna") ||
    typeof value.model_reported !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value.model_reported) ||
    !(value.model_reported === value.model_requested || value.model_reported.startsWith(value.model_requested + "-")) ||
    typeof value.provider_response_id !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$/.test(value.provider_response_id) ||
    ![value.prompt_sha256, value.input_sha256, value.output_sha256].every(
      (digest) => typeof digest === "string" && SHA256.test(digest),
    ) ||
    value.store !== false ||
    !hasExactFields(value.usage, [
      "cached_input_tokens",
      "input_tokens",
      "output_tokens",
      "total_tokens",
    ]) ||
    ![
      value.usage.cached_input_tokens,
      value.usage.input_tokens,
      value.usage.output_tokens,
      value.usage.total_tokens,
    ].every((tokens) => Number.isSafeInteger(tokens) && Number(tokens) >= 0) ||
    Number(value.usage.cached_input_tokens) > Number(value.usage.input_tokens) ||
    Number(value.usage.total_tokens) <
      Number(value.usage.input_tokens) + Number(value.usage.output_tokens)
  ) {
    throw new Error("Invalid model record");
  }
  return {
    callId: value.call_id,
    release: value.release,
    modelRequested: value.model_requested,
    modelReported: value.model_reported,
    providerResponseId: value.provider_response_id,
    promptSha256: String(value.prompt_sha256),
    inputSha256: String(value.input_sha256),
    outputSha256: String(value.output_sha256),
    store: false,
    usage: {
      inputTokens: Number(value.usage.input_tokens),
      outputTokens: Number(value.usage.output_tokens),
      cachedInputTokens: Number(value.usage.cached_input_tokens),
      totalTokens: Number(value.usage.total_tokens),
    },
  };
}

function modelRationale(value: unknown): string {
  if (
    !hasExactFields(value, [
      "confidence_basis",
      "customer_origin_read",
      "impact",
      "rationale",
      "risk",
    ]) ||
    typeof value.rationale !== "string" ||
    value.rationale.length < 1 ||
    value.rationale.length > 300 ||
    value.rationale !== value.rationale.trim() ||
    value.rationale.includes("\0")
  ) {
    throw new Error("Invalid model rationale");
  }
  return value.rationale;
}

function validateRoleContribution(
  value: unknown,
  modelProposal: boolean,
  verifiedHomepage: boolean,
): DashboardRoleContribution {
  if (
    !hasExactFields(value, ["release", "result", "role"]) ||
    ![
      "local-deterministic-v1",
      ...(modelProposal ? ["local-gpt-6-luna-metadata-v2", "local-gpt-5.6-luna-metadata-v1"] : []),
      ...(verifiedHomepage ? ["verified-gpt-6-luna-metadata-v2", "verified-gpt-5.6-luna-metadata-v1"] : []),
    ].includes(String(value.release)) ||
    !["technical_seo", "content_strategy", "independent_reviewer", "coordinator"].includes(
      String(value.role),
    ) ||
    ![
      "finding_supported",
      "metadata_draft_prepared",
      "scope_checks_passed",
      "approval_requested",
    ].includes(String(value.result))
  ) {
    throw new Error("Invalid role contribution");
  }
  return value as DashboardRoleContribution;
}

function approvalStatusValue(value: unknown): DashboardApprovalStatus {
  if (
    typeof value !== "string" ||
    !["pending", "expired", "approved", "rejected", "changes_requested"].includes(value)
  ) {
    throw new Error("Invalid approval status");
  }
  return value as DashboardApprovalStatus;
}

function decisionValue(value: unknown): DashboardApprovalDecision | null {
  if (value === null) return null;
  if (
    typeof value !== "string" ||
    !["approved", "rejected", "changes_requested"].includes(value)
  ) {
    throw new Error("Invalid approval decision");
  }
  return value as DashboardApprovalDecision;
}

function nullableUuid(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !UUID.test(value)) throw new Error("Invalid UUID");
  return value;
}

function validHttpsResource(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length < 9 ||
    value.length > 2048 ||
    !value.startsWith("https://") ||
    /[#\s]/.test(value)
  ) return false;
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.username === "" &&
      url.password === "" &&
      url.hash === ""
    );
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

function hasExactFields(value: unknown, fields: readonly string[]): value is Record<string, any> {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  return actual.length === expected.length && actual.every((field, index) => field === expected[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
