import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { IndexNowConnector } from "../components/indexnow-connector";
import { createIndexNowKey, loadIndexNow } from "../lib/indexnow-api";

const siteId = "11111111-1111-4111-8111-111111111111";
test("only an owner with an observed missing key sees the creation command", () => {
  const state = { state: "available", keyStatus: "not_created", canCreate: true, reason: "EC_142_KEY_NOT_CREATED", submissions: [] } as const;
  assert.match(renderToStaticMarkup(<IndexNowConnector state={state} siteId={siteId} owner />), /Create IndexNow key/);
  assert.doesNotMatch(renderToStaticMarkup(<IndexNowConnector state={state} siteId={siteId} />), /Create IndexNow key/);
});

test("key relay returns only a validated sealed revision and honest step-up failures", async () => {
  const token = "t".repeat(43), origin = "https://dashboard.example.invalid";
  let calls = 0;
  const fetcher: typeof fetch = async (_url, init) => {
    calls += 1;
    if (calls % 2 === 1) return Response.json({ schema_version: 1, csrf_token: "p".repeat(43) });
    assert.equal(init?.method, "POST");
    assert.equal(new Headers(init?.headers).get("X-CSRF-Token"), "p".repeat(43));
    assert.deepEqual(JSON.parse(String(init?.body)), { schema_version: 1, request_id: siteId });
    return Response.json({ schema_version: 1, state: "sealed", revision_id: siteId, revision_sha256: "a".repeat(64) });
  };
  assert.equal((await createIndexNowKey(token, siteId, siteId, origin, fetcher)).status, 200);
  const denied: typeof fetch = async (url) => String(url).includes("tenant-csrf") ? Response.json({ schema_version: 1, csrf_token: "p".repeat(43) }) : Response.json({ code: "INDEXNOW_STEP_UP_REQUIRED" }, { status: 403 });
  assert.deepEqual(await (await createIndexNowKey(token, siteId, siteId, origin, denied)).json(), { state: "step_up_required" });
  const cookie: typeof fetch = async () => Response.json({}, { headers: { "set-cookie": "synthetic-forbidden=x" } });
  assert.equal((await createIndexNowKey(token, siteId, siteId, origin, cookie)).status, 503);
  assert.equal((await createIndexNowKey("invalid", siteId, siteId, origin, fetcher)).status, 403);
});
test("IndexNow distinguishes unavailable, deployed, and skipped notifications", () => {
  assert.match(renderToStaticMarkup(<IndexNowConnector state={{ state: "unavailable" }} />), /unavailable/);
  const html = renderToStaticMarkup(<IndexNowConnector state={{ state: "available", keyStatus: "deployed", reason: "KEY_DEPLOYED", submissions: [{ changeId: siteId, urls: ["https://example.invalid/changed"], state: "skipped", providerStatus: null, reason: "EC_142_KEY_MISSING", recordedAt: "2026-10-02T12:00:00Z" }] }} />);
  assert.match(html, /Deployed/); assert.match(html, /Key missing/); assert.doesNotMatch(html, /EC_142_KEY_MISSING/); assert.match(html, /changed/);
});

test("IndexNow parser verifies site binding and keeps skipped distinct from zero", async () => {
  const payload = { schema_version: 1, site_id: siteId, correlation_id: "indexnow-test", key_status: "not_created", key_id: null, reason: "EC_142_KEY_NOT_CREATED", submissions: [] };
  const options = { tenantToken: "t".repeat(43), siteId, baseUrl: "http://127.0.0.1:8000" };
  assert.equal((await loadIndexNow({ ...options, fetcher: async () => Response.json(payload) })).state, "available");
  assert.equal((await loadIndexNow({ ...options, fetcher: async () => Response.json({ ...payload, site_id: "other" }) })).state, "invalid");
  assert.equal((await loadIndexNow({ ...options, fetcher: async () => new Response(null, { status: 403 }) })).state, "rejected");
  assert.equal((await loadIndexNow({ ...options, fetcher: async () => new Response(null, { status: 503 }) })).state, "unavailable");
  assert.equal((await loadIndexNow({ ...options, fetcher: async () => Response.json({ ...payload, submissions: [{ urls: ["https://other.example.invalid/"] }] }) })).state, "invalid");
  assert.equal((await loadIndexNow({ ...options, fetcher: async () => new Response("not-json") })).state, "invalid");
  assert.equal((await loadIndexNow({ ...options, fetcher: async () => { throw new Error("synthetic-network-failure"); } })).state, "unavailable");
});
