import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { TelegramConnector } from "../components/telegram-connector";
import { DashboardView } from "../components/dashboard-view";
import { loadTelegram, mutateTelegram, telegramPairingUrl } from "../lib/telegram-api";

const siteId = "11111111-1111-4111-8111-111111111111";
const binding = "22222222-2222-4222-8222-222222222222";
const token = "synthetic-" + "t".repeat(33), csrf = "synthetic-" + "c".repeat(33);
const origin = "https://dashboard.example.test";

test("unconfigured Telegram has no pretend setup controls", () => {
  const html = renderToStaticMarkup(createElement(TelegramConnector, { state: { availability: "unavailable" }, siteId, owner: true }));
  assert.match(html, /Telegram is not configured/); assert.doesNotMatch(html, /<input|<button/);
});
test("bot setup uses a password field and has no high-risk chat limit", () => {
  const html = renderToStaticMarkup(createElement(TelegramConnector, { state: { availability: "unbound" }, siteId, owner: true }));
  assert.match(html, /type="password"/); assert.match(html, /Connect Telegram/); assert.doesNotMatch(html, />A3</);
});
test("failed binding remains revocable and does not imply readiness", () => {
  const html = renderToStaticMarkup(createElement(TelegramConnector, { state: { availability: "failed", binding_id: binding,
    bot_username: null, max_risk: 2, link_id: null, telegram_user_id: null }, siteId, owner: true }));
  assert.match(html, /Setup not confirmed/); assert.match(html, /Disconnect Telegram/); assert.doesNotMatch(html, /Create pairing link/);
});
test("pairing and unpairing follow membership state, not the mutable username", () => {
  const html = renderToStaticMarkup(createElement(TelegramConnector, { state: { availability: "bound", binding_id: binding,
    bot_username: "synthetic_signal_bot", max_risk: 2, link_id: siteId, telegram_user_id: "9000092" }, siteId, owner: false }));
  assert.match(html, /Paired with your Telegram account <code>9000092<\/code>/); assert.match(html, /Unpair account/); assert.doesNotMatch(html, /Disconnect Telegram/);
  const page = renderToStaticMarkup(createElement(DashboardView, {
    snapshot: { fetchedAt: "2026-09-29T00:00:00.000Z", connection: "connected", dependencies: "not_ready",
      inventory: "available", releaseStatus: "development", productionWritesEnabled: false, capabilities: [] },
    session: { state: "signed_out" }, sites: { state: "not_authenticated" }, organizations: { state: "absent" },
    authNotice: null, activeSection: "connectors", telegram: { availability: "bound", binding_id: binding,
      bot_username: "synthetic_signal_bot", max_risk: 2, link_id: siteId, telegram_user_id: "9000092" },
  }));
  assert.match(page, /Paired with your Telegram account <code>9000092<\/code>/); assert.doesNotMatch(page, /Not paired/);
});
test("pairing links reject other origins, routes, duplicate codes, and extra parameters", () => {
  const url = "https://t.me/synthetic_signal_bot?start=" + token;
  assert.equal(telegramPairingUrl(url), url);
  for (const bad of [url.replace("t.me", "evil.invalid"), url + "&start=" + token, url + "&admin=1", url + "#fragment", "https://t.me/../join?start=" + token]) assert.equal(telegramPairingUrl(bad), null);
});
test("read projection rejects extra secrets, unsafe user ids, and provider failure", async () => {
  const view = { availability: "bound", binding_id: binding, bot_username: "synthetic_signal_bot", max_risk: 2, link_id: null, telegram_user_id: null };
  assert.deepEqual(await loadTelegram({ tenantToken: token, siteId, fetcher: async () => Response.json(view) }), view);
  for (const response of [Response.json({ ...view, bot_token: "synthetic-secret" }), Response.json({ ...view, link_id: siteId, telegram_user_id: "9007199254740991" }), Response.json({}, { status: 503 })]) {
    assert.deepEqual(await loadTelegram({ tenantToken: token, siteId, fetcher: async () => response }), { availability: "unavailable" });
  }
});
test("bot credentials stay in a same-origin CSRF-protected body, never the API URL", async () => {
  let calls = 0;
  const result = await mutateTelegram({ tenantToken: token, siteId, origin, command: { operation: "install", bot_token: "synthetic-telegram-secret", max_risk: 2 },
    fetcher: async (input, init) => {
      calls++; assert.doesNotMatch(String(input), /synthetic-telegram-secret/); assert.equal(init?.redirect, "error");
      if (calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("x-csrf-token"), csrf); assert.equal(headers.get("origin"), origin);
      assert.match(String(init?.body), /synthetic-telegram-secret/);
      return Response.json({ binding_id: binding });
    } });
  assert.deepEqual(result, { binding_id: binding }); assert.equal(calls, 2);
});
test("mutation response cannot echo secrets or untrusted pairing origins", async () => {
  for (const result of [{ binding_id: binding, bot_token: "synthetic-secret" }, { pairing_id: binding, pairing_url: "https://evil.invalid/?start=" + token, expires_in_seconds: 300 }]) {
    let calls = 0;
    assert.equal(await mutateTelegram({ tenantToken: token, siteId, origin, command: { operation: "install" }, fetcher: async () => ++calls === 1 ? Response.json({ schema_version: 1, csrf_token: csrf }) : Response.json(result) }), null);
  }
});
