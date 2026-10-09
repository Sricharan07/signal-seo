import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  selectSignalSite,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import {
  authRelayResponse,
  identityServiceUnavailableResponse,
  localNoticeResponse,
  rejectedMutationResponse,
} from "@/lib/auth-route";

const MAX_SELECTION_BYTES = 512;

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
  const selection = await selectedSite(request);
  const tenantToken = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (selection === null || tenantToken === null) {
    return localNoticeResponse(dashboardOrigin, "site-rejected");
  }
  const result = await selectSignalSite({
    tenantToken,
    siteId: selection.siteId,
    expectedSessionVersion: selection.sessionVersion,
    dashboardOrigin,
  });
  return authRelayResponse(dashboardOrigin, result, {
    not_ready: "site-failed",
    rejected: "site-rejected",
    conflict: "site-conflict",
    failed: "site-failed",
  });
}

async function selectedSite(
  request: Request,
): Promise<{ siteId: string; sessionVersion: number } | null> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim();
  const declaredLength = request.headers.get("content-length");
  if (
    contentType !== "application/x-www-form-urlencoded" ||
    (declaredLength !== null &&
      (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_SELECTION_BYTES))
  ) {
    return null;
  }
  try {
    const body = await request.text();
    if (new TextEncoder().encode(body).byteLength > MAX_SELECTION_BYTES) return null;
    const form = new URLSearchParams(body);
    if (
      [...form.keys()].some((key) => key !== "site_id" && key !== "session_version") ||
      form.getAll("site_id").length !== 1 ||
      form.getAll("session_version").length !== 1
    ) {
      return null;
    }
    const siteId = form.get("site_id");
    const rawSessionVersion = form.get("session_version");
    if (
      siteId === null ||
      rawSessionVersion === null ||
      !/^[1-9]\d{0,15}$/.test(rawSessionVersion)
    ) {
      return null;
    }
    const sessionVersion = Number(rawSessionVersion);
    return Number.isSafeInteger(sessionVersion) ? { siteId, sessionVersion } : null;
  } catch {
    return null;
  }
}
