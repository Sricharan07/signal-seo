# Slice 0157: Dashboard Cleanup

This is a product slice. It covers the dashboard-side findings of an independent code audit (2026-10-04).
Python-side cleanup and the findings in files that open slices are editing follow separately. Based on main
`2dbc656`.

## Fixed

- **DataForSEO settings were unreachable in every deployment.**
  - `lib/dataforseo-api.ts` read `SIGNAL_API_URL`, which nothing sets. Every deployment sets
    `SIGNAL_API_BASE_URL`, so the panel always called `127.0.0.1:8000` and showed unavailable.
  - It now reads `SIGNAL_API_BASE_URL` like every other client.
  - The module's stricter rule (plain http only for localhost) is unchanged.
  - A regression test fails without the fix.
- **Orphaned route removed.** Nothing referenced `/actions/analyze-fixture` or `analyzeDashboardFixture`, so the
  route, the client function and their tests are gone. The repository test that read the route file no longer
  does. Its assertion that the UI never links the route is kept, and the API fixture endpoint stays for
  qualification.
- **Dead CSS removed.**
  - Seven classes with no references: `.empty-ledger`, `.detail-rail`, `.attention-list`, `.settings-fields`,
    `.overview-work-row`, `.rail-copy`, `.fixture-boundary`.
  - Three unused custom properties: `--radius`, `--t-display`, `--detail-width`.
  - Mixed selector lists keep their live selectors.
- **Old section names replaced.** "Business Brain", "Content Writer" and "AI visibility" inside the renamed pages
  now read Business facts, Articles and AI answers. That covers notices, refresh labels, the Docs extraction note
  and the Webflow candidate label, plus the matching API error messages.
- **One label source.**
  - The Settings health list used its own names ("Temporal", "Outbox backlog") that disagreed with Home ("Workflow
    engine", "Outgoing work backlog"). Both now use `HEALTH_LABELS`.
  - Health reasons, IndexNow states and Business facts labels use `readableCode`.

## Verification

- Dashboard: 269 tests passed. One test was added; the three removed tests belonged to the deleted route and
  function.
- Repository: 46 checks passed, including the CSS guards (balanced braces, no empty rules, no orphaned selector
  lists).
- API: the business-facts and articles route tests passed (47).
- Typecheck and build pass in `npm test`.
- All 25 states render at desktop and phone width without horizontal overflow.
