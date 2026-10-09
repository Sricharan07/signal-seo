import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validBrandDocumentId as uuid } from "./brand-document-api";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

export interface WriterData {
  schema_version: 1; cap: number; used: number; cap_reached: boolean; platform_maximum: number;
  monthly_model_budget?: {cap_micros: number; used_micros: number; warning: boolean; state: "available" | "unavailable"; month: string};
  model_state: "available" | "unavailable"; candidate_state: "available" | "unavailable";
  briefs: {brief_id: string; status: string; origin: string; supersedes_id: string | null; created_at: string; payload: {topic: string; intent: string; query: string; source_ids: string[]; fact_ids: string[]; internal_links: string[]; kind: string}}[];
  drafts: {draft_id: string; brief_id: string; created_at: string; fact_snapshot: unknown[]; voice_snapshot: unknown; result: {state: string; reason?: string; quality?: {state: "passed" | "low_quality"; reasons: string[]; regenerations: number; metrics: Record<string,number|null>; language: string}; article?: unknown; grounding?: {state: string; sentences: {path: string; sentence: string; fact_ids: string[]; reasons: string[]; supported: boolean; claims?: {text: string; fact_ids: string[]; status: string; reasons: string[]}[]}[]}; originality?: {state: string; eight_gram_overlap: number; lexical_window_overlap: number; source_count: number}; provider?: string; fallback?: boolean}}[];
  candidates: {delivery_approval_id?: string | null; candidate_id: string; draft_id: string; revision_sha256: string; review_status: string; created_at: string; manifest: {approval_class: string; autonomy_eligible: boolean; work_type: string; grounding: unknown; originality: unknown; changed_files: {path: string; before: string; after: string}[]}}[];
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const text = (v: unknown, max = 4000) => typeof v === "string" && v.length <= max && !v.includes("\0");
const refs = (v: unknown, max = 20) => Array.isArray(v) && v.length <= max && v.every(uuid);
const date = (v: unknown) => typeof v === "string" && !Number.isNaN(Date.parse(v));
const keys = (v: Record<string, unknown>, allowed: string[]) => Object.keys(v).every(k => allowed.includes(k));
export async function boundedJson(response: Response, maximum: number): Promise<unknown> {
  return JSON.parse(await boundedRelayText(response, maximum));
}
export function validWriterData(v: unknown): v is WriterData {
  if (!object(v) || !keys(v, ["schema_version","cap","used","cap_reached","platform_maximum","model_state","candidate_state","briefs","drafts","candidates","monthly_model_budget"]) || v.schema_version !== 1 || !Number.isInteger(v.cap) || Number(v.cap) < 1 || Number(v.cap) > 5 || !Number.isInteger(v.used) || Number(v.used) < 0 || v.platform_maximum !== 5 || v.cap_reached !== (Number(v.used) >= Number(v.cap)) || !["available","unavailable"].includes(String(v.model_state)) || !["available","unavailable"].includes(String(v.candidate_state))) return false;
  if (v.monthly_model_budget !== undefined && (!object(v.monthly_model_budget) || !Number.isSafeInteger(v.monthly_model_budget.cap_micros) || Number(v.monthly_model_budget.cap_micros)<0 || Number(v.monthly_model_budget.cap_micros)>1_000_000_000 || !Number.isSafeInteger(v.monthly_model_budget.used_micros) || Number(v.monthly_model_budget.used_micros)<0 || typeof v.monthly_model_budget.warning !== "boolean" || !["available","unavailable"].includes(String(v.monthly_model_budget.state)) || !date(v.monthly_model_budget.month))) return false;
  if (![v.briefs,v.drafts,v.candidates].every(a => Array.isArray(a) && a.length <= 100)) return false;
  return (v.briefs as unknown[]).every(b => object(b) && uuid(b.brief_id) && ["proposed","accepted","superseded"].includes(String(b.status)) && ["owner","evidence_proposal"].includes(String(b.origin)) && (b.supersedes_id === null || uuid(b.supersedes_id)) && date(b.created_at) && object(b.payload) && text(b.payload.topic,200) && text(b.payload.query,200) && ["new_article","content_refresh"].includes(String(b.payload.kind)) && ["informational","commercial","navigational","transactional"].includes(String(b.payload.intent)) && refs(b.payload.source_ids,8) && refs(b.payload.fact_ids) && Array.isArray(b.payload.internal_links) && b.payload.internal_links.length <= 8 && b.payload.internal_links.every(s => text(s,2048))) &&
    (v.drafts as unknown[]).every(d => object(d) && uuid(d.draft_id) && uuid(d.brief_id) && date(d.created_at) && Array.isArray(d.fact_snapshot) && object(d.result) && (d.result.quality === undefined || object(d.result.quality) && ["passed","low_quality"].includes(String(d.result.quality.state)) && Array.isArray(d.result.quality.reasons) && d.result.quality.reasons.length <= 9 && d.result.quality.reasons.every(r=>text(r,128)) && Number.isInteger(d.result.quality.regenerations) && [0,1].includes(Number(d.result.quality.regenerations)) && object(d.result.quality.metrics) && Object.values(d.result.quality.metrics).every(n=>n===null || typeof n==="number" && Number.isFinite(n)) && text(d.result.quality.language,16)) && ["grounded","owner_required","rejected","failed","outcome_unknown"].includes(String(d.result.state)) && (d.result.grounding === undefined || object(d.result.grounding) && Array.isArray(d.result.grounding.sentences) && d.result.grounding.sentences.length <= 120 && d.result.grounding.sentences.every(s => object(s) && text(s.path,128) && text(s.sentence,1000) && refs(s.fact_ids) && Array.isArray(s.reasons) && s.reasons.every(r=>text(r,128)) && typeof s.supported === "boolean" && (s.claims===undefined || Array.isArray(s.claims) && s.claims.length <= 6 && s.claims.every(c=>object(c) && text(c.text,1000) && refs(c.fact_ids) && ["supported","unsupported","uncertain"].includes(String(c.status)) && Array.isArray(c.reasons) && c.reasons.every(r=>text(r,128)))))) && (d.result.originality === undefined || object(d.result.originality) && ["original","rejected"].includes(String(d.result.originality.state)) && [d.result.originality.eight_gram_overlap,d.result.originality.lexical_window_overlap].every(n=>typeof n === "number" && n >= 0 && n <= 1))) &&
    (v.candidates as unknown[]).every(c => object(c) && uuid(c.candidate_id) && uuid(c.draft_id) && (c.delivery_approval_id === undefined || c.delivery_approval_id === null || uuid(c.delivery_approval_id)) && typeof c.revision_sha256 === "string" && /^[0-9a-f]{64}$/.test(c.revision_sha256) && ["pending","approved","rejected","changes_requested"].includes(String(c.review_status)) && date(c.created_at) && object(c.manifest) && c.manifest.approval_class === "A2" && c.manifest.autonomy_eligible === false && Array.isArray(c.manifest.changed_files) && c.manifest.changed_files.length === 1 && c.manifest.changed_files.every(f=>object(f) && text(f.path,1024) && text(f.before,131072) && text(f.after,131072)));
}
export function writerCommand(v: unknown): {siteId: string; action: string; body: Record<string,unknown>} | null {
  if (!object(v) || !uuid(v.site_id) || v.schema_version !== 1 || typeof v.action !== "string") return null;
  const fields: Record<string,string[]> = {briefs:["payload","proposal","supersedes_id"],"accept-brief":["brief_id"],caps:["cap"],drafts:["brief_id"],candidates:["draft_id","extension_id","destination"],review:["candidate_id","revision_sha256","decision"],"approve-delivery":["candidate_id","revision_sha256","acknowledged_sentences"]};
  const allowed = fields[v.action];
  if (!allowed || !keys(v,["schema_version","site_id","action",...allowed]) || allowed.some(k=>!(k in v))) return null;
  if (v.action === "briefs" && (!object(v.payload) || !keys(v.payload,["topic","intent","query","source_ids","fact_ids","internal_links","kind"]) || !text(v.payload.topic,200) || !text(v.payload.query,200) || !refs(v.payload.source_ids,8) || !refs(v.payload.fact_ids) || typeof v.proposal !== "boolean" || !(v.supersedes_id === null || uuid(v.supersedes_id)))) return null;
  if (["drafts","accept-brief"].includes(v.action) && !uuid(v.brief_id)) return null;
  if (v.action === "caps" && (!Number.isInteger(v.cap) || Number(v.cap)<1 || Number(v.cap)>5)) return null;
  if (v.action === "candidates" && (!uuid(v.draft_id) || !uuid(v.extension_id) || !text(v.destination,1024))) return null;
  if (v.action === "review" && (!uuid(v.candidate_id) || typeof v.revision_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(v.revision_sha256) || !["approved","rejected","changes_requested"].includes(String(v.decision)))) return null;
  if (v.action === "approve-delivery" && (!uuid(v.candidate_id) || typeof v.revision_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(v.revision_sha256) || !Array.isArray(v.acknowledged_sentences) || v.acknowledged_sentences.length>120 || !v.acknowledged_sentences.every(s=>text(s,128)) || new Set(v.acknowledged_sentences).size!==v.acknowledged_sentences.length)) return null;
  return {siteId:v.site_id,action:v.action,body:Object.fromEntries(Object.entries(v).filter(([k])=>! ["site_id","action"].includes(k)))};
}
export async function relayWriter(token: string, siteId: string, origin: string, command?: ReturnType<typeof writerCommand>) {
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const cookie = `${TENANT_COOKIE_NAME}=${token}`;
    let csrf = "";
    if (command) {
      const r = await relayJson(new URL("/v1/session/tenant-csrf",base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(2500),headers:{Cookie:cookie,Accept:"application/json"}}, globalThis.fetch, 3000000);
      const proof = await boundedJson(r,4096);
      if (!r.ok || r.headers.getSetCookie().length || !object(proof) || proof.schema_version !== 1 || typeof proof.csrf_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(proof.csrf_token)) throw new Error();
      csrf = proof.csrf_token;
    }
    const r = await relayJson(new URL(`/v1/sites/${siteId}/content-writer${command ? "/"+command.action : ""}`,base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(command ? 90000:15000),method:command?"POST":"GET",headers:{Cookie:cookie,Accept:"application/json",...(command?{Origin:origin,"Sec-Fetch-Site":"same-origin","X-CSRF-Token":csrf,"Content-Type":"application/json"}:{})},...(command?{body:JSON.stringify(command.body)}:{})}, globalThis.fetch, 3000000);
    if (r.headers.getSetCookie().length) throw new Error();
    const body = await boundedJson(r,3_000_000);
    if (r.ok && (!command ? !validWriterData(body) : !object(body) || body.schema_version!==1 || !["created","accepted","updated","reviewed","replayed","grounded","owner_required","rejected","failed","outcome_unknown","cap_reached","unavailable","sealed","approved"].includes(String(body.state)))) throw new Error();
    if (!r.ok) return Response.json({state:r.status===403?"rejected":"unavailable"},{status:r.status});
    return Response.json(body,{status:r.status,headers:{"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
  } catch {return Response.json({state:"unavailable"},{status:503});}
}
