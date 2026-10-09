# ADR-0042: Enumerate Sites Through a Hash-Bound Authority Directory

- Status: Accepted
- Date: 2026-09-09
- Scope: Authenticated tenant site context

## Context

The dashboard needs organization and site names after a tenant session is
verified. Existing forced row-level-security policies deliberately require one
exact `signal.site_id`, so they cannot be reused to enumerate every site a user
may access. Disabling forced RLS, trusting a browser-supplied tenant/site, or
granting the identity process direct access to a cross-site index would weaken the
security boundary established by ADR-0004.

The directory must recheck both session layers, the external recovery generation,
the global user, tenant membership, tenant lifecycle, site membership, permission
shape, and site state at read time. A valid session with no sites must remain
different from an invalid session, and the result must be bounded.

## Decision

Migration `0025` adds `control.user_site_membership_routes`, a credential-free
index containing only tenant, site, site-membership, and user UUIDs plus creation
time. A security-definer trigger mirrors insert, identifier update, and deletion
of `app.site_memberships`. Runtime roles have no table privilege on the index.
Migration backfill temporarily removes `FORCE ROW LEVEL SECURITY` only inside the
atomic migrator transaction and restores it before commit.

`control.list_tenant_sites(bytea,text)` accepts only the exact hash of an opaque
tenant session and the independently read recovery generation. It resolves the
session's tenant and user, rechecks current identity and tenant authority, uses the
private index only to find candidate UUIDs, and then sets exact tenant/site context
before each ordinary forced-RLS lookup. It returns at most 100 current,
non-archived site projections. No active sites returns a typed empty sentinel;
invalid authority returns an indistinguishable invalid-session outcome.

The Python service validates every returned UUID, name, canonical origin, IANA
timezone, currency, state, and ownership state before sorting the projection. The
FastAPI `GET /v1/sites` route accepts only the host-only tenant cookie and exposes
the bounded version-one projection. The Next.js server forwards only that one
validated cookie, validates the response again, and reconciles the returned tenant
ID against its independently loaded current-session projection before rendering.

## Consequences

- Existing site RLS remains forced and unchanged; cross-site listing occurs only
  through one reviewed, hash-bound function.
- Revocation, expiry, disabled users, suspended tenant membership/lifecycle,
  suspended site grants, and archived sites take effect on the next read.
- A valid session can render an honest zero-site state without being treated as
  unauthenticated.
- The private routing table adds write amplification and must remain transactionally
  synchronized by its trigger. Migration and trigger tests cover backfill and delete.
- The 100-site ceiling fails closed rather than silently omitting authority. Future
  multi-site scale requires a separately designed cursor contract.
- This directory grants no site selection, onboarding, command, provider, or
  production-write authority.

## Alternatives Rejected

- **Relax the `app.sites` or `app.site_memberships` RLS policy:** broadens every
  query made by the identity role and turns a dashboard need into ambient access.
- **Accept tenant or site IDs from the browser:** treats requested scope as proven
  scope and creates a confused-deputy boundary.
- **Let the security-definer function bypass row security:** hides authorization
  inside table-owner privilege rather than proving each exact site through current
  RLS context.
- **Use the first site as an implicit durable selection:** silently creates state
  and can route later actions to the wrong site when more than one grant exists.
- **Return unbounded rows or truncate silently:** permits resource exhaustion or
  presents an incomplete authority set as complete.

## Verification

The real PostgreSQL suite covers migration backfill and rollback, active and empty
directories, same-tenant multi-site access, wrong-tenant exclusion, session and
membership reductions, archived/suspended sites, trigger cleanup, invalid stored
origins, no direct route-table access, and exact function privilege. API and
dashboard tests cover exact-cookie forwarding, strict schemas and sizes, invalid
authority, unconfigured dependencies, tenant reconciliation, and truthful UI
states. Desktop and 390-pixel browser checks cover the final responsive surface.
