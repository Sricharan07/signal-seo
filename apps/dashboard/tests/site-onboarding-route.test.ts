import assert from "node:assert/strict";
import test from "node:test";

import { POST } from "../app/auth/create-site/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const dashboardOrigin = "http://localhost:3000";
const tenantToken = "t".repeat(43);
const csrfToken = "c".repeat(43);
const tenantId = "11111111-1111-4111-8111-111111111111";
const userId = "22222222-2222-4222-8222-222222222222";
const siteId = "33333333-3333-4333-8333-333333333333";
const requestId = "44444444-4444-4444-8444-444444444444";

function body(overrides: Record<string, string> = {}): string {
  return new URLSearchParams({
    expected_session_version: "7",
    idempotency_key: requestId,
    name: "Acme Store",
    primary_origin: "https://www.example.com",
    reporting_currency: "USD",
    timezone: "America/Phoenix",
    ...overrides,
  }).toString();
}

function request(proposedBody: string, headers: Record<string, string> = {}): Request {
  return new Request(`${dashboardOrigin}/auth/create-site`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}`,
      Origin: dashboardOrigin,
      "Sec-Fetch-Site": "same-origin",
      ...headers,
    },
    body: proposedBody,
  });
}

async function withRouteEnvironment(
  fetcher: typeof globalThis.fetch,
  operation: () => Promise<void>,
): Promise<void> {
  const previousOrigin = process.env.SIGNAL_DASHBOARD_ORIGIN;
  const previousApi = process.env.SIGNAL_API_BASE_URL;
  const previousFetch = globalThis.fetch;
  process.env.SIGNAL_DASHBOARD_ORIGIN = dashboardOrigin;
  process.env.SIGNAL_API_BASE_URL = "http://127.0.0.1:8000";
  globalThis.fetch = fetcher;
  try {
    await operation();
  } finally {
    if (previousOrigin === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN;
    else process.env.SIGNAL_DASHBOARD_ORIGIN = previousOrigin;
    if (previousApi === undefined) delete process.env.SIGNAL_API_BASE_URL;
    else process.env.SIGNAL_API_BASE_URL = previousApi;
    globalThis.fetch = previousFetch;
  }
}

test("site onboarding route relays one bounded same-origin owner request", async () => {
  const calls: string[] = [];
  await withRouteEnvironment(
    async (input) => {
      const url = new URL(String(input));
      calls.push(url.pathname);
      if (url.pathname.endsWith("tenant-csrf")) {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      return Response.json({
        schema_version: 1,
        tenant_id: tenantId,
        user_id: userId,
        site_id: siteId,
        name: "Acme Store",
        primary_origin: "https://www.example.com",
        timezone: "America/Phoenix",
        reporting_currency: "USD",
        state: "onboarding",
        ownership_status: "unverified",
        session_version: 8,
        replayed: false,
      });
    },
    async () => {
      const response = await POST(request(body()));
      assert.equal(response.status, 303);
      assert.equal(response.headers.get("location"), `${dashboardOrigin}/?auth=site-created`);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.equal(response.headers.getSetCookie().length, 0);
    },
  );
  assert.deepEqual(calls, ["/v1/session/tenant-csrf", "/v1/sites"]);
});

test("site onboarding route rejects missing browser proof without API access", async () => {
  let dispatched = false;
  await withRouteEnvironment(
    async () => {
      dispatched = true;
      return new Response();
    },
    async () => {
      const response = await POST(request(body(), { Origin: "" }));
      assert.equal(response.status, 403);
      assert.equal((await response.json()).error.code, "BROWSER_REQUEST_REJECTED");
    },
  );
  assert.equal(dispatched, false);
});

test("site onboarding route rejects ambiguous or oversized form state", async () => {
  let dispatched = false;
  await withRouteEnvironment(
    async () => {
      dispatched = true;
      return new Response();
    },
    async () => {
      for (const proposed of [
        `${body()}&name=Second`,
        body({ unexpected: "field" }),
        body({ expected_session_version: "0" }),
        body({ name: "x".repeat(4096) }),
      ]) {
        const response = await POST(request(proposed));
        assert.equal(response.status, 303);
        assert.equal(
          response.headers.get("location"),
          `${dashboardOrigin}/?auth=site-create-rejected`,
        );
      }
    },
  );
  assert.equal(dispatched, false);
});

test("site onboarding route preserves closed conflict and unavailable states", async () => {
  for (const [status, notice] of [
    [409, "site-create-conflict"],
    [403, "site-create-rejected"],
    [503, "site-create-failed"],
  ] as const) {
    await withRouteEnvironment(
      async (input) =>
        new URL(String(input)).pathname.endsWith("tenant-csrf")
          ? Response.json({ schema_version: 1, csrf_token: csrfToken })
          : new Response(null, { status }),
      async () => {
        const response = await POST(request(body()));
        assert.equal(response.status, 303);
        assert.equal(response.headers.get("location"), `${dashboardOrigin}/?auth=${notice}`);
      },
    );
  }
});
