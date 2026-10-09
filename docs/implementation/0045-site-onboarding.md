# Slice 0045: Owner-Controlled Site Onboarding

- Status: Implemented and locally qualified; origin verification unavailable
- Date: 2026-09-12
- Milestone: M1/M2 partial
- Specification: Revision 3.2 sections 1.4, 2 INV-002/INV-003/INV-004,
  8.1, 8.4, 9, 24.2, and 33
- Decision: [ADR-0045](../adr/0045-owner-controlled-site-onboarding.md)
- Runbook: [Site onboarding](../runbooks/site-onboarding.md)

## Scope

This slice lets a currently authenticated organization owner create the initial
site record from the Overview. The operation creates an explicitly unverified
site, grants that owner the narrow site permission already used by harmless
snapshot commands, selects the new site in the exact tenant session, and writes
immutable evidence in one PostgreSQL transaction.

This slice does not prove origin ownership or reachability, crawl the site, connect
GSC/GitHub/Telegram, invite another member, archive/delete a site, run an SEO
agent, request approval, write to a provider, or implement product undo. Production
customer identity remains disabled by default.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0027` | Adds a private tenant/site/origin lifecycle route mirror and immutable forced-RLS onboarding events |
| Domain boundary | Validates one canonical HTTPS DNS origin, display name, IANA timezone, currency, UUIDv4 request identity, and session version |
| Atomic onboarding | Resolves the exact session, locks live owner authority, creates site and membership, selects it, and appends two evidence records |
| Concurrency and replay | Session-version compare-and-swap closes stale tabs; exact idempotent replay returns the original receipt; changed input conflicts |
| Resource bounds | Rejects duplicate current origins and tenants already holding 100 active/onboarding sites |
| Evidence | Extends the site-scoped active-context SHA-256 chain and adds a separate explicitly tenant-scoped, request-hashed `site.onboarded` event |
| API | Adds strict tenant-CSRF-protected `POST /v1/sites` with closed invalid, unauthorized, denied, conflict, limit, unavailable, and failure states |
| Dashboard BFF | Accepts one exact same-origin form, forwards only the tenant cookie, validates the response, and rejects cookie mutation |
| Dashboard view | Shows a compact owner-only Add site form and states that the newly selected site remains unverified |

## Trust And Data Flow

```text
Server-rendered owner session (tenant authority, version)
  -> same-origin Add site form (metadata, version, UUIDv4 request identity)
  -> dashboard BFF (one exact tenant cookie)
  -> tenant-bound CSRF read and strict create-site API
  -> one PostgreSQL transaction under hash-derived tenant authority
  -> unverified site + owner site grant + selected session context
  -> active-site chain event + immutable onboarding event
  -> redirect to a fresh server-rendered directory
```

The browser proposes configuration only. Tenant, actor, owner role, site grant,
session state, recovery generation, and lifecycle come from current server-side
authority. The private route mirror supports only the tenant-wide count and origin
collision checks that exact-site forced RLS cannot express; runtime roles cannot
read or write it directly.

## Verification

Run:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm run check:docs
```

The final source hashes, cumulative counts, browser viewports, dependency checks,
and cleanup state are recorded in:

- [0045 site-onboarding qualification](../evidence/0045-site-onboarding.json)

Local qualification uses a disposable loopback PostgreSQL 17.11 container and a
synthetic loopback browser fixture. No customer data, customer identity,
credentials, provider calls, or external writes are used.

The final run passed 537 cumulative real-PostgreSQL cases, 219 API cases, 672
API/identity/tooling cases, 62 dashboard cases, 20 repository cases, dashboard
typecheck and optimized build, Python lint/format for 158 files, dependency checks,
and expanded-form inspection at 1280, 980, and 390 pixels without horizontal
overflow. The documentation graph validates 125 Markdown files.

## Explicit Limits

- `onboarding` plus `unverified` is an incomplete resource state, not proof that
  the caller controls the origin or that the URL is safe/reachable.
- DNS/public-network ownership verification, canonical-origin changes, redirect
  policy, and proof expiry/recheck are the next separate authority boundary.
- The owner receives only the permission already implemented for harmless
  snapshot request authority; this does not enable the dashboard button or a
  production crawler.
- The 100-site limit is a safety bound, not evidence that Core V1 supports a
  multi-site pilot. Core V1 remains one owner-controlled site.
- Existing site rows are mirrored without normalizing history. Duplicate legacy
  origins require a separately reviewed remediation before a database-level
  uniqueness constraint could be introduced.
- There is no archive/delete UI, customer audit export, retention lifecycle,
  production deployment, or complete identity recovery path.
- No irreversible external effect occurs, so approval and undo controls are not
  invoked. Those controls remain mandatory for later GitHub/provider operations.

## Next Safe Dependency

Add exact public-origin ownership verification with explicit proof state, expiry,
recheck, failure, and canonical-origin policy. Do not infer ownership from site
creation or enable crawling/connectors before that proof is current.
