import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Ga4Connector } from "../components/ga4-connector";
import { GA4_SCOPE, ga4AuthorizationUrl, ga4Command, relayGa4, validGa4State } from "../lib/ga4-api";

const site = "11111111-1111-4111-8111-111111111111";
const token = "synthetic-" + "t".repeat(33), csrf = "synthetic-" + "c".repeat(33);
const origin = "https://dashboard.example.invalid";
const disconnected = { state: "disconnected", binding_id: null, property_resource_name: null,
  scope: GA4_SCOPE, generation_id: null, coverage: null, imported_at: null, selection: null };

test("GA4 is visible and unavailable without fabricated imports or controls", () => {
  const html = renderToStaticMarkup(createElement(Ga4Connector, { siteId: site, owner: true }));
  assert.match(html, /Google Analytics 4/); assert.match(html, /Unavailable/);
  assert.doesNotMatch(html, /Connect GA4|Disconnect GA4|> Import<|<input/);
  assert.match(html, /Refresh status/);
  assert.equal((html.match(/<button/g) ?? []).length, 1);
  const denied = renderToStaticMarkup(createElement(Ga4Connector, { siteId: site, owner: false }));
  assert.match(denied, /An owner with MFA/);
  assert.doesNotMatch(denied, /<button|<input/);
});

test("GA4 status projections reject secrets, wrong scope, and completeness", () => {
  assert.equal(validGa4State(disconnected), true);
  for (const value of [{ ...disconnected, refresh_token: "synthetic-secret" },
    { ...disconnected, scope: "analytics.edit" }, { ...disconnected, coverage: { complete: true } },
    { ...disconnected, state: "read_only" }]) assert.equal(validGa4State(value), false);
});

test("GA4 authorization URL fixes exact OAuth scope, redirect and S256", () => {
  const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.search = new URLSearchParams({ client_id: "synthetic-google-client.apps.googleusercontent.com",
    scope: GA4_SCOPE, redirect_uri: origin + "/auth/ga4/callback", access_type: "offline", prompt: "consent",
    response_type: "code", include_granted_scopes: "false", state: token, code_challenge: csrf, code_challenge_method: "S256" }).toString();
  assert.equal(ga4AuthorizationUrl(url.href, origin), url.href);
  for (const [key, value] of [["scope", GA4_SCOPE + " openid"], ["redirect_uri", "https://other.example.invalid"], ["code_challenge_method", "plain"], ["include_granted_scopes", "true"]]) {
    const bad = new URL(url); bad.searchParams.set(key, value); assert.equal(ga4AuthorizationUrl(bad.href, origin), null);
  }
});

test("GA4 owner BFF rejects overbroad commands and invalid date windows", () => {
  const command = { site_id: site, operation: "import", start_date: "2026-09-01", end_date: "2026-09-28" };
  assert.ok(ga4Command(command));
  for (const changes of [{ scope: "analytics.edit" }, { operation: "complete" }, { operation: "delete" },
    { end_date: "2026-08-01" }, { start_date: "2025-01-01" }, { site_id: "other" }]) assert.equal(ga4Command({ ...command, ...changes }), null);
});

test("GA4 BFF carries only the current session and CSRF on an exact read-only command", async () => {
  let calls = 0;
  const command = { operation: "import", start_date: "2026-09-01", end_date: "2026-09-28" };
  const result = await relayGa4(token, site, origin, command, async (_url, init) => {
    assert.equal(init?.redirect, "error");
    calls++;
    if (calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("x-csrf-token"), csrf); assert.equal(headers.get("origin"), origin);
    return Response.json({ state: "imported", generation_id: site, coverage: { complete: false } });
  });
  assert.deepEqual(result, { state: "imported" }); assert.equal(calls, 2);
});

test("GA4 BFF fails closed on provider failures, cookies and extra credentials", async () => {
  for (const response of [Response.json(disconnected, { status: 503 }), Response.json({ ...disconnected, access_token: "synthetic-secret" }),
    Response.json(disconnected, { headers: { "Set-Cookie": "synthetic-cookie" } })]) {
    assert.equal(await relayGa4(token, site, origin, undefined, async () => response), null);
  }
});
