# Slice 0019: Explicit Tenant Session Selection

Status: **INTERNAL TENANT-SELECTION HTTP CONTRACT IMPLEMENTED; DEFAULT PROCESS AND CUSTOMER ACCESS REMAIN DISABLED**.

## Outcome

A configured FastAPI composition can now list the active organizations attached
to a valid pre-tenant identity session and exchange that session for one
tenant-scoped session. The repository's default process injects neither the
credential-bearing gateway nor browser security and therefore fails closed.

| Route | Behavior |
| --- | --- |
| `GET /v1/organizations` | Return the caller's current active organizations and roles from a valid identity cookie |
| `GET /v1/session/csrf` | Derive a mutation proof bound to the exact pre-tenant identity cookie |
| `POST /v1/session/switch-tenant` | Require same-origin browser proof and a strict tenant UUID, recheck current authority, set the tenant cookie, and return a tenant-bound CSRF proof |

The tenant response also contains the server-derived user ID, role,
authentication level, and expiry. No route accepts a caller-claimed role, user,
site, recovery generation, or session expiry.

## Data And Authority

Migration `0009` adds `control.user_membership_routes`, a minimal global routing
index containing `tenant_id`, `membership_id`, `user_id`, and `created_at`. Its
foreign keys bind every row to the global user and exact tenant membership. A
security-definer trigger synchronizes inserts, identifier changes, and deletes.
The migration transaction backfills existing memberships, restores forced RLS
before commit, and rolls back completely on failure.

The identity runtime role has no table privilege on the directory. It can execute
only the narrow membership-listing function, which validates the SHA-256 identity
session hash and externally read recovery generation before visiting route rows
for that exact user. Each tenant name and role is then read from current tenant
tables under transaction-local forced RLS. Suspended tenants and non-active
memberships are omitted.

The raw identity and tenant tokens exist only in Secure, HttpOnly, host-only,
SameSite=Lax cookies and service memory. PostgreSQL stores only token hashes.
Organization listing does not issue authority. Tenant selection calls the existing
session issuer after discovery, so concurrent revocation, suspension, expiry, or
recovery-generation change fails closed.

## Browser Contract

The switch request requires exactly one identity cookie, one trusted `Origin`, a
same-origin Fetch Metadata value, and one `X-CSRF-Token` derived from that identity
token. The request body must have exactly one `application/json` content type, no
content encoding, no duplicate keys or extra fields, and at most 1,024 bytes.

Success sets `__Host-signal_session` with the tenant token and returns a newly
derived tenant-session CSRF proof. The pre-tenant identity cookie remains available
for an explicit later switch. Invalid identity authority returns one generic 401
and expires both identity and tenant cookies. Unknown or inactive tenant choices
return one generic 403 without disclosing membership details.

`GET /v1/session/csrf` does not perform a database read. It is a same-origin helper
for a browser already holding the HttpOnly identity cookie; the subsequent switch
is the authorization point and revalidates the complete server-side authority.

## Verification

Run from the repository root:

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Twenty-eight new non-database cases bring the API suite to 85 and the complete
non-database Python suite to 250. Sixteen new database cases bring the real
PostgreSQL 17.11 suite to 233. Reviewed source-hashed evidence is
[0019-postgresql.json](../evidence/0019-postgresql.json); it records the pinned
image, complete cleanup, and no production authority.

## Explicit Limits

- The default application has no OIDC, OpenBao, PostgreSQL, or CSRF secret
  configuration and cannot authenticate a customer.
- This slice has no current-session view or logout. Slice 0020 later adds both
  internal HTTP contracts; session cleanup and production key rotation remain absent.
- Invitation acceptance remains internal; there is no authenticated invitation
  POST, delivery channel, throttling, or abuse-control journey.
- Tenant selection is not site authorization. Every future command still needs
  current exact site authorization through the existing server-side boundary.
- There is no dashboard, Telegram adapter, human-attributed command route,
  workflow engine, reasoning agent, customer connector, or production write.
- Upstream callback-query redaction and production identity topology remain
  unqualified.

See [ADR-0019](../adr/0019-hash-scoped-membership-directory.md) for the routing,
session, CSRF, race, and rejected-alternative decisions.
