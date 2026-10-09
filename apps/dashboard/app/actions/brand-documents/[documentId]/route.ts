import { TENANT_COOKIE_NAME, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { relayBrandDocuments, validBrandDocumentId } from "@/lib/brand-document-api";

export async function GET(
  request: Request, { params }: { params: Promise<{ documentId: string }> },
): Promise<Response> {
  const { documentId } = await params;
  const query = new URL(request.url).searchParams;
  const siteId = query.get("site_id");
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (query.size !== 1 || !validBrandDocumentId(siteId) ||
      !validBrandDocumentId(documentId) || token === null) {
    return Response.json({ state: "rejected" }, { status: 403 });
  }
  try {
    return relayBrandDocuments(token, siteId, validatedDashboardOrigin(), "GET", undefined, documentId);
  } catch {
    return Response.json({ state: "not_ready" }, { status: 503 });
  }
}
