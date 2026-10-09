import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import { DashboardView, dashboardSections } from "../components/dashboard-view";
import type {
  DashboardFindings,
  DashboardPageObservationState,
} from "../lib/finding-api";
import type { DashboardSnapshot } from "../lib/signal-api";
import type { DashboardSession } from "../lib/session-api";
import type { DashboardSiteDirectory } from "../lib/site-api";
import type { DashboardOrganizationDirectory } from "../lib/organization-api";
import type { DashboardProposal } from "../lib/proposal-api";
import type { DashboardWork } from "../lib/work-api";

const signedOut: DashboardSession = { state: "signed_out" };
const noSiteContext: DashboardSiteDirectory = { state: "not_authenticated" };
const noIdentityContext: DashboardOrganizationDirectory = { state: "absent" };

test("renders real capability state and explicit unavailable product boundaries", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "not_ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [
      { key: "identity.session_issuance", availability: "internal_only" },
      { key: "provider.production_writes", availability: "disabled" },
    ],
  };

  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice={null}
    />,
  );

  assert.match(html, /Your SEO and AI-search employee/);
  assert.match(html, /Signal never merges,\s+deploys, or deletes anything/);
  assert.match(html, /action="\/auth\/login"/);
  assert.match(html, /External writes off/);
  // a signed-out visitor sees no site metrics, counts or loop state at all
  assert.doesNotMatch(html, /home-tile|weekly-loop|Waiting on you/);
  assert.doesNotMatch(html, /Published|All systems operational|100%/);

  const settings = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="settings"
    />,
  );
  assert.match(settings, /System status/);
  assert.match(settings, /identity\.session_issuance/);
  assert.match(settings, /provider\.production_writes/);
  assert.match(settings, /External changes/);
  assert.match(settings, /Blocked/);
  // no control is rendered that is not wired to anything
  assert.doesNotMatch(html, /disabled=""[^>]*placeholder|placeholder[^>]*disabled=""/);
});

test("renders evidence-first chart structures without invented observations", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "not_ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };

  const analytics = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="analytics"
    />,
  );
  const usage = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="usage"
    />,
  );

  assert.match(analytics, /Performance over time/);
  assert.match(usage, /Cost by category/);
  for (const html of [analytics, usage]) {
    assert.match(html, /No observations yet/);
    assert.match(html, /No customer metrics are shown until source evidence exists/);
    assert.match(html, /data-no-synthetic-data="true"/);
    assert.doesNotMatch(html, /8,426|214K|\$38\.42|PR #42/);
    // the plot advertises no axis, grid or series it cannot populate
    assert.doesNotMatch(html, /chart-grid|chart-legend/);
  }
});

test("renders every referenced dashboard destination without fabricating capability", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "not_ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const expected = {
    chat: "Choose a site to ask about",
    work: "Select or create a site first",
    strategy: "No qualified research",
    topics: "Topics require owner access",
    "business-brain": "Select an owner-authorized site",
    "content-writer": "Select an owner-authorized site",
    pages: "No inspectable audit evidence yet",
    changes: "No sealed candidate",
    approvals: "No immutable candidate",
    analytics: "Charts remain empty",
    visibility: "Select a site first",
    recipes: "No repository recipe",
    policy: "No signed policy bundle",
    connectors: "Search Console is unavailable for this session",
    usage: "No attributed model",
    settings: "Set when the site was added",
    help: "Current public readiness",
  } as const;

  for (const section of dashboardSections.filter(
    (value) => value !== "overview",
  )) {
    const html = renderToStaticMarkup(
      <DashboardView
        snapshot={snapshot}
        session={signedOut}
        sites={noSiteContext}
        organizations={noIdentityContext}
        authNotice={null}
        activeSection={section}
      />,
    );
    assert.match(html, new RegExp(expected[section]));
    assert.match(html, /External writes off/);
    // an unbuilt destination states its prerequisites and says nothing is
    // simulated, rather than restaging a built surface's furniture
    if (section === "work") {
      assert.match(html, /Durable site audit/);
    } else if (section === "connectors") {
      assert.match(html, /GitHub is unavailable for this session/);
      assert.doesNotMatch(html, /Connect repository|Connect Search Console|>Connected</);
    } else if (section === "settings") {
      assert.match(html, /System status/);
    } else if (section !== "pages") {
      assert.match(html, /What has to be true first/);
      assert.match(html, /Nothing here is simulated/);
    }
    // the generic Site scope / Control plane / Write authority band was
    // identical on every destination; a section-specific band is still fine
    assert.doesNotMatch(html, /Site scope[\s\S]*Write authority/);
    assert.doesNotMatch(
      html,
      /Signal is working|PR #42|8,426|All systems operational/,
    );
  }
});

