# Slice 0145: Dashboard Redesign (Direction C)

Product slice: presentation only. It uses the dashboard's existing data loaders,
forms and authority paths without changing any of them, adds no API, migration,
egress profile or permission, and changes no decision semantics. Based on main
`cc7f318`.

## Why

A first-principles UI audit found that the dashboard read like an engineering
console rather than an employee reporting to its owner:

- 17 navigation items, many of them readiness placeholders.
- Raw codes and hashes in the reading path, and safety notices repeated on every page.
- Contradictory copy: hard-coded "GPT-5.6 Luna", Telegram "Not paired" while paired,
  "Authority: Read-only shell" for owners, "Site ownership unverified" next to a
  verified site, and "Business facts are not implemented".
- An account label that was really a sign-out button.
- Duplicate page headings.

The owner reviewed three prototypes and chose Direction C in blue: B's structure,
A's plain-language decision screens, and Signal's own type and colour.

## Implemented

- **Visual system** (`app/globals.css`, `DESIGN.md`):
  - porcelain, ink and Signal-blue tokens
  - Bricolage Grotesque headings, Instrument Sans body, Geist Mono labels and
    identifiers, self-hosted from three pinned OFL `@fontsource-variable` packages
    (`font-src 'self'` unchanged)
  - one card language for every top-level section, ink primary buttons, worded pills
  - short motion that is fully disabled, including entrance delays, under reduced motion
- **Shell:**
  - top bar with the waveform brand, workspace, one environment badge, a Working/Idle
    state derived from real work or a running weekly cycle, and an account menu with
    an explicit Sign out form (same `/auth/logout` action)
  - grouped sidebar (Work, Content, Results, Setup) with the Inbox count; Recipes,
    Usage and Help (from the account menu) stay reachable by URL; Chat and Site audit
    appear only in the local pilot
  - the right rail and per-page footer are removed
- **Home:** built only from data `loadDashboardPage` already loads:
  - pending candidate revisions
  - opened pull requests
  - verified delivery observations
  - the weekly cycle's nine recorded stage outcomes with reasons in plain words
  - owner health checks: unknown is never called healthy, and non-owners see none
  - the SEO baseline and the sites panel

  Signed-out visitors get a welcome with Sign in and no site data.
- **Inbox decision:**
  - What changes (Now and After), What to expect, Checks passed, How it ships, and
    Technical details with every identifier
  - a decision bar with the same exact forms and hidden fields
  - Slack and Telegram requests moved into a small menu
- **Copy:**
  - plain section names and descriptions
  - readable codes in the SEO baseline, Content Writer, Webflow and weekly report
    panels; Activity keeps the truth-boundary sentence that an approved draft is not a
    pull request, deployment or measured outcome
  - the trust fixes listed above; the capability ledger moves to Settings → System
    status

## Verification

- Dashboard: 270 tests passed, including 8 new tests in `tests/home-redesign.test.tsx`
  covering:
  - real decision counts and exact revision links
  - unavailable delivery evidence
  - plain-language loop outcomes with no raw codes
  - honest health summaries
  - hidden placeholder navigation
  - the real Telegram state
  - the Inbox decision structure
- Updated assertions keep their original intent: signed-out Home shows no site data,
  every destination states that external writes are off, and capability truth now
  lives in Settings.
- Desktop (1440px) and phone (390px) renders of all 25 audited states showed no
  horizontal overflow.
- The design records match the shipped system: `DESIGN.md` and
  `.impeccable/design.json` were regenerated from the shipped CSS, and they keep the
  depth and glass rules, the Unavailable Product Ledger and the Evidence-Empty Chart.
- Two repository guard assertions changed with the shell. Neither was loosened:
  - The three glass surfaces are now `.topbar`, `.decision-bar` and
    `.mobile-navigation-panel`. The rule still requires exactly three, with the same
    blur value and an opaque fallback.
  - The removed Home "Search visibility" placeholder chart is replaced by a check that
    Home says delivery evidence is unavailable instead of inventing a number.
- `npm test` passed: 46 repository checks, the docs check, 270 dashboard tests,
  typecheck and the production build.
