# ADR-0164: Owner AI Question Sets and Grounded Inbox Handoff

- Status: Accepted for local implementation
- Date: 2026-10-04
- Owners: Signal owner decisions and Candidate Inbox
- Related: [ADR-0131](0131-owner-reviewed-grounded-structured-data.md),
  [ADR-0132](0132-reserved-ai-visibility-reobservation.md),
  [slice 0154](../implementation/0154-ai-answers.md)

## Context

The baseline could derive and record questions only through internal crawl-worker
functions. No owner route produced a question version. Structured-data visibility
acknowledgement also had no producer for the separately reviewed grounded recipe.
Historical dashboard messages incorrectly said that the recipe and scheduler did
not exist. Internal implementations must not be confused with reachable owner paths.

## Decision

Add narrow function-only owner wrappers in migration 0094, after unchanged 0093 (merge train 5; written as 0090 after 0089).
Reuse the existing crawl metadata loader, deterministic question derivation and
immutable question recorder. A proposal is editable browser state, not authority.
Approval rechecks selected site, current owner, fresh MFA, recovery generation,
latest committed crawl and exact previous question version. An append-only receipt
binds the actor and exact request. Replays return the same version; a changed body
or stale baseline cannot create another version. Preserve unchanged crawl provenance;
added or edited questions become owner data, within the existing 25/10/512 bounds.
Neither question text nor crawl content becomes instructions or publishing authority.

For an accepted structured-data proposal, the owner chooses exact approved fact
references. The producer resolves the existing static repository extension, reviewed
grounded release and same-page current-detector finding. It invokes the existing
source/digest, baseline/candidate build and grounded recipe service, with a separate
final database wrapper that rechecks the accepted proposal digest, provenance,
fresh-MFA authority and current approved facts. The sealed manifest retains proposal
and selected fact references. The owner still decides the exact revision in the
Candidate Inbox. No PR, merge, deploy or default-branch write is part of this action.

The visibility handoff supports Article headlines, a single FAQ question/answer
pair, and Organization/Product name and optional description. Every business value
must be an exact current approved claim from that proposal; Article/FAQ additionally
retain the existing whole-node page grounding. HowTo has no reviewed grounded
recipe and stays unavailable. Broader types/fields are not inferred.

Readiness is a current projection, not an edit to an immutable record. Scheduled
re-observation reports disabled/paused authority, cap reached, absent worker or
enabled state from the real settings/hold projection and configured runtime. A
saved enabled setting is not proof of a running worker. Missing build, facts,
provenance or reviewed release remains visibly unavailable. No slice identifiers
appear in the new user-facing reasons.

## Alternatives

Exposing tenant-addressed crawl-worker functions to the API was rejected because
those functions do not resolve owner identity. Model-generated target questions
or JSON-LD were unnecessary and would introduce unsupported facts and instruction
handling. Weekly-loop changes were rejected because this is an explicit owner
path, and those modules belong to another slice. Reusing the original invalid-block
repair recipe would silently enlarge an existing release and was rejected.

## Consequences

One new immutable forced-RLS receipt table, no new dependency, provider boundary,
external write authority or autonomous recipe. Existing migrations and accepted
specifications remain unchanged. Owner approval remains distinct from observation,
candidate sealing and delivery. A supported static source, current finding, current
facts, reviewed release and configured private candidate worker are required.
Unconfigured deployments remain unavailable rather than simulating readiness.

## Verification

Positive, negative and failure coverage spans owner HTTP/CSRF ingress, closed BFF
contracts, edit bounds, exact provenance, immutable/idempotent PostgreSQL approval,
schedule consumption, MFA/site/recovery refusal, approved-fact recipe inputs,
database sealing into a pending Inbox item, build failure and real isolated output
checks. Commands and local/live limits are recorded in the
[implementation record](../implementation/0154-ai-answers.md) and
[evidence](../evidence/0154-ai-answers.json). Live providers and deployment remain
NOT_EXECUTED; this decision grants no release or production-write authority.
