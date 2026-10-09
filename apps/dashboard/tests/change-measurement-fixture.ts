export const measurementId = "11111111-1111-4111-8111-111111111111";
const missing = { state: "unavailable", reason: "NOT_PROVIDED_BY_CURRENT_BING_IMPORT", generation_id: null, coverage: null, metrics: null };
const gsc = { state: "measured_as_reported", reason: "AS_REPORTED_COMPLETENESS_NOT_GUARANTEED", generation_id: measurementId, coverage: { complete: false, missing_data: "unknown_not_zero", top_rows_only: true }, metrics: { clicks: 70, impressions: 700, ctr: 0.1, position: 4 } };
const bing = { ...gsc, coverage: { complete: false, missing_data: "unknown", verticals: "all_provider_verticals" }, metrics: { clicks: 140, impressions: 1400, ctr: null, position: null } };
export const measurementFixture = {
  operation_id: measurementId, horizon: 7, page_url: "https://example.invalid/a-long-but-exact-marketing-page-for-the-observed-change",
  verified_live_at: "2026-09-21T12:00:00Z", due_at: "2026-09-28T12:00:00Z", baseline_start: "2026-09-14", baseline_end: "2026-09-20",
  baseline: { gsc_page: gsc, bing_site_context: bing, bing_page: missing }, post_start: "2026-09-22", post_end: "2026-09-28",
  evidence_url: `/changes#operation-${measurementId}`, verification_attempt_id: measurementId,
  observation: { state: "measured_as_reported", reason: "AS_REPORTED_COMPLETENESS_NOT_GUARANTEED", post: { gsc_page: { ...gsc, metrics: { ...gsc.metrics, clicks: 84, ctr: 0.12 } }, bing_site_context: bing, bing_page: missing },
    observed_change: { gsc_page: { clicks: 14, impressions: 0, ctr: 0.02, position: 0 }, bing_site_context: { clicks: 0, impressions: 0, ctr: null, position: null } },
    confounders: [{ operation_id: "22222222-2222-4222-8222-222222222222", verified_live_at: "2026-09-25T12:00:00Z", page_url: "https://example.invalid/a-long-but-exact-marketing-page-for-the-observed-change" }] },
  recorded_at: "2026-09-29T12:00:00Z",
};
export const measurementReportFixture = {
  schema_version: 2, cycle_id: measurementId, site_id: measurementId, week_start: "2026-09-28", status: "completed", stop_reason: "CYCLE_REPORTED",
  started_at: "2026-09-28T10:00:00Z", closed_at: "2026-09-28T11:00:00Z", observation_command: null,
  stages: [], gate_decisions: [], waiting_for_owner: [], handoffs: [], deferred: [], delivery: [],
  measurements: [measurementFixture], what_is_next: { state: "unavailable", source: "weekly_backlog", reason: "STRATEGY_OR_BACKLOG_UNAVAILABLE", items: [] },
  needs_decision: { count: 1, url: "/approvals", items: [{ revision_id: measurementId, kind: "technical", url: `/approvals?revision=${measurementId}` }] },
};
