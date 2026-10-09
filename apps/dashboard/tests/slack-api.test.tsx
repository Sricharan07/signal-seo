import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SlackConnector } from "../components/slack-connector";
import { loadSlack, mutateSlack, slackAuthorizationUrl } from "../lib/slack-api";

const siteId = "11111111-1111-4111-8111-111111111111";
const binding = "22222222-2222-4222-8222-222222222222";
const token = "synthetic-"+"t".repeat(33), csrf = "synthetic-"+"c".repeat(33);
const origin = "https://dashboard.example.test";

test("unconfigured Slack has no pretend install or link controls", () => {
  const html = renderToStaticMarkup(createElement(SlackConnector, { state:{ availability:"unavailable" },siteId,owner:true }));
  assert.match(html,/Slack is not configured/);
  assert.doesNotMatch(html,/<input|<button/);
});

test("Slack setup and per-user link controls follow actual binding state", () => {
  const setup = renderToStaticMarkup(createElement(SlackConnector,{ state:{ availability:"unbound" },siteId,owner:true }));
  assert.match(setup,/Workspace ID/); assert.match(setup,/Channel ID/); assert.doesNotMatch(setup,/>A3</);
  const linked = renderToStaticMarkup(createElement(SlackConnector,{ state:{ availability:"bound",binding_id:binding,
    workspace_id:"T00000001",channel_id:"C00000001",max_risk:2,link_id:siteId,slack_user_id:"U00000001" },siteId,owner:false }));
  assert.match(linked,/Unlink account/); assert.doesNotMatch(linked,/Disconnect Slack/);
});

test("Slack read projections reject extra secrets and provider unavailability", async () => {
  const view = {availability:"bound",binding_id:binding,workspace_id:"T00000001",channel_id:"C00000001",max_risk:2,link_id:null,slack_user_id:null};
  assert.deepEqual(await loadSlack({tenantToken:token,siteId,fetcher:async()=>Response.json(view)}),view);
  for (const response of [Response.json({...view,bot_token:"synthetic-secret"}),Response.json({}, {status:503})]) {
    assert.deepEqual(await loadSlack({tenantToken:token,siteId,fetcher:async()=>response}),{availability:"unavailable"});
  }
});

test("Slack mutations use current tenant CSRF without returning browser tokens", async () => {
  let calls=0;
  const result = await mutateSlack({tenantToken:token,siteId,origin,command:{operation:"link",binding_id:binding,slack_user_id:"U00000001"},
    fetcher:async(_input,init)=>{
      calls++;
      if(calls===1)return Response.json({schema_version:1,csrf_token:csrf});
      const headers=new Headers(init?.headers);
      assert.equal(headers.get("x-csrf-token"),csrf);assert.equal(headers.get("origin"),origin);
      return Response.json({state:"queued",outbox_id:siteId});
    }});
  assert.equal(calls,2);assert.deepEqual(result,{state:"queued",outbox_id:siteId});
});

test("OAuth redirect is one exact Slack origin with only chat:write", () => {
  const url = new URL("https://slack.com/oauth/v2/authorize");
  url.search = new URLSearchParams({client_id:"123.456",scope:"chat:write",state:token,team:"T00000001",redirect_uri:origin+"/auth/slack/callback"}).toString();
  assert.equal(slackAuthorizationUrl(url.href,origin),url.href);
  for(const [key,value] of [["scope","chat:write,channels:history"],["redirect_uri","https://evil.invalid"],["team","bad"]]){
    const bad=new URL(url);bad.searchParams.set(key,value);assert.equal(slackAuthorizationUrl(bad.href,origin),null);
  }
  assert.equal(slackAuthorizationUrl(url.href.replace("slack.com","evil.invalid"),origin),null);
});
