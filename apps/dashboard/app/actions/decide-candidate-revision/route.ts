import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { decideDashboardCandidateRevision } from "@/lib/candidate-inbox-api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const DECISIONS = new Set(["approved", "rejected", "changes_requested"]);
const FIELDS = ["decision", "decision_id", "revision_id", "revision_sha256", "site_id"] as const;

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return Response.json({ error: { code: "DASHBOARD_NOT_READY", message: "The dashboard is not ready." } }, { status: 503, headers: { "Cache-Control": "no-store" } }); }
  if (!dashboardMutationAccepted(request, origin)) return redirect("request_rejected", origin);
  const tenantToken = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  try {
    const form = await strictForm(request);
    const siteId = form.get("site_id") ?? "";
    const revisionId = form.get("revision_id") ?? "";
    const revisionSha256 = form.get("revision_sha256") ?? "";
    const decisionId = form.get("decision_id") ?? "";
    const decision = form.get("decision") ?? "";
    if (tenantToken === null || !UUID.test(siteId) || !UUID.test(revisionId) || !SHA256.test(revisionSha256) || !UUID_V4.test(decisionId) || !DECISIONS.has(decision)) return redirect("request_rejected", origin);
    const result = await decideDashboardCandidateRevision({ tenantToken, siteId, revisionId, revisionSha256, decisionId, decision: decision as "approved" | "rejected" | "changes_requested", dashboardOrigin: origin });
    return redirect(result.state === "available" ? decision : result.state, origin);
  } catch { return redirect("request_rejected", origin); }
}

async function strictForm(request: Request): Promise<URLSearchParams> {
  if (request.headers.get("content-type")?.split(";", 1)[0]?.trim() !== "application/x-www-form-urlencoded") throw new Error("invalid");
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > 1024) throw new Error("invalid");
  const form = new URLSearchParams(body);
  const actual = [...new Set(form.keys())].sort();
  if (actual.length !== FIELDS.length || !actual.every((field, index) => field === [...FIELDS].sort()[index]) || FIELDS.some((field) => form.getAll(field).length !== 1)) throw new Error("invalid");
  return form;
}

function redirect(state: string, origin: string): Response {
  return new Response(null, { status: 303, headers: { "Cache-Control": "no-store", Location: new URL(`/approvals?candidate=${encodeURIComponent(state)}`, origin).href } });
}
