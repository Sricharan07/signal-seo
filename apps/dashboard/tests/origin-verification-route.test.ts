import assert from "node:assert/strict";
import test from "node:test";

import { POST as issueChallenge } from "../app/auth/origin-challenge/route";
import { POST as verifyOrigin } from "../app/auth/verify-origin/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const dashboardOrigin = "http://localhost:3000";
const tenantToken = "t".repeat(43);
const csrfToken = "c".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const challengeId = "22222222-2222-4222-8222-222222222222";
const requestId = "33333333-3333-4333-8333-333333333333";
const origin = "https://www.example.com";

function body(kind: "issue" | "verify", overrides: Record<string, unknown> = {}) {
  return JSON.stringify({
    schema_version: 1,
    site_id: siteId,
    ...(kind === "verify" ? { challenge_id: challengeId } : {}),
    origin,
    idempotency_key: requestId,
    ...overrides,
  });
}

function request(
  path: string,
  proposedBody: string,
  headers: Record<string, string> = {},
): Request {
  return new Request(`${dashboardOrigin}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
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

function challengeResponse() {
  return {
    schema_version: 1,
    site_id: siteId,
    challenge_id: challengeId,
    origin,
    proof_method: "http_well_known",
    proof_url:
      "https://www.example.com/.well-known/signal-site-verification.txt",
    proof_content: `signal-site-verification=${challengeId}\n`,
    issued_at: "2026-09-12T20:00:00Z",
    expires_at: "2026-09-12T20:30:00Z",
    replayed: false,
  };
}

function verificationResponse() {
  return {
    schema_version: 1,
    site_id: siteId,
    challenge_id: challengeId,
    origin,
    ownership_status: "verified",
    proof_method: "http_well_known",
    permitted_origins: [origin],
    verified_at: "2026-09-12T20:05:00Z",
    recheck_at: "2026-10-12T20:05:00Z",
    replayed: false,
  };
}

test("origin routes relay bounded same-origin requests and closed responses", async () => {
  const calls: string[] = [];
  await withRouteEnvironment(
    async (input) => {
      const path = new URL(String(input)).pathname;
      calls.push(path);
      if (path.endsWith("tenant-csrf")) {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      return path.endsWith("origin-challenges")
        ? Response.json(challengeResponse(), { status: 201 })
        : Response.json(verificationResponse());
    },
    async () => {
      const issued = await issueChallenge(
        request("/auth/origin-challenge", body("issue")),
      );
      assert.equal(issued.status, 201);
      assert.equal(issued.headers.get("cache-control"), "no-store");
      assert.equal(issued.headers.getSetCookie().length, 0);
      assert.equal((await issued.json()).state, "issued");

      const verified = await verifyOrigin(
        request("/auth/verify-origin", body("verify")),
      );
      assert.equal(verified.status, 200);
      assert.equal((await verified.json()).state, "verified");
    },
  );
  assert.deepEqual(calls, [
    "/v1/session/tenant-csrf",
    `/v1/sites/${siteId}/origin-challenges`,
    "/v1/session/tenant-csrf",
    `/v1/sites/${siteId}/verify-origin`,
  ]);
});

test("origin routes reject cross-origin, ambiguous, malformed, and oversized input", async () => {
  let dispatched = false;
  await withRouteEnvironment(
    async () => {
      dispatched = true;
      return new Response();
    },
    async () => {
      const cases = [
        request("/auth/origin-challenge", body("issue"), { Origin: "" }),
        request(
          "/auth/origin-challenge",
          body("issue", { unexpected: "field" }),
        ),
        request("/auth/origin-challenge", "not-json"),
        request(
          "/auth/origin-challenge",
          body("issue", { origin: `https://${"x".repeat(3100)}.test` }),
        ),
      ];
      for (const proposed of cases) {
        const response = await issueChallenge(proposed);
        assert.equal(response.status, 403);
        assert.deepEqual(await response.json(), { state: "rejected" });
      }
    },
  );
  assert.equal(dispatched, false);
});

test("origin routes preserve provider conflict and not-ready classes", async () => {
  for (const [providerStatus, routeStatus, state] of [
    [409, 409, "conflict"],
    [503, 503, "not_ready"],
    [500, 500, "failed"],
  ] as const) {
    await withRouteEnvironment(
      async (input) =>
        new URL(String(input)).pathname.endsWith("tenant-csrf")
          ? Response.json({ schema_version: 1, csrf_token: csrfToken })
          : new Response(null, { status: providerStatus }),
      async () => {
        const response = await verifyOrigin(
          request("/auth/verify-origin", body("verify")),
        );
        assert.equal(response.status, routeStatus);
        assert.deepEqual(await response.json(), { state });
      },
    );
  }
});
