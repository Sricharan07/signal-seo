import { dashboardMutationAccepted, exactCookie, TENANT_COOKIE_NAME, validatedDashboardOrigin } from "@/lib/browser-auth";
import { boundedEmailText } from "@/lib/email-api";
import { issueTeamInvitation } from "@/lib/team-api";

export async function POST(request: Request): Promise<Response> {
  const headers = { "Cache-Control": "no-store" };
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return Response.json({ state: "unconfirmed" }, { status: 503, headers }); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!dashboardMutationAccepted(request, origin) || token === null || request.headers.get("content-type") !== "application/json") {
    return Response.json({ state: "denied" }, { status: 403, headers });
  }
  try {
    const body = JSON.parse(await boundedEmailText(request.body, 2048));
    if (body === null || Array.isArray(body) || Object.keys(body).sort().join(",") !== "email,role_key,site_id" ||
      ![body.email, body.role_key, body.site_id].every(v => typeof v === "string")) throw new Error();
    const state = await issueTeamInvitation({ tenantToken: token, siteId: body.site_id, email: body.email, roleKey: body.role_key, origin });
    return Response.json(state, { status: state.state === "created" ? 201 : state.state === "denied" ? 403 : state.state === "pending" ? 409 : 503, headers });
  } catch { return Response.json({ state: "denied" }, { status: 422, headers }); }
}
