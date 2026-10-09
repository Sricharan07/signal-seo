import { TENANT_COOKIE_NAME, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { mutateDocs } from "@/lib/docs-api";
import { SLACK_UUID } from "@/lib/slack-api";

export async function GET(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return new Response(null, { status: 503 }); }
  const headers = new Headers({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Set-Cookie": "__Host-signal-docs-attempt=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0" });
  let completed = false;
  try {
    const url = new URL(request.url), token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    const cookieHeader = request.headers.get("cookie") ?? "";
    const attempts = cookieHeader.length <= 16384 ? cookieHeader.split(";").map(x => x.trim())
      .filter(x => x.startsWith("__Host-signal-docs-attempt=")).map(x => x.slice("__Host-signal-docs-attempt=".length)) : [];
    const cookie = attempts.length === 1 ? attempts[0] : null;
    if (url.origin !== origin || token === null || cookie === null || url.searchParams.has("error") ||
      ["state", "code", "picked_file_ids", "scope"].some(key => url.searchParams.getAll(key).length !== 1) ||
      url.searchParams.get("scope") !== "https://www.googleapis.com/auth/drive.file") throw new Error();
    const [siteId, attemptId, extra] = cookie.split("."), state = url.searchParams.get("state") ?? "", code = url.searchParams.get("code") ?? "";
    const files = url.searchParams.get("picked_file_ids") ?? "";
    if (extra !== undefined || !SLACK_UUID.test(siteId ?? "") || !SLACK_UUID.test(attemptId ?? "") || !/^[A-Za-z0-9_-]{43}$/.test(state) ||
      !/^[!-~]{16,4096}$/.test(code) || files.length > 4020 || files.split(",").length > 20 ||
      files.split(",").some(x => !/^[A-Za-z0-9_-]{10,200}$/.test(x)) || new Set(files.split(",")).size !== files.split(",").length) throw new Error();
    const result = await mutateDocs({ tenantToken: token, siteId, origin, command: {
      operation: "complete", attempt_id: attemptId, state, code, picked_file_ids: files } });
    completed = result !== null;
  } catch { completed = false; }
  headers.set("Location", origin+"/connectors?google-docs="+(completed ? "bound" : "unavailable"));
  return new Response(null, { status: 303, headers });
}
