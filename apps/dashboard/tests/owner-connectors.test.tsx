import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { GscConnector, GithubConnector } from "../components/owner-connectors";
import { GET as callback } from "../app/auth/gsc/callback/route";
import { POST as start } from "../app/auth/gsc/route";
import { gscState, githubState, gscAuthorizationUrl, loadOwnerConnector, mutateOwnerConnector, ownerConnectorConfiguration } from "../lib/owner-connectors-api";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const site = "11111111-1111-4111-8111-111111111111", binding = "22222222-2222-4222-8222-222222222222";
const token = "synthetic-"+"t".repeat(33), csrf = "synthetic-"+"c".repeat(33);
const origin = "https://signal-test.example.invalid";
const TEST_GSC_PROPERTY = origin + "/";
const githubScope = {github_installation_id:123456789, github_owner:"example-owner", github_repository:"integration-test", github_base_branch:"main", github_content_path:"README.md"};
const configuration = {property:TEST_GSC_PROPERTY, github:githubScope};

test("missing or malformed private resource configuration is visibly unavailable", async () => {
  assert.deepEqual(gscState({availability:"unbound"}), {availability:"unavailable"});
  assert.deepEqual(githubState({availability:"unbound"}), {availability:"unavailable"});
  let calls = 0;
  assert.deepEqual(await loadOwnerConnector({connector:"github", tenantToken:token, siteId:site,
    configuration:null, fetcher:async()=>{calls++; return Response.json({availability:"unbound"});}}),
    {availability:"unavailable"});
  assert.equal(calls,0);
  const oldOrigin=process.env.SIGNAL_DASHBOARD_ORIGIN, oldScope=process.env.SIGNAL_OWNER_CONNECTOR_SCOPE;
  try {
    process.env.SIGNAL_DASHBOARD_ORIGIN=origin;
    delete process.env.SIGNAL_OWNER_CONNECTOR_SCOPE;
    assert.equal(ownerConnectorConfiguration(),null);
    for (const value of [{...githubScope, github_installation_id:0}, {...githubScope, extra:"denied"}, {...githubScope, github_owner:"../wrong"}]) {
      process.env.SIGNAL_OWNER_CONNECTOR_SCOPE=JSON.stringify(value);
      assert.equal(ownerConnectorConfiguration(),null);
    }
    process.env.SIGNAL_OWNER_CONNECTOR_SCOPE=JSON.stringify(githubScope);
    assert.deepEqual(ownerConnectorConfiguration(),configuration);
  } finally {
    if(oldOrigin===undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN=oldOrigin;
    if(oldScope===undefined) delete process.env.SIGNAL_OWNER_CONNECTOR_SCOPE; else process.env.SIGNAL_OWNER_CONNECTOR_SCOPE=oldScope;
  }
});
function authUrl() {
  const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.search = new URLSearchParams({ client_id: "1234567890123456.apps.googleusercontent.com", redirect_uri: origin+"/auth/gsc/callback",
    response_type: "code", scope: "https://www.googleapis.com/auth/webmasters.readonly", access_type: "offline", prompt: "consent",
    include_granted_scopes: "false", state: token, code_challenge: csrf, code_challenge_method: "S256" }).toString();
  return url;
}

test("connector projections accept only exact resources and never secrets", () => {
  const gsc = { availability: "bound", binding_id: binding, property_resource_name: TEST_GSC_PROPERTY };
  assert.deepEqual(gscState(gsc, TEST_GSC_PROPERTY), gsc);
  for (const bad of [{...gsc, refresh_token: "synthetic"}, {...gsc, property_resource_name: "sc-domain:example.invalid"}])
    assert.deepEqual(gscState(bad, TEST_GSC_PROPERTY), { availability: "unavailable" });
  const github = { availability: "active", binding_id: binding, installation_id: 123456789, owner: "example-owner",
    repository: "integration-test", base_branch: "main", content_path: "README.md", repository_id: 123,
    base_sha: "a".repeat(40), failure_code: null, base_protection: "protected" };
  assert.deepEqual(githubState(github, githubScope), github);
  for (const bad of [{...github, repository: "private-other"}, {...github, base_sha: null}, {...github, private_key: "synthetic"}])
    assert.deepEqual(githubState(bad, githubScope), { availability: "unavailable" });
});

test("GSC redirect requires exact read-only scope and PKCE", () => {
  assert.equal(gscAuthorizationUrl(authUrl().href, origin), authUrl().href);
  for (const [key, value] of [["scope", "https://mail.google.com/"], ["code_challenge_method", "plain"],
    ["include_granted_scopes", "true"], ["redirect_uri", "https://evil.invalid"], ["access_type", "online"]]) {
    const url = authUrl(); url.searchParams.set(key, value);
    assert.equal(gscAuthorizationUrl(url.href, origin), null);
  }
  const duplicate = authUrl(); duplicate.searchParams.append("state", token);
  assert.equal(gscAuthorizationUrl(duplicate.href, origin), null);
});

test("unprotected acceptance warning persists and risk acceptance is never preselected", () => {
  const state = {availability:"active" as const, binding_id:binding, installation_id:123456789,
    owner:"example-owner", repository:"integration-test", base_branch:"main", content_path:"README.md",
    repository_id:123, base_sha:"a".repeat(40), failure_code:null, base_protection:"owner_accepted_unprotected" as const};
  const html = renderToStaticMarkup(createElement(GithubConnector,{state,siteId:site,owner:true}));
  assert.match(html,/Unprotected default branch \(owner-accepted\)/);
  assert.match(html,/Every PR requires owner Inbox approval/);
  const pending = renderToStaticMarkup(createElement(GithubConnector,{state:{...state,base_protection:"unprotected_not_accepted"},siteId:site,owner:true}));
  assert.match(pending,/type="checkbox"/); assert.doesNotMatch(pending,/checked=""/);
  assert.match(pending,/disabled=""[^>]*><svg[^]*Accept unprotected default branch/);
  assert.deepEqual(githubState({...state,availability:"failed"},githubScope),{availability:"unavailable"});
});

test("owner connector read and mutation fail closed on provider failure", async () => {
  assert.deepEqual(await loadOwnerConnector({connector: "gsc", tenantToken: token, siteId: site, configuration,
    fetcher: async() => Response.json({availability: "unbound"})}), { availability: "unbound" });
  for (const response of [Response.json({}, {status: 503}), Response.json({availability:"unbound"}, {headers: {"Set-Cookie":"private"}})])
    assert.deepEqual(await loadOwnerConnector({connector: "github", tenantToken: token, siteId: site, configuration, fetcher: async()=>response}), {availability:"unavailable"});
  let calls = 0;
  const result = await mutateOwnerConnector({connector:"gsc", tenantToken:token, siteId:site, origin, command:{operation:"authorize"}, fetcher:async(_input, init)=>{
    calls++;
    if (calls === 1) return Response.json({schema_version:1,csrf_token:csrf});
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("x-csrf-token"),csrf); assert.equal(headers.get("origin"),origin);
    return Response.json({attempt_id:binding,authorization_url:authUrl().href});
  }});
  assert.equal(calls,2); assert.equal(result?.attempt_id,binding);
});

