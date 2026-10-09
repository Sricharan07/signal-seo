# Slice 0003: Durable Command Foundations

Status: **IMPLEMENTED AND VERIFIED AS A PROVIDER-INDEPENDENT FOUNDATION**.

## Outcome

This slice implements the first narrow write path in the Signal control plane. A
trusted internal service can accept one harmless `site.snapshot` intent and commit
its command, acceptance event, and outbox record atomically. A retry with the same
identity and request returns the original command. Conflicting reuse fails without
revealing another site's data.

The implementation covers a deliberately small subset of specification sections 7
and A.5. Recording an accepted intent is not execution: there is no consumer,
public API, login, Temporal workflow, model call, production connector, or CMS
write in this slice.

## Data Contract

Revision `0001` creates six domain tables:

| Schema | Table | Ownership and purpose |
| --- | --- | --- |
| `app` | `tenants` | Root customer record and lifecycle state |
| `app` | `sites` | Tenant/site identity and verified-scope precursor |
| `app` | `commands` | Immutable accepted intent and idempotency identity |
| `app` | `command_events` | Immutable facts about command acceptance |
| `app` | `outbox` | Atomic handoff record for a future dispatcher |
| `control` | `tenant_directory` | Minimal platform routing state, separate from customer rows |

All customer tables use forced row-level security. Site-owned relationships carry
both `tenant_id` and `site_id`, and composite foreign keys prevent cross-scope
references even when RLS is bypassed for integrity testing. Runtime roles do not
own schemas or tables and cannot bypass RLS, run migrations, disable policies,
truncate tables, or switch into privileged roles.

The Python transaction helper accepts UUID scope only, requires an idle autocommit
connection, rejects residual session scope, and applies tenant/site context with
transaction-local settings. This is a database isolation boundary, not an
authentication system. A future authenticated service must derive the scope from
the current session and membership before calling it. Models, chat inputs, and
untrusted HTTP callers must never choose this trusted scope directly.

## Acceptance Flow

`accept_snapshot` performs the following work in one database transaction:

1. Validate the internal service identity and idempotency key before database use.
2. Confirm the scoped tenant is active and the site is available.
3. Canonicalize and hash the typed `site.snapshot` request.
4. Insert the immutable command, or resolve an existing idempotent request.
5. Insert the acceptance event and matching outbox record.
6. Commit all three records together, or roll back all three on any failure.

Command state is intentionally limited to `accepted`. Outbox delivery bookkeeping
is reserved but immutable in this revision because no dispatcher contract exists
yet. Later work must add mutable progress separately instead of weakening the
accepted-intent history.

## Migration and Roles

`database/bootstrap.sql` creates passwordless `NOLOGIN` roles. Only the disposable
test runner grants temporary login credentials. Alembic reads its connection from
`SIGNAL_MIGRATION_DSN`, verifies it is running as the non-superuser
`signal_migrator` on a writable primary, uses bounded lock and statement timeouts,
and obtains a transaction-scoped advisory lock.

Revision `0001` is creation-only and has no live-data backfill. Its whole migration
is transactional, including the version table. Automatic destructive downgrade is
refused. Future production revisions require an explicit compatibility, backup,
restore, and deployment plan rather than treating downgrade as per-command Undo.

Reviewed runtime privileges are declared in `database/privileges.json` and tested
against the effective PostgreSQL grants. See [ADR-0003](../adr/0003-durable-command-foundations.md)
and the [database guide](../../database/README.md) for the decision and operations.

## Verification

Verification ran locally on 2026-09-07 with Python 3.12.14 and a digest-pinned
PostgreSQL 17.11 container:

- 38 PostgreSQL integration cases passed, including concurrency, lost-response
  retry, RLS denial, composite integrity, role restrictions, migration replay,
  migration rollback, immutable records, and atomic outbox failure.
- 8 database-lab safety tests passed, including low-disk refusal, sanitized
  failures, uncertain-create cleanup, exact-label cleanup, and JUnit parsing.
- 8 repository and documentation tests passed.
- Ruff lint and format checks and `pip check` passed.

The generated PostgreSQL report records the exact image digest, source hashes,
case names, and confirmed cleanup in
[`docs/evidence/0003-postgresql.json`](../evidence/0003-postgresql.json). It has
`production_authority: false` and must not be presented as a release certificate.

The disposable runner requires at least 3 GiB free before creating Docker
resources. It uses a uniquely named bridge network, publishes PostgreSQL only on
an inspected loopback address, mounts no host source, and removes only resources
matching both its invocation label and exact name. This lab does not claim to deny
container egress.

## Maintenance History

The first attempt stopped when the host had approximately 200 MiB free and Docker
became unresponsive. No passing PostgreSQL result was claimed. After the user freed
space, Docker Desktop was restarted with explicit approval. The uncertain old lab
network was confirmed absent, the runner was hardened for disk preflight and lost
creation responses, and the complete suite passed with cleanup confirmed. No
unrelated Docker resources were removed.

## Explicit Limitations

This slice does not implement customer identity, memberships, authorization,
tenant-wide commands, execution progress, outbox delivery, Temporal, approvals,
artifacts, independent journals, memory retrieval, recovery authority, or any
production connector. It does not make Milestone 1 complete. The pinned Python
dependency graph is not wheel-hash locked and this slice does not include a
dependency vulnerability attestation or Linux CI result.
