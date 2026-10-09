# ADR-0171: Migrator-owned SQL dispatch tables

Status: Accepted for security-critical slice 0164; local qualification recorded
in [the implementation record](../implementation/0164-sql-dispatch-tables.md).

## Context

Train 5 exposed lost Webflow profiles and a lost `github.pr.revoked` reference
exemption when independent slices restated shared SQL. The owner approved an
equivalent refactor, not new authority. Revision 4.0 sections 4 and 19 and Revision
3.2 sections 2, 7, 8, 10, 11 and 18 remain in force.

## Decision

Migration 0100 follows unchanged 0099. Global catalogs in `control` are owned by
`signal_migrator`; PUBLIC and every runtime role have no table access or write
grant. Read them only through fixed-search-path SECURITY DEFINER functions with
PUBLIC revoke-all and explicit function grants where runtime execution is needed.
There are no new tenant tables; all 183 app tables retain forced RLS and their
existing grants. No existing migration or protected specification is changed.

Use value rows for simple enums; use typed, parameterized predicate rows for
compound CHECK disjuncts. The latter retain the accepted SQL expression, including
NULL, JSON types, exact JSON equality, casts and CHECK's three-valued semantics.
Predicates are reviewed executable migration configuration, never provider or
tenant content. Only the migrator can supply SQL; caller data is bound separately
and restored through `jsonb_populate_record` of the catalog's schema-qualified
row type. This does not grant runtime SQL construction or catalog modification.
Unknown non-null values and unknown references fail closed.

The growing CHECKs become immediate INSERT/UPDATE trigger guards with the same
three-valued acceptance, SQLSTATE 23514 and diagnostic constraint names. Static
structural CHECKs remain unchanged. Migration validates existing rows before
installing each guard. Cross-table CHECK functions are deliberately not used:
PostgreSQL cannot guarantee catalog data loads before checked application data
during restore. Triggers are restored after table data. A real custom-format
dump/restore regression includes a future event row, then proves its guard still
rejects invalid facts after restore. See the
[PostgreSQL constraint guidance](https://www.postgresql.org/docs/17/ddl-constraints.html).

The inventory covers 19 repeatedly extended checks: owner/shared egress profiles;
the four outbox/tombstone authority checks; platform event and command event
shapes; audit event types; model-budget roles; weekly intent/result stages;
command statuses; candidate decision channels and GitHub delivery channels;
model run releases; draft authority names; candidate evidence exceptions; and
admission workload prefixes. Pure structural checks that were replaced once,
foreign keys and immutable lifecycle guards are not converted to configurable
authority. The independent journal schema has no growing kind CHECK.

Reference dispatch stores an event, qualified relation, and exact reference
predicate, or an explicit exemption row. Preserve the three existing exemptions
and the old NULL-event trigger skip. Never infer an exemption from a missing row.
The fixed trigger no longer contains a growing WHEN list.
Its reference reads remain **invoker-security**, preserving the original grants
and forced-RLS visibility, including identity-hash scope. Only the rule lookup is
SECURITY DEFINER. A SECURITY DEFINER reference query would change which identity
sessions and login attempts are visible; unchanged identity tests reject that
regression.

Owner and shared request rules contain profile, method, and exact URL or anchored
PostgreSQL regex. Preserve the original case sensitivity, anchors, quantifiers,
backreferences and NULL results. Shared profile rows additionally retain purpose,
origin, credential, size and other operation restrictions. Stateful WordPress
authority stays in a separate existing-state guard: an URL rule cannot bypass a
current binding, intent, independent journal receipt or dispatch receipt. Owner
Webflow/Pagespeed admission is similarly catalog-dispatched to its existing
authority/cap function. Preserve the historical NULL shared-profile route rather
than opportunistically changing its behavior.

Strategy sources run ordered, migrator-selected readers, combining the identical
base, learning, internal-link and topic/Bing packets. A null authorized base still
stops the packet. Owner and worker entry points retain their original grants,
stage/handle checks and read semantics; no owner session is borrowed by a worker.
Historical wrapper functions are removed after switching their callers.

## Future Slice Procedure

Every extension belongs in that slice's own new Alembic migration. No shared
constraint, trigger or dispatch function is dropped/redeclared. No runtime or
model may insert these rows. Use psycopg's `%%` escaping in migration SQL.

- Profile: insert its allowed value into the appropriate `sql_dispatch_values`
  contract (`owner_connector_egress_operations_profile_check` and/or
  `egress_operations_egress_profile_check`). Insert `owner_egress_profiles` plus
  exact method/URL rows into `owner_egress_request_rules`, or insert a
  `shared_egress_profiles` operation predicate plus `shared_egress_request_rules`.
  Configure robots GET and the POST exclusion pattern explicitly. The owner
  `admission_sql` parameters are hash, generation, site, profile, target URL,
  tenant, operation, kind and verified origin (`$1` through `$9`). Its default is
  true, not a new human grant. Stateful authority needs a reviewed guard, not
  broader URLs. Existing dedicated capability binders remain separate.
- Event: insert one typed disjunct per accepted shape into `sql_dispatch_shapes`
  under `platform_events_contract_check`, with `row_type='control.platform_events'`.
  Insert its exact reference relation/predicate in `platform_event_references`.
  The predicate compares `r` (reference row) with `e` (bound event row). A deliberate
  exemption has both relation and predicate NULL and requires security review.
  Add the corresponding producer/recovery logic in the owning slice; a catalog
  row alone does not create an event producer or publishing permission.
- Authority kind: insert one `(target_kind, restriction_kind,
  target_requires_restriction)` row into `authority_restriction_kinds`. Both
  primary outbox and tombstone checks consume it. The flag preserves the Docs
  target-check coupling; it is false for the other existing kinds. Journal
  signing, replay and producer qualification remain mandatory separate logic.
- Strategy source: create its bounded SECURITY DEFINER reader(s), owned by the
  migrator, fixed `pg_catalog` search path, revoke PUBLIC and no direct runtime
  grants; insert `(source_key, ordinal, owner_reader, worker_reader)` into
  `strategy_source_dispatch`. Readers return a JSON packet patch. The catalog
  dispatches both paths without a rename/wrap or definition-copy block. Shared
  implementations may serve both readers only after the shared permission
  contract is qualified; workers must still enforce stage/resource limits.
- Other enum/shape: insert a value under the existing contract key, or a typed
  disjunct with a unique rule key. Do not widen other domains that happen to use
  the same string. Re-run positive, denial, failure and equivalence tests.

## Alternatives And Consequences

Appending to live definitions avoids immediately losing other slices' additions
but retains text-rewrite coupling. Foreign keys alone cannot express exact event
JSON or method/URL semantics. Hand-rewriting all shapes into a new JSON rule
language risks changing NULL behavior and type checking. Trusted typed predicates
preserve those semantics at the cost of a small dynamic-query overhead; they are
reserved for compound shapes, not a tenant-configurable policy language.

The independent 0099 fixture compares accepted/rejected enum values, authority
cross-products, every owner route and method/anchor mutants, shared-profile
operation mutations, event shapes, reference branches/exemptions and strategy
packets on real PostgreSQL. New-row tests exercise future dispatch; a mid-migration
collision verifies transactional recovery. Existing provider/network labs remain
the proof of admission, RLS, credentials and external-write restrictions.

No live provider is needed for this equivalent SQL refactor. Live provider and
production composition remain NOT_EXECUTED; no merge, deploy, default-branch push,
delete, CI edit, repository-secret read or production write is enabled.
