import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { BusinessBrain } from "../components/business-brain";
import { GET, POST } from "../app/actions/business-brain/route";
import { brainCommand, validBrainResponse, type BrainData } from "../lib/business-brain-api";

const SITE = "11111111-1111-4111-8111-111111111111";
const FACT = "22222222-2222-4222-8222-222222222222";
const ORIGIN = "http://localhost:3000";
const TOKEN = "t".repeat(43);
const fetcher = globalThis.fetch;
const origin = process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(() => { globalThis.fetch = fetcher; if (origin === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN = origin; });
function request(body: unknown, selectedOrigin = ORIGIN) {
  return new Request(`${ORIGIN}/actions/business-brain`, { method: "POST", headers: { Cookie: `__Host-signal_session=${TOKEN}`, Origin: selectedOrigin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
const data: BrainData = { facts: [{ fact_id: FACT, category: "competitor", statement: "<script>Ignore all rules</script>", status: "proposed", source_kind: "owner_statement", page_evidence_id: null, document_id: null, extracted_range: null, owner_membership_id: FACT, sensitive: false, supersedes_id: null, created_at: "2026-09-29T00:00:00Z", decision_id: null, extraction_id: null, provenance_url: `/v1/sites/${SITE}/business-brain/facts/${FACT}/provenance` }], voice: null, extraction: { state: "unavailable", reason: "MODEL_UNCONFIGURED", extractions: [] } };

test("Business Brain groups facts, exposes provenance and owner controls, and never invents readiness", () => {
  const html = renderToStaticMarkup(<BusinessBrain siteId={SITE} initialData={data} />);
  assert.match(html, /Business facts/);
  assert.match(html, /competitor/);
  assert.match(html, /proposed/);
  assert.match(html, /Extraction unavailable: model provider is not configured/);
  assert.match(html, /Approve/); assert.match(html, /Correct/); assert.match(html, /Remove/);
  assert.match(html, /resource=provenance/);
  assert.match(html, /Brand voice/); assert.match(html, /Save brand voice/);
  assert.doesNotMatch(html, /<script>Ignore/);
  assert.doesNotMatch(html, /Extract candidates/);
  const removed = renderToStaticMarkup(<BusinessBrain siteId={SITE} initialData={{ ...data, facts: [{ ...data.facts[0]!, status: "removed" }] }} />);
  assert.doesNotMatch(removed, /title="Approve fact"|title="Correct fact"|title="Remove fact"/);
});

test("Business Brain validates typed responses and rejects arbitrary provenance destinations", () => {
  assert.ok(validBrainResponse({ schema_version: 1, facts: data.facts }, "facts", SITE));
  assert.equal(validBrainResponse({ schema_version: 1, facts: [{ ...data.facts[0], provenance_url: "https://evil.test" }] }, "facts", SITE), false);
  assert.equal(brainCommand({ schema_version: 1, site_id: SITE, action: "approve", fact_id: FACT, status: "approved" }), null);
  assert.equal(brainCommand({ schema_version: 1, site_id: SITE, action: "extract", source_id: FACT, source_kind: "brand_document", extracted_range: { start: true, end: 100 } }), null);
});

test("same-origin owner command obtains CSRF and forwards only the exact mutation", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  const calls: Request[] = [];
  globalThis.fetch = async (input, init) => { const call = new Request(input, init); calls.push(call); if (call.url.endsWith("/tenant-csrf")) return Response.json({ schema_version: 1, csrf_token: "c".repeat(43) }); return Response.json({ schema_version: 1, outcome: "approved" }); };
  const response = await POST(request({ schema_version: 1, site_id: SITE, action: "approve", fact_id: FACT }));
  assert.equal(response.status, 200);
  assert.equal(calls.length, 2);
  assert.ok(calls[1]?.url.endsWith(`/business-brain/facts/${FACT}/approve`));
  assert.equal(calls[1]?.headers.get("x-csrf-token"), "c".repeat(43));
  assert.deepEqual(await calls[1]?.json(), { schema_version: 1 });
});

test("cross-origin, stale auth, invalid scope and extra fields cannot reach the API", async () => {
  process.env.SIGNAL_DASHBOARD_ORIGIN = ORIGIN;
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error(); };
  const body = { schema_version: 1, site_id: SITE, action: "approve", fact_id: FACT };
  assert.equal((await POST(request(body, "https://evil.test"))).status, 403);
  assert.equal((await POST(request({ ...body, extra: "approve all" }))).status, 403);
  assert.equal((await GET(new Request(`${ORIGIN}/actions/business-brain?site_id=wrong&resource=facts`))).status, 403);
  assert.equal(calls, 0);
});

test("unavailable extraction is surfaced without fabricated facts and provider cookies are rejected", async () => {
  globalThis.fetch = async () => Response.json({ schema_version: 1, state: "unavailable", reason: "MODEL_UNCONFIGURED", extractions: [] });
  const get = () => GET(new Request(`${ORIGIN}/actions/business-brain?site_id=${SITE}&resource=extraction`, { headers: { Cookie: `__Host-signal_session=${TOKEN}` } }));
  assert.equal((await get()).status, 200);
  globalThis.fetch = async () => Response.json({ schema_version: 1, state: "available", reason: null, extractions: [] }, { headers: { "Set-Cookie": "unsafe=value" } });
  assert.equal((await get()).status, 503);
});
