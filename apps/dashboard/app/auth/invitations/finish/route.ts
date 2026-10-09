import { dashboardMutationAccepted, exactCookie, IDENTITY_COOKIE_NAME, invitationFinishPath,
  selectSignalOrganization, selectSignalSite, TENANT_COOKIE_NAME, validatedDashboardOrigin } from "@/lib/browser-auth";
import { rejectedMutationResponse } from "@/lib/auth-route";
import { boundedEmailText } from "@/lib/email-api";
import { invitationResponse } from "@/lib/invitation-route";
import { loadDashboardSession } from "@/lib/session-api";

export async function POST(request: Request): Promise<Response> {
  const origin = validatedDashboardOrigin();
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type")?.split(";", 1)[0] !== "application/x-www-form-urlencoded") return rejectedMutationResponse();
  try {
    const body = new URLSearchParams(await boundedEmailText(request.body, 256));
    const tenant = body.get("tenant") ?? "", site = body.get("site") ?? "";
    if (body.size !== 2 || !invitationFinishPath(`/invitations/finish?tenant=${tenant}&site=${site}`)) throw new Error();
    const identity = exactCookie(request.headers.get("cookie"), IDENTITY_COOKIE_NAME);
    if (identity === null) throw new Error();
    const selected = await selectSignalOrganization({ identityToken: identity, tenantId: tenant, dashboardOrigin: origin });
    if (selected.state !== "redirect") return invitationResponse(origin, selected);
    const token = exactCookie(selected.cookies.map(c => c.split(";", 1)[0]).join("; "), TENANT_COOKIE_NAME);
    if (token === null) throw new Error();
    const session = await loadDashboardSession({ sessionTokens: [token] });
    if (session.state !== "authenticated" || session.tenantId !== tenant) throw new Error();
    const result = await selectSignalSite({ tenantToken: token, siteId: site, expectedSessionVersion: session.sessionVersion, dashboardOrigin: origin });
    if (result.state !== "redirect") return invitationResponse(origin, result);
    return invitationResponse(origin, { state: "redirect", location: "/", cookies: selected.cookies });
  } catch { return invitationResponse(origin, { state: "rejected", cookies: [] }); }
}
