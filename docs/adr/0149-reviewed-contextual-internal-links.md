# ADR-0149: Reviewed Contextual Internal Links

Status: Accepted for the internal local boundary.
Date: 2026-10-04.

## Decision

Derive a deterministic, site-local graph from the latest committed crawl.
Count distinct observed referring pages, not link occurrences. Zero incoming
edges mean "no incoming links observed in this crawl", never a whole-site orphan
claim. One incoming referring page is weakly linked. Require two shared salient
title/heading terms; stable term and URL ordering makes evidence replayable.
Business Brain and model inference are not needed for this bounded recipe.

`technical_internal_link_add` is a separately signed, reviewed `owner_review`
recipe, never eligible for autonomy. The five-key 0104 A2 set is unchanged.
Only directly mapped Eleventy static HTML is supported. Preserve every source
byte except one anchor wrapper around two or three words already present in a
relevant plain-text main/article paragraph. Reject navigation, footer, hidden or
labelled boilerplate, repeated paragraphs, templates, nested markup, source drift,
unverified-origin targets, duplicates and reused exact anchors. Avoid reciprocal
links and reciprocal proposals within the selected batch.

Cap candidate additions at three per source URL per UTC week, optionally lowered
to one or two by the owner preparation port. Site-serialized immutable database
records prevent a new crawl or replay from resetting that cap. Rejected candidates
still consume it. Exact anchor repetition is conservatively refused site-wide,
including previous candidate history and anchors in the current checkout.

Baseline and candidate receipts must share the same base/tree and current
authority. The baseline output must equal committed crawl bytes. Only the source
page output may change, by the exact wrapper; every other artifact remains equal.
Existing owner Inbox approval, journaled PR dispatch, protected paths and live
verification remain authoritative. Dispatch reconstructs the paragraph proof;
live verification requires the complete sealed page digest, not merely a link.

## Consequences

No inserted keyword text, model calls, external writes, merge, deployment or
authority expansion is added by proposal preparation. Ambiguous source mapping,
unsupported adapters and builds remain unavailable. Astro/front-matter/Next.js
delivery, writing quality and site-local learning continue through their existing
modules, without an internal-link duplicate implementation.

Qualification and limits: [0140](../implementation/0140-internal-links.md).
