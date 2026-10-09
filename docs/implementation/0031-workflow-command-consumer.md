# Slice 0031: Workflow Command Consumer

Status: **IMPLEMENTED AND JOINTLY QUALIFIED; EXTERNAL SUPERVISION AND PRODUCTION
WORKFLOW ARE NOT IMPLEMENTED**.

## Outcome

Signal now has an executable, cooperatively stoppable process that composes the
existing command path from fenced outbox claim through durable workflow start and
delivery acknowledgement. It is the first end-to-end runtime crossing both the
business PostgreSQL and Temporal boundaries, but it registers no production
workflow and receives no external-write authority.

| Boundary | Implemented behavior |
| --- | --- |
| Dispatch | Deterministic tenant paging and one fenced envelope at a time |
| Admission | Exact event deduplication through a fresh workflow-role connection |
| Temporal | Fixed `CrawlSite` type and `signal.crawl.v1` queue with stable identity |
| Recording | Canonical first run and `processing`/`running` progress before ack |
| Delivery | Ack only after all three stages return coherent positive evidence |
| Ambiguity | Unknown admission/start/record/ack outcome remains leased for redelivery |
| Shutdown | `SIGINT`/`SIGTERM` drain the active envelope and block the next claim |
| Telemetry | Closed JSON-lines lifecycle, delivery, and workflow-stage observations |
| Transport | Verified database TLS and Temporal TLS outside explicit literal loopback |

This implements part of Revision 3.2 sections 6.3, 6.4, 7.5, 28.1, 29.1,
30.1, and 30.2. It exercises the internal forms of INV-007, INV-008, INV-013,
INV-017, and INV-024 plus EC-060, EC-062, EC-063, EC-065, and EC-099. It performs
no external SEO write, so it does not claim to satisfy the independent write journal
or provider reconciliation required by INV-006 and EC-061.

## Runtime Composition

`AsyncDeliveryWorker` keeps the existing outbox semantics while awaiting an async
publisher. PostgreSQL calls run outside the event loop and use fresh contexts.
Cancellation is not converted into a publish failure; ordinary failures are reduced
to either definite rejection or unknown outcome.

`WorkflowEventPublisher` performs admission, Temporal start, and recording in that
order. It validates the identity and state returned at every boundary before
allowing acknowledgement. The store and starter stay behind narrow protocols, and
the production composition uses the already-qualified `TemporalWorkflowStarter`.

`run-workflow-consumer.py` is an executable entrypoint intended to be owned by an
external supervisor. Configuration is strict and fail-closed. Scheduler and
workflow credentials cannot be interchanged. The outbox lease must exceed the
Temporal deadline by at least 20 seconds, and all delays, page sizes, addresses,
names, and TLS files are bounded.

The complete operating procedure and configuration inventory are in the
[workflow consumer runbook](../runbooks/workflow-command-consumer.md).

## Stable Admission Receipt

Migration 0018 corrects duplicate admission after a workflow has advanced to
`running`. Admission is an immutable receipt: a redelivery now returns the original
`admitted` state and event identity even though `app.workflow_refs` truthfully
remains `running`. Without this correction, acknowledgement loss after start
recording could strand the outbox because the consumer rejected the newer projection
as an invalid admission result.

The migration replaces only the narrow security-definer function, preserves its
function-only workflow grant, and remains forward-only. Real PostgreSQL tests cover
upgrade failure rollback and the distinct receipt/projection states.

## Joint Provider Lab

```sh
.venv/bin/python scripts/run-consumer-tests.py
```

The runner owns a disposable loopback PostgreSQL 17.11 container and an SDK-managed
Temporal development server. It applies all migrations twice, provisions only
synthetic tenants, runs only `tests/consumer`, hashes the relevant sources, records
exact versions and test results, and cleans up both providers. It strips any
externally configured Temporal address and refuses direct execution outside the
lab.

The integration test admits commands for two tenants, runs a real Temporal worker
with a test-only `CrawlSite`, and confirms both deliveries. It then injects a
database acknowledgement failure after durable start, derives the remaining lease
time from the PostgreSQL clock, retries after expiry, and verifies one workflow,
one first run, three command events, attempt two delivery, and coherent
`processing`/`running` state.

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

In a clean checkout, all 443 non-database Python cases, 165 API cases, 372 real
PostgreSQL 17.11 cases, one standalone real Temporal case, and one joint real
PostgreSQL/Temporal consumer case pass. The five real Keycloak and seven real
OpenBao regression scenarios also pass. All 12 repository cases and documentation
checks across 81 Markdown files pass; Ruff lint and format checks cover 103 Python
files, and dependency consistency passes.

Reviewed source-hashed evidence is stored in
[0031-consumer.json](../evidence/0031-consumer.json) and
[0031-postgresql.json](../evidence/0031-postgresql.json). Both labs report completed
cleanup and `production_authority` false. The evidence files contain source-path
keys ending in `pkce_secrets.py` and `session_tokens.py`; their values are SHA-256
source hashes, not credentials.

## Explicit Limits

- The joint lab registers a test-only workflow. There is no production `CrawlSite`
  implementation, crawl activity, heartbeat, retry policy, cancellation contract,
  terminal projection, worker versioning, or replay history.
- The executable is supervisor-ready, not deployed or supervised by this
  repository. There is no image, process manifest, readiness/health endpoint,
  metrics exporter, alert backend, or qualified shutdown grace period.
- PostgreSQL and Temporal development providers are disposable loopback test
  dependencies, not a production topology or restore qualification.
- Rejected messages have bounded retry delay but no terminal dead-letter policy or
  operator repair interface. Unknown outcomes are visible only in process output
  and durable leases; alert storage and dashboards remain future work.
- No model, crawler, approval, GSC, GitHub, Telegram, CMS, undo, dashboard, or
  production authority is added.

See [ADR-0031](../adr/0031-ack-after-durable-workflow-start.md) for the ordering,
ambiguity, shutdown, receipt, and deployment decisions.
