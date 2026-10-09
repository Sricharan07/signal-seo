import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { WeeklyReport } from "../components/weekly-report";
import { loadWeeklyReport, parseWeeklyReport } from "../lib/weekly-report-api";

const id = "11111111-1111-4111-8111-111111111111";
const week = "2026-09-28";
const report = {
  schema_version: 1, cycle_id: id, site_id: id, week_start: week, status: "completed",
  stop_reason: "CYCLE_REPORTED", started_at: "2026-09-29T00:00:00Z", closed_at: "2026-09-29T00:01:00Z",
  observation_command: null, gate_decisions: [], waiting_for_owner: [], handoffs: [], deferred: [],
  stages: [{ stage: "handoff", outcome: "completed", detail_code: "DELIVERY_HANDOFF_RECORDED", evidence_refs: [`operation:${id}`], recorded_at: "2026-09-29T00:01:00Z" }],
  delivery: [{ workload_id: id, finding_id: id, recipe_release_id: id, revision_id: id, revision_sha256: "a".repeat(64), operation_id: id, operation_state: "opened", pr_url: "https://github.com/SignalOwner/website/pull/42", authority_kind: "standing_grant", authority_id: id, authorization_owner_id: id, decision_channel: null, review_status: null, observation_id: id, observation_sha256: "b".repeat(64), delivery_outcome: "verified", delivery_reason: "EXACT_SEALED_RESULT", delivery_stage: "deployed" }],
};

test("weekly report loads through an owner cookie with committed evidence only", async () => {
  const result = await loadWeeklyReport({ tenantToken: "t".repeat(43), siteId: id, weekStart: week,
    fetcher: async (url, options) => {
      assert.match(String(url), /weekly-cycles\/2026-09-28$/);
      assert.equal(options?.cache, "no-store");
      assert.ok(new Headers(options?.headers).get("Cookie"));
      return Response.json(report);
    } });
  assert.equal(result.state, "available");
  const html = renderToStaticMarkup(<WeeklyReport value={result} />);
  assert.match(html, /Live verified/); assert.match(html, /Standing authorization/);
  assert.match(html, /github.com\/SignalOwner\/website\/pull\/42/);
  assert.match(html, /href="\/changes#operation-11111111-1111-4111-8111-111111111111"/);
  assert.match(html, /Observation:/); assert.doesNotMatch(html, /<button/);
});

test("empty, unavailable, rejected and invalid cycle reports remain distinct", async () => {
  for (const [response, state] of [[Response.json(null), "empty"], [new Response(null, { status: 404 }), "empty"], [new Response(null, { status: 503 }), "unavailable"], [Response.json({ ...report, unexpected: true }), "unavailable"]] as const) {
    assert.equal((await loadWeeklyReport({ tenantToken: "t".repeat(43), siteId: id, weekStart: week, fetcher: async () => response })).state, state);
  }
  assert.match(renderToStaticMarkup(<WeeklyReport value={{ state: "empty" }} />), /No recorded cycle/);
  assert.match(renderToStaticMarkup(<WeeklyReport value={{ state: "unavailable" }} />), /evidence unavailable/);
  assert.equal(renderToStaticMarkup(<WeeklyReport value={{ state: "rejected" }} />), "");
});

test("weekly report rejects fabricated delivery and cross-site evidence", () => {
  assert.equal(parseWeeklyReport(report, id.replace(/^1/, "2"), week), null);
  for (const changes of [{ delivery_stage: "merged" }, { pr_url: "https://other.invalid/42" }, { observation_sha256: null }, { authority_kind: "model" }, { authority_id: null }, { authorization_owner_id: null }, { future: true }]) {
    assert.equal(parseWeeklyReport({ ...report, delivery: [{ ...report.delivery[0], ...changes }] }, id, week), null);
  }
});

test("waiting and inconclusive records never turn into live verification", () => {
  for (const delivery_outcome of ["not_yet_deployed", "inconclusive", "regressed"]) {
    const parsed = parseWeeklyReport({ ...report, delivery: [{ ...report.delivery[0], delivery_outcome }] }, id, week);
    assert.ok(parsed);
    const html = renderToStaticMarkup(<WeeklyReport value={{ state: "available", report: parsed }} />);
    assert.doesNotMatch(html, /Live verified/);
  }
});

