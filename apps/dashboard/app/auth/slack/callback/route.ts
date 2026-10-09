import { TENANT_COOKIE_NAME, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { SLACK_UUID, mutateSlack } from "@/lib/slack-api";

const ATTEMPT_COOKIE = "__Host-signal-slack-attempt";
export async function GET(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return new Response(null, { status: 503 }); }
  const headers = new Headers({ "Cache-Control": "no-store",
    "Set-Cookie": `${ATTEMPT_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0` });
  let completed = false;
  try {
    const url = new URL(request.url);
    const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    // This cookie is a correlation identifier, not an authority bearer.
    const values = (request.headers.get("cookie") ?? "").split(";").map(value => value.trim()).filter(value => value.startsWith(ATTEMPT_COOKIE+"="));
    if (url.origin !== origin || values.length !== 1 || token === null ||
        url.searchParams.getAll("state").length !== 1 || url.searchParams.getAll("code").length !== 1 || url.searchParams.has("error")) throw new Error();
    const [siteId, attemptId, extra] = values[0].slice(ATTEMPT_COOKIE.length+1).split(".");
    const state = url.searchParams.get("state") ?? "", code = url.searchParams.get("code") ?? "";
    if (extra !== undefined || !SLACK_UUID.test(siteId ?? "") || !SLACK_UUID.test(attemptId ?? "") ||
        !/^[A-Za-z0-9_-]{43}$/.test(state) || code.length < 1 || code.length > 2048) throw new Error();
    const result = await mutateSlack({ tenantToken: token, siteId, origin,
      command: { operation: "complete", attempt_id: attemptId, state, code } });
    completed = result !== null && typeof result.binding_id === "string" && SLACK_UUID.test(result.binding_id);
  } catch { completed = false; }
  headers.set("Location", origin+"/connectors?slack="+(completed ? "bound" : "unavailable"));
  return new Response(null, { status: 303, headers });
}
