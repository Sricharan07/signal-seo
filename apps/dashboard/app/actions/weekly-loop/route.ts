import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { SLACK_UUID } from "@/lib/slack-api";
import { mutateWeeklyLoop } from "@/lib/weekly-loop-api";

export async function POST(request: Request): Promise<Response> {
  const failed = (status: number) => Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failed(503); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")) return failed(403);
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > 1024) return failed(403);
    const body = JSON.parse(raw);
    if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).sort().join(",") !== "operation,site_id" ||
        typeof body.site_id !== "string" || !SLACK_UUID.test(body.site_id) || !["pause", "resume"].includes(body.operation)) return failed(403);
    const result = await mutateWeeklyLoop({ tenantToken: token, siteId: body.site_id, operation: body.operation, origin });
    return result === null ? failed(503) : Response.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch { return failed(503); }
}
