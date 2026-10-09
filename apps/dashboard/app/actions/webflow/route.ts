import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";
import { boundedWebflowJson, relayWebflow, webflowReview } from "@/lib/webflow-api";

export async function GET(request: Request) {
  const token=exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  const query=new URL(request.url).searchParams;
  const site=query.get("site_id");
  if (!token || !uuid(site) || [...query.keys()].length!==1) return Response.json({state:"rejected"},{status:403});
  return relayWebflow(token,site,"");
}
export async function POST(request: Request) {
  let origin:string;
  try {origin=validatedDashboardOrigin();} catch {return Response.json({state:"unavailable"},{status:503});}
  const token=exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request,origin) || request.headers.get("content-type")?.split(";",1)[0]!=="application/json") return Response.json({state:"rejected"},{status:403});
  try {
    const command=webflowReview(await boundedWebflowJson(new Response(request.body),4096));
    if (!command) throw new Error();
    return relayWebflow(token,command.siteId,origin,command);
  } catch {return Response.json({state:"rejected"},{status:403});}
}
