import assert from "node:assert/strict";
import test from "node:test";

import { IDENTITY_COOKIE_NAME } from "../lib/browser-auth";
import { loadDashboardOrganizations } from "../lib/organization-api";

const token = "a".repeat(43);
const tenantId = "11111111-1111-4111-8111-111111111111";

function directoryResponse(organizations: unknown[]): Response {
  return Response.json({ schema_version: 1, organizations });
}

test("loads a bounded organization directory with only the identity cookie", async () => {
  const directory = await loadDashboardOrganizations({
    identityTokens: [token],
    baseUrl: "https://api.signal.test",
    fetcher: async (input, init) => {
      assert.equal(String(input), "https://api.signal.test/v1/organizations");
      assert.equal(
        new Headers(init?.headers).get("cookie"),
        `${IDENTITY_COOKIE_NAME}=${token}`,
      );
      assert.equal(init?.cache, "no-store");
      assert.equal(init?.redirect, "error");
      return directoryResponse([
        { tenant_id: tenantId, name: "Acme Search", role_key: "owner" },
      ]);
    },
  });

  assert.deepEqual(directory, {
    state: "available",
    organizations: [{ tenantId, name: "Acme Search", role: "owner" }],
  });
});

test("does not dispatch without one exact identity cookie", async () => {
  for (const identityTokens of [[], ["short"], [token, token]]) {
    let dispatched = false;
    const directory = await loadDashboardOrganizations({
      identityTokens,
      fetcher: async () => {
        dispatched = true;
        return directoryResponse([]);
      },
    });
    assert.equal(dispatched, false);
    assert.equal(directory.state, identityTokens.length === 0 ? "absent" : "invalid");
  }
});

test("maps expired, unavailable, and unexpected statuses explicitly", async () => {
  const expired = await loadDashboardOrganizations({
    identityTokens: [token],
    fetcher: async () => new Response(null, { status: 401 }),
  });
  assert.deepEqual(expired, { state: "expired" });

  const unavailable = await loadDashboardOrganizations({
    identityTokens: [token],
    fetcher: async () => {
      throw new Error(`synthetic failure carrying ${token}`);
    },
  });
  assert.deepEqual(unavailable, { state: "unavailable" });

  const invalid = await loadDashboardOrganizations({
    identityTokens: [token],
    fetcher: async () => new Response(null, { status: 502 }),
  });
  assert.deepEqual(invalid, { state: "invalid" });
});

test(
  "rejects malformed entries, duplicate tenants, excess entries, and oversized bodies",
  async () => {
    const valid = { tenant_id: tenantId, name: "Acme Search", role_key: "owner" };
    const cases = [
      directoryResponse([{ ...valid, role_key: "platform-admin" }]),
      directoryResponse([{ ...valid, name: "  Acme Search" }]),
      directoryResponse([valid, valid]),
      directoryResponse(Array.from({ length: 101 }, () => valid)),
      Response.json({ organizations: [], schema_version: 1, unexpected: true }),
      new Response("{}", {
        headers: {
          "content-type": "application/json",
          "content-length": String(64 * 1024 + 1),
        },
      }),
      new Response("{}", { headers: { "content-type": "text/html" } }),
    ];

    for (const response of cases) {
      const directory = await loadDashboardOrganizations({
        identityTokens: [token],
        fetcher: async () => response,
      });
      assert.deepEqual(directory, { state: "invalid" });
    }
  },
);

test("rejects unsafe API configuration without transmitting identity", async () => {
  let dispatched = false;
  const directory = await loadDashboardOrganizations({
    identityTokens: [token],
    baseUrl: "https://operator:credential@api.signal.test",
    fetcher: async () => {
      dispatched = true;
      return directoryResponse([]);
    },
  });
  assert.equal(dispatched, false);
  assert.deepEqual(directory, { state: "unavailable" });
});
