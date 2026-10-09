import assert from "node:assert/strict";
import test from "node:test";

import type { VisibilityData } from "../lib/ai-visibility-api";
import {
  buildActivity, buildWorkItems, changeMarkers, chartGeometry, citationGrid, clicksHeadline, compareWindows, dailySeries,
  isoWeekLabel, percent, sparkPaths, type DailyPoint,
} from "../lib/insights";
import type { SeoProjection } from "../lib/seo-strategy-api";

const days = (count: number, value: (index: number) => number, start = "2026-07-01"): DailyPoint[] =>
  Array.from({ length: count }, (_, index) => ({
    date: new Date(Date.parse(`${start}T00:00:00Z`) + index * 86_400_000).toISOString().slice(0, 10),
    value: value(index),
  }));

function projection(points: DailyPoint[], dimensions = ["date"], source: "gsc" | "bing" = "gsc"): SeoProjection {
  return {
    schema_version: 1, decisions: [],
    snapshot: {
      id: "11111111-1111-4111-8111-111111111111", version: 1, created_at: "2026-10-02T00:00:00Z", sha256: "a".repeat(64),
      payload: {
        headline: {}, sources: {}, pages: [], unavailable: [], strategy: { items: [] },
        performance: [{
          source, evidence_id: "22222222-2222-4222-8222-222222222222", dimensions, window: null, coverage: {}, totals: {}, total_scope: "returned",
          rows: points.map((point) => ({ labels: { date: point.date }, metrics: { clicks: { value: point.value, evidence_ids: [] }, impressions: { value: point.value * 10, evidence_ids: [] }, position: { value: 12, evidence_ids: [] } } })),
        }],
      },
    },
  } as unknown as SeoProjection;
}

test("daily series reads only the exact date-only cohort of one source", () => {
  assert.deepEqual(dailySeries(projection(days(3, (i) => i + 1)), "clicks").map((point) => point.value), [1, 2, 3]);
  assert.deepEqual(dailySeries(projection(days(3, () => 1), ["date", "page"]), "clicks"), []);
  assert.deepEqual(dailySeries(projection(days(3, () => 1), ["date"], "bing"), "clicks"), []);
  assert.deepEqual(dailySeries(null, "clicks"), []);
});

test("window comparisons need two complete gap-free windows and never invent a trend", () => {
  const full = compareWindows(days(56, (i) => (i < 28 ? 10 : 12)), "sum");
  assert.equal(full?.current, 336);
  assert.equal(full?.previous, 280);
  assert.equal(percent(full!.change), "+20%");
  assert.equal(clicksHeadline(full), "Clicks up 20% in 28 days.");

  const short = compareWindows(days(40, () => 5), "sum");
  assert.equal(short?.change, null);
  assert.equal(short?.days, 28);
  assert.equal(clicksHeadline(short), null);

  const gap = [...days(30, () => 5), ...days(30, () => 5, "2026-08-15")];
  assert.equal(compareWindows(gap, "sum")?.change, null);

  assert.equal(clicksHeadline(compareWindows(days(56, (i) => (i < 28 ? 10 : 8)), "sum")), "Clicks down 20% in 28 days.");
  assert.equal(clicksHeadline(compareWindows(days(56, () => 10), "sum")), "Clicks held steady in 28 days.");
  assert.equal(compareWindows(days(56, (i) => (i < 28 ? 0 : 4)), "sum")?.change, null);
  assert.equal(compareWindows([], "sum"), null);
});

test("chart geometry and sparklines need at least two points", () => {
  assert.equal(chartGeometry(days(1, () => 1)), null);
  assert.equal(sparkPaths([1]), null);
  const chart = chartGeometry(days(90, (i) => i));
  assert.ok(chart);
  assert.equal(chart.ticks.length, 3);
  assert.equal(chart.xLabels.length, 4);
  assert.equal(chart.xLabels[0].edge, "start");
  assert.match(chart.line, /^M0\.0 /);
});

test("change markers only place changes confirmed live inside the charted window", () => {
  const points = days(30, () => 1);
  const observation = (operationId: string, observedAt: string, outcome: "verified" | "not_yet_deployed") => ({
    attemptId: operationId, operationId, state: "completed" as const, revisionSha256: null, receiptSha256: null, canonicalReceipt: null,
    outcome, reason: null, stage: null, checksCount: null, passedCount: null, mergedSha: null, deploymentId: null, fetchedSha256: null,
    postconditions: [], recoveryPlan: null, observedAt, nextObserveAt: observedAt,
  });
  const markers = changeMarkers(points, [
    observation("a", "2026-07-10T09:00:00Z", "verified"),
    observation("b", "2026-07-11T09:00:00Z", "not_yet_deployed"),
    observation("c", "2026-09-30T09:00:00Z", "verified"),
  ], [], []);
  assert.equal(markers.length, 1);
  assert.equal(markers[0].date, "2026-07-10");
  assert.equal(markers[0].result, "Live and verified");
});

