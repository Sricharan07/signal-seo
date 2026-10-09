import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";
import { readStrategyJson, relayStrategy, strategyCommand } from "@/lib/seo-strategy-api";

export async function GET(request: Request): Promise<Response> {
  const u = new URL(request.url), token = exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME), site = u.searchParams.get("site_id");
  if (!token || !uuid(site) || [...u.searchParams.keys()].some(k => !["site_id","snapshot_id","evidence_id"].includes(k))) return Response.json({state:"rejected"},{status:403});
  return relayStrategy(token,site,"",undefined,u.searchParams.get("snapshot_id"),u.searchParams.get("evidence_id"));
}
export async function POST(request: Request): Promise<Response> {
  let origin: string; try { origin = validatedDashboardOrigin(); } catch { return Response.json({state:"unavailable"},{status:503}); }
  const token = exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request,origin) || request.headers.get("content-type")?.split(";",1)[0] !== "application/json" || Number(request.headers.get("content-length") ?? 0) > 2048) return Response.json({state:"rejected"},{status:403});
  try {
    const command = strategyCommand(await readStrategyJson(request,2048)); if (!command) throw new Error();
    return relayStrategy(token,command.siteId,origin,command);
  } catch { return Response.json({state:"rejected"},{status:403}); }
}
