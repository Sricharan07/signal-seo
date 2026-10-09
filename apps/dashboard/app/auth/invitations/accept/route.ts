import { acceptSignalInvitation, dashboardMutationAccepted, exactCookie, INVITATION_IDENTITY_COOKIE_NAME, validatedDashboardOrigin } from "@/lib/browser-auth";
import { rejectedMutationResponse } from "@/lib/auth-route";
import { boundedEmailText } from "@/lib/email-api";
import { invitationResponse, linkCookie, readLinkCookie } from "@/lib/invitation-route";

export async function POST(request: Request): Promise<Response> {
  const origin = validatedDashboardOrigin();
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type")?.split(";", 1)[0] !== "application/x-www-form-urlencoded") return rejectedMutationResponse();
  const credential = readLinkCookie(request.headers.get("cookie"));
  const proof = exactCookie(request.headers.get("cookie"), INVITATION_IDENTITY_COOKIE_NAME);
  if (credential === null || proof === null) return invitationResponse(origin, { state: "rejected", cookies: [] });
  try {
    const body = new URLSearchParams(await boundedEmailText(request.body, 2048));
    if (body.size !== 1 || !body.has("display_name")) throw new Error();
    const displayName = (body.get("display_name") ?? "").trim();
    if (displayName.length === 0 || displayName.length > 200 || /[\u0000-\u001f\u007f]/.test(displayName))
      return invitationResponse(origin, { state: "redirect", location: "/invitations/accept?notice=invalid-name", cookies: [] });
    const [invitationId, token] = credential.split(".");
    const result = await acceptSignalInvitation({ identityProof: proof, invitationId, token,
      displayName, dashboardOrigin: origin });
    return invitationResponse(origin, result, result.state === "redirect" ? [linkCookie(null)] : []);
  } catch { return invitationResponse(origin, { state: "rejected", cookies: [] }); }
}
