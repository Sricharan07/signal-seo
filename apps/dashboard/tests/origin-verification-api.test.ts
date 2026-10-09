import assert from "node:assert/strict";
import test from "node:test";

import {
  issueDashboardOriginChallenge,
  verifyDashboardOrigin,
} from "../lib/origin-verification-api";

const tenantToken = "t".repeat(43);
const csrfToken = "c".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const challengeId = "22222222-2222-4222-8222-222222222222";
const requestId = "33333333-3333-4333-8333-333333333333";
const origin = "https://www.example.com";
const dashboardOrigin = "https://dashboard.signal.test";
const issuedAt = "2026-09-12T20:00:00Z";
const expiresAt = "2026-09-12T20:30:00Z";
const verifiedAt = "2026-09-12T20:05:00Z";
const recheckAt = "2026-10-12T20:05:00Z";

function challengeBody(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    site_id: siteId,
    challenge_id: challengeId,
    origin,
    proof_method: "http_well_known",
    proof_url:
      "https://www.example.com/.well-known/signal-site-verification.txt",
    proof_content: `signal-site-verification=${challengeId}\n`,
    issued_at: issuedAt,
    expires_at: expiresAt,
    replayed: false,
    ...overrides,
  };
}

function verificationBody(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    site_id: siteId,
    challenge_id: challengeId,
    origin,
    ownership_status: "verified",
    proof_method: "http_well_known",
    permitted_origins: [origin],
    verified_at: verifiedAt,
    recheck_at: recheckAt,
    replayed: false,
    ...overrides,
  };
}

function csrfResponse(): Response {
  return Response.json({ schema_version: 1, csrf_token: csrfToken });
}

test("issues one exact origin challenge through tenant CSRF", async () => {
  const calls: string[] = [];
  const result = await issueDashboardOriginChallenge({
    tenantToken,
    siteId,
    origin,
    idempotencyKey: requestId,
    dashboardOrigin,
    baseUrl: "https://api.signal.test",
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      calls.push(url.pathname);
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("cookie"), `__Host-signal_session=${tenantToken}`);
      assert.equal(init?.cache, "no-store");
      assert.equal(init?.redirect, "manual");
      if (url.pathname.endsWith("tenant-csrf")) return csrfResponse();
      assert.equal(init?.method, "POST");
      assert.equal(headers.get("origin"), dashboardOrigin);
      assert.equal(headers.get("sec-fetch-site"), "same-origin");
      assert.equal(headers.get("x-csrf-token"), csrfToken);
      assert.deepEqual(JSON.parse(String(init?.body)), {
        schema_version: 1,
        idempotency_key: requestId,
        origin,
      });
      return Response.json(challengeBody(), { status: 201 });
    },
  });

  assert.deepEqual(calls, [
    "/v1/session/tenant-csrf",
    `/v1/sites/${siteId}/origin-challenges`,
  ]);
  assert.deepEqual(result, {
    state: "issued",
    challenge: {
      siteId,
      challengeId,
      origin,
      proofUrl:
        "https://www.example.com/.well-known/signal-site-verification.txt",
      proofContent: `signal-site-verification=${challengeId}\n`,
      issuedAt: "2026-09-12T20:00:00.000Z",
      expiresAt: "2026-09-12T20:30:00.000Z",
      replayed: false,
    },
  });
});

test("verifies the exact challenge and retains no browser authority", async () => {
  const result = await verifyDashboardOrigin({
    tenantToken,
    siteId,
    challengeId,
    origin,
    idempotencyKey: requestId,
    dashboardOrigin,
    baseUrl: "https://api.signal.test",
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("tenant-csrf")) return csrfResponse();
      assert.equal(url.pathname, `/v1/sites/${siteId}/verify-origin`);
      assert.deepEqual(JSON.parse(String(init?.body)), {
        schema_version: 1,
        idempotency_key: requestId,
        challenge_id: challengeId,
        origin,
      });
      return Response.json(verificationBody());
    },
  });

  assert.deepEqual(result, {
    state: "verified",
    verification: {
      siteId,
      challengeId,
      origin,
      verifiedAt: "2026-09-12T20:05:00.000Z",
      recheckAt: "2026-10-12T20:05:00.000Z",
      replayed: false,
    },
  });
});

test("rejects malformed local authority before network access", async () => {
  let calls = 0;
  const fetcher = async () => {
    calls += 1;
    return csrfResponse();
  };

  assert.deepEqual(
    await issueDashboardOriginChallenge({
      tenantToken,
      siteId: "not-a-site",
      origin,
      idempotencyKey: requestId,
      dashboardOrigin,
      fetcher,
    }),
    { state: "rejected" },
  );
  assert.deepEqual(
    await issueDashboardOriginChallenge({
      tenantToken,
      siteId,
      origin: "http://www.example.com",
      idempotencyKey: requestId,
      dashboardOrigin,
      fetcher,
    }),
    { state: "rejected" },
  );
  assert.deepEqual(
    await verifyDashboardOrigin({
      tenantToken,
      siteId,
      challengeId: "not-a-challenge",
      origin,
      idempotencyKey: requestId,
      dashboardOrigin,
      fetcher,
    }),
    { state: "rejected" },
  );
  assert.equal(calls, 0);
});

test("fails closed on malformed success bodies and cookie mutation", async () => {
  for (const response of [
    Response.json(challengeBody({ unexpected: true }), { status: 201 }),
    new Response(JSON.stringify(challengeBody()), {
      status: 201,
      headers: {
        "Content-Type": "application/json",
        "Set-Cookie": "unsafe=value",
      },
    }),
    Response.json(challengeBody({ expires_at: "2026-09-12T20:31:00Z" }), {
      status: 201,
    }),
  ]) {
    const result = await issueDashboardOriginChallenge({
      tenantToken,
      siteId,
      origin,
      idempotencyKey: requestId,
      dashboardOrigin,
      fetcher: async (input) =>
        new URL(String(input)).pathname.endsWith("tenant-csrf")
          ? csrfResponse()
          : response,
    });
    assert.deepEqual(result, { state: "failed" });
  }
});

test("maps external failure classes without leaking response bodies", async () => {
  for (const [status, state] of [
    [403, "rejected"],
    [409, "conflict"],
    [503, "not_ready"],
    [500, "failed"],
  ] as const) {
    const result = await verifyDashboardOrigin({
      tenantToken,
      siteId,
      challengeId,
      origin,
      idempotencyKey: requestId,
      dashboardOrigin,
      fetcher: async (input) =>
        new URL(String(input)).pathname.endsWith("tenant-csrf")
          ? csrfResponse()
          : new Response("private provider detail", { status }),
    });
    assert.deepEqual(result, { state });
  }
});
