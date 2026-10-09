import {
  dashboardMutationAccepted,
  logoutSignalSession,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import {
  authRelayResponse,
  identityServiceUnavailableResponse,
  rejectedMutationResponse,
} from "@/lib/auth-route";

export async function POST(request: Request): Promise<Response> {
  let dashboardOrigin: string;
  try {
    dashboardOrigin = validatedDashboardOrigin();
  } catch {
    return identityServiceUnavailableResponse();
  }
  if (!dashboardMutationAccepted(request, dashboardOrigin)) {
    return rejectedMutationResponse();
  }
  const result = await logoutSignalSession({
    cookieHeader: request.headers.get("cookie") ?? "",
    dashboardOrigin,
  });
  return authRelayResponse(dashboardOrigin, result, {
    not_ready: "logout-failed",
    rejected: "logout-rejected",
    failed: "logout-failed",
  });
}
