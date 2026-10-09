import { TENANT_COOKIE_NAME, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { GSC_ATTEMPT_COOKIE } from "@/lib/owner-connector-route";
import { mutateOwnerConnector } from "@/lib/owner-connectors-api";
import { SLACK_UUID } from "@/lib/slack-api";

export async function GET(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return new Response(null, { status: 503 }); }
  const headers = new Headers({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Set-Cookie": `${GSC_ATTEMPT_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0` });
  let completed = false;
  try {
    const url = new URL(request.url);
    const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    const values = (request.headers.get("cookie") ?? "").split(";").map(value => value.trim()).filter(value => value.startsWith(GSC_ATTEMPT_COOKIE+"="));
    if (url.origin !== origin || values.length !== 1 || token === null ||
        url.searchParams.getAll("state").length !== 1 || url.searchParams.getAll("code").length !== 1 || url.searchParams.has("error")) throw new Error();
    const [siteId, attemptId, extra] = values[0].slice(GSC_ATTEMPT_COOKIE.length+1).split(".");
    const state = url.searchParams.get("state") ?? "", code = url.searchParams.get("code") ?? "";
    if (extra !== undefined || !SLACK_UUID.test(siteId ?? "") || !SLACK_UUID.test(attemptId ?? "") ||
        !/^[A-Za-z0-9_-]{43}$/.test(state) || !/^[\x21-\x7e]{1,2048}$/.test(code)) throw new Error();
    const result = await mutateOwnerConnector({ connector: "gsc", tenantToken: token, siteId, origin,
      command: { operation: "complete", attempt_id: attemptId, state, code } });
    completed = result !== null && result.state === "selecting" && result.attempt_id === attemptId;
  } catch { completed = false; }
  headers.set("Location", origin+"/connectors?gsc="+(completed ? "selecting" : "unavailable"));
  return new Response(null, { status: 303, headers });
}
