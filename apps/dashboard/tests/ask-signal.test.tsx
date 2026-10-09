import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";

import { GET, POST } from "../app/actions/assistant/route";
import { AskSignalPage } from "../components/ask-signal";
import { assistantCommand, safeHref, validConversation, validOverview, type AssistantConversation, type AssistantOverview } from "../lib/assistant-api";

const SITE = "11111111-1111-4111-8111-111111111111";
const CONVERSATION = "22222222-2222-4222-8222-222222222222";
const REQUEST = "33333333-3333-4333-8333-333333333333";
const MEMORY = "44444444-4444-4444-8444-444444444444";
const ORIGIN = "http://localhost:3000";
const TOKEN = "t".repeat(43);
const fetcher = globalThis.fetch;
const origin = process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(() => { globalThis.fetch = fetcher; if (origin === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN = origin; });
function post(body: unknown, selectedOrigin = ORIGIN) {
  return new Request(`${ORIGIN}/actions/assistant`, { method: "POST", headers: { Cookie: `__Host-signal_session=${TOKEN}`, Origin: selectedOrigin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
const get = (query: string) => GET(new Request(`${ORIGIN}/actions/assistant?${query}`, { headers: { Cookie: `__Host-signal_session=${TOKEN}` } }));

const overview: AssistantOverview = {
  schema_version: 1, availability: "available", reason: null,
  suggestions: [{ text: "Why is “Missing meta description” waiting on me?" }],
  conversations: [{ conversation_id: CONVERSATION, title: "Pricing page", updated_at: "2026-10-04T09:00:00Z", message_count: 2 }],
};
const owner = { message_id: "55555555-5555-4555-8555-555555555555", role: "owner" as const, text: "Why is pricing waiting?", created_at: "2026-10-04T09:00:00Z", state: "answered" as const, citations: [], actions: [], remembered: [] };
const reply = {
  message_id: "66666666-6666-4666-8666-666666666666", role: "signal" as const, created_at: "2026-10-04T09:00:02Z", state: "answered" as const,
  text: "Its title is just “Pricing”. Pricing pages always come to you, so it’s in your Inbox.",
  citations: [{ kind: "revision" as const, id: "77777777-7777-4777-8777-777777777777", label: "Missing meta description", href: "/approvals?revision=77777777-7777-4777-8777-777777777777" }],
  actions: [{ intent: "open_revision" as const, label: "Open it", href: "/approvals?revision=77777777-7777-4777-8777-777777777777", confirm: null }],
  remembered: [{ memory_id: MEMORY, text: "Pricing changes always need the owner." }],
};
const conversation: AssistantConversation = { schema_version: 1, conversation_id: CONVERSATION, title: "Pricing page", messages: [owner, reply] };

test("links from answers stay inside the dashboard", () => {
  for (const href of ["/", "/approvals?revision=x", "/settings#memory-title", "/chat?conversation=1"]) assert.equal(safeHref(href), true, href);
  for (const href of ["https://evil.test/", "//evil.test", "/\\evil.test", "javascript:alert(1)", "/approvals/../../x", "/ approvals", "approvals", "/a/b"]) assert.equal(safeHref(href), false, href);
  assert.equal(validConversation({ ...conversation, messages: [{ ...reply, actions: [{ ...reply.actions[0], href: "https://evil.test" }] }] }), false);
  assert.equal(validConversation({ ...conversation, messages: [{ ...owner, citations: reply.citations }] }), false);
  assert.equal(validOverview({ ...overview, suggestions: [1, 2, 3, 4, 5].map((n) => ({ text: `q${n}` })) }), false);
  assert.equal(validOverview(overview), true);
});

test("only the four owner commands are relayed, with exact fields", () => {
  assert.deepEqual(assistantCommand({ schema_version: 1, site_id: SITE, action: "send", conversation_id: CONVERSATION, request_id: REQUEST, text: "Why?" })?.body, { schema_version: 1, request_id: REQUEST, text: "Why?" });
  assert.equal(assistantCommand({ schema_version: 1, site_id: SITE, action: "send", conversation_id: CONVERSATION, request_id: REQUEST, text: "x".repeat(2001) }), null);
  assert.equal(assistantCommand({ schema_version: 1, site_id: SITE, action: "send", conversation_id: CONVERSATION, request_id: REQUEST, text: "   " }), null);
  assert.equal(assistantCommand({ schema_version: 1, site_id: SITE, action: "remember", request_id: REQUEST, kind: "summary", text: "x" }), null);
  assert.equal(assistantCommand({ schema_version: 1, site_id: SITE, action: "forget", memory_id: MEMORY, extra: true }), null);
  assert.equal(assistantCommand({ schema_version: 1, site_id: SITE, action: "merge", request_id: REQUEST }), null);
  assert.equal(assistantCommand({ schema_version: 1, site_id: SITE, action: "forget", memory_id: MEMORY })?.path, `memory/${MEMORY}/forget`);
});

test("a question is relayed with the tenant CSRF proof and the reply is validated", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  const calls: Request[] = [];
  globalThis.fetch = async (input, init) => {
    const call = new Request(input, init); calls.push(call);
    if (call.url.endsWith("/tenant-csrf")) return Response.json({ schema_version: 1, csrf_token: "c".repeat(43) });
    return Response.json({ schema_version: 1, owner_message: owner, reply });
  };
  const response = await POST(post({ schema_version: 1, site_id: SITE, action: "send", conversation_id: CONVERSATION, request_id: REQUEST, text: "Why is pricing waiting?" }));
  assert.equal(response.status, 200);
  const relayed = calls[1];
  assert.equal(new URL(relayed.url).pathname, `/v1/sites/${SITE}/assistant/conversations/${CONVERSATION}/messages`);
  assert.equal(relayed.headers.get("x-csrf-token"), "c".repeat(43));
  assert.deepEqual(await relayed.json(), { schema_version: 1, request_id: REQUEST, text: "Why is pricing waiting?" });

  globalThis.fetch = async (input) => String(input).endsWith("/tenant-csrf")
    ? Response.json({ schema_version: 1, csrf_token: "c".repeat(43) })
    : Response.json({ schema_version: 1, owner_message: owner, reply: { ...reply, citations: [{ ...reply.citations[0], href: "//evil.test" }] } });
  assert.equal((await POST(post({ schema_version: 1, site_id: SITE, action: "send", conversation_id: CONVERSATION, request_id: REQUEST, text: "Why?" }))).status, 503);
});

test("cross-site, malformed and unknown requests never reach the API", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error(); };
  const body = { schema_version: 1, site_id: SITE, action: "forget", memory_id: MEMORY };
  assert.equal((await POST(post(body, "https://evil.test"))).status, 403);
  assert.equal((await POST(post({ ...body, action: "approve" }))).status, 403);
  assert.equal((await get(`site_id=${SITE}&resource=conversation&conversation_id=../memory`)).status, 403);
  assert.equal((await get(`site_id=wrong&resource=overview`)).status, 403);
  assert.equal(calls, 0);
});

test("API answers are passed through only when they match the contract", async () => {
  globalThis.fetch = async () => Response.json(overview);
  assert.equal((await get(`site_id=${SITE}&resource=overview`)).status, 200);
  globalThis.fetch = async () => Response.json({ ...overview, availability: "maybe" });
  assert.equal((await get(`site_id=${SITE}&resource=overview`)).status, 503);
  globalThis.fetch = async () => Response.json(overview, { headers: { "Set-Cookie": "unsafe=value" } });
  assert.equal((await get(`site_id=${SITE}&resource=overview`)).status, 503);
  globalThis.fetch = async () => Response.json({ error: { code: "budget_exhausted", message: "This month’s question budget is used up.", retryable: false, correlation_id: "c1" } }, { status: 429 });
  assert.equal((await get(`site_id=${SITE}&resource=overview`)).status, 429);
});

test("the Ask Signal page shows kept conversations, cited answers and memories", () => {
  const html = renderToStaticMarkup(<AskSignalPage siteId={SITE} siteName="Acme Docs" overview={overview} conversation={conversation} telegramPaired={false}
    memories={[{ memory_id: MEMORY, kind: "preference", text: "Pricing changes always need the owner.", created_at: "2026-10-04T09:00:02Z", source_conversation_id: CONVERSATION, source_message_id: reply.message_id }]} />);
  assert.match(html, /Ask about your site\./);
  assert.match(html, /1 conversation · Telegram not paired/);
  assert.match(html, /href="\/chat\?conversation=22222222-2222-4222-8222-222222222222" aria-current="page"/);
  assert.match(html, /<a href="\/approvals\?revision=77777777-7777-4777-8777-777777777777">Missing meta description<\/a>/);
  assert.match(html, /class="primary-command c-btn-sm" href="\/approvals\?revision=[^"]+">Open it/);
  assert.match(html, /Remembered: Pricing changes always need the owner\./);
  assert.match(html, /What Signal remembers/);
  assert.match(html, />Forget</);
  assert.match(html, /Why is “Missing meta description” waiting on me\?/);
});

test("an unavailable assistant says why and keeps the composer closed", () => {
  const html = renderToStaticMarkup(<AskSignalPage siteId={SITE} siteName="Acme Docs" overview={{ ...overview, availability: "model_unconfigured", reason: null, conversations: [] }} conversation={null} memories={null} telegramPaired />);
  assert.match(html, /needs a language model, and none is set up for this site yet/);
  assert.match(html, /<input[^>]*aria-label="Message"[^>]*disabled=""/);
  assert.doesNotMatch(html, /c-sug/);
  assert.match(html, /Memories can’t be read right now\. Nothing has been forgotten\./);
  const down = renderToStaticMarkup(<AskSignalPage siteId={SITE} siteName="Acme Docs" overview={null} conversation={null} memories={[]} telegramPaired={false} />);
  assert.match(down, /can’t be reached right now/);
});
