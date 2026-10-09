# Slice 0138: Faster, Steadier Verification

## Classification and Scope

Tooling tier (H2), as requested by the owner brief. No product behavior,
authorization, egress policy, provider contract, migration, dependency, or
production capability changes. Base: `7754746`. Revision 3.2 sections 30.2 and
30.5 govern real-database isolation and flaky-test repair; Revision 4.0 section
19's safety classification is unchanged. Existing disposable lab helpers are
reused rather than introducing a parallel test framework. No ADR is necessary
for this bounded tooling change.

## Design

The runner, shard coordinator, existing lab entrypoints/runtime paths and
Business Brain admission fixture are implemented and locally qualified.

- Database collection is sorted by full pytest node identity and assigned by
  index modulo N. N defaults to three and is bounded to 1..6. Each subprocess
  provisions its own pinned PostgreSQL, non-owner roles and twice-applied
  Alembic head. Collection manifests must agree and every main-suite case must
  occur exactly once; missing shards, reports, cases or source changes fail.
- JUnit suites are combined without dropping errors, failures, skipped cases,
  or their diagnostics. Standalone results remain under
  `.runtime/database-tests/latest.xml` and `latest.json`.
- Business Brain's source/extraction tests reset only synthetic OpenAI/Jev
  admission state before each relevant test. They no longer sleep to outlast
  earlier tests' wall-clock cooldown. Endpoint screening and all existing
  extraction, injection, owner-review, isolation and replay assertions remain.
  Within-test throttling and the production pre-dispatch retry policy are intact.
- The full runner discovers all lab entrypoints, uses bounded step/Docker
  concurrency, keeps conflicting fixed-subnet labs sequential, and runs the
  existing delivery partitions independently. Artifacts, pytest temporary data
  and SDK downloads have per-step roots. General temporary data is private and
  outside the repository, preserving recovery-material path safety checks.
  Only exclusively created work and temporary directories are eligible for
  deletion; shared/old runtime paths are untouched.
- Every step has a timeout and explicit counts/exit/cleanup status. The runner
  only signals process groups it started. Lab `finally` blocks retain their
  existing exact-label cleanup. Forced termination is explicitly unconfirmed;
  logs and compact reports are retained, and the gate fails closed.
- Gitleaks uses `git show main:.gitleaks.toml`; no allowlist change. A given
  commit range and a separate staged scan cover committed and pending changes.

## Commands and Evidence

```sh
.venv/bin/python -m pytest tests/tooling/test_fast_gate.py tests/tooling/test_database_shards.py -q
.venv/bin/python scripts/run-database-tests.py --shards 3 --repeat-brain 5
.venv/bin/python scripts/run-full-gate.py --range main..HEAD
```

Focused tooling verification: **49 passed, zero failed**, including actual
three-way pytest collection and exact-once assignment of the complete database
suite. Five consecutive real-PostgreSQL Business Brain extraction-file runs
passed all 14 cases each; the expanded file with the origin-scoped reset
regression passed all 15 cases. An actual CLI disk-guard run with a 64-GiB
threshold exited one, started no steps, and recorded all 20 as `NOT_EXECUTED`.
The real three-instance database lab passed **963 exact-once suite cases plus
20 additional extraction/replay cases (983 total)** in **268.319 seconds**;
all three workers exited zero and invocation-owned cleanup completed.
The complete API/identity/tooling/connector set passed **1,941 cases** through
the runner's repaired temporary-directory isolation. Ruff
lint/format (484 files), pip dependency consistency, and npm (43 repository and
200 dashboard cases, documentation hashes/links, typecheck and build) passed.

The serial baseline stopped after **1,696.236 seconds (28:16)**: eight steps
passed, then the consumer lab refused admission because free disk dropped below
the existing three-GiB PostgreSQL guard. Earlier checks passed: npm, 1,892
API/identity/tooling/connector cases, Ruff lint/format, pip, authority journal,
40 delivery cases and 11 candidate-sandbox cases. Delivery alone took
1,570.438 seconds (26:10 including setup/cleanup).

This is an infrastructure block under the owner's stop rule. The baseline's
invocation-owned bulk artifacts were removed; its logs and summary remain in
`.runtime/0138-before/`. Free space then fell further to 691 MiB despite cleanup.
No invocation-owned database/OpenBao container remained; other tracks' resources
were not stopped. No commit or PR was made for this incomplete slice.

The owner restored approximately 33 GiB of free disk and restarted Docker.
Files were checked for truncation before resuming. The complete serial baseline
ran against an immutable archive of main and passed **19 steps, zero failed**
in **2,736.128 seconds (45:36)**. Its 962 database cases took 613.775 seconds
(10:14), and 40 delivery cases took 1,580.705 seconds (26:21). All remaining
labs, static checks, boundary checks and scanner passed. Compact baseline
reports remain under `.runtime/0138-before-complete/reports/`; the invocation's
archive and bulk artifacts were removed after completion.

