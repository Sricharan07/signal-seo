# ADR-0086: Content Models Cannot Clear Editorial Escalations

Status: accepted for slice 0082, 2026-09-29.

The record-only limitation is superseded solely for the fresh-MFA exact dashboard
delivery approval in [ADR-0108](0108-exact-owner-editorial-delivery.md). Legacy
reviews, deterministic escalation and the article-autonomy exclusion remain.

## Context

Revision 4.0 sections 6.2, 7.2, 13 and 14 require grounded articles, conservative
weekly limits and owner review for sensitive claims. Content source text, briefs
and brand voice are data, not authority. Article autonomy eligibility is outside
this slice and must not inherit the technical-recipe publishing path.

## Decision

Use current approved Business Brain facts at draft time and record their exact
ids in immutable input snapshots and sentence annotations. Deterministic review
recognizes only exact normalized approved statements as supported. Untagged,
unknown, stale, sensitive and competitor-naming sentences require the owner.
Typed Jev Noul review can add escalations, never remove them; uncertain or
unavailable Jev uses a separately recorded, labelled frontier fallback. A fallback
that says a claim is supported still cannot clear deterministic flags.

Reserve a combined new-article/refresh rolling-seven-day slot before provider
dispatch, under the existing owner scope lock. Default two, platform maximum five.
Failed or unknown dispatches retain their slot and cannot be blindly retried.
Brief acceptance, draft receipts, cap changes and exact candidate decisions append
history and audits behind forced RLS.

Only plain Eleventy HTML is initially rendered, in the selected content directory.
One new file or one selected-file refresh is built through the existing protected,
network-isolated candidate sandbox. Its canonical manifest seals the exact diff,
base, build receipt, grounding report and originality score. Unsupported formats,
ambiguous refresh regions and incomplete originality coverage remain unavailable.

Editorial Inbox approval records an exact-revision owner decision only. It grants
no publishing or autonomy eligibility and is not accepted by the technical PR
dispatcher. The existing API and identity database roles remain separate; no
existing function grant or egress profile is broadened.

## Consequences

- Product classification remains appropriate: existing sessions, secrets,
  artifact encryption, shared model/GitHub egress and sandbox authority are reused.
  There is no external repository write, new secret path or authority grant.
- Exact-match grounding and lexical overlap review deliberately over-escalate.
  Semantic paraphrase support is not treated as deterministically proven.
- Originality uses eight-word shingles and reordered content-word windows, not a
  universal plagiarism certificate. Missing or oversized site evidence fails closed.
- No bulk articles, page sets, mass redirects, merge, deploy, deletion or unpublish
  capability is introduced. Article autonomy is deferred to a later slice.

## Verification

See [0082](../implementation/0082-content-writer.md) and its gate evidence.
