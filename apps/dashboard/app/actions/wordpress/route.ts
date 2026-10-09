import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";
import { boundedJson } from "@/lib/content-writer-api";
import { relayWordPress, wordpressCommand } from "@/lib/wordpress-api";

export async function GET(request: Request) {
  const token=exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  const siteId=new URL(request.url).searchParams.get("site_id");
  if (!token || !uuid(siteId)) return Response.json({state:"rejected"},{status:403});
  return relayWordPress(token,siteId,"");
}
export async function POST(request: Request) {
  let origin: string;
  try {origin=validatedDashboardOrigin();} catch {return Response.json({state:"unavailable"},{status:503});}
  const token=exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request,origin) || request.headers.get("content-type")?.split(";",1)[0]!=="application/json") return Response.json({state:"rejected"},{status:403});
  try {
    const body=await boundedJson(new Response(request.body),4096);
    const command=wordpressCommand(body);
    if (!command) throw new Error();
    return relayWordPress(token,command.siteId,origin,command);
  } catch {return Response.json({state:"rejected"},{status:403});}
}
