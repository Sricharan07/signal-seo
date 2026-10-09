import assert from "node:assert/strict";
import test from "node:test";

import { POST } from "../app/actions/start-snapshot/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const token = "a".repeat(43);
const csrf = "b".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const commandId = "22222222-2222-4222-8222-222222222222";

function request(origin = "http://localhost:3000") {
  return new Request("http://localhost:3000/actions/start-snapshot", {
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

test("starts one same-origin snapshot and redirects to visible work", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    Response.json({ csrf_token: csrf, schema_version: 1 }),
    Response.json(
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
      { status: 202 },
    ),
    Response.json({
      accepted_at: "2026-09-13T10:00:00Z",
      actor_user_id: "33333333-3333-4333-8333-333333333333",
      command_id: commandId,
      correlation_id: "work-read",
      kind: "site.snapshot",
      schema_version: 1,
      site_id: siteId,
      status: "accepted",
    }),
  ];
  try {
    globalThis.fetch = async () => responses.shift() ?? new Response(null, { status: 500 });
    const response = await POST(request());
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), "http://localhost:3000/work?run=started");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects cross-origin work before contacting the API", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async () => {
      calls += 1;
      return new Response(null, { status: 500 });
    };
    const response = await POST(request("https://attacker.example"));
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), "http://localhost:3000/work?run=rejected");
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
