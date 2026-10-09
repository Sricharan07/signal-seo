import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DataForSeoConnector } from "../components/dataforseo-connector";
import { dataforseoCommand, loadDataForSeo, mutateDataForSeo, parseDataForSeo } from "../lib/dataforseo-api";

const siteId = "11111111-1111-4111-8111-111111111111";
const token = "synthetic-" + "t".repeat(33), csrf = "synthetic-" + "c".repeat(33);
const origin = "https://dashboard.example.invalid";
const state = { availability: "unconfigured" as const, credential_configured: false, execution_configured: true,
  cap_micros: 5000000, usage_micros: 0, month: "2026-10-01", records: [],
  features: { competitor_gap: "unavailable" as const, search_volume: "unavailable" as const, competitor_backlinks: "unavailable" as const } };

test("EC-138 is visible with independent Search Console history and owner-only credential controls", () => {
  const html = renderToStaticMarkup(createElement(DataForSeoConnector, { state, siteId, owner: true }));
  assert.match(html, /Not configured/); assert.match(html, /Search Console history remains independent/);
  assert.match(html, /Store credential/); assert.match(html, /Save cap/);
  assert.equal((html.match(/type="password"/g) ?? []).length, 2);
  assert.doesNotMatch(html, /localStorage|synthetic/);
  const denied = renderToStaticMarkup(createElement(DataForSeoConnector, { state, siteId, owner: false }));
  assert.doesNotMatch(denied, /<input|<button/);
});

test("deployment unavailable never offers pretend setup", () => {
  const html = renderToStaticMarkup(createElement(DataForSeoConnector, { state: { availability: "unavailable" }, siteId, owner: true }));
  assert.match(html, /setup is unavailable/); assert.doesNotMatch(html, /<input|<button/);
});

test("closed settings projections reject secret extras and inconsistent readiness", async () => {
  assert.deepEqual(await loadDataForSeo({ tenantToken: token, siteId, fetcher: async () => Response.json(state) }), state);
  for (const bad of [{ ...state, password: "synthetic-password" }, { ...state, usage_micros: -1 }, { ...state, features: { ...state.features, search_volume: "available" } }]) {
    assert.deepEqual(parseDataForSeo(bad), { availability: "unavailable" });
  }
  assert.deepEqual(await loadDataForSeo({ tenantToken: token, siteId, fetcher: async () => Response.json({}, { status: 503 }) }), { availability: "unavailable" });
});

test("strict credential cap and remove commands reject untrusted extras and unsupported routes", () => {
  assert.ok(dataforseoCommand({ site_id: siteId, operation: "credential", login: "synthetic-login", password: "synthetic-password" }));
  assert.ok(dataforseoCommand({ site_id: siteId, operation: "remove" }));
  assert.ok(dataforseoCommand({ site_id: siteId, operation: "cap", cap_micros: 0 }));
  for (const bad of [{ operation: "query" }, { operation: "cap", cap_micros: -1 }, { operation: "remove", password: "synthetic-password" }, { operation: "credential", login: "a:b", password: "synthetic-password" }]) assert.equal(dataforseoCommand({ site_id: siteId, ...bad }), null);
});

test("credential mutation uses server-session CSRF and exposes only closed settings", async () => {
  let calls = 0;
  const result = await mutateDataForSeo({ tenantToken: token, siteId, origin, command: { operation: "credential", login: "synthetic-login", password: "synthetic-password" },
    fetcher: async (_input, init) => {
      calls++;
      if (calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
      assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrf);
      assert.equal(new Headers(init?.headers).get("origin"), origin);
      return Response.json(state);
    } });
  assert.deepEqual(result, state); assert.equal(calls, 2);
});

test("settings are read from the configured Signal API base URL like every other client", async () => {
  const previous = process.env.SIGNAL_API_BASE_URL;
  process.env.SIGNAL_API_BASE_URL = "https://api.example.invalid:8443";
  try {
    let requested = "";
    await loadDataForSeo({ tenantToken: token, siteId, fetcher: async (url) => { requested = String(url); return Response.json(state); } });
    assert.equal(requested, `https://api.example.invalid:8443/v1/sites/${siteId}/dataforseo`);
  } finally {
    if (previous === undefined) delete process.env.SIGNAL_API_BASE_URL; else process.env.SIGNAL_API_BASE_URL = previous;
  }
});
