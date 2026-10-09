import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { HealthPanel } from "../components/health-panel";
import { HEALTH_CHECKS, loadHealth, type HealthCheck, type HealthState } from "../lib/health-api";

const token = "synthetic-" + "t".repeat(33), siteId = "11111111-1111-4111-8111-111111111111";
function checks(): HealthCheck[] {
  return HEALTH_CHECKS.map((check, index) => ({ check, state: (["ok", "warning", "critical", "unknown"] as const)[index % 4],
    reason: ["within_threshold", "approaching_threshold", "threshold_exceeded", "probe_unavailable"][index % 4],
    checked_at: new Date().toISOString(), remediation: "Review Settings." }));
}

test("health panel keeps all four states, checked times and remediation visible", () => {
  const markup = renderToStaticMarkup(createElement(HealthPanel, { value: { state: "available", checks: checks() } }));
  for (const label of ["OK", "Warning", "Critical", "Unknown", "UTC", "Review Settings."]) assert.match(markup, new RegExp(label));
  assert.equal((markup.match(/<li /g) ?? []).length, 20);
});

test("health unavailable, rejected and empty are not green success", () => {
  const values: HealthState[] = [{ state: "unavailable" }, { state: "rejected" }, { state: "available", checks: [] }];
  for (const value of values) {
    const markup = renderToStaticMarkup(createElement(HealthPanel, { value }));
    assert.doesNotMatch(markup, /health-ok/);
  }
});

test("health read accepts bounded owner projection and labels stale data unknown", async () => {
  const data = checks(); data[0].checked_at = "2026-01-01T00:00:00Z";
  const result = await loadHealth({ tenantToken: token, siteId, fetcher: async () => Response.json({ checks: data }) });
  assert.equal(result.state, "available");
  if (result.state === "available") assert.equal(result.checks[0].state, "unknown");
});

test("health rejects malformed, duplicate, oversized and secret-bearing projections", async () => {
  for (const data of [{ checks: checks(), token }, { checks: [checks()[0], ...checks().slice(0, 19)] },
    { checks: checks().slice(0, 10) }, { checks: checks().map(c => ({ ...c, reason: "synthetic-secret" })) },
    { checks: checks().map(c => ({ ...c, state: "ok", reason: "probe_unavailable" })) }]) {
    assert.deepEqual(await loadHealth({ tenantToken: token, siteId, fetcher: async () => Response.json(data) }), { state: "unavailable" });
  }
  assert.deepEqual(await loadHealth({ tenantToken: token, siteId, fetcher: async () => new Response(null, { status: 403 }) }), { state: "rejected" });
});
