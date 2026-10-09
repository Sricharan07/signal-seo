import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";
import { relayVisibility, visibilityJson, visibilitySettings } from "@/lib/visibility-schedule-api";

export async function GET(request: Request) {
  const token=exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  const site=new URL(request.url).searchParams.get("site_id");
  if(!token||!uuid(site))return Response.json({state:"rejected"},{status:403});
  return relayVisibility(token,site,"");
}
export async function POST(request: Request) {
  let origin:string;
  try {origin=validatedDashboardOrigin();}catch{return Response.json({state:"unavailable"},{status:503});}
  const token=exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  if(!token||!dashboardMutationAccepted(request,origin)||request.headers.get("content-type")?.split(";",1)[0]!=="application/json")return Response.json({state:"rejected"},{status:403});
  try {const command=visibilitySettings(await visibilityJson(request.body,2048));if(!command)throw new Error();return relayVisibility(token,command.site,origin,command.body);}catch{return Response.json({state:"rejected"},{status:403});}
}
