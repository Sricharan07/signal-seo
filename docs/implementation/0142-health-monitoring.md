# Slice 0142: Health Monitoring and Alerts (H1)

Security-critical: operational observations and outbound owner alerts. Based on
`168510b` through the accepted Train 3 stack; migration `0089` follows `0088`
and adds two function-only, immutable, forced-RLS app tables (175 cumulative app
tables). Existing migrations and all
accepted specification revisions are unchanged. No dependency or egress profile
is added. [ADR-0153](../adr/0153-private-health-observations-and-alerts.md) records
the boundary. Local qualification does not authorize deployment or production writes.

## Implemented

`HealthMonitor.serve` runs immediately, then every 60 seconds by default, with a
bounded 30-300 second cadence. The existing `WeeklyDeliveryRuntime.serve` hosts
the optional monitor alongside its worker loop; stopping the host cancels its
tasks. `compose_health_monitor` supplies read-only existing dependency ports.
Each probe is bounded to five seconds. Every run records all twenty named checks
with a closed state (`ok`, `warning`, `critical`, `unknown`) and reason; malformed,
missing, inaccessible or stale observations cannot become `ok`. Storage rejects
incoherent state/reason pairs. Check times are database-authoritative.

| Check | Observation and thresholds |
| --- | --- |
| Temporal | Existing client health RPC; one-second latency warning, three-second observed latency or unreachable critical, failed/absent probe unknown |
| Weekly schedule | Existing scoped schedule description; paused/missing future actions or no run for eight days warning, running one hour warning, running one day or failed terminal execution critical |
| Change measurement | Existing due-job projection and actual workflow descriptions; overdue running job warning, two days overdue or failed terminal execution critical; missing description unknown |
| Outbox backlog / age | Undelivered command count: warning 100, critical 1,000; oldest available age: warning five minutes, critical thirty minutes; future work has zero age |
| GSC, Bing, GA4, GitHub, Slack, Telegram bindings | Existing binding/restriction records and recent shared-egress/import evidence; revoked/reauth-required or observed 401/403 critical, failed import/provider attempt in the last day warning, missing/old evidence unknown; a saved binding alone is not proof of usable credentials |
| Email binding | Existing SMTP configuration, owner preference and claim head, and delivery receipts; revoked preference or lost claim critical, delivery failure warning, no configuration or no recent accepted receipt unknown |
| Screened egress pins | Protected existing origin/provider pin files, public-address screening and bounded issuance TTL; expiry within five minutes warning, expired critical, absent/invalid evidence unknown; includes operator-supplied dedicated-environment pin paths |
| OpenBao | Existing bounded private `/sys/seal-status` request with app-visible credentials; unsealed ok, one-second latency warning, sealed or three-second latency critical, denied/malformed/unreachable unknown |
| Disk / database size | Configured workload filesystem usage: 80% warning, 95% critical; current database: 10 GiB warning, 20 GiB critical; unavailable observation unknown |
| Write intents | Existing blocked or outcome-unknown GitHub operations (failed or unresolved/quarantined effects): one warning, five critical; monitoring never retries or releases them |
| Budgets | Existing DataForSEO monthly usage/cap and optional existing model/assistant ledger read ports: 80% warning, exhausted/zero cap critical; absent ledger/cap unknown, never fabricated as zero usage |

No provider request is made to test a connector. Existing receipt evidence can
report a recent failure, not certify that the currently configured token works.
Healthy binding evidence expires after fifteen minutes; GSC/Bing/GA4 import
coverage additionally requires a generation within seven days. Provider failure
and authorization rejection windows are one day. No token, provider body,
destination, email address or raw metric is exposed in the health projection.

## Alerts and Authority

The scheduler takes the scoped site row lock and appends observations and alert
events transactionally. A persistent state/reason episode emits once; an observed
recovery permits a later recurrence. Changed or recovered episodes deferred by
rate limits remain eligible on subsequent runs. Cooldown is one hour per check,
with at most six alert events per site/hour; critical checks are considered first.
Unknown is a non-ok alert condition, not a success or an inferred outage.

Email queues only for the current opted-in, IdP-verified owner, current membership
epoch/change history, site authority and external recovery generation. Delivery
uses 0093's existing outbox, TLS SMTP shared-egress path, dispatch-time recipient
revalidation, attempts cap and ambiguous-outcome handling. The message is fixed,
token-free and links to Settings; it grants no authority. Unconfigured or
unverified recipients receive no outbound intent.

Operator critical observations and persistence/delivery failures use closed
structured logs. Configured eligible-owner email also carries those scoped
operational alerts; there is no separate arbitrary operator-address sender. A
database outage cannot commit an email intent and is logged explicitly.

