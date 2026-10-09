import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { boundedEmailText, mutateEmail } from "@/lib/email-api";

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failed(503); }
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json") return failed(403);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (token === null) return failed(403);
  let body: Record<string, unknown>;
  try {
    const raw = await boundedEmailText(request.body, 1024);
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    body = parsed as Record<string, unknown>;
    if (Object.keys(body).sort().join(",") !== "address,enabled,site_id" || typeof body.site_id !== "string" ||
        typeof body.enabled !== "boolean" || !(body.address === null || typeof body.address === "string")) throw new Error();
  } catch { return failed(403); }
  const state = await mutateEmail({ tenantToken: token, siteId: body.site_id as string, origin,
    enabled: body.enabled as boolean, address: body.address as string | null });
  return state === null ? failed(503) : Response.json({ state }, { headers: { "Cache-Control": "no-store" } });
}

function failed(status: number): Response {
  return Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
}
