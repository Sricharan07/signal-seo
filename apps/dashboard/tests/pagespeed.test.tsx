import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { PageSpeed } from "../components/pagespeed";
import { validPageSpeed, type PageSpeedData } from "../lib/pagespeed-api";
import { GET } from "../app/actions/pagespeed/route";
import { observedSpeed } from "./pagespeed-fixture";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const fetcher = globalThis.fetch;
afterEach(() => { globalThis.fetch = fetcher; });

const site = "b077d5ef-5962-49b0-b953-7242b26cb5fe";
const sample = "1c6d48eb-2937-4389-b878-27de51688c23";
const empty: PageSpeedData = { schema_version: 1, site_id: site, outcome: "found", samples: [], daily_request_cap: 4, weekly_page_cap: 5, local_lighthouse: "unavailable" };
test("empty and loading performance states make no field claims", () => {
  assert.equal(validPageSpeed(empty, site), true);
  const html = renderToStaticMarkup(<PageSpeed siteId={site} initialData={empty} />);
  assert.match(html, /No PageSpeed observations/);
  assert.match(html, /Local Lighthouse: unavailable/);
  assert.doesNotMatch(html, /p75|Performance score:/);
  assert.match(renderToStaticMarkup(<PageSpeed siteId={site} />), /Loading performance observations/);
});
test("scheduled, failure and unknown outcomes remain distinct", () => {
  for (const state of ["scheduled", "unavailable", "outcome_unknown"] as const) {
    const data: PageSpeedData = { ...empty, samples: [{ sample_id: sample, url: "https://example.invalid/product", strategy: "mobile", week_start: "2026-09-28", selection_source: "crawl_order", observation: null, reason: state === "unavailable" ? "PSI_RATE_LIMITED" : null, state }] };
    assert.equal(validPageSpeed(data, site), true);
    const html = renderToStaticMarkup(<PageSpeed siteId={site} initialData={data} />);
    assert.match(html, state === "outcome_unknown" ? /Provider outcome unknown/ : state === "unavailable" ? /Psi rate limited/ : /Scheduled/);
    assert.doesNotMatch(html, /p75/);
  }
});
test("closed scope and readiness projections reject extra fields", () => {
  for (const value of [{ ...empty, site_id: sample }, { ...empty, local_lighthouse: "available" }, { ...empty, key: "synthetic-pagespeed-key" }, { ...empty, daily_request_cap: 100 }]) assert.equal(validPageSpeed(value, site), false);
});
test("observed page and origin field data stay distinct from lab diagnostics", () => {
  assert.equal(validPageSpeed(observedSpeed, site), true);
  const html = renderToStaticMarkup(<PageSpeed siteId={site} initialData={observedSpeed} />);
  assert.match(html, /Field \/ Page/);
  assert.match(html, /Field \/ Origin/);
  assert.match(html, /Lab \/ Lighthouse 12.8.2/);
  assert.match(html, /4200 ms \(Poor\)/);
  assert.match(html, /Performance score: 84/);
  assert.match(html, /Collection period: 2026-09-04 to 2026-10-01/);
  assert.match(html, /Unavailable: Insufficient field data/);
  assert.match(html, /Response SHA-256/);
  assert.doesNotMatch(html.split('aria-label="Lab / Lighthouse"')[1], /INP/);
});
test("malformed nested observations and unavailable estimates are rejected", () => {
  for (const change of [
    (data: PageSpeedData) => { data.samples[0].observation!.field_url.source = "origin"; },
    (data: PageSpeedData) => { data.samples[0].observation!.field_origin.metrics.lcp.value = 2000; },
    (data: PageSpeedData) => { data.samples[0].observation!.lab.performance_score = 2; },
    (data: PageSpeedData) => { data.samples[0].observation!.field_url.collection_period.last_date = "not-a-date"; },
    (data: PageSpeedData) => { data.samples[0].observation!.findings[0].source_id = site; },
  ]) {
    const data = structuredClone(observedSpeed); change(data);
    assert.equal(validPageSpeed(data, site), false);
  }
});
test("BFF rejects anonymous, duplicate-cookie and extra-parameter reads", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error(); };
  for (const [url, cookie] of [[`http://localhost/actions/pagespeed?site_id=${site}`, ""], [`http://localhost/actions/pagespeed?site_id=${site}&key=synthetic-key`, `${TENANT_COOKIE_NAME}=${"s".repeat(43)}`], [`http://localhost/actions/pagespeed?site_id=${site}`, `${TENANT_COOKIE_NAME}=a; ${TENANT_COOKIE_NAME}=b`]]) {
    assert.equal((await GET(new Request(url, { headers: { cookie } }))).status, 403);
  }
  assert.equal(calls, 0);
});
test("BFF forwards only the exact owner cookie and closed read projection", async () => {
  const token = "s".repeat(43);
  const calls: Request[] = [];
  globalThis.fetch = async (input, init) => { calls.push(new Request(input, init)); return Response.json(observedSpeed); };
  const response = await GET(new Request(`http://localhost/actions/pagespeed?site_id=${site}`, { headers: { Cookie: `${TENANT_COOKIE_NAME}=${token}; unrelated=ignored` } }));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), observedSpeed);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");
  assert.equal(new URL(calls[0].url).pathname, `/v1/sites/${site}/performance`);
  assert.equal(calls[0].headers.get("cookie"), `${TENANT_COOKIE_NAME}=${token}`);
  assert.equal(calls[0].redirect, "error");
});
test("BFF masks provider failure, rejects cookies and malformed response data", async () => {
  const get = () => GET(new Request(`http://localhost/actions/pagespeed?site_id=${site}`, { headers: { Cookie: `${TENANT_COOKIE_NAME}=${"s".repeat(43)}` } }));
  for (const upstream of [
    Response.json({ private_error: "synthetic-secret" }, { status: 500 }),
    Response.json(observedSpeed, { headers: { "Set-Cookie": "unsafe=value" } }),
    Response.json({ ...empty, local_lighthouse: "available" }),
    new Response("malformed", { headers: { "Content-Type": "application/json" } }),
    new Response("x".repeat(400001), { headers: { "Content-Type": "application/json" } }),
  ]) {
    globalThis.fetch = async () => upstream;
    const response = await get();
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { state: "unavailable" });
    assert.equal(response.headers.get("set-cookie"), null);
  }
  globalThis.fetch = async () => { throw new Error("synthetic-secret"); };
  assert.deepEqual(await (await get()).json(), { state: "unavailable" });
});
