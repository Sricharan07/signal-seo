import { randomUUID } from "node:crypto";

import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { analyzeDashboardVerifiedHomepage } from "@/lib/finding-api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export async function POST(request: Request): Promise<Response> {
  let dashboardOrigin: string;
  try {
    dashboardOrigin = validatedDashboardOrigin();
  } catch {
    return Response.json(
      { error: { code: "DASHBOARD_NOT_READY", message: "The dashboard is not ready." } },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (!dashboardMutationAccepted(request, dashboardOrigin)) {
    return redirectToPages("rejected", dashboardOrigin);
  }
  const tenantToken = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  let siteId: string;
  try {
    const form = await request.formData();
    siteId = String(form.get("site_id") ?? "");
  } catch {
    return redirectToPages("rejected", dashboardOrigin);
  }
  if (tenantToken === null || !UUID.test(siteId)) {
    return redirectToPages("rejected", dashboardOrigin);
  }
  const result = await analyzeDashboardVerifiedHomepage({
    tenantToken,
    siteId,
    idempotencyKey: randomUUID(),
    dashboardOrigin,
  });
  return redirectToPages(
    result.state === "available" ? "observed" : result.state,
    dashboardOrigin,
  );
}

function redirectToPages(state: string, dashboardOrigin: string): Response {
  return new Response(null, {
    status: 303,
    headers: {
      "Cache-Control": "no-store",
      Location: new URL(`/pages?analysis=${encodeURIComponent(state)}`, dashboardOrigin).href,
    },
  });
}
