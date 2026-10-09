# Core V1 Implementation Roadmap

> **Superseded 2026-09-24.** The active sequence is the
> [R1–R4 roadmap](roadmap.md) under
> [Revision 4.0](../../Signal_Production_Engineering_Specification_Revision_4_0.md).
> This roadmap is preserved as history.

Status: **SUPERSEDED BY THE R1–R4 ROADMAP; PRESERVED HISTORY**.

This roadmap translates the approved
[Core V1 PRD](../product/core-v1-prd.md) and normative
[Revision 3.2 specification](../../Signal_Production_Engineering_Specification_Revision_3_2.md)
into bounded implementation work. The [status page](status.md), not this plan,
records what currently exists.

## Delivery Rules

- Complete one bounded slice with implementation, positive/negative/failure tests,
  documentation, status, changelog, and a reviewed commit before claiming it done.
- Prefer vertical increments that make one real user journey more complete. A
  foundational slice must name the later journey it enables and its current limit.
- Use real PostgreSQL and real dedicated provider stacks where the contract depends
  on their behavior. Fakes add fault injection but do not replace qualification.
- Keep provider I/O, deterministic authority, durable orchestration, and model
  reasoning separate. Do not create dormant services for future scale.
- Build all nine Core V1 roles, but implement them as typed responsibilities in the
  smallest bounded runtime that satisfies isolation, evaluation, and observability.
- Keep all consequential production authority disabled until the applicable gates
  and Core V1 pilot admission pass.

## Current Position

