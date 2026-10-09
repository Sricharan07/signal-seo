import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { assistantCommand, assistantResource, relayAssistant } from "@/lib/assistant-api";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  const siteId = url.searchParams.get("site_id");
  const read = assistantResource(url.searchParams.get("resource"), url.searchParams.get("conversation_id"));
  if (!token || !uuid(siteId) || !read) return Response.json({ state: "rejected" }, { status: 403 });
  return relayAssistant(token, siteId, "", read);
}

export async function POST(request: Request): Promise<Response> {
  let origin: string;
  try { origin = validatedDashboardOrigin(); }
  catch { return Response.json({ state: "unavailable" }, { status: 503 }); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request, origin) || request.headers.get("content-type")?.split(";", 1)[0] !== "application/json" || Number(request.headers.get("content-length") ?? 0) > 12000) return Response.json({ state: "rejected" }, { status: 403 });
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > 12000) throw new Error();
    const command = assistantCommand(JSON.parse(raw));
    if (!command) throw new Error();
    return relayAssistant(token, command.siteId, origin, null, command);
  } catch { return Response.json({ state: "rejected" }, { status: 403 }); }
}
