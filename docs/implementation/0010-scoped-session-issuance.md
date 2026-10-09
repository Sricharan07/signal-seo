# Slice 0010: Scoped Session Issuance

Status: **INTERNAL SESSION ISSUANCE IMPLEMENTED; CUSTOMER LOGIN IS NOT ENABLED**.

## Outcome

`signal_core.session_issuance` now performs two narrow transitions:

| Transition | Required server-side checks | Result |
| --- | --- | --- |
| Verified OIDC identity to global session | Exact existing issuer/subject user, enabled state, approved ACR, recent `auth_time`, live provider assertion, current external recovery generation | Hash-only pre-tenant identity session |
| Global session to tenant session | Exact global token hash, live parent and recovery generation, enabled user, active requested-tenant membership, active tenant | Hash-only session for one tenant |

Neither transition creates users, memberships, roles, or site grants. Tenant
selection does not authorize a site: the existing `authorize_snapshot` path still
requires the current exact site membership before deriving a `Scope`.

The default policy maps Keycloak ACR `1` to `primary`, and `2` or
`urn:signal:acr:mfa` to `mfa`. Other values fail closed. Global sessions expire
after at most eight hours and no later than twelve hours after authentication.
Tenant sessions expire after at most eight hours and never after their parent.

## Persistence And Privilege

Migration `0004` adds a transaction-local global-session hash function and forces
RLS on `control.identity_sessions`. `signal_identity` can select only the global
row matching that hash, or the parent row attached to the exact tenant-session
hash used by the existing authorizer. Tenant-session reads also require that exact
child hash rather than tenant context alone. The role receives column-level
`INSERT` on only the fields required to issue global and tenant sessions. It has no update,
delete, truncate, schema-create, migration, membership, role, or site-grant write.

Both returned credentials are 256-bit unpadded base64url values hidden from
dataclass representations. Only SHA-256 hashes reach PostgreSQL. Transaction-local
identity, tenant, site, OIDC, and session values are all checked before a pooled
connection can be reused.

## Verification

Run from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

The PostgreSQL 17.11 suite passed all 137 cases under non-owner runtime roles.
Forty-two cases were added for this slice. Reviewed, source-hashed evidence is
[0010-postgresql.json](../evidence/0010-postgresql.json); it records a digest-pinned
database image, completed invocation cleanup, and no production authority.

## Explicit Limits

- This slice added internal methods only. Slices 0018 and 0019 later add
  unconfigured login, callback, and tenant-selection HTTP contracts; slice 0020
  adds current-session inspection and audited logout.
- The API only reports this capability as `internal_only`; customer authentication
  remains `disabled` and there is no browser cookie response.
- PKCE retrieval/deletion from OpenBao is not implemented, so the complete login
  transaction does not yet exist.
- Users and memberships must be provisioned through a future separately authorized
  invitation and administration path.
- Provider session IDs, logout correlation, rate limits, anomaly controls, session
  rotation, and cleanup jobs are not implemented.
- The ACR allowlist is an internal pilot default, not production deployment policy.
- No customer identity, production credential, deployment, or external write was used.

See [ADR-0010](../adr/0010-scoped-hash-only-session-issuance.md) for the security
model, rejected alternatives, and current-state race analysis.
