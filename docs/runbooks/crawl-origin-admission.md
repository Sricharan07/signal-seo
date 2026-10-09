# Crawl Origin Admission Runbook

This runbook covers Slice 0038's global PostgreSQL request gate. It is a local
qualification and incident guide, not a production crawler startup procedure.

## Local Qualification

Run from a stable source tree with Docker Engine responsive, at least 3 GiB free,
and the locked Python 3.12 environment installed:

```sh
.venv/bin/pytest -q tests/tooling/test_crawl_admission.py
.venv/bin/python scripts/run-database-tests.py
```

The database command creates a digest-pinned PostgreSQL 17.11 container with
tmpfs storage, random non-owner role credentials, and one loopback-only ephemeral
port. It migrates twice, executes all control-plane tests, writes a source-hashed
report, and removes only invocation-owned resources.

## Decision Interpretation

| Outcome | Meaning | First safe action |
| --- | --- | --- |
| Active grant | Exact current frontier work owns one short global request slot | Use it at most once for its closed permit kind, then record completion |
| Active duplicate | Lost acknowledgement replayed the same still-current grant | Resume the same operation only; never create a second request |
| Terminal replay | Permit already completed or expired | Do not contact the origin; continue from the durable outcome |
| `in_flight` deferral | Another tenant/run owns the origin slot | Wait until `retry_at`; do not poll aggressively or bypass the bucket |
| `politeness` deferral | Shared minimum spacing has not elapsed | Schedule at `retry_at` or later |
| `backoff` deferral | Origin-wide `429`/`503` degradation is current | Preserve the provider signal and wait until `retry_at` |
| Frontier unavailable | Lease, workflow, tenant, site, or exact origin is no longer current | Stop new I/O and inspect the private durable authority chain |
| Permit conflict | Permit/workload identity or completion differs from its receipt | Treat as a caller/state bug; do not mint a substitute under the same workload |

## Runtime Invariants

- Acquire only with the typed `CrawlFrontierLease` returned by the database.
- Use one stable random permit UUID for acknowledgement recovery. Never reuse it
  for another origin, frontier lease, permit kind, delay, or lifetime.
- Do not treat a frontier lease, robots allow, or URL scope as a network permit.
  All are necessary and none replaces this global gate.
- Commit acquisition before network I/O. Hold no database transaction or row lock
  while resolving, connecting, reading, parsing, or writing an artifact.
- Finish the exact grant once with measured latency and a closed outcome. A `429`
  requires bounded `Retry-After`; a `503` may use its bounded value or the default.
- Completion and expiry receipts remain immutable. Never edit bucket counters or
  lease rows by hand to make work appear ready.
- Authority reduction blocks new grants and active-grant replay. Completion stays
  available so already accepted or ambiguous work can release global capacity.

## Stuck Capacity

An active permit expires no later than 120 seconds and no later than its frontier
lease. Normal acquisition reconciles expired permits for its bucket. The bounded
sweeper is:

```python
reconcile_expired_origin_permits(connection, limit=100)
```

A zero result means no expired active permit was found, not that the crawler is
healthy. Repeated active capacity beyond the recorded expiry indicates database
clock, scheduler, transaction, or schema corruption and requires operator review.
Do not delete leases or reset `in_flight_count` manually.

## Backoff Diagnosis

Inspect through an approved operator path, not a runtime role. Correlate the
bucket origin/profile and last permit completion without joining customer content
into a global operational view. `rate_limited` uses the exact bounded value;
`service_unavailable` defaults to 30 seconds; transport failure uses five seconds;
successful latency at or above two seconds uses that latency up to 60 seconds.

Never shorten a provider delay to recover throughput. The page-attempt coordinator
automatically supplies the applicable robots delay for HTML requests. Any other
request composition must preserve the same invariant.

## Migration And Production Boundary

Migration `0023` is forward-only. A failed transaction remains on `0022`; never
use destructive downgrade. A production rollout needs a reviewed backup, catalog-
lock assessment, application/schema pairing, reconciliation schedule, metrics,
alerts, cardinality/retention policy, load evidence, and restoration exercise.

The page-attempt coordinator now wires the gate to current robots evidence, pinned
HTTP, and artifact persistence on synthetic infrastructure. It is not wired to
robots retrieval, frontier settlement, Temporal, or controlled production egress.
Running these checks contacts no public site and grants no crawl authority.