test("enables the durable audit only in the explicit local pilot", () => {
  const activeSiteId = "22222222-2222-4222-8222-222222222222";
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const session: DashboardSession = {
    state: "authenticated",
    tenantId: "11111111-1111-4111-8111-111111111111",
    role: "owner",
    authenticationLevel: "primary",
    expiresAt: "2026-09-09T20:30:00.000Z",
    sessionVersion: 1,
    activeSiteId,
  };
  const sites: DashboardSiteDirectory = {
    state: "available",
    tenantId: session.tenantId,
    tenantName: "Acme Search",
    sites: [
      {
        id: activeSiteId,
        name: "Acme Docs",
        primaryOrigin: "https://docs.example.test",
        timezone: "UTC",
        reportingCurrency: "USD",
        state: "onboarding",
        ownershipStatus: "unverified",
      },
    ],
  };
  const render = (localPilot: boolean) =>
    renderToStaticMarkup(
      <DashboardView
        snapshot={snapshot}
        session={session}
        sites={sites}
        organizations={noIdentityContext}
        authNotice={null}
        activeSection="work"
        localPilot={localPilot}
      />,
    );

  const unavailable = render(false);
  const local = render(true);
  assert.match(unavailable, /production crawl executor is not connected/);
  assert.match(
    unavailable,
    /<button class="primary-command" type="submit" disabled="">.*Unavailable<\/button>/s,
  );
  assert.match(local, /Local product walkthrough/);
  assert.match(local, /action="\/actions\/start-snapshot"/);
  assert.match(
    local,
    /<button class="primary-command" type="submit">.*Start audit<\/button>/s,
  );
  const unverifiedCrawl = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={sites}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="work"
      localPilot
      localVerifiedCrawl
    />,
  );
  assert.match(unverifiedCrawl, /Verified-site local crawl/);
  assert.match(unverifiedCrawl, /type="submit" disabled="".*Verify origin first<\/button>/s);
  const verifiedCrawl = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={{
        ...sites,
        sites: sites.sites.map((site) => ({ ...site, ownershipStatus: "verified" as const })),
      }}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="work"
      localPilot
      localVerifiedCrawl
    />,
  );
  assert.match(verifiedCrawl, /pinned, robots-aware network boundary/);
  assert.match(verifiedCrawl, /type="submit">.*Start audit<\/button>/s);

  const failed = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={sites}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="work"
      localPilot
      work={{
        state: "available",
        work: {
          commandId: "55555555-5555-4555-8555-555555555555",
          status: "failed",
          acceptedAt: "2026-09-09T20:00:00.000Z",
          workflowId: "crawl-site/55555555-5555-4555-8555-555555555555",
          workflowState: "failed",
          projectedAt: "2026-09-09T20:00:01.000Z",
          result: null,
          terminalReason: "crawl_activity_failed",
        },
      }}
    />,
  );
  assert.match(
    failed,
    /<li class="current"><span class="pipeline-marker">4<\/span><strong>Result<\/strong>/,
  );
  assert.match(failed, /The worker closed the run safely without a result/);
});

