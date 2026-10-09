# Crawl Workflow Runbook

Status: development and qualification procedure for Slice 0032. There is no
deployed crawl worker or production crawler.

Slice 0034 separately qualifies exact-origin URL admission and address-pinned HTTP
GET behavior. Follow the [crawler HTTP boundary runbook](crawler-http-boundary.md)
for that test. It remains disconnected from this workflow until durable scope,
frontier, robots, artifact, and resolver/egress dependencies exist.

## Current Boundary

`CrawlSiteWorkflow` orchestrates one injected crawl activity and one PostgreSQL
terminal-projection activity. The command consumer starts the workflow on
`signal.crawl.v1`, but the repository does not yet ship a process that registers
the production workflow and activities. Only the disposable Temporal and joint
provider labs do so, using synthetic execution without external network access.

Do not interpret a `CrawlManifestReference` as proof that an artifact exists. In
this slice it is a strict metadata contract used to qualify state transitions. A
future artifact service must persist and verify the referenced hash before a real
crawl can report success.

## State Interpretation

| Command | Workflow reference | Event | Meaning |
| --- | --- | --- | --- |
| `workflow_admitted` | `admitted`, sequence 2 | `command.workflow_admitted` | Durable intent exists; Temporal start not yet proven |
| `processing` | `running`, sequence 3 | `command.workflow_started` | Canonical first run is recorded |
| `succeeded` | `succeeded`, sequence 4 | `command.workflow_succeeded` | Bounded manifest metadata was atomically projected |
| `failed` | `failed`, sequence 4 | `command.workflow_failed` | Crawl activity closed with `crawl_activity_failed` |
| `cancelled` | `cancelled`, sequence 4 | `command.workflow_cancelled` | Cooperative cancellation closed with `crawl_cancelled` |

An outbox row may be delivered while the workflow has already advanced beyond
event three. Admission and start calls intentionally return their original stage
receipts during redelivery; they do not move a terminal projection backwards.

## Development Verification

Run contract and adapter tests:

```sh
.venv/bin/python -m pytest tests/tooling/test_crawl_workflow.py \
  tests/api/test_human_commands_http.py -q
```

Run the real Temporal retry, failure, cancellation, and replay lab:

```sh
.venv/bin/python scripts/run-temporal-tests.py
```

Run the real PostgreSQL migration, concurrency, rollback, privilege, and terminal
projection suite:

```sh
.venv/bin/python scripts/run-database-tests.py
```

Run the joint command-to-terminal flow with acknowledgement-loss recovery:

```sh
.venv/bin/python scripts/run-consumer-tests.py
```

All labs own disposable loopback providers, synthetic data, and cleanup. They
reject or remove production endpoint configuration and record
`production_authority: false`.

## Failure Diagnosis

1. Check the command's event sequence. Missing event three means terminal
   projection correctly remains unavailable while start recording is retried.
2. Check Temporal activity type and attempt count. `crawl_retryable` may run three
   times; the closed crawl errors do not retry.
3. Check for `terminal_projection_unavailable`. It is retryable because the
   consumer and workflow can race or PostgreSQL can be temporarily unavailable.
4. Treat `terminal_projection_conflict` as committed evidence disagreement. Do not
   overwrite event four or change the first-run ID; preserve the history for
   investigation.
5. Treat `terminal_projection_invalid` as an adapter/schema fault. Do not weaken
   validation or edit the terminal row to make the workflow close.
6. A workflow cancellation is not complete until the execution activity has
   acknowledged cancellation and event four is committed.

Exception text from executors and storage adapters is deliberately absent from
Temporal history and API output. Development diagnostics must use separately
access-controlled logs once a worker runtime exists; never put URLs, credentials,
page content, DSNs, or certificate material into workflow errors.

## Recovery Rules

- Repeat admission, start, and terminal operations with the same command,
  workflow, first-run, and result identities. Never mint a replacement workflow ID
  to escape an unknown outcome.
- Do not manually advance `app.commands`, `app.workflow_refs`, or
  `app.command_events`. Guards reject unreviewed progress mutation.
- Tenant suspension blocks new claims but does not erase an accepted running
  operation's terminal truth. Record or reconcile the outcome, then enforce the
  restriction for new authority.
- Different terminal evidence for an already terminal workflow is a conflict, not
  a retry. Preserve both the caller evidence and committed event for operator
  review outside the database.
- Re-run history replay against the exact workflow code before changing a deployed
  workflow definition. Worker build-ID rollout and rollback remain prerequisites
  for production deployment.

## Not Yet Operable

There is no production startup command, worker build ID, process supervisor,
readiness contract, alert, dashboard, artifact lookup, crawl URL, or customer
credential for this boundary. The next crawl-execution slice must not enable
network access until the Revision 3.2 section 11 scope, redirect, SSRF, resource,
normalization, and evidence-storage controls have real tests.