Through Slice 0060, Signal has tested parts of the trusted foundation: scoped
identity/session primitives, invitation and harmless command contracts, durable
outbox/admission bookkeeping, deterministic Temporal start, and a supervisor-ready
stoppable command consumer. A production `CrawlSite` state machine, bounded
activity contracts, terminal PostgreSQL projection, cancellation behavior, and
replay tests are jointly qualified against PostgreSQL and Temporal with a synthetic
executor. The command consumer now has release-bound private health plus a locally
qualified digest-pinned non-root image and bounded Compose supervision contract.
It does not yet have a published/signed deployment, monitoring backend, worker
build-ID deployment, or a complete scope-enforcing crawler. An
exact-origin, public-address-screened, numeric-peer-pinned GET boundary is qualified
on an isolated real network. Its durable run snapshot, stable URL inventory,
discovery provenance, serialized count/depth/duration budgets, and lease-safe
frontier now exist in PostgreSQL. A separate local AES-GCM artifact backend,
append-only lease-bound fetch observations, integrity/restore attestations, and
grace-period orphan reconciliation are also qualified. Exact robots retrieval now
uses that network boundary, a pinned RFC parser profile, encrypted body artifacts,
immutable expiring snapshots, and a conservative status policy. A PostgreSQL
global-origin gate now serializes cross-tenant requests with shared delay,
provider/latency backoff, exact short permits, and crash-expiry reconciliation.
An authority-free page-attempt coordinator now binds the exact frontier lease,
current robots snapshot, global HTML permit, pinned request, encrypted observation,
and atomic permit completion. It records dispatch before HTTP and refuses blind
retry when the outcome is unknown. The composed boundary remains disconnected
from the workflow until frontier/byte settlement, fair scheduling, parsing,
coverage, and controlled resolver/egress exist. The local backend is not a
distributed production artifact service and has no retention/key lifecycle.
Signal now also has a responsive server-rendered dashboard shell with all fourteen
named destinations, a full-viewport white-and-cool-gray frame, solid-blue actions,
domain-specific Lucide icons, and readiness ledgers that do not simulate missing
customer data. Overview, Analytics, and Usage include stable chart structures with
no plotted observation until qualified evidence exists. It displays the real
public readiness/capability contract, fails closed for invalid or unavailable
responses, and leaves unimplemented controls visibly unavailable. Its server tier
now exposes same-origin existing-user login, callback, organization selection,
logout, and local cleanup over the existing identity/session APIs; forwards only
the exact cookie needed for each hop; and shows bounded identity, role, MFA,
organization, and site state without exposing bearers or browser-to-API access.
The exact tenant session now owns a versioned active-site selection, appends
immutable hash-chained transition evidence, exposes stale-selection conflicts,
and requires that context for site-scoped snapshot commands. A current owner can
now atomically add one canonical HTTPS origin as an explicitly unverified site,
receive the narrow existing site permission, select it in the session, and retain
immutable onboarding evidence with bounded idempotency and concurrency. The
default production identity gateway remains unconfigured. A one-command disposable
local composition now proves real Keycloak sign-in, PostgreSQL-backed organization
selection, owner site onboarding, and one selected-site Work command through the
real PostgreSQL outbox and local Temporal worker to a terminal manifest. It now
continues from an exact verified-homepage finding into Signal Chat, where a bounded
`gpt-5.6-luna` content responsibility drafts one metadata field and deterministic
checks seal its RFC 8785 revision for one exact immutable owner decision. Model
intent, outcome, usage, prompt/input/output identity, and provider receipt are
durable; the credential is isolated behind a disposable exact-read OpenBao
capability. The Temporal audit activity remains synthetic and performs no customer
read. After exact owner proof, Pages can separately commit intent, perform one
bounded GET of the verified homepage, and persist title, first H1, description
state, body digest, and a deterministic missing-description finding as immutable
customer evidence. That exact finding can now drive the bounded Luna release, a
PostgreSQL-reconstructed RFC 8785 revision, and one immutable owner decision
without external dispatch. That narrow flow is not broad crawl, repository
candidate, deployment, nine-role workforce, or GitHub evidence. An internal
exact-origin proof API now issues an
owner-gated expiring plaintext challenge, fetches it through the pinned no-redirect
boundary, records immutable outcomes, and protects one global origin claim. It is
now wired through strict same-origin owner dashboard controls with transient proof
state and closed ownership projections. The production identity/resolver path is
not composed or qualified. A fixed, bounded Google Search Console `sites.list` protocol
can now discover and exactly match read-only properties, but OAuth, secret storage,
durable binding, and authorized provider success are absent. Signal still lacks
owner-ready deployed verified onboarding and GSC/GitHub/Telegram bindings. A
separate GitHub App adapter can now mint a short-lived one-repository
`contents: read` token and validate exact repository/base identity without a PAT;
server-side secret composition, durable binding, and authorized provider success
are still absent. Signal still lacks the
specialist workforce, complete dashboard workflows beyond the local audit path,
repository patch/PR delivery,
live verification, and pilot authority.

The preserved WordPress lab is valuable provider evidence. It is not the Core V1
user journey and does not grant CMS write authority.

## Milestone Sequence

