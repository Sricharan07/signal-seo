import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { BingConnector } from "../components/owner-connectors";
import { GET as callback } from "../app/auth/bing/callback/route";
import { POST as start } from "../app/auth/bing/route";
import { bingAuthorizationUrl, bingState, loadOwnerConnector } from "../lib/owner-connectors-api";
import { BING_ATTEMPT_COOKIE } from "../lib/owner-connector-route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const site = "11111111-1111-4111-8111-111111111111", attempt = "22222222-2222-4222-8222-222222222222";
const token = "t".repeat(43), origin = "https://signal-test.example.invalid";
const originalFetch = globalThis.fetch, originalEnv = {...process.env};
afterEach(() => { globalThis.fetch = originalFetch; process.env = {...originalEnv}; });
function authorization() {
  const url = new URL("https://www.bing.com/webmasters/oauth/authorize");
  url.search = new URLSearchParams({client_id:"synthetic-bing-client",redirect_uri:origin+"/auth/bing/callback",
    response_type:"code",scope:"webmaster.read",state:token}).toString();
  return url;
}
function configure() { process.env.SIGNAL_DASHBOARD_ORIGIN=origin; process.env.SIGNAL_API_BASE_URL="http://127.0.0.1:8000"; }
const cookie = `${TENANT_COOKIE_NAME}=${token}; ${BING_ATTEMPT_COOKIE}=${site}.${attempt}`;

test("Bing redirect is exact, read-only and rejects added authority", () => {
  assert.equal(bingAuthorizationUrl(authorization().href,origin),authorization().href);
  for (const [key,value] of [["scope","webmaster.manage"],["redirect_uri","https://evil.invalid"],["response_type","token"]]) {
    const url=authorization(); url.searchParams.set(key,value); assert.equal(bingAuthorizationUrl(url.href,origin),null);
  }
  const duplicate=authorization(); duplicate.searchParams.append("state",token);
  assert.equal(bingAuthorizationUrl(duplicate.href,origin),null);
  const wrong=authorization(); wrong.hostname="www.bing.com.evil.invalid";
  assert.equal(bingAuthorizationUrl(wrong.href,origin),null);
});

test("Bing projections reject other sites and secrets and remain unavailable without configuration", async () => {
  const state={availability:"bound",binding_id:attempt,site_url:origin+"/"};
  assert.deepEqual(bingState(state,origin+"/"),state);
  for (const bad of [{...state,site_url:"https://other.invalid/"},{...state,refresh_token:"synthetic"}])
    assert.deepEqual(bingState(bad,origin+"/"),{availability:"unavailable"});
  assert.deepEqual(bingState(state,null),{availability:"unavailable"});
  assert.deepEqual(bingState({availability:"selecting",attempt_id:attempt,sites:[{url:origin+"/"}]},origin+"/"),
    {availability:"selecting",attempt_id:attempt,sites:[{url:origin+"/"}]});
  let calls=0;
  assert.deepEqual(await loadOwnerConnector({connector:"bing",tenantToken:token,siteId:site,configuration:null,
    fetcher:async()=>{calls++;throw Error();}}),{availability:"unavailable"}); assert.equal(calls,0);
});

test("Bing card uses shared status, owner-only actions and technical details", () => {
  const html=renderToStaticMarkup(<BingConnector state={{availability:"bound",binding_id:attempt,site_url:origin+"/"}} siteId={site} owner/>);
  assert.match(html,/Connected/); assert.match(html,/Technical details/); assert.match(html,/Disconnect/);
  const unavailable=renderToStaticMarkup(<BingConnector state={{availability:"unavailable"}} siteId={site} owner/>);
  assert.doesNotMatch(unavailable,/<button|>Connected</);
  const selecting=renderToStaticMarkup(<BingConnector state={{availability:"selecting",attempt_id:attempt,sites:[{url:origin+"/"}]}} siteId={site} owner={false}/>);
  assert.doesNotMatch(selecting,/<button|>Connected</);
});

test("Bing BFF correlates protected attempt cookie and callback without exposing codes", async () => {
  configure(); const commands: Record<string,unknown>[]=[];
  globalThis.fetch=async(input,init)=>{
    const req=new Request(input,init);
    if (req.url.endsWith("/tenant-csrf")) return Response.json({schema_version:1,csrf_token:token});
    assert.equal(req.headers.get("x-csrf-token"),token); assert.equal(req.headers.get("origin"),origin);
    const body=await req.json(); commands.push(body);
    return body.operation==="authorize" ? Response.json({attempt_id:attempt,authorization_url:authorization().href}) : Response.json({attempt_id:attempt,state:"selecting"});
  };
  const response=await start(new Request(origin+"/auth/bing",{method:"POST",headers:{Cookie:`${TENANT_COOKIE_NAME}=${token}`,Origin:origin,"Sec-Fetch-Site":"same-origin","Content-Type":"application/json"},body:JSON.stringify({site_id:site,operation:"authorize"})}));
  assert.equal(response.status,200); assert.match(response.headers.get("set-cookie")??"",/Secure; HttpOnly; SameSite=Lax; Max-Age=300/);
  const completed=await callback(new Request(origin+"/auth/bing/callback?code=synthetic-code&state="+token,{headers:{Cookie:cookie}}));
  assert.equal(completed.status,303); assert.equal(completed.headers.get("location"),origin+"/connectors?bing=selecting");
  assert.equal(completed.headers.get("referrer-policy"),"no-referrer"); assert.equal(commands.length,2);
  assert.doesNotMatch(completed.headers.get("location")??"",/synthetic-code/);
});

test("Bing callback failures, duplicated state and cookies never dispatch", async () => {
  configure();let calls=0;globalThis.fetch=async()=>{calls++;throw Error("private");};
  for (const [query,cookies] of [["?code=x&state="+token+"&state="+token,cookie],["?error=access_denied",cookie],
    ["?code=x&state="+token,cookie+`; ${BING_ATTEMPT_COOKIE}=${site}.${attempt}`],["?code=x&state="+token,`${TENANT_COOKIE_NAME}=${token}`]]) {
    const response=await callback(new Request(origin+"/auth/bing/callback"+query,{headers:{Cookie:cookies}}));
    assert.equal(response.headers.get("location"),origin+"/connectors?bing=unavailable");
    assert.equal(response.headers.get("cache-control"),"no-store");
  }
  const rejected=await start(new Request(origin+"/auth/bing",{method:"POST",headers:{Cookie:cookie,Origin:"https://evil.invalid","Content-Type":"application/json"},body:"{}"}));
  assert.equal(rejected.status,403);assert.equal(calls,0);
});
