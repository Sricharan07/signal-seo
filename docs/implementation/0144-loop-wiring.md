# Slice 0144: Weekly Loop Wiring

Classification: **security-critical**. Revision 4.0 research, standing authority,
reporting and operational invariants, with retained Revision 3.2 tenancy, egress,
build and recovery boundaries. Revision 4.1 default-branch rules are unchanged.

Implementation is complete for review; `full_gate` is **DEFERRED_TO_MERGE_TRAIN**.
The reviewer authorized commit, push and PR, not a passing-gate claim or merge.
Live providers are **NOT_EXECUTED**;
no production readiness, deployment, release or unattended write is claimed.
Final gate results belong in [evidence](../evidence/0144-loop-wiring.json).

## Scope

- Register `pagespeed_refresh`, `visibility_reobserve` and `chat_report_delivery`
  in 0135. Plans, standing scope, weekly volume/spend, immutable intent/result,
  replay and dashboard states use its existing mechanism, not a parallel loop.
- PSI has the distinct site/cycle research handle approved in
  [ADR-0157](../adr/0157-standing-scoped-pagespeed-weekly-execution.md). Current-owner
  collection is unchanged; no identity or session is created or borrowed.
- Visibility reuses 0131's current schedule and assistant reservations. The
  private host config supplies reviewed conservative ceilings; absent providers
  and exhausted budgets remain visibly unavailable.
- Chat uses 0133's verified owner report preferences and current bindings. It
  reports acceptance, suppression/unavailability or unknown outcomes, not
  simulated delivery. 0093 email remains unchanged.
- Health registers 0133 delivery at 0142's callback; only durable alert events
  fan out, deduplicated through the existing outbox uniqueness and upstream
  cooldown/hourly limits. A bounded site-scoped maintenance pump sends queued
  reports/alerts through existing provider profiles and recipient rechecks.
- 0094 windows now retain available Bing page rows, incomplete coverage and
  reported UTC dates alongside site context. Missing URL/window rows have an
  explicit unavailable reason; position and CTR are not fabricated.
- Astro static IndexNow placement uses configured literal `publicDir`, default
  `public/`, with the 0126 offline baseline/candidate artifact boundary. A4
  impact registration preserves 0127's fresh-owner publishing checks. Other
  framework placement and live-key verification rules are unchanged.
- One shared OpenBao lab helper uses a five-second readiness deadline and bounded
  half-second reads for newly mounted KV v2 configuration. Only transient 404/503/transport failures are
  retried; mount failure and forbidden/malformed responses fail closed. Existing
  provisioning assertions remain unchanged.

[ADR-0158](../adr/0158-weekly-observation-and-verified-chat-wiring.md) records the
remaining wiring decisions. No new work type, profile, dependency or service is
introduced. Existing Jev, journal, PR, approval and recovery authority is retained.

## Migration

0092 follows 0091 above the accepted internal-link and keyword-topic slices on
main `856a996`. Frozen migrations 0001-0089 are unchanged. App table count
remains 177. It adds closed workflow ports, a workload marker, registry/projection
states, site-scoped chat functions, Bing window logic and Astro key constraints.
Destructive downgrade is refused; recovery is reviewed roll-forward only.

## Qualification

Focused tests use disposable PostgreSQL, actual Temporal worker SIGKILL and
history replay, synthetic providers behind the shared gateway, and the pinned
network-disabled npm sandbox. No live PSI, assistant, Slack, Telegram, Bing,
GitHub or deployed Astro run is substituted by these fixtures. The synthetic
Astro executable proves the offline placement boundary, not Astro compatibility.

The full gate command is `.venv/bin/python scripts/run-full-gate.py --range
main..HEAD`, with main's unchanged secret configuration. It runs every
`scripts/run-*-tests.py`, OpenBao, GSC boundary, requested Python suites, lint,
format, dependency and npm checks. The single full run finished with 20/23 steps
passing: 4,853 test-case executions passed, four failed, and 21 command checks
passed; zero skips. The counts include the database runner's requested repeated
Brain checks. All 125 delivery cases, 34 candidate sandbox cases, 19 IndexNow
cases and 45 OpenBao checks passed. The retained private report is
`.runtime/full-gate/run-zlghrr89/summary.json`; portable commands/counts are in
the evidence record.

One failure caused by this slice was an inherited email test assuming the last
registry entry was email. Its assertion now selects `report_delivery` by name,
preserving its exact `REPORT_QUEUED` expectation. A disposable PostgreSQL rerun
of all four `report_queue_verified_recipients` cases passed. Product code and
email behavior did not change after the full gate.

Three remaining failures in unchanged code/tests are deferred to merge train 4:

- `tests/browser/test_browser_worker.py::test_real_container_time_budget_exits_and_cleans_up`:
  the four-second sandbox deadline expired during worker startup, before action.
- `tests/temporal/test_change_measurement_workflow.py::test_horizon_timer_and_reported_state_replay[7]`:
  the Temporal SDK could not connect to its local dev server within five seconds.
- `tests/control_plane/test_technical_recipes.py::test_committed_finding_is_loaded_and_exact_build_is_sealed[slack]`:
  the unchanged observation-backoff assertion did not raise. The same case passed
  in the separate IndexNow lab during this gate. The 30-second backoff code and
  test were not edited.

Full-gate disposition: **DEFERRED_TO_MERGE_TRAIN**; reviewer accepted as
load-induced pending the merge-train gate. Two other sessions were running labs
on this machine during these failures. Merge train 4 will run its single full
gate alone; flaky-test deterministic-clock fixes are queued in a separate slice.
The original 20/23 and 4,853-passed/4-failed counts remain unchanged. This is not
a passing full gate or a qualified deterministic root-cause claim.

No deadline, assertion, provider policy or gate was weakened. All gate processes
and their owned resources completed cleanup; foreign containers were left alone.
The full gate and Temporal/browser labs were not rerun. The reviewer explicitly
accepted this deferral for commit/push/PR; the isolated merge-train gate remains
required before merge. Staged gitleaks with main's unchanged configuration passed;
the delivery PR records commit-range scanning. No production readiness is claimed.

Focused development attempts included migration/port corrections and stale test
expectations; these are not discarded from the final gate record. A candidate
lab attempt timed out in an inherited case; its 34-case retry passed tests but
reported cleanup for that attempt's leftover container. Only the verified owned
container was removed. The final full-gate candidate lab passed 34/34 and cleanup.
The dashboard's actual components were inspected at 1440px and 390px via
Playwright using explicitly synthetic report data; no authenticated deployment or
readiness was simulated.

## First Live Run

The owner must supply the existing active own-site proof, current human
`research_audit` grant with reviewed A0 release and sufficient volume/spend,
current recovery authority, and qualified private worker DSNs/egress contexts.
`scripts/qualify_weekly_delivery.py` accepts explicit optional PSI context/token
and key path (omitted path explicitly selects keyless quota), assistant reader
token and per-provider context/ceiling, and chat identity DSN plus existing
Slack/Telegram reader token/context. Never supply an owner session to these ports.

Visibility also requires current enabled 0131 settings/questions/monthly budget.
Chat requires existing verified opted-in channels, active bindings/links and
0133 operator report origin/caps. Health requires the existing configured monitor
and scheduler store. Astro needs the existing protected/owner-accepted base,
literal static configuration, integrity-pinned registry cache, encrypted build
artifacts, offline build pair and fresh A4 owner approval before its key PR.
Actual deployment/key crawl and provider qualification remain NOT_EXECUTED.

## Merge Integration: Accumulated Internal-Link Constraints

The first meeting with 0140 exposed two genuine constraint-replacement defects:
0092 omitted `internal_link_proposals` from both weekly stage checks and omitted
`links.internal.add` from the reviewed candidate evidence check. The unchanged
0140 owner and weekly replay tests both failed on those constraints in a focused
PostgreSQL run (`train4-50/pre-union.log`, two failures). The fix belongs here,
where the later migration replaces the accepted earlier constraints.

0092 now retains all eleven registered stages and the existing owner-reviewed
internal-link case. Its added A4 exception remains restricted to IndexNow keys
with non-autonomous static placement; internal links cannot use it. New tests
evaluate the actual installed PostgreSQL check expressions for every registered
stage, unknown stages, reviewed links/keys, A4 key requirements and forbidden
internal-link/A2/unknown cases. Existing end-to-end sealing, exact scope, pause,
authority, replay and idempotency tests are retained unchanged.

Main's grouped shell, readable skill names, Finished cycle status, ink actions
and unchanged repository design guards remain intact. Exact new skill and Bing
generation evidence is retained under Technical details. No profile, authority,
test deadline, backoff assertion or database budget is widened.

## Merge Train 4

Rebased in the accepted order above main `856a996` (head 0089).
Migration `0092` follows `0091`; 177 cumulative app tables.
Frozen migrations 0001-0089, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2957 API/identity/tooling/connector
cases, 1322 PostgreSQL suite cases,
46 repository and 278 dashboard cases; Ruff check and format passed.
Main's Direction C presentation and unchanged design guards are retained.
Earlier qualification and deferrals above are historical. Integrated qualification
requires the final-tip 0138 full-gate receipt and PR comment; they supersede the
slice deferral only when every gate passes. Live-provider and production limits
remain unchanged.
