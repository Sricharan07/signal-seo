# ADR-0140: Standing-Grant Weekly Skill Ports

Status: Accepted for the local internal boundary.
Date: 2026-10-03.

## Decision

Admit a closed ordered set of existing read/extraction/proposal capabilities using
the recorded human standing grant, work type, reviewed release, recovery
generation, resource exclusions, epochs, pause, revocation, expiry and budget.
Opaque workload handles are not human sessions or approvals.

Copy only a closed reviewed list of local SQL implementations into additive
function-only ports; replace authority resolution and inject stage/resource
guards. Preserve owner ports. Pin sources/bindings, extraction version/ranges,
brief payloads and snapshot IDs to immutable forced-RLS intents. The private
directory binds tenant/site/generation. The facade is an additional guard, not
the database security boundary. Recheck authority before credential/artifact/HTTP
I/O; reuse shared egress without widening profiles. Do not copy approvals,
acceptance, publishing, grant/binding mutation or write preparation. 0104 is unchanged.

## Alternatives

Fabricated sessions conflate standing authority with owner approval. Broad worker
grants or generic RPC unnecessarily widen authority. Reimplementing provider and
grounding logic duplicates qualified behavior. None is selected.

## Consequences

Skills run only inside human-granted A0/A1 authority. Unconfigured stages remain
unavailable and failures cannot block independent work. Later registrations must
qualify their own ports. Closed-list changes require PostgreSQL, API, identity
and Temporal negatives. No live-provider or production readiness is implied.

Evidence: [0135](../implementation/0135-weekly-orchestration.md) and
[checks](../evidence/0135-weekly-orchestration.json).
