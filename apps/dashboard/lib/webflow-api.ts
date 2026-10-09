import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validBrandDocumentId as uuid } from "./brand-document-api";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

export interface WebflowData {
  schema_version: 1;
  capabilities: {create_draft: string; update: string; publish: string; refresh: string; production: string};
  bindings: {id: string; origin: string; provider_site: string; collection_id: string; field_mapping: Record<string,string>; schema_sha256: string; revoked_at: string | null}[];
  inbox: {id: string; binding_id: string; candidate_id: string; payload: {items: {isDraft: true; fieldData: Record<string,string>}[]}; revision_sha256: string; decision: string; state: string; provider_item: string | null}[];
}
const object = (v: unknown): v is Record<string,unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const digest = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const providerId = (v: unknown) => typeof v === "string" && /^[0-9a-f]{24}$/.test(v);
const keys = (v: Record<string,unknown>, allowed: string[]) => Object.keys(v).length === allowed.length && Object.keys(v).every(k => allowed.includes(k));

export function validWebflowData(v: unknown): v is WebflowData {
  if (!object(v) || !keys(v,["schema_version","capabilities","bindings","inbox"]) || v.schema_version !== 1 || !object(v.capabilities) || !keys(v.capabilities,["create_draft","update","publish","refresh","production"]) || v.capabilities.create_draft !== "DRAFT_ONLY" || v.capabilities.update !== "WEBFLOW_UPDATE_ATOMIC_PRECONDITION_UNAVAILABLE" || v.capabilities.publish !== "WEBFLOW_PUBLISH_ATOMIC_PRECONDITION_UNAVAILABLE" || v.capabilities.refresh !== "WEBFLOW_EXISTING_ITEM_REFRESH_UNAVAILABLE" || v.capabilities.production !== "WEBFLOW_LIVE_QUALIFICATION_NOT_EXECUTED" || ![v.bindings,v.inbox].every(a => Array.isArray(a) && a.length <= 100)) return false;
  return (v.bindings as unknown[]).every(b => object(b) && keys(b,["id","origin","provider_site","collection_id","field_mapping","schema_sha256","revoked_at"]) && uuid(b.id) && typeof b.origin === "string" && b.origin.startsWith("https://") && providerId(b.provider_site) && providerId(b.collection_id) && digest(b.schema_sha256) && (b.revoked_at === null || typeof b.revoked_at === "string" && !Number.isNaN(Date.parse(b.revoked_at))) && object(b.field_mapping) && keys(b.field_mapping,["title","description","body"]) && b.field_mapping.title === "name" && Object.values(b.field_mapping).every(s => typeof s === "string" && /^[a-z][a-z0-9-]{0,63}$/.test(s))) &&
    (v.inbox as unknown[]).every(r => object(r) && keys(r,["id","binding_id","candidate_id","payload","revision_sha256","decision","state","provider_item"]) && uuid(r.id) && uuid(r.binding_id) && uuid(r.candidate_id) && digest(r.revision_sha256) && ["pending","approved","rejected","changes_requested"].includes(String(r.decision)) && ["PLANNED","AUTHORIZED","OUTCOME_UNKNOWN","ESCALATED","DRAFT_RECORDED"].includes(String(r.state)) && (r.provider_item === null || providerId(r.provider_item)) && object(r.payload) && keys(r.payload,["items"]) && Array.isArray(r.payload.items) && r.payload.items.length === 1 && r.payload.items.every(i => object(i) && keys(i,["isDraft","fieldData"]) && i.isDraft === true && object(i.fieldData) && Object.keys(i.fieldData).length === 4 && typeof i.fieldData.slug === "string" && /^[a-z0-9-]{1,80}-signal-[0-9a-f]{32}$/.test(i.fieldData.slug) && Object.values(i.fieldData).every(s => typeof s === "string" && s.length <= 65536)));
}

export function webflowReview(v: unknown): {siteId: string; body: Record<string,unknown>} | null {
  if (!object(v) || !keys(v,["schema_version","site_id","id","revision_sha256","decision"]) || v.schema_version !== 1 || !uuid(v.site_id) || !uuid(v.id) || !digest(v.revision_sha256) || !["approved","rejected","changes_requested"].includes(String(v.decision))) return null;
  return {siteId:v.site_id,body:{schema_version:1,id:v.id,revision_sha256:v.revision_sha256,decision:v.decision}};
}

export async function boundedWebflowJson(response: Response, maximum: number): Promise<unknown> {
  return JSON.parse(await boundedRelayText(response, maximum));
}

export async function relayWebflow(token: string, siteId: string, origin: string, command?: ReturnType<typeof webflowReview>) {
  try {
    const base=validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL??"http://127.0.0.1:8000");
    const cookie=`${TENANT_COOKIE_NAME}=${token}`;let csrf="";
    if (command) {
      const r=await relayJson(new URL("/v1/session/tenant-csrf",base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(2500),headers:{Cookie:cookie,Accept:"application/json"}}, globalThis.fetch, 7000000);
      const proof=await boundedWebflowJson(r,4096);
      if (!r.ok || r.headers.getSetCookie().length || !object(proof) || proof.schema_version!==1 || typeof proof.csrf_token!=="string" || !/^[A-Za-z0-9_-]{43}$/.test(proof.csrf_token)) throw new Error();
      csrf=proof.csrf_token;
    }
    const r=await relayJson(new URL(`/v1/sites/${siteId}/webflow${command?"/review":""}`,base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(15000),method:command?"POST":"GET",headers:{Cookie:cookie,Accept:"application/json",...(command?{Origin:origin,"Sec-Fetch-Site":"same-origin","X-CSRF-Token":csrf,"Content-Type":"application/json"}:{})},...(command?{body:JSON.stringify(command.body)}:{})}, globalThis.fetch, 7000000);
    if (r.headers.getSetCookie().length) throw new Error();
    const result=await boundedWebflowJson(r,7_000_000);
    if (!r.ok) return Response.json({state:r.status===403?"rejected":"unavailable"},{status:r.status});
    if (command ? !object(result) || !keys(result,["schema_version","state"]) || result.schema_version!==1 || !["reviewed","replayed"].includes(String(result.state)) : !validWebflowData(result)) throw new Error();
    return Response.json(result,{headers:{"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
  } catch {return Response.json({state:"unavailable"},{status:503});}
}
