# ADR-0151: Evidence-Only Keyword Topics

Status: Accepted for local implementation; live qualification NOT_EXECUTED.

## Context

Slice 0141 needs useful topic research without purchased keyword data, including
Telugu and English. Queries/model output are data, never instructions or authority.
0077 strategy, 0135 registry, 0136 model budget and 0137 learning already supply
the relevant boundaries; no second planner, authorization or delivery flow is needed.

## Decision

Normalize Unicode NFKC/casefold and tokenize letters/numbers with attached marks.
Keep Telugu vowel signs/virama; remove join controls without splitting words.
No translation, stemming, language-specific stopwords or embeddings are required.
Sorted complete-link groups require two shared terms with Jaccard >=0.5 or a
shared bigram. Explain anchor overlap for each member; admission checks every
member, preventing bridge merging. IDs depend on version/source/dimensions and
normalized membership, not import IDs, row order or metrics. Changed membership
may change IDs. Choose one returned cohort per source, newest timestamp when
present then newest window, page preference, fewer dimensions and ID tie-break.
Never add overlapping imports. Position is impression-weighted and nullable.
Ranking pages require a page dimension. Title overlap in the successful crawl
is only a lexical targeting heuristic; absent observations cannot prove site-wide
content absence. Gap thresholds are diagnosis signals, not traffic predictions.

Optional owner-requested expansion reuses CSRF/exact owner scope and the existing
tool-less shared-egress model adapter. Add only `topic_ideas`, GPT-6 Luna/medium,
to 0136 routing and monthly budget admission. Stable existing operation derivation
prevents same-snapshot redispatch; unknown outcomes retain holds. Closed output
allows text tied to supplied cluster IDs, never metrics, labels or actions. The
server always labels it `idea, no volume data`. Completed budget output digests
bind immutable records. Changed query evidence makes old ideas unavailable.

DataForSEO volumes are separate completed receipts from the currently enabled
credential generation, labelled with language/location/date/evidence. No paid
lookup, new secret path, egress profile or SERP scraping is added. Missing values
remain unavailable/null. Topics feed existing content candidates and grounded
unaccepted brief payloads. 0135 `strategy_rebuild` and capped `brief_proposals`
consume them under existing grants. Owner decisions/acceptance, learning and all
delivery recipes are unchanged. Storage is immutable, forced-RLS, function-only;
worker projections retain the same exact stage/handle restrictions.

## Consequences

The deterministic path works without models/paid data. Lexical clusters are
conservative, not semantic-equivalence claims. Existing Bing daily/top-page data
have no query dimensions: topics are unavailable until real query data exists.
No brief acceptance, article drafting, candidate approval, publishing, merge or
deployment authority is gained. See [0141 implementation](../implementation/0141-keyword-topics.md).
