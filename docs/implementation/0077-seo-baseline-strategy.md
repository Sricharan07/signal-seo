# Slice 0077 - SEO Baseline and 90-day Strategy

Classification: Product. This consumes existing current owner/session and proposal
boundaries without adding grants, secrets, network profiles, provider calls,
repository writes, or execution authority.

## Implemented Boundary

Migration `0063` follows unchanged `0062` on the merge train. Forced-RLS, function-only snapshots and item decisions are append-only.
Each site has monotonically versioned canonical snapshots with SHA-256 identity.
Unchanged inputs replay the latest version. Recording rechecks the exact source
packet, and decisions require the current exact snapshot/item. Stable decisions
survive later versions: refresh cannot duplicate an accepted brief.

The baseline pins the latest 0066 crawl, its 0068 report, page inventory including
failed frontier settlements, latest current-bound final-web GSC generation per
dimension cohort, latest current-bound Bing performance import, latest observation
per question/provider in the current 0075 set, current approved 0073 facts and
competitors, and 0082 brief inventory. Every measurement carries evidence IDs;
an authenticated snapshot-evidence endpoint exposes the pinned record without
credentials, secret references or raw page/document bodies.

GSC returned cohorts are not complete site totals and are never added across
overlapping dimensions/windows. Bing currently has daily site clicks/impressions
only: page/query/position/CTR metrics remain unavailable. Source-specific daily
tables preserve gaps and coverage; absent dates are unknown, never zero. Partial
AI observations make no zero citation claim. API provider/model/date are retained;
consumer-app visibility is not inferred.

## Strategy And Acceptance

Candidates come from findings mapped through 0081 recipes; GSC queries with positive
impressions and position >= 8 or CTR < 0.02; missing approved Brain categories;
and complete uncited AI observations. These are diagnosis proxies, not ranking
forecasts or causal-effect claims. Inputs, alternatives, evidence, confidence and
effort are inspectable. Priority is `impact_proxy * confidence * (1 / effort_days)`
with stable ID tie-breaking. Sequential effort bands suggest days 1-30, 31-60,
61-90, or backlog; they are not promised delivery dates.

Model reordering is unconfigured and visibly unavailable. The immutable plan
records `deterministic_fallback` and `MODEL_REORDERING_UNCONFIGURED`; arbitrary
model candidate fields are rejected at API/BFF ingress. Source prose never selects
an action. No optional model path is simulated.

Content/AI acceptance atomically invokes existing
`control.content_writer_create_brief` with `evidence_proposal`, rechecking current
crawled sources and approved facts. It creates no brief acceptance, draft, approval,
outbox dispatch or provider operation. Brain gaps with no supporting approved
facts require owner input, not fabricated briefs.

Technical acceptance routes only to an existing matching pending sealed 0083
revision. There is no raw-finding Inbox format to create safely. Candidate
preparation remains unavailable for unsealed findings. A planning decision is not
an Inbox approval. Recipe eligibility displays only the 0104 release attestation;
current standing grant, exact candidate, caps and deterministic/Jev gates still
decide at execution. Unsupported recipes require owner diagnosis. See
[ADR-0087](../adr/0087-evidence-only-onboarding-strategy.md).

## Dashboard And Composition

Overview shows headline evidence, source coverage, top GSC cohorts and missing
sources. Pages shows per-page findings/metrics. Strategy shows phase, rationale,
inputs, status and owner accept/dismiss commands. Analytics separates GSC/Bing
cohorts and daily trends with coverage. Existing signed-out/non-owner unavailable
surfaces remain unchanged. The same-origin BFF reuses session cookies and CSRF.
Local pilot composes the existing API-role connection factory; production
composition and real customer onboarding are not qualified.

## Verification

Run `.venv/bin/python scripts/run-database-tests.py`,
`.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q`
and `npm test`. Tests cover complete/partial evidence, provenance, deterministic
score snapshots, injection-as-data, unbound GSC/Bing, absent/partial AI visibility,
absent DataForSEO, immutable snapshots, replay, stale decisions, source/fact checks,
tenant/site/role denials, exact unaccepted proposals, and dashboard/BFF contracts.

Full gate results and unavailable paths are in
[the evidence](../evidence/0077-seo-baseline-strategy.json). Eight desktop/mobile
captures use the actual components and CSS with labelled synthetic projections,
not a live authenticated customer dashboard. The scoped finish review is `SHIP`;
hydrated interactions, authenticated-shell fit and long-content stress qualification
are not established by these captures. Live GSC, Bing,
assistants and customer onboarding remain `NOT_EXECUTED`. A first live run needs
dedicated owner-controlled disposable resources and their existing connector
qualification, verified site crawl/audit, approved Brain facts, and the existing
API-role connection/recovery-generation composition. No live provider is contacted
by analysis itself. No production writes or release admission are enabled.

## Merge-Train Verification

PR #13 is rebased onto the preceding accepted train head. Migration 0063
follows unchanged 0062; the cumulative app-table assertion is 106.
All five requested fast checks passed. The original qualification above is
historical; current counts and commands are in the `merge_train` entry of
[the evidence](../evidence/0077-seo-baseline-strategy.json). No production authority changed.
