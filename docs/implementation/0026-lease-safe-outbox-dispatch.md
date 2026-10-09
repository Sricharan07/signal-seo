# Slice 0026: Lease-Safe Outbox Dispatch

Status: **IMPLEMENTED AS AN INTERNAL DELIVERY CONTRACT; NO PUBLISHER OR WORKFLOW**.

## Outcome

Signal can now claim accepted-command outbox events in short, bounded transactions
and acknowledge or reschedule the exact delivery attempt later. The envelope is
returned only after the claim transaction commits, so a future network publisher
does not hold database locks while contacting Temporal or another transport.

| Boundary | Implemented behavior |
| --- | --- |
| Tenant scan | Scheduler reads only the minimal active tenant directory in deterministic bounded pages |
| Claim | One active tenant, at most 100 rows, ordered availability, `FOR UPDATE SKIP LOCKED` |
| Lease | Lowercase worker key, 1-300 seconds, database time, incrementing attempt fence |
| Failure | Exact live fence can clear the lease and defer retry by 1-3600 seconds |
| Success | Exact live fence records database delivery time and clears the lease |
| Isolation | Forced RLS applies inside narrow security-definer functions; scope is transaction-local |
| Immutability | Event identity and payload never change; delivered rows remain retained |
| Privilege | Scheduler has function execution but no direct outbox table access |

## Database Changes

Migration `0015` replaces the blanket outbox mutation trigger with a strict
delivery-state guard. The only permitted changes are claim/reclaim, live-lease
delivery acknowledgement, and live-lease retry scheduling. Tenant, site, outbox,
event, command, aggregate, schema, and payload fields remain immutable; deletion
is always rejected.

A tenant-wide pending index supports the dispatcher access pattern. A dedicated
policy applies only to the migration-owned security-definer functions while they
set a tenant and no site. Claiming locks active `control.tenant_directory` and
`app.tenants` rows, so lifecycle reduction conflicts with acceptance of new work.
Acknowledgement and rescheduling remain possible after suspension to record the
outcome of a publish already in flight.

`signal_scheduler` keeps direct `SELECT` only on `control.tenant_directory` and
can execute exactly the claim, acknowledge, and reschedule functions. It cannot
select, insert, update, or delete `app.outbox`. `signal_api` keeps outbox reads and
can insert only the nine immutable envelope columns; database defaults own initial
availability, lease, delivery, and attempt state.

## Service Contract

`list_active_dispatch_tenants` returns a deterministic UUID page from the minimal
directory. `claim_outbox_batch` validates all bounds before opening a clean
transaction and returns immutable `OutboxEnvelope` values with canonical JSON
bytes. `mark_outbox_delivered` and `reschedule_outbox` require the exact
`attempt_count` returned by the claim and raise one detail-free `OutboxLeaseLost`
outcome for missing, expired, delivered, wrong-worker, or stale-fence rows.

The service does not invoke a transport. A future publisher must treat a return
from claim as temporary ownership, send outside the transaction, and acknowledge
only a positively accepted transport call. Timeouts and lost acknowledgements are
ambiguous and therefore redeliver under the same event identity.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

All 332 real PostgreSQL 17.11 cases pass, 18 more than Slice 0025. Coverage includes
concurrency, crash-style lease expiry, stale workers, lifecycle changes, retry
timing, transition integrity, least privilege, scope cleanup, migration rollback,
and a production-shaped upgrade with an existing pending event. Reviewed
source-hashed evidence is [0026-postgresql.json](../evidence/0026-postgresql.json);
cleanup completed and `production_authority` is false.

All 323 non-database Python and 11 repository cases pass, documentation checks
validate 66 Markdown files, Ruff validates and format-checks 76 Python files, and
the pinned Python environment has no broken requirements. The evidence file has
the same two known generic-key secret-scan false positives as prior database
evidence: source-path keys ending in `pkce_secrets.py` and `session_tokens.py`
whose values are SHA-256 source hashes, not credentials.

## Explicit Limits

- No continuously running dispatcher process, broker, Temporal client, or
  workflow run exists. Slice 0027 adds the inbox, workflow identity, and admission
  progress that were absent here.
- `delivered_at` means transport acknowledgement only. It does not prove consumer
  processing, workflow start, crawl completion, or provider change.
- At-least-once publication requires downstream deduplication; exactly-once
  external delivery is not promised.
- Dead-letter policy, attempt alerts, adaptive backoff, metrics, traces, and replay
  administration remain absent.
- No production credential, customer enablement, external effect, or release
  authority is added.

See [ADR-0026](../adr/0026-lease-safe-outbox-dispatch.md) for lease, fencing,
isolation, acknowledgement, and redelivery decisions.
