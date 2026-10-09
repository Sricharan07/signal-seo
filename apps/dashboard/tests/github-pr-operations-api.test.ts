import assert from "node:assert/strict";
import test from "node:test";

import { loadDashboardGithubPrOperations } from "../lib/github-pr-operations-api";

const siteId = "11111111-1111-4111-8111-111111111111";
const token = "t".repeat(43);
const operation = {
  schema_version: 1,
  operation_id: "22222222-2222-4222-8222-222222222222",
  revision_id: "33333333-3333-4333-8333-333333333333",
  revision_sha256: "a".repeat(64),
  branch_name: "signal/" + "2".repeat(32),
  state: "opened",
  step: "done",
  base_sha: "b".repeat(40),
  expected_tree_sha: "c".repeat(40),
  expected_commit_sha: "d".repeat(40),
  pr_number: 7,
  pr_url: "https://github.com/SignalOwner/website/pull/7",
  journal_generation: "44444444-4444-4444-8444-444444444444",
  journal_position: 3,
  journal_body_hash: "e".repeat(64),
  created_at: "2026-09-29T12:00:00Z",
  updated_at: "2026-09-29T12:01:00Z",
};

test("schema two names the exact owner or standing dispatch authorization", async () => {
  for (const [kind, decision_channel] of [["owner_inbox", "dashboard"], ["owner_inbox", "slack"], ["owner_inbox", "telegram"], ["standing_grant", null], ["owner_editorial", "dashboard"]]) {
    const authority = { kind, record_id: siteId, owner_user_id: siteId, decision_channel };
    const result = await loadDashboardGithubPrOperations({ tenantToken: token, siteId,
      fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, correlation_id: "source", operations: [{ ...operation, schema_version: 2, authority }] }) });
    assert.equal(result.state, "available");
    if (result.state === "available") {
      assert.equal(result.operations[0].authority?.kind, kind);
      assert.equal(result.operations[0].authority?.decisionChannel, decision_channel);
    }
  }
});

test("loads one exact PR operation without turning it into delivery", async () => {
  const result = await loadDashboardGithubPrOperations({
    tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, operations: [operation], correlation_id: "ok" }),
  });
  assert.equal(result.state, "available");
  if (result.state === "available") {
    assert.equal(result.operations[0].prUrl, operation.pr_url);
    assert.equal(result.operations[0].journalBodyHash, operation.journal_body_hash);
  }
});

test("rejects untrusted PR URL, extra fields and contradictory state", async () => {
  for (const changed of [
    { ...operation, pr_url: "https://example.invalid/steal" },
    { ...operation, unexpected: "value" },
    { ...operation, state: "ready" },
  ]) {
    const result = await loadDashboardGithubPrOperations({
      tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000",
      fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, operations: [changed], correlation_id: "bad" }),
    });
    assert.equal(result.state, "invalid");
  }
});

test("keeps absent and unavailable PR evidence visibly distinct", async () => {
  const empty = await loadDashboardGithubPrOperations({
    tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, operations: [], correlation_id: "empty" }),
  });
  assert.deepEqual(empty, { state: "available", operations: [] });
  const unavailable = await loadDashboardGithubPrOperations({
    tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => { throw new Error("offline"); },
  });
  assert.deepEqual(unavailable, { state: "unavailable" });
});
