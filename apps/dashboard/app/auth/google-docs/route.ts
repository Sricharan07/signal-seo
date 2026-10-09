import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { boundedBody, docsAuthorization, mutateDocs } from "@/lib/docs-api";
import { SLACK_UUID } from "@/lib/slack-api";

export async function POST(request: Request): Promise<Response> {
  const failed = (status: number) => Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failed(503); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json" || token === null) return failed(403);
  try {
    const data: unknown = JSON.parse(await boundedBody(request, 4096));
    if (typeof data !== "object" || data === null || Array.isArray(data)) return failed(403);
    const value = data as Record<string, unknown>;
    if (typeof value.site_id !== "string" || !SLACK_UUID.test(value.site_id) || !["connect", "sync", "disconnect"].includes(String(value.operation)) ||
      Object.keys(value).sort().join(",") !== (value.operation === "disconnect" ? "binding_id,operation,site_id" : "operation,site_id") ||
      value.operation === "disconnect" && (typeof value.binding_id !== "string" || !SLACK_UUID.test(value.binding_id))) return failed(403);
    const { site_id: siteId, ...command } = value;
    const result = await mutateDocs({ tenantToken: token, siteId: siteId as string, origin, command });
    if (result === null) return failed(503);
    const headers = new Headers({ "Cache-Control": "no-store" });
    if (value.operation === "connect") {
      const url = docsAuthorization(result.authorization_url, origin);
      if (url === null) return failed(503);
      headers.set("Set-Cookie", `__Host-signal-docs-attempt=${siteId}.${result.attempt_id}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600`);
      return Response.json({ authorization_url: url }, { headers });
    }
    return Response.json(result, { headers });
  } catch { return failed(403); }
}
