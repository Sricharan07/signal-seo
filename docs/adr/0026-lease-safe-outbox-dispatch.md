# ADR-0026: Dispatch Outbox Events With Short Fenced Leases

- Status: Accepted
- Date: 2026-09-08
- Owners: Command delivery, scheduler, and workflow ingress

## Context

Command acceptance already commits one immutable event and outbox row, but the
delivery columns were intentionally unusable: the original immutability trigger
rejected every update. Starting a workflow while holding a database row lock would
couple transaction health to a network dependency. Deleting a row after one send
would lose the stable identity needed to recover from a crash or ambiguous
acknowledgement.

The scheduler can enumerate a minimal tenant directory. Letting it select or
update all outbox columns directly would grant more data and mutation authority
than dispatch requires, while claiming separately for every site would require a
second global site directory that does not yet exist.

## Decision

Keep the scheduler's direct database authority limited to reading the active
tenant directory. Revoke its direct `app.outbox` access and expose three exact
`SECURITY DEFINER` functions:

- claim up to 100 currently available rows for one active tenant with
  `FOR UPDATE SKIP LOCKED` and a 1-300 second lease;
- mark one row delivered only while tenant, outbox ID, worker key, attempt count,
  and unexpired lease all match; and
- release one current lease with a database-clock retry delay of 1-3600 seconds.

The claim function sets only transaction-local tenant scope and explicitly leaves
site scope empty. A role-specific forced-RLS policy permits this tenant-wide view
only while the security-definer owner executes the reviewed functions. It locks
both minimal directory and tenant lifecycle records and returns no work unless
both are active. The caller publishes only after the claim transaction commits.

Each successful claim increments `attempt_count`; that value is the delivery
fence. An expired lease may be reclaimed, but the prior worker can no longer
acknowledge or reschedule it. A transport failure keeps the same outbox/event IDs,
clears the lease, and moves only `available_at`. A successful transport
acknowledgement sets database time in `delivered_at` and clears the lease.

Replace blanket outbox immutability with a trigger that preserves tenant, site,
IDs, aggregate identity, event type, schema, and payload while permitting only the
three exact bookkeeping transitions. Delivered rows remain retained and cannot be
reclaimed or deleted. Restrict the API role to only the columns required to insert
a fresh pending outbox record; it cannot set leases, attempts, or delivery state.

Delivery acknowledgement remains available after tenant suspension because it
records a publication that may already have occurred. Suspension blocks every new
claim. An acknowledgement says the configured transport accepted the envelope;
it does not say a workflow processed it or a command completed.

## Alternatives

- Publish while holding `FOR UPDATE` locks. Rejected because network latency and
  failure would hold database locks and make recovery brittle.
- Delete after publish. Rejected because publish acknowledgement can be lost and
  deduplication identity must survive redelivery and replay.
- Grant the scheduler table-wide `SELECT` and `UPDATE`. Rejected because guarded
  functions and column restrictions express a materially smaller capability.
- Treat lease expiry as proof the old publish stopped. Rejected because an
  external request can outlive its local lease.
- Update command status when the outbox is marked delivered. Rejected because
  transport acknowledgement is not consumer processing evidence.
- Add a broker or Temporal client in the same slice. Rejected so persistence,
  concurrency, and crash semantics can be qualified before network composition.

## Consequences

- Multiple workers can claim disjoint batches without blocking each other.
- A crash before publish leaves a leased row that becomes available after expiry;
  a crash after publish but before acknowledgement causes expected redelivery.
- Consumers must deduplicate on stable event and command identity. Exactly-once
  external delivery is not claimed.
- Operators can inspect retained attempt and delivery bookkeeping without losing
  the immutable event envelope.
- Attempt exhaustion, dead-letter policy, dispatcher process supervision, and
  actual workflow start remain future work. Slice 0027 adds the consumer inbox,
  stable workflow identity, and admission progress.

## Verification

All 332 real PostgreSQL 17.11 cases pass. The suite covers bounded and concurrent
claims, disjoint batches, active lifecycle gates and locks, expired-lease
redelivery, stale-fence rejection, retry scheduling, post-suspension
acknowledgement, transition guards, immutable
payloads, column-level API insertion, exact scheduler function privileges, direct
table denial, transaction-local scope cleanup, non-autocommit rejection, migration
rollback, and preservation of a pending pre-migration event.