test("surfaces committed work on Overview and renders inspectable Pages evidence", () => {
  const activeSiteId = "22222222-2222-4222-8222-222222222222";
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const session: DashboardSession = {
    state: "authenticated",
    tenantId: "11111111-1111-4111-8111-111111111111",
    role: "owner",
    authenticationLevel: "primary",
    expiresAt: "2026-09-09T20:30:00.000Z",
    sessionVersion: 1,
    activeSiteId,
  };
  const sites: DashboardSiteDirectory = {
    state: "available",
    tenantId: session.tenantId,
    tenantName: "Acme Search",
    sites: [
      {
        id: activeSiteId,
        name: "Acme Docs",
        primaryOrigin: "https://docs.example.test",
        timezone: "UTC",
        reportingCurrency: "USD",
        state: "onboarding",
        ownershipStatus: "unverified",
      },
    ],
  };
  const work: DashboardWork = {
    state: "available",
    work: {
      commandId: "55555555-5555-4555-8555-555555555555",
      status: "succeeded",
      acceptedAt: "2026-09-09T20:00:00.000Z",
      workflowId: "signal:CrawlSite:tenant:command",
      workflowState: "succeeded",
      projectedAt: "2026-09-09T20:00:01.000Z",
      result: {
        manifestId: "66666666-6666-4666-8666-666666666666",
        manifestSha256: "a".repeat(64),
        coverage: "complete",
        discoveredCount: 1,
        terminalCount: 1,
        scopeVersion: 1,
        crawlPolicyVersion: 1,
      },
      terminalReason: null,
    },
  };
  const render = (activeSection: "overview" | "pages") =>
    renderToStaticMarkup(
      <DashboardView
        snapshot={snapshot}
        session={session}
        sites={sites}
        organizations={noIdentityContext}
        authNotice={null}
        activeSection={activeSection}
        localPilot
        work={work}
      />,
    );

  const overview = render("overview");
  const pages = render("pages");
  assert.match(overview, /Last site audit<\/span><b>Finished Sep 9<\/b>/);
  assert.match(overview, /href="\/work"[^>]*>.*Site audit/s);
  assert.doesNotMatch(overview, /No owner work read model is connected/);
  assert.match(pages, /data-synthetic-evidence="true"/);
  assert.match(pages, /Audit manifest/);
  assert.match(pages, /66666666-6666-4666-8666-666666666666/);
  assert.match(pages, new RegExp("a{64}"));
  assert.match(pages, /Local no-network executor/);
  assert.doesNotMatch(pages, /action="\/actions\/analyze-fixture"/);
  assert.doesNotMatch(pages, /Analyze test fixture/);
  assert.match(pages, /No verified homepage finding is open/);
  assert.match(pages, /Analyze the verified homepage/);
  assert.match(pages, /Verify origin first/);

  const findings: DashboardFindings = {
    state: "available",
    findings: [
      {
        findingId: "77777777-7777-4777-8777-777777777777",
        evidenceId: "88888888-8888-4888-8888-888888888888",
        commandId: "55555555-5555-4555-8555-555555555555",
        manifestId: "66666666-6666-4666-8666-666666666666",
        findingKey: "metadata.meta_description.missing",
        title: "Missing meta description",
        summary:
          "The synthetic page fixture does not contain a non-empty meta description.",
        resourceLocator: "/fixture/missing-meta-description",
        severity: "medium",
        status: "open",
        confidenceClass: "deterministic",
        sourceKind: "synthetic_fixture",
        sourceIdentifier: "fixture:local-pilot/missing-meta-description/v1",
        contentSha256: "b".repeat(64),
        evidenceObservedAt: "2026-09-09T20:00:02.000Z",
        firstSeenAt: "2026-09-09T20:00:02.000Z",
        lastSeenAt: "2026-09-09T20:00:02.000Z",
        reused: false,
      },
    ],
  };
  const findingPages = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={sites}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="pages"
      localPilot
      work={work}
      findings={findings}
    />,
  );
  assert.match(findingPages, /No verified findings/);
  assert.doesNotMatch(findingPages, /Test fixture only/);
  assert.doesNotMatch(findingPages, /Missing meta description/);
  assert.doesNotMatch(findingPages, /88888888-8888-4888-8888-888888888888/);
  assert.doesNotMatch(findingPages, new RegExp("b{64}"));
  assert.doesNotMatch(findingPages, /Re-run test detector/);
  assert.match(findingPages, /No verified homepage finding is open/);

  const verifiedSites: DashboardSiteDirectory = {
    ...sites,
    sites: sites.state === "available"
      ? sites.sites.map((site) => ({ ...site, ownershipStatus: "verified" as const }))
      : [],
  };
  const verifiedFindings: DashboardFindings = {
    state: "available",
    findings: [
      {
        ...findings.findings[0]!,
        resourceLocator: "https://docs.example.test/",
        sourceKind: "verified_origin",
        sourceIdentifier: "https://docs.example.test/",
        summary: "The verified homepage does not contain a non-empty meta description.",
      },
    ],
  };
  const pageObservation: DashboardPageObservationState = {
    state: "available",
    observation: {
      intentId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      evidenceId: verifiedFindings.findings[0]!.evidenceId,
      findingId: verifiedFindings.findings[0]!.findingId,
      commandId: verifiedFindings.findings[0]!.commandId,
      manifestId: verifiedFindings.findings[0]!.manifestId,
      origin: "https://docs.example.test",
      finalUrl: "https://docs.example.test/",
      httpStatus: 200,
      mediaType: "text/html",
      title: "Acme documentation",
      heading: "Build with Acme",
      metaDescription: null,
      bodySha256: "d".repeat(64),
      observedAt: "2026-09-09T20:00:03.000Z",
      reused: false,
    },
  };
  const verifiedPages = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={verifiedSites}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="pages"
      localPilot
      work={work}
      findings={verifiedFindings}
      pageObservation={pageObservation}
    />,
  );
  assert.match(verifiedPages, /1 verified finding/);
  assert.match(verifiedPages, /Verified origin evidence/);
  assert.match(verifiedPages, /Acme documentation/);
  assert.match(verifiedPages, /Build with Acme/);
  assert.match(verifiedPages, /action="\/actions\/analyze-homepage"/);
  assert.match(verifiedPages, /Refresh homepage evidence/);
  assert.doesNotMatch(verifiedPages, /Analyze test fixture/);
  assert.doesNotMatch(verifiedPages, /data-synthetic-evidence="true"/);

  const proposal: DashboardProposal = {
    proposalId: "99999999-9999-4999-8999-999999999999",
    proposalKind: "model_verified_homepage_metadata_draft",
    revisionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    revisionNumber: 1,
    revisionSha256: "c".repeat(64),
    siteId: activeSiteId,
    findingId: verifiedFindings.findings[0]!.findingId,
    evidenceId: verifiedFindings.findings[0]!.evidenceId,
    commandId: verifiedFindings.findings[0]!.commandId,
    evidenceObservedAt: verifiedFindings.findings[0]!.evidenceObservedAt,
    resourceLocator: "https://docs.example.test/",
    beforeValue: null,
    afterValue: "Build with Acme using clear documentation grounded in the verified homepage title and heading.",
    impact: "One owner-verified homepage metadata field",
    risk: "low",
    confidenceBasis: "Model draft constrained by verified homepage metadata",
    rationale: "The draft uses only the observed homepage title and heading.",
    tests: [
      "finding_evidence_bound",
      "model_output_schema_valid",
      "verified_homepage_target_scoped",
      "external_write_disabled",
    ],
    roleContributions: [
      { role: "technical_seo", release: "local-deterministic-v1", result: "finding_supported" },
      { role: "content_strategy", release: "verified-gpt-6-luna-metadata-v2", result: "metadata_draft_prepared" },
      { role: "independent_reviewer", release: "local-deterministic-v1", result: "scope_checks_passed" },
      { role: "coordinator", release: "local-deterministic-v1", result: "approval_requested" },
    ],
    model: {
      callId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      release: "verified-gpt-6-luna-metadata-v2",
      modelRequested: "gpt-6-luna",
      modelReported: "gpt-6-luna-2026-09-01",
      providerResponseId: "resp_signal_local_1",
      promptSha256: "d".repeat(64),
      inputSha256: "e".repeat(64),
      outputSha256: "f".repeat(64),
      store: false,
      usage: {
        inputTokens: 120,
        outputTokens: 40,
        cachedInputTokens: 20,
        totalTokens: 160,
      },
    },
    approvalClass: "A1",
    requestedAuthority: "accept_verified_homepage_metadata_draft",
    externalWrite: false,
    maximumCostMinorUnits: 1,
    currency: "USD",
    recoveryMode: "discard_local_draft",
    recoverySummary: "Discard the proposed draft; no external state has changed.",
    createdByUserId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    createdAt: "2026-09-09T20:00:03.000Z",
    approvalRequestId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    approvalStatus: "pending",
    approvalRequestedAt: "2026-09-09T20:00:03.000Z",
    approvalExpiresAt: "2026-09-10T20:00:03.000Z",
    decisionId: null,
    decision: null,
    decidedByUserId: null,
    decisionChannel: null,
    decidedAt: null,
    reused: false,
  };
  const chatWithoutProposal = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={verifiedSites}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="chat"
      localPilot
      findings={verifiedFindings}
    />,
  );
  assert.match(chatWithoutProposal, /Prepare a safe proposal/);
  assert.match(chatWithoutProposal, /action="\/actions\/prepare-proposal"/);
  assert.match(chatWithoutProposal, /Strict schema, exact evidence, no tools, no external write/);

  const approval = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={verifiedSites}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="approvals"
      localPilot
      findings={verifiedFindings}
      proposals={{ state: "available", proposals: [proposal] }}
      proposalDecisionId="dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    />,
  );
  assert.match(approval, /Exact evidence-bound revision/);
  assert.match(approval, /Build with Acme using clear documentation/);
  assert.match(approval, new RegExp("c{64}"));
  assert.match(approval, /4 passed/);
  assert.match(approval, /Verified homepage metadata draft/);
  assert.match(approval, /gpt-6-luna-2026-09-01/);
  assert.match(approval, /resp_signal_local_1/);
  assert.match(approval, /Model audit/);
  assert.match(approval, /Total tokens/);
  assert.match(approval, />160</);
  assert.match(approval, /\$0\.01/);
  assert.doesNotMatch(approval, /does not run an OpenAI model/);
  assert.match(approval, /No GitHub or provider write/);
  assert.match(approval, /action="\/actions\/decide-proposal"/);
  assert.match(approval, /Approve exact draft/);
  assert.match(approval, /Request edits/);
  assert.match(approval, /Reject/);

  const approvedProposal: DashboardProposal = {
    ...proposal,
    approvalStatus: "approved",
    decisionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    decision: "approved",
    decidedByUserId: proposal.createdByUserId,
    decisionChannel: "dashboard",
    decidedAt: "2026-09-09T20:05:00.000Z",
  };
  const changes = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={verifiedSites}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="changes"
      localPilot
      proposals={{ state: "available", proposals: [approvedProposal] }}
      proposalNotice="approved"
    />,
  );
  assert.match(changes, /Exact revision delivery/);
  assert.match(changes, /Exact draft accepted/);
  assert.match(changes, /Build with Acme using clear documentation/);
  assert.match(changes, new RegExp("c{64}"));
  assert.match(changes, /Connect repository/);
  assert.match(changes, /No branch, commit, or pull request exists/);
  assert.match(changes, /<small>Repository<\/small><strong>.*?Not bound<\/strong>/s);
  assert.match(changes, /<strong>Candidate<\/strong><small>Not built<\/small>/);
  assert.doesNotMatch(changes, /PR ready|Deployed|Verified live/);
});

