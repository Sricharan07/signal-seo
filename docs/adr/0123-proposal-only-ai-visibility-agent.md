# ADR-0123: Keep AI-Visibility Optimization Proposal-Only

Status: Accepted. Date: 2026-10-03. Slice: 0096. Tier: Product.

## Context

Revision 4.0 section 15 adds optimization on the immutable 0075 question and
observation records. Main `4f3aca5` has no AI-visibility scheduler and 0081 only
repairs invalid single JSON-LD to WebPage. Neither is evidence of broader readiness.
The owner explicitly scoped 0096 to recommendations and existing 0082 brief flows;
the scheduler and structured-data recipe extension belong to security-critical 0131.

## Decision

Compute per-version question relevance using title/heading token overlap, with
stable URL/id tie-breaks and exact crawl references. Reparse only 0075's documented
provider citation fields from immutable 0074 responses. Keep newest observations
per provider separate from history; incomplete coverage is unknown, never zero.
Return other cited URLs, not competitor prose or speculative competitor identities.

Use fixed proposal rationale and exact current approved fact statements rather
than adding another model prompt or provider call. Apply 0082's claim grounding and
lexical originality checks to those statements against untrusted answer excerpts.
No model is used to draft 0096 recommendations; optional model rationale is not
necessary for this bounded implementation. Unsupported, sensitive and copied
claims are visibly flagged and cannot be accepted through this agent.

Content proposals create evidence-proposal briefs through the existing Content
Writer function, still proposed until an explicit owner decision. Acceptance calls
its existing accept-brief function atomically with the exact proposal decision.
No drafting, sealing, outbox dispatch, standing grant or publishing is triggered.

Structured-data recommendations name only FAQPage, HowTo, Organization, Product
or Article, with an exact page, crawl labels and approved facts. They are not
JSON-LD patches or candidates. The UI states
`unavailable: needs a structured-data recipe extension (slice 0131)`.
Internal-link recommendations may carry existing broken-link recipe inputs only
when a same-manifest not-found finding matches a crawled homepage link. The normal
0081 source/release/build and exact Inbox checks remain necessary; acknowledgment
does not seal a candidate or approve a revision. Other suggestions remain advice.

Re-observation states `no AI-visibility scheduler yet (slice 0131)`. There is also
no owner-triggered 0075 observation route to link to. Observation history uses
"Observed change" wording and denies causal inference and consumer-app equivalence.
Historical provider evidence is not advertised as current configured availability.

## Consequences

Migration 0078 follows unchanged 0062 and adds two immutable forced-RLS,
function-only tables. Current owner/session/site/recovery checks reuse the 0073
boundary and API-role composition from 0082. Exact payload digests, idempotent
preparation, single decisions and current approved facts bound all acceptance.
No new dependency, provider, credential, egress profile or write authority exists.
No existing recipe, release, migration or hash-protected specification is changed.

This is local product qualification, not R4 release admission. Live providers and
deployed composition remain NOT_EXECUTED. Bounded lexical originality is not
semantic plagiarism certification; no competitor-page fetch or copying pipeline
is added. Recommendation acknowledgments are internal records, never exact-revision
Inbox approval. Slice 0131 requires a separate brief and safety qualification.
