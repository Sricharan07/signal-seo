import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { prepareDashboardProposal } from "@/lib/proposal-api";

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
    return redirectToChat("request_rejected", dashboardOrigin);
  }
  const tenantToken = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  let siteId: string | null = null;
  try {
    const form = await strictForm(request, ["site_id"]);
    siteId = form.get("site_id");
  } catch {
    return redirectToChat("request_rejected", dashboardOrigin);
  }
  if (tenantToken === null || siteId === null || !UUID.test(siteId)) {
    return redirectToChat("request_rejected", dashboardOrigin);
  }
  const result = await prepareDashboardProposal({ tenantToken, siteId, dashboardOrigin });
  return redirectToChat(
    result.state === "available" ? "prepared" : result.state,
    dashboardOrigin,
  );
}

async function strictForm(request: Request, fields: readonly string[]): Promise<URLSearchParams> {
  if (
    request.headers.get("content-type")?.split(";", 1)[0]?.trim() !==
    "application/x-www-form-urlencoded"
  ) {
    throw new Error("Invalid form");
  }
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > 512) throw new Error("Invalid form");
  const form = new URLSearchParams(body);
  const actual = [...new Set(form.keys())].sort();
  if (
    actual.length !== fields.length ||
    !actual.every((field, index) => field === [...fields].sort()[index]) ||
    fields.some((field) => form.getAll(field).length !== 1)
  ) {
    throw new Error("Invalid form");
  }
  return form;
}

function redirectToChat(state: string, dashboardOrigin: string): Response {
  return new Response(null, {
    status: 303,
    headers: {
      "Cache-Control": "no-store",
      Location: new URL(`/chat?proposal=${encodeURIComponent(state)}`, dashboardOrigin).href,
    },
  });
}
