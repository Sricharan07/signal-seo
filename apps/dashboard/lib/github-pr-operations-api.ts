import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const SHA1 = /^[0-9a-f]{40}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const BRANCH = /^signal\/[0-9a-f]{32}$/;

export interface DashboardGithubPrOperation {
  operationId: string;
  revisionId: string;
  revisionSha256: string;
  branchName: string;
  state: "planned" | "dispatching" | "outcome_unknown" | "ready" | "opened" | "blocked";
  step: "tree" | "commit" | "branch" | "pr" | "done";
  baseSha: string;
  expectedTreeSha: string | null;
  expectedCommitSha: string | null;
  prNumber: number | null;
  prUrl: string | null;
  journalGeneration: string | null;
  journalPosition: number | null;
  journalBodyHash: string | null;
  createdAt: string;
  updatedAt: string;
  authority?: { kind: "owner_inbox" | "standing_grant" | "owner_editorial"; recordId: string; ownerUserId: string; decisionChannel: "dashboard" | "slack" | "telegram" | null } | null;
}

export type DashboardGithubPrOperations =
  | { state: "available"; operations: readonly DashboardGithubPrOperation[] }
  | { state: "not_authenticated" | "unavailable" | "invalid" };

function exact(value: unknown, fields: readonly string[]): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("|") === [...fields].sort().join("|");
}

function string(value: unknown, max: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max || value.includes("\0")) throw new Error("invalid");
  return value;
}

function optional<T>(value: unknown, parse: (value: unknown) => T): T | null {
  return value === null ? null : parse(value);
}

function parseOperation(value: unknown): DashboardGithubPrOperation {
  const fields = ["base_sha", "branch_name", "created_at", "expected_commit_sha", "expected_tree_sha", "journal_body_hash", "journal_generation", "journal_position", "operation_id", "pr_number", "pr_url", "revision_id", "revision_sha256", "schema_version", "state", "step", "updated_at"];
  if (!exact(value, fields) && !exact(value, [...fields, "authority"])) throw new Error("invalid");
  if ((value.schema_version !== 1 && value.schema_version !== 2) || (value.schema_version === 1 && "authority" in value) || (value.schema_version === 2 && !("authority" in value)) || !["planned", "dispatching", "outcome_unknown", "ready", "opened", "blocked"].includes(String(value.state)) || !["tree", "commit", "branch", "pr", "done"].includes(String(value.step))) throw new Error("invalid");
  let authority: DashboardGithubPrOperation["authority"] = null;
  if (value.schema_version === 2 && value.authority !== null) {
    if (!exact(value.authority, ["kind", "record_id", "owner_user_id", "decision_channel"]) || !["owner_inbox", "standing_grant", "owner_editorial"].includes(String(value.authority.kind))) throw new Error("invalid");
    if (value.authority.kind === "owner_editorial" ? value.authority.decision_channel !== "dashboard" : value.authority.kind === "owner_inbox" ? !["dashboard", "slack", "telegram"].includes(String(value.authority.decision_channel)) : value.authority.decision_channel !== null) throw new Error("invalid");
    const recordId = string(value.authority.record_id, 36);
    const ownerUserId = string(value.authority.owner_user_id, 36);
    if (!UUID.test(recordId) || !UUID.test(ownerUserId)) throw new Error("invalid");
    authority = { kind: value.authority.kind as "owner_inbox" | "standing_grant" | "owner_editorial", recordId, ownerUserId, decisionChannel: value.authority.decision_channel as "dashboard" | "slack" | "telegram" | null };
  }
  const operationId = string(value.operation_id, 36);
  const revisionId = string(value.revision_id, 36);
  const revisionSha256 = string(value.revision_sha256, 64);
  const branchName = string(value.branch_name, 39);
  const baseSha = string(value.base_sha, 40);
  if (!UUID.test(operationId) || !UUID.test(revisionId) || !SHA256.test(revisionSha256) || !BRANCH.test(branchName) || !SHA1.test(baseSha)) throw new Error("invalid");
  const expectedTreeSha = optional(value.expected_tree_sha, (part) => string(part, 40));
  const expectedCommitSha = optional(value.expected_commit_sha, (part) => string(part, 40));
  const journalGeneration = optional(value.journal_generation, (part) => string(part, 36));
  const journalBodyHash = optional(value.journal_body_hash, (part) => string(part, 64));
  if ((expectedTreeSha !== null && !SHA1.test(expectedTreeSha)) || (expectedCommitSha !== null && !SHA1.test(expectedCommitSha)) || (journalGeneration !== null && !UUID.test(journalGeneration)) || (journalBodyHash !== null && !SHA256.test(journalBodyHash))) throw new Error("invalid");
  const journalPosition = optional(value.journal_position, (part) => { if (!Number.isSafeInteger(part) || Number(part) < 1) throw new Error("invalid"); return Number(part); });
  const prNumber = optional(value.pr_number, (part) => { if (!Number.isSafeInteger(part) || Number(part) < 1) throw new Error("invalid"); return Number(part); });
  const prUrl = optional(value.pr_url, (part) => string(part, 2048));
  if (prUrl !== null && !/^https:\/\/github\.com\/[A-Za-z0-9-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*$/.test(prUrl)) throw new Error("invalid");
  if ((value.state === "opened") !== (prNumber !== null && prUrl !== null && value.step === "done")) throw new Error("invalid");
  if ([journalGeneration, journalPosition, journalBodyHash].filter((part) => part !== null).length % 3 !== 0) throw new Error("invalid");
  return { operationId, revisionId, revisionSha256, branchName, state: value.state as DashboardGithubPrOperation["state"], step: value.step as DashboardGithubPrOperation["step"], baseSha, expectedTreeSha, expectedCommitSha, prNumber, prUrl, journalGeneration, journalPosition, journalBodyHash, createdAt: string(value.created_at, 40), updatedAt: string(value.updated_at, 40), ...(value.schema_version === 2 ? {authority} : {}) };
}

export async function loadDashboardGithubPrOperations({
  tenantToken, siteId, baseUrl, fetcher = globalThis.fetch,
}: { tenantToken: string; siteId: string; baseUrl?: string; fetcher?: typeof globalThis.fetch }): Promise<DashboardGithubPrOperations> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId)) return { state: "invalid" };
  let origin: URL;
  try { origin = validatedSignalApiBaseUrl(baseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000"); }
  catch { return { state: "invalid" }; }
  let response: Response;
  try {
    response = await relayJson(new URL(`/v1/sites/${siteId}/github-pr-operations`, origin), {
      cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 131072);
  } catch { return { state: "unavailable" }; }
  if (response.status === 401 || response.status === 403) return { state: "not_authenticated" };
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };
  try {
    const raw = await boundedRelayText(response, 24 * 1024);
    if (new TextEncoder().encode(raw).byteLength > 24 * 1024) throw new Error("oversized");
    const payload: unknown = JSON.parse(raw);
    if (!exact(payload, ["correlation_id", "operations", "schema_version", "site_id"]) || payload.schema_version !== 1 || payload.site_id !== siteId || !Array.isArray(payload.operations) || payload.operations.length > 50) throw new Error("invalid");
    return { state: "available", operations: payload.operations.map(parseOperation) };
  } catch { return { state: "invalid" }; }
}
