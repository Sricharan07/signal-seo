import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { WebflowConnection } from "../components/webflow-connection";
import { WordPressDelivery } from "../components/wordpress-delivery";
import { POST } from "../app/auth/webflow/route";
import { GET as callback } from "../app/auth/webflow/callback/route";
import { webflowAuthorization, webflowOwnerCommand, validPublishingOptions } from "../lib/webflow-owner-api";
import { type WebflowData } from "../lib/webflow-api";
import { UPDATES_REASON, PUBLISH_REASON, QUARANTINE_REASON, validWordPressData } from "../lib/wordpress-api";

const SITE = "11111111-1111-4111-8111-111111111111", ID = "22222222-2222-4222-8222-222222222222";
const ORIGIN = "https://dashboard.example.invalid";
const mapping = {title: "name", description: "description", body: "body"};
const data: WebflowData = {schema_version: 1, capabilities: {create_draft: "DRAFT_ONLY", update: "WEBFLOW_UPDATE_ATOMIC_PRECONDITION_UNAVAILABLE", publish: "WEBFLOW_PUBLISH_ATOMIC_PRECONDITION_UNAVAILABLE", refresh: "WEBFLOW_EXISTING_ITEM_REFRESH_UNAVAILABLE", production: "WEBFLOW_LIVE_QUALIFICATION_NOT_EXECUTED"}, bindings: [], inbox: []};
const options = {schema_version: 1 as const, drafts: [{draft_id: ID, title: "An original article"}], articles: [{candidate_id: ID, title: "Approved article", source_sha256: "a".repeat(64)}]};
const fetcher = globalThis.fetch, previous = process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(() => {globalThis.fetch = fetcher; if (previous === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN = previous;});
function authorization() {return "https://webflow.com/oauth/authorize?" + new URLSearchParams({client_id: "synthetic-client", response_type: "code", redirect_uri: ORIGIN + "/auth/webflow/callback", state: "s".repeat(43), scope: "cms:read cms:write sites:read"});}
function request(value: unknown, origin = ORIGIN) {return new Request(ORIGIN + "/auth/webflow", {method: "POST", headers: {Cookie: `__Host-signal_session=${"t".repeat(43)}`, Origin: origin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}, body: JSON.stringify(value)});}

test("Webflow card uses shared status, confirmed mapping and named approved articles without a write control", () => {
  const empty = renderToStaticMarkup(<WebflowConnection siteId={SITE} initialData={data} initialOptions={options}/>);
  assert.match(empty, /connection-status/); assert.match(empty, /Connect Webflow/); assert.match(empty, /confirm this collection and field mapping/); assert.match(empty, /technical-details/);
  const bound = {...data, bindings: [{id: ID, origin: "https://cms.example.invalid", provider_site: "a".repeat(24), collection_id: "b".repeat(24), field_mapping: mapping, schema_sha256: "c".repeat(64), revoked_at: null}]};
  const html = renderToStaticMarkup(<WebflowConnection siteId={SITE} initialData={bound} initialOptions={options}/>);
  assert.match(html, /Article<select/); assert.match(html, /Approved article/); assert.match(html, /primary-command/); assert.match(html, /Disconnect/);
  assert.doesNotMatch(html, />Send|>Publish|draft ID|candidate ID/);
});

test("WordPress selects only current named bindings and reviewable articles and retains command shapes", () => {
  const base = {schema_version: 1 as const, provider_state: "available" as const, provider_reason: null, updates_reason: UPDATES_REASON, publish_reason: PUBLISH_REASON, quarantine_reason: QUARANTINE_REASON, candidates: [], intents: [], drafts: options.drafts};
  const bindings = [{id: ID, origin: "https://cms.example.invalid", user_id: 17, observed: {id: 17, roles: ["author"], capabilities: {read: true}}, current: true}];
  assert.ok(validWordPressData({...base, bindings}));
  const html = renderToStaticMarkup(<WordPressDelivery siteId={SITE} initialData={{...base, bindings}}/>);
  assert.match(html, /Publishing connection<select/); assert.match(html, /Article<select/); assert.match(html, /An original article/);
  assert.doesNotMatch(html, /Operator-provisioned binding ID|Content Writer draft ID|name="origin"|name="binding_id"/);
  const empty = renderToStaticMarkup(<WordPressDelivery siteId={SITE} initialData={{...base, bindings: []}}/>);
  assert.match(empty, /operator must provision/); assert.match(empty, /OpenBao/); assert.doesNotMatch(empty, /<form|<select/);
  assert.equal(validWordPressData({...base, bindings, drafts: [{...options.drafts[0], credential: "synthetic-password"}]}), false);
});

test("Webflow BFF binds HttpOnly callback context and sends only the closed command", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN; const calls: Request[] = [];
  globalThis.fetch = async (input, init) => {const r = new Request(input, init); calls.push(r); return Response.json(r.url.endsWith("tenant-csrf") ? {schema_version: 1, csrf_token: "c".repeat(43)} : {schema_version: 1, attempt_id: ID, authorization_url: authorization(), expires_in_seconds: 600});};
  const value = {schema_version: 1, site_id: SITE, operation: "begin", provider_site: "a".repeat(24), collection_id: "b".repeat(24), field_mapping: mapping};
  const response = await POST(request(value)); assert.equal(response.status, 200);
  assert.match(response.headers.get("set-cookie")!, /Secure; HttpOnly; SameSite=Lax; Max-Age=600/);
  assert.deepEqual(await calls[1]!.json(), {schema_version: 1, provider_site: value.provider_site, collection_id: value.collection_id});
  assert.equal(calls[1]!.headers.get("x-csrf-token"), "c".repeat(43));
  calls.length = 0;
  for (const bad of [{...value, access_token: "synthetic-token"}, {...value, operation: "deliver"}, {...value, field_mapping: {...mapping, body: "slug"}}]) assert.equal((await POST(request(bad))).status, 403);
  assert.equal((await POST(request(value, "https://evil.example.invalid"))).status, 403); assert.equal(calls.length, 0);
});

