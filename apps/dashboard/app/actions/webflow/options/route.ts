import { TENANT_COOKIE_NAME, exactCookie } from "@/lib/browser-auth";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";
import { relayWebflowOwner } from "@/lib/webflow-owner-api";

export async function GET(request: Request): Promise<Response> {
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  const q = new URL(request.url).searchParams;
  const site = q.get("site_id");
  if (!token || !uuid(site) || [...q.keys()].join(",") !== "site_id") return Response.json({state: "unavailable"}, {status: 403});
  return relayWebflowOwner(token, site, "");
}
