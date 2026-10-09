# ADR-0131: Owner-Reviewed Grounded Structured Data

- Status: Accepted
- Date: 2026-10-03
- Owners: Signal candidate boundary
- Related: [ADR-0069](0069-seal-one-evidence-bound-recipe-patch.md), [slice 0131](../implementation/0131-structured-data-and-visibility-schedule.md)

## Context

The reviewed `technical_structured_data` release repairs one invalid block as
WebPage. Changing that manifest would silently widen existing authority. Broader
structured data can assert business claims, so page markup alone cannot authorize
Organization or Product values.

## Decision

Introduce the distinct `structured_data_grounded` key and separately signed,
reviewed release manifest. Its approval class is `owner_review`. It is not in
0104's unchanged five-key A2 set and cannot exercise standing authorization.
All Organization and Product candidates additionally carry `owner_required`.

Use a closed deterministic builder for these schema.org subsets:

- [FAQPage](https://schema.org/FAQPage): one to eight Question/acceptedAnswer
  pairs, each question and answer matching a whole visible page text node
  verbatim. Hidden, paraphrased and substring matches are rejected.
- [Article](https://schema.org/Article) and
  [BlogPosting](https://schema.org/BlogPosting): headline exactly equals title or
  H1; optional ISO dates must occur verbatim in visible page text.
- [Organization](https://schema.org/Organization) and
  [Product](https://schema.org/Product): name and optional description/URL only
  from current approved Business Brain fact references. No implicit business
  value, offers, price, rating, SKU, brand or unsupported field is emitted.
- [BreadcrumbList](https://schema.org/BreadcrumbList): one to eight ordered
  ListItems whose labels and same-site URLs match visible page anchors.

Every emitted URL must be on the exact verified HTTPS origin. Unsupported
fields are omitted, not inferred. External CSS, inline styling, executable
scripts, ambiguous HTML or interactive visibility require render evidence and
are unavailable in this static subset.

One current committed crawl finding supplies page provenance. Its exact page
body digest must equal the selected static Eleventy HTML source. This release
adds or replaces exactly one JSON-LD script without reserializing the page.
Build both the unpatched baseline and candidate in the existing sandbox. The
target artifact must equal the exact before/after bytes; every other artifact
must be identical. Sealing rechecks the distinct release, current facts,
owner/site authority, closed shape and both immutable build receipts. PR
reconstruction uses byte offsets, including Unicode. Live verification requires
the exact script bytes and full sealed page digest, not just semantic JSON.

## Consequences

Existing recipes and autonomy contracts do not change. No model can mint
authority or facts. A clean page without a current detector finding cannot yet
enter this service; it is not given a synthetic finding. Templated/CMS mapping
and rendered visibility remain unavailable. Schema validity is not a search
engine eligibility or ranking promise. Sealing alone creates no PR, merge,
deployment or default-branch write. Dedicated live build and provider
qualification remain NOT_EXECUTED.

## Verification

See the implementation and [evidence](../evidence/0131-structured-data-and-visibility-schedule.json)
for closed-shape grounding, ownership, negative URL/authority cases, real
PostgreSQL sealing and exact isolated built-output qualification.
