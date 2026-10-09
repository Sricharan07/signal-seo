import {
  IDENTITY_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  selectSignalOrganization,
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
  const tenantId = await selectedTenantId(request);
  const identityToken = exactCookie(
    request.headers.get("cookie"),
    IDENTITY_COOKIE_NAME,
  );
  if (tenantId === null || identityToken === null) {
    return localNoticeResponse(dashboardOrigin, "organization-rejected");
  }
  const result = await selectSignalOrganization({
    identityToken,
    tenantId,
    dashboardOrigin,
  });
  return authRelayResponse(dashboardOrigin, result, {
    not_ready: "organization-failed",
    rejected: "organization-rejected",
    failed: "organization-failed",
  });
}

async function selectedTenantId(request: Request): Promise<string | null> {
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
      [...form.keys()].some((key) => key !== "tenant_id") ||
      form.getAll("tenant_id").length !== 1
    ) {
      return null;
    }
    return form.get("tenant_id");
  } catch {
    return null;
  }
}