test("weekly report bounds provider output and failures", async () => {
  for (const fetcher of [async () => { throw new Error("offline"); }, async () => Response.json("x".repeat(300000))]) {
    assert.equal((await loadWeeklyReport({ tenantToken: "t".repeat(43), siteId: id, weekStart: week, fetcher })).state, "unavailable");
  }
});

test("weekly report displays the exact Telegram Inbox channel and rejects unknown channels", () => {
  const delivery = { ...report.delivery[0], authority_kind: "owner_inbox", decision_channel: "telegram" };
  const parsed = parseWeeklyReport({ ...report, delivery: [delivery] }, id, week);
  assert.ok(parsed);
  const html = renderToStaticMarkup(<WeeklyReport value={{ state: "available", report: parsed }} />);
  assert.match(html, /Owner Inbox approval/);
  assert.match(html, /<dt>Decision channel<\/dt><dd>Telegram<\/dd>/);
  assert.equal(parseWeeklyReport({ ...report, delivery: [{ ...delivery, decision_channel: "unknown" }] }, id, week), null);
  assert.equal(parseWeeklyReport({ ...report, delivery: [{ ...delivery, authority_kind: "standing_grant" }] }, id, week), null);
});

const skill = { stage: "brain_refresh", outcome: "failed", detail_code: "OUTCOME_UNKNOWN",
  work_type: "research_audit", budget_source: "standing_authorization", cap_source: "standing_authorization",
  reserved_cents: 25, units: 1, spend_status: "upper_bound_reserved", evidence_refs: [], recorded_at: null };

test("new weekly stages display their recorded state rather than readiness", () => {
  const skill_stages = [
    { ...skill, stage: "pagespeed_refresh", outcome: "completed", detail_code: "PSI_OBSERVED_KEYLESS", cap_source: "standing_authorization_and_pagespeed", evidence_refs: [`pagespeed:${id}`] },
    { ...skill, stage: "visibility_reobserve", outcome: "unavailable", detail_code: "COST_CAP_REACHED", cap_source: "standing_authorization_and_assistants" },
    { ...skill, stage: "chat_report_delivery", outcome: "unavailable", detail_code: "NO_VERIFIED_CHAT_RECIPIENT", cap_source: "standing_authorization_and_chat" },
  ];
  const parsed = parseWeeklyReport({ ...report, skill_stages }, id, week);
  assert.ok(parsed);
  const html = renderToStaticMarkup(<WeeklyReport value={{ state: "available", report: parsed }} />);
  for (const text of ["Page speed", "AI answers", "Chat report", "psi observed keyless", "cost cap reached", "no verified chat recipient", "Finished"]) assert.ok(html.includes(text), text);
  assert.match(html, /<details class="technical-details"><summary>Technical details<\/summary>/);
  assert.ok(!html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, "").includes(`pagespeed:${id}`));
});

test("last recorded cycle displays unavailable reasons and reserved, not claimed actual, spend", async () => {
  const result = await loadWeeklyReport({ tenantToken: "t".repeat(43), siteId: id,
    fetcher: async url => {
      assert.match(String(url), /weekly-cycles\/latest$/);
      return Response.json({ ...report, delivery: [], skill_stages: [skill] });
    } });
  assert.equal(result.state, "available");
  const html = renderToStaticMarkup(<WeeklyReport value={result} />);
  assert.match(html, /Business facts/);
  assert.match(html, /Failed/);
  assert.match(html, /outcome unknown/);
  assert.match(html, /Reserved budget: \$0.25/);
  assert.match(html, /Actual provider spend unreported/);
  assert.doesNotMatch(html, /Live verified/);
});

test("skill evidence is closed, bounded and cannot claim acceptance or delivery", () => {
  for (const mutation of [{ stage: "approve" }, { work_type: "metadata_pr" }, { outcome: "accepted" },
    { units: 65 }, { reserved_cents: -1 }, { cap_source: "standing_authorization_and_email" },
    { spend_status: "actual" }, { evidence_refs: ["<script>"] }, { override: true }]) {
    assert.equal(parseWeeklyReport({ ...report, skill_stages: [{ ...skill, ...mutation }] }, id, week), null);
  }
  assert.equal(parseWeeklyReport({ ...report, skill_stages: [skill, skill] }, id, week), null);
});
