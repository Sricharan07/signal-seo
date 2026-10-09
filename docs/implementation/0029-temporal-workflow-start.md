# Slice 0029: Deterministic Temporal Workflow Start

Status: **IMPLEMENTED AND REAL-SERVER QUALIFIED; NO PRODUCTION WORKFLOW WORKER**.

## Outcome

Signal can now turn one admitted snapshot command into one deterministic Temporal
`CrawlSite` execution and durably record the canonical first execution run ID.
The SDK adapter and PostgreSQL projection are separate boundaries: Temporal is
called without a database transaction, and PostgreSQL advances only after
positive workflow-existence evidence.

| Boundary | Implemented behavior |
| --- | --- |
| Workflow identity | Reuses the tenant-qualified ID committed by admission |
| Open duplicate | `USE_EXISTING` returns the same open execution |
| Closed duplicate | `REJECT_DUPLICATE`; matching already-started run is reconciled |
| Start input | Typed schema-one IDs only; no secret or arbitrary payload |
| Timeout | Bounded locally and at RPC; outcome remains unknown |
| Durable projection | First run, command state, and event commit atomically |
| Retry | Same run returns the original event; another run conflicts |
| Suspension race | Already-positive start evidence can still be recorded |
| User status | Coherent `processing`/`running` projection with first run ID |

## Temporal Boundary

`TemporalWorkflowStarter` accepts only a validated `WorkflowAdmission`. It sends
`CrawlSiteWorkflowInput` to the fixed `signal.crawl.v1` task queue and applies
both no-reuse policies explicitly. The default SDK serializer was exercised with
the shared dataclass; an earlier generic `dict[str, object]` attempt failed on the
real worker and was replaced before commit.

The adapter wraps `Client.start_workflow` in a local timeout and also supplies an
SDK RPC timeout. A successful handle must contain a canonical UUID first execution
run ID. A matching `WorkflowAlreadyStartedError` must contain the same workflow
identity, type, and a canonical run ID. Every other exception becomes a sanitized
`WorkflowStartOutcomeUnknown` with no provider message.

`start_acknowledged` and `already_started` describe the response path only. With
`USE_EXISTING`, a normal response can represent a newly created or an already-open
execution, so Signal deliberately does not invent a `created` boolean.

## Database Projection

Migration `0017` expands the command projection to `processing`, the workflow
reference to `running`, and the strict command-event contract to
`command.workflow_started`. The event stores the stable workflow ID, canonical
first run ID, schema version, and closed start-evidence kind. Workflow identity
columns remain immutable, while a trigger permits exactly one `admitted` to
`running` transition.

`control.record_workflow_started` is executable only by `signal_workflow`. That
role still has no direct access to commands, command events, inbox rows, or
workflow references. One transaction locks the exact command and workflow
reference, updates both projections, and appends sequence three. A collision or
event failure rolls back both updates. Exact retries return the first event and
timestamp, including after lifecycle suspension; different run evidence returns
a closed conflict without disclosing the stored run.

The authorized command read and HTTP response now include `first_run_id` only
when command status is `processing` and workflow state is `running`. Accepted and
admitted projections reject that field, while incomplete or cross-command
projections fail closed.

## Real-Server Lab

```sh
.venv/bin/python scripts/run-temporal-tests.py
```

The runner starts the Temporal Python SDK's loopback-only, headless development
server with in-memory SQLite persistence. It removes any externally configured
Temporal address, runs only `tests/temporal`, captures JUnit and sanitized JSON,
checks source stability, records exact SDK/CLI/server versions, and shuts down the
server. The downloaded test binary is cached under ignored `.runtime` state. It
is not a production Temporal topology or persistence qualification.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/temporal tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/temporal tests/tooling
.venv/bin/python -m pip check
```

All 388 non-database Python cases, one real Temporal integration case, and 370
real PostgreSQL 17.11 cases pass. The API subset contains 165 cases. All 11
repository cases and documentation checks across 73 Markdown files pass; Ruff
lint and format checks cover 92 Python files. Reviewed
source-hashed evidence is stored in
[0029-temporal.json](../evidence/0029-temporal.json) and
[0029-postgresql.json](../evidence/0029-postgresql.json). Both labs report
completed cleanup and `production_authority` false.

The evidence files contain source-path keys ending in `pkce_secrets.py` and
`session_tokens.py` whose values are SHA-256 source hashes. Generic-key scanners
may report those keys; they are not credentials.

## Explicit Limits

- The registered `CrawlSite` workflow used by the Temporal lab is test-only. No
  production workflow implementation, activity, crawler, or completion projector
  is registered.
- No supervised consumer composes claim, admission, Temporal start, recording,
  and outbox acknowledgement. The new adapter is injected, not auto-started.
- The lab uses an SDK-managed development server with in-memory SQLite, not the
  separately owned PostgreSQL persistence required for production.
- Production Temporal TLS, namespace provisioning, authentication, visibility,
  worker build versioning, history replay, backup, restore, and upgrade tests are
  not implemented.
- No SEO analysis, competitor research, approval, CMS/GitHub operation, undo,
  dashboard, Telegram action, or production authority is added.

See [ADR-0029](../adr/0029-deterministic-temporal-workflow-start.md) for the
identity, reuse, evidence, suspension-race, and ambiguity decisions.
