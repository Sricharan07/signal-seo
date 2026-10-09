import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { WordPressDelivery } from "../components/wordpress-delivery";
import { PUBLISH_REASON, QUARANTINE_REASON, UPDATES_REASON, validWordPressData, wordpressCommand, type WordPressData } from "../lib/wordpress-api";
import { POST } from "../app/actions/wordpress/route";

const SITE="11111111-1111-4111-8111-111111111111";
const ID="22222222-2222-4222-8222-222222222222";
const DATA:WordPressData={schema_version:1,provider_state:"unavailable",provider_reason:"WORDPRESS_UNCONFIGURED",updates_reason:UPDATES_REASON,publish_reason:PUBLISH_REASON,quarantine_reason:QUARANTINE_REASON,bindings:[],candidates:[],intents:[]};
const fetcher=globalThis.fetch;const origin=process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(()=>{globalThis.fetch=fetcher;if(origin===undefined)delete process.env.SIGNAL_DASHBOARD_ORIGIN;else process.env.SIGNAL_DASHBOARD_ORIGIN=origin;});

test("WordPress unavailable states are explicit and never imply a bridge or publishing authority",()=>{
  const html=renderToStaticMarkup(<WordPressDelivery siteId={SITE} initialData={DATA}/>);
  assert.match(html,/not configured/);assert.match(html,/no atomic preconditions/);assert.match(html,/Publish in WordPress yourself/);
  assert.doesNotMatch(html,/application.password.*input|Publish<|Install bridge/);
  assert.ok(validWordPressData(DATA));
  assert.equal(validWordPressData({...DATA,password:"synthetic-password"}),false);
  for (const operation of ["publish","update","delete","trash"]) assert.equal(wordpressCommand({schema_version:1,site_id:SITE,command:{operation}}),null);
});

test("WordPress Inbox escapes sealed content and explains quarantined intents",()=>{
  const data:WordPressData={...DATA,candidates:[{id:ID,draft_id:ID,binding_id:ID,decision:"approved",revision_sha256:"a".repeat(64),payload:{status:"draft",title:"Sealed article",content:"<script>untrusted</script>",excerpt:"Bounded summary"}}],intents:[{id:ID,candidate_id:ID,marker:`signal-s${ID.replaceAll("-","")}`,state:"outcome_unknown",post_id:null,post_url:null,prior_intent_id:null,reconcile_count:1,next_read_at:"2026-01-01T00:00:00Z",observation:null}]};
  assert.ok(validWordPressData(data));
  const html=renderToStaticMarkup(<WordPressDelivery siteId={SITE} inbox initialData={data}/>);
  assert.match(html,/Quarantined: outcome unknown/);assert.match(html,/will not send it again/);assert.match(html,/Create separate draft/);assert.match(html,/Check for draft/);
  assert.doesNotMatch(html,/<script>untrusted/);assert.match(html,/&lt;script&gt;/);
});

test("WordPress BFF enforces same origin, closed command and server CSRF",async()=>{
  process.env.SIGNAL_DASHBOARD_ORIGIN="http://localhost:3000";const calls:Request[]=[];
  globalThis.fetch=async(input,init)=>{const r=new Request(input,init);calls.push(r);return Response.json(r.url.endsWith("tenant-csrf")?{schema_version:1,csrf_token:"c".repeat(43)}:{schema_version:1,state:"outcome_unknown"});};
  const request=(body:unknown,origin="http://localhost:3000")=>new Request("http://localhost:3000/actions/wordpress",{method:"POST",headers:{Cookie:`__Host-signal_session=${"t".repeat(43)}`,Origin:origin,"Sec-Fetch-Site":"same-origin","Content-Type":"application/json"},body:JSON.stringify(body)});
  const command={schema_version:1,site_id:SITE,command:{operation:"create",candidate_id:ID,intent_id:ID,prior_intent_id:null}};
  assert.equal((await POST(request(command))).status,200);assert.equal(calls.length,2);assert.equal(calls[1]?.headers.get("x-csrf-token"),"c".repeat(43));assert.ok(calls[1]?.url.endsWith("/wordpress"));
  calls.length=0;
  assert.equal((await POST(request(command,"https://evil.example.invalid"))).status,403);
  assert.equal((await POST(request({...command,credential:"synthetic-password"}))).status,403);
  assert.equal((await POST(request({...command,command:{operation:"publish"}}))).status,403);
  assert.equal(calls.length,0);
});

test("Stale bindings retain an owner revocation path before reconnecting",()=>{
  const data:WordPressData={...DATA,provider_state:"available",provider_reason:null,bindings:[{id:ID,origin:"https://cms.example.invalid",user_id:17,observed:{id:17,roles:["author"],capabilities:{read:true,edit_posts:true}},current:false}]};
  assert.ok(validWordPressData(data));
  const html=renderToStaticMarkup(<WordPressDelivery siteId={SITE} initialData={data}/>);
  assert.match(html,/Unavailable bindings/);
  assert.match(html,/Stale or revoked: user 17/);
  assert.match(html,/Revoke binding/);
  assert.match(html,/No current WordPress binding/);
});
