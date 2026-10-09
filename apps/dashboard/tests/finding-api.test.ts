import assert from "node:assert/strict";
import test from "node:test";

import {
  analyzeDashboardVerifiedHomepage,
  loadDashboardFindings,
  loadLatestPageObservation,
} from "../lib/finding-api";

const token = "a".repeat(43);
const csrf = "b".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const findingId = "22222222-2222-4222-8222-222222222222";
const evidenceId = "33333333-3333-4333-8333-333333333333";
const commandId = "44444444-4444-4444-8444-444444444444";
const manifestId = "55555555-5555-4555-8555-555555555555";

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function finding() {
  return {
    schema_version: 1,
    finding_id: findingId,
    evidence_id: evidenceId,
    command_id: commandId,
    manifest_id: manifestId,
    finding_key: "metadata.meta_description.missing",
    title: "Missing meta description",
    summary: "The synthetic page fixture does not contain a non-empty meta description.",
    resource_locator: "/fixture/missing-meta-description",
    severity: "medium",
    status: "open",
    confidence_class: "deterministic",
    source_kind: "synthetic_fixture",
    source_identifier: "fixture:local-pilot/missing-meta-description/v1",
    content_sha256: "a".repeat(64),
    evidence_observed_at: "2026-09-13T10:00:02Z",
    first_seen_at: "2026-09-13T10:00:02Z",
    last_seen_at: "2026-09-13T10:00:02Z",
    reused: false,
  };
}

function findingsResponse() {
  return {
    schema_version: 1,
    site_id: siteId,
    findings: [finding()],
    correlation_id: "finding-read",
  };
}

function observation(finding = findingId) {
  return {
    schema_version: 1,
    intent_id: "66666666-6666-4666-8666-666666666666",
    evidence_id: evidenceId,
    finding_id: finding,
    command_id: commandId,
    manifest_id: manifestId,
    origin: "https://docs.example.test",
    final_url: "https://docs.example.test/",
    http_status: 200,
    media_type: "text/html",
    title: "Acme documentation",
    heading: "Build with Acme",
    meta_description: null,
    body_sha256: "c".repeat(64),
    observed_at: "2026-09-13T10:00:02Z",
    reused: false,
  };
}

function verifiedFinding() {
  return {
    ...finding(),
    summary: "The verified homepage does not contain a non-empty meta description.",
    resource_locator: "https://docs.example.test/",
    source_kind: "verified_origin",
    source_identifier: "https://docs.example.test/",
  };
}

test("loads a strictly validated current finding projection", async () => {
  const requests: Request[] = [];
  const result = await loadDashboardFindings({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async (input, init) => {
      requests.push(new Request(input, init));
      return json(findingsResponse());
    },
  });

  assert.equal(result.state, "available");
  assert.equal(result.state === "available" && result.findings[0]?.findingId, findingId);
  assert.equal(result.state === "available" && result.findings[0]?.confidenceClass, "deterministic");
  assert.equal(requests[0]?.url, `https://api.signal.test/v1/sites/${siteId}/findings`);
  assert.equal(requests[0]?.headers.get("cookie"), `__Host-signal_session=${token}`);
});

test("loads and runs a strictly bound verified-homepage observation", async () => {
  const read = await loadLatestPageObservation({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () =>
      json({
        correlation_id: "page-read",
        observation: observation(),
        schema_version: 1,
        site_id: siteId,
      }),
  });
  assert.equal(read.state, "available");
  assert.equal(read.state === "available" && read.observation?.title, "Acme documentation");

  const requests: Request[] = [];
  const responses = [
    json({ csrf_token: csrf, schema_version: 1 }),
    json({
      correlation_id: "page-write",
      finding: verifiedFinding(),
      observation: observation(),
      schema_version: 1,
      site_id: siteId,
    }),
  ];
  const result = await analyzeDashboardVerifiedHomepage({
    tenantToken: token,
    siteId,
    idempotencyKey: "77777777-7777-4777-8777-777777777777",
    dashboardOrigin: "https://dashboard.signal.test",
    baseUrl: "https://api.signal.test",
    fetcher: async (input, init) => {
      requests.push(new Request(input, init));
      return responses.shift() ?? json({}, 500);
    },
  });
  assert.equal(result.state, "available");
  assert.equal(result.state === "available" && result.observation?.findingId, findingId);
  assert.deepEqual(await requests[1]?.json(), {
    idempotency_key: "77777777-7777-4777-8777-777777777777",
    schema_version: 1,
  });
});

test("rejects incoherent verified evidence and maps closed analysis failures", async () => {
  const invalid = await analyzeDashboardVerifiedHomepage({
    tenantToken: token,
    siteId,
    idempotencyKey: "77777777-7777-4777-8777-777777777777",
    dashboardOrigin: "https://dashboard.signal.test",
    baseUrl: "https://api.signal.test",
    fetcher: async (_input, init) =>
      init?.method === "POST"
        ? json({
            correlation_id: "page-write",
            finding: { ...verifiedFinding(), evidence_id: "88888888-8888-4888-8888-888888888888" },
            observation: observation(),
            schema_version: 1,
            site_id: siteId,
          })
        : json({ csrf_token: csrf, schema_version: 1 }),
  });
  const conflict = await analyzeDashboardVerifiedHomepage({
    tenantToken: token,
    siteId,
    idempotencyKey: "77777777-7777-4777-8777-777777777777",
    dashboardOrigin: "https://dashboard.signal.test",
    baseUrl: "https://api.signal.test",
    fetcher: async (_input, init) =>
      init?.method === "POST"
        ? json({ error: { code: "ORIGIN_NOT_VERIFIED" } }, 409)
        : json({ csrf_token: csrf, schema_version: 1 }),
  });
  assert.equal(invalid.state, "invalid");
  assert.equal(conflict.state, "conflict");
});

test("rejects malformed finding evidence and invalid lifecycle", async () => {
  const malformed = await loadDashboardFindings({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () =>
      json({
        ...findingsResponse(),
        findings: [{ ...finding(), content_sha256: "not-a-digest" }],
      }),
  });
  const reversed = await loadDashboardFindings({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () =>
      json({
        ...findingsResponse(),
        findings: [
          {
            ...finding(),
            first_seen_at: "2026-09-13T10:00:03Z",
            last_seen_at: "2026-09-13T10:00:02Z",
          },
        ],
      }),
  });
  assert.equal(malformed.state, "invalid");
  assert.equal(reversed.state, "invalid");
});

test("fails closed before dispatch and maps unavailable findings", async () => {
  let calls = 0;
  const malformed = await loadDashboardFindings({
    tenantToken: "short",
    siteId,
    fetcher: async () => {
      calls += 1;
      return json({});
    },
  });
  const unavailable = await loadDashboardFindings({
    tenantToken: token,
    siteId,
    baseUrl: "https://api.signal.test",
    fetcher: async () => new Response(null, { status: 503 }),
  });
  assert.equal(malformed.state, "invalid");
  assert.equal(unavailable.state, "unavailable");
  assert.equal(calls, 0);
});
