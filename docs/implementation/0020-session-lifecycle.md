# Slice 0020: Current Session And Audited Logout

Status: **INTERNAL SESSION LIFECYCLE HTTP CONTRACT IMPLEMENTED; DEFAULT PROCESS AND CUSTOMER ACCESS REMAIN DISABLED**.

## Outcome

A configured API can now inspect the selected tenant session and perform an
explicit server-side logout from either the selected or pre-tenant browser state.
The default process still injects no database gateway, OpenBao reader, or CSRF
key and fails closed.

| Route | Behavior |
| --- | --- |
| `GET /v1/session` | Return the exact tenant session's current server-verified tenant, user, role, authentication level, and expiry |
| `POST /v1/session/logout` | Require same-origin session-bound proof, revoke the parent identity session, append one event, and clear all Signal auth cookies |

The session view rejects revoked or expired child and parent sessions, a stale
recovery generation, disabled users, inactive tenants, and inactive memberships
with one generic 401. A missing or invalid tenant cookie is expired in the
response. Site authority is deliberately absent and still requires an exact
site-level authorization check.

## Revocation And Audit

Migration `0010` adds no session table and grants no direct update privilege.
Instead, the identity runtime role can execute one security-definer function with
a SHA-256 token hash, a closed `identity` or `tenant` kind, and a UUIDv4 event ID.
The function locks and revokes only the matching parent identity session. For a
tenant proof it also marks that exact child row revoked. The live-parent checks in
every existing authorization path make all sibling tenant sessions unusable even
though their retained rows are not rewritten.

The same transaction appends `identity.session.revoked` to the forced-RLS,
immutable `control.platform_events` table. Its contract permits no arbitrary
facts or reason text. An event collision rolls back revocation and is retried at
most three times. Concurrent requests serialize on the parent and emit one event;
later retries return an indistinguishable idempotent result.

Logout does not call the recovery authority, so an OpenBao outage cannot strand
the user's server-side session. It returns `204` only after the database boundary
returns. The API clears `__Host-signal_identity`, `__Host-signal_session`, and
`__Host-signal_oidc_binding` together. A database failure returns a generic 500
without claiming logout or deleting the browser's only remaining proof.

## Browser Proof

The logout dependency accepts a tenant-bound proof whenever a tenant cookie is
present. It falls back to the identity-bound proof only for a pre-tenant browser.
Both modes require one exact trusted Origin, allowed Fetch Metadata, one selected
cookie, and one matching `X-CSRF-Token`. Duplicate session-level cookies fail
closed. Holding both cookies does not permit fallback to an identity CSRF token.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Twenty-one new non-database cases bring the API suite to 106 and the complete
non-database Python suite to 271. Seventeen new database cases bring the real
PostgreSQL 17.11 suite to 250. Reviewed source-hashed evidence is
[0020-postgresql.json](../evidence/0020-postgresql.json); it records the pinned
image, completed cleanup, and no production authority. The unchanged disposable
OpenBao and Keycloak boundaries also pass seven and five real-service scenarios.

## Explicit Limits

- The default application cannot authenticate or revoke a customer session.
- Logout does not terminate the upstream Keycloak session. A later provider
  logout design must bind and qualify exact provider-session behavior.
- There is no list of devices/sessions, selective remote revocation,
  administrator revocation, cleanup scheduler, or production retention policy.
- The session view contains no effective site capabilities or reauthentication
  recommendation because those require a requested operation and exact site.
- Invitation acceptance remains internal. Slice 0021 later adds purpose-bound
  proof issuance, and slice 0022 adds atomic proof consumption plus a cleanup
  primitive; browser cookie composition, authenticated POST body, deployed
  cleanup, delivery, and abuse controls remain absent.
- There is still no dashboard, Telegram channel, workflow engine, reasoning agent,
  customer connector, approval flow, undo operation, or production write.

See [ADR-0020](../adr/0020-audited-browser-session-revocation.md) for the parent
revocation, proof precedence, audit, outage, and idempotency decisions.
