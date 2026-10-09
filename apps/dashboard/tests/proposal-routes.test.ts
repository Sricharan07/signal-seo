import assert from "node:assert/strict";
import test from "node:test";

import { POST as decide } from "../app/actions/decide-proposal/route";
import { POST as prepare } from "../app/actions/prepare-proposal/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const token = "t".repeat(43);
const csrf = "c".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const approvalRequestId = "44444444-4444-4444-8444-444444444444";
const decisionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const revisionSha256 = "a".repeat(64);

function request(path: string, body: URLSearchParams, origin = "http://localhost:3000") {
  return new Request(`http://localhost:3000${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: `${TENANT_COOKIE_NAME}=${token}`,
      Origin: origin,
      "Sec-Fetch-Site": "same-origin",
    },
    body,
  });
}

test("prepares from chat and decides from approvals through same-origin forms", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    Response.json({ schema_version: 1, csrf_token: csrf }),
    new Response(null, { status: 503 }),
    Response.json({ schema_version: 1, csrf_token: csrf }),
    new Response(null, { status: 409 }),
  ];
  try {
    globalThis.fetch = async () => responses.shift() ?? new Response(null, { status: 500 });
    const prepared = await prepare(
      request("/actions/prepare-proposal", new URLSearchParams({ site_id: siteId })),
    );
    const decided = await decide(
      request("/actions/decide-proposal", new URLSearchParams({
        site_id: siteId,
        approval_request_id: approvalRequestId,
        revision_sha256: revisionSha256,
        decision_id: decisionId,
        decision: "approved",
      })),
    );
    assert.equal(prepared.status, 303);
    assert.equal(prepared.headers.get("location"), "http://localhost:3000/chat?proposal=unavailable");
    assert.equal(decided.status, 303);
    assert.equal(decided.headers.get("location"), "http://localhost:3000/approvals?approval=conflict");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects cross-origin and ambiguous proposal forms before API access", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async () => {
      calls += 1;
      return new Response(null, { status: 500 });
    };
    const crossOrigin = await prepare(
      request(
        "/actions/prepare-proposal",
        new URLSearchParams({ site_id: siteId }),
        "https://attacker.example",
      ),
    );
    const ambiguous = new URLSearchParams({ site_id: siteId });
    ambiguous.append("site_id", siteId);
    const duplicate = await prepare(request("/actions/prepare-proposal", ambiguous));
    assert.equal(crossOrigin.headers.get("location"), "http://localhost:3000/chat?proposal=request_rejected");
    assert.equal(duplicate.headers.get("location"), "http://localhost:3000/chat?proposal=request_rejected");
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
