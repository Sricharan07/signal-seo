# ADR-0069: Seal One Evidence-Bound Recipe Patch

- Status: Accepted
- Date: 2026-09-29
- Owners: Signal repository candidate boundary
- Related: [ADR-0068](0068-isolate-candidate-builds-before-repository-writes.md), Revision 4.0 section 4, Revision 3.2 section 21

## Context

The 0068 audit can identify a defect on an observed page, but neither a finding
nor an owner-selected repository grants permission to edit arbitrary files.
The 0103 registry requires a signed, reviewed recipe release before execution.
The 0080 sandbox can build an allowlisted patch, but its receipt alone does not
identify the finding, the exact edit, or a recovery path.

## Decision

Accept only one committed finding from the current detector release, one
platform-reviewed signed recipe release, and one exact owner-selected base
commit. The initial implementation supports an Eleventy static root
`index.html` only when its bytes equal the crawl page body digest. Unknown or
templated formats, ambiguous markup, changed source, incomplete crawl output,
and claims unsupported by observed facts are unavailable rather than guessed.

Each recipe makes one narrow token edit. The existing protected-path preflight
runs before the credential-free candidate build. A passed immutable build
receipt, exact before/after fragment, source/result hashes, finding and release
identities, expected impact, and revert plan are sealed as one RFC 8785
revision under fresh owner/site authority. No repository write is admitted.
The next Inbox slice must show the exact revision before any later PR intent.

## Consequences

Only the root static HTML subset is available for this beta slice; Next.js,
Astro, dynamic Eleventy templates, and other content formats remain visibly
unavailable. Natural-language drafts use the bounded model boundary and a
conservative evidence-word check, then still require owner review. This check
does not replace approved-business-fact curation, which remains a later product
capability. A release can be revoked between preparation and sealing; the
database rechecks its current state and denies the seal. No standing
authorization, PR, merge, deploy, default-branch push, workflow edit, or
secret read follows from a sealed revision.

## Alternatives

Mapping arbitrary rendered pages back to source files would be a guess, so it
is rejected. Whole-document HTML reserialization would broaden the diff, so
the recipe edits one parser-located fragment instead. Storing only a build
receipt would leave the review surface unable to reproduce the exact patch.

## Verification

See [slice 0081](../implementation/0081-technical-seo-recipes.md) and its
[evidence record](../evidence/0081-technical-seo-recipes.json).
