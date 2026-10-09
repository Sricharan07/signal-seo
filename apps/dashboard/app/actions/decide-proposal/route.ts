import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import {
  type DashboardApprovalDecision,
  decideDashboardProposal,
} from "@/lib/proposal-api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const DECISIONS = new Set<DashboardApprovalDecision>([
  "approved",
  "rejected",
  "changes_requested",
]);
const FIELDS = [
  "approval_request_id",
  "decision",
  "decision_id",
  "revision_sha256",
  "site_id",
] as const;

export async function POST(request: Request): Promise<Response> {
  let dashboardOrigin: string;
  try {
    dashboardOrigin = validatedDashboardOrigin();
  } catch {
    return Response.json(
      { error: { code: "DASHBOARD_NOT_READY", message: "The dashboard is not ready." } },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (!dashboardMutationAccepted(request, dashboardOrigin)) {
    return redirectToApprovals("request_rejected", dashboardOrigin);
  }
  const tenantToken = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  let form: URLSearchParams;
  try {
    form = await strictForm(request);
  } catch {
    return redirectToApprovals("request_rejected", dashboardOrigin);
  }
  const siteId = form.get("site_id") ?? "";
  const approvalRequestId = form.get("approval_request_id") ?? "";
  const revisionSha256 = form.get("revision_sha256") ?? "";
  const decisionId = form.get("decision_id") ?? "";
  const decision = form.get("decision") ?? "";
  if (
    tenantToken === null ||
    !UUID.test(siteId) ||
    !UUID.test(approvalRequestId) ||
    !UUID_V4.test(decisionId) ||
    !SHA256.test(revisionSha256) ||
    !DECISIONS.has(decision as DashboardApprovalDecision)
  ) {
    return redirectToApprovals("request_rejected", dashboardOrigin);
  }
  const result = await decideDashboardProposal({
    tenantToken,
    siteId,
    approvalRequestId,
    revisionSha256,
    decisionId,
    decision: decision as DashboardApprovalDecision,
    dashboardOrigin,
  });
  const state = result.state === "available" ? decision : result.state;
  if (result.state === "available" && decision === "approved") {
    return redirectToOutcome("changes", state, dashboardOrigin);
  }
  return redirectToOutcome("approvals", state, dashboardOrigin);
}

async function strictForm(request: Request): Promise<URLSearchParams> {
  if (
    request.headers.get("content-type")?.split(";", 1)[0]?.trim() !==
    "application/x-www-form-urlencoded"
  ) {
    throw new Error("Invalid form");
  }
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > 1024) throw new Error("Invalid form");
  const form = new URLSearchParams(body);
  const actual = [...new Set(form.keys())].sort();
  if (
    actual.length !== FIELDS.length ||
    !actual.every((field, index) => field === [...FIELDS].sort()[index]) ||
    FIELDS.some((field) => form.getAll(field).length !== 1)
  ) {
    throw new Error("Invalid form");
  }
  return form;
}

function redirectToApprovals(state: string, dashboardOrigin: string): Response {
  return redirectToOutcome("approvals", state, dashboardOrigin);
}

function redirectToOutcome(
  destination: "approvals" | "changes",
  state: string,
  dashboardOrigin: string,
): Response {
  return new Response(null, {
    status: 303,
    headers: {
      "Cache-Control": "no-store",
      Location: new URL(`/${destination}?approval=${encodeURIComponent(state)}`, dashboardOrigin).href,
    },
  });
}
