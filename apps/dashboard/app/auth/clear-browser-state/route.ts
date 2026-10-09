import {
  clearBrowserAuthCookies,
  dashboardMutationAccepted,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import {
  localNoticeResponse,
  rejectedMutationResponse,
} from "@/lib/auth-route";

export async function POST(request: Request): Promise<Response> {
  let dashboardOrigin: string;
  try {
    dashboardOrigin = validatedDashboardOrigin();
  } catch {
    return rejectedMutationResponse();
  }
  if (!dashboardMutationAccepted(request, dashboardOrigin)) {
    return rejectedMutationResponse();
  }
  return localNoticeResponse(
    dashboardOrigin,
    "browser-cleared",
    clearBrowserAuthCookies(),
  );
}
