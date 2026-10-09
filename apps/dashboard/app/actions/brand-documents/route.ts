import {
  TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin,
} from "@/lib/browser-auth";
import { relayBrandDocuments, validBrandDocumentId } from "@/lib/brand-document-api";

const MAX_BODY = 2_800_000;

export async function GET(request: Request): Promise<Response> {
  const query = new URL(request.url).searchParams;
  const siteId = query.get("site_id");
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (query.size !== 1 || !validBrandDocumentId(siteId) || token === null) {
    return Response.json({ state: "rejected" }, { status: 403 });
  }
  try {
    return relayBrandDocuments(token, siteId, validatedDashboardOrigin(), "GET");
  } catch {
    return Response.json({ state: "not_ready" }, { status: 503 });
  }
}

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); }
  catch { return Response.json({ state: "not_ready" }, { status: 503 }); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  const contentType = request.headers.get("content-type")?.split(";", 1)[0];
  const length = request.headers.get("content-length");
  if (!dashboardMutationAccepted(request, origin) || token === null ||
      contentType !== "application/json" ||
      (length !== null && (!/^\d+$/.test(length) || Number(length) > MAX_BODY))) {
    return Response.json({ state: "rejected" }, { status: 403 });
  }
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > MAX_BODY) throw new Error();
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error();
    const item = value as Record<string, unknown>;
    if (Object.keys(item).sort().join(",") !==
      "content_base64,filename,schema_version,site_id,supersedes_id" ||
      item.schema_version !== 1 || !validBrandDocumentId(item.site_id) ||
      typeof item.filename !== "string" || item.filename.length > 120 ||
      typeof item.content_base64 !== "string" || item.content_base64.length > 2_796_204 ||
      (item.supersedes_id !== null && !validBrandDocumentId(item.supersedes_id))) throw new Error();
    const { site_id: siteId, ...body } = item;
    return relayBrandDocuments(token, siteId as string, origin, "POST", body);
  } catch {
    return Response.json({ state: "rejected" }, { status: 403 });
  }
}
