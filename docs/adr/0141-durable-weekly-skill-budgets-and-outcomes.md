# ADR-0141: Durable Weekly Skill Budgets and Outcomes

Merge train 3 also binds writing-quality monthly model calls to the existing
Brain skill handle and each admitted source's standing reservation. Exact
classification/extraction IDs and both budget ceilings are checked before
dispatch; cap-setting remains owner-only. The implementation record contains
positive, negative and failure qualification for this integration.

Status: Accepted for the local internal boundary.
Date: 2026-10-03.

## Decision

Commit immutable `(site, cycle, stage)` intent and bounded resource plan before
I/O. Atomically reserve units against the aggregate standing ledger; failed batch
admission consumes no partial budget. Apply writer/SMTP caps additionally. Paid
extraction needs a conservative upper bound covering its bounded provider cascade.

Persist closed outcomes, reasons and bounded refs. Completion requires an intent.
Replay returns evidence; missing results become `OUTCOME_UNKNOWN` without another
dispatch. Retain unknown/failed holds and exclude previously intended Brain content
from automatic retries. Report reserved cents as reserved, with paid actual spend
unreported. Independent failure permits partial-source strategy, but a successful
cycle snapshot is required for unaccepted brief proposals.

For closed version-one cycles, atomically record `REPORT_QUEUED`, project the same
report and queue verified opted-in recipients through 0093. Outbox is not SMTP
success; existing receipts and ambiguity handling remain authoritative. Legacy
histories keep their original notification behavior.

## Alternatives

In-memory idempotency loses holds on death. Ambiguous retries can repeat paid calls.
Releasing holds assumes remote non-execution. Reporting reservations as charges or
queues as delivery simulates readiness. None is selected.

## Consequences

Restart is conservative and inspectable. Budget may remain unavailable until reset
or reviewed reconciliation; no hold-release/redispatch authority is added. Bounds
need reconfirmation before live runs. Real PostgreSQL/Temporal tests exercise kill,
replay, cap, failure and report reasons; live providers remain unqualified.

Evidence: [0135](../implementation/0135-weekly-orchestration.md) and
[checks](../evidence/0135-weekly-orchestration.json).
