# Slice 0165 - Shared Permission Check

Classification: **Security-critical** (Revision 4.0 section 19). Owner-approved
track A consistency refactor stacked on slice 0164. Migration 0101 follows 0100;
183 forced-RLS app tables and existing runtime grants are unchanged.
[ADR-0172](../adr/0172-shared-permission-check.md) defines the admission/decision
boundary. No historical migration or protected specification is edited.

## Implementation

One private permission predicate replaces the owner/workload outcome and role
comparisons in 66 operation families. 147 existing entry points become thin,
literal-actor wrappers around fixed-search-path SECURITY DEFINER cores. Owner
credential helpers use the same predicate. The normalized internal context never
becomes a credential, a grant or fresh MFA. Runtime roles cannot execute the
predicate, admission adapter or cores, select an actor, or use the private type.
The migrator owns all new objects; original entry-point ACLs remain exact.

36 families share one operation body. 30 retain explicit legacy domain variants
while sharing the permission decision. Worker stage/resource restrictions,
assistant message-bound budget use, reservation handles, deterministic snapshot
replay and owner-only domain expansions are preserved rather than widened.
Internal Pagespeed planning shares the owner sampling body but retains its old
verified-origin admission, available only through existing private callers.
No migration-time function definition copying or replacement is used in 0101.

Opaque worker handles and owner sessions remain noninterchangeable. Existing
admission validates identity, tenant/site, generation, standing authorization,
stage and planned resource. Historical delivery reconciliation remains read-only
and keeps its old admission semantics, including revocation recovery. No model
can supply an actor, mint authority or bypass the deterministic gate.

Nine Python adapters now use a rejection factory in the existing `session_tokens`
module. Canonical 43-character base64url validation and SHA-256 remain unchanged.
Each domain keeps its exact exception class/message and exception chaining. Only
the original three adapters translate TypeError; other unexpected errors propagate.
No raw token is persisted or logged; no dependency or dormant service is added.

## Equivalence And Recovery

The independent `shared_permission_0100.json` fixture records all 147 entry-point
signatures/ACLs and 21 accepted read definitions across eight representative
families. Structural tests prevent a wrapper from acquiring its own permission
policy. Real PostgreSQL tests compare old and new results for admitted outcomes,
roles, NULLs and injected admission failures, then compare owner and worker
decisions for identical normalized inputs. A rollback-owned fixture substitutes
only credential/stage/resource admission; real admission is tested unchanged in
the database and delivery labs. A deliberate worker policy fork must fail the
divergence assertion. Private execution denials are exercised as runtime roles.
The pure predicate checks all 180 old outcome/role/NULL combinations.

The 108 Python adapter cases cover valid digests, every domain rejection, old
TypeError translations and unexpected failures. A mid-0101 collision proves
transactional rollback to 0100 with the exact old entry-point definition and no
partial context type. Existing 0164 extension/dump/restore tests run at 0101 too.
Failed migration recovery is a reviewed forward migration; destructive downgrade
remains disabled. Apply away from other DDL; immutable records are not rewritten.

The only existing test changes in this slice are eight migration-head assertions
in seven `test_database.py` functions, advancing 0100 to 0101. The PR body lists
each. No behavioral, RLS, grant, authority or egress assertion is weakened.

## Qualification

Final qualification passes: 3,213 fast Python cases (including 108 adapter cases),
409 npm cases, Ruff check/format and pip dependency consistency. The normal
three-shard PostgreSQL lab passes 1,486 cases, including all 15 new permission,
ACL, divergence, private-denial, no-separate-formula and recovery cases. Delivery
passes both complementary shards (63 + 62), authority-journal passes 19 checks,
and Ask Signal passes 26 cases. All final counts have zero failures/skips.
Fourteen focused permission cases had already passed before the additional
structural case. Earlier development corrections were a missing SQL statement
terminator and omitted resource-admission substitutes in the new rollback-owned
normalized fixture; these attempts are not counted as qualification passes.
No existing behavioral test, timeout or safety gate was changed to resolve them.
Final SQL cleanup removed trailing whitespace only: all 87,612 non-comment SQL
tokens and quoted literals match the broadly qualified source. The final
combined permission/0164 equivalence PostgreSQL rerun passes 37 cases. Staged
secret scanning uses unchanged main configuration and finds no leaks; the PR
records the final commit-range scan.
Labs run one at a time, using normal database sharding and unchanged timeouts.
Check `df` before each lab; below 4 GiB wait two minutes, bounded to 30 minutes.
Only invocation-owned disposable private resources may be cleaned.

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
npm test
.venv/bin/python scripts/run-database-tests.py --shards 3
.venv/bin/python scripts/run-autonomy-delivery-tests.py --shard 0
.venv/bin/python scripts/run-autonomy-delivery-tests.py --shard 1
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-ask-signal-tests.py
```

Evidence: [0165](../evidence/0165-shared-permission-check.json).
Full gate: **DEFERRED_TO_MERGE_TRAIN**, explicitly not executed at owner direction.
Live providers and deployed production composition: **NOT_EXECUTED**. Provider
doubles use only synthetic credentials behind existing shared egress. A first
live run still requires separately authorized private deployment, provider
configuration and owner-selected resources/approvals. No merge, deploy,
default-branch push, deletion, CI edit, secret read or production write is enabled.