| Milestone | Exit outcome | Current state |
| --- | --- | --- |
| M0. Pilot contract and resources | Revision 3.2, PRD, roadmap, target-resource manifest, one recipe candidate, scorecard draft, and preserved WordPress evidence | Contract revision in progress; resource manifest not executed |
| M1. Finish trusted foundations | Owner-ready identity/invitation lifecycle, supervised consumer, production `CrawlSite` terminal flow, restriction/restore safety, and operational scheduling | In progress |
| M2. Connections, evidence, and memory | Verified origin, GSC, GitHub App/repository, Telegram, crawl/performance evidence, business facts, scoped retrieval, dashboard shell, and data lifecycle | In progress: a disposable real-stack browser journey proves identity, organization selection, onboarding, a durable audit manifest, exact proof, and one verified-homepage evidence/finding path; full-viewport information architecture, evidence-empty charts, read-only GSC discovery, and read-only GitHub App repository inspection protocols exist; broad crawl observations, production identity/resolver qualification, provider OAuth/secret/binding success, and other resources remain |
| M3. Specialist workforce and planning | All nine versioned roles, bounded tools/costs, durable handoffs, disagreement/injection handling, competitor research, and one reviewed recipe | In progress: one Luna content responsibility proves bounded model I/O, durable run/call state, exact provenance, deterministic scope review, and owner supervision on one verified homepage; the other roles, tools, competitor research, evaluations, and durable planning workflows remain |
| M4. Human experience and control | Complete dashboard work/review/settings journeys and Telegram status/control/approval handoff backed by one durable truth | In progress: the local Work and verified Pages path now continues through Signal Chat proposal preparation, exact Approvals decisions, and an honest Changes delivery projection; arbitrary chat, Telegram, repository execution, and production execution remain absent |
| M5. GitHub PR safety and recovery | Isolated candidate build, independent review, exact approval, idempotent PR, CI/deploy observation, reconciliation, and conflict-aware revert PR | Planned |
| M6. Live verification and owner pilot | Bound live verification, later measurement, failure/restart matrix, fresh-browser journey, and signed usefulness scorecard on owner resources | Planned |
| M7. GA1 breadth | CMS teams and other advertised integrations, recipes, interfaces, operations, compatibility, and capacity evidence | Deferred beyond Core V1 |
| M8. Bounded autonomy and expansion | Narrow standing grants or added sites/resources only after separate qualification | Deferred beyond Core V1 |

Milestones describe dependency order, not permission to batch unreviewed work. M2
through M5 can have parallel test lanes only where their authority boundaries are
stable and each merged slice remains independently truthful.

## Core V1 Dependency Work

ADR-0052 changes the near-term implementation priority to coherent user-testable
vertical slices: evidence/findings, planning/proposal, review/approval, and then
PR/recovery. The production-hardening work below remains a parallel release gate,
not a prerequisite for exposing each authority-free local step. The numbered
sections retain dependency order for Core V1 admission; they do not require the UI
lane to wait for every production deployment control.

### 1. Close The Existing M1 Path

1. Package and qualify external supervision, health, and deployment around the
   implemented stoppable outbox/admission/start/record/ack consumer.
2. Complete `CrawlSite` execution by adding robots/global-origin admission,
   frontier and byte settlement, the real scope-enforcing crawler, final coverage,
   distributed artifact/key lifecycle, and worker build-ID rollout around the
   implemented workflow input, activity, heartbeat, timeout, retry, cancellation,
   terminal projection, replay, and qualified pinned-HTTP boundary.
3. Complete invitation delivery, resend, revocation, abuse controls, public identity
   transition, provider logout decision, and deployed cleanup schedules.
4. Implement independent restriction persistence/replay, recovery rotation, restore
   fencing, ambiguous-operation reconciliation, and a tested resume procedure.

Exit evidence: an authorized owner can start a harmless crawl through the supported
HTTP path, observe durable progress and terminal state through restart, and cannot
bypass or resurrect authority. This still grants no external write.

### 2. Establish The Core V1 Shell And Bindings

1. Qualify the implemented same-origin owner identity transition, active-site
   selection, and unverified site creation in the intended deployment, then add
   connection, overview, activity, and settings states.
2. Deployment-qualify the implemented exact public-origin ownership verification
   dashboard path with the controlled resolver, then add stale-proof handling and
   canonical-origin change policy.
3. Build on the fixed read-only GSC discovery protocol with least-privilege OAuth,
   owner property selection, token storage reference,
   revoke/reconnect, health, scope, and data-quality contracts.
4. Add GitHub App installation and exact repository/base binding, permission
   inventory, signed webhook ingress, replay protection, and read-only health.
5. Add dashboard-originated, expiring Telegram pairing; closed command schema;
   current membership checks; replay identity; and notification delivery state.

Exit evidence: a fresh owner browser can bind all four resources and see exact,
truthful health/degraded states. No role or connector may act beyond read-only
preflight at this stage.

### 3. Build Evidence And Scoped Memory

