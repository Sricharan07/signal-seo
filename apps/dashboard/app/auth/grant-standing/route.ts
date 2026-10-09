import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { mutateStandingAuthorization } from "@/lib/standing-authorization-api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const FIELDS = [
  "ends_at", "excluded_paths", "recipe_ranges", "recovery_window_hours",
  "schema_version", "site_id", "starts_at", "thresholds",
  "weekly_spend_cents", "weekly_total_cap", "weekly_volume_caps",
];

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try {
    origin = validatedDashboardOrigin();
  } catch {
    return result("not_ready", 503);
  }
  if (!dashboardMutationAccepted(request, origin)) return result("rejected", 403);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (token === null || request.headers.get("content-type") !== "application/json") {
    return result("rejected", 403);
  }
  let body: Record<string, unknown>;
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > 8192) throw new Error();
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    body = parsed as Record<string, unknown>;
    if (Object.keys(body).sort().join(",") !== FIELDS.join(",") ||
        body.schema_version !== 1 || typeof body.site_id !== "string" ||
        !UUID.test(body.site_id)) throw new Error();
  } catch {
    return result("rejected", 403);
  }
  const siteId = body.site_id as string;
  delete body.site_id;
  const response = await mutateStandingAuthorization({
    tenantToken: token, siteId, body, dashboardOrigin: origin,
  });
  const status = response.state === "recorded" ? 201 :
    response.state === "rejected" ? 403 : response.state === "conflict" ? 409 :
    response.state === "not_ready" ? 503 : 500;
  return Response.json(response, { status, headers: { "Cache-Control": "no-store" } });
}

function result(state: "not_ready" | "rejected", status: number): Response {
  return Response.json({ state }, { status, headers: { "Cache-Control": "no-store" } });
}
