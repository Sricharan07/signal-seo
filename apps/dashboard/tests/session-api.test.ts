import assert from "node:assert/strict";
import test from "node:test";

import { TENANT_COOKIE_NAME } from "../lib/browser-auth";
import { loadDashboardSession, SESSION_COOKIE_NAME } from "../lib/session-api";

const token = "a".repeat(43);
const fixedNow = () => new Date("2026-09-09T20:00:00.000Z");

test("uses the environment-specific tenant cookie name", () => {
  assert.equal(SESSION_COOKIE_NAME, TENANT_COOKIE_NAME);
});

function sessionResponse(overrides: Record<string, unknown> = {}): Response {
  return Response.json({
    schema_version: 2,
    tenant_id: "11111111-1111-4111-8111-111111111111",
    user_id: "22222222-2222-4222-8222-222222222222",
    role_key: "owner",
    authentication_level: "mfa",
    expires_at: "2026-09-09T20:30:00.000Z",
    session_version: 7,
    active_site_id: "33333333-3333-4333-8333-333333333333",
    ...overrides,
  });
}

test("forwards one exact opaque cookie and accepts bounded current authority", async () => {
  const session = await loadDashboardSession({
    sessionTokens: [token],
    baseUrl: "https://signal-api.test",
    now: fixedNow,
    fetcher: async (input, init) => {
      assert.equal(String(input), "https://signal-api.test/v1/session");
      assert.equal(new Headers(init?.headers).get("cookie"), `${SESSION_COOKIE_NAME}=${token}`);
      assert.equal(init?.cache, "no-store");
      assert.equal(init?.redirect, "error");
      return sessionResponse();
    },
  });

  assert.deepEqual(session, {
    state: "authenticated",
    tenantId: "11111111-1111-4111-8111-111111111111",
    role: "owner",
    authenticationLevel: "mfa",
    expiresAt: "2026-09-09T20:30:00.000Z",
    sessionVersion: 7,
    activeSiteId: "33333333-3333-4333-8333-333333333333",
  });
  assert.equal("userId" in session, false);
});

test("does not dispatch without one well-formed cookie", async () => {
  for (const sessionTokens of [[], ["short"], [token, token]]) {
    let dispatched = false;
    const session = await loadDashboardSession({
      sessionTokens,
      fetcher: async () => {
        dispatched = true;
        return sessionResponse();
      },
    });

    assert.equal(dispatched, false);
    assert.equal(session.state, sessionTokens.length === 0 ? "signed_out" : "invalid");
  }
});

test("maps unauthorized and unavailable responses without parsing their bodies", async () => {
  for (const [status, expected] of [
    [401, "signed_out"],
    [503, "unavailable"],
  ] as const) {
    const session = await loadDashboardSession({
      sessionTokens: [token],
      fetcher: async () => new Response("not JSON", { status }),
    });
    assert.equal(session.state, expected);
  }

  const unreachable = await loadDashboardSession({
    sessionTokens: [token],
    fetcher: async () => {
      throw new Error(`synthetic failure carrying ${token}`);
    },
  });
  assert.deepEqual(unreachable, { state: "unavailable" });
});

test("rejects malformed, expired, overlong, and over-lifetime session projections", async () => {
  const cases = [
    sessionResponse({ role_key: "platform-admin" }),
    sessionResponse({ expires_at: "2026-09-09T19:59:59.000Z" }),
    sessionResponse({ expires_at: "2026-09-10T20:00:01.000Z" }),
    sessionResponse({ schema_version: 1 }),
    sessionResponse({ session_version: true }),
    sessionResponse({ session_version: 0 }),
    sessionResponse({ active_site_id: "not-a-uuid" }),
    Response.json({ unexpected: true }),
    new Response("{}", {
      headers: { "content-type": "application/json", "content-length": "not-a-length" },
    }),
    new Response("{}", {
      headers: { "content-type": "application/json", "content-length": String(16 * 1024 + 1) },
    }),
    new Response("{}", { headers: { "content-type": "text/html" } }),
  ];

  for (const response of cases) {
    const session = await loadDashboardSession({
      sessionTokens: [token],
      now: fixedNow,
      fetcher: async () => response,
    });
    assert.deepEqual(session, { state: "invalid" });
  }
});

test("rejects unsafe API configuration without transmitting the session", async () => {
  let dispatched = false;
  const session = await loadDashboardSession({
    sessionTokens: [token],
    baseUrl: "https://operator:credential@signal-api.test",
    fetcher: async () => {
      dispatched = true;
      return sessionResponse();
    },
  });

  assert.equal(dispatched, false);
  assert.deepEqual(session, { state: "unavailable" });
});
