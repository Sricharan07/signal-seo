# Slice 0164 - SQL Dispatch Tables

Classification: **Security-critical** (Revision 4.0 section 19). Owner-approved
track A consistency refactor based on main `a519a65`, branch
`slice/0164-sql-dispatch-tables`. Migration 0100 follows 0099; 183 app tables are
unchanged. [ADR-0171](../adr/0171-sql-dispatch-tables.md) defines the extension and
security contract.

## Implemented

Migrator-only global value, shape, reference, owner/shared egress and strategy
dispatch catalogs replace 19 repeatedly restated CHECKs, growing trigger
exemptions/reference branches, request-profile and robots-method lists, and
strategy/source wrapper chains. Exact method/URL matching, purpose/credential/
origin/byte limits, stateful provider authority and three-valued SQL semantics
are preserved. All old migrations and specifications remain untouched. Runtime
roles gain no catalog writes or direct reads; fixed-search-path SECURITY DEFINER
functions mediate reads. Existing tenant tables retain forced RLS and grants.

## Equivalence And Recovery

`tests/control_plane/fixtures/sql_dispatch_0099.json` captures the independent
accepted baseline. Tests enumerate its enums, restriction cross-products, event
JSON mutations and NULL cases, all owner route forms with method/anchor/host
mutations, shared-profile operations and refusals, reference branches and
exemptions, and identical strategy packets. Future migrator-inserted rows are
exercised without recreating shared functions/constraints. A collision partway
through migration 0100 leaves revision 0099 and its exact old function intact.
The 19 growing CHECKs are immediate row guards, preserving NULL acceptance and
SQLSTATE/constraint diagnostics. Catalog-reading CHECK functions are not restore
safe. A real dump/restore includes a future event and verifies rejection still
works after restore; static structural CHECKs, foreign keys and RLS stay intact.

Migration is one transaction. DDL briefly takes normal creation/function and
constraint-validation locks. It backfills only static configuration, never
customer records, secrets, evidence, grants or network outcomes. Existing rows
are validated against equivalent predicates. Apply away from other DDL. A failed
application rolls back fully; destructive downgrade remains disabled. Recover
with a reviewed forward migration, retaining immutable receipts and restrictions.

Existing test-shape changes, also listed in the PR body: the database-head and
head-backfill assertions advance from 0099 to 0100; the provider-composition test
now exercises accepted profiles and robots GET decisions instead of inspecting
the old SQL text. Its allowed profiles and expected GET behavior are unchanged.
No behavioral test is relaxed.

Additional old-shape assertions now exercise reference exemptions through a
temporary trigger table, registered weekly stages through the value helper, and
the unchanged ten candidate-evidence cases through the shape helper. Identity
tests stay unchanged. They caught a draft reference-query security-mode error;
reference reads now retain the original invoker/RLS scope, with only catalog
lookup using SECURITY DEFINER.

## Verification

Focused development qualification: 22 dispatch/equivalence cases pass on real
PostgreSQL 17.11; 34 initial shared-egress/provider behavioral cases passed before
updating the single old-shape assertion. The combined dispatch/provider rerun
passed 38 cases before the final reference/strategy and binder additions.
The first delivery run was deliberately interrupted on invocation-owned
processes to correct the draft's restore hazard before expensive qualification;
it is not counted as a passing lab. No test or restore restriction was disabled.
Ruff check and format check pass. Fast Python passes 3,105 tests and `npm test`
passes 409 tests. Both autonomy-delivery shards pass (63 + 62), Ask Signal passes
26 tests, and authority-journal passes 19 checks. Commands/results are recorded in
[the evidence](../evidence/0164-sql-dispatch-tables.json).

Final corrected-source qualification on 2026-10-05: 48 focused PostgreSQL cases
pass, including unchanged identity tests, all dispatch equivalence, restore and
rollback cases, and the converted shape assertions. The normal three-shard
database lab passes 1,471 cases (491 + 490 + 490), Webflow passes 60, and
provider-data passes 84. Crawler-network passed 14 before the reference-only
correction; its network code was unchanged. Earlier delivery, journal and Ask
Signal counts above remain scoped qualification history; the complete database
rerun covers their SQL at the corrected tip.

Earlier attempts were incomplete, not passing qualification: a missing semaphore
in the untracked helper, disk-floor stops, one-shard timeout, and Docker health
failure. The owner restored infrastructure and explicitly required the existing
three-shard runner. No timeout, safety assertion or disk floor was changed.
Only invocation-owned processes/resources were cleaned. Slice 0165 remains a
separate prototype until its own implementation and qualification.

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
npm test
.venv/bin/python scripts/run-database-tests.py --shards 3
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-autonomy-delivery-tests.py --shard 0
.venv/bin/python scripts/run-autonomy-delivery-tests.py --shard 1
.venv/bin/python scripts/run-webflow-tests.py
.venv/bin/python scripts/provider_data_lab.py
.venv/bin/python scripts/run-ask-signal-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
```

The owner's follow-up defers the full gate to the merged train:
**DEFERRED_TO_MERGE_TRAIN**. Labs run strictly sequentially with the normal
`run-database-tests.py --shards 3`. Before each lab, check `df`; below 4 GiB,
wait two minutes and retry for at most 30 minutes without lowering the guard.
Labs use only disposable private
resources and synthetic provider doubles behind shared egress. No foreign
container is stopped; invocation-owned cleanup is mandatory. No dependencies,
Python connector-framework changes, UI or other-track branch changes are added.

## Limits

Live provider qualification and deployed production composition are
**NOT_EXECUTED**. Existing optional capabilities stay unavailable without their
qualified composition. This refactor supplies neither provider credentials nor
new autonomous or publishing authority. New kinds still require their owning
slice's producers, replay, authority and provider qualification.
