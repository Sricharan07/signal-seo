import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";

import { DashboardView } from "../components/dashboard-view";
import type { DashboardCandidateRevision } from "../lib/candidate-inbox-api";
import type { OwnerInsights } from "../lib/owner-insights";

const site = "11111111-1111-4111-8111-111111111111";
const revision: DashboardCandidateRevision = {
  revisionId: "22222222-2222-4222-8222-222222222222", revisionSha256: "a".repeat(64), sealedAt: "2026-09-29T10:00:00Z",
  recipeReleaseId: "33333333-3333-4333-8333-333333333333", releaseContentHash: "b".repeat(64), baseSha: "c".repeat(40),
  patchSha256: "d".repeat(64), reviewStatus: "pending", decisionId: null, decision: null, decidedAt: null,
  sourcePath: "pricing/index.html", before: "<title>Pricing</title>", after: '<title>Pricing: plans from $0</title>\n<meta name="description" content="Plans &amp; pricing">',
  finding: { id: "55555555-5555-4555-8555-555555555555", title: "Missing meta description", summary: "The committed crawl found no description on the pricing page.", resourceLocator: "https://site.example/pricing" },
  evidence: { manifestId: "66666666-6666-4666-8666-666666666666", manifestSha256: "e".repeat(64), pageUrl: "https://site.example/pricing" },
  build: { toolchain: "node", command: "npm run build", logsSha256: "f".repeat(64), artifacts: [] },
  expectedImpact: "Make the page description available.", recoveryPlan: "Inverse patch; preserve unrelated later edits.",
  claimReviewRequired: false, reused: false,
} as DashboardCandidateRevision;

const writer = {
  schema_version: 1, cap: 2, used: 1, cap_reached: false, platform_maximum: 5, model_state: "available", candidate_state: "available",
  briefs: [{ brief_id: "77777777-7777-4777-8777-777777777777", status: "accepted", origin: "owner", supersedes_id: null, created_at: "2026-09-30T00:00:00Z", payload: { topic: "Docs as code", intent: "informational", query: "docs as code", source_ids: [], fact_ids: [], internal_links: [], kind: "new_article" } }],
  drafts: [{ draft_id: "88888888-8888-4888-8888-888888888888", brief_id: "77777777-7777-4777-8777-777777777777", created_at: "2026-10-01T00:00:00Z", fact_snapshot: [], voice_snapshot: null, result: { state: "grounded", quality: { state: "passed", reasons: [], regenerations: 0, metrics: {}, language: "en" }, originality: { state: "original", eight_gram_overlap: 0.02, lexical_window_overlap: 0.03, source_count: 1 } } }],
  candidates: [{ candidate_id: "99999999-9999-4999-8999-999999999999", draft_id: "88888888-8888-4888-8888-888888888888", revision_sha256: "9".repeat(64), review_status: "pending", created_at: "2026-10-02T00:00:00Z",
    manifest: { approval_class: "A2", autonomy_eligible: false, work_type: "new_article", grounding: { sentences: [{ path: "p1", sentence: "Search returns in 200 ms.", reasons: ["SENSITIVE_CLAIM"] }] }, originality: {}, changed_files: [{ path: "guides/docs-as-code.html", before: "", after: "<h1>Docs as code</h1><p>Docs live with code.</p><h2>Why</h2><h2>How</h2>" }] } }],
};
const fact = { fact_id: "aaaaaaaa-0000-4000-8000-000000000001", category: "proof_point", statement: "Used by 1,200 teams", status: "proposed", source_kind: "page_evidence", page_evidence_id: null, document_id: null, extracted_range: null, owner_membership_id: null, sensitive: true, supersedes_id: null, created_at: "2026-10-02T00:00:00Z", decision_id: null, extraction_id: null, provenance_url: "/x" };
const insights = {
  seo: { state: "unavailable" }, visibility: { state: "unavailable" },
  writer: { state: "available", value: writer }, facts: { state: "available", value: [fact] },
} as unknown as OwnerInsights;

function view(overrides: Record<string, unknown> = {}) {
  return renderToStaticMarkup(
    <DashboardView
      authNotice={null}
      snapshot={{ fetchedAt: "2026-10-03T10:03:00Z", connection: "connected", dependencies: "ready", inventory: "available", releaseStatus: "development", productionWritesEnabled: false, capabilities: [] }}
      session={{ state: "authenticated", tenantId: site, role: "owner", authenticationLevel: "mfa", expiresAt: "2026-10-04T10:00:00Z", sessionVersion: 3, activeSiteId: site }}
      sites={{ state: "available", tenantId: site, tenantName: "Acme", sites: [
        { id: site, name: "Docs", primaryOrigin: "https://site.example", timezone: "UTC", reportingCurrency: "USD", state: "active", ownershipStatus: "verified" },
        { id: "44444444-4444-4444-8444-444444444444", name: "Blog", primaryOrigin: "https://blog.site.example", timezone: "UTC", reportingCurrency: "USD", state: "onboarding", ownershipStatus: "unverified" },
      ] }}
      organizations={{ state: "absent" }}
      candidateInbox={{ state: "available", revisions: [revision] }}
      candidateDecisionId="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
      insights={insights}
      {...overrides}
    />,
  );
}

