import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { validBrandDocumentId as uuid } from "@/lib/brand-document-api";
import { relayWriter, writerCommand } from "@/lib/content-writer-api";

export async function GET(request: Request) {
  const token = exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  const siteId = new URL(request.url).searchParams.get("site_id");
  if (!token || !uuid(siteId)) return Response.json({state:"rejected"},{status:403});
  return relayWriter(token,siteId,"");
}
export async function POST(request: Request) {
  let origin: string;
  try {origin=validatedDashboardOrigin();} catch {return Response.json({state:"unavailable"},{status:503});}
  const token=exactCookie(request.headers.get("cookie"),TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request,origin) || request.headers.get("content-type")?.split(";",1)[0]!=="application/json") return Response.json({state:"rejected"},{status:403});
  try {
    const reader=request.body?.getReader(); if (!reader) throw new Error();
    const chunks: Uint8Array[]=[]; let bytes=0;
    try {while (true) {const part=await reader.read(); if (part.done) break; bytes+=part.value.byteLength; if (bytes>20000) throw new Error(); chunks.push(part.value);}} finally {await reader.cancel();}
    const buffer=new Uint8Array(bytes);let offset=0; for (const chunk of chunks) {buffer.set(chunk,offset); offset+=chunk.byteLength;}
    const command=writerCommand(JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(buffer)));
    if (!command) throw new Error(); return relayWriter(token,command.siteId,origin,command);
  } catch {return Response.json({state:"rejected"},{status:403});}
}
