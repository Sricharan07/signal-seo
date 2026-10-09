import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { DashboardView } from "../components/dashboard-view";
import type { DashboardDeliveryObservation, DashboardDeliveryObservations } from "../lib/github-delivery-api";
import type { DashboardCandidateRevision } from "../lib/candidate-inbox-api";

const site = "11111111-1111-4111-8111-111111111111";
const revision: DashboardCandidateRevision = { revisionId: "22222222-2222-4222-8222-222222222222", revisionSha256: "a".repeat(64), sealedAt: "2026-09-29T10:00:00Z", recipeReleaseId: "33333333-3333-4333-8333-333333333333", releaseContentHash: "b".repeat(64), baseSha: "c".repeat(40), patchSha256: "d".repeat(64), reviewStatus: "approved", decisionId: "44444444-4444-4444-8444-444444444444", decision: "approved", decidedAt: "2026-09-29T10:01:00Z", sourcePath: "index.html", before: "", after: '<meta name="description" content="Evidence description">', finding: { id: "55555555-5555-4555-8555-555555555555", title: "Missing meta description", summary: "The committed crawl found no description on the homepage.", resourceLocator: "https://site.example/" }, evidence: { manifestId: "66666666-6666-4666-8666-666666666666", manifestSha256: "e".repeat(64), pageUrl: "https://site.example/" }, build: { toolchain: "node", command: "npm run build", logsSha256: "f".repeat(64), artifacts: [{ path: "_site/index.html", sha256: "1".repeat(64), size: 123 }] }, expectedImpact: "Make the page description available.", recoveryPlan: "Inverse patch; preserve unrelated later edits.", claimReviewRequired: true, reused: false };
const operationId = "77777777-7777-4777-8777-777777777777";
const observed: DashboardDeliveryObservation = { attemptId: "88888888-8888-4888-8888-888888888888", operationId, state: "completed", revisionSha256: revision.revisionSha256, receiptSha256: "2".repeat(64), canonicalReceipt: '{"synthetic_test_evidence":true}', outcome: "verified", reason: "EXACT_SEALED_RESULT", stage: "deployed", checksCount: 1, passedCount: 1, mergedSha: "3".repeat(40), deploymentId: 81, fetchedSha256: "4".repeat(64), postconditions: [{ field: "meta_description", matched: true, expected: "Evidence description", observed: ["Evidence description"] }], recoveryPlan: revision.recoveryPlan, observedAt: "2026-09-29T10:03:00Z", nextObserveAt: "2026-09-29T10:03:30Z" };

export function deliveryFixture(overrides: Partial<DashboardDeliveryObservation> = {}, boundary?: DashboardDeliveryObservations) {
  return <DashboardView activeSection="changes" authNotice={null} localPilot={true}
    snapshot={{ fetchedAt: "2026-09-29T10:03:00Z", connection: "connected", dependencies: "ready", inventory: "available", releaseStatus: "development", productionWritesEnabled: false, capabilities: [] }}
    session={{ state: "authenticated", tenantId: site, role: "owner", authenticationLevel: "mfa", expiresAt: "2026-09-30T10:00:00Z", sessionVersion: 1, activeSiteId: site }}
    sites={{ state: "available", tenantId: site, tenantName: "Synthetic test organization", sites: [{ id: site, name: "Synthetic test site", primaryOrigin: "https://site.example", timezone: "UTC", reportingCurrency: "USD", state: "active", ownershipStatus: "verified" }] }}
    organizations={{ state: "absent" }} candidateInbox={{ state: "available", revisions: [revision] }}
    githubPrOperations={{ state: "available", operations: [{ operationId, revisionId: revision.revisionId, revisionSha256: revision.revisionSha256, branchName: "signal/" + "7".repeat(32), state: "opened", step: "done", baseSha: revision.baseSha, expectedTreeSha: "d".repeat(40), expectedCommitSha: "e".repeat(40), prNumber: 7, prUrl: "https://github.com/SignalOwner/website/pull/7", journalGeneration: site, journalPosition: 3, journalBodyHash: "f".repeat(64), createdAt: revision.sealedAt, updatedAt: observed.observedAt }] }}
    deliveryObservations={boundary ?? { state: "available", observations: [{ ...observed, ...overrides }] }} />;
}

