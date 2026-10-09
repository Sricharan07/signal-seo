import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validBrandDocumentId as uuid } from "./brand-document-api";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

export interface VisibilityObservation {
  id: string; question_id: string; provider: string; model: string; observed_at: string;
  provider_evidence_id: string | null; coverage: "complete" | "incomplete";
  site_cited: boolean | null; cited_pages: string[]; competitor_pages: string[];
  failure_code: string | null;
}
export interface VisibilityProposal {
  id: string; observation_id: string; page_id: string; kind: string; digest: string;
  brief_id: string | null; created_at: string; decision: string;
  sealing?: { state: string; reason: string; revision_id?: string; revision_sha256?: string };
  payload: {
    state: string; page_url: string; fact_ids: string[]; rationale: string;
    provider: string; model: string; observed_at: string; provider_evidence_id: string;
    manifest_id: string; claims: { text: string; fact_ids: string[] }[];
    schema_type?: string; reason?: string; supporting_page_labels?: string;
    grounding: { state: string; sentences: { path: string; sentence: string; reasons: string[] }[] };
    originality: { state: string; eight_gram_overlap: number; lexical_window_overlap: number };
    recipe_inputs?: { recipe_key: string; report_id: string; finding_id: string; target_url: string }[];
  };
}
export interface VisibilityData {
  schema_version: 1; state: string;
  gaps: {
    id: string; question: string; question_set_id: string; crawl_manifest_id: string; created_at: string;
    observations: VisibilityObservation[]; history: VisibilityObservation[];
    relevant_pages: { id: string; url: string; title: string | null; score: number; matched_terms: string[]; manifest_id: string }[];
  }[];
  proposals: VisibilityProposal[];
  providers: { provider: string; state: string; reason: string }[];
  reobservation: { state: "unavailable" | "paused" | "enabled" | "cap_reached" | "worker_unavailable"; reason: string };
  on_demand_observation: { state: "unavailable"; reason: string };
  comparison_wording: string;
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const text = (v: unknown, max = 4000): v is string => typeof v === "string" && v.length <= max && !v.includes("\0");
const date = (v: unknown) => text(v, 64) && !Number.isNaN(Date.parse(v));
const url = (v: unknown) => {
  if (!text(v, 2048)) return false;
  try { const u = new URL(v); return ["https:", "http:"].includes(u.protocol) && !u.username && !u.password; } catch { return false; }
};
const array = (v: unknown, max: number, valid: (item: unknown) => boolean): boolean => Array.isArray(v) && v.length <= max && v.every(valid);
const references = (v: unknown) => array(v, 20, uuid);
const provider = (v: unknown) => ["openai", "perplexity", "gemini"].includes(String(v));
const digest = (v: unknown) => text(v,64) && /^[0-9a-f]{64}$/.test(v);
function observation(v: unknown): boolean {
  return object(v) && uuid(v.id) && uuid(v.question_id) && provider(v.provider) && text(v.model,128) && date(v.observed_at) &&
    (v.provider_evidence_id === null || uuid(v.provider_evidence_id)) && ["complete","incomplete"].includes(String(v.coverage)) &&
    (v.coverage === "complete" ? typeof v.site_cited === "boolean" && uuid(v.provider_evidence_id) : v.site_cited === null && Array.isArray(v.cited_pages) && !v.cited_pages.length && Array.isArray(v.competitor_pages) && !v.competitor_pages.length) &&
    array(v.cited_pages,32,url) && array(v.competitor_pages,32,url) && (v.failure_code === null || text(v.failure_code,128));
}
export function validVisibilityData(v: unknown): v is VisibilityData {
  if (!object(v) || v.schema_version !== 1 || !["available","origin_unavailable","coverage_limit"].includes(String(v.state)) ||
      Object.keys(v).some(k=>!["schema_version","state","gaps","proposals","providers","reobservation","on_demand_observation","comparison_wording"].includes(k))) return false;
  if (!array(v.providers,3,p=>object(p) && provider(p.provider) && ["unavailable","historical_observations_only"].includes(String(p.state)) && text(p.reason,200)) ||
      !object(v.reobservation) || !["unavailable","paused","enabled","cap_reached","worker_unavailable"].includes(String(v.reobservation.state)) || !text(v.reobservation.reason,200) ||
      !object(v.on_demand_observation) || v.on_demand_observation.state!=="unavailable" || !text(v.on_demand_observation.reason,200) || !text(v.comparison_wording,300)) return false;
  if (!array(v.gaps,500,g=>object(g) && uuid(g.id) && uuid(g.question_set_id) && uuid(g.crawl_manifest_id) && text(g.question,512) && date(g.created_at) && array(g.observations,3,observation) && array(g.history,500,observation) && array(g.relevant_pages,3,p=>object(p) && uuid(p.id) && uuid(p.manifest_id) && url(p.url) && (p.title === null || text(p.title,512)) && Number.isInteger(p.score) && Number(p.score)>0 && array(p.matched_terms,512,t=>text(t,512))))) return false;
  return array(v.proposals,1500,p=>object(p) && uuid(p.id) && uuid(p.observation_id) && uuid(p.page_id) && ["content","structured_data","internal_link"].includes(String(p.kind)) && digest(p.digest) && (p.brief_id === null || uuid(p.brief_id)) && date(p.created_at) && ["proposed","accepted","dismissed"].includes(String(p.decision)) && object(p.payload) &&
    (p.sealing===undefined || object(p.sealing) && ["available","sealed","step_up_required","proposal_unavailable","facts_unavailable","inputs_unavailable","worker_unavailable","type_unavailable"].includes(String(p.sealing.state)) && text(p.sealing.reason,400) && (p.sealing.state!=="sealed" || uuid(p.sealing.revision_id) && digest(p.sealing.revision_sha256))) &&
    ["ready","owner_required","rejected"].includes(String(p.payload.state)) && url(p.payload.page_url) && references(p.payload.fact_ids) && text(p.payload.rationale,1000) && provider(p.payload.provider) && text(p.payload.model,128) && date(p.payload.observed_at) && uuid(p.payload.provider_evidence_id) && uuid(p.payload.manifest_id) && array(p.payload.claims,20,c=>object(c) && text(c.text,4000) && references(c.fact_ids)) &&
    (p.payload.schema_type === undefined || ["FAQPage","HowTo","Organization","Product","Article"].includes(String(p.payload.schema_type))) && (p.payload.reason === undefined || text(p.payload.reason,400)) && (p.payload.supporting_page_labels === undefined || text(p.payload.supporting_page_labels,16000)) &&
    object(p.payload.grounding) && ["grounded","owner_required"].includes(String(p.payload.grounding.state)) && array(p.payload.grounding.sentences,23,s=>object(s) && text(s.path,128) && text(s.sentence,4000) && array(s.reasons,10,r=>text(r,128))) &&
    object(p.payload.originality) && ["original","rejected"].includes(String(p.payload.originality.state)) && [p.payload.originality.eight_gram_overlap,p.payload.originality.lexical_window_overlap].every(n=>typeof n === "number" && n>=0 && n<=1) &&
    (p.payload.recipe_inputs === undefined || array(p.payload.recipe_inputs,8,r=>object(r) && r.recipe_key === "technical_broken_link" && uuid(r.report_id) && uuid(r.finding_id) && url(r.target_url))));
}
export function visibilityCommand(v: unknown) {
  if (!object(v) || !uuid(v.site_id) || v.schema_version !== 1 || !["prepare","decide","questions-propose","questions-approve","seal"].includes(String(v.action))) return null;
  const allowed = ["schema_version","site_id","action", ...(v.action==="decide"?["proposal_id","digest","decision"]:v.action==="questions-approve"?["request_id","crawl_manifest_id","supersedes_id","questions"]:v.action==="seal"?["proposal_id","digest","fact_fields"]:[])];
  if (Object.keys(v).length !== allowed.length || Object.keys(v).some(k=>!allowed.includes(k))) return null;
  if (v.action === "decide" && (!uuid(v.proposal_id) || !digest(v.digest) || !["accepted","dismissed"].includes(String(v.decision)))) return null;
  if(v.action==="questions-approve" && (!uuid(v.request_id)||!uuid(v.crawl_manifest_id)||!(v.supersedes_id===null||uuid(v.supersedes_id))||!Array.isArray(v.questions)||!v.questions.length||!array(v.questions,25,q=>text(q,512)&&q.trim().length>=8)||new Set(v.questions.map(q=>String(q).trim().toLowerCase())).size!==v.questions.length))return null;
  if(v.action==="seal" && (!uuid(v.proposal_id)||!digest(v.digest)||!object(v.fact_fields)||!Object.keys(v.fact_fields).length||Object.keys(v.fact_fields).length>2||!Object.entries(v.fact_fields).every(([k,ref])=>["name","description","headline","question","answer"].includes(k)&&uuid(ref))))return null;
  return {siteId:v.site_id, action:String(v.action), body:Object.fromEntries(Object.entries(v).filter(([k])=>!["site_id","action"].includes(k)))};
}
export interface QuestionProposal { schema_version: 1; state: "proposed"; crawl_manifest_id: string; supersedes_id: string | null; questions: { question: string; source_kind: string; source_evidence_id: string }[] }
export function validQuestionProposal(v: unknown): v is QuestionProposal {
  return object(v) && v.schema_version===1 && v.state==="proposed" && uuid(v.crawl_manifest_id) && (v.supersedes_id===null||uuid(v.supersedes_id)) && Array.isArray(v.questions) && v.questions.length>0 && array(v.questions,25,q=>object(q)&&text(q.question,512)&&q.question.length>=8&&q.source_kind==="crawl"&&uuid(q.source_evidence_id));
}
export async function boundedVisibilityJson(response: Response, maximum: number): Promise<unknown> {
  return JSON.parse(await boundedRelayText(response, maximum));
}
export async function relayVisibility(token: string, siteId: string, origin: string, command?: ReturnType<typeof visibilityCommand>) {
  try {
    const base=validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL??"http://127.0.0.1:8000");
    const cookie=`${TENANT_COOKIE_NAME}=${token}`; let csrf="";
    if(command){const r=await relayJson(new URL("/v1/session/tenant-csrf",base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(2500),headers:{Cookie:cookie,Accept:"application/json"}}, globalThis.fetch, 3000000);const p=await boundedVisibilityJson(r,4096);if(!r.ok||r.headers.getSetCookie().length||!object(p)||p.schema_version!==1||!text(p.csrf_token,43)||!/^[A-Za-z0-9_-]{43}$/.test(p.csrf_token))throw new Error();csrf=p.csrf_token;}
    const r=await relayJson(new URL(`/v1/sites/${siteId}/ai-visibility${command?"/"+command.action:""}`,base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(command?.action==="seal"?90000:15000),method:command?"POST":"GET",headers:{Cookie:cookie,Accept:"application/json",...(command?{Origin:origin,"Sec-Fetch-Site":"same-origin","X-CSRF-Token":csrf,"Content-Type":"application/json"}:{})},...(command?{body:JSON.stringify(command.body)}:{})}, globalThis.fetch, 3000000);
    if(r.headers.getSetCookie().length)throw new Error();const body=await boundedVisibilityJson(r,3_000_000);
    if(!r.ok)return Response.json({state:r.status===403?"rejected":"unavailable"},{status:r.status});
    if(!command?!validVisibilityData(body):!object(body)||body.schema_version!==1||!["prepared","accepted","dismissed","replayed","unavailable","proposed","approved","sealed"].includes(String(body.state)))throw new Error();
    if(command?.action==="questions-propose" && object(body) && body.state!=="unavailable" && !validQuestionProposal(body))throw new Error();
    if(command?.action==="questions-approve" && object(body) && (body.state!=="approved"||!uuid(body.question_set_id)))throw new Error();
    if(command?.action==="seal" && object(body) && (body.state!=="sealed"||!uuid(body.revision_id)||!digest(body.revision_sha256)))throw new Error();
    return Response.json(body,{headers:{"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
  }catch{return Response.json({state:"unavailable"},{status:503});}
}
