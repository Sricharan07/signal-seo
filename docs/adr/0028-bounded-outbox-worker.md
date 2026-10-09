# ADR-0028: Keep Publication Outside Short Database Operations

- Status: Accepted
- Date: 2026-09-08
- Owners: Command delivery and service operations

## Context

Signal has database operations for tenant paging, fenced outbox claim,
acknowledgement, and rescheduling, but no component composes them into a
long-running loop. A naive loop could retain a connection or transaction while a
network publish blocks, blindly retry an ambiguous timeout, process enough rows
that later leases expire before use, or stop entirely when one tenant or telemetry
sink fails.

The transport remains undecided. The worker therefore needs a small boundary
that can be tested now without pretending that a broker, Temporal, or production
deployment exists.

## Decision

Add a synchronous delivery-worker core with injected `OutboxStore`,
`EventPublisher`, observer, and cooperative stop signal. The PostgreSQL store
adapter opens a fresh scheduler connection for each list, claim, acknowledge, or
reschedule operation. The publisher is called only after the claim operation and
its connection context have completed.

Each cycle reads one bounded page from the active tenant directory and claims at
most one envelope per tenant. The database supports larger batches, but this
synchronous worker fixes its batch size at one until bounded concurrent
publication and per-call timeouts are implemented. A cursor advances across
tenant pages and resets only after an empty page, preventing one low-ID tenant
from monopolizing every cycle.

Publication outcomes are explicit:

- normal return means the transport positively accepted the envelope and the
  worker attempts the exact fenced acknowledgement;
- `PublishNotAccepted` means the transport positively rejected it, so the worker
  reschedules the exact live lease with a bounded delay; and
- `PublishOutcomeUnknown`, timeout, unexpected exception, non-null return, or
  acknowledgement failure leaves the lease untouched for safe expiry and
  redelivery.

A stale acknowledgement or reschedule fence is counted separately and never
reported as delivered. Scan and claim failures are bounded and isolated. Observer
failures cannot interrupt delivery. Observations contain only stable tenant,
outbox, event, attempt, and closed outcome fields; exception messages and payloads
are deliberately excluded.

`run_forever` completes one cycle at a time and waits through the injected stop
signal. Both active and idle delays are bounded, preventing a tight retry loop and
allowing cooperative shutdown.

## Alternatives

- Publish inside the claim transaction. Rejected because a network dependency
  must not hold database locks or extend transaction lifetime.
- Claim a large batch and publish sequentially. Rejected because later envelopes
  can lose their lease before the worker reaches them.
- Reschedule every publisher exception. Rejected because a timeout may follow a
  successful external acceptance and a blind retry can duplicate effects.
- Treat publisher return data as a receipt. Rejected for now because no durable
  transport receipt contract exists; success is an exact `None` return.
- Let telemetry failure stop the worker. Rejected because observability must not
  become an availability dependency for already-authorized delivery.
- Add a concrete broker or Temporal client in this slice. Rejected so loop,
  failure classification, and connection lifetime can be verified independently.

## Consequences

- The worker can run for an arbitrary duration without one unbounded model or
  database transaction.
- Every network publish happens after claim commit and before a separate fenced
  acknowledgement operation.
- Ambiguous outcomes favor redelivery under stable identity rather than false
  completion; downstream deduplication remains mandatory.
- Throughput is intentionally conservative at one envelope per tenant per cycle.
  Concurrent publication needs its own lease-budget and shutdown design.
- A deployment wrapper, credential loading, transport implementation, process
  supervision, metrics exporter, and health endpoint remain future work.

## Verification

Twenty-seven focused cases cover bounds, operation ordering, positive rejection,
three ambiguous failure classes, unexpected return data, stale fences,
bookkeeping failures, per-tenant isolation, sanitized observations, pagination,
observer failure, cooperative shutdown delays, and fresh connection contexts.
The 350-case real PostgreSQL suite also remains green against the final worker
source hash.
