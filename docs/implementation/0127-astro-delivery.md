# Slice 0127: Owner-Approved Astro Delivery

Classification: **security-critical**. Decision:
[ADR-0122](../adr/0122-exact-owner-approved-astro-delivery.md).
Stacked on accepted slice 0126. Migration **0080** follows unchanged 0079 and
adds three function-only, immutable forced-RLS tables. Accepted specifications
and historical migrations remain byte-for-byte unchanged.

## Implemented Scope

The existing technical recipe service now admits four separately reviewed signed
Astro recipe keys: title, meta description, image alt and single-page JSON-LD.
Each revision changes one bounded exact source fragment and reconstructs that
same fragment during GitHub dispatch. Source drift, protected paths, workflow
files and unreviewed/revoked releases are refused.

Title/description edits support parser-proven existing frontmatter/JS metadata
values, literal page props/markup, root JSON metadata and narrow quoted
YAML/Markdown frontmatter lines in one collection entry. Missing title/description
can be inserted into a literal page head. Alt edits change only a literal image's
alt attribute in its own markup. JSON-LD replaces one existing script payload
with the evidence-bound schema.org WebPage context/type/URL, in one static page.
Arbitrary generator logic, function calls, expression-valued metadata props, configuration
edits, unsupported Astro syntax and unknown source mappings stay unavailable.

Configured static source pages and single collection entries are A2 only when
exactly one built page changes. Dynamic routes such as `[slug].astro`, shared
layouts/components, data/generators and collection configuration are A4 even
with one changed page. Any larger measured page scope is A4. Realistic duplicate
date-page metadata therefore cannot be disguised as a per-URL A2 edit.

## Exact Build Proof

Baseline and candidate run only through 0126's integrity-verified public tarball
cache and credential-free, network-none sandbox. Both use the same exact Git
base/tree and lockfile. `npm ci --ignore-scripts --offline` and the fixed offline
build are unchanged; there is no lifecycle-enabled or networked fallback.

The verifier enumerates every built HTML page and compares every artifact, not
just samples. Changed page paths must equal the declared set exactly. Each
target's entire candidate HTML must equal its baseline with only the intended
title/description/alt/JSON-LD field replaced or inserted. Unrelated bytes inside
a target, extra changed pages, added/deleted pages and changed CSS/JS/assets all
refuse sealing. Grounded vocabulary and owner claim review remain mandatory.
The committed finding's baseline built bytes and URL must match its crawl
evidence; stale or transformed production HTML does not get a permissive bypass.

Full raw HTML uses the existing encrypted immutable artifact store, scoped to
the current owner/site and authenticated with a private artifact key. It is
read back before atomic registration with the build receipt. PostgreSQL retains
only its canonical digest and exact artifact reference, never full page bodies.
Unavailable storage, missing/corrupt objects and wrong keys fail closed; the key
never enters the sandbox or SQL parameters. Replay decrypts and authenticates
the same object. Existing artifact retention/orphan cleanup applies (30-day
minimum retention in this internal profile). Full raw HTML is bound to the
existing artifact/lockfile receipts. SQL
independently checks page hashes/sizes, exact replacement assertions, unchanged
artifacts, count, declared paths, bounded samples and committed finding scope.
The complete RFC 8785 assertion set is immutable; its digest is part of the
sealed candidate. Inbox shows A2/A4, exact impact count and affected paths,
sample before/after built HTML, scope digest and lockfile digest. Unicode safety
and escaped rendering remain unchanged. Approval records no repository write.

Additional bounds: at most 128 affected pages, one source fragment of at most
4 KiB before/after, complete assertions at most 2 MiB, at most three sample
diffs totaling 8 KiB, and the existing 32 KiB sealed manifest. Existing 0126
source/dependency/container/artifact limits remain; the encrypted canonical
all-page object is at most 20 MiB. Larger or unsupported real
sites are explicitly unavailable, never sampled/truncated into eligibility.

## Authority And Recovery

Every Astro approval requires the current Owner through the dashboard Inbox and
MFA within five minutes, including A2. The authentication time is immutable.
Dispatch and each existing durable write permit recheck current Owner/MFA,
five-minute freshness, exact decision/revision, one-hour approval expiry,
authorization epochs, recovery generation, binding/base/build and release.
Astro is never standing- or weekly-dispatch eligible; Slack approval is not Astro
dispatch authority. A4 is never autonomy-eligible.

0125's owner-accepted unprotected base works only while its exact acceptance is
valid; real protection remains preferred and risk-generation drift blocks
dispatch. Default rejection without acceptance and all provider identity,
branch/installation/permission/recovery invalidations remain unchanged.
The existing ref-restricted GitHub write profile, independent journal, effect
fences and read reconciliation remain mandatory. There is no merge, deployment,
workflow modification, default-branch push or repository secret access.

## Qualification

The final frozen-source local gate passed **3,001 counted cases, zero failed and
zero skipped**: 906 PostgreSQL, 37 delivery, 1,768 API/identity/tooling/connector,
90 other lab cases, 43 repository and 157 dashboard cases. All nine
`scripts/run-*-tests.py` labs, OpenBao, the additional Keycloak lab, GSC boundary,
lint, format (436 files), dependency, documentation, typecheck, build and secret
checks passed. Source-hashed receipts match the qualified source and report
completed owned cleanup. The evidence retains the superseded plaintext-HTML
qualification, source-hardening restart, provider interruption and unchanged
Docker setup retry; none replaces the final complete gate.

Final commands, counts, source-hashed lab receipts and cleanup are recorded in
[the evidence record](../evidence/0127-astro-delivery.json). The synthetic fixture
mirrors generated date routes, a shared layout/component, data/generator and a
collection. Real pinned npm/container execution proves the offline boundary;
its explicitly synthetic package does not qualify the actual Astro compiler.
Tests never read or clone a private customer repository.

Live GitHub, official-registry fetches, actual Astro-engine compatibility,
customer repository/build, customer deployment/live assertions and production
composition remain **NOT_EXECUTED**. No production write, release admission or
signed delivery certification is enabled by this slice.

## First Live Run And Follow-Up

The integration thread needs an owner-selected verified site/repository, exact
current App installation/permissions and base binding, private OpenBao setup,
admitted GitHub and official-registry shared-egress contexts, the pinned sandbox
image, worktree-local public cache, private encrypted artifact backend and
OpenBao-held artifact key. Qualify retention/backup and unavailable/corrupt-key
failure paths before live use. The owner must accept any unprotected
base with fresh MFA, approve the exact candidate in Inbox and step up freshly
for each Astro PR. A reviewed signed Astro release and independently qualified
real compiler/build within all bounds are required. Retain exact raw baseline
and candidate receipts, all page assertions and redacted provider evidence.

The real target's existing Actions workflow does not authorize Signal to touch
it. The existing `PR_WORKFLOW_CERTIFICATION_REQUIRED` guard still refuses a
checkout containing workflows without separate delivery qualification. Do not
remove this refusal to make the first live run succeed. Opening a PR is not
deployment; broader Astro live verification/certification is not claimed here.

**0086 integration follow-up:** IndexNow is absent from this base (no key-file
implementation to modify). On integration, Astro key-file placement must target
the configured `publicDir`, not the repository root. Include custom `publicDir`
positive/negative tests and exact built key-file evidence in that follow-up;
0127 deliberately does not reimplement the merge-train slice.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0080` follows `0079`; 157 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2331 API/identity/tooling/connector
cases, 1051 PostgreSQL cases (including the runner's repeated Brain checks),
46 repository and 229 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.
The slice-owned candidate-sandbox lab passed all 18 cases on this rebased source.
