# ADR-0027: Admit Workflow Work Before Calling the Orchestrator

- Status: Accepted
- Date: 2026-09-08
- Owners: Command delivery, workflow admission, and command service

## Context

The outbox now supports at-least-once publication with stable event identity. A
publisher crash after transport acceptance can deliver the same event again, and
transport acknowledgement does not prove that a consumer processed it. Starting
an independently named workflow for every delivery would turn one durable command
into duplicate logical work.

Temporal can reject or reuse a workflow ID, but its finite retained history is not
the business deduplication authority. Signal also needs a durable command progress
record that the authenticated status route can expose without claiming that a
workflow has started or completed.

## Decision

Before any workflow client call, atomically persist three facts in PostgreSQL:

- a `consumer_inbox` receipt keyed by tenant, the fixed consumer release key, and
  source event ID;
- one `workflow_refs` row with deterministic identity
  `signal:CrawlSite:{tenant_id}:{command_id}` and state `admitted`; and
- command progress `accepted -> workflow_admitted` plus append-only event number
  2, `command.workflow_admitted`.

The admission function accepts the complete immutable outbox envelope, not only
an event ID. It compares tenant, site, outbox, event, command, aggregate, type,
schema, and payload with the authoritative row before writing anything. Only the
fixed `workflow.command-start.v1` consumer and `command.accepted` schema version 1
are supported in this slice.

New admission locks and rechecks active tenant-directory, tenant, and site rows.
Lifecycle reduction therefore conflicts with acceptance of new work. A duplicate
whose receipt already committed is returned before that gate, even after
suspension, so the consumer can acknowledge an already-processed redelivery
instead of retrying forever.

A separate `signal_workflow` runtime role receives execute permission on only the
admission function and no direct table access. The publisher's `signal_scheduler`
credential cannot fabricate consumer progress. Forced RLS remains active in the
security-definer function. Inbox receipts and this initial workflow projection
are immutable until a later migration introduces reviewed workflow-start
transitions.

Command intent remains immutable. A replacement trigger permits only the exact
progress transition to `workflow_admitted` with a one-step `row_version`
increment. The original POST retry remains an acceptance receipt and therefore
still returns `accepted`; the separately authorized GET status route exposes the
current progress and deterministic workflow identity.

## Alternatives

- Depend only on Temporal workflow-ID reuse. Rejected because retained workflow
  history is not Signal's long-lived business deduplication record.
- Insert the inbox after starting Temporal. Rejected because a crash between the
  start and insert would lose proof that a retry is a duplicate.
- Mark the command running during admission. Rejected because no orchestrator has
  accepted a start request yet.
- Require `outbox.delivered_at` before admission. Rejected because consumer
  delivery can race the publisher's bookkeeping acknowledgement; the immutable
  envelope is the authority and both operations are independently idempotent.
- Block duplicate acknowledgements after suspension. Rejected because committed
  work would become an unbounded retry loop without creating new safety.
- Reuse the publisher credential for admission. Rejected because a compromised
  publisher must not be able to fabricate consumer progress.
- Grant the workflow role direct table writes. Rejected because one narrow
  function can verify the envelope, lifecycle, and atomic state transition with
  less authority.

## Consequences

- A duplicate delivery returns the original processing time, progress event, and
  workflow identity without creating another logical workflow.
- A crash before commit writes nothing; a crash after commit can safely repeat the
  same admission.
- A future Temporal starter must use the persisted workflow ID and record the
  actual first run ID in a separately reviewed transition.
- `workflow_admitted` proves durable consumer processing only. It does not prove
  transport acknowledgement, Temporal start, crawl execution, or SEO output.
- The initial workflow reference is intentionally immutable and has a null
  `first_run_id`; future lifecycle support requires a migration and tests.

## Verification

All 350 real PostgreSQL 17.11 cases pass. New coverage proves atomic admission,
exact-envelope rejection, concurrent and post-suspension deduplication, lifecycle
gates and locks, rollback on injected failure, immutable intent and receipts,
least-privilege function execution, migration preservation and rollback, stable
human retry receipts, and authorized progress reads. Three API cases cover the
complete progress response and fail-closed missing or mismatched workflow data.