test("the Inbox lists fixes, articles and facts together with real counts", () => {
  const html = view({ activeSection: "approvals" });
  assert.match(html, /Inbox · 3 waiting/);
  assert.match(html, /3 decisions wait on you\./);
  assert.match(html, /href="\/approvals\?kind=article"[^>]*>Articles<span>1<\/span>/);
  assert.match(html, /New article: Docs as code/);
  assert.match(html, /Confirm “Used by 1,200 teams”/);
  assert.match(html, /class="nav-count" aria-label="3 waiting">3</);
});

test("a title and description fix shows the search result before and after", () => {
  const html = view({ activeSection: "approvals" });
  assert.match(html, /What people will see in search/);
  assert.match(html, /site\.example › pricing/);
  assert.match(html, /Pricing: plans from \$0/);
  assert.match(html, /Plans &amp; pricing/);
  assert.match(html, /action="\/actions\/decide-candidate-revision"/);
});

test("the article and fact panes keep both decision steps and never approve on their own", () => {
  const article = view({ activeSection: "approvals", inboxSelection: { article: "99999999-9999-4999-8999-999999999999" } });
  assert.match(article, /Approving the article is the first step/);
  assert.match(article, /Docs live with code\./);
  assert.match(article, /98% original/);
  assert.doesNotMatch(article, /Approve and open a pull request/);
  const factView = view({ activeSection: "approvals", inboxSelection: { fact: "aaaaaaaa-0000-4000-8000-000000000001" } });
  assert.match(factView, /Can Signal say “Used by 1,200 teams”\?/);
  assert.match(factView, /Yes, it’s accurate/);
  assert.match(factView, /Sensitive claim/);
});

test("an empty Inbox says so in words", () => {
  const html = view({ activeSection: "approvals", candidateInbox: { state: "available", revisions: [] }, insights: { ...insights, writer: { state: "unavailable" }, facts: { state: "unavailable" } } });
  assert.match(html, /Nothing waits on you\. Signal keeps working\./);
});

test("Activity logs recorded actions with what allowed them, and filters by kind", () => {
  const html = view({ activeSection: "changes" });
  assert.match(html, /What Signal did,/);
  assert.match(html, /Prepared a fix: Missing meta description\./);
  assert.match(html, /Sent “Docs as code” to you for review\./);
  const drafted = view({ activeSection: "changes", activityFilter: "measured" });
  assert.match(drafted, /Nothing of this kind recorded yet\./);
});

test("Results never invents a chart or a measured effect", () => {
  const html = view({ activeSection: "analytics" });
  assert.match(html, /What moved,/);
  assert.match(html, /No daily search data yet/);
  assert.match(html, /No change has been measured yet\./);
});

test("the Content board shows recorded work in each column", () => {
  const html = view({ activeSection: "content-writer" });
  assert.match(html, /What Signal writes next\./);
  assert.match(html, /Content · 1 of 2 drafts this week/);
  assert.match(html, /aria-label="Your review"[^]*?Docs as code/);
  assert.match(html, /href="\/approvals\?article=99999999-9999-4999-8999-999999999999"/);
  assert.match(html, /Ideas appear once Search Console data is imported\./);
});

test("navigation matches the prototype and keeps every page one tab away", () => {
  const html = view({ activeSection: "strategy" });
  for (const label of ["Home", "Inbox", "Activity", "Content", "Results", "Connections", "Autonomy"]) assert.match(html, new RegExp(`<span>${label}</span>`));
  assert.match(html, /<a href="\/content-writer" class="active" aria-current="page">/);
  assert.match(html, /class="c-subtabs"[^]*?href="\/business-brain"/);
});

test("the site switcher posts the existing site-selection form", () => {
  const html = view();
  assert.match(html, /<summary class="c-site">/);
  assert.match(html, /action="\/auth\/select-site"[^]*?name="site_id" value="44444444-4444-4444-8444-444444444444"/);
  assert.match(html, /href="\/settings#site-directory-title">Add a site/);
});

test("Autonomy shows three levels and the unsupported one cannot be chosen", () => {
  const html = view({ activeSection: "policy", standingAuthorization: { state: "available", grant: { state: "no_grant", grantId: null, recipeReleaseIds: [], workTypes: [], thresholds: {}, weeklyVolumeCaps: {}, weeklyTotalCap: null, weeklySpendCents: null, excludedPaths: [], startsAt: null, endsAt: null, recoveryWindowHours: null, restrictionEventId: null, durability: null } } });
  assert.match(html, /How much Signal/);
  assert.match(html, /Ask me first/);
  assert.match(html, /Small fixes on its own/);
  assert.match(html, /disabled=""[^>]*class="c-prod[^"]*p3/);
  assert.match(html, /Never merges/);
});

test("the top ticker counts real waiting decisions and links to the Inbox", () => {
  const html = view();
  assert.match(html, /class="app-shell has-ticker"/);
  assert.match(html, /<a class="c-ticker-go" href="\/approvals"><b>3<\/b>decisions are waiting on you/);
  const none = view({ candidateInbox: { state: "available", revisions: [] }, insights: { ...insights, writer: { state: "unavailable" }, facts: { state: "unavailable" } } });
  assert.doesNotMatch(none, /c-ticker/);
});
