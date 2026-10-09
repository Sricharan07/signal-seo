import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "./browser-auth";
import { SLACK_UUID } from "./slack-api";
import { bingAuthorizationUrl, gscAuthorizationUrl, mutateOwnerConnector, type OwnerConnector } from "./owner-connectors-api";

export const GSC_ATTEMPT_COOKIE = "__Host-signal-gsc-attempt";
export const BING_ATTEMPT_COOKIE = "__Host-signal-bing-attempt";
export function failedConnector(status: number): Response {
  return Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
}

export async function ownerConnectorPost(request: Request, connector: OwnerConnector): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failedConnector(503); }
  if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json" ||
      request.headers.has("content-encoding")) return failedConnector(403);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (token === null) return failedConnector(403);
  let body: Record<string, unknown>;
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > 4096) throw new Error();
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    body = parsed as Record<string, unknown>;
    if (typeof body.site_id !== "string" || !SLACK_UUID.test(body.site_id) ||
        !(connector === "gsc" || connector === "bing" ? ["authorize", "confirm", "revoke"] : connector === "github" ? ["bind", "inspect", "accept_unprotected", "revoke"] : ["prepare", "finish", "revoke"]).includes(String(body.operation))) throw new Error();
  } catch { return failedConnector(403); }
  const { site_id: siteId, ...command } = body;
  const result = await mutateOwnerConnector({ connector, tenantToken: token, siteId: siteId as string, origin, command });
  if (result === null) return failedConnector(503);
  const headers = new Headers({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" });
  if (connector !== "github" && body.operation === "authorize") {
    const url = (connector === "bing" ? bingAuthorizationUrl : gscAuthorizationUrl)(result.authorization_url, origin);
    if (url === null || typeof result.attempt_id !== "string" || !SLACK_UUID.test(result.attempt_id)) return failedConnector(503);
    headers.set("Set-Cookie", `${connector === "bing" ? BING_ATTEMPT_COOKIE : GSC_ATTEMPT_COOKIE}=${siteId}.${result.attempt_id}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${connector === "bing" ? 300 : 600}`);
    return Response.json({ authorization_url: url }, { headers });
  }
  return Response.json(result, { headers });
}

export async function bingConnectorCallback(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return failedConnector(503); }
  const headers = new Headers({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Set-Cookie": `${BING_ATTEMPT_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0` });
  let completed = false;
  try {
    const url = new URL(request.url), token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    const values = (request.headers.get("cookie") ?? "").split(";").map(v => v.trim()).filter(v => v.startsWith(BING_ATTEMPT_COOKIE + "="));
    if (url.origin !== origin || token === null || values.length !== 1 ||
        [...url.searchParams.keys()].sort().join(",") !== "code,state") throw new Error();
    const [siteId, attemptId, extra] = values[0].slice(BING_ATTEMPT_COOKIE.length + 1).split(".");
    const state = url.searchParams.get("state") ?? "", code = url.searchParams.get("code") ?? "";
    if (extra !== undefined || !SLACK_UUID.test(siteId ?? "") || !SLACK_UUID.test(attemptId ?? "") ||
        !/^[A-Za-z0-9_-]{43}$/.test(state) || !/^[\x21-\x7e]{1,2048}$/.test(code)) throw new Error();
    const result = await mutateOwnerConnector({ connector: "bing", tenantToken: token, siteId, origin,
      command: { operation: "complete", attempt_id: attemptId, state, code } });
    completed = result !== null && result.state === "selecting" && result.attempt_id === attemptId;
  } catch { completed = false; }
  headers.set("Location", origin + "/connectors?bing=" + (completed ? "selecting" : "unavailable"));
  return new Response(null, { status: 303, headers });
}
