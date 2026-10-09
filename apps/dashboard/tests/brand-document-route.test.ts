import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { GET, POST } from "../app/actions/brand-documents/route";
import { POST as DELETE } from "../app/actions/brand-documents/delete/route";

const SITE = "11111111-1111-4111-8111-111111111111";
const DOCUMENT = "22222222-2222-4222-8222-222222222222";
const TOKEN = "t".repeat(43);
const ORIGIN = "http://localhost:3000";
const CSRF = "c".repeat(43);
const originalFetch = globalThis.fetch;
const originalOrigin = process.env.SIGNAL_DASHBOARD_ORIGIN;

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalOrigin === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN;
  else process.env.SIGNAL_DASHBOARD_ORIGIN = originalOrigin;
});

function request(path: string, body: unknown, origin = ORIGIN): Request {
  return new Request(`${ORIGIN}${path}`, {
    method: "POST",
    headers: {
      Cookie: `__Host-signal_session=${TOKEN}`,
      Origin: origin,
      "Sec-Fetch-Site": "same-origin",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

function document() {
  return {
    schema_version: 1, document_id: DOCUMENT, display_name: "brand.txt",
    media_type: "text/plain", created_at: "2026-09-29T00:00:00Z",
    supersedes_id: null, injection_signal: false, secret_signal: false,
    deleted: false, retained_for_evidence: true,
  };
}

test("same-origin upload fetches tenant CSRF and relays only bounded fields", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  const calls: Request[] = [];
  globalThis.fetch = async (input, init) => {
    const current = new Request(input, init);
    calls.push(current);
    if (current.url.endsWith("/v1/session/tenant-csrf")) {
      return Response.json({ schema_version: 1, csrf_token: CSRF });
    }
    assert.equal(current.headers.get("x-csrf-token"), CSRF);
    assert.equal(current.headers.get("cookie"), `__Host-signal_session=${TOKEN}`);
    assert.equal(current.headers.get("origin"), ORIGIN);
    return Response.json(document(), { status: 201 });
  };
  const response = await POST(request("/actions/brand-documents", {
    schema_version: 1, site_id: SITE, filename: "brand.txt",
    content_base64: "QnJhbmQ=", supersedes_id: null,
  }));
  assert.equal(response.status, 201);
  assert.equal(calls.length, 2);
  assert.deepEqual(await calls[1]?.json(), {
    schema_version: 1, filename: "brand.txt", content_base64: "QnJhbmQ=", supersedes_id: null,
  });
});

test("cross-origin and extra upload fields are rejected before API", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error(); };
  const body = { schema_version: 1, site_id: SITE, filename: "brand.txt",
    content_base64: "QnJhbmQ=", supersedes_id: null };
  assert.equal((await POST(request("/actions/brand-documents", body, "https://evil.test"))).status, 403);
  assert.equal((await POST(request("/actions/brand-documents", { ...body, extra: true }))).status, 403);
  assert.equal(calls, 0);
});

test("list and deletion stay bound to exact site and document", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  const calls: string[] = [];
  globalThis.fetch = async (input, init) => {
    const current = new Request(input, init);
    calls.push(current.url);
    if (current.url.endsWith("/v1/session/tenant-csrf")) {
      return Response.json({ schema_version: 1, csrf_token: CSRF });
    }
    if (current.method === "DELETE") {
      return Response.json({ schema_version: 1, outcome: "deleted_retained",
        retained_for_evidence: true });
    }
    return Response.json({ schema_version: 1, documents: [document()] });
  };
  const list = await GET(new Request(`${ORIGIN}/actions/brand-documents?site_id=${SITE}`, {
    headers: { Cookie: `__Host-signal_session=${TOKEN}` },
  }));
  assert.equal(list.status, 200);
  const removed = await DELETE(request("/actions/brand-documents/delete", {
    schema_version: 1, site_id: SITE, document_id: DOCUMENT,
  }));
  assert.equal(removed.status, 200);
  assert.equal(calls.length, 3);
  assert.ok(calls[2]?.endsWith(`/v1/sites/${SITE}/brand-documents/${DOCUMENT}`));
});
