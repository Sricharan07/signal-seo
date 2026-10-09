import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { brainCommand, relayBrain } from "@/lib/business-brain-api";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  const siteId = url.searchParams.get("site_id");
  const resource = url.searchParams.get("resource");
  const factId = url.searchParams.get("fact_id");
  if (!token || !uuid(siteId) || !resource || !["facts", "voice", "extraction", "provenance"].includes(resource) || (resource === "provenance" && !uuid(factId))) return Response.json({ state: "rejected" }, { status: 403 });
  return relayBrain(token, siteId, "", resource === "provenance" ? `facts/${factId}/provenance` : resource);
}
export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); }
  catch { return Response.json({ state: "unavailable" }, { status: 503 }); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request, origin) || request.headers.get("content-type")?.split(";",1)[0] !== "application/json" || Number(request.headers.get("content-length") ?? 0) > 20000) return Response.json({ state: "rejected" }, { status: 403 });
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > 20000) throw new Error();
    const command = brainCommand(JSON.parse(raw));
    if (!command) throw new Error();
    return relayBrain(token,command.siteId,origin,command.path,command);
  } catch { return Response.json({ state: "rejected" }, { status: 403 }); }
}
