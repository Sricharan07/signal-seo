import { boundedRelayText, relayJson } from "./relay-json";
import { validBrandDocumentId as uuid } from "./brand-document-api";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";

export interface VisibilityScheduleData {
  schema_version: 1; site_id: string; settings_id: string | null;
  cadence_days: number; monthly_cap_micros: number; enabled: boolean;
  held_micros: number; spent_micros: null; currency: "USD"; month_start: string;
  cap_reached: boolean; authority_current: boolean; runtime_state: "available" | "unavailable";
  runs: {run_id: string; started_at: string; question_set_id: string | null; reason: string | null; observed_changes: string[];
    calls: {operation_id: string; provider: string; reserved_micros: number; status: string; reason: string | null; observation_id: string | null}[]}[];
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).sort().join("|") === fields.sort().join("|");
const integer = (v: unknown, min: number, max: number) => Number.isSafeInteger(v) && Number(v) >= min && Number(v) <= max;
const nullableId = (v: unknown) => v === null || uuid(v);
const reason = (v: unknown) => v === null || typeof v === "string" && /^[A-Z][A-Z0-9_]{0,127}$/.test(v);
export function validVisibilitySchedule(v: unknown, site: string): v is VisibilityScheduleData {
  if (!object(v) || !exact(v,["schema_version","site_id","settings_id","cadence_days","monthly_cap_micros","enabled","held_micros","spent_micros","currency","month_start","cap_reached","authority_current","runtime_state","runs"]) || v.schema_version !== 1 || !uuid(v.site_id) || v.site_id !== site || !nullableId(v.settings_id) || !integer(v.cadence_days,1,30) || !integer(v.monthly_cap_micros,0,100_000_000) || !integer(v.held_micros,0,Number.MAX_SAFE_INTEGER) || v.spent_micros !== null || v.currency !== "USD" || typeof v.enabled !== "boolean" || typeof v.authority_current !== "boolean" || v.cap_reached !== (Number(v.held_micros) >= Number(v.monthly_cap_micros)) || !["available","unavailable"].includes(String(v.runtime_state)) || typeof v.month_start !== "string" || !/^\d{4}-\d{2}-01$/.test(v.month_start) || !Array.isArray(v.runs) || v.runs.length > 30) return false;
  return v.runs.every(r => object(r) && exact(r,["run_id","started_at","question_set_id","reason","observed_changes","calls"]) && uuid(r.run_id) && typeof r.started_at === "string" && !Number.isNaN(Date.parse(r.started_at)) && nullableId(r.question_set_id) && reason(r.reason) && Array.isArray(r.observed_changes) && r.observed_changes.every(uuid) && Array.isArray(r.calls) && r.calls.length <= 75 && r.calls.every(c => object(c) && exact(c,["operation_id","provider","reserved_micros","status","reason","observation_id"]) && uuid(c.operation_id) && ["openai","perplexity","gemini"].includes(String(c.provider)) && integer(c.reserved_micros,0,100_000_000) && ["complete","incomplete","unavailable","outcome_unknown"].includes(String(c.status)) && reason(c.reason) && nullableId(c.observation_id)));
}
export function visibilitySettings(v: unknown): {site: string; body: Record<string, unknown>} | null {
  if (!object(v) || !exact(v,["schema_version","site_id","request_id","cadence_days","monthly_cap_micros","enabled"]) || v.schema_version !== 1 || !uuid(v.site_id) || !uuid(v.request_id) || !integer(v.cadence_days,1,30) || !integer(v.monthly_cap_micros,0,100_000_000) || typeof v.enabled !== "boolean") return null;
  return {site:v.site_id,body:Object.fromEntries(Object.entries(v).filter(([key])=>key!=="site_id"))};
}
export async function visibilityJson(body: ReadableStream<Uint8Array> | null, maximum = 300_000): Promise<unknown> {
  return JSON.parse(await boundedRelayText({ body }, maximum));
}
export async function relayVisibility(token: string, site: string, origin: string, body?: Record<string,unknown>) {
  try {
    const base=validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL??"http://127.0.0.1:8000");
    const cookie=`${TENANT_COOKIE_NAME}=${token}`;let csrf="";
    if(body){const r=await relayJson(new URL("/v1/session/tenant-csrf",base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(2500),headers:{Cookie:cookie,Accept:"application/json"}}, globalThis.fetch, 300000);const p=await visibilityJson(r.body,4096);if(!r.ok||r.headers.getSetCookie().length||!object(p)||p.schema_version!==1||typeof p.csrf_token!=="string"||!/^[A-Za-z0-9_-]{43}$/.test(p.csrf_token))throw new Error();csrf=p.csrf_token;}
    const r=await relayJson(new URL(`/v1/sites/${site}/ai-visibility/schedule`,base),{cache:"no-store",redirect:"error",signal:AbortSignal.timeout(15000),method:body?"POST":"GET",headers:{Cookie:cookie,Accept:"application/json",...(body?{Origin:origin,"Sec-Fetch-Site":"same-origin","X-CSRF-Token":csrf,"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})}, globalThis.fetch, 300000);
    if(r.headers.getSetCookie().length)throw new Error();const data=await visibilityJson(r.body);
    if(!r.ok)return Response.json({state:r.status===403?"rejected":"unavailable"},{status:r.status});
    if(!validVisibilitySchedule(data,site))throw new Error();
    return Response.json(data,{headers:{"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
  }catch{return Response.json({state:"unavailable"},{status:503});}
}
