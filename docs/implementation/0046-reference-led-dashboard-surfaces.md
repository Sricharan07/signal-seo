# Slice 0046: Reference-Led Dashboard Surfaces

- Status: Implemented and locally qualified; product workflows remain unavailable
- Date: 2026-09-12
- Milestone: M2/M4 partial
- Specification: Revision 3.2 sections 8.1-8.5, 24.1-24.5, 32, and 33
- Decision: [ADR-0046](../adr/0046-truthful-full-dashboard-information-architecture.md)

## Scope

This slice redesigns the dashboard around the supplied operational UI references
and exposes every named product destination: Overview, Signal Chat, Work, Strategy,
Pages, Changes, Approvals, Analytics, Recipes, Policy, Connectors, Usage, Settings,
and Help.

The references define composition and visual direction only. Their sample tasks,
metrics, connector states, specialists, pull requests, approvals, budgets, and
timestamps are not runtime inputs. This slice does not add any of those capabilities,
does not change PostgreSQL data, and does not grant external-write authority.

## Implementation

| Component            | Implemented behavior                                                                                                                                 |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Route map            | Adds one explicit allowlisted dynamic route for the thirteen non-Overview destinations; unknown sections return Next.js not found                    |
| Shared page loader   | Centralizes no-store readiness, session, organization, and site reads plus tenant reconciliation for every page                                      |
| Application frame    | Introduces a light navigation rail, flexible main canvas, contextual detail rail, mobile top bar, and disclosure navigation                          |
| Overview             | Preserves real readiness, capability, identity, organization, site selection, and owner onboarding projections and commands                          |
| Product destinations | Render domain-specific requirements and current unavailable states rather than mock customer records                                                 |
| Chat                 | Shows an empty conversation surface and disabled composer until durable scoped conversation and command contracts exist                              |
| Settings             | Displays only server-validated selected-site metadata in read-only fields; business facts and mutable controls remain unavailable                    |
| Visual system        | Updates `DESIGN.md` and its structured sidecar for the light rail, three-column frame, detail rail, availability ledgers, and responsive breakpoints |

## Truth And Authority Boundary

```text
Requested allowlisted dashboard route
  -> shared dynamic server loader
  -> bounded readiness + exact cookie-scoped identity/site reads
  -> tenant/site reconciliation
  -> real Overview projections or truthful domain readiness ledger
  -> no browser API origin, credentials, synthetic records, or new mutation
```

Existing server-owned commands remain limited to login, organization selection,
active-site selection, owner onboarding of an unverified site, logout, and explicit
local browser-state cleanup. A visible button is never treated as authority. Disabled
product controls explain their missing contract rather than suggesting a successful
or pending operation.

## Responsive Contract

- Wide screens use a 222-pixel navigation rail and 338-pixel contextual detail rail.
- At 1260 pixels the navigation rail becomes more compact.
- At 1080 pixels the detail rail moves below the working canvas.
- At 720 pixels navigation becomes a disclosure panel and page content is one column.
- At 520 pixels settings tabs form a complete three-column wrapping grid and data
  rows collapse without hiding current-state text.

All fourteen destinations were inspected at 1440 by 900 and 390 by 844 with no
horizontal overflow. Overview and Analytics were also inspected at the wide and
stacked-detail layouts, Settings at mobile, and the mobile navigation disclosure in
its expanded state. Browser console warning/error output was empty.

## Verification

Run:

```sh
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm run check:docs
npm test
npm audit --omit=dev
.venv/bin/python -m pip check
git diff --check
```

The final source hashes, exact test counts, build routes, browser checks, dependency
integrity, secret scan, and cleanup state are recorded in:

- [0046 dashboard-surface qualification](../evidence/0046-dashboard-surfaces.json)

No database or provider contract changed, so this slice does not claim a new real
PostgreSQL, WordPress, GSC, GitHub, Telegram, or production deployment qualification.
The aggregate repository test remains the regression gate for all implemented code.

## Explicit Limits

- Non-Overview destinations are navigable information architecture and honest
  readiness views, not operational implementations of the named capability.
- There are no customer analytics, tasks, proposals, approvals, recipes, policy
  bundles, connector bindings, provider health records, budgets, or chat messages.
- The dashboard cannot execute, merge, deploy, approve, recover, or undo a change.
- Existing owner onboarding remains unverified configuration intake. It is not proof
  of origin ownership and does not enable crawling or connectors.
- Future live pages require tenant/site-scoped read models, strict API/BFF contracts,
  positive/negative/failure tests, and real-provider qualification when applicable.

## Next Safe Dependency

Continue the existing M1/M2 path with exact public-origin ownership verification.
Once a page-specific backend read model exists, replace only that destination's
readiness ledger without weakening the shared truth, authority, or responsive rules.
