# Crawl Run And Frontier Runbook

This runbook covers Slice 0035's durable crawl admission state. It is a local
qualification and diagnosis guide, not a production crawler startup procedure.
Robots, artifact, and global-origin boundaries now exist separately, but no public
resolver, egress authority, live page-attempt composition, or workflow executor
registration exists.

## Local Qualification

Prerequisites:

- Docker Engine is responsive;
- at least 3 GiB is free;
- Node 22 and the locked Python 3.12 environment are installed; and
- the source tree remains unchanged during the run.

Run from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
```

The command starts a digest-pinned PostgreSQL 17.11 container with tmpfs storage
and one loopback-only ephemeral host port, provisions random credentials for
non-owner roles, migrates twice, runs every control-plane case, writes sanitized
evidence, and removes only its invocation-labeled resources.

## Expected Evidence

`.runtime/database-tests/latest.json` must show:

- PostgreSQL `17.11` and the pinned image identity;
- exactly 411 passing cases with no skip or failure;
- a source hash for every database, control-plane, and control-plane-test input;
- `production_authority` false; and
- cleanup `completed`.

The reviewed copy is `docs/evidence/0035-postgresql.json`. Never edit generated or
checked-in evidence to make hashes match; rerun the lab against a stable source.

## State Interpretation

| State or outcome | Meaning | First safe action |
| --- | --- | --- |
| Run `running` | Immutable crawl admission exists for the exact running workflow | Inspect workflow identity and snapshot versions; do not infer that a fetch occurred |
| Frontier `pending` | URL is admitted but has no attempt yet | Check not-before time, run duration, current authority, and future robots/origin gates |
| Frontier `leased` | One worker owns the current short attempt identity | Retry only with the exact worker and lease UUID; do not mint a replacement before expiry |
| Frontier `permanently_failed` | Its immutable attempt ceiling was exhausted by expired leases | Preserve the row for future coverage; do not reset the counter manually |
| Run unavailable | Workflow is not the exact running first execution, duration ended, or scope is unavailable | Inspect durable workflow and authority records; never broaden a function grant |
| Candidate rejected | Origin, provenance, depth, URL count, duration, or input contract failed | Correct the source data or retain explicit truncation; do not bypass the database function |
| Run conflict | The command is already bound to different immutable configuration | Treat as a release/configuration fault; never overwrite the accepted snapshot |

## Runtime Invariants

- Open with the exact command, workflow ID, first execution run ID, scope version,
  policy version, seed, and immutable snapshots.
- Use the deterministic run/frontier handle returned by `open_crawl_run`; never
  construct a substitute from browser, model, Telegram, or website content.
- Enqueue only normalized URLs discovered from an existing URL in the same run.
- Treat exact retries as convergence, not permission to alter priority, provenance,
  policy, or budgets.
- Use a fresh random lease UUID for new work and repeat the exact UUID only while
  recovering an ambiguous claim acknowledgement. A consumed or expired UUID is
  retained in bounded history and cannot acquire different work later.
- Do not fetch from a frontier lease alone. A current exact robots snapshot, one
  exact global-origin permit, controlled resolution, and pinned-peer checks remain
  mandatory. Slice 0039 composes these for one attempt, but does not settle this
  frontier or its run byte budget.
- Never update run, URL, frontier, lease, attempt, or terminal columns directly.

## Failure And Cleanup

The database runner rejects low disk, an unresponsive daemon, source changes, an
unexpected PostgreSQL version, skipped/failed cases, or unconfirmed cleanup. It
prints the invocation name when diagnosis is needed. Inspect and remove only the
resource with that exact name and `dev.signal.database-lab` label. Never run a
global prune to clean this lab.

Migration `0020` is forward-only. A failed migration transaction remains on
revision `0019`; do not run a destructive downgrade. A production rollout still
requires a reviewed backup, lock/size assessment, release pairing, and restoration
exercise.

## Not A Production Start Procedure

Do not wire `claim_crawl_frontier` directly to `PinnedHttpFetcher`; use the durable
page-attempt composition. Production use still requires controlled resolver/egress,
frontier/byte settlement, workflow activity composition, progress/final coverage,
monitoring, retention, and the remaining G-03/G-04 evidence.
