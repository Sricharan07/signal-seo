# Slice 0131: Structured Data And Visibility Schedule

Status: **INTERNAL OWNER-REVIEWED STATIC RECIPE AND CAPPED SCHEDULER IMPLEMENTED; LOCAL QUALIFICATION RECORDED BELOW; LIVE PROVIDERS NOT_EXECUTED**.

## Scope And Classification

Security-critical: this extends candidate construction and admits paid assistant
reads. [ADR-0131](../adr/0131-owner-reviewed-grounded-structured-data.md) and
[ADR-0132](../adr/0132-reserved-ai-visibility-reobservation.md) record the
independent recipe and immutable spend boundary. Accepted specification bytes,
the existing WebPage-only repair manifest, and 0104's five A2 keys are unchanged.

Migration `0084_structured_visibility` follows `0083`; only new migration files
are added. Merge train 3 retains the accepted slice above the complete main stack.

## Implemented

`structured_data_grounded` supports closed FAQPage, Article/BlogPosting,
Organization, Product and BreadcrumbList subsets. Verbatim visible crawl text
grounds FAQs, article headline/dates and breadcrumb labels/links. Current
approved Brain facts ground every Organization/Product value. Unsupported
fields, including prices, offers and ratings, are omitted. All revisions require
owner review; Organization/Product additionally require owner claim handling.
Off-site URLs and uncertain visibility fail closed.

One static Eleventy HTML source must match committed crawl bytes. Exactly one
JSON-LD block is inserted or replaced. Both baseline and candidate builds must
pass the existing no-network sandbox. The built target is byte-exact and every
other output artifact remains unchanged. The distinct database sealer checks
current release, fact references, source/base, crawl URL, closed packet and
immutable receipts. Inbox parsing supports the additive packet. Existing PR
authority remains unchanged; byte-based reconstruction preserves Unicode.
Independent live verification checks exact JSON-LD bytes and the full page hash.

### Merge Train Integration

The static/grounded patch contract uses UTF-8 byte offsets, whereas the Astro,
frontmatter and Next.js adapters seal character offsets. Dispatch now preserves
both contracts and still requires the exact source fragment and result digest.
Existing Unicode adapter tests and the static byte-offset regression qualify
the distinction. The scheduling route rejects prepare/decide payloads with 422;
observe/publish/candidates remain absent and no mutation is admitted.

Grounded dispatch resolves its exact `structured_data_grounded` reviewed release,
not the technical recipe inferred from its provenance finding. Hash/contract
drift, another key and autonomy-eligible packets remain denied. Five release
binding regressions qualify this existing owner-review path; no standing grant
or new write authority is introduced. Live-provider delivery remains unqualified.

Owner-only `GET/POST /v1/sites/{site_id}/ai-visibility/schedule` and the selected
site's AI-visibility panel expose cadence, cap/held usage and per-provider run
history. POST has a closed bounded schema, strict integer/bool validation and
same-origin CSRF proof. The dashboard BFF preserves exact site scope, response
bounds and no-store handling. Analysts, foreign tenants/sites, stale authority
and unconfigured runtime are visibly denied/unavailable.

The private Temporal worker/reconciler uses one stable per-site UTC schedule,
weekly default and 1-30 day owner cadence. Each immutable run snapshots the
latest question version, then reserves each question/provider before any
factory, OpenBao credential read or existing 0074 shared-egress request.
Admission is site-serialized; cap exhaustion/unconfigured providers have visible
zero-hold unavailable receipts. Stable workflow intent and reservation keys
prevent paid redispatch after worker loss. Unknown outcomes retain their hold.
Verified shipped changes are associated with the next admitted run using
`observed change` wording, never a causal result claim.

## Qualification

The [evidence record](../evidence/0131-structured-data-and-visibility-schedule.json)
contains exact final command counts. Every requested lab uses real disposable
PostgreSQL and Temporal where applicable; provider doubles remain behind shared
egress. The isolated Docker build checks exact target bytes and unchanged assets.
Positive, negative and failure cases cover each supported type, verbatim FAQ,
approved sensitive facts, omitted unsupported values, off-site URLs, non-A2
authority, sealed/built output, cap admission, persistent unknown holds,
idempotency, worker cancellation/restart and subprocess SIGKILL, unconfigured
providers and tenant/role denials. Tests use synthetic credentials only.

The SIGKILL probe uses the real production activity with bounded test-only
deadlines; a killed child loses its activity acknowledgement, its PostgreSQL
hold persists, and a replacement worker cannot invoke the provider factory.
The production workflow's own schedule identity and history replay are checked
separately without changing production timeouts.

The existing Operations Ledger dashboard system is retained. A clearly labelled
synthetic loopback fixture was inspected at 1440x1000 and 390x844. Controls fit
without page overflow; the history region supports keyboard focus and horizontal
scrolling. The temporary fixture/server were removed/stopped. This visual check
does not qualify a production owner session or provider. No design-system
change, new dependency, deployed service or production write is introduced.

## Limits And First Live Inputs

The static recipe currently requires an existing current-detector finding as
page provenance. Clean pages without one, templated/CMS source mapping, rendered
visibility with CSS/scripts and unsupported schema fields remain unavailable.
No fabricated finding or business value substitutes for missing evidence.

Schedule defaults are disabled, seven days and USD 1.00. Actual billing spend is
unpriced/null. All dispatched reservations, including successful unpriced calls
and prior-month uncertainty, stay held against admission. This is conservative
carry-forward accounting, not automatic monthly replenishment or a claim that a
provider's actual bill is bounded by an unqualified estimate. There is no hold
settlement/release in this slice.

A first dedicated live run needs a current owner/MFA session, selected verified
site and crawl/question version; an independently signed/reviewed release and
approved fact references for Organization/Product; exact static repository/base
and the existing isolated build path; private API-role schedule connection and
workflow/evidence-role worker connections; a private Temporal client and current
external recovery source; provider-specific OpenBao credentials; scoped shared
egress ports; and explicitly reviewed integer monetary ceilings qualified
against the actual provider contract. The owner must set enabled cadence/cap.
Exact deployment/provider identifiers stay in protected private configuration.

Live OpenAI, Perplexity, Gemini, customer repository/build, PR delivery and effect
measurement are **NOT_EXECUTED**. Deployed scheduler composition remains
unavailable unless explicitly configured. No production authority, merge,
deploy, default-branch push, repository secret read or CI edit is enabled.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0084` follows `0083`; 164 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2639 API/identity/tooling/connector
cases, 1095 PostgreSQL suite cases,
46 repository and 246 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.

The owned candidate-sandbox lab passed all 32 cases, including grounded JSON-LD
construction alongside Astro, frontmatter and Next.js. Cleanup was confirmed;
only this invocation's artifacts were retained as bounded evidence.
