import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { startDashboardSnapshot } from "@/lib/work-api";

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
    return redirectToWork("rejected", dashboardOrigin);
  }
  const tenantToken = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  let siteId: string | null = null;
  try {
    const form = await request.formData();
    siteId = String(form.get("site_id") ?? "");
  } catch {
    return redirectToWork("rejected", dashboardOrigin);
  }
  if (tenantToken === null || !UUID.test(siteId)) {
    return redirectToWork("rejected", dashboardOrigin);
  }
  const result = await startDashboardSnapshot({
    tenantToken,
    siteId,
    dashboardOrigin,
  });
  return redirectToWork(
    result.state === "available" ? "started" : result.state,
    dashboardOrigin,
  );
}

function redirectToWork(state: string, dashboardOrigin: string): Response {
  return new Response(null, {
    status: 303,
    headers: {
      "Cache-Control": "no-store",
      Location: new URL(`/work?run=${encodeURIComponent(state)}`, dashboardOrigin).href,
    },
  });
}
