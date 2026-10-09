# ADR-0039: Record Dispatch Before Composing Crawl Page I/O

- Status: Accepted
- Date: 2026-09-09
- Scope: Authority-free crawl page-attempt composition

## Context

Signal already had separately qualified frontier leases, current robots decisions,
global origin permits, a pinned HTTP boundary, and encrypted fetch observations.
Calling those boundaries in sequence without another durable receipt leaves a
dangerous ambiguity: a worker can send HTTP, crash before recording the response,
and then repeat the request because durable state says only that a permit existed.

The HTTP request cannot be made atomic with PostgreSQL. Holding a database
transaction across DNS, connection, response streaming, or artifact storage would
also create unacceptable lock and recovery behavior.

## Decision

Record one immutable `crawl_page_attempts` row in state `dispatched` before calling
HTTP. The attempt UUID is the exact global origin-permit UUID and is also bound to
the tenant/site/run/frontier URL, frontier lease receipt and worker, current robots
snapshot/reason, and database dispatch time.

Only the caller that inserts the dispatch receipt may invoke HTTP. A concurrent or
later caller seeing `dispatched` first looks for the exact immutable fetch
observation. If evidence exists, it finalizes without fetching. If evidence is
absent, it returns `dispatch_outcome_unknown` and does not issue another request.

Terminal page-attempt update and exact origin-permit completion occur in one
PostgreSQL transaction. Terminal states are `observed` with an exact observation,
or `failed` with one closed local reason. Retry-After classification preserves
whether delay came from provider seconds, provider date, missing/invalid fallback,
or a 24-hour cap. A definitive artifact/observation non-commit records conservative
origin degradation without inventing a response observation. Database commit
uncertainty remains `dispatched` unless an exact observation can be recovered.
The frontier-lease row serializes first observation commit against first dispatch,
and an observation insert trigger serializes against terminal failure. Preexisting
evidence blocks dispatch, while failed settlement blocks a late observation.

Fresh dispatch still requires the exact current frontier/workflow/lifecycle,
unexpired current robots snapshot, active HTML permit, matching canonical origin
and authority fingerprint, and permit delay no shorter than the robots delay.
Historical exact reconciliation does not require resurrected current authority.

## Consequences

- The composition is at-most-once with respect to Signal dispatch, preferring an
  unresolved receipt over a blind duplicate request.
- PostgreSQL transactions remain short and no lock is held during network or file
  I/O.
- A response observation committed before finalization can converge safely after
  restart, including after authority reduction.
- Database commit uncertainty cannot create a contradictory failed attempt; a
  retry either recovers the exact observation or reports unresolved dispatch.
- Preexisting and late observation races fail closed without granting another HTTP
  dispatch or allowing contradictory terminal evidence.
- A crash after dispatch but before observation remains deliberately unresolved.
  Frontier settlement must not silently classify or retry it.
- A permit expiring before finalization leaves durable evidence for operator or
  later settlement logic; this slice does not rewrite its `lease_expired` receipt.
- The local encrypted backend and injected resolver remain qualification-only.
  This decision grants no production crawl authority.

## Alternatives Rejected

- **Retry whenever no observation exists:** can duplicate an already accepted HTTP
  request after response loss.
- **Record dispatch after HTTP:** preserves the same crash gap.
- **Hold a transaction around HTTP and artifact storage:** couples remote latency
  and failure to database locks and still cannot make the provider atomic.
- **Treat an active permit as proof no request was sent:** a permit records
  authority, not whether network dispatch occurred.
- **Auto-close an unknown dispatch as failed:** invents evidence and can allow an
  unsafe retry through later frontier logic.
