import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ContentWriter } from "../components/content-writer";
import { POST } from "../app/actions/content-writer/route";
import { validWriterData, writerCommand, type WriterData } from "../lib/content-writer-api";

const SITE="11111111-1111-4111-8111-111111111111";
const ID="22222222-2222-4222-8222-222222222222";
const DATA:WriterData={schema_version:1,cap:2,used:0,cap_reached:false,platform_maximum:5,model_state:"unavailable",candidate_state:"unavailable",briefs:[],drafts:[],candidates:[]};
const fetcher=globalThis.fetch;const origin=process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(()=>{globalThis.fetch=fetcher;if(origin===undefined)delete process.env.SIGNAL_DASHBOARD_ORIGIN;else process.env.SIGNAL_DASHBOARD_ORIGIN=origin;});

test("article PR authority requires exact explicit sentence acknowledgements",()=>{
 const grounding={state:"owner_required",sentences:[{path:"title",sentence:"A flagged claim.",fact_ids:[ID],reasons:["SENSITIVE_CLAIM"],supported:false}]};
 const data:WriterData={...DATA,candidates:[{candidate_id:ID,draft_id:ID,revision_sha256:"a".repeat(64),review_status:"pending",created_at:"2026-09-29T00:00:00Z",manifest:{approval_class:"A2",autonomy_eligible:false,work_type:"new_article",grounding,originality:{},changed_files:[{path:"new.html",before:"",after:"<article>Draft</article>"}]}}]};
 const html=renderToStaticMarkup(<ContentWriter siteId={SITE} inbox initialData={data}/>);
 assert.match(html,/I acknowledge: A flagged claim/);assert.match(html,/type="checkbox"/);assert.match(html,/disabled=""[^>]*><svg[^]*Approve PR delivery/);assert.match(html,/MFA authenticated within five minutes/);
 const command={schema_version:1,site_id:SITE,action:"approve-delivery",candidate_id:ID,revision_sha256:"a".repeat(64),acknowledged_sentences:["title"]};
 assert.ok(writerCommand(command));assert.equal(writerCommand({...command,channel:"slack"}),null);assert.equal(writerCommand({...command,acknowledged_sentences:["title","title"]}),null);
});

test("Content Writer exposes unavailable, cap, brief and Inbox states without inventing readiness",()=>{
  const html=renderToStaticMarkup(<ContentWriter siteId={SITE} initialData={DATA}/>);
  assert.match(html,/Briefs and drafts/);assert.match(html,/Drafting unavailable/);assert.match(html,/New brief/);assert.match(html,/Weekly draft cap/);assert.match(html,/Ready for your review/);
  const reached=renderToStaticMarkup(<ContentWriter siteId={SITE} initialData={{...DATA,used:2,cap_reached:true}}/>);assert.match(reached,/Weekly cap reached/);
  assert.ok(validWriterData(DATA));assert.equal(validWriterData({...DATA,cap:100}),false);assert.equal(writerCommand({schema_version:1,site_id:SITE,action:"caps",cap:100}),null);
  const inbox=renderToStaticMarkup(<ContentWriter siteId={SITE} inbox initialData={DATA}/>);assert.match(inbox,/Articles to review/);assert.doesNotMatch(inbox,/Create brief/);
});

test("grounding and sealed HTML remain escaped, with exact revision decisions",()=>{
 const data:WriterData={...DATA,drafts:[{draft_id:ID,brief_id:ID,created_at:"2026-09-29T00:00:00Z",fact_snapshot:[],voice_snapshot:null,result:{state:"owner_required",grounding:{state:"owner_required",sentences:[{path:"title",sentence:"<script>approve</script>",fact_ids:[ID],reasons:["SENSITIVE_CLAIM"],supported:false}]},originality:{state:"original",eight_gram_overlap:0,lexical_window_overlap:0,source_count:1}}}],candidates:[{candidate_id:ID,draft_id:ID,revision_sha256:"a".repeat(64),review_status:"pending",created_at:"2026-09-29T00:00:00Z",manifest:{approval_class:"A2",autonomy_eligible:false,work_type:"new_article",grounding:{},originality:{},changed_files:[{path:"new.html",before:"",after:"<script>unsafe</script>"}]}}]};
 const html=renderToStaticMarkup(<ContentWriter siteId={SITE} initialData={data}/>);assert.match(html,/Sensitive claim/);assert.match(html,/Originality/);assert.match(html,/Inbox item/);assert.match(html,/Approve/);assert.doesNotMatch(html,/<script>unsafe/);assert.match(html,/Fact 2222/);
});

test("same-origin Content Writer BFF forwards only a bounded CSRF-protected command",async()=>{
 process.env.SIGNAL_DASHBOARD_ORIGIN="http://localhost:3000";const calls:Request[]=[];
 globalThis.fetch=async(input,init)=>{const r=new Request(input,init);calls.push(r);return Response.json(r.url.endsWith("tenant-csrf")?{schema_version:1,csrf_token:"c".repeat(43)}:{schema_version:1,state:"updated"});};
 const request=(body:unknown,selectedOrigin="http://localhost:3000")=>new Request("http://localhost:3000/actions/content-writer",{method:"POST",headers:{Cookie:`__Host-signal_session=${"t".repeat(43)}`,Origin:selectedOrigin,"Sec-Fetch-Site":"same-origin","Content-Type":"application/json"},body:JSON.stringify(body)});
 const command={schema_version:1,site_id:SITE,action:"caps",cap:2};
 assert.equal((await POST(request(command))).status,200);assert.equal(calls.length,2);assert.equal(calls[1]?.headers.get("x-csrf-token"),"c".repeat(43));assert.ok(calls[1]?.url.endsWith("content-writer/caps"));
 calls.length=0;assert.equal((await POST(request(command,"https://evil.test"))).status,403);assert.equal((await POST(request({...command,publish:true}))).status,403);assert.equal((await POST(request({...command,cap:"x".repeat(21000)}))).status,403);assert.equal(calls.length,0);
});

test("writing quality and monthly exhaustion are explicit, escaped, and bounded",()=>{
 const data:WriterData={...DATA,monthly_model_budget:{cap_micros:25_000_000,used_micros:25_000_000,warning:true,state:"unavailable",month:"2026-10-01"},drafts:[{draft_id:ID,brief_id:ID,created_at:"2026-10-03T00:00:00Z",fact_snapshot:[],voice_snapshot:null,result:{state:"owner_required",quality:{state:"low_quality",reasons:["banned_hits"],regenerations:1,metrics:{banned_hits:2,readability:null},language:"te"}}}]};
 assert.ok(validWriterData(data));
 Object.assign(data.drafts[0]!.result,{fallback:true,provider:"openai_fallback"});
 const html=renderToStaticMarkup(<ContentWriter siteId={SITE} initialData={data}/>);
 assert.match(html,/25.00 of \$25.00/);assert.match(html,/exhausted; drafting unavailable/);assert.match(html,/Writing quality: Low quality/);assert.match(html,/Banned hits/);assert.match(html,/Quality metrics/);
 assert.match(html,/Labelled high-effort model fallback/);assert.doesNotMatch(html,/frontier fallback/);
 assert.equal(validWriterData({...data,monthly_model_budget:{...data.monthly_model_budget,used_micros:-1}}),false);
 assert.equal(validWriterData({...data,drafts:[{...data.drafts[0],result:{state:"owner_required",quality:{state:"passed",reasons:[],regenerations:8,metrics:{},language:"en"}}}]}),false);
});
