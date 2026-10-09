# Slice 0090: Record-Only Weekly Loop

Status: **INTERNAL ORCHESTRATION FOUNDATION; NO AUTONOMOUS PR OR PRODUCTION WRITE**.

Security-critical. Migration 0051 adds one cycle per verified site and UTC week,
immutable stage receipts, record-only gate-approved handoffs, next-window
deferrals, and an owner-scoped evidence report. `WeeklySiteLoop` runs observe,
analyze, plan, prepare, gate, handoff, verify, measure, and report as separate
Temporal activities. Its start time determines the week; the first execution
run ID fences duplicate schedule starts. Restarting a worker after each stage
does not repeat a durable result, and workflow histories replay deterministically.

The observe adapter admits the existing 0066 `CrawlSite` command through its
outbox, waits for its terminal projection, and analyzes the manifest with 0068.
Planning orders sealed findings by evidence and severity, but does not assert
that a fix exists. The typed candidate port is empty until 0081 supplies sealed
recipes. The 0089 gate remains the sole candidate decision authority. A handoff
is only an immutable database record of a current `ship` with a reserved budget;
there is no PR, merge, deployment, or publishing call in this module. Until the
0084 consumer is integrated, verification and measurement are explicitly
unavailable, not synthetic successes.

The per-site Temporal schedule reconciler uses current verified-site, owner,
grant, reviewed-release, and recovery-generation authority. It sets a Monday
09:00 UTC schedule with overlap `SKIP` and pause-on-failure. No deployed
reconciler or weekly worker is wired into the production consumer image in
this slice; schedule creation and stage execution are qualified in disposable
Temporal and joint PostgreSQL/Temporal labs only.

Owner pause atomically blocks new grant eligibility and revokes extant site
grants through the 0102 restriction outbox. The weekly crawl admission checks
again before new frontier leases and egress dispatch, including a pause after
robots retrieval. Already dispatched effects remain in the command projection
and the report; pause does not erase them. Clearing pause never restores a
revoked grant: a new human standing authorization is required. A reduced grant
or changed recovery generation stops new stages and prevents a late handoff.
The API exposes owner-only pause, pause-clear, and per-week reports. The report
includes command status and its owner-scoped status URL, stage evidence
references, gate decisions and owner
review IDs, handoff records, and next-week deferred revision hashes. It includes
no fabricated traffic, ranking, conversion, or effect metrics. Dashboard
presentation is not connected yet.

## Verification

Run the exact commands in [0090 evidence](../evidence/0090-weekly-loop.json).
Real PostgreSQL tests cover grant/pause/crawl races, schedule eligibility,
duplicate cycle admission, immutable stage and handoff records, deferral, owner
reporting, non-owner isolation, and atomic migration rollback. Real Temporal
tests cover schedules, all stages, worker termination after every durable stage,
stop/failure, and history replay. The joint consumer lab exercises a weekly
observation command with the existing outbox and a real Temporal schedule.

## Limits

The recipe candidate adapter, PR consumer, deployed schedule reconciler and
worker, dashboard report view, live provider qualification, deployment
observation, and real effect measurement are not implemented here. Historical
gate records and reserved budget are evidence, not a reusable external-write
permit. Integration must bind authoritative sealed revisions and exact PR
targets, recheck current authorization at dispatch, and qualify real provider
success before any unattended write is enabled.
