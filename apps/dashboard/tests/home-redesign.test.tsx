import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";

import { DashboardView } from "../components/dashboard-view";
import { readableCode } from "../components/home-panels";
import type { DashboardCandidateRevision } from "../lib/candidate-inbox-api";
import type { HealthCheck } from "../lib/health-api";
import type { WeeklyReport } from "../lib/weekly-report-api";

const site = "11111111-1111-4111-8111-111111111111";
const revision: DashboardCandidateRevision = {
  revisionId: "22222222-2222-4222-8222-222222222222", revisionSha256: "a".repeat(64), sealedAt: "2026-09-29T10:00:00Z",
  recipeReleaseId: "33333333-3333-4333-8333-333333333333", releaseContentHash: "b".repeat(64), baseSha: "c".repeat(40),
  patchSha256: "d".repeat(64), reviewStatus: "pending", decisionId: null, decision: null, decidedAt: null,
  sourcePath: "pricing/index.html", before: "", after: '<meta name="description" content="Plans and pricing">',
  finding: { id: "55555555-5555-4555-8555-555555555555", title: "Missing meta description", summary: "The committed crawl found no description on the pricing page.", resourceLocator: "https://site.example/pricing" },
  evidence: { manifestId: "66666666-6666-4666-8666-666666666666", manifestSha256: "e".repeat(64), pageUrl: "https://site.example/pricing" },
  build: { toolchain: "node", command: "npm run build", logsSha256: "f".repeat(64), artifacts: [] },
  expectedImpact: "Make the page description available.", recoveryPlan: "Inverse patch; preserve unrelated later edits.",
  claimReviewRequired: false, reused: false,
};

const report: WeeklyReport = {
  cycleId: "77777777-7777-4777-8777-777777777777", weekStart: "2026-09-28", status: "running",
  stages: [
    { stage: "observe", outcome: "completed", detailCode: "IMPORTS_COMMITTED", evidenceRefs: [] },
    { stage: "analyze", outcome: "completed", detailCode: "FINDINGS_RECORDED", evidenceRefs: [] },
    { stage: "plan", outcome: "completed", detailCode: "PLAN_RECORDED", evidenceRefs: [] },
    { stage: "prepare", outcome: "completed", detailCode: "CANDIDATES_SEALED", evidenceRefs: [] },
    { stage: "gate", outcome: "waiting_owner", detailCode: "OWNER_DECISION_REQUIRED", evidenceRefs: [] },
  ],
  skills: [], delivery: [], measurements: [], next: null, decisions: null,
};

function view(overrides: Record<string, unknown> = {}) {
  return renderToStaticMarkup(
    <DashboardView
      authNotice={null}
      snapshot={{ fetchedAt: "2026-09-29T10:03:00Z", connection: "connected", dependencies: "ready", inventory: "available", releaseStatus: "development", productionWritesEnabled: false, capabilities: [] }}
      session={{ state: "authenticated", tenantId: site, role: "owner", authenticationLevel: "mfa", expiresAt: "2026-09-30T10:00:00Z", sessionVersion: 1, activeSiteId: site }}
      sites={{ state: "available", tenantId: site, tenantName: "Synthetic test organization", sites: [{ id: site, name: "Synthetic test site", primaryOrigin: "https://site.example", timezone: "UTC", reportingCurrency: "USD", state: "active", ownershipStatus: "verified" }] }}
      organizations={{ state: "absent" }}
      {...overrides}
    />,
  );
}

