import assert from "node:assert/strict";
import test from "node:test";

import { POST } from "../app/actions/analyze-homepage/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const token = "a".repeat(43);
const csrf = "b".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";

function request(origin = "http://localhost:3000") {
  return new Request("http://localhost:3000/actions/analyze-homepage", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: `${TENANT_COOKIE_NAME}=${token}`,
      Origin: origin,
      "Sec-Fetch-Site": "same-origin",
    },
    body: new URLSearchParams({ site_id: siteId }),
  });
}

function observation() {
  return {
    schema_version: 1,
    intent_id: "22222222-2222-4222-8222-222222222222",
    evidence_id: "33333333-3333-4333-8333-333333333333",
    finding_id: null,
    command_id: "44444444-4444-4444-8444-444444444444",
    manifest_id: "55555555-5555-4555-8555-555555555555",
    origin: "https://docs.example.test",
    final_url: "https://docs.example.test/",
    http_status: 200,
    media_type: "text/html",
    title: "Acme documentation",
    heading: "Build with Acme",
    meta_description: "Documentation for Acme customers.",
    body_sha256: "c".repeat(64),
    observed_at: "2026-09-13T10:00:02Z",
    reused: false,
  };
}

test("runs same-origin verified homepage analysis and redirects to Pages", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Request[] = [];
  const responses = [
    Response.json({ csrf_token: csrf, schema_version: 1 }),
    Response.json({
      correlation_id: "page-write",
      finding: null,
      observation: observation(),
      schema_version: 1,
      site_id: siteId,
    }),
  ];
  try {
    globalThis.fetch = async (input, init) => {
      requests.push(new Request(input, init));
      return responses.shift() ?? new Response(null, { status: 500 });
    };
    const response = await POST(request());
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), "http://localhost:3000/pages?analysis=observed");
    const body = await requests[1]?.json() as Record<string, unknown>;
    assert.equal(body.schema_version, 1);
    assert.match(String(body.idempotency_key), /^[0-9a-f-]{36}$/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects cross-origin homepage analysis before contacting the API", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async () => {
      calls += 1;
      return new Response(null, { status: 500 });
    };
    const response = await POST(request("https://attacker.example"));
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), "http://localhost:3000/pages?analysis=rejected");
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("maps a known bounded read failure without claiming evidence", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    Response.json({ csrf_token: csrf, schema_version: 1 }),
    Response.json({ error: { code: "PAGE_OBSERVATION_FAILED" } }, { status: 502 }),
  ];
  try {
    globalThis.fetch = async () => responses.shift() ?? new Response(null, { status: 500 });
    const response = await POST(request());
    assert.equal(response.headers.get("location"), "http://localhost:3000/pages?analysis=unavailable");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
