import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

async function source(path) {
  return readFile(new URL(`../../${path}`, import.meta.url), 'utf8');
}

test('dashboard workspace is pinned, reproducible, and part of the root quality gate', async () => {
  const rootPackage = JSON.parse(await source('package.json'));
  const dashboardPackage = JSON.parse(await source('apps/dashboard/package.json'));

  assert.deepEqual(rootPackage.workspaces, ['apps/dashboard']);
  for (const command of ['test:dashboard', 'typecheck:dashboard', 'build:dashboard']) {
    assert.match(rootPackage.scripts.test, new RegExp(`npm run ${command}`));
  }
  assert.equal(dashboardPackage.engines.node, '>=22 <23');
  for (const dependencies of [dashboardPackage.dependencies, dashboardPackage.devDependencies]) {
    for (const version of Object.values(dependencies)) {
      assert.match(version, /^\d+\.\d+\.\d+$/);
    }
  }
});

test('dashboard preserves the server-only, fail-closed product boundary', async () => {
  const [
    page,
    dashboardPage,
    productRoute,
    client,
    sessionClient,
    siteClient,
    findingClient,
    proposalClient,
    prepareProposalRoute,
    decideProposalRoute,
    view,
    configuration,
  ] = await Promise.all([
    source('apps/dashboard/app/page.tsx'),
    source('apps/dashboard/lib/dashboard-page.ts'),
    source('apps/dashboard/app/[section]/page.tsx'),
    source('apps/dashboard/lib/signal-api.ts'),
    source('apps/dashboard/lib/session-api.ts'),
    source('apps/dashboard/lib/site-api.ts'),
    source('apps/dashboard/lib/finding-api.ts'),
    source('apps/dashboard/lib/proposal-api.ts'),
    source('apps/dashboard/app/actions/prepare-proposal/route.ts'),
    source('apps/dashboard/app/actions/decide-proposal/route.ts'),
    source('apps/dashboard/components/dashboard-view.tsx'),
    source('apps/dashboard/next.config.ts'),
  ]);

  assert.doesNotMatch(page, /["']use client["']/);
  assert.match(client, /cache: "no-store"/);
  assert.match(client, /redirect: "error"/);
  assert.match(client, /AbortSignal\.timeout\(2500\)/);
  assert.match(client, /MAX_CAPABILITY_BYTES = 64 \* 1024/);
  assert.match(client, /productionWritesEnabled: false/);
  assert.match(dashboardPage, /getAll\(SESSION_COOKIE_NAME\)/);
  assert.match(sessionClient, /SESSION_TOKEN = \/\^\[A-Za-z0-9_-\]\{43\}\$\//);
  assert.match(sessionClient, /Cookie: `\$\{SESSION_COOKIE_NAME\}=\$\{sessionTokens\[0\]\}`/);
  assert.match(sessionClient, /cache: "no-store"/);
  assert.match(sessionClient, /redirect: "error"/);
  assert.match(dashboardPage, /reconcileDashboardSiteDirectory/);
  assert.match(siteClient, /new URL\("\/v1\/sites", baseUrl\)/);
  assert.match(siteClient, /MAX_SITE_DIRECTORY_BYTES = 64 \* 1024/);
  assert.match(siteClient, /MAX_SITES = 100/);
  assert.match(siteClient, /redirect: "error"/);
  assert.match(siteClient, /directory\.tenantId !== session\.tenantId/);
  assert.match(siteClient, /session\.activeSiteId/);
  assert.match(findingClient, /cache: "no-store"/);
  assert.match(findingClient, /redirect: "error"/);
  assert.match(findingClient, /MAX_JSON_BYTES = 32 \* 1024/);
  assert.doesNotMatch(findingClient, /NEXT_PUBLIC_/);
  assert.match(proposalClient, /MAX_JSON_BYTES = 48 \* 1024/);
  assert.match(proposalClient, /redirect: "error"/);
  assert.match(proposalClient, /revision_sha256/);
  assert.match(proposalClient, /externalWrite: false/);
  assert.doesNotMatch(proposalClient, /NEXT_PUBLIC_/);
  assert.match(prepareProposalRoute, /dashboardMutationAccepted/);
  assert.match(prepareProposalRoute, /prepareDashboardProposal/);
  assert.match(decideProposalRoute, /dashboardMutationAccepted/);
  assert.match(decideProposalRoute, /decideDashboardProposal/);
  assert.match(decideProposalRoute, /redirectToOutcome\("changes", state/);
  assert.match(view, /Production writes off/);
  assert.match(view, /Authorized sites/);
  // unavailability is stated in prose on the surface itself; it is no longer
  // implied by rendering dead controls
  assert.match(view, /Choose a site to ask about/);
  assert.match(view, /Nothing here is simulated/);
  assert.doesNotMatch(view, /placeholder="[^"]*"\n\s*disabled/);
  // no control is rendered inert on every render: a disabled attribute must be
  // driven by state, never hardcoded, and aria-disabled must not fake a tab bar
  assert.equal(view.match(/^\s*disabled$/gm), null);
  assert.doesNotMatch(view, /aria-disabled="true"/);
  assert.match(view, /data-no-synthetic-data="true"/);
  assert.match(view, /data-synthetic-evidence=/);
  assert.doesNotMatch(view, /Test fixture only/);
  assert.doesNotMatch(view, /\/actions\/analyze-fixture/);
  assert.match(view, /\/actions\/analyze-homepage/);
  assert.match(view, /Verified origin evidence/);
  assert.match(view, /\/actions\/prepare-proposal/);
  assert.match(view, /\/actions\/decide-proposal/);
  assert.match(view, /No GitHub or provider write/);
  assert.match(view, /Exact revision delivery/);
  assert.match(view, /No branch, commit, or pull request exists/);
  assert.match(view, /A human-approved draft is not a repository candidate/);
  // Home counts only recorded delivery evidence and says when it is unavailable
  assert.match(
    await source('apps/dashboard/components/home-parity.tsx'),
    /Delivery evidence unavailable/,
  );
  assert.match(view, /Performance over time/);
  assert.match(view, /Cost by category/);
  assert.match(productRoute, /isDashboardSection/);
  assert.match(productRoute, /notFound\(\)/);
  assert.doesNotMatch(view, /dangerouslySetInnerHTML/);
  assert.match(configuration, /frame-ancestors 'none'/);
  assert.match(configuration, /poweredByHeader: false/);
});

test('dashboard identity commands remain same-origin and cookie-minimal', async () => {
  const [
    dashboardPage,
    browserAuth,
    organizationClient,
    loginRoute,
    callbackRoute,
    selectionRoute,
    siteSelectionRoute,
    siteOnboardingRoute,
    originChallengeRoute,
    originVerificationRoute,
    originVerificationClient,
    logoutRoute,
    clearRoute,
  ] = await Promise.all([
    source('apps/dashboard/lib/dashboard-page.ts'),
    source('apps/dashboard/lib/browser-auth.ts'),
    source('apps/dashboard/lib/organization-api.ts'),
    source('apps/dashboard/app/auth/login/route.ts'),
    source('apps/dashboard/app/auth/callback/route.ts'),
    source('apps/dashboard/app/auth/select-organization/route.ts'),
    source('apps/dashboard/app/auth/select-site/route.ts'),
    source('apps/dashboard/app/auth/create-site/route.ts'),
    source('apps/dashboard/app/auth/origin-challenge/route.ts'),
    source('apps/dashboard/app/auth/verify-origin/route.ts'),
    source('apps/dashboard/lib/origin-verification-api.ts'),
    source('apps/dashboard/app/auth/logout/route.ts'),
    source('apps/dashboard/app/auth/clear-browser-state/route.ts'),
  ]);

  assert.match(dashboardPage, /getAll\(IDENTITY_COOKIE_NAME\)/);
  assert.match(organizationClient, /new URL\("\/v1\/organizations", baseUrl\)/);
  assert.match(organizationClient, /Cookie: `\$\{IDENTITY_COOKIE_NAME\}=\$\{identityTokens\[0\]\}`/);
  assert.match(organizationClient, /MAX_ORGANIZATION_BYTES = 64 \* 1024/);
  assert.match(browserAuth, /request\.headers\.get\("sec-fetch-site"\) !== "same-origin"/);
  assert.match(browserAuth, /if \(origin === dashboardOrigin\) return true/);
  assert.match(browserAuth, /if \(origin !== "null"\) return false/);
  assert.match(
    browserAuth,
    /dashboard\.protocol === "http:" && isLoopbackHost\(dashboard\.hostname\)/,
  );
  assert.match(browserAuth, /redirect: "manual"/);
  assert.match(browserAuth, /validatedProviderRedirect/);
  assert.match(browserAuth, /validatedResponseCookies/);
  assert.match(browserAuth, /validatedClearingCookies/);
  assert.doesNotMatch(browserAuth, /NEXT_PUBLIC_/);
  for (const route of [
    loginRoute,
    selectionRoute,
    siteSelectionRoute,
    siteOnboardingRoute,
    originChallengeRoute,
    originVerificationRoute,
    logoutRoute,
    clearRoute,
  ]) {
    assert.match(route, /dashboardMutationAccepted/);
  }
  assert.match(callbackRoute, /OIDC_BINDING_COOKIE_NAME/);
  assert.match(selectionRoute, /MAX_SELECTION_BYTES = 512/);
  assert.match(siteSelectionRoute, /MAX_SELECTION_BYTES = 512/);
  assert.match(siteSelectionRoute, /session_version/);
  assert.match(siteSelectionRoute, /selectSignalSite/);
  assert.doesNotMatch(siteSelectionRoute, /tenant_id/);
  assert.match(dashboardPage, /randomUUID\(\)/);
  assert.match(siteOnboardingRoute, /MAX_ONBOARDING_BYTES = 4096/);
  assert.match(siteOnboardingRoute, /idempotency_key/);
  assert.match(siteOnboardingRoute, /onboardSignalSite/);
  assert.doesNotMatch(siteOnboardingRoute, /tenant_id|user_id/);
  assert.match(originChallengeRoute, /issueDashboardOriginChallenge/);
  assert.match(originVerificationRoute, /verifyDashboardOrigin/);
  assert.match(originVerificationClient, /redirect: "manual"/);
  assert.match(originVerificationClient, /assertNoCookies/);
  assert.match(originVerificationClient, /MAX_JSON_BYTES = 16 \* 1024/);
  assert.doesNotMatch(originVerificationClient, /NEXT_PUBLIC_/);
  assert.match(browserAuth, /new URL\("\/v1\/session\/tenant-csrf", baseUrl\)/);
  assert.match(browserAuth, /new URL\("\/v1\/session\/site", baseUrl\)/);
  assert.match(browserAuth, /new URL\("\/v1\/sites", baseUrl\)/);
  assert.match(logoutRoute, /logoutSignalSession/);
  assert.match(clearRoute, /clearBrowserAuthCookies/);
});

test('the dashboard uses one pinned, familiar icon vocabulary', async () => {
  const [view, pkg] = await Promise.all([
    source('apps/dashboard/components/dashboard-view.tsx'),
    source('apps/dashboard/package.json'),
  ]);
  const dashboardPackage = JSON.parse(pkg);
  assert.equal(dashboardPackage.dependencies['lucide-react'], '1.43.0');
  assert.match(view, /from "lucide-react"/);
  assert.doesNotMatch(view, /from "\.\/icons"|<svg|<path|<circle|<rect/);
  await assert.rejects(source('apps/dashboard/components/icons.tsx'), {
    code: 'ENOENT',
  });
});

test('product and design records preserve the implemented interface rules', async () => {
  const [product, design, sidecar, styles] = await Promise.all([
    source('PRODUCT.md'),
    source('DESIGN.md'),
    source('.impeccable/design.json'),
    source('apps/dashboard/app/globals.css'),
  ]);
  const sectionOrder = [
    '## Overview',
    '## Colors',
    '## Typography',
    '## Elevation',
    '## Components',
    "## Do's and Don'ts",
  ].map((heading) => design.indexOf(heading));

  assert.ok(sectionOrder.every((index) => index >= 0));
  assert.deepEqual([...sectionOrder].sort((left, right) => left - right), sectionOrder);
  assert.match(product, /Never simulate readiness/);
  assert.match(design, /black-box AI interfaces/);
  assert.match(design, /navigation rail that shares/);
  assert.match(design, /Unavailable Product Ledger/);
  assert.match(design, /full browser viewport/);
  assert.match(design, /Evidence-Empty Chart/);
  // elevation and glass are bounded and purposeful, and the docs say so
  assert.match(design, /The Depth Means Layering Rule/);
  assert.match(design, /The Glass Needs Something Behind It Rule/);
  const designSidecar = JSON.parse(sidecar);
  assert.equal(designSidecar.schemaVersion, 2);
  assert.ok(designSidecar.components.length >= 11);
  assert.ok(designSidecar.components.some((component) => component.name === 'Application Frame'));
  assert.ok(designSidecar.components.some((component) => component.name === 'Evidence-Empty Chart'));
  assert.match(styles, /\.app-shell\s*{[^}]*width: 100%;[^}]*min-height: 100vh;/s);
  assert.doesNotMatch(styles, /--[a-z-]*pale:/i);
  assert.doesNotMatch(styles, /gradient\(/i);
  // blur is granted only to surfaces with live content behind them, and always
  // degrades to an opaque surface
  assert.match(styles, /@supports not \(backdrop-filter: blur\(1px\)\)/);
  // the CSS has no orphaned selector list or empty rule: a selector run must
  // end in a brace, which a careless edit to a shared selector list breaks
  // silently and invisibly
  const bare = styles.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.equal(
    (bare.match(/\{/g) ?? []).length,
    (bare.match(/\}/g) ?? []).length,
  );
  assert.equal(bare.match(/^[^{}/\n][^{}\n]*,\s*\n\s*\n/gm), null);
  assert.equal(bare.match(/\{\s*\}/g), null);
  for (const glassSurface of ['.topbar', '.decision-bar', '.mobile-navigation-panel']) {
    assert.ok(styles.includes(glassSurface));
  }
  // exactly three glass surfaces. the blur list stays literal and unprefixed:
  // the compiler collapses `backdrop-filter` and its -webkit- alias into
  // whichever is declared last, so a hand-written prefix would delete the
  // standard property Chromium needs. It adds prefixes itself per browserslist.
  assert.equal(styles.match(/backdrop-filter: saturate\(1\.8\) blur\(20px\)/g)?.length, 3);
  assert.doesNotMatch(styles, /-webkit-backdrop-filter/);
  assert.doesNotMatch(styles, /backdrop-filter: var\(/);
  // no kicker above page titles, no decorative row glyphs, no unwired controls
  assert.doesNotMatch(styles, /\.context-line|\.table-toolbar|\.chart-grid|\.chart-legend/);
  assert.doesNotMatch(styles, /\.round-icon|\.signal-bars|\.agent-logo|\.global-search|\.composer/);
});
