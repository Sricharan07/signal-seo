import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { GA4_ATTEMPT_COOKIE, ga4Command, relayGa4 } from "@/lib/ga4-api";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";

const failed = (status: number) => Response.json({ state: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });

export async function GET(request: Request): Promise<Response> {
  try {
    const origin = validatedDashboardOrigin();
    const url = new URL(request.url);
    const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    const site = url.searchParams.get("site_id");
    if (url.origin !== origin || token === null || !uuid(site) || [...url.searchParams.keys()].join(",") !== "site_id") return failed(403);
    const result = await relayGa4(token, site as string, origin);
    return result ? Response.json(result, { headers: { "Cache-Control": "no-store" } }) : failed(503);
  } catch { return failed(503); }
}

export async function POST(request: Request): Promise<Response> {
  try {
    const origin = validatedDashboardOrigin();
    if (!dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json") return failed(403);
    const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
    if (token === null) return failed(403);
    const reader = request.body?.getReader();
    if (!reader) return failed(422);
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 4096) return failed(422);
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const parsed = ga4Command(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
    if (!parsed) return failed(422);
    const result = await relayGa4(token, parsed.site, origin, parsed.command);
    if (!result) return failed(503);
    const headers = new Headers({ "Cache-Control": "no-store" });
    if (parsed.command.operation === "connect") headers.set("Set-Cookie",
      `${GA4_ATTEMPT_COOKIE}=${parsed.site}.${result.attempt_id}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600`);
    return Response.json(result, { headers });
  } catch { return failed(503); }
}
