import assert from "node:assert/strict";
import test from "node:test";

import {
  loadDashboardSiteDirectory,
  reconcileDashboardSiteDirectory,
} from "../lib/site-api";
import type { DashboardSession } from "../lib/session-api";

const token = "t".repeat(43);
const tenantId = "11111111-1111-4111-8111-111111111111";
const siteId = "22222222-2222-4222-8222-222222222222";

function directoryBody(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    tenant_id: tenantId,
    tenant_name: "Acme Search",
    sites: [
      {
        id: siteId,
        name: "Acme Docs",
        primary_origin: "https://docs.example.test",
        timezone: "UTC",
        reporting_currency: "USD",
        state: "onboarding",
        ownership_status: "unverified",
      },
    ],
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("loads one exact authorized site without retaining the session token", async () => {
  let request: Request | undefined;
  const result = await loadDashboardSiteDirectory({
    sessionTokens: [token],
    baseUrl: "https://signal-api.test",
    fetcher: async (input, init) => {
      request = new Request(input, init);
      return jsonResponse(directoryBody());
    },
  });

  assert.deepEqual(result, {
    state: "available",
    tenantId,
    tenantName: "Acme Search",
    sites: [
      {
        id: siteId,
        name: "Acme Docs",
        primaryOrigin: "https://docs.example.test",
        timezone: "UTC",
        reportingCurrency: "USD",
        state: "onboarding",
        ownershipStatus: "unverified",
      },
    ],
  });
  assert.equal(request?.url, "https://signal-api.test/v1/sites");
  assert.equal(request?.headers.get("cookie"), `__Host-signal_session=${token}`);
  assert.equal(JSON.stringify(result).includes(token), false);
});

test("accepts every closed origin ownership projection", async () => {
  for (const ownershipStatus of [
    "unverified",
    "verified",
    "reverification_required",
  ]) {
    const result = await loadDashboardSiteDirectory({
      sessionTokens: [token],
      fetcher: async () =>
        jsonResponse(
          directoryBody({
            sites: [
              {
                ...directoryBody().sites[0],
                ownership_status: ownershipStatus,
                state: ownershipStatus === "unverified" ? "onboarding" : "active",
              },
            ],
          }),
        ),
    });
    assert.equal(result.state, "available");
    if (result.state === "available") {
      assert.equal(result.sites[0]?.ownershipStatus, ownershipStatus);
    }
  }
});

test("does no I/O for absent, duplicate, or malformed session cookies", async () => {
  let calls = 0;
  const fetcher = async () => {
    calls += 1;
    return jsonResponse(directoryBody());
  };

  assert.deepEqual(await loadDashboardSiteDirectory({ sessionTokens: [], fetcher }), {
    state: "not_authenticated",
  });
  assert.deepEqual(
    await loadDashboardSiteDirectory({ sessionTokens: [token, token], fetcher }),
    { state: "invalid" },
  );
  assert.deepEqual(await loadDashboardSiteDirectory({ sessionTokens: ["short"], fetcher }), {
    state: "invalid",
  });
  assert.equal(calls, 0);
});

test("maps unauthorized, unavailable, transport, and unexpected status safely", async () => {
  const unauthorized = await loadDashboardSiteDirectory({
    sessionTokens: [token],
    fetcher: async () => jsonResponse({}, 401),
  });
  const unavailable = await loadDashboardSiteDirectory({
    sessionTokens: [token],
    fetcher: async () => jsonResponse({}, 503),
  });
  const transport = await loadDashboardSiteDirectory({
    sessionTokens: [token],
    fetcher: async () => {
      throw new Error("private network detail");
    },
  });
  const unexpected = await loadDashboardSiteDirectory({
    sessionTokens: [token],
    fetcher: async () => jsonResponse({}, 500),
  });

  assert.deepEqual(unauthorized, { state: "not_authenticated" });
  assert.deepEqual(unavailable, { state: "unavailable" });
  assert.deepEqual(transport, { state: "unavailable" });
  assert.deepEqual(unexpected, { state: "invalid" });
});

test("rejects extra fields, duplicate sites, and malformed site values", async () => {
  const validSite = directoryBody().sites[0];
  const cases = [
    directoryBody({ unexpected: true }),
    directoryBody({ tenant_id: "not-a-uuid" }),
    directoryBody({ tenant_name: " padded " }),
    directoryBody({ sites: Array.from({ length: 101 }, () => validSite) }),
    directoryBody({ sites: [validSite, validSite] }),
    directoryBody({ sites: [{ ...validSite, primary_origin: "http://docs.example.test" }] }),
    directoryBody({ sites: [{ ...validSite, primary_origin: "https://user@docs.example.test" }] }),
    directoryBody({ sites: [{ ...validSite, timezone: "Not/A_Real_Zone" }] }),
    directoryBody({ sites: [{ ...validSite, state: "archived" }] }),
    directoryBody({ sites: [{ ...validSite, ownership_status: "claimed" }] }),
    directoryBody({ sites: [{ ...validSite, internal: "leak" }] }),
  ];

  for (const body of cases) {
    const result = await loadDashboardSiteDirectory({
      sessionTokens: [token],
      fetcher: async () => jsonResponse(body),
    });
    assert.deepEqual(result, { state: "invalid" });
  }
});

test("rejects oversized, non-JSON, and malformed declared-length responses", async () => {
  const cases = [
    new Response("x".repeat(65 * 1024), { headers: { "content-type": "application/json" } }),
    new Response("{}", { headers: { "content-type": "text/html" } }),
    new Response("{}", {
      headers: { "content-type": "application/json", "content-length": "unknown" },
    }),
    new Response("{}", {
      headers: { "content-type": "application/json", "content-length": "65537" },
    }),
  ];

  for (const response of cases) {
    const result = await loadDashboardSiteDirectory({
      sessionTokens: [token],
      fetcher: async () => response,
    });
    assert.deepEqual(result, { state: "invalid" });
  }
});

test("rejects unsafe API configuration without transmitting the session", async () => {
  let dispatched = false;
  const result = await loadDashboardSiteDirectory({
    sessionTokens: [token],
    baseUrl: "https://operator:credential@signal-api.test",
    fetcher: async () => {
      dispatched = true;
      return jsonResponse(directoryBody());
    },
  });

  assert.equal(dispatched, false);
  assert.deepEqual(result, { state: "unavailable" });
});

test("reconciles the site directory against independently verified tenant authority", () => {
  const session: DashboardSession = {
    state: "authenticated",
    tenantId,
    role: "viewer",
    authenticationLevel: "primary",
    expiresAt: "2026-09-09T20:30:00.000Z",
    sessionVersion: 1,
    activeSiteId: null,
  };
  const matching = {
    state: "available" as const,
    tenantId,
    tenantName: "Acme Search",
    sites: [],
  };

  assert.deepEqual(reconcileDashboardSiteDirectory(session, matching), matching);
  assert.deepEqual(
    reconcileDashboardSiteDirectory(session, {
      ...matching,
      tenantId: "33333333-3333-4333-8333-333333333333",
    }),
    { state: "invalid" },
  );
  assert.deepEqual(
    reconcileDashboardSiteDirectory(
      { ...session, activeSiteId: "44444444-4444-4444-8444-444444444444" },
      matching,
    ),
    { state: "invalid" },
  );
  assert.deepEqual(reconcileDashboardSiteDirectory({ state: "signed_out" }, matching), {
    state: "not_authenticated",
  });
  assert.deepEqual(
    reconcileDashboardSiteDirectory(session, { state: "not_authenticated" }),
    { state: "invalid" },
  );
});
