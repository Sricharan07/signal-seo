# ADR-0074: Keep Weekly Work Record-Only Until PR Integration

Status: Accepted for the internal 0090 foundation, 2026-09-29.

## Context

Revision 4.0 calls for a weekly autonomous employee with standing authority,
pause, budget limits, and verifiable effects. The existing 0066 crawl, 0068
findings, 0088 grant, and 0089 gate can be composed now, while sealed 0081
recipes and the 0084 PR writer are not available on this branch. A schedule
must not turn a historical gate result into an external-write permit.

## Decision

Use one UTC-week cycle per site, a deterministic Temporal workflow, and an
idempotent activity receipt at every stage. The schedule reconciler evaluates
current site, owner, grant, release, and recovery authority before creating or
unpausing a per-site schedule. Database admission fences duplicate executions
for one site and week. Candidate preparation and handoff are typed boundaries;
the current candidate adapter returns unavailable and the handoff only records
an immutable, current `ship` revision. No external writer is reachable.

Pause is a database authority transition, not merely a Temporal toggle. It
atomically revokes site grants through the independent restriction journal and
blocks new weekly crawl dispatch. In-flight commands and outcomes remain
visible. Clearing pause requires a new human grant for further work. Weekly
cap exhaustion records exact deferred revision hashes for the next window.
Owner reports present only durable command, stage, gate, and handoff evidence.

## Alternatives

- A monolithic long-running activity would hide durable stage boundaries and
  make termination or replay ambiguous.
- A Temporal-only pause would leave already queued work and gate finalization
  authorized during schedule propagation.
- Calling the PR writer directly would couple unfinished recipe and write
  authority to a loop before the 0084 integration and dispatch recheck exist.

## Consequences

The weekly loop is an internal, record-only foundation. A deployed reconciler,
weekly worker, recipe adapter, PR consumer, live verification, and measurement
are still required. The API report is owner-scoped but has no dashboard view.
No autonomous production write is claimed.

## Verification

See [0090 implementation](../implementation/0090-weekly-loop.md) and
[0090 evidence](../evidence/0090-weekly-loop.json). Real PostgreSQL, Temporal,
and joint consumer labs cover durable admission, pause, stage restart, replay,
reporting, and record-only handoff behavior.