test("delivery stages expose only recorded evidence and never offer merge or deploy", () => {
  const html = renderToStaticMarkup(deliveryFixture());
  assert.match(html, /Exact page verified/); assert.match(html, /Fetched page digest/);
  assert.match(html, /Exact observation evidence/); assert.match(html, /not a signed delivery certification/);
  assert.doesNotMatch(html, /<button[^>]*>[^<]*(Merge|Deploy)/);
  assert.equal((html.match(/<small>Evidence recorded<\/small>/g) ?? []).length, 5);
});

test("green checks and merge never claim live delivery", () => {
  const html = renderToStaticMarkup(deliveryFixture({ stage: "merged", deploymentId: null, fetchedSha256: null, outcome: "not_yet_deployed", postconditions: [] }));
  assert.match(html, /Waiting for customer delivery/);
  assert.equal((html.match(/<small>Evidence recorded<\/small>/g) ?? []).length, 3);
  assert.doesNotMatch(html, /Exact page verified|Fetched page digest/);
});

test("inconclusive and regressed pages expose observed mismatch and sealed recovery", () => {
  for (const outcome of ["inconclusive", "regressed"] as const) {
    const html = renderToStaticMarkup(deliveryFixture({ outcome, reason: "EC_123_POSTCONDITION_MISMATCH", postconditions: [{ field: "meta_description", expected: "Evidence description", observed: ["Other\u202e"], matched: false }] }));
    assert.match(html, /owner review required/); assert.match(html, /Recovery plan/);
    assert.match(html, /Mismatched/); assert.match(html, /\\u202e/);
    assert.match(html, /No revert has been dispatched/); assert.doesNotMatch(html, /Exact page verified/);
  }
});

test("empty, invalid, stale revision and lost observation never invent progress", () => {
  for (const boundary of [{ state: "available", observations: [] }, { state: "invalid" }, { state: "unavailable" }] as DashboardDeliveryObservations[]) {
    const html = renderToStaticMarkup(deliveryFixture({}, boundary));
    assert.match(html, /No delivery observation recorded|Delivery evidence unavailable/);
    assert.equal((html.match(/<small>Evidence recorded<\/small>/g) ?? []).length, 1);
  }
  const stale = renderToStaticMarkup(deliveryFixture({ revisionSha256: "0".repeat(64) }));
  assert.match(stale, /No delivery observation recorded/);
  const lost = renderToStaticMarkup(deliveryFixture({ state: "outcome_unknown", revisionSha256: null, canonicalReceipt: null, outcome: null }));
  assert.match(lost, /Observation outcome unknown/);
});

