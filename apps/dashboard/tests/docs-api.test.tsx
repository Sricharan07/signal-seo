import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DocsConnector } from "../components/docs-connector";
import { GET as callback } from "../app/auth/google-docs/callback/route";
import { POST } from "../app/auth/google-docs/route";
import { boundedBody, docsAuthorization, docsState, loadDocs, mutateDocs, NOTION_REASON, type DocsState } from "../lib/docs-api";

const site = "11111111-1111-4111-8111-111111111111";
const binding = "22222222-2222-4222-8222-222222222222";
const token = "synthetic-"+"t".repeat(33), csrf = "synthetic-"+"c".repeat(33);
const origin = "https://dashboard.example.invalid";
const originalFetch = globalThis.fetch, originalOrigin = process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(() => { globalThis.fetch=originalFetch; if(originalOrigin===undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN=originalOrigin; });
const ready: DocsState = { availability:"ready",extraction_availability:"available",binding_id:binding,reason:null,sources:[{source_id:site,file_id:"synthetic-picked-doc-123456",document_id:binding,provider_version:"1",modified_time:"2026-10-01T12:00:00Z",observed_at:"2026-10-01T12:00:00Z",state:"version",review_fact_ids:[]}] };

function authorization() {
  const url=new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.search=new URLSearchParams({client_id:"synthetic-client-123.apps.googleusercontent.com",scope:"https://www.googleapis.com/auth/drive.file",state:token,code_challenge:csrf,code_challenge_method:"S256",redirect_uri:origin+"/auth/google-docs/callback",response_type:"code",access_type:"offline",prompt:"consent",include_granted_scopes:"false",trigger_onepick:"true",allow_multiple:"true",mimetypes:"application/vnd.google-apps.document"}).toString();
  return url;
}

test("unconfigured Docs and unqualified Notion have no bind or sync controls",()=>{
  const html=renderToStaticMarkup(createElement(DocsConnector,{state:{availability:"unavailable",sources:[]},siteId:site,owner:true}));
  assert.match(html,/Google Docs is unavailable/); assert.ok(html.includes(NOTION_REASON));
  assert.doesNotMatch(html,/<button|<input/);
});

test("owner controls reflect ready, degraded and withdrawn source states",()=>{
  const html=renderToStaticMarkup(createElement(DocsConnector,{state:ready,siteId:site,owner:true}));
  assert.match(html,/Sync documents/);assert.match(html,/Disconnect Google Docs/);assert.match(html,/Extract and review proposed facts/);
  const withdrawn=renderToStaticMarkup(createElement(DocsConnector,{state:{...ready,sources:[{...ready.sources[0]!,state:"withdrawn",document_id:null,provider_version:null,modified_time:null,review_fact_ids:[binding]}]},siteId:site,owner:true}));
  assert.match(withdrawn,/Withdrawn/);assert.match(withdrawn,/1 derived fact needs owner review/);
  assert.match(withdrawn,/Review withdrawn-source facts/);
  const modelUnavailable=renderToStaticMarkup(createElement(DocsConnector,{state:{...ready,extraction_availability:"unavailable"},siteId:site,owner:true}));
  assert.match(modelUnavailable,/Review proposed facts/);assert.doesNotMatch(modelUnavailable,/Extract and review/);
  const denied=renderToStaticMarkup(createElement(DocsConnector,{state:ready,siteId:site,owner:false}));
  assert.doesNotMatch(denied,/<button/);
});

test("closed Docs projection rejects secrets, excessive manifests, wrong IDs and extra fields",()=>{
  assert.deepEqual(docsState(ready),ready);
  for(const bad of [{...ready,refresh_token:"synthetic-private"},{...ready,sources:Array(21).fill(ready.sources[0])},{...ready,sources:[{...ready.sources[0],file_id:"../secret"}]},{...ready,sources:[{...ready.sources[0],access_token:"synthetic-private"}]}])assert.equal(docsState(bad),null);
});

test("unavailable provider and secret-bearing response cannot reach a browser projection",async()=>{
  for(const response of [Response.json(ready,{status:503}),Response.json({...ready,access_token:"synthetic-private"}),Response.json(ready,{headers:{"Set-Cookie":"provider-secret=value"}})]){
    assert.deepEqual(await loadDocs({tenantToken:token,siteId:site,fetcher:async()=>response}),{availability:"unavailable",sources:[]});
  }
});

test("Google redirect permits exactly Picker code flow and drive.file without scope widening",()=>{
  const url=authorization();assert.equal(docsAuthorization(url.href,origin),url.href);
  for(const [key,value] of [["scope","https://www.googleapis.com/auth/drive"],["include_granted_scopes","true"],["redirect_uri","https://evil.example.invalid"],["response_type","token"],["mimetypes","*"],["access_token","synthetic-private"]]){
    const bad=new URL(url);bad.searchParams.set(key!,value!);assert.equal(docsAuthorization(bad.href,origin),null);
  }
});

test("mutations fetch current CSRF and discard any credential-bearing result",async()=>{
  let calls=0;
  const fetcher:typeof fetch=async(_input,init)=>{ calls++; if(calls===1)return Response.json({schema_version:1,csrf_token:csrf});
    assert.equal(new Headers(init?.headers).get("x-csrf-token"),csrf); assert.equal(init?.redirect,"error");
    return Response.json({binding_id:binding,refresh_token:"synthetic-private"}); };
  assert.equal(await mutateDocs({tenantToken:token,siteId:site,origin,command:{operation:"complete"},fetcher}),null);
  assert.equal(calls,2);
});

test("bounded request and response reader cancels excessive streams",async()=>{
  await assert.rejects(()=>boundedBody(new Response("x".repeat(4097)),4096));
  assert.equal(await boundedBody(new Response("source"),4096),"source");
});

function request(body:unknown,selectedOrigin=origin){return new Request(origin+"/auth/google-docs",{method:"POST",headers:{Cookie:`__Host-signal_session=${token}`,Origin:selectedOrigin,"Sec-Fetch-Site":"same-origin","Content-Type":"application/json"},body:JSON.stringify(body)});}
test("BFF rejects cross-origin, complete commands, credential fields and unpicked manifest injection",async()=>{
  process.env.SIGNAL_DASHBOARD_ORIGIN=origin;let calls=0;globalThis.fetch=async()=>{calls++;throw new Error();};
  assert.equal((await POST(request({site_id:site,operation:"connect"},"https://evil.example.invalid"))).status,403);
  for(const body of [{site_id:site,operation:"complete"},{site_id:site,operation:"connect",picked_file_ids:"synthetic-unpicked-doc"},{site_id:site,operation:"sync",access_token:"synthetic-private"}])assert.equal((await POST(request(body))).status,403);
  assert.equal(calls,0);
});

test("connect keeps the attempt in an HttpOnly cookie and sends only the exact consent URL",async()=>{
  process.env.SIGNAL_DASHBOARD_ORIGIN=origin;
  globalThis.fetch=async(input)=>String(input).endsWith("/tenant-csrf")?Response.json({schema_version:1,csrf_token:csrf}):Response.json({attempt_id:binding,authorization_url:authorization().href});
  const response=await POST(request({site_id:site,operation:"connect"}));
  assert.equal(response.status,200);assert.match(response.headers.get("set-cookie")??"",/HttpOnly; SameSite=Lax; Max-Age=600/);
  assert.deepEqual(await response.json(),{authorization_url:authorization().href});
});

function callbackRequest(scope="https://www.googleapis.com/auth/drive.file",cookie=`${site}.${binding}`){
  const url=new URL(origin+"/auth/google-docs/callback"); url.search=new URLSearchParams({scope,state:token,code:"synthetic-docs-code-123456",picked_file_ids:"synthetic-picked-doc-123456"}).toString();
  return new Request(url,{headers:{Cookie:`__Host-signal_session=${token}; __Host-signal-docs-attempt=${cookie}`}});
}
test("Picker callback exchanges code server-side, then clears attempt and strips all OAuth parameters",async()=>{
  process.env.SIGNAL_DASHBOARD_ORIGIN=origin;const calls:Request[]=[];
  globalThis.fetch=async(input,init)=>{const call=new Request(input,init);calls.push(call);return call.url.endsWith("/tenant-csrf")?Response.json({schema_version:1,csrf_token:csrf}):Response.json({binding_id:binding});};
  const response=await callback(callbackRequest()); assert.equal(response.status,303);assert.equal(response.headers.get("location"),origin+"/connectors?google-docs=bound");
  assert.match(response.headers.get("set-cookie")??"",/Max-Age=0/);assert.equal(response.headers.get("referrer-policy"),"no-referrer");
  assert.deepEqual(await calls[1]?.json(),{operation:"complete",attempt_id:binding,state:token,code:"synthetic-docs-code-123456",picked_file_ids:"synthetic-picked-doc-123456"});
});

test("ambiguous callback cookies or broad scopes fail before token exchange",async()=>{
  process.env.SIGNAL_DASHBOARD_ORIGIN=origin;let calls=0;globalThis.fetch=async()=>{calls++;throw new Error();};
  const bad=callbackRequest();bad.headers.append("Cookie",`__Host-signal-docs-attempt=${site}.${binding}`);
  for(const request of [bad,callbackRequest("https://www.googleapis.com/auth/drive"),callbackRequest(undefined,"wrong")]){
    const response=await callback(request);assert.equal(response.headers.get("location"),origin+"/connectors?google-docs=unavailable");
  }
  assert.equal(calls,0);
});
