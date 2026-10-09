# Slice 0035: Durable Crawl Run And Frontier Admission

- Status: Implemented and real-PostgreSQL-qualified; production crawl disabled
- Date: 2026-09-09
- Milestone: M1 partial
- Specification: Revision 3.2 sections 2, 3, 6, 7.2-7.7, 11.1-11.5,
  27.2-27.4, 30, 32 G-01/G-03/G-04, and 33 Milestones 1-2
- Decision: [ADR-0035](../adr/0035-bind-crawl-leases-to-immutable-run-snapshots.md)

## Scope

This slice gives the future crawl executor a durable admission boundary. It binds
one exact running `CrawlSite` workflow to an immutable scope and limits snapshot,
creates its stable root URL and frontier item, records in-scope discoveries with
provenance, and leases ready URLs under database-enforced concurrency and budget
rules.

It does not connect the qualified HTTP fetcher to Temporal or enable a customer
crawl. Fetch observations, body artifacts, robots decisions, cross-run and
cross-tenant origin politeness, parser facts, byte settlement, run finalization,
and coverage manifests remain separate dependencies.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0020` | Adds forced-RLS crawl run, URL inventory, frontier, and append-only lease-receipt tables with scoped foreign keys, immutable identities, guarded transitions, query indexes, and transactional rollback |
| Run admission | Requires one exact running command/workflow/first-run tuple and stores closed schema-versioned scope and limit JSON plus a 32-byte fetch-profile hash |
| URL admission | Uses normalization version 1, exact allowed origins, stable URL identity, source-in-the-same-run provenance, depth rules, priority/not-before values, and a serialized URL-count ceiling |
| Lease admission | Uses database time, exact live retry, unique append-only receipts, 1-300 second requests capped by the run deadline, one live lease per origin within a run, attempt exhaustion, and a bounded exhaustion sweep per claim |
| Authority | Exact accepted retries converge; current workflow, tenant, and site reductions stop new discovery and leases |
| Service boundary | Adds typed opaque Python records, deterministic UUIDv5 identities, strict result reconstruction, clean short transactions, and closed unavailable/conflict/rejected outcomes |
| Runtime role | Adds function-only `signal_crawl_admission`; it owns no table, has no direct table grant, cannot migrate, and does not bypass RLS |

The database is authoritative for contested admission. Opening locks the exact
workflow and command before checking or creating a run. Enqueue locks the run
before deduplication and budget checks. Claim locks the run and selected frontier
row, expires exhausted leases, and will not issue a second live lease for the same
origin in that run.

The service reconstructs the returned URL, scope policy, limits, and profile hash
instead of trusting arbitrary database output. Returned dataclasses have opaque
representations so routine diagnostics do not print crawled URLs or query values.

## Data Contract

`app.crawl_runs` is currently an immutable admission record rather than a claimed
complete crawl. It stores the exact command/workflow binding, scope/policy
versions, allowed origins, seed, authorized public-HTML purpose, user agent,
URL/depth/attempt/redirect/body/total-byte/duration/timeout limits, and fetch
profile hash. Completion columns must remain null in this slice.

`app.urls` keeps one normalized fetch identity per site and normalization cohort.
`app.crawl_frontier` references that stable URL and one same-run source URL, then
holds queue state and the current lease. `app.crawl_frontier_leases` retains each
issued lease's UUID, worker, attempt, issue time, and expiry so stale identity reuse
cannot acquire different work. None of these tables stores response bodies,
credentials, cookies, request authorization, DNS answers, or provider exception
text.

## Verification

Run:

```sh
.venv/bin/python scripts/run-database-tests.py
```

The slice adds 20 positive, negative, failure, concurrency, and role-isolation
cases. The complete disposable PostgreSQL 17.11 suite passes 411 cases, including
the transactional migration-failure check. The suite provisions random temporary
role credentials, publishes PostgreSQL only on an ephemeral loopback port, uses
tmpfs, records source hashes, and confirms invocation-owned cleanup.

The unchanged surrounding boundaries are requalified separately before commit:
553 non-database Python cases, 171 API cases, three real Temporal cases, one joint
PostgreSQL/Temporal consumer case, six workflow-consumer image cases, five crawler
network cases, five Keycloak cases, seven OpenBao cases, and repository/document
checks (16 repository cases and 95 Markdown files). Exact checked-in evidence is
listed under `docs/evidence/0035-*`.

- [0035 PostgreSQL](../evidence/0035-postgresql.json)
- [0035 Temporal](../evidence/0035-temporal.json)
- [0035 joint consumer](../evidence/0035-consumer.json)
- [0035 workflow-consumer image](../evidence/0035-workflow-consumer-image.json)
- [0035 crawler network](../evidence/0035-crawler-network.json)
- [0035 Keycloak](../evidence/0035-keycloak.json)
- [0035 OpenBao](../evidence/0035-openbao.json)

Every provider record reports completed cleanup and production authority false.

## Explicit Limits

- The frontier is not registered as the Temporal crawl executor and grants no
  public-network or customer authority.
- The current one-live-lease rule is per run, not origin-wide across Signal. The
  `control.origin_buckets` and `control.admission_leases` contract is still needed.
- Robots retrieval/parsing/cache, retry-after, adaptive rate, trap classification,
  fairness, and sitemap limits are not implemented.
- The total-byte ceiling is persisted but cannot be settled until durable fetch
  observations exist. The qualified HTTP boundary separately enforces one-body
  and timeout limits but is not wired here.
- `permanently_failed` currently means lease-attempt exhaustion only. No other
  fetch/robots/parser terminal category or final coverage summary is fabricated.
- URL values are business records under the database's protected site scope, not
  log fields. Artifact encryption, retention, export, and deletion are still due.
- Lease receipts are immutable and currently retained with the run. A separately
  reviewed finalized-run retention cleanup does not exist yet.
- G-03 and G-04 remain incomplete; passing this slice does not release crawling or
  establish a complete restore/restriction-replay story.

## Next Safe Dependency

Add private immutable artifact persistence and append-only fetch observations with
cross-store commit/reconciliation rules. Then implement robots and global origin
admission before composing frontier claim, pinned HTTP fetch, durable observation,
and terminal coverage in the workflow executor.
