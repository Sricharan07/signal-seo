import { TENANT_COOKIE_NAME, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { GA4_ATTEMPT_COOKIE, GA4_SCOPE, relayGa4 } from "@/lib/ga4-api";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";

export async function GET(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return new Response(null, { status: 503 }); }
  const headers = new Headers({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Set-Cookie": `${GA4_ATTEMPT_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0` });
  try {
    const url = new URL(request.url);
    const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    const values = (request.headers.get("cookie") ?? "").split(";").map(v => v.trim()).filter(v => v.startsWith(GA4_ATTEMPT_COOKIE + "="));
    const [site, attempt, extra] = (values[0] ?? "").slice(GA4_ATTEMPT_COOKIE.length + 1).split(".");
    if (url.origin !== origin || values.length !== 1 || token === null || !uuid(site) || !uuid(attempt) || extra !== undefined ||
        url.searchParams.getAll("code").length !== 1 || url.searchParams.getAll("state").length !== 1 ||
        [...url.searchParams.keys()].some(key => !["code", "state", "scope", "authuser", "prompt"].includes(key) || url.searchParams.getAll(key).length !== 1) ||
        (url.searchParams.has("scope") && url.searchParams.get("scope") !== GA4_SCOPE) ||
        (url.searchParams.has("authuser") && !/^[0-9]{1,3}$/.test(url.searchParams.get("authuser") ?? "")) ||
        (url.searchParams.has("prompt") && url.searchParams.get("prompt") !== "consent") ||
        !/^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get("state") ?? "") ||
        !/^[!-~]{16,4096}$/.test(url.searchParams.get("code") ?? "")) throw new Error();
    await relayGa4(token, site, origin, { operation: "complete", attempt_id: attempt,
      state: url.searchParams.get("state"), code: url.searchParams.get("code") });
  } catch { /* The connector projection reports whether selection was staged. */ }
  headers.set("Location", origin + "/connectors");
  return new Response(null, { status: 303, headers });
}
