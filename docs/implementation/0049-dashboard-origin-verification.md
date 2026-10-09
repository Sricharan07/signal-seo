# Slice 0049: Dashboard Verification And UI Hardening

- Status: Implemented and locally qualified; production composition unavailable
- Date: 2026-09-12
- Milestone: M2/M4 partial
- Specification: Revision 3.2 sections 2 INV-002/INV-003/INV-004, 3.3, 8.5,
  9, 12, 24.1-24.5, 25, 31, and 33
- Decision: [ADR-0049](../adr/0049-same-origin-dashboard-verification-bff.md)
- Backend dependency: [Slice 0048](0048-origin-verification.md)
- Runbook: [Origin verification](../runbooks/origin-verification.md)

## Scope

This slice completes the browser side of the exact public-origin ownership proof
and finishes the frontend takeover against the supplied operational-dashboard
direction.
A current owner can issue a short-lived proof, publish its exact URL and plaintext
content, ask Signal to verify it, and then reload the server-owned site projection.
The same slice reconciles the completed frontend redesign with the standard Lucide
icon vocabulary and repairs its mobile navigation overlay.

This slice does not configure production identity or resolution, schedule rechecks,
bind GSC/GitHub/Telegram, ingest evidence, run agents, approve changes, write to a
provider, or implement undo.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Challenge BFF | Exact same-origin JSON route extracts one tenant cookie, obtains tenant CSRF, and issues one exact-site challenge |
| Verification BFF | Forwards one UUIDv4 challenge/request pair and exact HTTPS origin through the same closed authority boundary |
| Response validation | Accepts exact fields, UUIDv4 identities, proof method/URL/content, UTC timestamps, 30-minute challenge lifetime, 30-day recheck, and no `Set-Cookie` |
| Owner interaction | Shows proof control only for the selected owner site in `unverified` or `reverification_required` state |
| Browser state | Retains challenge URL/content only in React memory; exposes labeled copy/open/verify commands and explicit closed errors |
| Site projection | Accepts `unverified`, `verified`, and `reverification_required`; rejects non-HTTPS origins and unknown states |
| Operational hierarchy | Uses one cool working ground, white elevated modules, compact fact bands, restrained solid-blue actions, and an unframed full viewport |
| Honest empty data | Keeps the requested graph surfaces but removes invented axes, legends, trends, and chart furniture until real observations exist |
| Unavailable destinations | Replaces repeated pseudo-tables and decorative row icons with concise unboxed prerequisite ledgers |
| Controls | Removes the permanently disabled global-search field; every rendered command is actionable or explicitly contextual |
| Icon vocabulary | Uses pinned Lucide symbols directly and removes the parallel custom glyph implementation |
| Responsive repair | Makes the mobile disclosure panel fill the viewport below the sticky toolbar and scroll independently |

## Visual Audit

| Finding | Resolution |
| --- | --- |
| Previous surfaces mixed borders, shadows, and repeated card treatments | Consolidated hierarchy into four documented elevation levels and hairlines only between related rows |
| Empty charts still rendered visual conventions that implied observations | Kept stable graph modules but reduced each plot to an explicit evidence-empty field and connector action |
| Permanently disabled global search looked like a shipped command | Removed it until a real tenant-scoped query contract exists |
| Repeated category icons added noise to prerequisite rows | Reserved icons for navigation, commands, and actual semantic state |
| Custom glyphs duplicated familiar controls | Standardized direct imports on pinned Lucide icons |
| Mobile navigation collapsed to a 48-pixel strip | Removed the filtered-ancestor containment failure and verified a 784-pixel scrollable overlay at 390x844 |

The reference supplies composition and interaction direction only. Its commerce
data, decorative AI object, totals, customers, products, and trends are not copied.

## Trust And Data Flow

```text
Server-rendered owner + selected unverified/recheck-required site
  -> same-origin browser JSON command
  -> Next.js BFF validates origin proof, body, and exact tenant cookie
  -> BFF obtains transient tenant CSRF from Signal API
  -> strict challenge or verification mutation
  -> exact bounded response with no cookie mutation
  -> transient owner-visible proof or verified result
  -> server page reload reads current site truth
```

Tenant, actor, role, selected site, current ownership state, and durable result
remain server-owned. The browser supplies only the rendered site ID and origin plus
fresh UUIDv4 request identity and, for verification, the issued challenge ID.

## Verification

Run:

```sh
npm run test:dashboard
npm run test:repo
npm run typecheck:dashboard
npm run build:dashboard
npm run check:docs
npm audit --omit=dev
.venv/bin/python -m pip check
node <impeccable-skill>/scripts/detect.mjs \
  --json apps/dashboard/app/globals.css \
  apps/dashboard/components/dashboard-view.tsx \
  apps/dashboard/components/origin-verification.tsx
git diff --check
```

Exact counts, source hashes, browser measurements, dependency checks, and the
secret scan are recorded in
[0049 dashboard origin qualification](../evidence/0049-dashboard-origin-verification.json).

## Explicit Limits

- The default FastAPI process still lacks the credential-bearing identity gateway
  and controlled resolver, so a local owner journey is not production-composed and
  the BFF returns a closed unavailable/rejected state without valid authority.
- The UI cannot change or revoke an origin and does not persist a challenge across
  a browser reload. The owner can issue a fresh bounded challenge instead.
- No scheduler marks an overdue proof `reverification_required`; the UI only
  consumes that state when the server supplies it.
- Verification still grants no crawl, connector, repository, CMS, approval, merge,
  deployment, or production-write authority.
- Graphs remain evidence-empty until a real tenant/site-scoped analytics source is
  connected; no reference metrics are rendered as customer data.

## Next Safe Dependency

Deployment-qualify identity, active-site selection, onboarding, proof, and the
controlled resolver together, then add the least-privilege Google Search Console
property binding and real analytics read model.
