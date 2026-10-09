# ADR-0093: Exact Standing Authority for Technical Delivery

Status: Accepted for the internal GitHub-first beta path.

## Context

Revision 4.0 section 6 permits eligible A2 pull requests under a human-granted
standing authorization. The existing recipe registry, weekly gate, owner Inbox,
independent intent journal, and GitHub operation are independently implemented.
An owner-reviewed recipe release is not, by itself, platform approval for autonomy.
Connecting those components must not manufacture an owner decision or convert a
model recommendation into write authority.

## Decision

An operation has exactly one authorization kind:

- `owner_inbox`: the existing immutable human approval of the exact revision hash,
  recorded through the shared dashboard/Slack exact-revision decision implementation.
  Its original channel remains in the operation, intent digest, execution
  receipt, PR body and Changes. A Slack callback is not a separate write authority.
- `standing_grant`: a separate immutable dispatch authorization binding the grant
  ID, grant-row version digest, recovery generation, gate decision, exact sealed
  revision, reviewed release and separate autonomy attestation, threshold,
  confidence, deterministic policy, and exact weekly reservation operation.

The platform release-manager role alone may attest autonomy eligibility. A closed
manifest check permits only title, meta description, image alt, structured-data,
and broken internal-link releases. Canonical tags, robots, indexing sitemaps,
redirects, shared templates, pricing, legal, and product claims cannot become
autonomy-eligible through a claimed A2 label or a recommendation. Candidates
requiring claim review remain in the owner Inbox, including the current bounded
natural-language drafts. Review and revocation of the original release remain
mandatory; existing `owner_review` releases and owner-only functions are unchanged.

The workflow role uses separate, resource-bound database ports, never a synthesized
human session. Current authority is checked before preparation and every new
external effect. The original PR path still journals before I/O, verifies exact
Git objects, fences workers, and reconciles ambiguous outcomes. Authorization
source IDs and the real granting/approving owner are visible in the PR and Changes.
An in-flight operation cannot switch authorization kinds, even if the owner later
approves the same revision. Its original journaled authority must still be current
for another write; only read-only reconciliation may continue otherwise.

Read-only reconciliation survives grant revocation and pause while retaining
current binding, origin proof, recovery generation, exact operation scope and
lease fencing. It reads the immutable execution receipt and cannot recreate a
missing or ambiguous effect. Customer merge and deployment remain external; an
opened PR, check, or merge is never reported as live delivery.

Unreserved cap-deferred revisions can be reconsidered in a later active cycle
without redrafting or changing their hash. Existing reservations or writes cannot
be rebound to another operation. Base and binding are independently inspected
before dispatch. A new candidate never substitutes for an exact deferred revision.

This supersedes ADR-0074's record-only limitation only for the configured internal
technical-delivery composition. Revision 4.0, release admission, signed delivery
certification, and all safety gates are unchanged.

## Alternatives

Fabricating an Inbox approval would erase the distinction between human review
and standing authority. Inferring eligibility from `owner_review` would widen old
releases without platform review. Giving the workflow role a human session or
owner mutation ports would enable authority minting. Retrying all GitHub failures
would turn uncertain effects into duplicate writes. Those alternatives are rejected.

## Consequences

Migration `0060`, after unchanged `0059`, adds immutable, forced-RLS dispatch and workload receipts and a
private worker directory. The owner functions remain intact; explicit worker-port
copies retain their existing checks and require review when those checks evolve.
The supported candidate subset remains one static Eleventy `index.html` change.
Unsupported formats, unconfigured models, uncertain outcomes and effect measurement
stay visible. Deployment and provider qualification remain owner-operated gates.
Content Writer candidates and editorial reviews remain a separate path and cannot
be consumed by the technical dispatcher. Channel integration does not alter Slack's
high-risk dashboard step-up or any standing-authorization exclusion.

## Verification

The [0104 implementation record](../implementation/0104-autonomy-delivery-integration.md)
and [evidence](../evidence/0104-autonomy-delivery-integration.json) record commands,
results, fault injection, replay, and live qualification exclusions.