test("renders an API failure without leaking internal error details", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "unreachable",
    dependencies: "unknown",
    inventory: "unavailable",
    releaseStatus: "unknown",
    productionWritesEnabled: false,
    capabilities: [],
  };

  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice={null}
    />,
  );

  assert.match(html, /API unavailable/);
  assert.match(html, /could not reach the Signal server/);
  assert.doesNotMatch(html, /stack|ECONNREFUSED|token|secret/i);
  const settings = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice={null}
      activeSection="settings"
    />,
  );
  assert.match(settings, /Capability inventory could not be verified/);
  assert.doesNotMatch(settings, /stack|ECONNREFUSED|token|secret/i);
});

test("renders only bounded server-verified session context", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const session: DashboardSession = {
    state: "authenticated",
    tenantId: "11111111-1111-4111-8111-111111111111",
    role: "owner",
    authenticationLevel: "mfa",
    expiresAt: "2026-09-09T20:30:00.000Z",
    sessionVersion: 2,
    activeSiteId: "22222222-2222-4222-8222-222222222222",
  };
  const sites: DashboardSiteDirectory = {
    state: "available",
    tenantId: session.tenantId,
    tenantName: "Acme Search",
    sites: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        name: "Acme Docs",
        primaryOrigin: "https://docs.example.test",
        timezone: "UTC",
        reportingCurrency: "USD",
        state: "onboarding",
        ownershipStatus: "unverified",
      },
    ],
  };

  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={sites}
      organizations={noIdentityContext}
      authNotice={null}
      siteOnboardingRequestId="44444444-4444-4444-8444-444444444444"
    />,
  );

  assert.match(html, /Acme Search/);
  assert.match(html, /Acme Docs/);
  assert.match(html, /https:\/\/docs\.example\.test/);
  assert.match(html, /Ownership unverified/);
  assert.match(html, /Owner \/ MFA/);
  assert.match(html, /Current/);
  assert.doesNotMatch(html, /action="\/auth\/select-site"/);
  assert.match(html, /action="\/auth\/logout"/);
  assert.match(html, /action="\/auth\/create-site"/);
  assert.match(html, /name="expected_session_version" value="2"/);
  assert.match(html, /name="idempotency_key"/);
  assert.match(html, /HTTPS origin/);
  assert.match(html, /Verify public origin/);
  assert.match(html, /Issue proof/);
  assert.match(html, /Session verified · production authority: none/);
  assert.match(html, /Signed in with 2-step verification/);
  assert.match(html, /class="account-menu"/);
  assert.doesNotMatch(html, /Customer sign-in disabled/);
  assert.doesNotMatch(html, /22222222-2222-4222-8222-222222222222/);

  const viewerHtml = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={{ ...session, role: "viewer" }}
      sites={sites}
      organizations={noIdentityContext}
      authNotice={null}
      siteOnboardingRequestId="44444444-4444-4444-8444-444444444444"
    />,
  );
  assert.doesNotMatch(viewerHtml, /action="\/auth\/create-site"/);
  assert.doesNotMatch(viewerHtml, /Issue proof/);
});

