# Slice 0024: Human Command Authority

Status: **IMPLEMENTED AS AN INTERNAL DATABASE CONTRACT; NO HTTP OR EXECUTION**.

## Outcome

Signal can now accept the harmless `site.snapshot` command from an authenticated
human without splitting authorization and persistence across transactions. The
opaque tenant session, current independent recovery generation, requested site,
actor derivation, command insert, acceptance event, and outbox insert are resolved
under one clean PostgreSQL transaction.

| Boundary | Implemented behavior |
| --- | --- |
| Session | Hashes a validated 256-bit opaque token; raw credentials never reach storage |
| Authority | Rechecks both session layers, recovery generation, enabled user, membership, exact site grant, tenant, and site |
| Attribution | Derives `actor_user_id` and `user:<uuid>` principal only from server-side records |
| Idempotency | Computes a SHA-256 fingerprint over tenant, actor, stable route, site target, and canonical body |
| Durability | Commits immutable command, sequence-one event, and outbox record together |
| Retry | Returns the original ID and acceptance time for an exact retry; conflicting reuse is generic |
| Status | Rechecks live authority and returns only the current actor's human snapshot command |

This slice deliberately adds no provider call, workflow dispatch, external side
effect, approval, or production authority.

## Database Changes

Migration `0014` expands `app.commands` without rewriting existing internal
service intent. `actor_service` becomes nullable, `actor_user_id` is added, and
constraints require exactly one actor type. Human actors have a same-tenant
membership foreign key and a fixed `api.site.snapshot` route; service actors keep
the existing `internal.site.snapshot` route.

`control.resolve_snapshot_authority` is the single current-authority rule used by
both `authorize_snapshot` and the new command operations. It is hash-scoped before
tenant discovery, sets transaction-local tenant/site context, and locks the exact
session and authority rows with key-share locks.

`control.accept_authenticated_snapshot` computes the fingerprint inside the
trusted database boundary after deriving the actor. It returns closed outcomes for
invalid session, denied authority, idempotency conflict, or acceptance. The
identity runtime role can execute the function but cannot insert directly into
`app.commands`, `app.command_events`, or `app.outbox`.

`control.read_authenticated_snapshot_command` repeats the same live authority
checks and returns a bounded projection only when site, command, route, and actor
all match. Missing and inaccessible command IDs are not enumerated through table
access.

## Service Contract

`accept_authenticated_snapshot` validates the opaque session, site UUID,
recovery-generation shape, and one 1-128 character ASCII idempotency token before
opening a transaction. It maps only the database's closed outcomes to
`InvalidSession`, `AuthorizationDenied`, or `IdempotencyConflict` and validates
the returned projection before exposing it to callers.

`read_authenticated_snapshot_command` returns command ID, actor user ID, kind,
status, and timezone-aware acceptance time. Current schema permits only the
`accepted` state; progress is intentionally deferred rather than fabricated.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

The real PostgreSQL 17.11 suite passes 314 cases, 28 more than Slice 0023. It
includes a production-shaped `0013 -> 0014` upgrade with an existing service
command, atomic fault injection, concurrent retries, authority reductions,
cross-site conflicts, least-privilege checks, and the complete prior regression
suite. Reviewed source-hashed evidence is
[0024-postgresql.json](../evidence/0024-postgresql.json); cleanup completed and
`production_authority` is false.

Gitleaks reports no finding in the staged source/documentation diff when generated
evidence is excluded. Scanning the evidence file itself reports two generic-key
heuristic matches: the keys are source paths ending in `pkce_secrets.py` and
`session_tokens.py`, and both values are SHA-256 source hashes rather than secrets.

The local lab's Alembic and pytest subprocess budgets are now 180 and 300 seconds
respectively. This does not weaken a safety check; it prevents false failures as
the migration and integration suites grow, while every command remains bounded.

## Explicit Limits

- There is no HTTP route for this command yet.
- There is no dispatcher, consumer inbox, Temporal workflow, progress transition,
  result artifact, cancellation, SSE stream, or dashboard.
- A snapshot command is harmless durable intent only; it does not crawl a site or
  contact a provider.
- Human command acceptance is not an approval mechanism and grants no external
  write authority.
- Recovery rotation, restriction replay, restore reconciliation, customer
  enablement, and production operations remain absent.

See [ADR-0024](../adr/0024-atomic-human-command-authority.md) for the transaction,
attribution, fingerprint, and privilege decisions.
