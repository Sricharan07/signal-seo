# Slice 0094: Change Measurement and Weekly Report

Classification: **Product**. Base: `404eeb9`. This reads existing imported source
evidence and appends internal measurement records under the existing worker role.
It adds no provider call, credential, egress profile, external write, human grant
or publishing authority. Existing owner-session/site report authorization remains
in force. Contract: Revision 4.0 sections 16 and 19, REQ-026 and INV-018; Revision
3.2 measurement and evidence sections. [ADR-0077](../adr/0077-provider-reported-change-measurements.md)
records the explicitly provider-reported interpretation approved for this slice.

**Current state: implemented and locally qualified; live providers NOT_EXECUTED.**
The final gate passed 837 PostgreSQL, 37 unsharded delivery, 1,252 other Python,
24 repository and 152 dashboard cases, plus every requested lab, boundary,
lint/format, dependency and dashboard typecheck/build command. Lab source hashes
match the final source. Range-secret verification follows the commit and is
recorded on the pull request; a staged-diff secret check precedes the commit.

## Records and Windows

Migration `0066_change_measurement` follows unchanged `0065` on the accepted
merge train. No existing migration or hash-protected specification is modified.

The first live-verified 0085 receipt creates three immutable forced-RLS plans,
keyed by tenant, site, operation and horizon (7, 28, 90). Each pins the exact
manifest page URL, verification attempt, verified-live timestamp, windows, source
generation IDs, metrics and verbatim coverage. Existing verified receipts are
backfilled with the same cutoff rule. The currently deliverable technical recipe
changes one page; this slice does not invent multi-page or editorial delivery.

Baseline windows contain the horizon's whole days ending before PR preparation;
only generations imported at or before preparation can be pinned. Missing
baseline evidence stays explicit and immutable, even if later imports could fill
it. Post windows begin the day after verified-live and contain the same number of
days. Due times are verified-live plus exactly 7/28/90 elapsed days. A due horizon
waits until its whole post window closes. GSC calendar dates use
`America/Los_Angeles`; Bing daily dates use UTC. These are labelled source-calendar
comparisons, not precisely aligned sub-day intervals across providers.

## Honest Imported Data

- GSC uses one current-binding, `web`, page-dimension generation covering the
  whole window. A wider generation must also have a date dimension. Exact page
  rows produce summed clicks/impressions, aggregate CTR and impression-weighted
  position. Zero impressions yield null CTR/position. No top-row interpolation,
  cross-generation splicing or site-to-page substitution is performed.
- GSC's `first_incomplete_date` keeps lag explicit. Missing covering page
  generations are `awaiting_data`; absent exact-page rows are `unavailable`.
- Bing requires a single performance generation with daily site rows covering
  every date. Clicks/impressions are **site context**, never page attribution.
  Bing page metrics, CTR and position are unavailable from this boundary. Both
  0069 and 0071 import implementations and their permissions remain unchanged.
- `measured_as_reported` is the normal terminal observation, labelled "as reported
  by provider; completeness not guaranteed". Source coverage remains verbatim,
  including `complete=false` and unknown missing data. There is no complete state
  or invented completeness threshold. GSC is the primary page-observation state;
  Bing's independent context state is exposed alongside it.
- Other states are `not_yet_due`, `awaiting_data`, `unavailable` (including unbound
  or revoked bindings) and `failed`. Missing metrics/deltas are null, not zero.
  Invalid source records fail explicitly. Deltas are observed subtraction only,
  not a causal estimate. Confounders list other recorded Signal live-verified
  changes to the exact page between baseline start and post end. External changes
  and other influences may be unobserved.

## Scheduling and Projection

The existing optional weekly runtime registers `ChangeMeasurementWorkflow` and
its imported-data-only activity. Maintenance reconciles stable per-operation and
per-horizon workflow IDs; duplicate starts are rejected. Temporal sleeps to the
due time and polls existing imports daily for up to 31 attempts while awaiting
data. The existing weekly measure stage sweeps plans thereafter, including late
imports and corrections. No dormant service or dependency is added.

SQL locks the plan before observation comparison and append. Retries, simultaneous
workers and lost activity acknowledgements do not duplicate unchanged evidence.
Later changed evidence appends another immutable observation, never edits prior
records. Timers and weekly reads exercise no publishing authority; worker inputs
can identify only existing live-verification-bound plans on active tenant/sites.
Runtime roles cannot inject observation time or access measurement tables directly.

`GET /v1/sites/{site_id}/weekly-cycles/{week_start}` now returns schema version 2
through the existing owner API/BFF. Legacy version 1 remains readable. Its stable
projection includes:

- What Shipped: existing exact-authority delivery/live-verification evidence.
- What It Did: horizons due in that UTC week, plus measurements updated that week;
  exact windows, baseline, latest observation before week end, source evidence,
  observed changes, confounders and Changes links. Pending records are explicit.