test("the citations grid shows the latest answer per question and assistant, unknown is never cited", () => {
  const observation = (provider: string, site_cited: boolean | null, competitors: string[], observed_at: string) => ({
    id: observed_at, question_id: "q", provider, model: "m", observed_at, provider_evidence_id: null, coverage: "complete" as const,
    site_cited, cited_pages: [], competitor_pages: competitors, failure_code: null,
  });
  const data = {
    providers: [{ provider: "openai", state: "available", reason: "" }, { provider: "perplexity", state: "available", reason: "" }],
    gaps: [
      { question: "What is docs as code?", observations: [observation("openai", false, [], "2026-09-01"), observation("openai", true, [], "2026-09-08"), observation("perplexity", false, ["https://rival.example"], "2026-09-08")] },
      { question: "Best docs host?", observations: [observation("openai", null, [], "2026-09-08")] },
    ],
  } as unknown as VisibilityData;
  const grid = citationGrid(data);
  assert.ok(grid);
  assert.deepEqual(grid.providers, ["openai", "perplexity"]);
  assert.deepEqual(grid.questions[0].cells, ["cited", "rival"]);
  assert.deepEqual(grid.questions[1].cells, ["unknown", "unknown"]);
  assert.equal(grid.cited, 1);
  assert.equal(grid.rivals, 1);
  assert.equal(grid.checked, 2);
  assert.equal(citationGrid(null), null);
});

test("work items come only from recorded work and mark what waits on the owner", () => {
  const items = buildWorkItems({
    projection: null, writer: null, measurements: [], observations: [], operations: [],
    revisions: [{ revisionId: "r1", revisionSha256: "a".repeat(64), reviewStatus: "pending", sealedAt: "2026-10-03T00:00:00Z", decidedAt: null, finding: { title: "Missing meta description" } } as never],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0].stage, "ship");
  assert.equal(items[0].tone, "you");
  assert.equal(items[0].href, "/approvals?revision=r1");
  assert.deepEqual(buildWorkItems({ projection: null, writer: null, measurements: [], observations: [], operations: [], revisions: [] }), []);
});

test("week labels use the ISO week number and the seven-day range", () => {
  assert.equal(isoWeekLabel("2026-09-28"), "Week 40 · Sep 28 – Oct 4");
});

test("activity lists recorded actions newest first with what allowed them", () => {
  const revision = { revisionId: "r1", revisionSha256: "a".repeat(64), reviewStatus: "approved", approvalClass: "A2", sealedAt: "2026-10-01T09:00:00Z", decidedAt: "2026-10-01T10:00:00Z", finding: { title: "Missing meta description" } };
  const operation = { operationId: "o1", revisionId: "r1", revisionSha256: "a".repeat(64), state: "opened", prNumber: 7, prUrl: "https://github.com/o/r/pull/7", createdAt: "2026-10-01T11:00:00Z", authority: { kind: "owner_inbox", decisionChannel: "slack" } };
  const entries = buildActivity({
    projection: null, writer: null, measurements: [], observations: [],
    revisions: [revision as never], operations: [operation as never],
    report: { weekStart: "2026-09-28", stages: [{ stage: "gate", outcome: "waiting_owner", detailCode: "OWNER_DECISION_REQUIRED" }, { stage: "observe", outcome: "completed", detailCode: "IMPORTS_COMMITTED" }], skills: [{ stage: "report_delivery", outcome: "completed", detailCode: "EMAIL_ACCEPTED" }] },
  });
  assert.deepEqual(entries.map((entry) => entry.kind), ["shipped", "decided", "drafted", "held", "shipped"]);
  assert.equal(entries[0].authority, "Approved by you in the Inbox in Slack");
  assert.equal(entries[0].status, "Waiting for your merge");
  assert.equal(entries[3].text, "The check before shipping held back: owner decision required.");
  assert.equal(entries[3].exactTime, false);
  assert.ok(entries.every((entry) => !/[A-Z]{2,}_[A-Z]/.test(entry.text)));
});
