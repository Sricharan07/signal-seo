# ADR-0029: Preserve One Logical Workflow Across Start Ambiguity

- Status: Accepted
- Date: 2026-09-08
- Owners: Workflow orchestration and command projection

## Context

Slice 0027 records one stable workflow identity before any orchestrator call, but
Signal still has to cross the PostgreSQL-to-Temporal boundary. A Temporal start
can succeed while its response is lost. Retrying under a new ID would create a
second logical workflow, while recording `processing` before positive existence
evidence would make the dashboard claim work that Temporal may never have accepted.

Temporal distinguishes conflict with an open execution from reuse after a closed
execution. Signal also needs a durable first-run reference that remains stable
across delivery retries and later Continue-As-New runs. The business database is
a projection and reference store; Temporal history remains workflow authority.

## Decision

Start `CrawlSite` with the workflow ID already committed during admission. Use
Temporal `WorkflowIDConflictPolicy.USE_EXISTING` for an open execution and
`WorkflowIDReusePolicy.REJECT_DUPLICATE` for a closed execution. Never generate a
replacement workflow ID during retry or ambiguity.

The start request carries one serialization-safe dataclass with schema version,
tenant, site, command, and source-event identifiers. It carries no command body,
credential, secret reference, exception detail, or arbitrary model output. The
workflow type and task queue are closed to `CrawlSite` and `signal.crawl.v1`.
Both the SDK RPC deadline and local coroutine wait are bounded to the same
validated interval.

A normal start response with a canonical `first_execution_run_id` is positive
existence evidence. A matching `WorkflowAlreadyStartedError` with a canonical run
ID is also positive existence evidence after a closed execution. The adapter
records which response path supplied the evidence without claiming whether a
`USE_EXISTING` response created or found an open execution. Timeout, malformed
run identity, mismatched already-started evidence, and every unclassified SDK
exception collapse to `WorkflowStartOutcomeUnknown`; no database progress is
recorded from them.

After positive evidence, one security-definer PostgreSQL function binds the first
run ID, advances the workflow projection from `admitted` to `running`, advances
the command from `workflow_admitted` to `processing`, and appends exact
`command.workflow_started` evidence in one transaction. An exact retry returns
the original event and timestamp. A different run ID is a conflict and can never
replace the first. Recording remains available after tenant or site suspension
because the Temporal request may already have succeeded before authority was
reduced; suspension still prevents new outbox claims and workflow admission.

The HTTP status projection exposes `first_run_id` only with a complete
`processing`/`running` state. It is an observability reference, not a credential
or authorization grant.

## Alternatives

- Start with a random workflow ID on every delivery. Rejected because ambiguous
  retries could create independent executions for one command.
- Use the SDK's default closed-workflow reuse policy. Rejected because a retained
  business command must not become a new execution after the earlier run closes.
- Use only `FAIL` for open conflicts. Rejected because the current SDK supports a
  direct idempotent `USE_EXISTING` response with the first execution run ID.
- Mark the command processing before the Temporal call. Rejected because local
  intent is not evidence of orchestrator acceptance.
- Treat every SDK exception as a definite rejection and retry under fresh state.
  Rejected because the call may have crossed the server boundary before failure.
- Recheck active tenant/site lifecycle while recording. Rejected because it could
  discard proof of a start already accepted before suspension and leave operators
  with an invisible running workflow.

## Consequences

- Repeated starts before and after completion converge on one Temporal workflow
  execution identity for the retained command lifetime.
- PostgreSQL never advances to `processing` without a canonical Temporal run ID.
- A database failure after start is recoverable by repeating the same deterministic
  start and idempotent record operations.
- The database preserves response-path evidence but does not duplicate Temporal's
  authoritative history or infer workflow completion.
- `REJECT_DUPLICATE` means an intentionally repeated business action requires a
  new command and therefore a new deterministic workflow ID.
- There is still no production `CrawlSite` implementation, worker deployment,
  client credential loader, supervised consumer, or completion projector.

## Verification

The SDK-managed loopback Temporal lab starts a test-only `CrawlSite` worker,
verifies exact typed input, repeats start while the execution is open, waits for
completion, repeats start after closure, and observes one execution with one
first-run ID. PostgreSQL tests cover atomic projection, exact retry, concurrent
retry, conflicting runs, post-suspension recording, forced-RLS function-only
access, guards, event rollback, schema upgrade compatibility, failed migration
rollback, and authenticated status. Unit and API tests cover fixed policies,
timeouts, malformed evidence, cancellation, input/configuration bounds, and
coherent public projection.
