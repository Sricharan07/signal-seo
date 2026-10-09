import assert from "node:assert/strict";
import test from "node:test";

import { POST } from "../app/auth/select-site/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const dashboardOrigin = "http://localhost:3000";
const tenantToken = "t".repeat(43);
const csrfToken = "c".repeat(43);
const tenantId = "11111111-1111-4111-8111-111111111111";
const userId = "22222222-2222-4222-8222-222222222222";
const siteId = "33333333-3333-4333-8333-333333333333";

function request(body: string, headers: Record<string, string> = {}): Request {
  return new Request(`${dashboardOrigin}/auth/select-site`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}`,
      Origin: dashboardOrigin,
      "Sec-Fetch-Site": "same-origin",
      ...headers,
    },
    body,
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

test("site route accepts one bounded same-origin form and relays selection", async () => {
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
        session_version: 8,
        changed: true,
      });
    },
    async () => {
      const response = await POST(request(`site_id=${siteId}&session_version=7`));
      assert.equal(response.status, 303);
      assert.equal(response.headers.get("location"), `${dashboardOrigin}/?auth=site-selected`);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.equal(response.headers.getSetCookie().length, 0);
    },
  );
  assert.deepEqual(calls, ["/v1/session/tenant-csrf", "/v1/session/site"]);
});

test("site route rejects missing origin proof without calling the API", async () => {
  let dispatched = false;
  await withRouteEnvironment(
    async () => {
      dispatched = true;
      return new Response();
    },
    async () => {
      const response = await POST(
        request(`site_id=${siteId}&session_version=7`, { Origin: "" }),
      );
      assert.equal(response.status, 403);
      assert.equal((await response.json()).error.code, "BROWSER_REQUEST_REJECTED");
    },
  );
  assert.equal(dispatched, false);
});

test("site route rejects ambiguous form state and preserves conflicts", async () => {
  let dispatched = false;
  await withRouteEnvironment(
    async () => {
      dispatched = true;
      return new Response();
    },
    async () => {
      const response = await POST(
        request(`site_id=${siteId}&site_id=${tenantId}&session_version=7`),
      );
      assert.equal(response.status, 303);
      assert.equal(response.headers.get("location"), `${dashboardOrigin}/?auth=site-rejected`);
    },
  );
  assert.equal(dispatched, false);

  await withRouteEnvironment(
    async (input) =>
      new URL(String(input)).pathname.endsWith("tenant-csrf")
        ? Response.json({ schema_version: 1, csrf_token: csrfToken })
        : new Response(null, { status: 409 }),
    async () => {
      const response = await POST(request(`site_id=${siteId}&session_version=7`));
      assert.equal(response.status, 303);
      assert.equal(response.headers.get("location"), `${dashboardOrigin}/?auth=site-conflict`);
    },
  );
});
