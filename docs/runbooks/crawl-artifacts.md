# Crawl Artifact And Observation Runbook

This runbook covers Slice 0036's encrypted local artifact and append-only fetch-
observation boundary. It is a qualification, integrity, and reconciliation guide,
not a production crawler startup procedure.

## Local Qualification

Prerequisites:

- Docker Engine is responsive and at least 3 GiB is free;
- Node 22 and the locked Python 3.12 environment are installed;
- the source tree remains unchanged during a provider run; and
- test artifact roots are absolute, canonical, owner-owned, and mode `0700`.

Run from the repository root:

```sh
.venv/bin/pytest -q tests/tooling/test_crawl_artifacts.py
.venv/bin/python scripts/run-database-tests.py
```

The database command starts digest-pinned PostgreSQL 17.11 with tmpfs storage and
one loopback-only ephemeral port, provisions a random-password
`signal_crawl_ingest` test login, migrates twice, runs every control-plane case,
writes sanitized evidence, and removes only invocation-labeled resources.

## Expected Evidence

`.runtime/database-tests/latest.json` must show PostgreSQL `17.11`, 432 passing
cases, exact source hashes, `production_authority` false, and cleanup `completed`.
The reviewed copy is `docs/evidence/0036-postgresql.json`. Never edit evidence to
make hashes match; rerun the lab after the source is stable.

## Storage Invariants

- Supply a canonical absolute root with no symbolic-link component. Keep the root
  and every subdirectory `0700`; objects and lock files must be `0600`.
- Supply exactly 32 bytes of key material from a secret boundary and a stable,
  non-secret key reference. Never put key material in PostgreSQL, filenames, logs,
  exceptions, configuration committed to Git, or operator chat.
- Never alter or replace a `.sig` object. An exact retry must authenticate and match
  it; a conflict is an incident, not permission to overwrite.
- Never treat a parseable or decryptable file as authoritative without its exact
  PostgreSQL record and current `verified` durability state.
- Do not point two independent hosts at ordinary local directories and call that a
  shared artifact service.

## State Interpretation

| State or outcome | Meaning | First safe action |
| --- | --- | --- |
| `verified` | Latest append-only attestation authenticated the exact registered plaintext hash | Permit bounded evidence use through the verified record |
| `missing` | Registered object path was absent | Block use; investigate storage and backup before any restore attempt |
| `corrupt` | Envelope identity or authenticated decryption failed | Block use; preserve bytes and investigate key/configuration and storage integrity |
| `unreadable` | Object or lock could not be safely accessed | Block use; inspect ownership, permissions, path type, and storage health |
| Observation conflict | Fetch attempt already has different immutable evidence | Preserve both object candidates; inspect worker/retry identity; do not overwrite SQL |
| Fetch unavailable | Lease receipt, scope, URL, or worker does not exactly match | Stop; recover the exact receipt rather than broadening privileges |
| Recent orphan | Parseable object has no SQL row but remains inside grace | Leave it untouched so an in-flight or ambiguous commit can converge |
| Old orphan | Parseable object has no exact SQL registration after grace | Delete only through the bounded reconciler while holding its artifact lock |

## Integrity And Restore

Run scheduled checks with a fresh attestation UUID, the exact artifact record, key
reference/material, and database-clock-compatible verification time. Missing,
corrupt, and unreadable checks append evidence and atomically block ordinary reads.

A recovered file remains blocked while the durable state is not `verified`. Restore
it to the exact immutable object key with private ownership/permissions, then run a
`restore_verification` attestation. Only successful authenticated decryption and an
exact plaintext hash may return the projection to `verified`. Do not update
`durability_state` directly or delete failed attestations.

## Orphan Reconciliation

Use `cleanup_orphan_artifacts` only with an idle autocommit ingest-role connection.
The grace period is 300-86,400 seconds and the batch is 1-1,000 files. Start with a
small batch and inspect `scanned`, `registered`, `recent`, `deleted`, and
`unreadable` counts. A nonzero unreadable count requires investigation; the
reconciler deliberately leaves those candidates in place.

Object upload followed by SQL failure is expected to leave an orphan. SQL commit
followed by an unreadable object is not repaired by orphan cleanup; integrity
attestation must block it and a verified restore must recover it.

## Migration And Production Boundary

Migration `0021` is forward-only. A failed transaction remains on revision `0020`;
do not run a destructive downgrade. Production rollout still requires object-store
selection, key provisioning/rotation, backup and restore qualification, capacity
and lock assessment, integrity/orphan schedules, alerts, retention/hold/deletion,
and reconciliation evidence.

Do not wire this module directly to `PinnedHttpFetcher` or a frontier lease; use
the durable page-attempt composition so dispatch is recorded first. Production use
still requires distributed storage/key lifecycle, controlled resolver/egress,
frontier and byte settlement, workflow composition, coverage, monitoring, and
remaining G-03/G-04 gates.