1. Complete production crawl scope enforcement, redirect/private-network defense,
   normalization, page inventory, evidence artifacts, and incremental refresh.
2. Import GSC data at its real source grains with import generations, quality flags,
   freshness, and idempotent reconciliation.
3. Collect Lighthouse lab results and available field evidence as distinct sources.
4. Implement approved business facts, goals, constraints, contradiction/supersession,
   keyword search, and bounded tenant/site-scoped semantic retrieval.
5. Implement retention, export, deletion, holds, and artifact-reference lifecycle
   for the data introduced above.

Exit evidence: dashboard evidence views can trace each conclusion to a scoped,
immutable observation and represent empty, partial, stale, or conflicting data.

### 4. Implement The Nine-Role Workforce

1. Define the typed delegation envelope and versioned input/output schemas for all
   nine Core V1 roles.
2. Implement the OpenAI adapter, prompt/tool release binding, per-role tool policy,
   token/cost reservation, durable run record, timeout, and sanitized failure path.
3. Implement bounded tools for crawl/GSC/performance/business-fact retrieval and
   public/licensed competitor research with provenance and injection separation.
4. Implement deterministic fan-out/join, disagreement retention, missing/invalid
   output handling, plan ranking, and escalation.
5. Create held-out, adversarial, missing-data, disagreement, permission, budget,
   and end-to-end evaluation cohorts for every role and the combined flow.

Exit evidence: all nine roles contribute to a durable, evidence-backed plan without
receiving approval, repository, or external-write authority they do not own.

### 5. Certify One Repository Recipe

1. Record the real framework, package manager, lockfile, runtime, allowed/forbidden
   paths, dependency policy, build/test commands, artifact identity, maximum patch,
   and recovery method.
2. Implement a pinned, isolated, credential-free repository checkout and candidate
   generator for that recipe only.
3. Add protected-path, symlink/submodule, archive/path traversal, script/network,
   resource, stale-base, oversized-diff, timeout, and build-failure tests.
4. Bind the immutable candidate, evidence, role releases, tests, expected live
   marker, inverse patch, and full cost into one approval manifest.
5. Enforce independent review: the verifier cannot modify the candidate under
   review, and repository generation cannot approve itself.

Exit evidence: Signal can prepare and explain one correct, build-tested proposal
for the owner site while external writes remain disabled.

### 6. Finish Dashboard And Telegram Control

1. Implement findings/evidence, plan/work queue, change review, activity,
   conversations, settings/safety, budgets, sessions, and connector diagnostics.
2. Implement complete loading/empty/stale/degraded/conflict/reconciliation/error/
   success/no-access states with responsive and accessibility verification.
3. Implement exact approval, rejection, pause, cancel, resume, and authenticated
   Telegram approval handoff through the same authority service.
4. Make Communicator output derive from committed records and prove dashboard/
   Telegram consistency under duplicate, reordered, delayed, and failed delivery.

Exit evidence: the owner can understand and control the complete proposed change
without inspecting logs or asking an operator to edit the database.

### 7. Qualify GitHub PR Delivery And Recovery

1. Add operation-first GitHub PR intent, stable identity, resource lease, budget
   hold, current-authority preflight, and independently durable write-intent record.
2. Create one branch/commit/PR with least-privilege credentials and explicit
   inability to merge, deploy, administer, or modify protected workflows.
3. Reconcile lost acknowledgement, retry, preexisting PR, rate limit, permission
   reduction, stale base, duplicate/forged/reordered webhook, and restart.
4. Observe CI and configured delivery provider without treating either as live
   success; bind observed artifacts to the approved commit where supported.
5. Generate a new reviewed three-way revert PR for recovery and block overlapping
   later edits.

Exit evidence: one exact approval causes at most one reconciled PR, later state is
truthful, and recovery never silently overwrites unrelated work.

### 8. Verify Live And Run The Owner Pilot

