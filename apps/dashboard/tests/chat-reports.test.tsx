import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ChatReportPreferences } from "../components/chat-report-preferences";
import { CHAT_CHANNELS, loadChatReports, mutateChatReports, type ChatReportState } from "../lib/chat-reports-api";
import { POST } from "../app/auth/chat-reports/route";

const token = "synthetic-" + "t".repeat(33), csrf = "synthetic-" + "c".repeat(33);
const site = "00000000-0000-4000-8000-000000000133";
const view = { channels: CHAT_CHANNELS.map(channel => ({ channel, availability: "available" as const, enabled: false })),
  history: [{ id: site, channel: "telegram" as const, category: "weekly_report", state: "unknown", attempt_count: 1, created_at: "2026-10-03T12:00:00+00:00", outcome: "unknown" }] };

test("chat report settings show preferences, empty and explicit unknown history", () => {
  const markup = renderToStaticMarkup(createElement(ChatReportPreferences, { state: view, siteId: site }));
  assert.equal((markup.match(/type="checkbox"/g) ?? []).length, 3);
  assert.match(markup, /Slack direct messages/); assert.match(markup, /Outcome unknown; not resent/);
  assert.match(markup, /Telegram private chat/); assert.doesNotMatch(markup, /Approve|callback/);
  const empty = renderToStaticMarkup(createElement(ChatReportPreferences, { state: { ...view, history: [] }, siteId: site }));
  assert.match(empty, /No chat report deliveries recorded/);
  const unavailable = renderToStaticMarkup(createElement(ChatReportPreferences, { state: { availability: "unavailable" }, siteId: site }));
  assert.match(unavailable, /unavailable for this deployment/); assert.doesNotMatch(unavailable, /checkbox/);
});

test("chat report unavailable destination can still opt out", () => {
  const state: ChatReportState = { ...view, channels: view.channels.map(row => ({ ...row, enabled: row.channel === "telegram", availability: "unavailable" })) };
  const markup = renderToStaticMarkup(createElement(ChatReportPreferences, { state, siteId: site }));
  assert.equal((markup.match(/disabled=""/g) ?? []).length, 2);
});

test("chat report API validates exact bounded projections and fails closed", async () => {
  assert.deepEqual(await loadChatReports({ tenantToken: token, siteId: site, fetcher: async () => Response.json(view) }), view);
  for (const data of [{ ...view, raw: "synthetic-secret" }, { ...view, channels: [] }, { ...view, history: Array(21).fill(view.history[0]) },
    { ...view, channels: [...view.channels].reverse() }, { ...view, history: [{ ...view.history[0], state: "delivered" }] },
    { ...view, history: [{ ...view.history[0], outcome: "<script>" }] }, { ...view, history: [{ ...view.history[0], created_at: "bad" }] }]) {
    assert.deepEqual(await loadChatReports({ tenantToken: token, siteId: site, fetcher: async () => Response.json(data) }), { availability: "unavailable" });
  }
});

test("chat report preference mutation preserves API CSRF and exact scope", async () => {
  const calls: RequestInit[] = [];
  const result = await mutateChatReports({ tenantToken: token, siteId: site, channel: "slack_dm", enabled: true,
    origin: "https://dashboard.example.invalid", fetcher: async (_input, init) => { calls.push(init ?? {});
      return Response.json(calls.length === 1 ? { schema_version: 1, csrf_token: csrf } : { state: "enabled" }); } });
  assert.equal(result, "enabled"); assert.equal(calls.length, 2);
  assert.equal(new Headers(calls[1].headers).get("X-CSRF-Token"), csrf);
  assert.deepEqual(JSON.parse(String(calls[1].body)), { channel: "slack_dm", enabled: true });
  assert.equal(await mutateChatReports({ tenantToken: token, siteId: site, channel: "telegram", enabled: true,
    origin: "https://dashboard.example.invalid", fetcher: async () => { throw Error("synthetic-failure"); } }), null);
});

test("chat report BFF refuses foreign origins before contacting API", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = "https://dashboard.example.invalid";
  const response = await POST(new Request("https://dashboard.example.invalid/auth/chat-reports", { method: "POST",
    headers: { Origin: "https://other.example.invalid", "Sec-Fetch-Site": "cross-site", "Content-Type": "application/json" }, body: JSON.stringify({ site_id: site, channel: "telegram", enabled: true }) }));
  assert.equal(response.status, 403);
});
