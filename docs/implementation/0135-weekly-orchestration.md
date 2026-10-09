# Slice 0135: Weekly Orchestration

Classification: **security-critical**. Contract: Revision 4.0 sections 6.2, 6.3,
16 and 19; unchanged Revision 3.2 tenancy, workload, egress and reporting
boundaries; INV-005, INV-013, INV-027 and INV-034.

**Implemented and locally qualified. Live providers NOT_EXECUTED.**
No deployment, release admission or production readiness is claimed. The resumed
worktree passed compilation, whitespace and protected-specification checks after
the disk-full event. Final results: [evidence](../evidence/0135-weekly-orchestration.json).

The full gate passed 1,007 PostgreSQL, 43 unsharded delivery, 1,903
API/identity/tooling/connector, 43 repository and 202 dashboard tests, plus 112
other lab/boundary checks, with zero failures or skips. Lint/format (484 files),
dependencies, dashboard typecheck/build and protected documentation checks passed.
Range-secret qualification is recorded in the PR after commit and before push,
using main's unchanged configuration. Staged secrets are checked before commit.

## Ordered Skills

### Merge Train Model-Budget Integration

Writing quality 0136 now budgets Brain page classification and fact extraction.
The weekly adapter admits only its read/reserve/dispatch/completion ports, never
cap-setting. Exact extraction/derived classification IDs, admitted source, stage,
tenant/site, current generation and standing eligibility are checked in PostgreSQL.
Both model reservations share each source's already recorded standing cost ceiling
and the owner's existing monthly cap; unknown holds are not released or retried.
The guarded egress adapter exposes only the tenant/site scope already checked on
every leaf, retaining its guarded transports. The nested Content Writer read and
monthly read are copied through the same qualified workload resolver.

Four formerly failing Brain refresh cases exercise both model roles. Added
scope, source/role, per-source ceiling, monthly-cap, pause and dispatch refusals
also assert zero unauthorized model dispatches. Owner cap-setting remains absent
from the workload SQL and Python adapters. No owner session or write authority
is minted, no old migration is changed, and no egress profile is widened.

The version-one registry declares work type, standing budget, cap source,
dependencies and `(site, cycle, stage)` idempotency for seven stages:

| Stage | Work Type | Additional Cap | Dependency |
| --- | --- | --- | --- |
| GSC, Bing, GA4 imports | `research_audit` | Each current active binding | Independent |
| Business Brain refresh | `research_audit` | Changed sources, model cost bound | Crawl/analyze ordering |
| Strategy rebuild | `research_audit` | One new snapshot per cycle | After imports/refresh, including their failures |
| Brief proposals | `draft_patch` | Writer weekly cap: default 2, maximum 5 | Successful cycle strategy snapshot |
| Email report queue | `research_audit` | SMTP daily cap, verified recipients | Closed cycle |

Unconfigured, uncovered, exhausted or failed stages record closed reasons;
independent stages continue. PSI 0128 and AI-visibility 0131 retain their own
bounded owner-configured observation paths; no standing worker ports are
fabricated. Chat reports 0133 retain their separately opted-in, capped outbox and
weekly-close trigger alongside this registry's admitted email report port.
GSC imports final query/page evidence over 28 days ending three days before the
cycle. Bing and GA4 use their existing bounded imports. Existing one-active
GSC/Bing rules remain; GA4 iterates current bindings. Coverage stays evidence,
never invented zeroes.

Brain selects new/changed successful pages from the latest completed manifest and
current nonsecret, nondeleted, nonsuperseded documents. Extracted or previously
intended URL/body and document text hashes are excluded, including unknown work.
Plans over 64 units are unavailable, not truncated. Facts remain proposed.
Strategy writes a new cycle-pinned 0077 snapshot even for unchanged sources.
Briefs use its grounded payloads, never accept/supersede them, draft articles,
decide strategy items or approve facts.

## Authority and Outcomes

[ADR-0140](../adr/0140-standing-grant-weekly-skill-ports.md): admission resolves the
recorded human grant, reviewed A0/A1 release, recovery generation, work type,
owner/site epochs, exclusions, pause, revocation and expiry before capability I/O.
Batch reservations are all-or-none in the existing aggregate standing ledger.
Opaque handles never enter `app.sessions`; owner IDs are audit foreign keys only.
Closed additive SQL ports reuse qualified local functions with replaced authority
resolution, stage/resource checks and pinned source/binding/payload identities.
The connection facade and database grants independently deny approval, acceptance,
arbitrary SQL and write preparation. Tables are private, forced-RLS and immutable.
API, identity and scheduler roles cannot use worker ports.

