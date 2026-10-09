import { TENANT_COOKIE_NAME, exactCookie } from "@/lib/browser-auth";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";
import { relayPageSpeed } from "@/lib/pagespeed-api";

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  const siteId = url.searchParams.get("site_id");
  if (!token || !uuid(siteId) || [...url.searchParams.keys()].length !== 1) return Response.json({ state: "rejected" }, { status: 403, headers: { "Cache-Control": "no-store" } });
  return relayPageSpeed(token, siteId);
}
