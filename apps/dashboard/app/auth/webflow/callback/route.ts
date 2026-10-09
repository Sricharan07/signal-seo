import { TENANT_COOKIE_NAME, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { relayWebflowOwner, webflowOwnerCommand } from "@/lib/webflow-owner-api";

export async function GET(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return new Response(null, {status: 503}); }
  const headers = new Headers({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Set-Cookie": "__Host-signal-webflow-attempt=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0"});
  let completed = false;
  try {
    const u = new URL(request.url);
    const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    const raw = request.headers.get("cookie") ?? "";
    const attempts = raw.length <= 16384 ? raw.split(";").map(x => x.trim()).filter(x => x.startsWith("__Host-signal-webflow-attempt=")).map(x => x.slice("__Host-signal-webflow-attempt=".length)) : [];
    if (!token || u.origin !== origin || attempts.length !== 1 || [...u.searchParams.keys()].sort().join(",") !== "code,state") throw new Error();
    const [site_id, attempt_id, description, body, extra] = attempts[0]!.split(".");
    if (extra !== undefined) throw new Error();
    const command = webflowOwnerCommand({schema_version: 1, site_id, operation: "complete", attempt_id, state: u.searchParams.get("state"), code: u.searchParams.get("code"), field_mapping: {title: "name", description, body}});
    if (!command) throw new Error();
    completed = (await relayWebflowOwner(token, command.siteId, origin, command.operation, command.body)).ok;
  } catch { completed = false; }
  headers.set("Location", origin + "/connectors?webflow=" + (completed ? "bound" : "unavailable"));
  return new Response(null, {status: 303, headers});
}
