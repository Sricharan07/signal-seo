import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { TELEGRAM_UUID, mutateTelegram } from "@/lib/telegram-api";

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failed(503); }
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json") return failed(403);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (token === null) return failed(403);
  let body: Record<string, unknown>;
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > 4096) throw new Error();
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    body = parsed as Record<string, unknown>;
    if (typeof body.site_id !== "string" || !TELEGRAM_UUID.test(body.site_id) ||
        !["install", "link", "revoke", "request"].includes(String(body.operation))) throw new Error();
  } catch { return failed(403); }
  const { site_id: siteId, ...command } = body;
  const result = await mutateTelegram({ tenantToken: token, siteId: siteId as string, origin, command });
  return result === null ? failed(503) : Response.json(result, { headers: { "Cache-Control": "no-store" } });
}
function failed(status: number): Response {
  return Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
}
