import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { WebflowInbox } from "../components/webflow-inbox";
import { GET, POST } from "../app/actions/webflow/route";
import { validWebflowData, webflowReview, type WebflowData } from "../lib/webflow-api";

const SITE="11111111-1111-4111-8111-111111111111";
const ID="22222222-2222-4222-8222-222222222222";
const DATA:WebflowData={schema_version:1,capabilities:{create_draft:"DRAFT_ONLY",update:"WEBFLOW_UPDATE_ATOMIC_PRECONDITION_UNAVAILABLE",publish:"WEBFLOW_PUBLISH_ATOMIC_PRECONDITION_UNAVAILABLE",refresh:"WEBFLOW_EXISTING_ITEM_REFRESH_UNAVAILABLE",production:"WEBFLOW_LIVE_QUALIFICATION_NOT_EXECUTED"},bindings:[],inbox:[]};
const fetcher=globalThis.fetch;const origin=process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(()=>{globalThis.fetch=fetcher;if(origin===undefined)delete process.env.SIGNAL_DASHBOARD_ORIGIN;else process.env.SIGNAL_DASHBOARD_ORIGIN=origin;});

test("Webflow remains draft-only and visibly unavailable for live writes, updates and publishing",()=>{
  assert.ok(validWebflowData(DATA));
  assert.equal(validWebflowData({...DATA,capabilities:{...DATA.capabilities,publish:"available"}}),false);
  const html=renderToStaticMarkup(<WebflowInbox siteId={SITE} initialData={DATA}/>);
  assert.match(html,/has not been tested against a live Webflow site/);assert.match(html,/no documented atomic update precondition/);assert.match(html,/owner publishes in Webflow/);assert.match(html,/No active Webflow OAuth binding/);
  assert.doesNotMatch(html,/>Publish</);
});

test("Webflow Inbox escapes provider content and shows exact revision, quarantine and approval",()=>{
  const data:WebflowData={...DATA,inbox:[{id:ID,binding_id:ID,candidate_id:ID,payload:{items:[{isDraft:true,fieldData:{name:"<script>publish</script>",description:"Synthetic text",body:"<h2>Synthetic</h2>",slug:"article-signal-"+ID.replaceAll("-","")}}]},revision_sha256:"a".repeat(64),decision:"pending",state:"PLANNED",provider_item:null}]};
  assert.ok(validWebflowData(data));
  const html=renderToStaticMarkup(<WebflowInbox siteId={SITE} initialData={data}/>);
  assert.match(html,/Approve draft/);assert.match(html,/a{64}/);assert.doesNotMatch(html,/<script>/);
  const unknown=renderToStaticMarkup(<WebflowInbox siteId={SITE} initialData={{...data,inbox:[{...data.inbox[0]!,state:"OUTCOME_UNKNOWN"}]}}/>);
  assert.match(unknown,/No second create request/);assert.doesNotMatch(unknown,/Approve draft/);
  assert.equal(validWebflowData({...data,inbox:[{...data.inbox[0],secret_reference:"secret://webflow/"+ID}]}),false);
});

test("Webflow BFF accepts only an exact same-origin draft review with session CSRF",async()=>{
  process.env.SIGNAL_DASHBOARD_ORIGIN="http://localhost:3000";const calls:Request[]=[];
  globalThis.fetch=async(input,init)=>{const r=new Request(input,init);calls.push(r);return Response.json(r.url.endsWith("tenant-csrf")?{schema_version:1,csrf_token:"c".repeat(43)}:{schema_version:1,state:"reviewed"});};
  const command={schema_version:1,site_id:SITE,id:ID,revision_sha256:"a".repeat(64),decision:"approved"};
  const request=(body:unknown,selectedOrigin="http://localhost:3000")=>new Request("http://localhost:3000/actions/webflow",{method:"POST",headers:{Cookie:`__Host-signal_session=${"t".repeat(43)}`,Origin:selectedOrigin,"Sec-Fetch-Site":"same-origin","Content-Type":"application/json"},body:JSON.stringify(body)});
  assert.equal((await POST(request(command))).status,200);assert.equal(calls.length,2);assert.match(calls[1]!.url,/webflow\/review$/);assert.equal(calls[1]!.headers.get("x-csrf-token"),"c".repeat(43));
  calls.length=0;
  for (const bad of [{...command,publish:true},{...command,decision:"publish"},{...command,revision_sha256:"x".repeat(5000)}]) assert.equal((await POST(request(bad))).status,403);
  assert.equal((await POST(request(command,"https://evil.example.invalid"))).status,403);assert.equal(calls.length,0);
  assert.equal(webflowReview({...command,isArchived:true}),null);
  assert.equal((await GET(new Request(`http://localhost:3000/actions/webflow?site_id=${SITE}&site_id=${ID}`,{headers:{Cookie:`__Host-signal_session=${"t".repeat(43)}`}}))).status,403);
});
