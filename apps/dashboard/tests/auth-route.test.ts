import assert from "node:assert/strict";
import test from "node:test";

import { POST as clearBrowserState } from "../app/auth/clear-browser-state/route";
import { authRelayResponse } from "../lib/auth-route";
import { clearBrowserAuthCookies } from "../lib/browser-auth";

test("relays a validated auth result with no-store and a 303 redirect", () => {
  const response = authRelayResponse(
    "http://localhost:3000",
    { state: "redirect", location: "/?auth=identity-ready", cookies: clearBrowserAuthCookies() },
    {
      not_ready: "callback-failed",
      rejected: "callback-rejected",
      failed: "callback-failed",
    },
  );
  assert.equal(response.status, 303);
  assert.equal(response.headers.get("location"), "http://localhost:3000/?auth=identity-ready");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.getSetCookie().length, 4);
});

test("local relays use the configured origin rather than a request host", () => {
  const response = authRelayResponse(
    "https://dashboard.signal.test",
    { state: "redirect", location: "/?auth=signed-in", cookies: [] },
    {
      not_ready: "organization-failed",
      rejected: "organization-rejected",
      failed: "organization-failed",
    },
  );
  assert.equal(response.headers.get("location"), "https://dashboard.signal.test/?auth=signed-in");
});

test("local pilot relays may reach a loopback HTTP identity provider", () => {
  const response = authRelayResponse(
    "http://127.0.0.1:3000",
    { state: "redirect", location: "http://127.0.0.1:49152/authorize?state=opaque", cookies: [] },
    {
      not_ready: "identity-not-ready",
      rejected: "request-rejected",
      failed: "identity-not-ready",
    },
  );
  assert.equal(
    response.headers.get("location"),
    "http://127.0.0.1:49152/authorize?state=opaque",
  );
});

test("HTTP relay destinations remain closed outside the loopback pilot", () => {
  const response = authRelayResponse(
    "http://127.0.0.1:3000",
    { state: "redirect", location: "http://identity.signal.test/authorize", cookies: [] },
    {
      not_ready: "identity-not-ready",
      rejected: "request-rejected",
      failed: "identity-not-ready",
    },
  );
  assert.equal(response.headers.get("location"), "http://127.0.0.1:3000/?auth=identity-not-ready");
});

test("unsafe relay destinations fail closed without setting cookies", () => {
  const response = authRelayResponse(
    "https://dashboard.signal.test",
    {
      state: "redirect",
      location: "javascript:alert(1)",
      cookies: clearBrowserAuthCookies(),
    },
    {
      not_ready: "callback-failed",
      rejected: "callback-rejected",
      failed: "callback-failed",
    },
  );
  assert.equal(
    response.headers.get("location"),
    "https://dashboard.signal.test/?auth=callback-failed",
  );
  assert.equal(response.headers.getSetCookie().length, 0);
});

test("clear-browser-state rejects missing origin proof and makes no redirect claim", async () => {
  const response = await clearBrowserState(
    new Request("http://localhost:3000/auth/clear-browser-state", { method: "POST" }),
  );
  assert.equal(response.status, 403);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.getSetCookie().length, 0);
});

test(
  "clear-browser-state removes all local authority only after exact same-origin proof",
  async () => {
    const response = await clearBrowserState(
      new Request("http://localhost:3000/auth/clear-browser-state", {
        method: "POST",
        headers: { Origin: "http://localhost:3000", "Sec-Fetch-Site": "same-origin" },
      }),
    );
    assert.equal(response.status, 303);
    assert.equal(
      response.headers.get("location"),
      "http://localhost:3000/?auth=browser-cleared",
    );
    assert.deepEqual(response.headers.getSetCookie(), clearBrowserAuthCookies());
  },
);
