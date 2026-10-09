# Slice 0042: Authenticated Site Context

- Status: Implemented and locally qualified; live customer login unavailable
- Date: 2026-09-09
- Milestone: M1/M2 partial
- Specification: Revision 3.2 sections 2 INV-002/INV-003, 3.1, 7.2, 10,
  24.1, 24.2 Overview, and 24.5
- Decision: [ADR-0042](../adr/0042-hash-bound-site-directory.md)

## Scope

This slice carries one authenticated, current-authority site directory from real
PostgreSQL through FastAPI and the Next.js server boundary into the Overview. It
lets the rendered product identify the organization and every non-archived site
currently granted to the tenant session without trusting browser-owned scope or
weakening forced site RLS.

It does not activate login, invitations, site creation/selection, origin
verification, connectors, work reads, snapshot submission from the dashboard, or
production changes. The default local browser remains signed out.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0025` | Backfills and transactionally maintains a private UUID-only user/site routing index without changing existing RLS policies |
| PostgreSQL directory | Revalidates exact child/parent sessions, recovery generation, user, tenant membership/lifecycle, site membership/permission, and site state; bounds results at 100 |
| Control-plane projection | Distinguishes valid zero-site state from invalid authority and validates UUIDs, display text, canonical origins, IANA timezones, currencies, and closed states |
| FastAPI route | `GET /v1/sites` reads only the exact host-only tenant cookie and returns one strict version-one tenant/site projection or stable safe failure |
| Dashboard server reader | Forwards only one validated token with no-store, redirect rejection, 2.5-second timeout, 64 KiB body, exact fields, duplicate-site rejection, and no raw error |
| Authority reconciliation | Renders site data only when its tenant ID exactly matches the independently verified current-session tenant |
| Overview | Shows organization name, authorized-site count, names, canonical origins, onboarding/active state, ownership status, and explicit missing work/provider authority |

## Data Flow And Storage

`app.site_memberships` remains authoritative. The new
`control.user_site_membership_routes` table contains no names, origins,
permissions, cookies, provider tokens, or customer content; it stores only the
UUIDs needed to find candidate site scopes under forced RLS. A trigger updates it
in the same transaction as site-membership changes.

The browser credential remains only in the secure host-only cookie. Next.js sends
one exact value to the internal API from the server, and neither layer stores or
renders it. The API gateway hashes the token, reads the current recovery generation
from the existing external authority, and invokes the bounded PostgreSQL function.
PostgreSQL rechecks authoritative session and membership rows before each result.

The response is a transient read projection. It is not cached or persisted by the
dashboard. It contains tenant/site UUIDs internally for reconciliation, but the UI
renders names/origins/states rather than raw IDs. Site membership proves visibility
only; it does not prove origin ownership, connector health, command authority, or
permission to make an external change.

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

The disposable PostgreSQL 17.11 lab passes 492 cases and confirms cleanup. The API
suite has 184 passing cases, including the composed gateway and strict site route.
The dashboard has 25 passing tests for session/status/site readers and rendered
states; the repository suite remains 19 passing tests. The complete non-database
API, identity, and tooling regression passes 637 cases.

- [0042 authenticated site-context qualification](../evidence/0042-authenticated-site-context.json)

GitHub Actions remains externally unavailable because the account's billing or
Actions spending state blocks jobs before checkout. No remote runtime claim is made.

## Explicit Limits

- The default process does not compose customer database, Keycloak, OpenBao, or
  browser-security credentials, so the route returns 503 until deployed correctly.
- No real customer or provider credential was used. Browser authenticated-state
  rendering is contract-tested with synthetic data, not provider-qualified.
- The UI does not choose or persist an active site when multiple grants exist.
- The current `ownership_status` schema supports only `unverified`; origin
  verification is a later authority-bearing slice.
- The directory is bounded to 100 sites and has no cursor; Core V1 needs one site.
- There is still no work read model, dashboard mutation, Telegram control,
  connector, approval, undo, reasoning agent, or production authority.

## Next Safe Dependency

Implement same-origin dashboard login, callback, logout, and stale-cookie cleanup
around the already purpose-bound API contracts, then add an explicit server-owned
site selection/onboarding transition before any site mutation is enabled.
