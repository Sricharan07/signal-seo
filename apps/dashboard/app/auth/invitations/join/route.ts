import { beginSignalLogin, dashboardMutationAccepted, invitationFinishPath, validatedDashboardOrigin } from "@/lib/browser-auth";
import { rejectedMutationResponse } from "@/lib/auth-route";
import { boundedEmailText } from "@/lib/email-api";
import { invitationResponse } from "@/lib/invitation-route";

export async function POST(request: Request): Promise<Response> {
  const origin = validatedDashboardOrigin();
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type")?.split(";", 1)[0] !== "application/x-www-form-urlencoded") return rejectedMutationResponse();
  try {
    const body = new URLSearchParams(await boundedEmailText(request.body, 256));
    const path = `/invitations/finish?tenant=${body.get("tenant")}&site=${body.get("site")}`;
    if (body.size !== 2 || !invitationFinishPath(path)) throw new Error();
    return invitationResponse(origin, await beginSignalLogin({ returnPath: path }));
  } catch { return invitationResponse(origin, { state: "rejected", cookies: [] }); }
}