test("unavailable and incomplete connectors cannot pretend to be connected", () => {
  for (const element of [createElement(GscConnector, {state:{availability:"unavailable"},siteId:site,owner:true}),
    createElement(GithubConnector, {state:{availability:"unavailable"},siteId:site,owner:true})]) {
    const html = renderToStaticMarkup(element);
    assert.doesNotMatch(html,/<button|>Connected</);
  }
  const selecting = renderToStaticMarkup(createElement(GscConnector, {state:{availability:"selecting", attempt_id:binding,
    properties:[{resource_name:TEST_GSC_PROPERTY,property_type:"url_prefix"}]},siteId:site,owner:true}));
  assert.match(selecting,/Confirmation required/); assert.match(selecting,/Confirm https/); assert.doesNotMatch(selecting,/>Connected</);
});

test("GSC BFF start and callback correlate cookies without exposing codes", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN=origin;
  process.env.SIGNAL_API_BASE_URL="http://127.0.0.1:8000";
  const original=globalThis.fetch;
  let calls=0;
  globalThis.fetch=async(_input,init)=>{
    calls++;
    if (calls % 2 === 1) return Response.json({schema_version:1,csrf_token:csrf});
    const command=JSON.parse(String(init?.body)) as Record<string,unknown>;
    return Response.json(command.operation === "authorize" ? {attempt_id:binding,authorization_url:authUrl().href} : {attempt_id:binding,state:"selecting"});
  };
  try {
    const response=await start(new Request(origin+"/auth/gsc",{method:"POST",headers:{"Content-Type":"application/json",Origin:origin,"Sec-Fetch-Site":"same-origin",Cookie:`${TENANT_COOKIE_NAME}=${token}`},body:JSON.stringify({operation:"authorize",site_id:site})}));
    assert.equal(response.status,200); assert.match(response.headers.get("set-cookie")??"",/Secure; HttpOnly; SameSite=Lax; Max-Age=600/);
    const cookie=`${TENANT_COOKIE_NAME}=${token}; __Host-signal-gsc-attempt=${site}.${binding}`;
    const completed=await callback(new Request(origin+`/auth/gsc/callback?code=synthetic-code&state=${token}`,{headers:{Cookie:cookie}}));
    assert.equal(completed.status,303); assert.equal(completed.headers.get("location"),origin+"/connectors?gsc=selecting");
    assert.equal(completed.headers.get("referrer-policy"),"no-referrer"); assert.match(completed.headers.get("set-cookie")??"",/Max-Age=0/);
    const before=calls;
    for (const query of [`code=synthetic&state=${token}&state=${token}`, `error=access_denied&state=${token}`, `code=synthetic&state=wrong`]) {
      const failed=await callback(new Request(origin+"/auth/gsc/callback?"+query,{headers:{Cookie:cookie}}));
      assert.equal(failed.headers.get("location"),origin+"/connectors?gsc=unavailable");
    }
    assert.equal(calls,before);
  } finally {globalThis.fetch=original; delete process.env.SIGNAL_DASHBOARD_ORIGIN; delete process.env.SIGNAL_API_BASE_URL;}
});
