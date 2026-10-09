import {
  OIDC_BINDING_COOKIE_NAME,
  completeSignalLogin,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import {
  authRelayResponse,
  identityServiceUnavailableResponse,
} from "@/lib/auth-route";

export async function GET(request: Request): Promise<Response> {
  let dashboardOrigin: string;
  try {
    dashboardOrigin = validatedDashboardOrigin();
  } catch {
    return identityServiceUnavailableResponse();
  }
  const url = new URL(request.url);
  const states = url.searchParams.getAll("state");
  const codes = url.searchParams.getAll("code");
  const result = await completeSignalLogin({
    state: states.length === 1 ? states[0] ?? "" : "",
    code: codes.length === 1 ? codes[0] ?? "" : "",
    browserBinding:
      exactCookie(request.headers.get("cookie"), OIDC_BINDING_COOKIE_NAME) ?? "",
  });
  return authRelayResponse(dashboardOrigin, result, {
    not_ready: "callback-failed",
    rejected: "callback-rejected",
    failed: "callback-failed",
  });
}
