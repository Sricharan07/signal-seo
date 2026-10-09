# ADR-0031: Acknowledge Delivery Only After Durable Workflow Start

- Status: Accepted
- Date: 2026-09-08
- Owners: Workflow orchestration and outbox delivery

## Context

Slices 0026 through 0029 established fenced outbox claims, durable workflow
admission, deterministic Temporal start, and atomic first-run recording as separate
operations. A process must now compose those boundaries without treating a timeout,
lost response, malformed adapter result, or database failure as proof that work was
not accepted.

The outbox is at-least-once. A process may crash after any boundary, including after
Temporal accepts a start or after PostgreSQL records it but before the outbox is
acknowledged. Duplicate delivery must converge on the original admission and first
execution. Shutdown must not abandon a claimed request halfway through the
composition, while a stale lease must never authorize a late acknowledgement.

Migration 0017 exposed a related receipt problem: duplicate admission returned the
workflow reference's current projection. Once start recording advanced that
projection to `running`, an otherwise valid redelivery no longer returned the
original `admitted` admission receipt expected by the start boundary.

## Decision

Run claim, admission, Temporal start, durable start recording, and acknowledgement
as one asynchronous consumer composition. Every PostgreSQL operation owns a fresh,
short-lived connection and completes before the Temporal call; no database
transaction crosses provider I/O.

Acknowledgement occurs only after admission, Temporal existence evidence, and the
durable `running` projection all return coherent typed results. A positive admission
rejection is rescheduled. Every unprovable outcome at admission, Temporal start, or
recording remains unknown and leaves the fenced lease to expire. Invalid adapter
output is unknown rather than success. Observer failure cannot change the delivery
result.

Use the stable event, command, workflow, and first-run identities on every retry.
Migration 0018 makes duplicate admission return the immutable original
`state_projection = admitted` receipt while leaving the authoritative workflow
reference at its later `running` state. It changes no workflow authority and grants
no new table access.

Translate `SIGINT` and `SIGTERM` into cooperative shutdown. Finish the envelope
already claimed by the current coroutine, then stop before claiming another tenant.
All acknowledgement and reschedule operations continue to require the exact live
worker and attempt fence.

The executable accepts separate `signal_scheduler` and `signal_workflow`
credentials. Non-loopback use requires `sslmode=verify-full` for both databases and
Temporal TLS. Explicit insecure mode accepts only literal loopback endpoints. It
emits a closed JSON-lines observation schema containing outcomes and durable IDs,
never payloads, DSNs, certificate bodies, or exception text.

## Alternatives

- Acknowledge after Temporal start but before recording. Rejected because the
  command could remain `workflow_admitted` with no deliverable event left to repair
  the projection.
- Reschedule every exception immediately. Rejected because Temporal or PostgreSQL
  may have committed despite a lost response; lease expiry plus stable identities is
  the safe retry path.
- Hold one transaction across admission, Temporal, and acknowledgement. Rejected
  because external I/O cannot participate in the database transaction and would
  create long locks with false atomicity.
- Return the workflow reference's latest state as the duplicate admission receipt.
  Rejected because later projection progress must not mutate an earlier durable
  receipt contract.
- Catch cancellation and immediately release the lease. Rejected because the
  external outcome may already be unknown and a stale worker must not rewrite a
  replacement claim.
- Package an in-repository process manager now. Deferred because deployment health,
  image, secret delivery, and declarative supervision must be designed with the
  production worker rather than represented by a dormant manifest.

## Consequences

- Crash and acknowledgement-loss retries converge on one PostgreSQL admission and
  one Temporal logical workflow.
- Delivery means the workflow start is durably projected, not merely requested.
- A definite pre-start rejection is retried after a bounded delay; unknown outcomes
  wait for lease expiry and remain visible in sanitized observations.
- Shutdown drains one active envelope, so supervisor termination grace must exceed
  the bounded start sequence and database operations.
- The process is suitable for an external supervisor but has no health endpoint,
  packaged supervisor, metrics exporter, alert backend, or production deployment
  manifest yet.
- There is still no production `CrawlSite` workflow, completion projection,
  dead-letter policy, SEO work, connector operation, or production authority.

## Verification

Unit tests cover boundary ordering, definite rejection, unknown outcomes, malformed
adapter results, cancellation, observer containment, configuration, TLS files,
role separation, signal registration, and sanitized process output. Real PostgreSQL
tests cover the stable duplicate receipt and forward-only migration failure. A joint
disposable PostgreSQL and Temporal test accepts work for two tenants, starts a
test-only workflow, injects acknowledgement loss, waits for the database lease, and
proves redelivery converges on one Temporal execution and the original first run.