`control.health_alert_events` is an identity-worker-only durable registration point for
0133: downstream chat outboxes deduplicate by event identity and must revalidate
current verified Slack/Telegram owner bindings. `chat_alerts(scope, generation)`
may register that existing consumer; it is called after each successful commit
so delivery can recover independently of observation dedup. No Slack/Telegram
sender, new profile, unchecked recipient or alternate transport is added here.

## Owner and Private Projections

The owner-only site read requires the existing session, site authority, active
tenancy and external recovery generation. Other roles/tenants are denied. Settings
shows all states, UTC check times and fixed remediation hints in a read-only flat
panel. Missing composition and empty observations are visibly unavailable.
Observations older than five minutes are unknown in SQL and the bounded server
fetch; future-skewed browser evidence is also unknown.

`/health` is schema-hidden and admits only literal loopback peers without forwarded
address headers. Remote requests are 404 even with forged private forwarding.
It returns 200 only with a complete, fresh, persisted all-ok snapshot; every other
case is 503/unknown or non-ok. It returns no tenant identifiers. Existing public
Caddy `/health*` denial is unchanged and covered by regression tests. Do not add a
public health proxy or broaden the private listener when composing the monitor.

## Verification

Commands, counts and explicit exclusions are recorded in
[0142 evidence](../evidence/0142-health-monitoring.json). Tests cover all four states
for every check, malformed and failed probes, bounded screened-pin expiry and
private-address denial, app-visible seal state, real Temporal schedule pause and
stuck execution, real PostgreSQL dedup/caps/delayed transitions, actual GSC
revocation, unverified-recipient suppression, function-only/RLS/immutable storage,
stale observations, tenant/role negatives, and public/forwarding route denial.

The final frozen-source gate passed all 17 commands: 2,002 API/identity/tooling/
connector cases, 973 PostgreSQL cases, 40 delivery cases, 11 Temporal cases,
67 other lab cases, 34 OpenBao scenarios, 43 repository and 204 dashboard cases.
That is 3,374 reported case executions (including overlap), zero failures.
Ruff check and format (483 files), pip check, dashboard typecheck/build, protected
specification hashes and the GSC provider boundary also pass. Staged gitleaks
reports zero findings with main's unchanged configuration; commit-range scanning
is run after commit creation and recorded in the PR before push. Earlier source
guard rejection and the corrected globally unique OAuth-state fixture failure
are superseded by this complete rerun, not waived.

Synthetic visual captures use the actual owner Settings component and CSS:
[desktop](../evidence/0142-health-desktop.png) and
[mobile](../evidence/0142-health-mobile.png). The bounded extension review is
`ship`; it introduces no design-system changes and preserves existing design
metadata. These captures do not represent a deployed monitor or live provider.

## First Live Run and Exclusions

The operator must privately supply scoped scheduler/API connection factories,
current external recovery authority, existing Temporal client and measurement
activities, app-visible OpenBao authority, filesystem path and protected screened
pin paths/reader. Compose the monitor through `compose_health_monitor`, pass it
to the existing weekly runtime and API, and register `ComposedHealthGateway` with
the API-role connection factory. Unregistered capabilities stay unavailable.

Model/assistant budget monitoring requires actual existing usage/cap read ports;
this main revision has no durable aggregate ledger for them. Absent ports remain
unknown. SMTP configuration, secrets and personal opt-in use 0093 unchanged.
0133 must register its existing verified-recipient chat consumers before chat
delivery can be qualified. No personal or environment identifiers belong in git.

Live provider delivery, inbox placement, Slack/Telegram health delivery, deployed
monitor supervision/restart, dedicated-environment pin refresh/expiry and actual
resource-growth exercises are **NOT_EXECUTED**. Qualify positive, negative and
failure paths in the authorized dedicated environment before enabling customer
alerts. No merge, deployment, default-branch push, content deletion, CI edit,
repository-secret access, cap change or autonomy grant is authorized by this slice.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0089` follows `0088`; 175 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2867 API/identity/tooling/connector
cases, 1226 PostgreSQL suite cases,
46 repository and 262 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.

The owned Temporal lab passed its focused real reachability, schedule pause,
missing schedule and stuck execution case (one case, ten unrelated cases
deselected). Receipt: `/tmp/signal-merge-train/train3-40-health-lab/00.log`.
The final stack is checked once with
`.venv/bin/python scripts/run-full-gate.py --range main..HEAD`; its machine-readable
receipt and final PR comments are the authority for full-stack counts, rather than
the historical per-slice totals above.
