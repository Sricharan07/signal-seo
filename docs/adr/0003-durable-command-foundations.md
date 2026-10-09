# ADR-0003: Narrow Durable Commands Before Public APIs

Status: Accepted. Date: 2026-09-07.

## Context

The WordPress experiment permits provider-independent foundation work under
ADR-0002, not production publishing. The next useful contract is durable command
acceptance with real tenant/site isolation. Identity and execution authority are
not yet available; exposing an unauthenticated API would bypass the design.

## Decision

Use Python 3.12 and psycopg for parameterized persistence, Alembic for explicit
migration jobs, and PostgreSQL 17.11 for the disposable integration profile. Keep
SQL migrations readable rather than duplicating these initial tables in an ORM.

Implement only an internal service-actor `site.snapshot` intent. Accepting it
atomically records its command, acceptance event, and outbox entry. Duplicate
principal/route/idempotency keys return the original result or a generic intent
conflict. Nothing executes the command yet; status remains `accepted`.

Force tenant/site RLS, use non-owner runtime roles and composite foreign keys,
and test effective privileges against a checked-in manifest. Require clean, idle
connections and transaction-local scope. Reject contaminated pooled connections
rather than restoring a previous session-level tenant setting after commit.
These are defense-in-depth controls, not session authentication or a defense
against a compromised trusted backend. PostgreSQL documents its privileged-role
and referential-integrity boundaries in its [RLS documentation](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).

## Narrowed Contracts

- No human actors until membership, session, step-up, and recovery-generation
  contracts are implemented. Internal actor names are not user authentication.
- Commands are site-scoped only. Tenant-wide commands need an explicit migration.
- Command/event/outbox records are immutable in this slice. Dispatch bookkeeping
  and progress require later reviewed procedures and column grants.
- Site origins remain unverified labels; recording an origin grants no crawl or
  publish authority. Profile revisions and site-control authority are not added yet.
- Only six domain tables are created. Temporal, Keycloak, secrets, artifacts,
  release families, approvals, execution leases, and journals remain separate work.

## Alternatives and Consequences

A SQLite or mocked database would miss PostgreSQL RLS, real privileges, locking,
and constraint behavior. A public mock API would misrepresent identity readiness.
A custom workflow queue would compete with the selected Temporal architecture.
None of those shortcuts is used.

The test database has a run-private bridge network, random credentials, no host
mounts, tmpfs data, and a validated loopback-only ephemeral port. On Docker 28.5.1,
an `--internal` network produced no published port, preventing the host test client
from connecting. This development profile therefore does not claim denied
outbound networking. It runs trusted synthetic tests, not hostile crawler/build
code. The WordPress lab keeps its fully internal network unchanged.

## Verification

See [slice 0003](../implementation/0003-durable-commands.md) and the
[database guide](../../database/README.md). The completed PostgreSQL suite covers
duplicate races, partial-transaction failures, scope leakage, wrong-site
references, effective privileges, migration rollback, and runtime migration
denial. Pilot and GA1 release authority remain disabled.
