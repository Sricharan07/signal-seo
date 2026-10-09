import { relayJson } from "./relay-json";
import { validBrandDocumentId as uuid } from "./brand-document-api";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { boundedJson } from "./content-writer-api";

export const UPDATES_REASON = "WordPress core REST has no atomic preconditions; needs a certified bridge";
export const PUBLISH_REASON = "Publish in WordPress yourself; Signal cannot publish an exact revision safely via core REST";
export const QUARANTINE_REASON = "The request may still create a draft. Signal will only look for it; it will not send it again.";
export interface WordPressData {
  schema_version: 1;
  provider_state: "available" | "unavailable";
  provider_reason: string | null;
  updates_reason: string;
  publish_reason: string;
  quarantine_reason: string;
  bindings: {id: string; origin: string; user_id: number; observed: {id: number; roles: string[]; capabilities: Record<string, boolean>}; current: boolean}[];
  drafts?: {draft_id: string; title: string}[];
  candidates: {id: string; draft_id: string; binding_id: string; revision_sha256: string; decision: "pending" | "approved" | "rejected"; payload: {status: "draft"; title: string; content: string; excerpt: string}}[];
  intents: {id: string; candidate_id: string; marker: string; state: string; post_id: number | null; post_url: string | null; prior_intent_id: string | null; reconcile_count: number; next_read_at: string; observation: string | null}[];
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const keys = (v: Record<string, unknown>, allowed: string[]) => Object.keys(v).every(k => allowed.includes(k));
const text = (v: unknown, maximum: number) => typeof v === "string" && v.length <= maximum && !v.includes("\0");
const digest = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const states = ["queued", "dispatching", "retry", "recorded", "outcome_unknown", "escalated", "failed"];
function origin(v: unknown): boolean {
  try {const u = new URL(String(v));return u.protocol === "https:" && !u.username && !u.password && u.origin === v;} catch {return false;}
}
function publicLink(v: unknown, origins: unknown[], postId: unknown): boolean {
  try {const u = new URL(String(v));return origins.includes(u.origin) && u.protocol === "https:" && !u.username && !u.password && (!u.search || u.search===`?p=${postId}`) && !u.hash;} catch {return false;}
}
export function validWordPressData(v: unknown): v is WordPressData {
  if (!object(v) || !keys(v,["schema_version","provider_state","provider_reason","updates_reason","publish_reason","quarantine_reason","bindings","candidates","intents","drafts"]) || v.schema_version !== 1 || !["available","unavailable"].includes(String(v.provider_state)) || !(v.provider_reason === null || v.provider_reason === "WORDPRESS_UNCONFIGURED") || v.updates_reason !== UPDATES_REASON || v.publish_reason !== PUBLISH_REASON || v.quarantine_reason !== QUARANTINE_REASON) return false;
  if (v.drafts !== undefined && (!Array.isArray(v.drafts) || v.drafts.length > 100 || !v.drafts.every(d => object(d) && Object.keys(d).length === 2 && keys(d,["draft_id","title"]) && uuid(d.draft_id) && text(d.title,60000)))) return false;
  if (![v.bindings,v.candidates,v.intents].every(a => Array.isArray(a) && a.length <= 100)) return false;
  if (!(v.bindings as unknown[]).every(b => object(b) && keys(b,["id","origin","user_id","observed","current"]) && uuid(b.id) && origin(b.origin) && Number.isSafeInteger(b.user_id) && Number(b.user_id)>0 && typeof b.current === "boolean" && object(b.observed) && keys(b.observed,["id","roles","capabilities"]) && b.observed.id===b.user_id && Array.isArray(b.observed.roles) && b.observed.roles.length===1 && b.observed.roles.every(r=>text(r,64)) && object(b.observed.capabilities) && Object.keys(b.observed.capabilities).length<=64 && Object.values(b.observed.capabilities).every(c=>typeof c === "boolean"))) return false;
  if (!(v.candidates as unknown[]).every(c => object(c) && keys(c,["id","draft_id","binding_id","revision_sha256","decision","payload"]) && uuid(c.id) && uuid(c.draft_id) && uuid(c.binding_id) && digest(c.revision_sha256) && ["pending","approved","rejected"].includes(String(c.decision)) && object(c.payload) && keys(c.payload,["status","title","content","excerpt"]) && c.payload.status === "draft" && text(c.payload.title,60000) && text(c.payload.content,60000) && text(c.payload.excerpt,60000))) return false;
  const origins = (v.bindings as WordPressData["bindings"]).map(b=>b.origin);
  return (v.intents as unknown[]).every(i => object(i) && keys(i,["id","candidate_id","marker","state","post_id","post_url","prior_intent_id","reconcile_count","next_read_at","observation"]) && uuid(i.id) && uuid(i.candidate_id) && i.marker===`signal-s${String(i.id).replaceAll("-","")}` && states.includes(String(i.state)) && (i.post_id===null || Number.isSafeInteger(i.post_id) && Number(i.post_id)>0) && (i.post_url===null || text(i.post_url,2048) && publicLink(i.post_url,origins,i.post_id)) && (i.prior_intent_id===null || uuid(i.prior_intent_id)) && Number.isInteger(i.reconcile_count) && Number(i.reconcile_count)>=0 && Number(i.reconcile_count)<=12 && typeof i.next_read_at === "string" && !Number.isNaN(Date.parse(i.next_read_at)) && (i.observation===null || text(i.observation,64)));
}
export function wordpressCommand(v: unknown): {siteId: string; body: Record<string,unknown>} | null {
  if (!object(v) || !keys(v,["schema_version","site_id","command"]) || v.schema_version!==1 || !uuid(v.site_id) || !object(v.command)) return null;
  const c=v.command;
  const fields: Record<string,string[]>={connect:["binding_id","origin"],seal:["binding_id","draft_id"],review:["candidate_id","revision_sha256","decision"],create:["candidate_id","intent_id","prior_intent_id"],reconcile:["intent_id"],observe:["intent_id"],revoke:["binding_id"]};
  const allowed = fields[String(c.operation)];
  if (!allowed || !keys(c,["operation",...allowed]) || allowed.some(k=>!(k in c))) return null;
  if (allowed.filter(k=>k.endsWith("_id") && k!=="prior_intent_id").some(k=>!uuid(c[k]))) return null;
  if (c.operation==="create" && !(c.prior_intent_id===null || uuid(c.prior_intent_id))) return null;
  if (c.operation==="connect" && !origin(c.origin)) return null;
  if (c.operation==="review" && (!digest(c.revision_sha256) || !["approved","rejected"].includes(String(c.decision)))) return null;
  return {siteId:v.site_id,body:{schema_version:1,command:c}};
}
export async function relayWordPress(token: string, siteId: string, dashboardOrigin: string, command?: ReturnType<typeof wordpressCommand>) {
  try {
    const base=validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL??"http://127.0.0.1:8000");
    const cookie=`${TENANT_COOKIE_NAME}=${token}`;
    let csrf="";
    if (command) {
      const r=await relayJson(new URL("/v1/session/tenant-csrf",base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(2500),headers:{Cookie:cookie,Accept:"application/json"}}, globalThis.fetch, 6100000);
      const proof=await boundedJson(r,4096);
      if (!r.ok || r.headers.getSetCookie().length || !object(proof) || proof.schema_version!==1 || typeof proof.csrf_token!=="string" || !/^[A-Za-z0-9_-]{43}$/.test(proof.csrf_token)) throw new Error();
      csrf=proof.csrf_token;
    }
    const r=await relayJson(new URL(`/v1/sites/${siteId}/wordpress`,base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(command?90000:15000),method:command?"POST":"GET",headers:{Cookie:cookie,Accept:"application/json",...(command?{Origin:dashboardOrigin,"Sec-Fetch-Site":"same-origin","X-CSRF-Token":csrf,"Content-Type":"application/json"}:{})},...(command?{body:JSON.stringify(command.body)}:{})}, globalThis.fetch, 6100000);
    if (r.headers.getSetCookie().length) throw new Error();
    const body=await boundedJson(r,6_100_000);
    if (r.ok && (!command?!validWordPressData(body):!object(body)||!keys(body,["schema_version","state","intent_id","candidate_id","reason"])||body.schema_version!==1||!text(body.state,128)||(body.intent_id!==undefined&&!uuid(body.intent_id))||(body.candidate_id!==undefined&&!uuid(body.candidate_id))||(body.reason!==undefined&&!text(body.reason,128)))) throw new Error();
    if (!r.ok) return Response.json({state:r.status===403?"rejected":"unavailable"},{status:r.status});
    return Response.json(body,{headers:{"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
  } catch {return Response.json({state:"unavailable"},{status:503});}
}