1. Verify the expected artifact and page behavior on the bound public origin, with
   explicit cache, redirect, timeout, wrong-commit, wrong-origin, and inconclusive
   outcomes.
2. Import a compatible post-change observation window and report measurement with
   data-quality and attribution limits.
3. Run the full positive, negative, failure, isolation, restart, restore,
   reconciliation, and recovery matrix from the PRD.
4. Run the complete fresh-browser owner journey and record usability,
   accessibility, operator, alert, support, cost, and review-effort evidence.
5. Complete the thresholds-fixed-in-advance usefulness scorecard and evaluate
   `CORE_V1_PILOT_ADMISSION` without retroactively narrowing failed criteria.

Exit evidence: either Core V1 earns bounded pilot admission or the gate records
specific blockers. It is not GA1 and has no CMS, merge, deployment, or standing
autonomy authority.

## Parallel Test Lanes

These lanes should mature alongside their dependent slices rather than being
postponed until the final pilot:

| Lane | Starts when | Must block |
| --- | --- | --- |
| Contract and schema | Before each API/event/role lands | incompatible or ambiguous state |
| PostgreSQL and RLS | With each authoritative record/function | cross-tenant/site access and unsafe concurrency |
| Workflow replay and faults | With each durable transition | nondeterminism, lost progress, blind retry |
| Real provider | Before relying on GSC/GitHub/Telegram/deployment behavior | unsupported scopes, identities, receipts, or recovery |
| Agent evaluations | With each role/tool release | invalid schemas, unsafe tool use, unsupported claims, unacceptable quality |
| Browser UX/accessibility | With each complete user step | inaccessible, misleading, or operator-only flows |
| Security and abuse | From public ingress onward | forgery, replay, injection, SSRF, confused deputy, resource exhaustion |
| Recovery and restore | From first authority record onward | stale authority, untracked ambiguity, data loss, unsafe resume |
| Observability and operations | With each deployable process | invisible failures, missing ownership, unbounded cost or latency |

The current M2 path includes a disposable real-stack browser composition for
Keycloak sign-in, PostgreSQL-backed organization selection, and owner site
onboarding. Its Work page now starts one current-site command, dispatches it through
the real outbox and local Temporal server/worker, and renders the committed
terminal projection from the current actor's bounded latest-work read. The
activity is no-network and synthetic. The path also includes the full-viewport dashboard information architecture,
evidence-empty chart modules, same-origin browser identity controls, a server-
verified tenant session, bounded hash-derived organization/site directories,
durable active-site selection, owner-controlled creation of an explicitly
unverified site, and an internal exact-origin verification API with immutable
attempt evidence and a protected global claim. Same-origin owner controls now issue
and verify that proof without exposing tenant or CSRF credentials to browser code.
The non-Overview destinations expose prerequisites rather than synthetic customer
records. The default production gateway and resolver remain unconfigured, and the
path intentionally stops before customer-resource and deployment qualification,
connector binding, public-site crawling, or any external mutation.

## Deferred Work And No-Go Boundaries

Only the explicit PRD deferrals are outside Core V1. In particular, CMS-specific
teams and production CMS writes wait until M7. Core V1 does not include GitHub
merge/deploy/admin authority, standing autonomy, GA4, multiple recipes, multi-site
scale, or bulk/destructive SEO changes.

Do not defer the nine roles, GSC, GitHub PR delivery, Telegram, the dashboard,
evidence/memory, competitor/content/performance/analytics work, independent review,
deployment observation, live verification, measurement, recovery, security, or
operability. Do not simulate a missing Core V1 dependency and call the owner pilot
complete.

## Change Control

A change to Core V1 scope requires all of the following in one documentation slice:

1. a new explicit specification revision when normative scope changes;
2. a superseding ADR with alternatives and consequences;
3. corresponding PRD, roadmap, status, index, and changelog updates;
4. repository checks that prevent stale or contradictory scope claims; and
5. no rewrite of earlier reviewed specification or implementation evidence.
