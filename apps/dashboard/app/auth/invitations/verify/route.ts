import { beginSignalLogin, dashboardMutationAccepted, validatedDashboardOrigin } from "@/lib/browser-auth";
import { rejectedMutationResponse } from "@/lib/auth-route";
import { boundedEmailText } from "@/lib/email-api";
import { invitationCredential, invitationResponse, linkCookie } from "@/lib/invitation-route";

export async function POST(request: Request): Promise<Response> {
  const origin = validatedDashboardOrigin();
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type")?.split(";", 1)[0] !== "application/x-www-form-urlencoded") return rejectedMutationResponse();
  try {
    const body = new URLSearchParams(await boundedEmailText(request.body, 256));
    const credential = body.size === 1 ? invitationCredential(body.get("credential")) : null;
    if (credential === null) throw new Error();
    const result = await beginSignalLogin({ invitation: true, returnPath: "/invitations/accept" });
    return invitationResponse(origin, result, result.state === "redirect" ? [linkCookie(credential)] : []);
  } catch { return invitationResponse(origin, { state: "rejected", cookies: [] }); }
}