Credential/artifact reads, existing refresh rotation and actual HTTP requests
recheck current standing and recovery authority. Shared-egress profiles are
unchanged; older adapter limits are reduced to the profile/run bounds, not widened.
No new external-write permission or owner session is created. The 0104
dispatch/journal/GitHub delivery path is unchanged.

[ADR-0141](../adr/0141-durable-weekly-skill-budgets-and-outcomes.md): intent precedes
I/O. Missing results become `OUTCOME_UNKNOWN`, never redispatch or refund. Paid
extraction requires a positive conservative per-source USD-cent bound covering
bounded Jev/model/fallback work. Reports label reserved upper bounds and actual
spend as unreported. Zero-cost stages still use volume units. Activity heartbeats,
an additive Temporal version patch and actual process-kill tests preserve unknown
work and stop replacement-worker admission after pause/revocation.

Version-one reports atomically record `REPORT_QUEUED`, project that same bounded
report and queue current verified opted-in owner recipients through 0093, under its
cap. Queueing is not SMTP delivery. The existing dispatcher remains required;
legacy cycles keep their notification trigger. The dashboard reads the latest
cycle and shows seven outcomes, reasons, refs, units and reserved budget with
closed bounded Python/TypeScript parsing. Synthetic full-page captures at desktop
1440 and mobile 390 CSS pixels, DPR 2, showed no overlap/clipping. Component review
passed; this is not authenticated-dashboard or provider qualification. Incumbent
product/design contracts are unchanged.

## Migration and Qualification

Migration `0087` follows `0086` in merge train 3. Older migrations and
accepted specifications are unchanged. It adds two immutable app tables, a private
directory, narrow ports and version-one report behavior. Destructive downgrade is
refused; use reviewed roll-forward recovery.

Tests cover grants, tenant/site/role negatives, pause/revoke/expiry/recovery and
owner-epoch changes, volume/spend/writer/email caps, replay, real worker kill,
independent failure, changed-only extraction, proposed facts/briefs, denied
approval/acceptance/write ports and honest report reasons. Synthetic providers use
the actual shared-egress gateway. Run every `scripts/run-*-tests.py` sequentially,
then `scripts/openbao_lab.py`, `scripts/check-gsc-provider-boundary.py`, Python
API/identity/tooling/connectors, full ruff check/format including delivery/page
attempt, pip check, npm test and range gitleaks with main's unchanged configuration.
Exact commands and counts are in the evidence. Labs clean only invocation-owned
resources; disk monitoring stops work below 3 GiB. No shared prune is performed.

## First Live Inputs

1. Verified site, human standing grant for `research_audit` and optional
   `draft_patch`, reviewed matching A0/A1 release ranges, explicit caps and current
   recovery generation. This slice invents or auto-reviews no A0 release.
2. Existing 0104 single-site worker inputs: role DSNs, Temporal, OpenBao recovery,
   qualified crawl/artifact/egress contexts and unchanged delivery requirements.
3. Private `SIGNAL_WEEKLY_{GSC,BING,GA4}_TOKEN`, `_TOKEN_CONTEXT`, `_DATA_CONTEXT`,
   owner-confirmed read-only OAuth bindings and OpenBao client credentials.
   Missing optional configuration records `PORT_UNCONFIGURED` without I/O.
4. Private `SIGNAL_WEEKLY_MODEL_TOKEN`, `SIGNAL_WEEKLY_MODEL_CONTEXT`,
   `SIGNAL_WEEKLY_BRAIN_ARTIFACT_ROOT`, `SIGNAL_WEEKLY_BRAIN_KEY_FILE`,
   `SIGNAL_WEEKLY_BRAIN_KEY_REF`, `SIGNAL_WEEKLY_BRAIN_COST_BOUND_CENTS`. The private
   absolute key file contains exactly 32 bytes matching the source store.
   Reconfirm both providers' bounded-request cost before paid work.
5. Existing TLS SMTP dispatcher/configuration, current identity-verified opted-in
   recipients such as `owner@example.invalid`, and cap. Live import, extraction,
   email delivery and later PSI/visibility/chat registrations stay NOT_EXECUTED.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0087` follows `0086`; 173 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2732 API/identity/tooling/connector
cases, 1208 PostgreSQL suite cases,
46 repository and 255 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.

Owned lab qualification selected only this slice's changed tests: three real
PostgreSQL/Temporal worker-kill/pause/revocation cases and four Temporal weekly-loop
replay/restart/finalizer cases passed. These are focused receipts, not a full-suite
claim; the final 0138 gate collects every delivery and Temporal case.
