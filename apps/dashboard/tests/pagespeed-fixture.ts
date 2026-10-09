import type { FieldExperience, PageSpeedData, SpeedMetric } from "../lib/pagespeed-api";

export const speedSite = "b077d5ef-5962-49b0-b953-7242b26cb5fe";
export const speedSample = "1c6d48eb-2937-4389-b878-27de51688c23";
const url = "https://example.invalid/product/team-collaboration-and-reporting";
const lab = (value: number, unit: "ms" | "score" = "ms"): SpeedMetric => ({ state: "available", reason: null, value, unit });
const field = (value: number, rating: "good" | "poor", unit: "ms" | "score" = "ms"): SpeedMetric => ({ ...lab(value, unit), rating });
const unavailable = (unit: "ms" | "score"): SpeedMetric => ({ state: "unavailable", reason: "insufficient_field_data", value: null, rating: null, unit });
const origin: FieldExperience = {
  source: "origin", locator: "https://example.invalid", percentile: 75, status: "unavailable",
  collection_period: { state: "unavailable", reason: "collection_period_not_reported", first_date: null, last_date: null },
  metrics: { lcp: unavailable("ms"), inp: unavailable("ms"), cls: unavailable("score") },
};

export const observedSpeed: PageSpeedData = {
  schema_version: 1, site_id: speedSite, outcome: "found", daily_request_cap: 4, weekly_page_cap: 5, local_lighthouse: "unavailable",
  samples: [{
    sample_id: speedSample, url, strategy: "mobile", week_start: "2026-09-28", selection_source: "gsc_clicks", reason: null, state: "observed",
    observation: {
      evidence_id: speedSample, url, strategy: "mobile", lighthouse_version: "12.8.2", fetched_at: "2026-10-03T12:00:00+00:00", response_sha256: "a".repeat(64),
      lab: { source: "psi_lighthouse", status: "available", reason: null, run_at: "2026-10-03T11:59:00Z", performance_score: 0.84, metrics: { lcp: lab(2100), fcp: lab(1200), tbt: lab(40), speed_index: lab(2300), cls: lab(0.04, "score") } },
      field_url: { ...origin, source: "url", locator: url, status: "poor", collection_period: { state: "available", reason: null, first_date: "2026-09-04", last_date: "2026-10-01" }, metrics: { lcp: field(4200, "poor"), inp: field(180, "good"), cls: field(0.06, "good", "score") } },
      field_origin: origin,
      findings: [{ id: "c4d0f349-091e-45fb-bf49-c4c3b645ac51", key: "performance.cwv.url.lcp.poor", title: "Poor field LCP", summary: "URL field LCP p75 is poor (mobile); observation only.", severity: "medium", resource_locator: url, source_kind: "pagespeed_observation", source_id: speedSample }],
    },
  }],
};
