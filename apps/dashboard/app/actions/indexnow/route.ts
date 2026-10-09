import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { boundedBody } from "@/lib/docs-api";
import { createIndexNowKey } from "@/lib/indexnow-api";

export async function POST(request: Request): Promise<Response> {
  const failed = (status: number) => Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failed(503); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")) return failed(403);
  try {
    const value = JSON.parse(await boundedBody(request, 1024));
    if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== "request_id,site_id" || typeof value.site_id !== "string" || typeof value.request_id !== "string") return failed(403);
    return await createIndexNowKey(token, value.site_id, value.request_id, origin);
  } catch { return failed(503); }
}
