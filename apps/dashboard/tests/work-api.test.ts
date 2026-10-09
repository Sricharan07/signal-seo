import assert from "node:assert/strict";
import test from "node:test";

import { loadDashboardWork, startDashboardSnapshot } from "../lib/work-api";

const token = "a".repeat(43);
const csrf = "b".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const commandId = "22222222-2222-4222-8222-222222222222";
const actorId = "33333333-3333-4333-8333-333333333333";
const manifestId = "44444444-4444-4444-8444-444444444444";

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function acceptedStatus() {
  return {
    schema_version: 1,
    command_id: commandId,
    site_id: siteId,
    actor_user_id: actorId,
    kind: "site.snapshot",
    status: "accepted",
    accepted_at: "2026-09-13T10:00:00Z",
    correlation_id: "work-read",
  };
}

test("loads the latest bounded site work projection", async () => {
  const requests: Request[] = [];
  const result = await loadDashboardWork({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async (input, init) => {
      requests.push(new Request(input, init));
      return json(acceptedStatus());
    },
  });

  assert.equal(result.state, "available");
  assert.equal(result.state === "available" && result.work.status, "accepted");
  assert.equal(requests[0]?.url, `https://api.signal.test/v1/sites/${siteId}/commands/latest`);
  assert.equal(requests[0]?.headers.get("cookie"), `__Host-signal_session=${token}`);
});

test("starts work through tenant CSRF and returns the committed latest projection", async () => {
  const requests: Request[] = [];
  const responses = [
    json({ csrf_token: csrf, schema_version: 1 }),
    json(
      {
        accepted_at: "2026-09-13T10:00:00Z",
        command_id: commandId,
        correlation_id: "work-start",
        reused: false,
        schema_version: 1,
        site_id: siteId,
        status: "accepted",
        status_url: `/v1/sites/${siteId}/commands/${commandId}`,
      },
      202,
    ),
    json(acceptedStatus()),
  ];
  const result = await startDashboardSnapshot({
    tenantToken: token,
    siteId,
    dashboardOrigin: "https://dashboard.signal.test",
    baseUrl: "https://api.signal.test",
    fetcher: async (input, init) => {
      requests.push(new Request(input, init));
      return responses.shift() ?? json({}, 500);
    },
  });

  assert.equal(result.state, "available");
  assert.equal(requests.length, 3);
  assert.equal(requests[1]?.method, "POST");
  assert.equal(requests[1]?.headers.get("origin"), "https://dashboard.signal.test");
  assert.equal(requests[1]?.headers.get("x-csrf-token"), csrf);
  assert.match(requests[1]?.headers.get("idempotency-key") ?? "", /^dashboard-/);
  assert.deepEqual(await requests[1]?.json(), { schema_version: 1 });
});

test("accepts a coherent terminal manifest and rejects fabricated terminal state", async () => {
  const complete = {
    ...acceptedStatus(),
    status: "succeeded",
    workflow_id: `signal:CrawlSite:${siteId}:${commandId}`,
    workflow_type: "CrawlSite",
    first_run_id: "55555555-5555-4555-8555-555555555555",
    workflow_state: "succeeded",
    projected_at: "2026-09-13T10:00:02Z",
    result_reference: {
      schema_version: 1,
      manifest_id: manifestId,
      manifest_sha256: "a".repeat(64),
      coverage: "complete",
      discovered_count: 1,
      terminal_count: 1,
      scope_version: 1,
      crawl_policy_version: 1,
    },
  };
  const valid = await loadDashboardWork({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () => json(complete),
  });
  const invalid = await loadDashboardWork({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () => json({ ...complete, result_reference: undefined }),
  });
  const invalidVersion = await loadDashboardWork({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () =>
      json({
        ...complete,
        result_reference: { ...complete.result_reference, scope_version: 0 },
      }),
  });

  assert.equal(valid.state, "available");
  assert.equal(
    valid.state === "available" && valid.work.result?.manifestId,
    manifestId,
  );
  assert.equal(
    valid.state === "available" && valid.work.result?.scopeVersion,
    1,
  );
  assert.equal(
    valid.state === "available" && valid.work.result?.crawlPolicyVersion,
    1,
  );
  assert.equal(invalid.state, "invalid");
  assert.equal(invalidVersion.state, "invalid");
});

test("fails closed before dispatch and maps absent work without parsing errors", async () => {
  let calls = 0;
  const malformed = await loadDashboardWork({
    tenantToken: "short",
    siteId,
    fetcher: async () => {
      calls += 1;
      return json({});
    },
  });
  const absent = await loadDashboardWork({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () => new Response(null, { status: 404 }),
  });

  assert.equal(malformed.state, "invalid");
  assert.equal(absent.state, "no_work");
  assert.equal(calls, 0);
});
