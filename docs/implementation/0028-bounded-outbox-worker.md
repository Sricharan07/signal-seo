# Slice 0028: Bounded Outbox Worker

Status: **IMPLEMENTED AS A TESTED WORKER CORE; NO TRANSPORT OR DEPLOYED PROCESS**.

## Outcome

Signal now has a bounded, stoppable service loop that composes active-tenant
paging, one fenced claim, external publication, and exact acknowledgement or
rescheduling. Database operations use separate short-lived connection contexts,
so the publisher is never invoked while the claim operation owns a transaction.

| Boundary | Implemented behavior |
| --- | --- |
| Tenant fairness | One deterministic tenant page per cycle with a persistent cursor |
| Claim | One envelope per tenant for safe synchronous lease use |
| Publish success | Exact `None` return, followed by fenced acknowledgement |
| Definite rejection | `PublishNotAccepted`, followed by bounded fenced reschedule |
| Ambiguous outcome | Timeout, unknown, unexpected exception, or bad return leaves lease to expire |
| Isolation | One tenant claim failure does not stop other tenants in the page |
| Observability | Closed structured outcomes contain IDs and fence only, never payload or exception text |
| Lifetime | Bounded active/idle waits and injected cooperative shutdown |

## Service Design

`DeliveryWorkerConfig` validates worker identity, tenant page size, database lease,
retry delay, and poll delays before work starts. The synchronous implementation
accepts only `outbox_batch_size=1`: claiming more and then publishing serially
could let later leases expire before their network call.

`PostgresOutboxStore` adapts the Slice 0026 database operations. Its injected
connection factory is entered and exited independently for tenant scan, claim,
acknowledgement, and reschedule. The transport-facing `EventPublisher` receives
the immutable `OutboxEnvelope` only after claim commit.

`DeliveryCycleReport` exposes counts for active tenants, claims, confirmed
delivery, rescheduling, ambiguous outcomes, stale fences, and storage failures.
`DeliveryObservation` provides the corresponding event stream without including
payload bytes, DSNs, credentials, or exception messages. Observer exceptions are
contained so telemetry cannot interrupt an authorized delivery.

The loop treats every unclassified publisher failure conservatively as ambiguous.
It does not clear or move the lease, allowing the database clock to make the same
event available again. Only a positive non-acceptance can be rescheduled early;
only a positive acceptance followed by a current fence can be marked delivered.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

All 353 non-database Python cases pass, 27 more than Slice 0027; the API subset
remains 161 cases. All 350 real PostgreSQL 17.11 and 11 repository cases pass.
Documentation checks validate 71 Markdown files, Ruff validates and format-checks
82 Python and documentation code files, and the pinned Python environment has no
broken requirements.
Reviewed source-hashed database regression evidence is
[0028-postgresql.json](../evidence/0028-postgresql.json); cleanup completed and
`production_authority` is false.

The evidence file has the same two known generic-key secret-scan false positives
as prior database evidence: source-path keys ending in `pkce_secrets.py` and
`session_tokens.py` whose values are SHA-256 source hashes, not credentials.

## Explicit Limits

- No concrete broker, Temporal client, HTTP publisher, consumer process,
  credential loader, executable entry point, container, or supervisor is wired.
- The worker core is long-running-capable but is not started by this repository or
  deployed anywhere.
- No publisher timeout is imposed by this core; a future transport must provide a
  bounded call and classify its result under this contract.
- Throughput is one envelope per tenant per cycle; concurrent publishing is not
  enabled until lease timing and graceful shutdown are qualified.
- No workflow run, crawl, research, approval, CMS/GitHub operation, undo,
  dashboard, Telegram action, or production authority is added.

See [ADR-0028](../adr/0028-bounded-outbox-worker.md) for the connection, outcome,
retry, observability, and shutdown decisions.
