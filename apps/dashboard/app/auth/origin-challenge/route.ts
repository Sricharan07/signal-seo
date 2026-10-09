import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { issueDashboardOriginChallenge } from "@/lib/origin-verification-api";
import {
  originMutationResponse,
  readOriginMutation,
} from "@/lib/origin-verification-route";

const FIELDS = ["idempotency_key", "origin", "schema_version", "site_id"];

export async function POST(request: Request): Promise<Response> {
  let dashboardOrigin: string;
  try {
    dashboardOrigin = validatedDashboardOrigin();
  } catch {
    return originMutationResponse({ state: "not_ready" });
  }
  if (!dashboardMutationAccepted(request, dashboardOrigin)) {
    return originMutationResponse({ state: "rejected" });
  }
  const proposed = await readOriginMutation(request, FIELDS);
  const tenantToken = exactCookie(
    request.headers.get("cookie"),
    TENANT_COOKIE_NAME,
  );
  if (proposed === null || tenantToken === null) {
    return originMutationResponse({ state: "rejected" });
  }
  return originMutationResponse(
    await issueDashboardOriginChallenge({
      tenantToken,
      siteId: String(proposed.site_id),
      origin: String(proposed.origin),
      idempotencyKey: String(proposed.idempotency_key),
      dashboardOrigin,
    }),
  );
}
