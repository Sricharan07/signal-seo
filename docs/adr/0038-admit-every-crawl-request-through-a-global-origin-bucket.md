# ADR-0038: Admit Every Crawl Request Through A Global Origin Bucket

- Status: Accepted
- Date: 2026-09-09
- Milestone: M1 partial
- Supersedes: none
- Related: ADR-0034, ADR-0035, ADR-0037

## Context

The durable frontier prevents more than one live URL lease for an origin inside
one crawl run. That is not an origin-wide safety boundary: two tenants or two runs
could still contact the same public server concurrently, ignore one another's
delay, or bypass a backoff observed elsewhere. A frontier lease also lasts longer
than one network request and cannot itself be treated as request authority.

The active specification requires global origin concurrency, shared politeness,
provider backoff, crash recovery, current scope checks, and a dedicated
`signal_crawl_admission` owner. The gate must not put tenant business data into a
global bucket or hold a database transaction across network I/O.

## Decision

Every future robots or HTML navigation request must first acquire one short
`control.admission_leases` receipt under the canonical origin's
`control.origin_buckets` row.

Profile version 1 has one token, one in-flight request, a minimum 1,000 ms delay,
and a maximum 120-second permit. A request can impose a stricter delay up to 60
seconds. The bucket row is locked while expired permits are reconciled, readiness
is evaluated, and a new permit is inserted, so all tenants share one serialized
decision for the same origin.

A new permit is issued only for the exact active frontier lease, worker, running
workflow, admitted URL origin, active tenant directory and tenant, and non-archived
site. The global lease stores an opaque SHA-256 authority fingerprint and a
workload identity containing only run and frontier-lease UUIDs; the global tables
contain no tenant/site columns or customer content. Exact active retries recheck
current authority before returning the existing grant. Terminal retries return a
non-authorizing replay receipt. Rebinding a permit or workload identity conflicts.

Network I/O occurs after the acquisition transaction commits. Completion uses a
separate exact operation that releases capacity and applies shared policy:

- `429` uses a required bounded `Retry-After`;
- `503` uses a bounded `Retry-After` or a 30-second default;
- transport failure adds a five-second cooldown;
- successful latency of at least two seconds adds a delay equal to that latency,
  capped at 60 seconds; and
- cancellation releases capacity without inventing provider backoff.

Permits abandoned by failed workers expire. Acquisition reconciles the affected
bucket, and a separate bounded function reconciles idle buckets. Every mutating
path locks the origin bucket before its permit rows to avoid opposing lock order.
Only `signal_crawl_admission` may execute the three security-definer functions;
it receives no direct table access.

## Alternatives

- **Keep admission per crawl run.** Rejected because multiple tenants could
  collectively overload one origin and provider backoff would not be shared.
- **Hold a row lock during HTTP I/O.** Rejected because slow or failed networks
  would consume database connections and locks, making recovery worse.
- **Use process memory or Redis first.** Rejected because the current system of
  record is PostgreSQL and this safety decision needs exact transactional receipts
  before another operational dependency is introduced.
- **Treat a frontier lease as a request permit.** Rejected because one frontier
  attempt may need robots and HTML requests and its lease is not globally scoped.
- **Permit several concurrent requests in profile version 1.** Rejected until
  load, fairness, and owner-controlled increase policy are separately qualified.

## Consequences

- Same-origin requests across tenants now share concurrency, delay, slow-response
  adaptation, and provider backoff in the authoritative database.
- A lost acquisition or completion response converges on the original receipt
  instead of creating another request permit.
- Authority reduction blocks new permits and active-receipt replay, while permit
  completion remains available so already accepted work can release capacity.
- Global rows reveal origin-level operational state to the privileged migrator,
  but not tenant ownership or customer content. Runtime callers cannot query them.
- First-come callers can still starve one another. Fair queue selection, origin
  cardinality/retention policy, and aggregate telemetry remain required.
- The gate is not yet composed with robots retrieval, the HTTP fetcher, frontier
  settlement, Temporal activities, or controlled production egress. It therefore
  creates no public crawl authority.

## Verification

Real PostgreSQL tests cover cross-tenant races, independent origins, strict delay,
`429`/`503`/transport/latency backoff, exact acquisition and completion retry,
identity conflict, active-authority reduction, abandoned-permit reconciliation,
bounded cleanup, mutation guards, function-only grants, and migration rollback.
Pure Python tests cover every policy/completion boundary and reject malformed
authority before database I/O.
