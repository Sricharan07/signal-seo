# ADR-0155: Exact Next.js Metadata Delivery

Status: accepted for internal qualification; live builds NOT_EXECUTED.
Slice: 0143. Classification: security-critical.

## Context

Next.js exposes [static App Router metadata](https://nextjs.org/docs/app/api-reference/functions/generate-metadata)
and [Pages Router Head elements](https://nextjs.org/docs/pages/api-reference/components/head).
Changing a route layout can affect several built pages. Source instructions,
functions and imported data cannot establish publication authority or exact reach.

## Decision

Use the existing pinned tree-sitter JavaScript/TSX parsers, without evaluating
source. Admit existing title/description string literals in one exported constant
static metadata object, or direct literal title/description children of one
unshadowed default import from `next/head`. Replace precisely that literal span,
retain its quote style, reparse the result, and preserve all other source bytes.
Literal JSX title text, including entities, is also supported. No field insertion,
whole-object serialization, configuration, workflow or protected-path edits.

Functions (`generateMetadata`), template literals, imported constants, metadata
mutations, spreads, non-static sibling metadata and ambiguous mappings are
unavailable. This slice does not claim bounded support for those forms. Editable
routes use `.tsx`/`.jsx` under `app`, `pages`, `src/app` or `src/pages`; route
groups, parallel routes, APIs and special Pages files remain unavailable.
LF-only, valid UTF-8 source is bounded to 128 KiB; spans are bounded to 4 KiB.

Ordinary single-page edits are A2. Measured multi-page edits, every layout edit,
and dynamic App routes are A4. A dynamic route needs a literal bounded array
returned by an argument-free `generateStaticParams`; that function is never
edited. Pages dynamic routes are unavailable. A segment layout must have exactly
one static descendant page and one measured affected page. Root layouts need a
bounded static source catalog and at most 128 total built HTML pages; paired
builds must measure every affected page. Unbounded layouts remain unavailable.

Reuse 0127/0139 exact all-HTML/artifact proofs, encrypted receipts, signed reviewed
releases and Owner Inbox approval. Both A2 and A4 require dashboard approval and
MFA within five minutes, including dispatch. Standing/weekly/Slack/autonomous
delivery is denied. 0125 unprotected-base acceptance remains separate and current.
No model or repository content can create authority.

## Consequences

Migration 0082 follows 0081 without new tables. F1 and F2 use one receipt validator
behind fixed-adapter wrappers; callers cannot invoke its adapter-taking core.
Existing immutable impact and MFA records remain the authority boundary. Strict
API/dashboard contracts expose the same receipt-backed counts and samples.
PR dispatch reconstructs the literal proof before the existing journaled,
ref-only write path. No default-branch write, merge, deploy or secrets access.

## Verification

Parser, injection, exact-span, reconstruction and impact negatives supplement
real PostgreSQL tenant/role/MFA/release/receipt/base checks and offline container
tests. See [0143](../implementation/0143-nextjs-metadata-delivery.md) and
[evidence](../evidence/0143-nextjs-metadata.json).
