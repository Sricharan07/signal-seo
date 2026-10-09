# ADR-0019: Route Pre-Tenant Membership Discovery Through A Hash-Scoped Directory

- Status: Accepted
- Date: 2026-09-08
- Owners: Identity, account authority, and API

## Context

The OIDC callback introduced a global identity session but deliberately did not
choose an organization. A user can belong to zero, one, or several tenants, so
the browser needs a way to discover only its current memberships and explicitly
exchange the global session for one tenant session.

Tenant data is protected by forced row-level security. A pre-tenant request has
no trusted tenant ID, and scanning every tenant to discover memberships would be
both an isolation risk and unbounded work. Accepting a browser-supplied tenant ID
without proving membership would turn user input into authority.

## Decision

Add the protected `control.user_membership_routes` index. It contains only the
user, tenant, and membership identifiers needed to route an exact identity to
candidate tenant scopes. It stores no tenant name, role, browser token, or token
hash and has no runtime table grants. A security-definer trigger maintains the
index with each membership insert, update, or delete.

Expose one narrow `control.list_identity_memberships(bytea, text)` function to
`signal_identity`. The caller hashes the opaque identity token and obtains the
current recovery generation from OpenBao before opening a database transaction.
The function validates the live, unrevoked identity session and enabled user,
then visits only that user's route rows. For each candidate tenant it applies
transaction-local RLS and returns only a current active tenant, active membership,
name, and role. It does not grant tenant or site authority.

A valid identity with no memberships returns an internal sentinel row so the
service can distinguish an empty account from an invalid session without granting
table enumeration. The Python boundary validates every returned UUID, role, and
name and removes the sentinel and user identifier from its public projection.

Add `GET /v1/organizations`, `GET /v1/session/csrf`, and
`POST /v1/session/switch-tenant`. The mutation requires the pre-tenant identity
cookie, exact same-origin and Fetch Metadata checks, and an HMAC proof bound to
that cookie. Its JSON body is exact, duplicate-key rejecting, unencoded, and
limited to 1 KiB. Selection first confirms the requested tenant appears in the
identity's current directory and then uses the existing tenant-session issuer,
which independently rechecks the live session, membership, tenant, and recovery
generation before returning a new opaque tenant cookie.

Keep the identity cookie after selection so a user can switch tenants without a
new provider login. Starting or completing a new login clears any older tenant
cookie. A newly issued tenant token receives a separate CSRF proof; the pre-tenant
proof is never reused as tenant authorization.

## Alternatives

- Scan every tenant under a privileged function. Rejected because work and
  exposure would grow with all customers rather than the authenticated user.
- Let the browser submit any tenant ID directly to session issuance. Rejected
  because the request is a selection, not evidence of authority.
- Copy tenant names and roles into the global directory. Rejected because those
  are mutable tenant-owned facts and must be read under current tenant RLS.
- Put selected tenant state inside the identity cookie. Rejected because the
  server-side tenant session already provides hash-only revocable authority.
- Automatically select the only membership. Rejected for now because an explicit
  transition gives every browser flow one auditable and testable boundary.
- Treat zero returned rows as both invalid and empty. Rejected because the API
  must distinguish a valid user with no organizations from an invalid credential.

## Consequences

- Membership discovery is proportional to one user's routed memberships and
  still validates tenant-owned facts through forced RLS.
- The routing index is derived security-sensitive data. Its foreign keys,
  trigger, migration backfill, and no-grant posture must remain tested.
- A configured browser gateway can now establish a tenant session, but the
  repository's default process remains unconfigured and customer authentication
  remains disabled.
- The CSRF read route proves cookie possession for a later same-origin mutation;
  it does not query the database or confer authority. Tenant selection performs
  the authoritative current-state checks.
- A race that removes membership after listing is denied by the second check in
  tenant-session issuance. Invalidated identity authority clears both browser
  cookies and requires a new login.
- This decision does not add current-session inspection or logout; slice 0020
  later adds both internal contracts. Invitation acceptance over HTTP, abuse
  controls, production ingress qualification, and a dashboard remain absent.

## Verification

Twenty-four tenant HTTP cases and four gateway-composition cases cover empty and
multiple memberships, exact cookies, CSRF/origin proofs, strict bounded JSON,
forbidden selections, invalidated identities, malformed gateway output, rotated
tenant proofs, unavailable dependencies, and connection ownership. Sixteen new
PostgreSQL cases cover live-session filtering, current membership state, route
synchronization, least privilege, migration rollback and existing-row backfill,
malformed output, and connection-scope cleanup. The API suite passes 85 cases,
the complete non-database Python suite passes 250, and the real PostgreSQL 17.11
suite passes 233.
