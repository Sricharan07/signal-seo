import assert from "node:assert/strict";
import test from "node:test";

import {
  loadStandingAuthorization,
  mutateStandingAuthorization,
} from "../lib/standing-authorization-api";

const siteId = "11111111-1111-4111-8111-111111111111";
const grantId = "22222222-2222-4222-8222-222222222222";
const releaseId = "33333333-3333-4333-8333-333333333333";
const eventId = "44444444-4444-4444-8444-444444444444";
const token = "synthetic-" + "t".repeat(33);
const csrf = "synthetic-" + "c".repeat(33);

async function withApiEnvironment(operation: () => Promise<void>) {
  const previous = process.env.SIGNAL_API_BASE_URL;
  process.env.SIGNAL_API_BASE_URL = "https://api.signal.test";
  try { await operation(); } finally {
    if (previous === undefined) delete process.env.SIGNAL_API_BASE_URL;
    else process.env.SIGNAL_API_BASE_URL = previous;
  }
}

test("reads only an exact current grant projection", async () => {
  await withApiEnvironment(async () => {
    const result = await loadStandingAuthorization({
      tenantToken: token, siteId,
      fetcher: async (input, init) => {
        assert.equal(new URL(String(input)).pathname, `/v1/sites/${siteId}/standing-authorization`);
        assert.equal(new Headers(init?.headers).get("cookie"), `__Host-signal_session=${token}`);
        return Response.json({
          schema_version: 1, site_id: siteId, state: "active", grant_id: grantId,
          recipe_release_ids: [releaseId], work_types: ["draft_patch"],
          thresholds: { draft_patch: 0.9 }, weekly_volume_caps: { draft_patch: 2 },
          weekly_total_cap: 2, weekly_spend_cents: 0, excluded_paths: ["/private"],
          starts_at: "2026-09-29T12:00:00Z", ends_at: "2026-10-06T12:00:00Z",
          recovery_window_hours: 24, restriction_event_id: null, durability: null,
        });
      },
    });
    assert.equal(result.state, "available");
    if (result.state === "available") assert.equal(result.grant.grantId, grantId);
  });
});

test("mutations use tenant CSRF and preserve pending durability", async () => {
  await withApiEnvironment(async () => {
    const calls: string[] = [];
    const result = await mutateStandingAuthorization({
      tenantToken: token, siteId, grantId,
      body: { schema_version: 1 }, dashboardOrigin: "https://dashboard.signal.test",
      fetcher: async (input, init) => {
        const path = new URL(String(input)).pathname;
        calls.push(path);
        if (path.endsWith("tenant-csrf")) return Response.json({ schema_version: 1, csrf_token: csrf });
        const headers = new Headers(init?.headers);
        assert.equal(headers.get("x-csrf-token"), csrf);
        assert.equal(headers.get("origin"), "https://dashboard.signal.test");
        return Response.json({
          schema_version: 1, site_id: siteId, grant_id: grantId,
          restriction_event_id: eventId, durability: "AUTHORITY_DURABILITY_PENDING",
        }, { status: 202 });
      },
    });
    assert.deepEqual(calls, [
      "/v1/session/tenant-csrf",
      `/v1/sites/${siteId}/standing-authorization/${grantId}/revoke`,
    ]);
    assert.deepEqual(result, {
      state: "revoked", restrictionEventId: eventId,
      durability: "AUTHORITY_DURABILITY_PENDING",
    });
  });
});

test("invalid grant identity never contacts the API", async () => {
  let calls = 0;
  const result = await mutateStandingAuthorization({
    tenantToken: token, siteId: "other", body: {},
    dashboardOrigin: "https://dashboard.signal.test",
    fetcher: async () => { calls++; return Response.json({}); },
  });
  assert.equal(result.state, "rejected");
  assert.equal(calls, 0);
});
