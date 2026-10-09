import { dashboardMutationAccepted, exactCookie, TENANT_COOKIE_NAME, validatedDashboardOrigin } from "@/lib/browser-auth";
import { boundedEmailText } from "@/lib/email-api";
import { revokeTeamInvitation } from "@/lib/team-api";

export async function POST(request: Request): Promise<Response> {
  const headers = { "Cache-Control": "no-store" };
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return Response.json({ state: "unconfirmed" }, { status: 503, headers }); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!dashboardMutationAccepted(request, origin) || token === null || request.headers.get("content-type") !== "application/json")
    return Response.json({ state: "denied" }, { status: 403, headers });
  try {
    const body = JSON.parse(await boundedEmailText(request.body, 512));
    if (body === null || Array.isArray(body) || Object.keys(body).sort().join(",") !== "confirmed,invitation_id,site_id" ||
      typeof body.site_id !== "string" || typeof body.invitation_id !== "string" || body.confirmed !== true) throw new Error();
    const state = await revokeTeamInvitation({ tenantToken: token, siteId: body.site_id, invitationId: body.invitation_id, confirmed: true, origin });
    return Response.json(state, { status: state.state === "revoked" ? 200 : state.state === "denied" ? 403 : 503, headers });
  } catch { return Response.json({ state: "denied" }, { status: 422, headers }); }
}
