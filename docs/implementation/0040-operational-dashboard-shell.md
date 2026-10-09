# Slice 0040: Operational Dashboard Shell

- Status: Implemented and locally qualified; authenticated owner workflows unavailable
- Date: 2026-09-09
- Milestone: M2 partial
- Specification: Revision 3.2 sections 1.3, 3.1, 24.1, 24.2 Overview,
  24.4, 24.5, and Milestone 2
- Decision: [ADR-0040](../adr/0040-server-rendered-dashboard-truth-boundary.md)

## Scope

This slice creates the first visible Signal product surface: a responsive Next.js
operational shell whose Overview route renders the existing real Signal API
readiness and capability inventory. It establishes persistent information
architecture, explicit environment/authority state, honest empty states, a mobile
navigation disclosure, and a documented visual system.

It does not implement customer authentication, organization/site selection,
onboarding, conversation storage, commands, approvals, analytics, connectors,
notifications, pause, changes, recovery, or undo. Those destinations remain
visibly disabled; the shell contains no fabricated activity or metrics.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Next.js workspace | Pinned Next.js 16, React 19, TypeScript, TSX test runner, and Lucide icons under the root lockfile and quality command |
| Server read client | Validates one trusted API origin, dispatches parallel no-store reads with rejected redirects and 2.5-second timeouts, and never exposes the origin to browser code |
| Capability contract | Accepts only exact version-one fields, closed availability values, unique bounded keys, at most 128 entries, and at most 64 KiB |
| Truthful projection | Distinguishes connected, not-ready, unreachable, misconfigured, and invalid inventory states; every fallback disables production writes |
| Overview | Shows control-plane connectivity, dependency readiness, external-write authority, exact capability inventory, blockers, current-work emptiness, and update time |
| Navigation | Includes every required Section 24 destination; only Overview is active and all unimplemented destinations expose locked semantics |
| Responsive UI | Uses stable desktop/two-column/mobile breakpoints, keyboard focus, screen-reader labels, non-color status cues, and reduced-motion handling |
| Browser policy | Denies framing, objects, off-origin connections, and referrer disclosure; development-only eval support is omitted in production |
| Design record | Root `PRODUCT.md`, `DESIGN.md`, and the Impeccable sidecar preserve the product and visual decisions used by later screens |

## Data Flow

The dashboard stores no business data, customer data, credentials, or local
server state. For each request, the server component reads public control-plane
status, validates it, derives a bounded `DashboardSnapshot`, renders HTML, and
discards the response body. `cache: no-store` prevents the first shell from
creating an accidental cross-request state cache.

The capability inventory contains only release status, production-write posture,
and named availability categories. It is not permission. Future authenticated
BFF reads must derive tenant/site context from the current server-side session and
must never reuse this public projection as authorization.

## Verification

Run:

```sh
npm ci --ignore-scripts
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm run check:docs
npm audit --omit=dev
```

Nine dashboard tests cover successful and not-ready responses; malformed,
duplicate, oversized, and unreachable responses; credential-bearing
configuration; safe server-rendered empty/failure states; and production versus
development CSP behavior. Nineteen repository tests cover workspace pinning,
quality-gate composition, the server-only boundary, fail-closed defaults, response
bounds, and security posture. The unchanged API, identity, and tooling suite passes
624 cases, and the repository documentation graph passes across 112 Markdown files.

Manual browser verification against the real local FastAPI process covers a
1440-by-900 desktop viewport and a 390-by-844 mobile viewport. The mobile layout
has no horizontal overflow, all required state remains readable, and the
navigation disclosure is operable. A stopped API produces the explicit
`API unavailable` and `Unknown` state instead of stale or fabricated health.

- [0040 dashboard qualification](../evidence/0040-dashboard.json)

The pushed private-repository Quality run `34397200180` did not execute either
job. GitHub rejected both before checkout because the account's payments or Actions
spending limit require attention. This is an external CI availability block, not a
passing remote qualification or an observed code failure. Local gates above remain
the only Slice 0040 runtime evidence until a runner can execute the workflow.

## Explicit Limits

- The shell has no customer authentication or tenant/site BFF boundary and must
  not be deployed as an owner interface.
- Readiness remains `Needs setup` under the default API because no authoritative
  dependency probe is configured.
- Navigation, search, notifications, pause, site connection, chat, and send
  controls are intentionally unavailable.
- There is no loading or stale-data projection yet because this server-rendered
  route has neither client revalidation nor authenticated cached server state.
- The CSP still permits inline Next.js runtime scripts. A nonce-based production
  contract must be qualified with the authenticated deployment.
- Automated and manual checks in this slice are not a complete WCAG 2.2 AA audit.

## Next Safe Dependency

Add the authenticated server-side dashboard session/BFF boundary and owner
identity transition, then derive organization/site context from current server
authority before enabling site onboarding or any mutable control.