test("an Inbox link selects the exact sealed revision instead of the first item", () => {
  const selected = { ...revision, revisionId: "99999999-9999-4999-8999-999999999999", revisionSha256: "9".repeat(64), after: "Exact selected after value", reviewStatus: "pending" as const, decision: null, decisionId: null, decidedAt: null };
  const html = renderToStaticMarkup(<DashboardView {...deliveryFixture().props}
    activeSection="approvals" selectedCandidateRevisionId={selected.revisionId}
    candidateDecisionId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    candidateInbox={{ state: "available", revisions: [revision, selected] }} />);
  assert.match(html, /Exact selected after value/);
  assert.match(html, /name="revision_id" value="99999999-9999-4999-8999-999999999999"/);
  assert.match(html, /name="revision_sha256" value="9999999999999999/);
  assert.match(html, /aria-current="true"/);
  assert.doesNotMatch(html, /name="revision_id" value="22222222/);
});

test("an unknown Inbox revision never displays or approves a different patch", () => {
  const html = renderToStaticMarkup(<DashboardView {...deliveryFixture().props}
    activeSection="approvals" selectedCandidateRevisionId="not-a-sealed-revision" />);
  assert.match(html, /Inbox unavailable/);
  assert.doesNotMatch(html, /action="\/actions\/decide-candidate-revision"|Evidence description/);
});

test("Changes retains the exact Slack owner decision source", () => {
  const fixture = deliveryFixture();
  const operation = fixture.props.githubPrOperations.operations[0];
  const html = renderToStaticMarkup(<DashboardView {...fixture.props}
    githubPrOperations={{ state: "available", operations: [{ ...operation,
      authority: { kind: "owner_inbox", recordId: revision.decisionId!, ownerUserId: site, decisionChannel: "slack" },
    }] }} />);
  assert.match(html, /Owner Inbox approval/);
  assert.match(html, /Decision channel: Slack/);
  assert.match(html, new RegExp(revision.decisionId!));
});

test("Changes retains the exact Telegram owner decision source", () => {
  const fixture = deliveryFixture();
  const operation = fixture.props.githubPrOperations.operations[0];
  const html = renderToStaticMarkup(<DashboardView {...fixture.props}
    githubPrOperations={{ state: "available", operations: [{ ...operation,
      authority: { kind: "owner_inbox", recordId: revision.decisionId!, ownerUserId: site, decisionChannel: "telegram" },
    }] }} />);
  assert.match(html, /Owner Inbox approval/);
  assert.match(html, /Decision channel: Telegram/);
  assert.match(html, new RegExp(revision.decisionId!));
});

test("article Changes retains exact dashboard owner authority and rejects stale observation", () => {
  const fixture = deliveryFixture();
  const operation = fixture.props.githubPrOperations.operations[0];
  const props = { ...fixture.props, candidateInbox: { state: "available" as const, revisions: [] },
    githubPrOperations: { state: "available" as const, operations: [{ ...operation,
      authority: { kind: "owner_editorial" as const, recordId: site, ownerUserId: site, decisionChannel: "dashboard" as const },
    }] } };
  const html = renderToStaticMarkup(<DashboardView {...props} />);
  assert.match(html, /Article changes/);
  assert.match(html, /Owner editorial approval/);
  assert.match(html, /Dashboard/);
  assert.match(html, /Exact page verified/);
  assert.doesNotMatch(html, /Technical changes/);
  const stale = renderToStaticMarkup(<DashboardView {...props}
    deliveryObservations={{ state: "available", observations: [{ ...observed, revisionSha256: "0".repeat(64) }] }} />);
  assert.match(stale, /No delivery observation recorded/);
});

export function astroInboxFixture() {
  const current: DashboardCandidateRevision = { ...revision, reviewStatus: "pending", decision: null, decisionId: null, decidedAt: null,
    approvalClass: "A4", sourcePath: "src/pages/dates/[slug].astro",
    builtImpact: { pageCount: 3, pages: ["dist/dates/one/index.html", "dist/dates/three/index.html", "dist/dates/two/index.html"],
      scopeSha256: "5".repeat(64), lockfileSha256: "6".repeat(64),
      samples: [{ path: "dist/dates/one/index.html", before: "Calendar", after: "Calendar One" }] } };
  return <DashboardView {...deliveryFixture().props} activeSection="approvals"
    candidateDecisionId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    candidateInbox={{ state: "available", revisions: [current] }} />;
}

test("Astro approval displays shared reach, sample HTML and fresh-MFA authority", () => {
  const html = renderToStaticMarkup(astroInboxFixture());
  assert.match(html, /A4.*3 built pages/);
  assert.match(html, /Shared-template change/);
  assert.match(html, /MFA within 5 minutes/);
  assert.match(html, /No standing authorization/);
  assert.match(html, /Built HTML impact/);
  assert.match(html, /Calendar One/);
  assert.match(html, /Exact affected pages \(3\)/);
  assert.doesNotMatch(html, /action="\/actions\/slack\/approval"/);
});

test("Home says when delivery evidence cannot be read instead of showing nothing live", () => {
  const fixture = deliveryFixture();
  const html = renderToStaticMarkup(<DashboardView {...fixture.props} activeSection="overview" deliveryObservations={{ state: "unavailable" }} />);
  assert.match(html, /Delivery evidence unavailable: live checks could not be read/);
});
