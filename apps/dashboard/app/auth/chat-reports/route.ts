import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { boundedEmailText } from "@/lib/email-api";
import { CHAT_CHANNELS, mutateChatReports, type ChatChannel } from "@/lib/chat-reports-api";

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failed(503); }
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json") return failed(403);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (token === null) return failed(403);
  let body: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(await boundedEmailText(request.body, 1024));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    body = parsed as Record<string, unknown>;
    if (Object.keys(body).sort().join(",") !== "channel,enabled,site_id" || typeof body.site_id !== "string" ||
      typeof body.enabled !== "boolean" || !CHAT_CHANNELS.includes(body.channel as ChatChannel)) throw new Error();
  } catch { return failed(403); }
  const state = await mutateChatReports({ tenantToken: token, siteId: body.site_id as string, origin,
    channel: body.channel as ChatChannel, enabled: body.enabled as boolean });
  return state === null ? failed(503) : Response.json({ state }, { headers: { "Cache-Control": "no-store" } });
}
function failed(status: number): Response {
  return Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
}