- What Is Next: unresolved weekly-cap deferrals and the existing unverified
  delivery backlog; otherwise `STRATEGY_OR_BACKLOG_UNAVAILABLE`.
- Needs a Decision: current, not historical, technical/editorial Inbox count and
  exact destination links. Reviewed/stale/previously-operated technical revisions
  are excluded.

The Changes dashboard shows those sections with native source-evidence
disclosures and responsive metric rows. No email, Slack or Telegram report send
is built. Channel delivery can consume this projection in later slices.

## Verification

Exact final commands and counts are recorded in the
[evidence](../evidence/0094-change-measurement.json). Qualification runs all
`scripts/run-*-tests.py` labs, OpenBao, GSC boundary checks, API/identity/tooling/
connector tests, full Python lint/format including delivery and page-attempt
tests, dependency check, `npm test` and range gitleaks using main's configuration.
All lab databases, Temporal servers and test workers are disposable and owned by
their run. Shared Docker resources belonging to other tracks are not stopped.

Focused tests cover baseline pins and immutability; each horizon and real Temporal
timer; provider lag then as-reported data; incomplete coverage retained verbatim;
unbound/revoked providers; absent page dimensions/rows; malformed data; concurrent
idempotency; deterministic replay; process kill after a real measurement commit
and heartbeat-timeout recovery; same-page confounders; tenant/role negatives; and
report windows, backlog/Inbox projection and unsafe contract rejection. Synthetic
domain generations do not qualify live providers; existing provider labs continue
to test import boundaries behind shared egress.

UI captures use the actual dashboard component and CSS with labelled synthetic
fixtures at desktop 1440x980 and mobile 390x844. Independent finish review:
`ship`, no material fixes; incumbent design files preserved. Pre-existing design
documentation drift was reported, not repaired. An ignored stale `.next/dev`
cache from the waiting branch was preserved under `.runtime` before typecheck.

Development corrections are not qualification: eight legacy schema-inventory
assertions needed the new exact head/table count; privilege checks were preserved
and a transactional migration-rollback test added. A source-changing run was
rejected by its unchanged integrity guard. The new delivery test initially loaded
the existing fixture under two module identities and therefore two synthetic
journal keypairs; its import now uses pytest's collected fixture. Signature
verification was not weakened. All 37 delivery cases then passed against stable
source. A sandbox cleanup check overlapped other tracks' candidate containers;
the unchanged gate passed on retry after that overlap cleared. No foreign
container was stopped.

## First Live Run and Limits

Live GSC, Bing, GitHub/customer deployment, Jev, GA4, production composition and
report-channel delivery remain **NOT_EXECUTED**. No production readiness or
causal outcome is claimed.

The owner needs an active verified site and existing owner-selected GSC/Bing
bindings, privately configured credentials and existing import workers. GSC
generations must request page (and date for wider windows) through the already
supported import boundary; site-only historical rows cannot supply page metrics.
Import matching whole baseline windows **before** preparing a change. Retain
those generations, existing 0085 verified-live evidence and a configured 0104
PostgreSQL/Temporal worker. Existing 0104 delivery credentials, grants and live
admission requirements remain unchanged; see its implementation record. Continue
imports through each post window and provider lag, then inspect the owner report.
No new measurement credentials are needed.

Follow-ups: separately qualified Bing page-level import, GA4, multi-page/editorial
delivery when supported, deployed runtime/live-provider qualification and later
report channels. None is simulated by this slice.

## Merge-Train Verification

PR #16 is rebased onto the preceding accepted train head. Migration 0066
follows unchanged 0065; the cumulative app-table assertion is 118.
All five requested fast checks passed. The original qualification above is
historical; current counts and commands are in the `merge_train` entry of
[the evidence](../evidence/0094-change-measurement.json). No production authority changed.

## Train Generation Fixture Correction

Classification: test-only product correction, carried as a separate fix commit on
#24, the earliest PR in Train 2. The pre-existing test assumed that a later insert
must have a strictly later wall-clock timestamp. The unchanged 0066 selector
already orders by `imported_at DESC, id DESC`; equal timestamps select the greatest
UUID, not the last insert. The failed run did not retain its import timestamps,
so a literal tie versus a clock adjustment cannot be distinguished retrospectively.
Fixtures now explicitly separate the earlier and later imports and assert cutoff
pinning. Two new real PostgreSQL cases force equal timestamps in both insertion
orders and repeat both GSC and Bing selection twenty times. All ten focused
change-measurement cases passed in 9.54 seconds. No production SQL, frozen migration,
timeout, safety gate, or existing assertion was weakened. Final train qualification
is recorded in the PR comments and run-owned receipts.