The first bounded attempt exposed a runner defect: repository-local pytest
temporary data correctly failed the existing recovery-material safety checks
(1,918 passed, 12 failed, nine errors). The invocation was cancelled; all four
owned database containers/networks were independently confirmed absent, and
artifact cleanup completed. Private temporary roots now live outside the
repository, with new positive and fail-closed regression checks. The repaired
pytest step passed all 1,941 cases. No recovery assertion or policy was changed.

The restarted bounded gate passed **20 steps, zero failures or skipped cases**:
**3,308 test cases plus 20 checks** in **1,189.925 seconds (19:50)**. This is
56.5% less elapsed time than the complete serial baseline, with 70 additional
test cases (49 tooling regressions, one database regression, 20 extraction
repetitions). Database execution inside the gate took 263.477 seconds (4:23).
Both delivery partitions passed 20 cases each. Defaults were four steps, two
Docker labs and three database shards; the disk threshold stayed four GiB.

The retained machine summary is
`.runtime/full-gate/run-ttcabcfs/summary.json`. All 20 steps reported completed
process/artifact cleanup; its bulk work directory is empty. Independently checked
all 13 explicitly logged disposable project names against Docker containers and
networks: zero remained. Process creation times were checked; other tracks'
resources were left alone. The retained gate logs/reports occupy about 2.1 MiB.
The [sanitized evidence](../evidence/0138-fast-gate.json) records counts and timings.

These are wall-clock observations on a shared macOS/Docker Desktop machine,
not a controlled benchmark: other tracks were active and caches were warm.
Allow approximately 20-30 minutes on a comparable setup. Existing lab guards,
provider policies, assertions and protected specification bytes are unchanged.

The baseline command sequence, in the immutable main archive, was `npm test`,
API/identity/tooling/connector pytest, Ruff lint/format over the full Python tree,
`pip check`, sorted `scripts/run-*-tests.py` entrypoints without shard arguments,
OpenBao, Keycloak, GSC boundary and gitleaks. The new gate's exact commands are
retained in its machine summary and normalized in the evidence record. The
precommit `main..HEAD` range is empty; a separate staged scan is required, and
the actual committed range is scanned again before publication using main's
configuration. No scanner allowlist is modified.

The staged scan passed with zero findings:

```sh
gitleaks git --pre-commit --staged --config=.runtime/full-gate/run-ttcabcfs/main-gitleaks.toml --redact .
```

After final documentation/evidence edits, `npm test` passed again (43 repository,
200 dashboard cases, 265 Markdown checks, typecheck/build). Ruff lint and format
checks passed again over all 484 Python files. The committed-range scan result
is reported with the published PR, not claimed in this precommit evidence.

## Limits

All data and provider responses are synthetic behind the existing shared egress
boundary. Live GSC/GitHub/Bing/Slack/Telegram/Jev/model/SMTP calls, deployed
workflows, and production release admission are `NOT_EXECUTED`. No owner input
or provider credential is needed for this local gate. A first live run still
requires the owning capability's dedicated environment, explicit resource scope,
credentials and separate qualification; this slice grants none of them.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
No migration; inherited head `0079`; 154 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2283 API/identity/tooling/connector
cases, 1047 PostgreSQL cases (including the runner's repeated Brain checks),
46 repository and 227 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.

### Absolute Lab Entrypoints

The final train run exposed a wrapper integration defect before the inherited
browser-worker lab could start: `runpy` preserved a relative `__file__`, while the
lab correctly requires repository-relative source hashes from absolute paths.
Normalize only the entrypoint to an absolute path before invoking it. Arguments,
exit status, graceful cancellation, source checks and all safety limits stay
unchanged. Two subprocess regressions cover relative and absolute entrypoints,
argument forwarding and nonzero exit status; the relative case failed before the
fix. The runner/sharding regression suites now pass 58 cases, with Ruff clean.

The original full-gate receipt is retained at
`.runtime/full-gate/run-758p8a7f/summary.json` (22 steps passed; browser did not
start). Restacking this tooling-only fix does not change any product, migration or
provider lab source. Reuse unchanged passing labs and rerun browser plus affected
static checks at the final tip; retain both receipts rather than hiding the failure.

### Train 2 Lab Integration Correction

The runner was reviewed on Train 1, before browser-worker, self-host and Webflow
landed. Those three entrypoints still used shared `.runtime` paths, so the new
runner could neither retain their exact JUnit counts nor remove their owned
artifacts. They now honor the existing opt-in private runtime and their reports
are explicitly registered. Standalone paths, tests, cleanup authority, subprocess
timeouts and safety limits are unchanged. A regression test covers the browser
runtime and all three exact report registrations. Their real labs run at the final
tip; this tooling-only correction adds no product authority.