test("renders an optimistic server-owned site selection action", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const session: DashboardSession = {
    state: "authenticated",
    tenantId: "11111111-1111-4111-8111-111111111111",
    role: "owner",
    authenticationLevel: "primary",
    expiresAt: "2026-09-09T20:30:00.000Z",
    sessionVersion: 7,
    activeSiteId: null,
  };
  const sites: DashboardSiteDirectory = {
    state: "available",
    tenantId: session.tenantId,
    tenantName: "Acme Search",
    sites: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        name: "Acme Docs",
        primaryOrigin: "https://docs.example.test",
        timezone: "UTC",
        reportingCurrency: "USD",
        state: "onboarding",
        ownershipStatus: "unverified",
      },
    ],
  };

  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={sites}
      organizations={noIdentityContext}
      authNotice="site-conflict"
    />,
  );

  assert.match(html, /Site context changed/);
  assert.match(html, /Choose a site to get started/);
  assert.match(html, /action="\/auth\/select-site"/);
  assert.match(html, /name="session_version" value="7"/);
  assert.match(html, /name="site_id"/);
  assert.match(html, />Select</);
  assert.match(html, /Signal works on one site at a time/);
  assert.doesNotMatch(html, /home-tile|weekly-loop/);
  assert.doesNotMatch(html, /Request snapshot/);
});