test("Home counts only real pending decisions and links each one to its exact revision", () => {
  const html = view({ candidateInbox: { state: "available", revisions: [revision, { ...revision, revisionId: "99999999-9999-4999-8999-999999999999", reviewStatus: "approved" }] } });
  assert.match(html, /1 decision waits on you\./);
  assert.match(html, /href="\/approvals\?revision=22222222-2222-4222-8222-222222222222"/);
  assert.doesNotMatch(html, /href="\/approvals\?revision=99999999/);
  assert.match(html, /class="nav-count" aria-label="1 waiting">1</);
  assert.match(html, /Missing meta description/);

  const none = view({ candidateInbox: { state: "available", revisions: [] } });
  assert.match(none, /Nothing waits on you\./);
  assert.doesNotMatch(none, /nav-count/);

  const unavailable = view({ candidateInbox: { state: "unavailable" } });
  assert.match(unavailable, /Your Inbox is unavailable right now/);
  assert.doesNotMatch(unavailable, /Nothing waits on you<\/b>/);
});

test("Home tiles never invent search or citation numbers", () => {
  const html = view({ githubPrOperations: { state: "unavailable" }, deliveryObservations: { state: "invalid" } });
  assert.equal((html.match(/No data yet/g) ?? []).length, 4);
  assert.match(html, /Search data could not be read\./);
  assert.match(html, /AI answers could not be read\./);
  assert.doesNotMatch(html, /class="c-dl/);
  assert.match(html, /Search data unavailable/);
});

test("the Home loop reports the recorded cycle in plain words and never raw codes", () => {
  const html = view({ weeklyReport: { state: "available", report }, candidateInbox: { state: "available", revisions: [revision] } });
  assert.match(html, /Week 40 · Sep 28 – Oct 4/);
  assert.match(html, /Running now this week/);
  assert.match(html, /aria-label="Ship: 1 item, something waits on you"/);
  assert.doesNotMatch(html, /OWNER_DECISION_REQUIRED|IMPORTS_COMMITTED/);
  assert.match(html, /class="run-state live"/);

  const empty = view({ weeklyReport: { state: "empty" } });
  assert.match(empty, /Not run yet this week/);
  assert.match(empty, /class="run-state"/);
});

test("health problems are shown with their reason and unknown checks are never called healthy", () => {
  const check = (name: HealthCheck["check"], state: HealthCheck["state"], reason: string): HealthCheck =>
    ({ check: name, state, reason, checked_at: "2026-09-29T10:02:00Z", remediation: "Reconnect it from Connections." });
  const problems = view({ health: { state: "available", checks: [check("binding_gsc", "critical", "revoked"), check("openbao", "ok", "unsealed")] } });
  assert.match(problems, /Search Console: Revoked/);
  assert.match(problems, /href="\/settings#health-title"/);

  const unknown = view({ health: { state: "available", checks: [check("openbao", "ok", "unsealed"), check("disk", "unknown", "probe_unavailable")] } });
  assert.match(unknown, /Disk space: could not be checked/);
  assert.doesNotMatch(unknown, /healthy/i);

  const healthy = view({ health: { state: "available", checks: [check("openbao", "ok", "unsealed")] } });
  assert.doesNotMatch(healthy, /Needs attention/);

  const viewer = view({
    session: { state: "authenticated", tenantId: site, role: "viewer", authenticationLevel: "primary", expiresAt: "2026-09-30T10:00:00Z", sessionVersion: 1, activeSiteId: site },
    health: { state: "available", checks: [check("binding_gsc", "critical", "revoked")] },
  });
  assert.doesNotMatch(viewer, /Search Console: Revoked/);
});

test("navigation hides placeholder destinations and pilot-only surfaces in production", () => {
  const html = view();
  for (const hidden of ["/recipes", "/usage", "/chat", "/work"]) {
    assert.doesNotMatch(html, new RegExp(`<a href="${hidden}"`));
  }
  assert.match(html, /<a href="\/help">Help<\/a>/);
  const pilot = view({ localPilot: true });
  assert.match(pilot, /<a href="\/chat"/);
  assert.match(pilot, /<a href="\/work"/);
});

test("Chat reports the real Telegram pairing and the current model name", () => {
  const paired = view({
    activeSection: "chat",
    telegram: { availability: "bound", binding_id: "88888888-8888-4888-8888-888888888888", bot_username: "synthetic_signal_bot", max_risk: 2, link_id: site, telegram_user_id: "9000092" },
  });
  assert.match(paired, /Ask Signal · 0 conversations · Telegram paired/);
  const unpaired = view({ activeSection: "chat" });
  assert.match(unpaired, /Telegram not paired/);
  assert.doesNotMatch(paired + unpaired, /GPT-5\.6/);
  // nothing on the page contradicts the pairing shown in its header
  assert.doesNotMatch(paired, /not paired/i);
});

test("the Inbox decision explains the change in plain words and keeps every exact identifier", () => {
  const html = view({
    activeSection: "approvals",
    candidateInbox: { state: "available", revisions: [revision] },
    candidateDecisionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  });
  assert.match(html, /What changes/);
  assert.match(html, /Nothing is there yet\./);
  assert.match(html, /How it ships/);
  assert.match(html, /It does not write to your repository by itself\./);
  assert.match(html, /Technical details/);
  assert.match(html, new RegExp("d".repeat(64)));
  assert.match(html, /action="\/actions\/decide-candidate-revision"/);
  assert.match(html, /Approve exact revision/);
  assert.doesNotMatch(html, /<button[^>]*>[^<]*(Merge|Deploy)/);
});

test("readable codes turn internal reasons into sentences", () => {
  assert.equal(readableCode("OWNER_DECISION_REQUIRED"), "Owner decision required");
  assert.equal(readableCode("probe_unavailable"), "Probe unavailable");
  assert.equal(readableCode(""), "");
});

test("the setup checklist reads real connection states and never guesses", () => {
  const unknown = view();
  assert.match(unknown, /Get Signal working/);
  assert.match(unknown, /Could not check/);
  assert.doesNotMatch(unknown, /data-state="todo"[^]*Connect Search Console/);

  const fresh = view({ gsc: { availability: "unbound" }, github: { availability: "unbound" } });
  assert.match(fresh, /data-state="todo"><span class="setup-mark"[^]*?Connect Search Console/);
  assert.match(fresh, /1 of 6 done/);

  const ready = view({
    gsc: { availability: "bound", binding_id: site, property_resource_name: "sc-domain:site.example" },
    github: { availability: "active", binding_id: site, installation_id: 1, repository_id: 1, owner: "example", repository: "site", base_branch: "main", base_sha: null, content_path: "", base_protection: "protected", failure_code: null },
  });
  assert.doesNotMatch(ready, /Get Signal working/);

  const viewer = view({
    session: { state: "authenticated", tenantId: site, role: "viewer", authenticationLevel: "primary", expiresAt: "2026-09-30T10:00:00Z", sessionVersion: 1, activeSiteId: site },
  });
  assert.doesNotMatch(viewer, /Get Signal working/);
});
