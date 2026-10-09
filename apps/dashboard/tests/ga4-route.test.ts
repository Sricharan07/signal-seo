import assert from "node:assert/strict";
import test from "node:test";
import { GET as callback } from "../app/auth/ga4/callback/route";
import { POST } from "../app/auth/ga4/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";
import { GA4_ATTEMPT_COOKIE, GA4_SCOPE } from "../lib/ga4-api";

const origin = "https://dashboard.example.invalid";
const site = "11111111-1111-4111-8111-111111111111";
const attempt = "22222222-2222-4222-8222-222222222222";
const token = "synthetic-" + "t".repeat(33), state = "synthetic-" + "s".repeat(33);
const csrf = "synthetic-" + "c".repeat(33);
const code = "synthetic-google-authorization-code";
const cookie = `${TENANT_COOKIE_NAME}=${token}; ${GA4_ATTEMPT_COOKIE}=${site}.${attempt}`;

async function environment(fetcher: typeof fetch, action: () => Promise<void>) {
  const saved = [process.env.SIGNAL_DASHBOARD_ORIGIN, process.env.SIGNAL_API_BASE_URL, globalThis.fetch] as const;
  process.env.SIGNAL_DASHBOARD_ORIGIN = origin;
  process.env.SIGNAL_API_BASE_URL = "http://127.0.0.1:8000";
  globalThis.fetch = fetcher;
  try { await action(); } finally {
    if (saved[0] === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN = saved[0];
    if (saved[1] === undefined) delete process.env.SIGNAL_API_BASE_URL; else process.env.SIGNAL_API_BASE_URL = saved[1];
    globalThis.fetch = saved[2];
  }
}

test("GA4 BFF denies cross-origin, overlarge streamed and extra-field commands before dispatch", async () => {
  let calls = 0;
  await environment(async () => { calls++; throw new Error("synthetic-unexpected-dispatch"); }, async () => {
    for (const [body, proposedOrigin, status] of [
      [JSON.stringify({ site_id: site, operation: "connect" }), "https://other.example.invalid", 403],
      ["x".repeat(4097), origin, 422],
      [JSON.stringify({ site_id: site, operation: "connect", scope: "analytics.edit" }), origin, 422],
    ] as const) {
      const result = await POST(new Request(origin + "/auth/ga4", { method: "POST", body,
        headers: { Cookie: cookie, Origin: proposedOrigin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json" } }));
      assert.equal(result.status, status); assert.equal(result.headers.get("cache-control"), "no-store");
    }
  });
  assert.equal(calls, 0);
});

test("GA4 callback relays one correlated owner code and erases browser callback material", async () => {
  const calls: string[] = [];
  await environment(async (input, init) => {
    const url = new URL(String(input)); calls.push(url.pathname);
    if (url.pathname.endsWith("tenant-csrf")) return Response.json({ schema_version: 1, csrf_token: csrf });
    assert.deepEqual(JSON.parse(String(init?.body)), { operation: "complete", attempt_id: attempt, code, state });
    assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrf);
    return Response.json({ attempt_id: attempt, properties: [{ resource_name: "properties/123", display_name: "Synthetic property" }] });
  }, async () => {
    const query = new URLSearchParams({ code, state, scope: GA4_SCOPE, authuser: "0", prompt: "consent" });
    const result = await callback(new Request(origin + "/auth/ga4/callback?" + query, { headers: { Cookie: cookie } }));
    assert.equal(result.status, 303); assert.equal(result.headers.get("location"), origin + "/connectors");
    assert.equal(result.headers.get("referrer-policy"), "no-referrer");
    assert.match(result.headers.get("set-cookie") ?? "", /Max-Age=0/);
    assert.doesNotMatch([...result.headers.values()].join(" "), /synthetic-google-authorization-code/);
  });
  assert.deepEqual(calls, ["/v1/session/tenant-csrf", `/v1/sites/${site}/ga4`]);
});

test("GA4 callback denies duplicate state, wrong scope, missing and ambiguous correlation cookies", async () => {
  let calls = 0;
  await environment(async () => { calls++; throw new Error("synthetic-unexpected-dispatch"); }, async () => {
    const query = new URLSearchParams({ code, state });
    for (const [parameters, cookies] of [
      [query + "&state=" + state, cookie], [query + "&scope=analytics.edit", cookie],
      [query.toString(), `${TENANT_COOKIE_NAME}=${token}`],
      [query.toString(), cookie + `; ${GA4_ATTEMPT_COOKIE}=${site}.${attempt}`],
    ]) {
      const result = await callback(new Request(origin + "/auth/ga4/callback?" + parameters, { headers: { Cookie: cookies } }));
      assert.equal(result.status, 303); assert.equal(result.headers.get("location"), origin + "/connectors");
      assert.match(result.headers.get("set-cookie") ?? "", /Max-Age=0/);
    }
  });
  assert.equal(calls, 0);
});
