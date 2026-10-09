import assert from "node:assert/strict";
import test from "node:test";

import { decideDashboardCandidateRevision, loadDashboardCandidateInbox } from "../lib/candidate-inbox-api";

const token = "t".repeat(43);
const csrf = "c".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const revisionId = "22222222-2222-4222-8222-222222222222";
const releaseId = "33333333-3333-4333-8333-333333333333";
const manifestId = "55555555-5555-4555-8555-555555555555";
const pageId = "66666666-6666-4666-8666-666666666666";

function revision(status = "pending") {
  const decided = status === "pending" || status === "superseded" || status === "stale_base" ? null : status;
  return { schema_version: 1, revision_id: revisionId, revision_sha256: "a".repeat(64), sealed_at: "2026-09-29T10:00:00Z", recipe_release_id: releaseId, release_content_hash: "b".repeat(64), base_sha: "c".repeat(40), patch_sha256: "d".repeat(64), review_status: status, decision_id: decided === null ? null : "77777777-7777-4777-8777-777777777777", decision: decided, decided_by_user_id: decided === null ? null : "88888888-8888-4888-8888-888888888888", decision_channel: decided === null ? null : "dashboard", decided_at: decided === null ? null : "2026-09-29T10:01:00Z", reused: false,
    manifest: { schema_version: 1, site_id: siteId, extension_id: "99999999-9999-4999-8999-999999999999", build_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", audit_report_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", finding_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", recipe_release_id: releaseId, release_content_hash: "b".repeat(64), base_sha: "c".repeat(40), patch_sha256: "d".repeat(64), source_path: "index.html", source_sha256: "e".repeat(64), result_sha256: "f".repeat(64), patch: { offset: 0, before: "old\u202e", after: "new" }, evidence: { manifest_id: manifestId, manifest_sha256: "1".repeat(64), page_id: pageId, page_url: "https://example.test/", finding: { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", title: "Missing title", summary: "No title was found.", resource_locator: "https://example.test/" } }, build_receipt: { toolchain: "node", command: "npm run build", exit_class: "passed", logs_sha256: "2".repeat(64), artifacts: [{ path: "_site/index.html", sha256: "3".repeat(64), size: 12 }] }, expected_impact: "A title becomes available.", recovery_plan: "Revert this exact patch.", approval_class: "owner_review", claim_review_required: false, model_draft: null } };
}

function list(status = "pending") { return Response.json({ schema_version: 1, site_id: siteId, revisions: [revision(status)], correlation_id: "candidate-inbox" }); }

test("contextual links retain existing anchor and require reviewed same-origin scope", async () => {
  const current = revision();
  const internal = { recipe_key: "technical_internal_link_add", page_cap: 3, autonomy_eligible: false, baseline_build_id: pageId, output_path: "_site/index.html", target_id: pageId, target_url: "https://example.test/water.html", graph: {}, coverage: "partial" };
  const manifest = { ...current.manifest, audit_report_id: null, internal_link: internal,
    patch: { offset: 20, before: "Garden irrigation", after: '<a href="https://example.test/water.html">Garden irrigation</a>' },
    evidence: { ...current.manifest.evidence, site_origin: "https://example.test", finding: { ...current.manifest.evidence.finding, key: "links.internal.add" } } };
  const load = () => loadDashboardCandidateInbox({tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => Response.json({schema_version: 1, site_id: siteId, revisions: [{...current, manifest}], correlation_id: "synthetic-link"})});
  assert.equal((await load()).state, "available");
  internal.autonomy_eligible = true;
  assert.equal((await load()).state, "invalid");
  internal.autonomy_eligible = false;
  internal.page_cap = 4;
  assert.equal((await load()).state, "invalid");
  internal.page_cap = 3;
  internal.target_url = "https://other.example.invalid/water.html";
  assert.equal((await load()).state, "invalid");
});

test("root key-file additions remain inspectable owner-review Inbox entries", async () => {
  const original = revision();
  const key = "synthetic-indexnow-key";
  const keyId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const manifest = { ...original.manifest, audit_report_id: null, source_path: `${key}.txt`, patch: { offset: 0, before: "", after: key }, evidence: { key_id: keyId, key_sha256: original.manifest.result_sha256, site_origin: "https://example.invalid", page_url: `https://example.invalid/${key}.txt`, finding: { id: keyId, key: "indexnow.key.required", title: "IndexNow key file", summary: "Publish the exact key.", resource_locator: `https://example.invalid/${key}.txt` } } };
  const result = await loadDashboardCandidateInbox({ tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, revisions: [{ ...original, manifest }], correlation_id: "key-inbox" }) });
  assert.equal(result.state, "available");
  if (result.state === "available") { assert.equal(result.revisions[0].before, ""); assert.equal(result.revisions[0].evidence.kind, "indexnow_key"); }
});

test("loads one strict sealed candidate revision and retains its exact diff", async () => {
  const result = await loadDashboardCandidateInbox({ tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => list() });
  assert.equal(result.state, "available");
  if (result.state !== "available") return;
  assert.equal(result.revisions[0]?.before, "old\u202e");
  assert.equal(result.revisions[0]?.build.artifacts[0]?.path, "_site/index.html");
});

