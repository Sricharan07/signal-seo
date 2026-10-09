# Slice 0027: Deduplicated Workflow Admission

Status: **IMPLEMENTED AS AN INTERNAL ADMISSION CONTRACT; NO TEMPORAL CLIENT OR WORKFLOW RUN**.

## Outcome

Signal can now consume a delivered `command.accepted` envelope exactly once at
the business layer before a future Temporal call. The transaction creates a
durable inbox receipt, assigns one deterministic workflow identity, advances the
command progress projection, and appends its progress evidence together.

| Boundary | Implemented behavior |
| --- | --- |
| Input | Complete typed outbox envelope and fixed consumer release key |
| Verification | Exact tenant, site, outbox, event, command, type, schema, and payload match |
| Deduplication | Unique tenant/consumer/event inbox receipt retained for replay horizon |
| Workflow identity | `signal:CrawlSite:{tenant_id}:{command_id}` is stable across retries |
| Progress | `accepted -> workflow_admitted`, row version 1 to 2, append-only event number 2 |
| Lifecycle | New work requires locked active directory, tenant, and site state |
| Duplicate | Original receipt remains acknowledgeable after lifecycle suspension |
| Privilege | Separate workflow role can execute one function and has no table access |

## Database Changes

Migration `0016` adds tenant/site-scoped `app.consumer_inbox` and
`app.workflow_refs` tables under forced RLS. The inbox key is unique across
tenant, `workflow.command-start.v1`, and source event. The workflow reference is
unique per command and has a deterministic tenant-qualified ID, fixed `CrawlSite`
type, `admitted` projection, event sequence 2, and null `first_run_id`.

`control.admit_command_event` derives site scope only after finding the exact
outbox identity under tenant scope. It returns an existing receipt before live
lifecycle checks, but a first admission takes shared locks on the tenant
directory, tenant, and site before locking the command and writing progress. The
source inbox row, workflow reference, command update, and progress event share one
transaction and roll back together.

Provisioning adds the non-owner, no-login `signal_workflow` role with only
`control` schema usage. The admission function is executable by that role alone;
the scheduler publisher cannot invoke it and neither role can directly read or
mutate the inbox or workflow projection.

The command table now separates immutable intent from mutable progress. Its guard
allows only the one transition introduced by this migration and rejects identity,
payload, actor, timestamp, result, deletion, skipped-version, and reverse-state
changes. Command-event constraints now bind accepted evidence to event number 1
and admitted evidence to event number 2 with exact workflow facts.

The existing authenticated command read function returns the optional workflow
projection. The acceptance function was tightened so an idempotent POST retry
continues to return its original `accepted` receipt even after current progress
has advanced.

## Service And API Contracts

`admit_command_event` accepts only a validated `OutboxEnvelope`, canonicalizes its
versioned payload, invokes the narrow database function, and validates every
returned identity and timestamp. `WorkflowAdmissionRejected` intentionally does
not disclose whether an envelope or current scope was unavailable.

The authenticated command status response now permits `workflow_admitted` with a
complete workflow ID, type, state, and projection time. Accepted responses omit
all four fields. Pydantic cross-field validation rejects partial progress and a
workflow ID belonging to another command. The route still repeats live session,
recovery, membership, site, and actor authorization before returning either state.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

All 350 real PostgreSQL 17.11 cases pass, 18 more than Slice 0026. The 161 API and
326 non-database Python cases pass, including three new progress-contract cases.
All 11 repository cases pass, documentation checks validate 68 Markdown files,
Ruff validates and format-checks 79 Python files, and the pinned Python
environment has no broken requirements. Reviewed source-hashed evidence is
[0027-postgresql.json](../evidence/0027-postgresql.json); cleanup completed and
`production_authority` is false.

The evidence file has the same two known generic-key secret-scan false positives
as prior database evidence: source-path keys ending in `pkce_secrets.py` and
`session_tokens.py` whose values are SHA-256 source hashes, not credentials.

## Explicit Limits

- No publisher process, broker, consumer daemon, Temporal client, Temporal server,
  worker deployment, or supervision loop is implemented.
- `workflow_admitted` is not `running`: `first_run_id` remains null and no workflow
  history exists.
- No crawl, analytics import, competitor research, plan, approval, CMS/GitHub
  operation, verification, undo, dashboard, or Telegram connector is implemented.
- Only `command.accepted` version 1 and the fixed workflow-start consumer are
  admitted; this is not a generic event bus.
- Workflow start recording, terminal progress, reconciliation, retention cleanup,
  metrics, traces, alerts, and replay administration remain future work.
- No production credential, customer enablement, external effect, or release
  authority is added.

See [ADR-0027](../adr/0027-deduplicated-workflow-admission.md) for the transaction,
identity, lifecycle, and retry decisions.
