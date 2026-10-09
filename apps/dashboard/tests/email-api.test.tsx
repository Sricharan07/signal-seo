import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { EmailPreferences } from "../components/email-preferences";
import { boundedEmailText, loadEmail, mutateEmail, type EmailState } from "../lib/email-api";

const siteId = "11111111-1111-4111-8111-111111111111";
const token = "synthetic-" + "t".repeat(33), csrf = "synthetic-" + "c".repeat(33);
const origin = "https://dashboard.example.invalid";
const view: EmailState = { availability: "available", address: "owner@example.invalid",
  can_enable: true, enabled: false, last_delivery_state: null };

test("email unconfigured and unverified states never offer a verification send", () => {
  const unavailable = renderToStaticMarkup(createElement(EmailPreferences, { state: { availability: "unavailable" }, siteId }));
  assert.match(unavailable, /Email delivery is unavailable/); assert.doesNotMatch(unavailable, /<input|<button/);
  const unverified = renderToStaticMarkup(createElement(EmailPreferences, { state: { ...view, availability: "identity_unverified", address: null, can_enable: false }, siteId }));
  assert.match(unverified, /has not verified/); assert.match(unverified, /disabled/);
  assert.doesNotMatch(unverified, /verification link|one-click|Send/);
});

test("email stale-session and unknown-acceptance states remain explicit", () => {
  const markup = renderToStaticMarkup(createElement(EmailPreferences, { state: { ...view, availability: "stale_session", can_enable: false, last_delivery_state: "unknown" }, siteId }));
  assert.match(markup, /Sign in again/); assert.match(markup, /not resent automatically/);
});

test("email read rejects extra secrets, malformed and failed provider responses", async () => {
  assert.deepEqual(await loadEmail({ tenantToken: token, siteId, fetcher: async () => Response.json(view) }), view);
  for (const data of [{ ...view, token }, { ...view, last_delivery_state: "delivered" }, { ...view, address: "<script>" }]) {
    assert.deepEqual(await loadEmail({ tenantToken: token, siteId, fetcher: async () => Response.json(data) }), { availability: "unavailable" });
  }
});

test("email preference mutation gets CSRF and forwards no verification claim", async () => {
  let calls = 0;
  const state = await mutateEmail({ tenantToken: token, siteId, origin, enabled: true, address: view.address,
    fetcher: async (_url, init) => {
      calls++;
      if (calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
      assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrf);
      assert.deepEqual(JSON.parse(String(init?.body)), { enabled: true, address: view.address });
      return Response.json({ state: "enabled" });
    } });
  assert.equal(calls, 2); assert.equal(state, "enabled");
});

test("email request and response streams are cancelled at their byte boundary", async () => {
  for (const maximum of [1024, 2048]) {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) { controller.enqueue(new Uint8Array(512)); },
      cancel() { cancelled = true; },
    });
    await assert.rejects(() => boundedEmailText(stream, maximum));
    assert.equal(cancelled, true);
  }
  await assert.rejects(() => boundedEmailText(new Response(new Uint8Array([0xff])).body, 1024));
});
