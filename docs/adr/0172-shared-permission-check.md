# ADR-0172: One permission decision after distinct credential admission

Status: Accepted; locally slice-qualified on 2026-10-05. See the
[implementation record](../implementation/0165-shared-permission-check.md) and
[evidence](../evidence/0165-shared-permission-check.json).

Security-critical slice 0165, stacked migration 0101 after 0100. No historical
migration, protected specification, runtime grant or RLS policy changes.

Owner sessions and opaque worker handles are deliberately not interchangeable.
Admission still verifies the original identity, tenant, site, generation,
current membership/grant, stage, planned resource and mode. Read/reconciliation
admission retains the original historical-authority behavior and cannot dispatch.
The normalized context is an internal SQL composite, not a credential or grant.
Unused fields are NULL; it never fabricates fresh MFA or an owner session.

Private fixed-search-path SECURITY DEFINER cores implement each paired operation.
The inventory is 66 families and 147 existing entry points: 36 shared operation
bodies and 30 explicitly preserved domain variants.
Public functions keep the same names, arguments, defaults, result shapes and ACLs,
and supply a literal actor. No runtime role can execute a private core, choose an
actor, change the admission adapter or invoke the permission predicate directly.
Only the migrator owns the new type/functions; revoke PUBLIC and runtime access.

One pure permission predicate consumes the admitted outcome and required role.
Preserve both legacy `IS DISTINCT FROM` and ordinary comparison NULL semantics;
callers select the old mode with a fixed literal. Owner/workload roles represent
the admitted capability and are normalized against their respective required
role, not treated as interchangeable identities. Other admission checks stay
where they were. Credential rejection, SQL failure and unknown external outcomes
do not become successful authority.

Shared operation bodies replace the migration-time definition-copy mechanism.
Where old owner and worker domain behavior intentionally differs, preserve
explicit operation variants in one private core and share the permission decision.
These include worker stage/resource limits, assistant message-bound spending,
worker-only reservation handles, deterministic snapshot replay and historical
owner-only domain expansions. This is not an approval to align those differences
by widening worker authority or tightening an existing reconciliation path.

Future paired operations create one reviewed private core with thin literal-actor
entry points, retaining distinct admission and original ACLs. Do not use
`pg_get_functiondef` or migration-time text replacements. Extend the test matrix
and qualify denial/failure paths. A new actor/admission mode requires its own
security review; models cannot supply it.

Nine Python session/token wrappers use a factory in the existing `session_tokens`
module. Canonical validation and SHA-256 hashing remain unchanged. Preserve each
caller's exact exception class/message and the three old TypeError translations;
unexpected failures still propagate. Raw tokens are not persisted or logged.

Independent pre-0101 definitions/ACLs, real PostgreSQL equivalence and normalized
owner/worker divergence tests govern qualification. Existing provider, authority,
delivery and Ask Signal tests remain unchanged apart from migration-head shape.
Transactional collision/recovery and no-runtime-private-execution tests are
required. Full gate: DEFERRED_TO_MERGE_TRAIN by the owner. Live provider and
production composition: NOT_EXECUTED. No new external write or publishing grant.

Final checks: 1,486 PostgreSQL cases, 125 delivery cases, 19 authority-journal
checks, 26 Ask Signal cases, 3,213 fast Python and 409 npm cases pass. All 15 new
permission/ACL/divergence/recovery cases and 108 Python adapter cases pass; Ruff
and dependency consistency pass. No timeout or safety restriction was weakened.
