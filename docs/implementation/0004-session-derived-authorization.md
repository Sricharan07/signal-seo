# Slice 0004: Session-Derived Site Authorization

Status: **IMPLEMENTED AND VERIFIED AS AN INTERNAL AUTHORIZATION FOUNDATION**.

## Outcome

This slice adds the server-side data and repository contract needed to turn one
opaque tenant-session token into a current, site-specific authorization result. It
derives tenant and user identity from database state, checks an externally anchored
recovery generation, and rechecks current membership and the exact requested site
grant before returning a `Scope`.

It does not authenticate with Keycloak, issue a browser cookie, expose an HTTP
endpoint, accept Telegram messages, or create a human-attributed command. Those
boundaries remain disabled rather than represented by development shortcuts.

## Implemented Data

Migration `0002` adds five tables:

| Schema | Table | Purpose |
| --- | --- | --- |
| `control` | `users` | Global `(oidc_issuer, oidc_subject)` identity; email is optional profile data |
| `control` | `identity_sessions` | Pre-tenant verified session, auth level, expiry, revocation, and recovery generation |
| `app` | `memberships` | Current tenant role, state, and authorization epoch |
| `app` | `sessions` | One tenant-selected opaque session linked to the same global user |
| `app` | `site_memberships` | Current exact site permission and authorization epoch |

Raw session tokens are never stored. Both session layers retain 32-byte hashes.
Tenant and site authority remains relational rather than embedded in a bearer
token. Membership, tenant-session, and site-grant tables force RLS and use scoped
foreign keys. The current permission JSON is deliberately constrained to one
versioned `site.snapshot.request` value rather than accepting arbitrary claims.

The bootstrap adds a non-superuser, non-owner, non-BYPASSRLS `signal_identity`
role. Its table grants are read-only. Before tenant scope is known, the session RLS
policy exposes only the row matching the transaction-local hash and the exact
identity role. After lookup, transaction-local tenant/site context controls every
authority join. Public function execution is denied and reviewed grants are tested.

## Authorization Flow

`authorize_snapshot` performs these checks inside one short transaction:

1. Validate the high-entropy token shape, site UUID, and external recovery-generation shape.
2. Hash the token and expose only the matching tenant-session row through RLS.
3. Require both session layers to be live, unrevoked, unexpired, and linked to the same user.
4. Require the global identity to be enabled and the auth time/level to agree across layers.
5. Match the session generation to the independently supplied current generation.
6. Set the derived tenant plus requested site as transaction-local context.
7. Recheck active tenant membership, exact active site grant, tenant lifecycle, and site state.
8. Return tenant, site, user, role, auth level, and both authority epochs.

Invalid session states are intentionally indistinguishable. Authorization denials
do not reveal whether a different tenant or site exists. Scope and session-hash
settings clear after commit or rollback; contaminated connections are rejected.

## Verification

The completed suite ran on 2026-09-07 with Python 3.12.14 and the digest-pinned
PostgreSQL 17.11 profile:

- 63 PostgreSQL integration cases passed: the previous 38 command/isolation cases
  plus 25 identity, grant, function-privilege, and migration cases.
- Identity cases cover valid derivation, both expiry layers, both revocation
  layers, disabled users, stale recovery generations, auth-level mismatch,
  malformed tokens, current membership and site changes, wrong-site denial,
  issuer separation, hash-only token storage, and read-only role restrictions.
- Revision `0002` upgrades after `0001`, repeated `upgrade head` is stable, and an
  injected collision proves the new revision rolls back without changing `0001`.
- 8 database-lab safety tests, 8 repository/documentation tests, Ruff checks, and
  `pip check` passed.

The generated, source-hashed report is
[`docs/evidence/0004-postgresql.json`](../evidence/0004-postgresql.json). It records
confirmed Docker cleanup and `production_authority: false`.

The first two attempts stopped on the runner's 60-second subprocess timeout while
macOS rehydrated Alembic dependencies. Both invocation-owned environments were
confirmed cleaned and no result was claimed. A standalone import completed the
rehydration; the unchanged timeout then completed normally. The first executing
test run exposed a missing identity-role function grant. That run failed, the
grant was added only in revision `0002`, and the final full run passed.

## Explicit Limitations

There is no production identity provider, OIDC token verification, account flow,
session issuance/revocation service, external recovery anchor, authorization API,
CSRF control, human command attribution, audit event, or user interface. The
identity role is a trusted backend boundary and its ability to set context is not
contained by RLS. This slice advances Milestone 1 but does not complete identity,
authorization, or any customer-facing flow.

See [ADR-0004](../adr/0004-session-derived-site-authorization.md) for the decision
and [the database guide](../../database/README.md) for operations and ownership.
