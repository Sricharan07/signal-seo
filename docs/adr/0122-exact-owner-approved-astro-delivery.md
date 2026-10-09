# ADR-0122: Exact Owner-Approved Astro Source And Built Scope

Status: Accepted for internal qualification, 2026-10-03.

## Context

Slices 0125 and 0126 provide the owner-accepted unprotected base and a closed
dependency/offline build boundary. Neither grants Astro delivery authority.
Generated date pages can share a dynamic route, layout and data generator; a
finding about one crawled URL does not make its source a per-page template.
The owner requires every Astro PR to receive exact owner approval and fresh MFA.

## Decision

Extend the existing sealed technical recipe, Inbox and journaled GitHub PR path,
not a separate delivery service. A reviewed signed Astro release admits one
exact source fragment: title or description in frontmatter, literal props or
collection metadata; image alt in literal page markup; or a single-page JSON-LD
WebPage identity. JavaScript/TypeScript expressions are parser-restricted to
strings, local identifiers/member access, interpolation and concatenation.
Calls, code injection and unsupported syntax are unavailable. Repository code
never runs on the host. Markup edits must occupy parser-proven markup, not
HTML-looking strings in frontmatter, expressions or scripts.

Static pages under the configured source directory and a single collection
entry with exactly one affected built page are A2. Layouts, shared components,
data/generator files, collection configuration and bracketed dynamic routes are
always A4, even when a build measures one changed page. More than one changed
page raises any source edit to A4. This follows Astro's
[file-based static/dynamic routing model](https://docs.astro.build/en/guides/routing/),
not a URL-name heuristic. Unsupported source mapping stays unavailable.

Build the unchanged baseline and candidate through 0126 only, at the exact same
base/tree and lockfile digest. Compare every artifact and all built HTML bytes.
The exact declared page set must equal the measured changed set. Each page must
contain only the independently asserted intended field replacement or insertion;
every byte outside that field and every undeclared HTML/non-HTML artifact must
remain unchanged. Added/deleted pages, changed assets, incidental formatting and
nondeterministic output are refused, not explained away as build noise.

Retain full built HTML only through the existing encrypted immutable artifact
backend, with scoped authenticated readback and atomic SQL metadata registration.
The key never enters the sandbox or PostgreSQL. SQL receives bounded temporary
verification bytes and persists only object references/digests, not page bodies.
Missing objects, unavailable storage, wrong keys and corrupt ciphertext prevent
sealing/dispatch. Retention and orphan cleanup use the existing artifact boundary.

Seal the complete canonical assertion set and its digest immutably, plus the
exact count, affected paths and at most three bounded before/after HTML samples.
Samples are owner review aids, never verification coverage. Bind full HTML to
the existing artifact/lockfile receipts; recheck scope, fields, hashes and
evidence at the SQL sealing boundary. The committed finding's crawled bytes must
match its baseline built URL exactly. A model recommendation cannot supply
missing authority or override a failed assertion.

Both A2 and A4 require exact dashboard Owner Inbox approval and MFA authenticated
within five minutes. Record that approval authentication time immutably. Dispatch
and every subsequent durable write permit require a current Owner/MFA session
within five minutes, unchanged authorization epochs/recovery and an approval no
older than one hour. Astro is never standing-grant or weekly-dispatch eligible;
the release contract explicitly denies autonomy. Slack cannot authorize Astro
dispatch. An owner-accepted unprotected base is usable only while 0125's exact
acceptance remains valid. Real protection remains preferred.

Preserve the ref-only shared GitHub write profile, protected-path preflight,
independent intent journal, fenced effects and ambiguous-response reconciliation.
No workflow patch, default-branch push, merge, deploy or secret endpoint is
admitted. Existing workflow-certification refusal remains in force.

## Consequences

This is a bounded internal delivery path, not broad Astro compatibility or a
production-enabled provider. Configuration and dependency restrictions remain
those in [ADR-0121](0121-astro-offline-build-boundary.md). Exact output may exceed
the existing sandbox or sealed-manifest bounds; that is visibly unavailable,
never a truncated impact count or weaker build profile. Owner approval does not
cause a write by itself. Customer deployment/live certification remains separate.

0086 is absent from this base. When integrated, Astro IndexNow key-file placement
must use configured `publicDir`, following the
[Astro public directory contract](https://docs.astro.build/en/reference/configuration-reference/#publicdir),
not the repository root. Track this explicitly rather than reimplementing 0086.

## Verification

Real PostgreSQL tests cover immutable scope receipts, exact replay, forged
assertions/counts/samples, owner MFA and PR preparation, standing denial and
unprotected acceptance invalidation. Pinned no-network npm containers exercise
paired builds of synthetic static, collection and generated date pages. The
small fixture contains a dynamic route, shared layout/component, data generator
and collection. Its package is explicitly a synthetic engine, not the actual
Astro compiler. Tests do not read or clone a private customer repository.
Commands, results and NOT_EXECUTED items are recorded in
[0127](../implementation/0127-astro-delivery.md).
