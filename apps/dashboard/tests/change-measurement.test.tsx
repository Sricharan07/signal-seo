import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { WeeklyReport } from "../components/weekly-report";
import { ChangeMeasurements } from "../components/change-measurements";
import { parseChangeMeasurements } from "../lib/change-measurement-api";
import { parseWeeklyReport } from "../lib/weekly-report-api";
import { measurementFixture, measurementId, measurementReportFixture } from "./change-measurement-fixture";

test("Bing page rows remain reported, incomplete and separate from site context", () => {
  const page = { ...measurementFixture.baseline.bing_site_context, coverage: { complete: false, coverage: "provider_returned_top_pages", date_granularity: "unknown" } };
  const fixture = { ...measurementFixture, baseline: { ...measurementFixture.baseline, bing_page: page }, observation: { ...measurementFixture.observation, post: { ...measurementFixture.observation.post, bing_page: page } } };
  const parsed = parseChangeMeasurements([fixture]);
  assert.ok(parsed);
  const html = renderToStaticMarkup(<ChangeMeasurements values={parsed} />);
  assert.match(html, /Bing, this page<\/h4>/);
  assert.match(html, /Bing page baseline/);
  assert.match(html, /provider_returned_top_pages/);
  assert.match(html, /<details class="technical-details"><summary>Technical details<\/summary>/);
  assert.equal(parseChangeMeasurements([{ ...fixture, baseline: { ...fixture.baseline, bing_page: { ...page, coverage: { ...page.coverage, complete: true } } } }]), null);
});

test("provider-reported page changes preserve uncertainty, context and evidence", () => {
  const report = parseWeeklyReport(measurementReportFixture, measurementId, "2026-09-28");
  assert.ok(report);
  const html = renderToStaticMarkup(<WeeklyReport value={{ state: "available", report }} />);
  for (const text of ["What it did", "What is next", "Needs a decision", "As reported by provider; completeness not guaranteed", "Search Console, this page", "Bing, whole site (context)", "observed change 14", "Other page change", "Import generation:", "unknown_not_zero", "Strategy or backlog unavailable", "1 pending"]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /impact|caused|Complete|complete measurement/);
  assert.match(html, /href="\/changes#operation-/);
  assert.match(html, /href="\/approvals\?revision=/);
});

test("complete, causal output and fake source coverage fail closed", () => {
  for (const fixture of [
    { ...measurementFixture, observation: { ...measurementFixture.observation, state: "complete" } },
    { ...measurementFixture, impact: { clicks: 14 } },
    { ...measurementFixture, baseline: { ...measurementFixture.baseline, gsc_page: { ...measurementFixture.baseline.gsc_page, coverage: { complete: true } } } },
    { ...measurementFixture, baseline: { ...measurementFixture.baseline, bing_page: measurementFixture.baseline.gsc_page } },
    { ...measurementFixture, evidence_url: "https://other.example.invalid" },
    { ...measurementFixture, horizon: "7" },
    { ...measurementFixture, baseline: { ...measurementFixture.baseline, gsc_page: { ...measurementFixture.baseline.gsc_page, state: "awaiting_data" } } },
  ]) assert.equal(parseChangeMeasurements([fixture]), null);
});

test("all unavailable and pending states remain words, never zeros", () => {
  for (const state of ["not_yet_due", "awaiting_data", "unavailable", "failed"]) {
    const fixture = { ...measurementReportFixture, measurements: [{ ...measurementFixture, observation: { state, reason: "DATA_UNAVAILABLE", post: null, observed_change: null, confounders: [] } }] };
    const report = parseWeeklyReport(fixture, measurementId, "2026-09-28");
    assert.ok(report);
    const html = renderToStaticMarkup(<WeeklyReport value={{ state: "available", report }} />);
    assert.match(html, /after Unavailable; observed change Unavailable/);
    assert.ok(html.toLowerCase().includes(state.replaceAll("_", " ")));
  }
});

test("report rejects mismatched Inbox counts and unsafe decision links", () => {
  assert.equal(parseWeeklyReport({ ...measurementReportFixture, schema_version: "2" }, measurementId, "2026-09-28"), null);
  for (const needs_decision of [
    { ...measurementReportFixture.needs_decision, count: 0 },
    { count: 1, url: "/approvals", items: [{ revision_id: measurementId, kind: "technical", url: "https://other.invalid" }] },
  ]) assert.equal(parseWeeklyReport({ ...measurementReportFixture, needs_decision }, measurementId, "2026-09-28"), null);
});

test("new pages expose no fabricated pre-change window, metrics or observed change", () => {
  const absent = { state: "unavailable", reason: "NO_PRE_CHANGE_WINDOW", generation_id: null, coverage: null, metrics: null };
  const baseline = { state: "new_page", reason: "NO_PRE_CHANGE_WINDOW", gsc_page: absent, bing_site_context: absent, bing_page: absent };
  const delta = { clicks: null, impressions: null, ctr: null, position: null };
  const fixture = { ...measurementFixture, baseline_start: null, baseline_end: null, baseline,
    observation: { ...measurementFixture.observation, observed_change: { gsc_page: delta, bing_site_context: delta } } };
  const parsed = parseChangeMeasurements([fixture]);
  assert.ok(parsed);
  const html = renderToStaticMarkup(<ChangeMeasurements values={parsed} />);
  assert.match(html, /New page: no pre-change window/);
  assert.match(html, /Before Unavailable; after 84; observed change Unavailable/);
  assert.match(html, /Observed change, not a causal estimate/);
  for (const invalid of [
    { ...fixture, baseline_start: "2026-09-14" },
    { ...fixture, baseline_end: "2026-09-20" },
    { ...fixture, baseline: { ...baseline, gsc_page: { ...absent, metrics: delta } } },
    { ...fixture, baseline: { ...baseline, gsc_page: { ...absent, generation_id: measurementId } } },
    { ...fixture, baseline: { ...baseline, gsc_page: { ...absent, coverage: { complete: false } } } },
    { ...fixture, baseline: measurementFixture.baseline },
    { ...fixture, observation: measurementFixture.observation },
  ]) assert.equal(parseChangeMeasurements([invalid]), null);
});
