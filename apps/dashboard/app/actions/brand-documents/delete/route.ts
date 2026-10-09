import {
  TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { relayBrandDocuments, validBrandDocumentId } from "@/lib/brand-document-api";

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); }
  catch { return Response.json({ state: "not_ready" }, { status: 503 }); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!dashboardMutationAccepted(request, origin) || token === null ||
      request.headers.get("content-type")?.split(";", 1)[0] !== "application/json" ||
      Number(request.headers.get("content-length") ?? 0) > 256) {
    return Response.json({ state: "rejected" }, { status: 403 });
  }
  try {
    const raw = await request.text();
    if (raw.length > 256) throw new Error();
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error();
    const item = value as Record<string, unknown>;
    if (Object.keys(item).sort().join(",") !== "document_id,schema_version,site_id" ||
      item.schema_version !== 1 || !validBrandDocumentId(item.document_id) ||
      !validBrandDocumentId(item.site_id)) throw new Error();
    return relayBrandDocuments(token, item.site_id, origin, "DELETE", undefined, item.document_id);
  } catch {
    return Response.json({ state: "rejected" }, { status: 403 });
  }
}
