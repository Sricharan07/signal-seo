import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { boundedWebflowJson } from "@/lib/webflow-api";
import { relayWebflowOwner, webflowOwnerCommand } from "@/lib/webflow-owner-api";

export async function POST(request: Request): Promise<Response> {
  const failed = (status: number) => Response.json({state: "unavailable"}, {status, headers: {"Cache-Control": "no-store"}});
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failed(503); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json") return failed(403);
  try {
    const command = webflowOwnerCommand(await boundedWebflowJson(new Response(request.body), 4096));
    // Completion is accepted only by the one-time server callback, not browser JSON.
    if (!command || command.operation === "complete") return failed(403);
    const response = await relayWebflowOwner(token, command.siteId, origin, command.operation, command.body);
    if (!response.ok || command.operation !== "begin") return response;
    const result = await response.json();
    const mapping = command.mapping!;
    return Response.json({authorization_url: result.authorization_url}, {headers: {"Cache-Control": "no-store", "Set-Cookie":
      `__Host-signal-webflow-attempt=${command.siteId}.${result.attempt_id}.${mapping.description}.${mapping.body}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600`}});
  } catch { return failed(403); }
}
