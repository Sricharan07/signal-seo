# Slice 0032: Crawl Workflow State And Terminal Projection

Status: **IMPLEMENTED AND JOINTLY QUALIFIED; NETWORK CRAWLING, ARTIFACT STORAGE,
AND A DEPLOYED WORKER ARE NOT IMPLEMENTED**.

## Outcome

Signal now has a deterministic production `CrawlSite` workflow definition and
activity boundaries that move an accepted snapshot from `processing` to one
durable terminal state. The workflow is replay-tested against a real Temporal
server, and terminal state is committed through the least-privilege workflow role
in real PostgreSQL before Temporal closes.

| Boundary | Implemented behavior |
| --- | --- |
| Workflow input | Exact tenant/site/command/source-event identity with pinned scope and crawl-policy versions |
| Crawl activity | Injected executor, bounded heartbeat schema, fixed timeout and three-attempt retry policy |
| Success | Bounded complete/partial manifest reference with UUID, SHA-256, counts, and bound versions |
| Failure | Closed `crawl_activity_failed` reason with no untrusted error text |
| Cancellation | Wait for activity cancellation, commit `crawl_cancelled`, then close as cancelled |
| Projection | Atomic command, workflow-reference, and event-four transition under one narrow function |
| Redelivery | Stable admission and start receipts even after the current projection is terminal |
| Status API | Authorized terminal status with either manifest metadata or one closed reason |

This implements another bounded part of Revision 3.2 sections 6.1 through 6.5,
7.5, 28.1, 29.1, 30.1, and 30.2. It exercises the implemented forms of INV-007,
INV-008, INV-013, INV-017, and INV-024 plus EC-060, EC-062, EC-063, EC-065, and
EC-099. It does not implement the network and artifact controls in section 11 and
does not grant connector or external-write authority.

## Workflow Contract

`CrawlSiteWorkflowInput` is a serialization-safe dataclass. The workflow rejects a
noncanonical UUID, unsupported schema/version, or Temporal identity that differs
from `signal:CrawlSite:{tenant_id}:{command_id}` before scheduling I/O. Scope and
crawl-policy versions are carried through the activity result so a result generated
under a different policy cannot be committed.

The deterministic workflow contains no database, clock, random, filesystem,
network, or credential access. It schedules explicitly versioned activity names and
uses Temporal history for orchestration. Temporal worker build-ID routing and a
deployable workflow-code release identity are not configured in this slice.

## Activity Policy

The crawl adapter accepts an injected `CrawlExecutor`. It emits a validated
`starting` heartbeat, permits the executor to report bounded
`starting`/`crawling`/`finalizing`/`completed` progress, then emits the final counts.
It returns only a `CrawlManifestReference`; fetched content and provider details
cannot cross this history boundary.

The execution activity has a ten-minute schedule-to-close timeout, two-minute
start-to-close timeout, ten-second heartbeat timeout, and three attempts with
one-to-five-second exponential backoff. Only `crawl_retryable` is retried.
Classified rejection, unexpected executor exceptions, invalid result shape, and
version mismatch use fixed non-retryable error types and sanitized messages.

The terminal activity opens a fresh workflow-role connection, runs outside the
event loop, and retries availability failures up to eight times within two minutes.
Input rejection, committed-evidence conflict, and invalid returned evidence are
non-retryable. No transaction spans Temporal and PostgreSQL.

## Durable Terminal State

Migration 0019 extends only the existing command and workflow progress state
machines:

```text
processing/running/event 3
  -> succeeded/succeeded/event 4 + crawl manifest reference
  -> failed/failed/event 4 + crawl_activity_failed
  -> cancelled/cancelled/event 4 + crawl_cancelled
```

The security-definer terminal function locks the exact command and workflow,
validates the canonical first execution run ID, and commits both projections plus
the append-only event atomically. Exact duplicate evidence returns the original
event with `duplicate=true`; different terminal evidence returns a conflict. The
workflow role still has no direct table access.

Completion remains recordable after tenant suspension or site archival so accepted
in-flight work is not made untraceable. Migration 0019 also returns event three's
original start receipt when a delivery retry arrives after terminal completion.
The current workflow reference remains terminal and no extra event is added.

The command status query now returns event-four reason and command result reference.
The core and API layers independently validate exact shape, identity, state/result
coherence, UUIDs, hash, count bounds, and versions before responding.

## Joint Boundary Qualification

```sh
.venv/bin/python scripts/run-consumer-tests.py
```

The joint lab now registers `CrawlSiteWorkflow`, the production activity adapters,
a deterministic synthetic executor, and the real PostgreSQL terminal store. It
does not perform network I/O. Two tenants complete successfully, then an injected
acknowledgement loss causes the same delivered event to be retried after lease
expiry. The test proves there is one Temporal execution, the original first run,
attempt-two outbox delivery, exactly four command events, and coherent terminal
state.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/consumer tests/control_plane tests/identity tests/temporal tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/consumer tests/control_plane tests/identity tests/temporal tests/tooling
.venv/bin/python -m pip check
```

All 468 non-database Python cases, 171 API cases, 390 real PostgreSQL 17.11
cases, three real Temporal cases, and one joint PostgreSQL/Temporal consumer case
pass. Ruff lint and format checks cover 110 Python files, and dependency
consistency passes. All 12 repository cases and documentation checks across 84
Markdown files also pass. The five real Keycloak and seven real OpenBao regression
scenarios remain green.

Reviewed source-hashed provider evidence is stored in
[0032-postgresql.json](../evidence/0032-postgresql.json),
[0032-temporal.json](../evidence/0032-temporal.json), and
[0032-consumer.json](../evidence/0032-consumer.json). Each lab reports completed
cleanup and `production_authority` false. Values under `source_sha256` are file
digests, including keys whose source filenames contain words such as `secrets` or
`tokens`; they are not credentials.

## Explicit Limits

- `CrawlExecutor` is a tested protocol, not an HTTP crawler. There is no URL
  normalization, robots handling, redirect policy, DNS rebinding defense,
  private-network filter, fetch budget, Playwright execution, or external request.
- The result is a bounded reference contract. No artifact manifest/body is stored,
  uploaded, read, existence-checked, retained, or deleted yet.
- There is no production worker executable, OCI image, build-ID deployment routing,
  supervisor, health/readiness endpoint, metrics exporter, alert backend, or
  qualified rolling upgrade. Replay tests do not substitute for those controls.
- The status API remains unconfigured in the default process. No customer command
  journey, dashboard, Telegram update, or approval operation is enabled.
- No model, GSC, GitHub, Telegram, CMS, competitor research, SEO recommendation,
  approval, undo, or production authority is added.

See [ADR-0032](../adr/0032-durable-crawl-terminal-projection.md) for the activity,
projection, cancellation, stable-receipt, and deferral decisions, and the
[crawl workflow runbook](../runbooks/crawl-workflow.md) for current diagnostics.