test("Webflow callback rejects duplicates, cancellation and absent context without leaking code", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN; const calls: Request[] = [];
  globalThis.fetch = async (input, init) => {const r = new Request(input, init); calls.push(r); return Response.json(r.url.endsWith("tenant-csrf") ? {schema_version: 1, csrf_token: "c".repeat(43)} : {schema_version: 1, state: "bound", id: ID});};
  const cookie = `__Host-signal_session=${"t".repeat(43)}; __Host-signal-webflow-attempt=${SITE}.${ID}.description.body`;
  const url = ORIGIN + "/auth/webflow/callback?state=" + "s".repeat(43) + "&code=synthetic-code";
  const result = await callback(new Request(url, {headers: {Cookie: cookie}}));
  assert.equal(result.headers.get("location"), ORIGIN + "/connectors?webflow=bound"); assert.match(result.headers.get("set-cookie")!, /Max-Age=0/); assert.equal(result.headers.get("referrer-policy"), "no-referrer");
  assert.deepEqual(await calls[1]!.json(), {schema_version: 1, attempt_id: ID, state: "s".repeat(43), code: "synthetic-code", field_mapping: mapping});
  calls.length = 0;
  for (const [target, cookies] of [[url + "&state=" + "x".repeat(43), cookie], [url + "&error=denied", cookie], [url, cookie + `; __Host-signal-webflow-attempt=${SITE}.${ID}.description.body`], [url, ""]]) {
    const failed = await callback(new Request(target!, {headers: {Cookie: cookies!}}));
    assert.equal(failed.headers.get("location"), ORIGIN + "/connectors?webflow=unavailable");
    assert.doesNotMatch(failed.headers.get("location")!, /synthetic-code/);
  }
  assert.equal(calls.length, 0);
});

test("Webflow failures preserve pending revocation, refuse token responses and validate redirects", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  let result: unknown = {schema_version: 1, state: "AUTHORITY_DURABILITY_PENDING", event_id: ID, upstream: "OUTCOME_UNKNOWN"};
  globalThis.fetch = async input => String(input).endsWith("tenant-csrf") ? Response.json({schema_version: 1, csrf_token: "c".repeat(43)}) : Response.json(result);
  const revoke = {schema_version: 1, site_id: SITE, operation: "revoke", binding_id: ID};
  const r = await POST(request(revoke)); assert.equal(r.status, 200); assert.equal((await r.json()).state, "AUTHORITY_DURABILITY_PENDING");
  result = {...result as object, access_token: "synthetic-token"}; assert.equal((await POST(request(revoke))).status, 503);
  globalThis.fetch = async () => {throw new Error("synthetic-private-error");}; assert.equal((await POST(request(revoke))).status, 503);
  assert.ok(webflowAuthorization(authorization(), ORIGIN));
  for (const url of [authorization().replace("webflow.com", "evil.example.invalid"), authorization() + "&state=x", authorization().replace("cms%3Awrite", "users%3Awrite")]) assert.equal(webflowAuthorization(url, ORIGIN), null);
  assert.ok(validPublishingOptions(options)); assert.equal(validPublishingOptions({...options, secret_reference: "secret://webflow/" + ID}), false);
  assert.equal(webflowOwnerCommand({...revoke, operation: "publish"}), null);
});