test("renders rejected browser state without granting account authority", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "not_ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };

  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={{ state: "invalid" }}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice={null}
    />,
  );

  assert.match(html, /Clear browser state/);
  assert.match(html, /Browser session rejected/);
  assert.match(html, /action="\/auth\/clear-browser-state"/);
  assert.doesNotMatch(html, /Session verified/);
  assert.doesNotMatch(html, /class="account-menu"|action="\/auth\/logout"/);
});

test("fails closed when authenticated site authority is unavailable", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const session: DashboardSession = {
    state: "authenticated",
    tenantId: "11111111-1111-4111-8111-111111111111",
    role: "viewer",
    authenticationLevel: "primary",
    expiresAt: "2026-09-09T20:30:00.000Z",
    sessionVersion: 1,
    activeSiteId: null,
  };

  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={session}
      sites={{ state: "invalid" }}
      organizations={noIdentityContext}
      authNotice={null}
    />,
  );

  assert.match(html, /Authorized sites/);
  assert.match(html, /site directory response was rejected/);
  assert.match(html, /Site access rejected/);
  assert.doesNotMatch(html, /Request snapshot/);
});

test("renders pre-tenant organization choices without granting tenant authority", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const organizations: DashboardOrganizationDirectory = {
    state: "available",
    organizations: [
      {
        tenantId: "11111111-1111-4111-8111-111111111111",
        name: "Acme Search",
        role: "approver",
      },
    ],
  };

  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={organizations}
      authNotice="identity-ready"
    />,
  );

  assert.match(html, /Identity verified/);
  assert.match(html, /Choose organization/);
  assert.match(html, /Acme Search/);
  assert.match(html, /Approver membership/);
  assert.match(html, /action="\/auth\/select-organization"/);
  assert.match(html, /name="tenant_id"/);
  assert.match(html, /Organization selection required/);
  assert.doesNotMatch(html, /Session verified/);
});

test("renders closed auth notices without reflecting untrusted query text", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "not_ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const html = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={noIdentityContext}
      authNotice="logout-failed"
    />,
  );

  assert.match(html, /Logout service unavailable/);
  assert.match(html, /could not confirm the sign-out, so you are still signed in here/);
  assert.doesNotMatch(html, /stack|ECONNREFUSED|secret/i);
});

test("distinguishes an empty membership directory from unavailable identity", () => {
  const snapshot: DashboardSnapshot = {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [],
  };
  const empty = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={{ state: "available", organizations: [] }}
      authNotice={null}
    />,
  );
  assert.match(empty, /No organizations/);
  assert.match(empty, /no active organization membership/i);
  assert.doesNotMatch(empty, /action="\/auth\/clear-browser-state"/);

  const unavailable = renderToStaticMarkup(
    <DashboardView
      snapshot={snapshot}
      session={signedOut}
      sites={noSiteContext}
      organizations={{ state: "unavailable" }}
      authNotice={null}
    />,
  );
  assert.match(unavailable, /Identity service unavailable/);
  assert.match(unavailable, /action="\/auth\/clear-browser-state"/);
  assert.doesNotMatch(unavailable, /Session verified/);
});
