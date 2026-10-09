import {
  beginSignalLogin,
  dashboardMutationAccepted,
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
  const result = await beginSignalLogin();
  return authRelayResponse(dashboardOrigin, result, {
    not_ready: "identity-not-ready",
    rejected: "request-rejected",
    failed: "identity-not-ready",
  });
}