test("decides only one exact candidate revision through CSRF then reloads", async () => {
  const calls: RequestInit[] = [];
  const responses = [Response.json({ schema_version: 1, csrf_token: csrf }), Response.json({ schema_version: 1, site_id: siteId, revision: revision("approved"), correlation_id: "candidate-decision" }), list("approved")];
  const result = await decideDashboardCandidateRevision({ tenantToken: token, siteId, revisionId, revisionSha256: "a".repeat(64), decisionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", decision: "approved", baseUrl: "http://127.0.0.1:8000", dashboardOrigin: "http://localhost:3000", fetcher: async (_input, init) => { calls.push(init ?? {}); return responses.shift() ?? new Response(null, { status: 500 }); } });
  assert.equal(result.state, "available");
  assert.equal(calls.length, 3);
  assert.match(String(calls[1]?.body), /revision_sha256/);
});

test("fails closed for an invalid candidate schema", async () => {
  const result = await loadDashboardCandidateInbox({ tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, revisions: [{ nope: true }], correlation_id: "bad" }) });
  assert.equal(result.state, "invalid");
});

test("Astro Inbox retains exact shared impact and refuses autonomy or count mismatch", async () => {
  const current = revision();
  const manifest = { ...current.manifest, framework: "astro", recipe_key: "astro_title", approval_class: "A4", autonomy_eligible: false,
    evidence: { ...current.manifest.evidence, site_origin: "https://example.test" },
    built_impact: { baseline_build_id: pageId, lockfile_sha256: "4".repeat(64), scope_sha256: "5".repeat(64), page_count: 2,
      pages: ["dist/dates/one/index.html", "dist/dates/two/index.html"], samples: [{ path: "dist/dates/one/index.html", before: "", after: "Calendar" }] } };
  const load = () => loadDashboardCandidateInbox({ tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, revisions: [{ ...current, manifest }], correlation_id: "synthetic-astro" }) });
  const result = await load();
  assert.equal(result.state, "available");
  if (result.state === "available") assert.equal(result.revisions[0]?.builtImpact?.pageCount, 2);
  manifest.patch.before = "";
  assert.equal((await load()).state, "available");
  manifest.autonomy_eligible = true;
  assert.equal((await load()).state, "invalid");
  manifest.autonomy_eligible = false;
  manifest.built_impact.page_count = 1;
  assert.equal((await load()).state, "invalid");
});

for (const [framework, adapter, recipe] of [["astro", "front_matter", "front_matter_title"], ["eleventy", "front_matter", "front_matter_title"], ["nextjs", "front_matter", "front_matter_title"], ["nextjs", "nextjs_metadata", "nextjs_title"], ["nextjs", "nextjs_metadata", "nextjs_description"]]) {
  test(`${adapter} ${framework} ${recipe} Inbox retains exact scope and refuses invented authority`, async () => {
    const current = revision();
    const manifest = { ...current.manifest, framework, content_adapter: adapter, recipe_key: recipe, approval_class: "A4", autonomy_eligible: false, claim_review_required: true,
      evidence: { ...current.manifest.evidence, site_origin: "https://example.invalid" },
      built_impact: { baseline_build_id: pageId, lockfile_sha256: "4".repeat(64), scope_sha256: "5".repeat(64), page_count: 2,
        pages: ["out/one/index.html", "out/two/index.html"], samples: [{ path: "out/one/index.html", before: "", after: "Calendar" }] } };
    const load = () => loadDashboardCandidateInbox({ tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, revisions: [{ ...current, manifest }], correlation_id: "synthetic-f1" }) });
    const result = await load();
    assert.equal(result.state, "available");
    if (result.state === "available") assert.equal(result.revisions[0]?.builtImpact?.pageCount, 2);
    manifest.autonomy_eligible = true;
    assert.equal((await load()).state, "invalid");
    manifest.autonomy_eligible = false;
    manifest.claim_review_required = false;
    assert.equal((await load()).state, "invalid");
    manifest.claim_review_required = true;
    manifest.framework = "hugo";
    assert.equal((await load()).state, "invalid");
    manifest.framework = framework;
    manifest.built_impact.page_count = 1;
    assert.equal((await load()).state, "invalid");
  });
}

test("new grounded recipe exposes exact insertion and refuses widened packet authority", async () => {
  const entry = revision();
  entry.manifest.patch.before = "";
  const structured = { recipe_key: "structured_data_grounded", json_ld: { "@context": "https://schema.org", "@type": "Article", headline: "Observed title" }, fact_refs: {}, owner_required: false, autonomy_eligible: false, output_path: "_site/index.html", baseline_build_id: pageId };
  const load = async (packet: unknown) => loadDashboardCandidateInbox({ tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000", fetcher: async () => Response.json({ schema_version: 1, site_id: siteId, revisions: [{ ...entry, manifest: { ...entry.manifest, structured_data: packet } }], correlation_id: "synthetic-grounded" }) });
  const result = await load(structured);
  assert.equal(result.state, "available");
  if (result.state === "available") assert.equal(result.revisions[0]?.before, "");
  for (const change of [{ autonomy_eligible: true }, { owner_required: true }, { extra: true }, { fact_refs: { price: pageId } }]) assert.equal((await load({ ...structured, ...change })).state, "invalid");
});
