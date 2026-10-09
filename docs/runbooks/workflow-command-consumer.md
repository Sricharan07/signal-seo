# Workflow Command Consumer Runbook

Status: **SOURCE AND LOCAL IMAGE QUALIFICATION ONLY**. The process has no
production deployment approval or deployable crawl worker.

## Purpose

The consumer claims accepted command events, admits one stable workflow, starts or
finds that workflow in Temporal, durably records its first run, and acknowledges
the exact outbox attempt. It now exposes private release-bound health for a local
container supervisor. Use this runbook for local qualification; do not point it at
customer data or grant it connector, GitHub, CMS, or deployment credentials.

## Preconditions

- Apply migrations through revision 0019 with the migrator role.
- Provision separate `signal_scheduler` and `signal_workflow` database credentials.
- Provision a Temporal namespace and a compatible worker on task queue
  `signal.crawl.v1`. This repository does not yet provide that production worker.
- Provide a supervisor termination grace period longer than the configured
  Temporal start timeout plus database completion time.
- Route stdout JSON lines to access-controlled log storage with retention and
  alerts. The repository does not yet provide that backend.

## Configuration

| Variable | Requirement |
| --- | --- |
| `SIGNAL_SCHEDULER_DSN_FILE` | Required outside loopback development; owner-only file containing the exact `signal_scheduler` DSN |
| `SIGNAL_WORKFLOW_DSN_FILE` | Required outside loopback development; owner-only file containing the exact `signal_workflow` DSN |
| `SIGNAL_SCHEDULER_DSN` | Literal-loopback development only; direct scheduler DSN |
| `SIGNAL_WORKFLOW_DSN` | Literal-loopback development only; direct workflow DSN |
| `SIGNAL_TEMPORAL_ADDRESS` | Required `host:port`; bounded and validated |
| `SIGNAL_TEMPORAL_NAMESPACE` | Required lowercase namespace token |
| `SIGNAL_WORKFLOW_CONSUMER_KEY` | Required lowercase worker token, at most 64 characters |
| `SIGNAL_WORKFLOW_CONSUMER_RELEASE` | Required immutable `sha256:<64 lowercase hex>` source-bundle identity |
| `SIGNAL_WORKFLOW_TENANT_PAGE_SIZE` | Optional; default 25, range 1 through 1,000 |
| `SIGNAL_WORKFLOW_LEASE_SECONDS` | Optional; default 45, range 1 through 300 |
| `SIGNAL_WORKFLOW_RETRY_SECONDS` | Optional; default 30, range 1 through 3,600 |
| `SIGNAL_WORKFLOW_IDLE_SECONDS` | Optional; default 1, range 0.05 through 60 |
| `SIGNAL_WORKFLOW_ACTIVE_SECONDS` | Optional; default 0.05, range 0.01 through 5 |
| `SIGNAL_TEMPORAL_START_TIMEOUT_SECONDS` | Optional; default 10, range 0.1 through 30 |
| `SIGNAL_WORKFLOW_CONNECT_TIMEOUT_SECONDS` | Optional; default 10, range 1 through 30 |
| `SIGNAL_WORKFLOW_HEALTH_PORT` | Optional; default 8081, unprivileged loopback port only |
| `SIGNAL_WORKFLOW_HEALTH_STALE_SECONDS` | Optional; default 15, range 5 through 300 and at least two idle cycles plus one second |
| `SIGNAL_TEMPORAL_ROOT_CA_FILE` | Optional absolute path to a nonempty bounded CA file |
| `SIGNAL_TEMPORAL_CLIENT_CERT_FILE` | Optional absolute client certificate path; pair with key |
| `SIGNAL_TEMPORAL_CLIENT_KEY_FILE` | Optional absolute private key path with mode no broader than owner |
| `SIGNAL_TEMPORAL_SERVER_NAME` | Optional validated TLS verification name |
| `SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK` | Development only; `1` permits literal loopback without TLS |

Outside explicit insecure loopback, direct DSN environment values are rejected;
both DSN files must be absolute, owner-only regular files, at most 4 KiB, and
opened without following symlinks. Both DSNs must specify `sslmode=verify-full`,
and Temporal TLS is enabled. Insecure mode rejects hostnames, remote IP addresses,
and TLS file settings. TLS files must be absolute regular files, not symlinks,
nonempty, and at most 1 MiB. The client certificate and key must be configured
together. The lease must be at least 20 seconds longer than the Temporal start
timeout. Health never binds beyond literal loopback.

## Start And Stop

From the repository root with dependencies installed:

```sh
PYTHONPATH=services/control_plane/src .venv/bin/python scripts/run-workflow-consumer.py
```

The process fails before work on missing or invalid configuration. A configuration
error exits 2; an unclassified startup/runtime failure exits 1 without rendering a
traceback or provider message. `SIGINT` and `SIGTERM` request cooperative shutdown.
The active envelope is drained, but no next tenant is claimed.

## Observe

Each stdout record is compact JSON with schema version, service, kind, closed
outcome, UTC observation time, and applicable UUIDs/attempt count. Expected healthy
progress is:

```text
claimed
workflow_admitted
temporal_start_confirmed
workflow_start_recorded
delivered
```

`consumer_started`, `consumer_draining`, and `consumer_stopped` bound a normal run.
`GET /health/live` remains live during drain; `GET /health/ready` opens only after
Temporal startup and one clean scheduler cycle, closes on dependency/ambiguity
failure or staleness, and closes before drain. No payload, DSN, certificate,
provider error, exception message, URL, user value, or token belongs in either
surface.

## Failure Actions

| Observation | Meaning | First safe action |
| --- | --- | --- |
| `tenant_scan_failed` or `claim_failed` | Scheduler database operation failed | Keep production writes disabled; verify role, TLS, migration, and database health |
| `workflow_admission_rejected` then `rescheduled` | Durable source or current scope was definitely unavailable | Inspect committed command/scope state; do not edit tables or force acknowledgement |
| `*_outcome_unknown` | A stage may have committed despite a lost result | Do not manually retry under a new identity; preserve the lease and inspect PostgreSQL plus Temporal |
| `ack_failed` or `ack_lease_lost` | Workflow may be recorded but this attempt was not acknowledged | Let the stable envelope retry; confirm a single workflow ID and first run |
| `reschedule_failed` or `reschedule_lease_lost` | Definite rejection was not scheduled by this fence | Inspect the current lease owner and database health; never override the fence |

There is no approved manual skip, table update, new workflow ID, or force-delivery
procedure. Prolonged unknown outcomes require the future reconciliation and alerting
slice. Pausing a tenant blocks new claims but does not erase already accepted work;
inspect in-flight Temporal and PostgreSQL evidence before shutdown or recovery.

## Qualification

Run the isolated joint test instead of manually wiring local providers:

```sh
.venv/bin/python scripts/run-consumer-tests.py
```

The lab owns and removes its providers and writes sanitized ignored evidence to
`.runtime/consumer-tests/latest.json`. A committed reviewed copy belongs in
`docs/evidence/`. Lab success does not grant production authority.

Qualify the package and external container mechanics separately:

```sh
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
```

The image lab uses no provider credentials or network and is not a substitute for
a signed, monitored private-pilot deployment. Follow the separate
[deployment runbook](workflow-consumer-deployment.md).
