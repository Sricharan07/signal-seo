import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { loadDashboardDeliveryObservations } from "../lib/github-delivery-api";

const siteId = "11111111-1111-4111-8111-111111111111";
const attemptId = "22222222-2222-4222-8222-222222222222";
const operationId = "33333333-3333-4333-8333-333333333333";

function receipt() {
  return { schema_version: 1, site_id: siteId, attempt_id: attemptId, operation_id: operationId, revision_sha256: "a".repeat(64), environment: "production", deployment_actor_id: 56, provider: { stage: "deployed", reason: "CORRELATED_CUSTOMER_DEPLOYMENT", checks: { head_sha: "b".repeat(40), observed_count: 0, passed_count: 0, records: [] }, merged_sha: "c".repeat(40), merged_tree_sha: "d".repeat(40), merged_at: "2026-09-29T10:00:00Z", deployment: { id: 81, status_id: 82, sha: "c".repeat(40), environment: "production", actor_id: 56, environment_url: "https://site.example/", created_at: "2026-09-29T10:01:00Z", status_created_at: "2026-09-29T10:02:00Z" } }, provider_evidence: [], live: { outcome: "verified", reason: "EXACT_SEALED_RESULT", fetched_sha256: "e".repeat(64), http_status: 200, observed: { title: ["Evidence title"] }, postconditions: [{ field: "title", expected: "Evidence title", observed: ["Evidence title"], matched: true }], matched: true }, live_egress_operation_id: "44444444-4444-4444-8444-444444444444", outcome: "verified", reason: "EXACT_SEALED_RESULT", recovery_plan: "Revert this exact patch with conflict review.", delivery_certified: false };
}

function record(document: unknown = receipt()) {
  const raw = JSON.stringify(document);
  return { attempt_id: attemptId, operation_id: operationId, state: "completed", canonical_receipt: raw, receipt_sha256: createHash("sha256").update(raw).digest("hex"), observed_at: "2026-09-29T10:03:00Z", next_observe_at: "2026-09-29T10:03:30Z" };
}

async function load(observations: unknown[]) {
  return loadDashboardDeliveryObservations({ tenantToken: "t".repeat(43), siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async (_input, init) => {
    assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
    assert.equal(init?.method, undefined);
    return Response.json({ schema_version: 1, site_id: siteId, correlation_id: "delivery-test", observations });
  } });
}

test("loads exact immutable observation bytes without delivery certification", async () => {
  const result = await load([record()]);
  assert.equal(result.state, "available");
  if (result.state === "available") {
    assert.equal(result.observations[0].outcome, "verified");
    assert.equal(result.observations[0].deploymentId, 81);
    assert.equal(result.observations[0].postconditions[0].matched, true);
  }
});

test("empty, dispatching and lost observations never fabricate a stage", async () => {
  assert.deepEqual(await load([]), { state: "available", observations: [] });
  for (const state of ["dispatching", "outcome_unknown"]) {
    const result = await load([{ ...record(), state, canonical_receipt: null, receipt_sha256: null }]);
    assert.equal(result.state, "available");
    if (result.state === "available") assert.equal(result.observations[0].stage, null);
  }
});

test("rejects hash drift, wrong site, extra schema, uncorrelated deployment and fake success", async () => {
  const mutations = [
    { ...record(), receipt_sha256: "0".repeat(64) },
    record({ ...receipt(), site_id: operationId }),
    record({ ...receipt(), unexpected: true }),
    record({ ...receipt(), provider: { ...receipt().provider, deployment: { ...receipt().provider.deployment, sha: "f".repeat(40) } } }),
    record({ ...receipt(), live: { ...receipt().live, matched: false } }),
    record({ ...receipt(), delivery_certified: true }),
  ];
  for (const mutation of mutations) assert.deepEqual(await load([mutation]), { state: "invalid" });
  assert.deepEqual(await load([record(), record()]), { state: "invalid" });
});

test("provider outages and denied reads stay unavailable", async () => {
  for (const status of [401, 403, 503, 500]) {
    const result = await loadDashboardDeliveryObservations({ tenantToken: "t".repeat(43), siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => new Response(null, { status }) });
    assert.equal(result.state, [401, 403].includes(status) ? "not_authenticated" : status === 503 ? "unavailable" : "invalid");
  }
});
