# Changelog

## 0171 - Source-Available Release Readiness - 2026-10-09

- Licensed Signal under the Elastic License 2.0 (`LICENSE`, `NOTICE`): self-hosting
  and modification are allowed, offering it as a hosted or managed service is not,
  and copyright and attribution notices must be kept.
- Added contributor and community files: `CLA.md`, `CODE_OF_CONDUCT.md`
  (Contributor Covenant 2.1), `SECURITY.md`, `SUPPORT.md`, `GOVERNANCE.md`, issue
  forms, a pull request template, `CODEOWNERS` and Dependabot configuration.
- Replaced "open source" with "source-available" in the README, `AGENTS.md` and the
  PRD, and added a contributor start section to `CONTRIBUTING.md`.
- Replaced private test-environment identifiers in the docs with placeholders.
  Product code and authority are unchanged.

## 0169 - Ask Signal Grounded Prose - 2026-10-06

- Replaced internal exact-only output with cited owner-addressed sentences, using
  Content Writer grounding/sensitive rules and deterministic record-token checks.
- Added one independently budgeted, batched entailment pass on the configured
  product model. Unverified prose falls back to exact excerpts; exhaustion and
  unknown outcomes remain explicit, with retained holds and no replay spending.
- Migration 0103 (written as 0102; it now composes on 0170's 0102) binds one verification identity to the existing pending private
  reply. No tables, provider, dashboard/API v1 change or publishing authority.
- One full gate passed 26/27 steps; its Ask Signal question-field error was fixed
  and the affected lab/fast suites rerun successfully (35 and 3,843 cases).
  Database 1,518 and npm 412 passed. Original gate remains recorded as failed;
  integrated qualification is pending. Live providers/deployment NOT_EXECUTED.
## 0170 - Fail-closed Permission And Lab Isolation - 2026-10-06

- Removed nullable permission comparisons and the compatibility flag in forward
  migration 0102 after 0101 (written as 0103); NULL and unknown authority inputs deny. Existing
  authorized decisions, credential ports, ACLs and operation bodies are preserved.
- Added independent entry-point equivalence, malformed-admission denial,
  divergence and transactional failure checks; 63 focused PostgreSQL cases pass.
- Isolated browser, crawler and page-attempt fixture subnets through public-address
  validation and atomic Docker IPAM; scoped candidate cleanup to each invocation.
  Product egress checks and sandbox controls are unchanged.
- Reproduced the reported 13-case browser failure with a deterministic multicast
  draw and the fixed-subnet collision; 85 concurrent browser/crawler/page/candidate
  cases now pass. The single final full gate passes all 27 steps, including 1,557
  database, 3,831 fast Python and 412 npm cases; live providers/composition
  NOT_EXECUTED. No authority or production write.
- Final cleanup review covers lost Docker creation acknowledgements without
  touching foreign resources: 22 focused tooling and 50 concurrent browser/crawler
  cases pass; Ruff rechecked, no full-gate rerun.

## 0168 - Cleanup - 2026-10-04

- Added the owner IndexNow key action: browser proof and fresh MFA, the existing
  reviewed recipe and exact build, then Candidate Inbox review. No direct write.
- Capability inventory follows composed gateways, including the newer connectors;
  missing configuration stays disabled. Removed misleading fallback state claims
  and unified owner-facing outcome labels without changing the dashboard design.
- Shared streamed, byte-bounded API relays reject cookie mutation while preserving
  each domain's response validation. Shared schedule, input and readiness helpers;
  test-only implementations moved out of production modules.
- Made observation backoff qualification use one database transaction clock;
  bounded browser/Temporal startup separately without extending action deadlines.
- Corrected stale dashboard/readiness documentation and duplicate status rows.
  No migration, new provider, deployment, publishing or standing authority.
- Focused qualification: 75 passed (delivery 12, Temporal 3, browser-worker 11,
  IndexNow 5, PostgreSQL 44); the standalone test-worker import is repaired.
  Full gate: DEFERRED_TO_MERGE_TRAIN (infrastructure: disk guard under three parallel tracks).
  Its single local invocation passed 8/27 steps, including npm 412 and Python
  3,113 cases. No live provider run, deployment or release is claimed.
## 0167 - Egress Declarations - 2026-10-04

- Consolidated Python outbound profile declarations, scoped admission ports and
  connector factory policies with a frozen before/after allow/deny oracle.
  Existing profile permissions, SQL gates and public contracts remain unchanged.
- Full gate DEFERRED_TO_MERGE_TRAIN (infrastructure: Temporal dev-server startup
  under parallel load), as directed by the reviewer. No full-gate pass is claimed.

## 0166 - Connector Framework - 2026-10-04

- Consolidated connector lifecycle, OpenBao OAuth custody, KV-v2 metadata and
  duplicate-key JSON parsing without changing provider scopes, public contracts
  or pending/durable revocation semantics. No SQL or dashboard changes.
## 0165 - Shared Permission Check - 2026-10-05

- Shared the deterministic permission decision behind distinct owner-session and
  worker-handle admission, preserving legacy domain and reconciliation behavior.
- Consolidated nine session/token hash adapters without broadening their errors.
- Added independent 0100 decision/ACL equivalence, owner/port divergence, private
  execution denial and transactional recovery tests; migration 0101 after 0100.
- Qualification: 1,486 database, 125 delivery, 19 journal, 26 Ask Signal,
  3,213 fast Python and 409 npm cases/checks pass; 15 new PostgreSQL permission
  cases and 108 hash-adapter cases included. Full gate DEFERRED_TO_MERGE_TRAIN;
  live providers/deployment NOT_EXECUTED.

## 0164 - SQL Dispatch Tables - 2026-10-04

- Replaced growing SQL enums, event shapes/reference dispatch, egress request and
  robots lists, and strategy wrapper chains with migrator-owned catalogs.
- Preserved exact request restrictions, authority, RLS, grants and NULL semantics;
  added independent 0099 equivalence, new-row extension and rollback tests.
- Migration 0100 follows unchanged 0099; no new app tables or production authority.
- Corrected-tip qualification: 48 focused PostgreSQL, 1,471 database, 60 Webflow
  and 84 provider-data cases pass; earlier scoped checks are recorded in evidence.
  Full gate DEFERRED_TO_MERGE_TRAIN; live providers/deployment NOT_EXECUTED.

## 0163 - Ask Signal - 2026-10-04

- Added Ask Signal: a top-bar drawer and a conversation page where the owner
  asks about their site. Answers cite the records they came from and link into
  the existing owner flows; nothing is executed from a chat.
- Conversations and memories are kept by the API, so they persist. The owner can
  see, add and forget what Signal remembers.

## 0161 - Prototype Parity - 2026-10-04

- Rebuilt every screen of the approved Direction C prototype from real data:
  Home metric tiles, six-stage loop, 90-day chart with change markers and AI
  citations grid; one Inbox for fixes, articles and facts with a search-result
  preview; a day-grouped activity log; Results with what each change did; the
  content board; connection tiles; autonomy levels; and a site switcher.
- Missing data is stated in words; no prototype sample numbers are shown. Every
  decision still sends the existing command, and authority is unchanged.

## 0160 - Strategy and Results UI - 2026-10-04

- Finished the dashboard redesign on Strategy, Search results, the weekly report
  and change measurements: plain-language states and headings, priority formulas
  and evidence identifiers under Rationale or Technical details, and one
  collapsed list of unavailable sources instead of the same block on every page.
## 0162 - Ask Signal Backend - 2026-10-04

- Added private, resumable site conversations and seven bounded, session-scoped
  assistant routes with browser mutation proof, forced RLS and 365-day retention.
- Added committed-record-grounded, budgeted medium-effort answers with citations,
  honest unavailable/unknown states and confirmation links to existing owner
  controls. Chat cannot approve, change authority, merge or deploy.
- Added source-linked preference/context memory, bounded rolling summaries,
  PostgreSQL-ranked retrieval and audited forgetting. Business statements become
  proposed Business Brain facts requiring Inbox approval, never approved memory.
- Backend only: dashboard BFF/UI are reviewer-owned. Live gateway, deployment and
  the inactive-scope cleanup timer remain unqualified. Migration 0099 follows 0098
  (merge train 5).

## 0155 - Data Providers - 2026-10-04

- Owner/weekly Strategy can research bounded topics through DataForSEO, reserving
  monthly and human standing spend/volume caps first. Unknown outcomes retain
  holds; dated volume, competitors and optional backlinks never predict rankings.
- Added current-owner/fresh-MFA Bing API, BFF and card under More data. Weekly
  imports include top-page performance; Strategy and measurement retain separate
  as-reported cohorts, incomplete coverage and named-position caveats.
- Migration 0098 follows 0097 (merge train 5; written as 0093 after 0092); no existing
  egress profile, dependency or publishing authority was broadened. Live
  qualification/deployment remain outstanding.

## 0159 - Invitation revocation - 2026-10-04

- Owners with current five-minute-fresh MFA, browser proof and a verified selected
  site can revoke pending invitations after confirmation in Team settings.
- Migration 0097 (merge train 5; written as 0091) adds a guarded pending-to-revoked transition, immutable audit and
  independent authority-journal restriction. Revoke/accept races have one winner;
  revoked or restored-revoked links receive the same generic acceptance denial.
- Local revocation stays effective during journal outage, with recovery confirmation
  visibly pending until acknowledged. Accepted members, roles and issuance/delivery
  boundaries are unchanged; no production authority is added.

## 0158 - Team invitations - 2026-10-04

- Added current-owner, fresh-MFA, verified-site invitation issuance and an
  owner-only Team section with invitation history and current site members.
- Added fresh-browser invitation acceptance using existing purpose-bound OIDC
  proofs, then ordinary sign-in and exact invited-site selection before Home.
  Dashboard passwords, owner invitations and account-existence disclosure remain
  prohibited. Migration 0096 (merge train 5; written as 0090) adds guarded functions only.
- Show a one-time share link and explicitly state it was not emailed: the existing
  verified-recipient, token-free report email boundary cannot send invitations.
  Unsupported revoke/resend controls are absent; no production authority is added.
## 0156 - Publishing Owner Paths - 2026-10-04

- Added bounded Webflow owner OAuth, confirmed collection mapping, sealing from
  exact owner-approved articles and revocation, with fresh MFA, browser mutation
  proofs, OpenBao-only credentials and unchanged pending durability semantics.
- Added a Webflow connection card under Content sources and publishing. Replaced
  WordPress internal-ID inputs with named current bindings and Article selections;
  initial credentials and binding remain operator-provisioned.
- Migration 0095 follows 0094 (merge train 5; written as 0090 after 0089). Owner egress admits only scoped OAuth/revocation
  and validation reads; Webflow's lab-only deliver gate is unchanged. Live CMS
  qualification and production publishing remain unavailable.
## Slice 0154 - AI Answers Completeness - 2026-10-04

- Added owner question proposal/edit/approval paths from the latest committed crawl,
  with immutable versions, fresh MFA, bounded owner data and exact replay/stale checks.
- Added accepted structured-data proposals into the Candidate Inbox through the
  existing approved-fact grounded recipe and isolated build boundary. Separate exact
  Inbox approval is still required; no PR, merge or deploy happens here.
- Replaced stale recipe/scheduler placeholders with current readiness and corrected
  historical reachability claims. Kept dashboard and weekly-loop conflict boundaries.
- Local qualification and live limitations: [0154](docs/implementation/0154-ai-answers.md).

## 0157 - Dashboard Cleanup - 2026-10-04

- Fixed DataForSEO settings, which read an environment variable no deployment
  sets and so were always unavailable; added a regression test.
- Removed an orphaned fixture-analysis route, dead CSS and unused custom
  properties; replaced leftover "Business Brain", "Content Writer" and "AI
  visibility" names; Home and Settings now share one list of health-check names.

## Documentation Truth - 2026-10-04

- Repaired the status table (two blank lines had ended it early, so later rows
  rendered as plain text) and removed a duplicated row.
- Replaced the stale "review 0104" next work and the 0125–0127 next slice with
  the current sequence, refreshed the README status banner from the status
  table, named the model provider the code uses, and rewrote the dashboard
  README for the redesigned dashboard.

## 0149 - Owner Paths - 2026-10-04

- Owners can grant, finish checking and revoke scoped GitHub PR permission from
  Connections, with fresh MFA, verified site/read binding, an optional Home step,
  exact scope in Technical details and explicit unknown states. Revocation uses
  the independent restriction journal and restored-primary deny-only replay;
  pending recovery protection is never shown as durable success.
- Home and Autonomy now expose weekly pause/resume from stored current state;
  resume does not restore revoked allowances. Owners can reach the AI-visibility
  schedule, and Activity no longer invents a disconnected GitHub state.
- Non-pilot empty Inbox copy no longer sends owners to hidden Chat. Reports and
  alerts link to Activity rather than the hidden Work placeholder.
- Migration 0093 follows 0092; no merge, deploy, default-branch write, CI edit,
  secret read, production write or live-provider readiness is enabled.

## 0148 - Plain-Language Messages - 2026-10-04

- Sign-in, organization, site and connection notices now say what happened in
  plain words ("Nothing was unlocked. Try signing in again.") instead of system
  terms, with the same meaning; a failed state is still never shown as success.
- The error screen no longer repeats the organization failure in a second card.

## 0147 - Activity and Pages Evidence - 2026-10-04

- Activity shows each change as a card: a status chip with only the recorded
  fact, the page, file, decision and approval channel in words, and the delivery
  track; hashes and identifiers sit under Technical details and Delivery evidence.
- Pages shows the latest crawl in plain words with the exact manifest under Audit
  manifest, and "Verify origin first" now leads to the site panel.

## 0146 - Dashboard Pages, Phase 2a - 2026-10-04

- Home gains a setup checklist for owners, built from the connection states the
  page already loads; a state that could not be read says so. Phones get a bottom
  tab bar for Home, Inbox, Activity and Results.
- Connections are grouped by purpose with worded status pills; Autonomy, Business
  facts, Articles, AI answers and Settings read in plain language, with exact
  identifiers kept under Technical details. Chat no longer says "Not paired" when
  Telegram is paired. Forms, request bodies and authority are unchanged.

## 0145 - Dashboard Redesign - 2026-10-04

- Redesigned the dashboard as an employee reporting to its owner: a Home with
  real decision counts, the weekly loop's recorded stage outcomes in plain words
  and an honest health summary; grouped navigation without placeholder
  destinations; an Inbox decision that explains the change and keeps every exact
  identifier in Technical details. New porcelain, ink and Signal-blue visual
  system with self-hosted OFL fonts; `font-src 'self'` is unchanged.
- Fixed contradictory copy: the Chat model name, the real Telegram pairing,
  owner authority wording and the account menu's explicit Sign out. Data
  loaders, forms, decision semantics and authority are unchanged.

## Slice 0140 - Proactive Internal Linking (G2) - 2026-10-04

- Build deterministic committed-crawl link graphs and evidence-backed weak/orphan
  opportunities without claiming complete-site coverage.
- Add owner-reviewed, never-autonomous exact existing-paragraph link candidates,
  per-source weekly caps, duplicate/anchor/boilerplate refusals and paired exact
  build assertions. Keep the five-key A2 set and all publishing authority unchanged.
- Register candidate preparation in the existing standing-authorized weekly loop;
  preserve learning projections, owner approval, shared egress and unknown holds.
- Migration 0090 follows 0089. Qualification and live NOT_EXECUTED limits are in
  [0140](docs/implementation/0140-internal-links.md).
- Focused qualification passes 133 Python and 263 dashboard cases. The full gate
  is DEFERRED_TO_MERGE_TRAIN by reviewer direction due to concurrent slice 0144
  Temporal/lab collisions. The earlier attempt passed six steps, failed both
  delivery shards on Temporal startup, and cancelled 15; no shard pass is claimed.

## Slice 0141 - Keyword and Topic Ideas (G3) - 2026-10-04

- Cluster observed queries deterministically with Telugu/English-safe tokens,
  explained membership, evidence metrics/pages and conservative gap signals.
- Feed opportunities through existing strategy, weekly registry and capped
  unaccepted briefs; preserve learning, approval and delivery gates.
- Add optional budgeted Luna hypotheses labelled `idea, no volume data` and
  separately attributed completed DataForSEO volumes, with explicit unavailability.
- Add owner keyword ideas inspection and existing decisions; 0091 follows 0090.
  No new authority, paid lookup, secret path or egress profile.
- Merge integration: use four distinct observed topics in the weekly two-brief
  cap fixture, retaining every authority and replay assertion; separately verify
  related queries still cluster together. Runtime behavior is unchanged.

## Slice 0144 - Weekly Loop Wiring

- Wire bounded PSI research, capped visibility re-observation and verified owner
  chat reports into the standing-scoped weekly registry without owner sessions.
- Register durable health alerts through existing capped, verified chat delivery;
  retain email behavior, unknown-outcome holds and current-recipient checks.
- Preserve reported Bing page evidence in measurement windows and place Astro
  IndexNow keys in the configured static directory with offline A4 build proof.
- Add one bounded KV v2 readiness helper to OpenBao lab provisioning.
- Merge integration: retain 0140's internal-link stage and reviewed-candidate
  constraints when 0092 adds observation/chat stages and Astro IndexNow keys;
  reject unknown stages and keep the A4 exception restricted to non-autonomous keys.
- Full gate DEFERRED_TO_MERGE_TRAIN: 20/23 steps passed, 4,853 test executions
  passed and four failed. The three unchanged browser/Temporal startup and
  observation-backoff failures were reviewer accepted as load-induced pending
  the merge-train gate. The slice-caused email-test ordering assumption is
  corrected and its four PostgreSQL cases pass. No passing full gate or release
  readiness claimed; live providers and deployment NOT_EXECUTED.

## Merge Train 3 Integration

- Preserve editorial refresh measurements and guarded weekly sources in learning
  projections; exclude explicit new-page baselines from comparative feedback and
  priority bonuses without fabricating a pre-change window or causal effect.

- Integrate weekly Brain extraction with source-bound monthly model reservations
  under the recorded standing cost ceiling; retain owner-only cap-setting,
  current authority, guarded egress and persistent unknown holds.

- Preserve built-adapter character offsets alongside static/grounded UTF-8 byte
  offsets, bind grounded dispatch to its exact reviewed release, and retain
  scheduling payload rejection without asserting the new route is absent.

## Train 3 - Private Lab Report Integration - 2026-10-04

- Integrated Train 2's browser-worker, self-host and Webflow labs with 0138's
  opt-in invocation-private reports and exact JUnit counts. Standalone behavior,
  tests, cleanup authority and safety limits are unchanged.
- Normalize the graceful lab wrapper's entrypoint path so inherited browser
  source-hash checks see an absolute `__file__`; preserve arguments and exits.

## Train 2 - Measurement Fixture Determinism - 2026-10-03

- Made inherited 0094 import fixture timestamps explicit rather than assuming
  insertion order implies increasing wall-clock time. Added both insertion orders
  for equal-timestamp GSC/Bing UUID selection and retained exact cutoff assertions.
  The production selector and frozen migrations are unchanged. Separate fix on
  #24; see [0094](docs/implementation/0094-change-measurement.md).

## Slice 0143 - Next.js Metadata Delivery (F2) - 2026-10-03

- Added parser-proven App Router metadata and Pages Router `next/head` literal
  recipes, preserving quote style and all surrounding source bytes.
- Reused paired offline static-export receipts, exact all-HTML/artifact scope and
  signed reviewed releases with dashboard Owner/fresh-MFA-only approval/dispatch.
  Bounded layouts/dynamic routes and multi-page edits require A4; never autonomous.
- Kept server mode, generated/imported/template metadata and network-needing
  builds unavailable. No configuration/workflow/protected-path edit or network
  fallback. Migration 0082 follows 0081 with no new tables or dependencies.
- The full local gate passed 3,226 test/check cases with zero failures/skips.
  Qualification and live NOT_EXECUTED items are recorded in
  [0143](docs/implementation/0143-nextjs-metadata-delivery.md).

## Slice 0139 - Front-Matter Content Adapter (F1) - 2026-10-03

- Added parser-proven YAML/TOML/JSON metadata recipes for Markdown/MDX/Nunjucks,
  preserving formatting, BOM, CRLF and content bytes.
- Reused Astro collections, offline receipts, exact all-page scope, A4 multi-page
  escalation and dashboard Owner/fresh-MFA-only authority; never autonomous.
- Detected Hugo/Jekyll but kept delivery unavailable without pinned offline
  toolchains. Layouts/configuration/workflows/protected paths stay denied.
- Migration 0081 follows 0080 without new tables or historical migration edits.
  The full local gate passed 3,142 test/check cases in [0139](docs/implementation/0139-front-matter-content-adapter.md);
  live providers/generator compatibility and production remain NOT_EXECUTED.

## Slice 0127 - Owner-Approved Astro Delivery - 2026-10-03

- Added closed Astro source recipes with conservative A2/A4 classification for
  static pages, collections, dynamic routes and shared generators/templates.
- Added paired offline build proofs, exact all-page/artifact scope assertions,
  immutable impact counts/sample diffs and the existing Inbox review surface.
- Required dashboard owner approval and five-minute MFA for every Astro PR;
  standing dispatch remains denied, including owner-accepted unprotected bases.
- Kept all workflow, protected-path and ref-only write guards. Live compiler,
  customer and provider qualification remain NOT_EXECUTED. Recorded the absent
  0086 Astro `publicDir` IndexNow follow-up in
  [0127](docs/implementation/0127-astro-delivery.md) and ADR-0122.

## Slice 0126 - Astro Dependency And Build Boundary - 2026-10-02

- Added bounded Astro configuration, output-directory and collection observation
  without executing repository configuration on the host.
- Added a closed GET-only official registry profile, integrity-verified bounded
  worktree cache, and credential-free offline install/build with no lifecycle
  scripts or network-enabled fallback.
- Added immutable lockfile/all-built-HTML receipt evidence, atomic failure and
  replay checks. Astro delivery and every Astro patch remain unavailable.
- Scope, bounds, commands and live limitations are recorded in
  [0126](docs/implementation/0126-astro-build-boundary.md) and ADR-0121.

## Slice 0125 - Owner-Accepted Unprotected Default Branch - 2026-10-02

- Added the narrow, hash-protected Revision 4.1 amendment and ADR-0120 without
  modifying accepted specification baselines.
- Added migration 0076 after 0075 with immutable owner/fresh-MFA acceptance and invalidation receipts, an
  unchecked explicit dashboard command, and a persistent protection warning.
- Kept normal binding denial, preferred later protection, and invalidated
  exceptions on provider identity, branch, installation, permission or recovery
  drift. Standing dispatch always requires actual protection; each exception PR
  requires exact dashboard owner Inbox approval. Existing guards are unchanged.
- Qualification and live limitations are recorded in
  [0125](docs/implementation/0125-unprotected-base-acceptance.md).

## Slice 0096 - AI-Visibility Optimization Agent - 2026-10-03

- Added deterministic evidence-linked question gaps, per-provider/model/date
  history and approved-fact recommendations with reused Content Writer grounding
  and originality checks. Owner acceptance uses existing proposed brief flows;
  acknowledgments create no candidate or external authority.
- Added owner API/dashboard and migration 0078 after unchanged 0062. Broader
  structured-data candidates and scheduled re-observation were unavailable at that
  checkpoint. 0131 added their internal foundations; 0154 adds owner question and
  candidate producers. Live providers remain NOT_EXECUTED. See
  [0096](docs/implementation/0096-ai-visibility-agent.md).
- Recorded one unchanged, load-sensitive Business Brain extraction failure
  (`RobotsSnapshotUnavailable`) and the owner-requested full rerun: 893 passed.
  The test and its shared crawler fixtures remain unchanged.

## Slice 0129 - Bing Per-Page Performance Import - 2026-10-03

- Added the official GetPageStats read to the existing bounded Bing OAuth profile,
  preserving page clicks, impressions, both named integer position fields and
  provider dates. Unknown date granularity and incomplete coverage stay explicit.
- Added migration 0080 and a current-binding ingest read port with strict row,
  exact egress evidence, role/isolation and revocation checks. Other-host rows
  are dropped and counted; credentials never become generation data.
- Kept 0094 unchanged and recorded its follow-up. Live Bing and production
  composition remain NOT_EXECUTED; no writes or measurement readiness enabled.

## Slice 0130 - Self-Host Package - 2026-10-03

- Added a generic digest-bound HTTPS-only private self-host deployment candidate,
  encrypted quorum bootstrap, hidden owner-key storage, fresh signed OTP first-owner
  creation, tmpfs hydration and explicit migration/image upgrade procedures.
- Reused existing identity, OpenBao, PostgreSQL, dashboard/ingress/worker and backup
  primitives without changing the qualified integration environment's defaults.
  Missing and configured unqualified providers stay visibly unavailable.
- Added real disposable PostgreSQL/OpenBao safety tests, tooling/topology checks
  and [the self-host runbook](docs/self-host.md). Full-stack/live qualification and
  production admission remain NOT_EXECUTED/NOT_CERTIFIED; no new migration or
  external-write authority. See [0130](docs/implementation/0130-self-host.md).
## Slice 0138 - Faster, Steadier Verification - 2026-10-03

- Added deterministic, complete database test shards on independently disposable
  PostgreSQL instances and combined JUnit results; default three, bounded to six.
- Isolated synthetic provider admission state between Business Brain extraction
  tests without changing assertions, production cooldowns or provider profiles.
- Added a bounded full-gate runner with per-step deadlines, disk admission guards,
  private temporary data, owned-artifact cleanup and machine-readable counts.
- Qualified all 20 steps: 3,308 cases and 20 checks passed. Full wall-clock time
  fell from 45:36 to 19:50 on the shared local machine. No migration, dependency,
  product behavior or production capability change. See
  [0138](docs/implementation/0138-fast-gate.md).

## Slice 0128 - Page Speed And Core Web Vitals - 2026-10-03

- Added exact verified-site PSI v5 mobile/desktop requests through shared egress,
  optional OpenBao-only query credentials, keyless quota and honest rate-limit states.
- Added immutable evidence-linked lab and separate URL/origin CrUX observations,
  unavailable reasons/collection periods and observation-only poor CWV findings.
- Bounded the internal weekly sample to five pages and daily provider admission
  to four requests; added current-owner read API and source-labelled Pages dashboard.
- Live PSI remains NOT_EXECUTED, local sandbox Lighthouse unavailable, and no
  unattended production timer or automated fix recipe is enabled. See
  [0128](docs/implementation/0128-page-speed.md).

## Slice 0131 - Grounded Structured Data And Visibility Schedule - 2026-10-03

- Added a separate owner-reviewed static JSON-LD recipe with closed crawl/Brain
  grounding, exact baseline/candidate output checks and no A2 eligibility.
- Added internal owner schedule settings/history and per-site Temporal re-observation
  through existing assistant boundaries, with immutable pre-I/O integer holds,
  visible unavailability and no paid redispatch after worker loss.
- This checkpoint had no owner question producer or visibility-proposal recipe
  producer; 0154 adds those owner paths. Schedule controls on the owner's page are
  tracked separately in 0149.
- Billing remains unpriced; all dispatched holds carry forward conservatively.
  Live providers and deployed composition remain NOT_EXECUTED. See
  [0131](docs/implementation/0131-structured-data-and-visibility-schedule.md).

## Slice 0133 - Weekly Chat Reports and Alerts - 2026-10-03

- Added optional Slack-channel/linked-owner-DM and paired-private-Telegram weekly
  report/alert delivery through the existing shared gateway and bot profiles.
- Added current-owner per-channel opt-in/out, token-free bounded rendering,
  idempotent outbox intents, shared site/provider daily attempt caps, immutable
  suppression/history and terminal unknown outcomes; reports carry links, no approvals.
- Added owner Settings preferences/history and real PostgreSQL/provider-double
  qualification. See [0133](docs/implementation/0133-chat-reports.md) for exact
  gates and limitations. Live Slack, Telegram and deployed pumps NOT_EXECUTED;
  no profile widening or production authority.

## Slice 0136 - Writing Quality and Claim Grounding - 2026-10-03

- Added operator-configured GPT-6 Luna roles, stable style/voice prompts,
  four-pass articles and one budgeted quality regeneration with explicit flags.
- Added Content Writer claim-level citations and reduction-only Jev/high-effort Luna entailment;
  sensitive claims keep strict matching and owner review. Originality is retained.
- Older standalone metadata/technical-recipe grounding contracts are not migrated;
  that C2 scope decision remains open and the slice is not marked complete.
- Added immutable monthly model reservations, USD 25 default/80 percent warning,
  visible exhaustion and owner review projections. No publishing or autonomy
  grant; live providers and production composition remain NOT_EXECUTED. See
  [0136](docs/implementation/0136-writing-quality.md).

## Slice 0135 - Weekly Skill Orchestration - 2026-10-03

- Added weekly imports, changed-only proposed facts, new strategy snapshots,
  capped unaccepted briefs and verified-recipient email queueing under current
  human standing authority and recovery generation. The 0104 path is unchanged.
- Added narrow ports, atomic reservations, immutable outcomes and latest-cycle
  owner status. Unknown work is not redispatched; reserved budget is not reported
  as reconciled spend. The full local gate passed; live providers remain
  NOT_EXECUTED. See [0135](docs/implementation/0135-weekly-orchestration.md).

## Slice 0137 - Observed Learning and Page Decay - 2026-10-03

- Added site-local 28/90-day observed effectiveness, explicit sample sizes,
  confounder weighting, neutral small-sample shrinkage and bounded explained
  strategy influence; no causal claims or cross-site learning.
- Added evidence-only material page decline screening, preferred year-over-year
  comparison, seasonality flags, recent-change exclusions and unaccepted Content
  Writer refresh proposals. Missing/partial page-days remain unknown.
- Added owner Overview/Analytics effectiveness and declining-page evidence views.
  Migration 0088 extends existing read sources only; full local qualification passed.
  Live providers and production writes remain unqualified. See
  [0137](docs/implementation/0137-learning-and-decay.md).

## Slice 0142 - Health Monitoring and Alerts - 2026-10-03

- Added bounded scheduled health observations with honest unknown states for
  missing probes, stale evidence, optional providers and unavailable budgets.
- Added durable episode deduplication and rate limits, existing verified-owner
  email intents and a clean durable registration point for 0133 chat delivery;
  no new egress profile or authority is introduced.
- Added an owner-only Settings health panel and literal-loopback private health
  projection. Public Caddy health routes remain 404. Migration 0089 follows 0088.
  See [0142](docs/implementation/0142-health-monitoring.md) for local evidence and
  explicit live delivery/deployment exclusions.

## Integration Privacy Correction - 2026-10-02

- Removed personal and deployment identifiers from the unpublished 0105-0122
  tree. Exact owner, origin, network, repository, workspace and OAuth/App targets
  now come from protected operator configuration and fail visibly closed when
  missing or invalid. Provider secret storage remains in private OpenBao.
- Preserved all authority, MFA, session, PKCE, CSRF, TLS and egress restrictions;
  tests now pin placeholders or strict configuration-loading contracts.
- Added [the private environment runbook](docs/runbooks/integration-environment.md)
  and documented the owner's Search Console consent restart. No sign-in or
  consent was performed by an operator during this correction.
- Rebuilt the unpublished history with the approved noreply identity and retired
  the old local branch after a zero-match patch/metadata audit. Deployed scrubbed
  images through the approved private recovery, revoked temporary credentials,
  and requalified the preserved Owner/MFA session, exact bindings, fresh GitHub
  inspection, callback rejection and private IPv4/IPv6 isolation. GSC consent
  and Slack interactivity remain unqualified; production writes stay disabled.

## Slice 0122 - Dedicated Provider Callback Qualification - 2026-10-02

- Deployed the committed callback repair and qualified the real Slack OAuth
  installation and exact protected GitHub read binding, with fresh inspection.
- Completed explicitly approved test-repository visibility/protection and exact
  Google test-property HTML-file ownership; Search Console consent is pending.
- Preserved the normal Owner/MFA session, rechecked private isolation and
  explicitly retired the narrow deployment operator. Slack signed interactivity
  and production writes remain unqualified. See
  [0122](docs/implementation/0122-dedicated-provider-callback-qualification.md).

## Slice 0121 - Test Callback Metadata and Binding Failure - 2026-10-02

- Repaired dedicated Next callback URL metadata while keeping the exact origin
  guards and private HTTPS listener unchanged.
- Records an observed unprotected GitHub branch as a durable failed binding,
  rather than leaving it prepared; failed retries do not repeat provider calls.
- Local qualification passed; live replacement and positive provider
  qualification remain pending. See
  [0121](docs/implementation/0121-test-callback-metadata-and-binding-failure.md).

## Slice 0120 - Live Test Connector Runtime - 2026-10-02

- Deployed the committed connector API through approved private quorum recovery,
  qualified workload credentials, encrypted database backup and migration 0062.
- Restored the secure listener, retired the temporary root, qualified pinned
  network/isolation and preserved the human's normal eight-hour Owner/MFA session.
- Added an explicit test-only capability qualification profile without claiming
  connections or enabling work. Actual GitHub/Slack attempts exposed failures
  requiring repair; the full integration request is not complete. See
  [0120](docs/implementation/0120-live-test-connector-runtime.md).

## Slice 0119 - Private Connector Deployment Preparation - 2026-10-02

- Qualified narrow existing-operator issuance and restrictive provisioning-failure
  cleanup, with an encrypted new-key backup after temporary-root retirement.
- Built the committed connector images on the existing VM and deployed only the
  dashboard; actual negative HTTPS callback and anonymous-route checks passed.
- Requalified public/private isolation and fixed denied GSC/Slack callbacks;
  rejects downloads, credential cookies and redirects claiming a connection.
- API deployment, private quorum provisioning and positive provider flows remain
  pending explicit owner decisions. No integration-complete claim is made. See
  [0119](docs/implementation/0119-private-connector-deployment-preparation.md).

## Slice 0118 - Owner Connector Browser Flows - 2026-10-01

- Composed current MFA-owner GSC and read-only GitHub setup, strict same-origin
  BFF/callbacks, safe binding projections and truthful dashboard controls.
- Restricted the dedicated runtime to the one approved property and repository;
  preserved protected-branch, private TLS, shared egress and production gates.
- Qualified 874 real PostgreSQL, 1,417 API/identity/tooling, 39 repository and
  153 dashboard cases, plus desktop/mobile layout fixtures. Live deployment and
  actual provider connections remain pending, not complete. See
  [0118](docs/implementation/0118-owner-connector-browser-flows.md).

## Slice 0117 - Dedicated Connector Composition - 2026-10-01

- Added optional current-owner Slack runtime composition for the exact test site
  and Sample, with request-time gateway resolution and bounded connection cleanup.
- Prepared narrow renewable AppRoles, a separate encrypted robots key/volume and
  screened, API-source-only provider pins with persistent all-state expiry.
- Qualified disposable TLS/Raft workload ACLs, 1,407 API/identity/tooling and
  858 real PostgreSQL cases. Live provider/runtime qualification and
  the complete integration request remain unfinished. See
  [0117](docs/implementation/0117-dedicated-connector-composition.md).

## Slice 0116 - Owner Connector Egress - 2026-10-01

- Added a distinct current MFA-owner onboarding context through the existing
  shared pinned HTTP, robots parser and global origin admission controls, without
  inventing a crawl, workflow or standing grant.
- Added function-only immutable owner-operation receipts, closed provider paths,
  encrypted proved robots evidence and fail-closed uncertainty/replay handling.
- Local regression qualification passed. No live provider or callback is
  enabled; the complete integration request remains in progress. See
  [0116](docs/implementation/0116-owner-connector-egress.md).

## Slice 0115 - Dedicated Test Origin Proof - 2026-10-01

- Composed normal owner ownership proof for only the approved test origin using
  screened expiring public pins and existing bounded, redirect-free TLS fetching.
- Qualified real public proof and database evidence, denied-peer failure without
  invented ownership, Internet/metadata denials and unchanged private administration.
- Slack/GSC/GitHub runtime and all external writes remain unavailable; ongoing
  connector setup is not complete. See [0115](docs/implementation/0115-dedicated-test-origin-proof.md).

## Slice 0114 - Dedicated Test Site Onboarding - 2026-10-01

- Composed existing protected site discovery, owner creation and selection for
  only the approved test origin, preserving MFA, tenancy, CSRF and recovery.
- Qualified actual Safari creation/selection and PostgreSQL readback, anonymous
  denials, exact-origin/failure tests and unchanged public/private TLS isolation.
- Provider runtime, site verification, commands and external writes remain
  unavailable. Ongoing integration work is not complete; see
  [0114](docs/implementation/0114-dedicated-test-site-onboarding.md).

## Slice 0113 - Private Test Owner Bootstrap - 2026-09-30

- Implemented a private, expiring signed-assertion capture and separately
  human-invoked create-only test-owner bootstrap, requiring actual issuer/subject,
  consumed database nonce and fresh signed completed-OTP proof. The public API
  receives no provisioning authority; standing grants and external writes remain
  disabled.
- Qualified local proof/rollback/RLS/failure boundaries and renewed the four
  identity-only Google pins with real TLS and unrelated-peer denials. Qualified
  the actual Google/OTP assertion, persistent rollback rehearsal, isolated owner
  creation and subsequent normal Owner/MFA browser workspace session. Corrected
  the dedicated API's missing session/directory admission using an exact method
  and path allowlist. Retired proof capture; deployed through approved private
  quorum recovery, restoring the secure listener and revoking/denying temporary
  root and operator. No site, standing grant or connector capability was enabled. See
  [0113](docs/implementation/0113-private-test-owner-bootstrap.md).

## Slice 0112 - Incomplete Test Identity Recovery - 2026-09-30

- Removed only the human-approved, incomplete test identity after authenticated
  protected backup, real PostgreSQL delete/restore rollback, and exact-snapshot
  failure checks. Retained bootstrap retirement and required MFA; no Google
  account, provider link, credential or application authority changed.
- Repaired the timed-out callback's omitted fixed Google user-info peer; added
  bounded credential-free response diagnostics and non-download HTML failures.
  Real Google linkage and human OTP enrollment are confirmed. Signed Signal owner
  session and connector runtime qualification remain pending.
  See [0112](docs/implementation/0112-incomplete-test-identity-recovery.md).

## Slice 0111 - Dedicated Test Login Ingress - 2026-09-30

- Added persistent private Signal DB/API/Next composition, exact renewable
  AppRoles, independent recovery read, bootstrap retirement and required signed
  completed-OTP session policy. Patched Next to 16.3.8 before publication.
- Published only the approved DNS-only test hostname with free public TLS;
  qualified actual login/PKCE and private-route/port isolation. Fixed real HTML
  form Origin handling without weakening CSRF or exposing callback referrers.
- Qualified fixed expiring Google identity peers and denied other Internet and
  metadata traffic. Human OTP/owner session and Slack/GSC/GitHub runtime are
  pending; unqualified capabilities and external writes remain disabled. See
  [0111](docs/implementation/0111-dedicated-test-login-ingress.md).

## Slice 0110 - Private Test Identity - 2026-09-30

- Deployed pinned optimized Keycloak and a separate persistent TLS PostgreSQL
  database on the existing VM, with private ingress, nonroot bounded containers,
  create-only audited infrastructure secrets and blocked outbound traffic.
- Qualified exact realm/client/Google broker configuration, TLS/PKCE/state/grant
  denials, database privilege denial, container persistence and missing-secret
  startup failure. Public callbacks, Google consent/MFA, application composition
  and recovery/bootstrap gates remain pending; see
  [0110](docs/implementation/0110-private-test-identity.md).

## Slice 0109 - Test Provider Registration - 2026-09-30

- Created the separate exact-callback Google Web clients, saved the sole approved
  test user while retaining Testing mode, and imported both configurations into
  audited private OpenBao with real read-only/denial/revocation checks.
- Verified GitHub's read-only one-selected-repository installation, privately
  imported the owner-generated App key with real ACL/revocation checks, and captured
  a fresh encrypted secret-store backup without replacing the older recovery pair.
- Independently verified owner-entered Slack client/signing configuration and
  captured a post-Slack encrypted backup. Slack installation and public
  application/callback deployment remain pending; see
  [0109](docs/implementation/0109-test-provider-registration.md).
- Confirmed the owner's retired Mac IPv6 alias removal and a fresh approved-source
  SSH connection without expanding administrative access.

## Slice 0108 - Private Connector Configuration - 2026-09-30

- Added operator-only, create-only app imports with strict private-file and
  exact Google callback validation, CAS-protected read-only policies, revoked
  verification tokens and explicit unknown outcomes. Qualified on real local
  TLS/Raft OpenBao with synthetic data; no provider authorization is claimed.
- Saved the approved Slack redirect, enabled Search Console and created Google
  Testing branding/sign-in client. Recorded partial app registration and repaired
  exact-Mac SSH without opening administration. Public callbacks remain absent;
  see [0108](docs/implementation/0108-private-connector-configuration.md).

## Slice 0107 - Private Integration Secret Store - 2026-09-30

- Switched the same 4-GB/80-GB VM to the owner-approved $24/month dual-stack plan
  after a real IPv6-only registry failure; administrative exposure stays closed.
- Added pinned persistent TLS OpenBao, hidden key entry, exact read-only model
  policies, independently encrypted backups and disposable restore qualification.
  OpenAI/Jev keys are stored but not provider-qualified.
- Recorded initial failures and repairs without weakening audit, TLS or production
  gates. No public application or OAuth integration is claimed; see
  [0107](docs/implementation/0107-private-integration-secret-store.md).

## Slice 0106 - Owner-Approved Integration Test Scope - 2026-09-30

- Recorded the owner's explicit authorization for complete testing at the
  dedicated hostname, test repository and Slack workspace, and amended the
  default no-public-test-services working rule only for that environment.
- Administrative services, customer data and public synthetic credentials remain
  excluded; all product invariants, live qualification and release gates remain.
  No runtime or provider permission was enabled; see
  [0106](docs/implementation/0106-owner-approved-integration-test-scope.md).

## Slice 0105 - Closed Development Base VM - 2026-09-30

- Provisioned one owner-requested Lightsail base VM: 2 vCPUs, 4 GB RAM,
  80 GB SSD, Ubuntu 24.04 LTS, IPv6-only in `us-east-1`.
- Restricted AWS and OS firewalls to this Mac's exact IPv6 SSH source, disabled
  root/password login, required IMDSv2, and checked denied access and reboot
  persistence. Recorded initial key/port/bootstrap failures and manual recovery.
- No Signal runtime, secrets, DNS, callbacks or public application were deployed.
  This is not a qualified installer, private pilot or production topology; see
  [0105](docs/implementation/0105-closed-development-base-vm.md).

## Slice 0077 - SEO Baseline and 90-day Strategy - 2026-10-02

- Added immutable site baselines and deterministic evidence-backed strategies
  (migration 0063 after unchanged 0062).
- Added owner Overview, Pages, Strategy and Analytics views with source-specific
  coverage, top GSC cohorts, evidence links and honest unavailable sources.
- Acceptance creates an unaccepted brief proposal or routes to an existing sealed
  Inbox revision, never execution authority. DataForSEO, model reordering and
  unsealed candidate preparation remain unavailable; see
  [0077](docs/implementation/0077-seo-baseline-strategy.md).

## Slice 0076 - Optional DataForSEO - 2026-10-02

- Added site-scoped OpenBao-only credential setup/removal, a three-endpoint
  closed Basic-auth shared-egress profile, and monthly spend reservations.
- Added immutable evidence-linked typed SERP competitors, volumes and backlink
  summaries with explicit unavailable/unknown/rejected states and no blind replay.
- Added owner-only dashboard controls and a bounded read API. No scraping, 0077
  changes or production authority; live DataForSEO remains `NOT_EXECUTED`.
- Migration 0064 follows unchanged 0063; see [0076](docs/implementation/0076-dataforseo.md).

## Slice 0086 - IndexNow - 2026-10-02

- Add OpenBao-backed per-site key generations, owner-reviewed one-file static
  key recipes through Inbox/0084, and immutable old-key retirement (migration
  0065 after 0064). Keep the closed technical A2 set unchanged.
- Queue only committed 0085 verified changes, check exact live key bytes through
  shared crawl egress, and submit site-bound JSON through a closed IndexNow
  profile with independent intents, immutable receipts, bounded definite-response
  retries, and no blind replay of ambiguous writes.
- Show owner key status, recent submissions and EC-142 skips in API/Connectors.
  Qualify the internal path on real PostgreSQL/OpenBao and isolated builds, with
  the complete local lab, static, dependency, and dashboard gates passing.
  Production composition and live provider qualification remain unavailable;
  see [0086](docs/implementation/0086-indexnow.md).

## Slice 0094 - Change Measurement and Weekly Report - 2026-10-02

- Pin immutable pre-change evidence for live-verified technical page changes and
  schedule 7/28/90-day observations through existing Temporal/weekly machinery.
- Preserve GSC page metrics and Bing site context with exact source generations,
  verbatim incomplete coverage, explicit missing states, observed-change deltas
  and same-page confounders. No complete or causal-impact claims.
- Extend the versioned owner report and Changes dashboard with measurements,
  existing backlog and current Inbox decisions. Migration 0066 follows 0065;
  imports, egress, credentials and publishing authority are unchanged.
- Live providers, production deployment and email/Slack/Telegram report delivery
  remain NOT_EXECUTED; see [0094](docs/implementation/0094-change-measurement.md).
- Final local qualification passes 837 database, 37 delivery, 1,252 other Python,
  24 repository and 152 dashboard cases, with all required lab and build gates.

## Slice 0097 - GA4 Binding and Import - 2026-10-02

- Added optional owner/MFA read-only GA4 OAuth and exact verified-origin property
  binding with shared Google PKCE helpers and isolated OpenBao-only refresh tokens.
- Added closed Admin/Data egress profiles, bounded per-page report generations,
  verbatim provider coverage, and sampling/threshold/other flags without completeness.
- Added owner API/dashboard consent, selection, import status/coverage and disconnect;
  local denial and independent restriction-journal restore replay precede reuse.
- Migration 0067 follows 0066. Existing GSC defaults/tests and specifications stay
  unchanged. Live GA4, production composition and browser visual qualification remain
  `NOT_EXECUTED`; see [0097](docs/implementation/0097-ga4-binding.md).

## Slice 0093 - Email Reports and Alerts - 2026-10-02

- Added operator-configured TLS-only SMTP through a closed shared-egress profile,
  OpenBao-only credentials, daily site caps, bounded retries and immutable receipts.
- Added dashboard self opt-in from fresh verified OIDC identity, with claim and
  membership-change invalidation; no verification email or email tokens.
- Review correction: stale/out-of-window email claims now audit and invalidate
  email verification without blocking authenticated sign-in. Forward migration
  `0069` preserves `0068`; authority errors retain atomic session rollback.
- Queued the 0104 weekly report projection and fixed pause/revocation/failure/binding
  alerts, bounded token-free plain text/escaped HTML and dashboard-only links.
- Unconfigured delivery stays unavailable. Live SMTP and production supervision
  remain `NOT_EXECUTED`; see [0093](docs/implementation/0093-email-reports.md).
## Slice 0123 - Owner-Approved Article Delivery - 2026-10-02

- Merge-train integration correction: verified new articles record an explicit
  `new_page` baseline without pre-change dates or fabricated metrics; refreshes
  use their existing page's normal baseline. Both enter 0094's imported GSC
  page-dimension 7/28/90-day post observations. Technical measurement behavior,
  safety gates and authority are unchanged; no causal or live-provider claim.

- Added an exact, immutable `owner_editorial` operation authority with current
  owner, dashboard-only fresh MFA, candidate/revision/recovery binding and explicit
  acknowledgement of every flagged sentence (migration 0071 after 0070).
- Recheck unsuperseded briefs and facts, exact build/base/binding and weekly
  article/refresh output caps before the existing journaled and fenced PR effects.
  Reuse protected-path preflight, exact Git objects and lost-response reconciliation.
- Extend evidence-only deployment and live verification to the sealed article
  artifact. Dashboard approval and Changes retain the real editorial authority.
- Legacy editorial reviews remain record-only. Article autonomy, Slack/Telegram
  approval, standing-grant dispatch, merge, deploy and deletion remain excluded.
  Local qualification passed 70 unfiltered delivery cases and 2,401 tests across
  the full gate; live GitHub remains `NOT_EXECUTED`. See
  [0123](docs/implementation/0123-owner-approved-article-delivery.md).
## Slice 0067 - Sandboxed Browser Worker - 2026-10-02

- Added disposable non-root/read-only Chromium sessions on private internal
  isolated-gateway networks, with a credential-free GET/HEAD forward proxy whose
  only upstream path is the existing durable shared-egress gateway.
- Enforced a closed worker action schema, inert forms/dialogs/downloads/popups,
  bounded reduced accessibility links and recorded Jev Choice with stop-only
  fallback/low-confidence behavior. Page text cannot introduce actions.
- Added encrypted snapshot/screenshot artifacts and immutable forced-RLS session,
  step, robots/admission and decision evidence in migration 0072 after 0071.
- Added internal render/read/sealed-fragment verification and real Docker labs.
  0085 GET verification is unchanged. Live Jev/owner-site and production
  composition are `NOT_EXECUTED`; Lighthouse/CWV and model planning/vision remain
  unavailable. See [0067](docs/implementation/0067-sandboxed-browser-worker.md).
## Slice 0099 - WordPress Core REST Drafts - 2026-10-02

- Added least-privilege owner binding, OpenBao-only application passwords,
  restriction-journal revocation and closed bound-origin shared egress (migration 0073).
- Added sealed Content Writer new drafts after exact owner Inbox approval,
  per-attempt independent write intents, idempotent dispatch and quarantine with
  read-only delayed-commit reconciliation. Ambiguous writes never retry.
- Added truthful Connectors/Inbox states and read-only manual-publication observation.
  Existing-post updates and publishing remain unavailable; no bridge is installed.
  Live WordPress and production composition are `NOT_EXECUTED`; see
  [0099](docs/implementation/0099-wordpress-drafts.md).
## Slice 0100 - Google Docs Sync - 2026-10-02

- Added owner/MFA, verified-site Picker binding with exact `drive.file` scope,
  reused Google PKCE/OpenBao protocols, selected-only bounded Drive reads and
  durable local disconnect/restore denial (migration 0074, ADR-0112).
- Synced immutable provider versions through the encrypted/screened document
  pipeline and existing proposed-only Business Brain extraction. Withdrawal
  flags retained facts for review and excludes them from approved grounding.
- Added owner API/dashboard connect, selected sources, sync and disconnect;
  missing connector/model composition remains visibly unavailable. Notion has
  no bindable OAuth/sync path pending sanitized live capability qualification.
  Live Google, Notion and models remain `NOT_EXECUTED`; see
  [0100](docs/implementation/0100-google-docs-sync.md).
## Slice 0098 - Webflow Draft Delivery - 2026-10-02

- Recorded official API v2 research: no documented atomic update/publish
  precondition; both remain unavailable and the owner publishes in Webflow.
- Added one-site/collection OAuth, OpenBao-only credentials, forced-RLS exact CMS
  revisions and owner Inbox reviews, restriction-journal revocation (migration 0075 after 0074).
- Added bound draft-only shared egress, independent one-shot write intent, and
  read-only reconciliation retaining zero-match unknowns and delayed commits.
  No updates, archive, DELETE, live/design/hosting endpoints or article autonomy.
- Production dispatch remains disabled and live Webflow `NOT_EXECUTED`; see
  [0098](docs/implementation/0098-webflow.md).

## Slice 0082 - Content Writer - 2026-09-29

- Added forced-RLS append-only briefs, acceptance, draft evidence, weekly caps,
  sealed content revisions and exact owner reviews with audits (migration 0059).
- Added bounded structured drafting through existing model egress, current-fact
  grounding, reduction-only Jev Noul/frontier fallback and originality rejection.
- Added one-file Eleventy HTML articles/refreshes through existing candidate
  builds, owner API/dashboard and editorial Inbox without publishing or autonomy.
- Missing model/candidate composition and unsupported formats stay unavailable.
  Live providers/customer qualification remain `NOT_EXECUTED`; see
  [0082](docs/implementation/0082-content-writer.md).
## Slice 0092 - Telegram Pairing and Exact-Revision Approvals - 2026-09-29

- Added optional group-disabled bot installation, OpenBao-only bot/webhook
  secrets, one-use expected-person private pairing, and deny-journal revocation
  with restored-primary replay. Migration `0070` follows unchanged `0069`.
- Added closed JSON POST egress with token injection only at the pinned HTTP
  transport, token-safe evidence/errors, secret/replay-verified private ingress,
  and outbox writes that never blindly resend accepted or ambiguous messages.
- Extended the same exact-revision Inbox decision path for Telegram, with
  current-member authority and A3/A4/canonical/unknown dashboard handoff. Added
  truthful API/dashboard states. 0104 derives Telegram's channel from that exact
  decision through the existing 0084 binding trigger; retry and stale-revision
  delivery regressions are covered without new write authority. Live Telegram and production composition remain
  `NOT_EXECUTED`; see [0092](docs/implementation/0092-telegram-approvals.md).

## Slice 0073 - Business Brain - 2026-09-29

- Added append-only, forced-RLS Business Brain facts with page, document-range, or
  owner-membership provenance; owner approval, correction, and removal retain
  history and audit records.
- Composed encrypted crawl evidence and screened document-range extraction through
  the existing Jev decision recorder and shared-egress reasoner, with labelled
  frontier fallback, strict proposed-only output, source/version idempotency,
  atomic provenance-bound completion, and explicit failed/unknown receipts.
- Added owner API and dashboard facts/status/provenance, approval/correction/removal,
  first-class competitors, versioned brand voice, approved claim grounding, and
  visibly unavailable extraction while manual facts work. Live Jev/model and
  production composition remain `NOT_EXECUTED`; see [0073](docs/implementation/0073-business-brain.md).

## Slice 0091 - Slack Linking and Exact-Revision Approvals - 2026-09-29

- Added optional single-workspace/channel OAuth with only `chat:write`, OpenBao
  credential isolation, short-lived per-person links, and durable revocation.
- Added closed shared-egress form OAuth and JSON bot methods, outbox dispatch with explicit unknown
  outcomes, signed/replay-rejected interactivity, current-member authorization,
  and exact sealed-revision decisions shared with the Inbox and 0084 path.
- Added A3/A4 dashboard handoff and fresh-MFA high-risk decisions, independent
  denial-journal replay, strict API/BFF contracts, and truthful Connectors states.
  No production capability or live Slack success is claimed; see
  [0091](docs/implementation/0091-slack-approvals.md).
- Rebased onto the Business Brain mainline; Slack migration is `0058` after
  unchanged `0057`. OAuth token exchange now uses exact bounded form POST;
  `chat.postMessage` and `auth.revoke` keep documented JSON with bearer authority.

## Slice 0104: Autonomy-to-Delivery Integration - 2026-09-29

- Connect weekly committed crawl findings, sealed technical recipes, the current
  deterministic-first gate and exact owner or standing-grant authority to the
  journaled, fenced GitHub PR path and customer delivery/live verification.
- Add separately reviewed closed-family autonomy eligibility, immutable exact
  dispatch/execution receipts, read-only recovery after authority reduction and
  an evidence-only owner cycle report. Keep all owner-only paths and write limits.
- Add real PostgreSQL/Temporal/shared-egress qualification, exact escalation,
  revocation/cap/base exclusions, replay and lost-response coverage. Live provider
  calls remain NOT_EXECUTED; no production write or release is enabled.
- Rebased onto the Business Brain, Slack and Content Writer mainline with migration
  `0060` after unchanged `0059`. Exact owner approvals retain their dashboard or
  Slack channel throughout delivery; editorial approvals remain excluded.
- The full rebased gate passes, including 831 PostgreSQL, 36 delivery, 1,243 other
  Python, 24 repository and 148 dashboard tests. Exact commands and provider limits
  are in the implementation/evidence records; no production or live success is claimed.

## Slice 0085 - Delivery Observation and Live Verification - 2026-09-29

- Added read-only GitHub check, merge, and exact customer-deployment correlation
  through shared egress, followed by robots/origin-admitted GET verification of
  the sealed homepage postconditions and result digest.
- Added immutable bounded observation receipts and evidence-only Changes stages,
  explicit inconclusive/regressed/unknown results, and sealed recovery evidence.
  No merge, deploy, signed delivery certification, or production authority is
  enabled. Live App/site qualification remains `NOT_EXECUTED`; see
  [0085](docs/implementation/0085-live-verification.md).

## Slice 0084 - Idempotent Pull-Request Creation - 2026-09-29

- Added a journal-before-GitHub operation with exact revision, owner decision,
  binding, release, base, lease, and fence checks. A separate one-repository
  write adapter creates a deterministic tree, commit, branch, and PR through
  shared egress; ambiguous responses require exact read reconciliation.
- Integrated a separate typed repository-write profile with exact origin,
  repository/token and route bounds, cross-profile denial, and bounded
  pre-dispatch spacing waits. Existing profiles are not widened; see ADR-0092.
- Exposed read-only operation and PR evidence in Changes. Local PostgreSQL,
  journal, gateway, and fake-provider qualification does not enable production
  writes or claim live GitHub delivery. See [0084](docs/implementation/0084-idempotent-pr-creation.md).

## Slice 0083 - Exact-Revision Inbox Review - 2026-09-29

- Added an owner-scoped Inbox for sealed technical-recipe revisions with the
  evidence link, exact before/after fragment, changed destination, recipe release,
  build receipt, expected impact, recovery plan, and revision digest.
- Decisions bind an immutable owner record to the exact revision hash. The Inbox
  visibly renders superseded and stale-base revisions, reveals control and
  directional Unicode in diffs, and states that approval grants no repository write.
- Added strict same-origin BFF and API contracts, CSRF-protected decisions, and
  forced-RLS PostgreSQL storage. Live customer review and all repository writes
  remain `NOT_EXECUTED`.

## Slice 0072 - Owner Brand Documents - 2026-09-29

- Added owner-only PDF, DOCX, Markdown, and text upload through the same-origin
  dashboard BFF with strict CSRF, size, type, and count bounds.
- Stored files as encrypted immutable artifacts using a dedicated read-only
  OpenBao key path; bounded offline extraction records injection/credential
  signals but no document text in ordinary tables.
- Added owner list/view/supersede/delete controls. Secret-bearing text is
  withheld, and deletion honestly retains encrypted evidence; physical purge
  and production composition remain unavailable. See
  [0072](docs/implementation/0072-brand-documents.md) and ADR-0084.

## Slice 0075 - AI-Visibility Baseline - 2026-09-29

- Added internal immutable versioned per-site target-question functions for crawl
  evidence and bounded owner inputs, but no owner input route at this checkpoint.
  0154 makes question versions owner-reachable. Added OpenAI, Perplexity, and Gemini
  citation parsing over the qualified 0074 boundary.
- Added explicit complete/incomplete coverage, linked provider evidence, cited pages,
  other domains, bounded usage and cost reservation records, and an evidence-first
  per-site AI visibility dashboard view with no fabricated score.
- Live provider success and production composition remain `NOT_EXECUTED`; see
  [0075](docs/implementation/0075-ai-visibility-baseline.md).

## Slice 0081 - Technical SEO Recipes - 2026-09-29

- Added committed missing-alt and duplicate-description crawl findings, six
  narrow evidence-bound technical recipe families, and reviewed-release gating.
- Reused the protected-path preflight and isolated candidate build to seal one
  immutable RFC 8785 revision with exact patch, evidence, receipt, impact, and
  revert plan. No repository write or PR is enabled.
- Recorded the limited static-homepage format, real PostgreSQL/container
  qualification, and live-resource limits in
  [0081](docs/implementation/0081-technical-seo-recipes.md) and ADR-0069.

## Slice 0074 - Assistant Provider Boundaries - 2026-09-29

- Added independent OpenBao-only OpenAI, Perplexity, and Gemini credentials,
  bounded fixed official API requests through provider-specific model-egress
  profiles, and immutable provider response evidence tied to verified sites,
  the exact profile, and observed operations.
- Kept all three provider capabilities visibly unavailable pending live
  qualification and the separate visibility-baseline slice. Added real
  PostgreSQL, OpenBao, and isolated-network checks, a silent-key live
  qualification command, [ADR-0082](docs/adr/0082-assistant-provider-boundaries.md),
  and the [implementation record](docs/implementation/0074-assistant-provider-boundaries.md).

## Slice 0071 - Bing Webmaster Binding and Import - 2026-09-29

- Added fixed read-only Bing OAuth with one-use owner state, exact verified-site
  confirmation, OpenBao-only refresh credentials, CAS rotation, and durable
  revocation through the shared egress gateway.
- Added separate, immutable `bing_webmaster` performance, inbound-link count, and
  link-detail generations. Coverage remains explicitly incomplete; GSC and Bing
  values are not silently merged.
- Added real PostgreSQL, OpenBao, and isolated-network checks, a silent-key live
  qualification command, [ADR-0081](docs/adr/0081-bing-read-only-source-evidence.md),
  and the [implementation record](docs/implementation/0071-bing-binding.md).
  Authorized Bing success and dashboard composition remain unavailable.

## Slice 0069 - GSC Binding and Egress Profiles - 2026-09-29

- Added fixed-scope Search Console OAuth/PKCE, OpenBao-only secrets, exact owner
  property confirmation, durable revocation, and explicitly incomplete analytics
  generations. Live Google consent/import and owner UI remain unavailable.
- Replaced shared provider header and media-type discretion with closed,
  purpose-specific egress profiles. Dispatch and completion bind the profile;
  GitHub, OAuth, GSC, model, and crawl shapes cannot borrow each other's allowances.
- Added real PostgreSQL, OpenBao, and isolated-network checks, ADR-0080 and
  ADR-0083, and the [implementation record](docs/implementation/0069-gsc-binding.md).

## Secret-Scanning Configuration - 2026-09-29

### Security

- Added `.gitleaks.toml`, which keeps every default gitleaks rule and allows only
  three reviewed false-positive shapes of the generic-api-key rule: file-path
  SHA-256 digests in evidence records, `synthetic-` test credentials in tests
  and labs, and bare identifiers or Ed25519 key type names. Full history on all
  branches now scans clean (previously 249 generic-api-key false positives, none a
  real credential). A negative test confirmed that random secrets planted in
  each allowlisted location are still detected.
- Documented the scanning command and allowlist rules in CONTRIBUTING.

## Slice 0090 - Record-Only Weekly Loop - 2026-09-29

- Add one per-site, per-week Temporal cycle with durable observe, analyze, plan,
  prepare, gate, handoff, verify, measure, and report stage receipts and replay.
- Add owner pause and pause-clear, atomic grant revocation, crawl dispatch
  admission checks, next-week cap deferrals, and an evidence-only owner report.
- Keep candidate preparation unavailable and handoff record-only until the
  recipe and PR integrations qualify. See [0090](docs/implementation/0090-weekly-loop.md).
- AI-visibility question production was not supplied by this weekly loop. 0131's
  independent observation schedule consumes question versions; 0154 adds the owner
  producer without changing weekly-loop modules.

## Slice 0089 - Deterministic-First Jev Autonomy Gate - 2026-09-29

- Compose the current external recovery generation, deterministic policy, 0088
  grant and cap preflight, immutable 0064 Jev recommendation, and atomic final
  authority/budget recheck in that order.
- Persist forced-RLS immutable gate outcomes and prevent fallback, low
  confidence, revocation races, cap races, and historical retries from shipping.
- Keep the result internal: no PR, CMS, or other external writer consumes it.
  See [0089](docs/implementation/0089-jev-autonomy-gate.md).

## Slice 0088 - Standing Authorization - 2026-09-29

- Added owner-only, immutable standing grants bound to the current recovery
  generation and exact signed reviewed recipe releases resolved at grant time.
- Added site-wide serialized weekly volume/spend reservations, with exact retry
  identity and no A4/A5 eligibility.
- Journaled local grant revocation with a visible pending-durability state and
  proved deny-only replay after a real primary restore and OpenBao rotation.
- Added dashboard grant/revoke controls; autonomous dispatch and production
  writes remain unavailable. See [0088](docs/implementation/0088-standing-authorization.md).

## Slice 0103 - Reviewed Recipe-Release Registry - 2026-09-29

- Add immutable platform-signed recipe releases and append-only draft, tested,
  reviewed, and revoked history under a dedicated release-manager role.
- Resolve compatible ranges to verified immutable release IDs and seed the
  existing verified-homepage proposal as reviewed but not dispatch-eligible.
- Journal recipe revocations as immediate local restrictions and prove deny-only
  replay after a real primary restore and OpenBao generation rotation.
- Record scope, qualification, and production limits in
  [0103](docs/implementation/0103-recipe-release-registry.md).

## Slice 0102 - Authority-Restriction Journal - 2026-09-29

- Added an independently operated PostgreSQL authority journal with encrypted,
  signed, hash-chained, deduplicated restriction records and authenticated heads.
- Made browser-session revocation atomically enqueue a stable journal intent.
  Logout reports `AUTHORITY_DURABILITY_PENDING` until an independently verified
  append produces an immutable platform receipt; outage never undoes local denial.
- Added deny-only replay, typed absent-target tombstones, and an OpenBao
  recovery-generation check. A real PostgreSQL snapshot restore and replay drill
  exercises the boundary. Production dispatcher and key lifecycle remain disabled.
- Recorded the storage decision in [ADR-0070](docs/adr/0070-independent-authority-restriction-journal.md)
  and the qualification limits in [0102](docs/implementation/0102-authority-restriction-journal.md).

## Slice 0080 - Isolated Candidate Build - 2026-09-29

- Added bounded exact-commit/blob checkout through the existing shared-egress
  GitHub route and deterministic pre-build protected-path and recipe-scope
  validation. No host clone or repository write is performed.
- Added a digest-pinned, non-root, credential-free, no-network disposable
  candidate sandbox with time/resource/output bounds, source-integrity and
  artifact checks, and immutable owner/site build receipts in migration `0043`.
- Added real PostgreSQL and container failure/security tests, ADR-0068, and a
  dedicated live qualification command. Customer repository success and
  production runner isolation remain `NOT_EXECUTED`; see the
  [implementation record](docs/implementation/0080-isolated-candidate-build.md).

## Slice 0079 - GitHub PR Permission and Repository Format - 2026-09-29

- Added a revocable owner/site extension of the exact 0070 binding, with
  forced-RLS intent and immutable events. It observes one-repository
  `pull_requests: write` permission but grants no repository operation.
- Added bounded base commit/tree inspection and deterministic framework and
  content-format detection through fixed-route shared egress. Truncated,
  missing, symlink, unknown, or ambiguous evidence is not candidate-compatible.
- Added current permission/base rechecks, PostgreSQL and provider-negative
  tests, ADR-0067, and a live qualification command. Dedicated live App success,
  checkout, and external writes remain unavailable; see the
  [implementation record](docs/implementation/0079-github-pr-authority-framework-detection.md).

## Slice 0070 - Durable GitHub Read Binding - 2026-09-29

- Added an owner/session/site-attributed, revocable one-repository GitHub read
  binding with immutable lifecycle receipts, forced RLS, function-only identity
  access, current origin proof, and fail-closed replay and authorization checks.
- Added an OpenBao-only App credential reader and a fixed-route GitHub transport
  through shared connector egress. Current reads recheck repository identity and
  base-branch protection; no PR or repository write authority is granted.
- Added real PostgreSQL and OpenBao lab coverage, provider and egress fakes,
  ADR-0066, and a dedicated live qualification command. Live App success and
  the browser connector UI remain unavailable; see the
  [implementation record](docs/implementation/0070-github-read-binding.md).

## Slice 0068 - Crawl-Wide Technical Findings - 2026-09-29

- Added deterministic technical findings derived from immutable completed-crawl
  page and settlement evidence, with exact evidence links and replay-safe report
  sealing. Real crawls now produce the report before returning from the activity.
- Added forced-RLS, function-only report storage in migration `0038`, plus
  positive, negative, failure, cross-site, and rollback tests. Reclassified the
  slice as security-critical because its narrow crawl-ingest grant changes
  internal evidence authority; ADR-0065 and an evidence record describe the gate.
- Recorded the approved GitHub-first internal beta reorder in ADR-0064 and the
  roadmap. Revision 4.0 and all release and safety gates remain unchanged.
- Sitemap XML is explicitly `not_assessed`; no customer-facing report or real
  owner-origin qualification is claimed. See the
  [implementation record](docs/implementation/0068-crawl-technical-findings.md).

## Slice 0066 - Full-Site Crawl Composition - 2026-09-29

- Added verified-origin robots bootstrap and a bounded link-discovered crawl that
  composes shared egress, encrypted observations, parsed SEO evidence, exact
  frontier settlements, byte accounting, and immutable complete/partial manifests.
- Added restart-safe classification for unknown robots/page dispatches, cooperative
  cancellation, and a Temporal activity window matched to the one-hour crawl cap.
- Added an explicit disposable verified-crawl pilot mode while preserving the
  default no-network walkthrough. Work requires exact origin proof; no production
  crawl or external write authority is enabled.
- Added positive, negative, failure, RLS, rollback, network-boundary, Temporal,
  dashboard, and tooling tests, plus ADR-0063 and the
  [implementation record](docs/implementation/0066-full-site-crawl.md). Real
  owner-origin end-to-end qualification remains `NOT_EXECUTED`.

## Slice 0065 - Shared Egress Proxy - 2026-09-28

### Added

- Added a bounded GET/HEAD/POST shared-egress contract and extended the numeric-peer
  HTTP boundary with credential-safe headers, response/media/time limits, identity
  encoding, an absolute response deadline, redirect denial without destination
  retention, and no retry of an ambiguous POST.
- Added durable forced-RLS PostgreSQL egress operations with intent-before-I/O,
  exact robots and global-origin permit binding, at-most-once dispatch, sanitized
  terminal evidence, provider backoff, and function-only runtime access.
- Added real isolated-network POST and private-redirect qualification, migration
  rollback coverage, strict completion-consistency tests, ADR-0062, implementation
  record 0065, and executable evidence.

### Changed

- Jev and the labelled fallback no longer own public HTTP clients. Both require the
  shared-egress capability and treat missing, denied, deferred, uncertain, or
  invalid egress as an explicit conservative fallback condition.
- The silent-key Jev qualification command now requires an exact TypeSafe-only
  shared-egress context and least-privilege database roles. It cannot fall back to
  a direct provider call.

### Verification

- The focused shared-egress and decision suites, isolated Docker network lab,
  disposable PostgreSQL lab, OpenBao lab, full API/identity/tooling suite, Ruff,
  dependency checks, repository checks, dashboard tests, typecheck, and production
  build are recorded in
  [the slice evidence](docs/evidence/0065-shared-egress-proxy.json).

### Security

- Public-address screening, numeric-peer pinning, current robots evidence, and one
  global origin bucket now gate provider calls before a socket opens. Credentials
  and request/response bodies are not stored; unresolved dispatch is never retried
  blindly. No merge, deploy, delete, authority-minting, or production write was
  added.

## Slice 0064 - Jev Decision Client - 2026-09-26

- Added bounded Choice, Noul, and Score domain contracts and a fixed-origin
  TypeSafe HTTP adapter with strict identity, probability, confidence, usage,
  transport, timeout, and response-size validation.
- Added a single-version read-only OpenBao Jev credential and extended the real TLS
  OpenBao lab to prove its reader cannot mutate the secret.
- Added labelled Luna and deterministic fallbacks that can return only
  `ask_owner` or `reject`; every primary and fallback result is reduced to the
  deterministic policy ceiling and exposes no authorization or execution API.
- Added forced-RLS immutable decision records in migration `0035`, written only
  through a narrow workflow function and containing exact digests, answers,
  probabilities, confidence, threshold, ceiling, outcome, and fallback evidence.
- Added positive, negative, and failure tests for typed questions, limits, EC-125
  response failures, timeout, 429, 5xx, unconfigured operation, exact replay,
  scope/authority rejection, immutability, migration rollback, and INV-026.
- Added ADR-0061, the implementation record, a silent-key live qualification
  command, and explicit `NOT_EXECUTED` evidence because no TypeSafe key exists.

## R1 Roadmap Corrections - 2026-09-26

### Changed

- Added an R1 security-critical slice for a durable, owner-selected GitHub App read
  binding with one-repository `contents: read`, OpenBao-held App credentials,
  revocation handling, and wrong-binding checks. R2 now extends that binding with
  pull-request authority instead of creating it.
- Split the planned AI-visibility work into security-critical OpenAI, Perplexity,
  and Gemini provider boundaries followed by a product-tier baseline built only on
  qualified boundaries.
- Renumbered all later planned R1–R4 slices consistently; implemented slice and
  accepted specification identifiers are unchanged.

## Slice 0063 - Autonomous SEO Employee Direction (Revision 4.0) - 2026-09-24

### Added

- Added amending specification Revision 4.0: autonomous SEO and AI-search employee
  contract, REQ-019 to REQ-027 (REQ-016 amended), INV-026 to INV-034, autonomy
  under standing authorization with deterministic policy first and a Jev
  confidence gate that can only reduce autonomy, the Jev decision layer and
  fallback, the sandboxed browser agent, Business Brain, build-first data sources
  with optional DataForSEO, twelve least-privilege connectors, content production
  limits, AI-search visibility, measurement, R1–R4 releases, open-source and managed
  SaaS distribution, process tiers, EC-124 to EC-146, and a section-by-section
  amendment table.
- Added the accepted product requirements (`docs/product/prd.md`), ADR-0060, the
  R1–R4 roadmap with ordered slices 0064–0099, implementation record 0063, and
  `CLAUDE.md`, which imports `AGENTS.md` for Claude Code.

### Changed

- Rewrote `AGENTS.md` around the current direction, product safety rules, reading
  order, and security-critical versus product process tiers.
- Updated the README, CONTRIBUTING, PRODUCT, documentation and decision indexes, and
  implementation status to the Revision 4.0 direction; marked the Core V1 PRD,
  Core V1 roadmap, and ADR-0030 scope and sequence as superseded while preserving
  them.
- Protected Revision 4.0 by SHA-256 alongside Revisions 3.0, 3.1, and 3.2, and added
  repository assertions for the preserved Core V1 contract and the active Revision
  4.0 direction.

### Verification

- `npm test` passed: 24 of 24 repository cases, documentation checks across 165
  Markdown files, 106 of 106 dashboard cases, dashboard typecheck, and dashboard
  build.
- The dashboard typecheck first failed on iCloud-created duplicate files inside the
  git-ignored `apps/dashboard/.next` build output; that directory was moved aside and
  regenerated by the build. No tracked file was affected.

### Security

- Documentation only. No runtime capability, provider connection, autonomy, or
  production write is added; all Revision 4.0 runtime evidence is `NOT_EXECUTED`.

## Slice 0062 - GitHub App Repository Inspection - 2026-09-20

### Added

- Added a real GitHub App provider adapter that signs a bounded RS256 App JWT,
  mints a short-lived token restricted to one repository and `contents: read`, and
  reads exact repository and selected base-branch identity.
- Added current stateless installation-token support, fixed GitHub API origin and
  version, bounded response handling, sanitized provider failures, and strict
  repository/ref/content-path validation.
- Added 34 positive, negative, and failure adapter cases, repository policy checks,
  a live negative provider-boundary check, ADR-0059, and implementation records.

### Security

- Personal access tokens are not accepted. Private keys and installation tokens
  remain transient, are excluded from representations, and never enter returned
  snapshots or error messages.
- This adapter is read-only and grants no durable binding, checkout, branch,
  pull-request, merge, workflow, deployment, or production authority.

## Slice 0061 - Approved Change Continuation - 2026-09-20

### Added

- Added a real Changes continuation for the verified-homepage proposal, including
  exact revision/evidence identity, before/after scope, human decision, and seven
  evidence-gated delivery stages.
- Redirected successful approval to Changes and added the explicit next action for
  repository connection while keeping closed decisions closed.
- Added positive approved-state, empty-state, fail-closed route, repository-rule,
  type, build, and visual browser coverage plus implementation/status records.

### Security

- The projection creates no authority and performs no provider I/O. It explicitly
  states that no branch, commit, pull request, deployment, or outcome exists.
- Repository identity, base commit, and content path are not inferred. GitHub and
  production writes remain disabled.

## Slice 0060 - Verified Homepage Model Proposal - 2026-09-20

### Added

- Added a dedicated bounded Luna release that drafts one meta description from
  the exact owner-verified URL, title, H1, and immutable evidence identities.
- Added durable verified-evidence model admission, known/unknown failure handling,
  exact completion, and PostgreSQL reconstruction of the immutable RFC 8785
  proposal revision under migration `0034`.
- Added the authenticated API mutation and strict dashboard projection so Signal
  Chat prepares the verified-page proposal and Approvals records one exact owner
  decision.
- Removed fixture analysis and fixture findings from normal customer-visible Pages
  and Chat controls while retaining internal regression coverage.
- Added positive, not-ready, malformed-output, insecure-target, replay, weak-page-
  evidence, API, dashboard, model adapter, migration, and real PostgreSQL coverage
  plus ADR-0058 and implementation/runbook updates.

### Security

- The model has no tools, receives no HTML or credential, uses `store=false`, and
  cannot choose scope, authority, cost, recipe, or recovery behavior.
- Approval accepts only the draft. Repository, GitHub, CMS, merge, deployment,
  live verification, and production-write authority remain disabled.

## Slice 0059 - Verified Homepage Observation - 2026-09-20

### Added

- Added durable pre-network observation intents and immutable results bound to the
  current selected site, exact owner proof, completed audit manifest, and recovery
  generation.
- Added one bounded verified-homepage GET, metadata extraction, verified-origin
  evidence, and a deterministic missing-meta-description finding.
- Added authenticated API routes, same-origin dashboard action, and a Pages view
  for the exact URL, status, title, H1, description state, evidence identity,
  digest, and observation time.
- Added positive, absent-field, known-failure, replay, authority-reduction,
  immutability, strict-schema, API, dashboard, and real PostgreSQL coverage plus
  ADR-0057 and implementation/runbook updates.

### Security

- DNS answers remain public-address screened and connections stay pinned with TLS,
  same-origin redirect, timeout, MIME, and body bounds.
- This is an owner-authorized GET only. GitHub, CMS, Telegram, merge, deployment,
  customer mutation, and production-write authority remain disabled.

## Slice 0058 - Bounded System Resolver - 2026-09-20

### Added

- Added a bounded host A/AAAA resolver with explicit timeout, concurrency cap,
  stable de-duplication, and sanitized failure behavior.
- Composed the resolver through the existing pinned HTTP boundary into the
  disposable pilot, enabling the owner-only public HTTPS origin proof for sites
  the developer controls.
- Added positive, invalid-input, resolver-error, timeout, capacity, and
  private-address rejection tests plus implementation and runbook updates.

### Security

- All DNS answers remain subject to complete public-address validation before a
  connection is attempted; TLS, exact-origin, redirect, MIME, and body bounds are
  unchanged.
- This enables ownership proof only. Customer crawling, provider writes, GitHub,
  Telegram, deployment, and production authority remain disabled.

## Slice 0057 - Model-Backed Fixture Proposal - 2026-09-13

### Added

- Added a bounded OpenAI Responses adapter fixed to `gpt-5.6-luna`, strict
  structured output, no tools, `store=false`, fixed timeout/output limits, token
  accounting, and sanitized provider failures.
- Added exact read-only OpenBao model-secret provisioning for the disposable pilot;
  the API key is removed from the process environment and is never stored in
  PostgreSQL, browser state, logs, source control, or evidence.
- Added forward-only database revision `0032` with durable model intent,
  append-only run/call outcomes, bounded known-failure retry, unknown-outcome
  blocking, and PostgreSQL-reconstructed model proposal manifests.
- Connected Signal Chat to the model path and exposed model release, provider
  receipt, prompt/input/output identities, rationale, and token usage in the exact
  approval revision while retaining historical deterministic revisions.
- Added positive, negative, malformed, timeout/ambiguity, tamper, stale-evidence,
  privilege, migration, API, dashboard, real OpenBao, real PostgreSQL, and live
  model coverage plus ADR-0056 and implementation/evidence records.

### Security

- Luna receives one canonical synthetic evidence packet and no tools or authority.
  Deterministic policy and owner approval remain outside the model.
- Approval dispatches no external operation. Customer-origin reads, GitHub,
  Telegram, repository mutation, merge, deployment, and production authority stay
  disabled.

## Slice 0056 - Supervised Local Proposal Flow - 2026-09-13

### Added

- Added immutable forced-RLS proposal revisions, expiring approval requests, and
  exact owner decisions in forward-only database revision `0031`.
- Added a deterministic fixture-only four-responsibility planning pipeline whose
  closed manifest is RFC 8785-canonicalized, SHA-256-bound to current evidence,
  and independently reconstructed by PostgreSQL before commit.
- Added strict FastAPI prepare/read/decision contracts and same-origin dashboard
  BFF routes. Signal Chat now prepares the bounded proposal and Approvals exposes
  evidence, exact change, checks, authority, cost, recovery, expiry, and revision
  identity before approve, reject, or request-edits decisions.
- Added positive, negative, failure, concurrency, immutability, API, dashboard,
  repository, real PostgreSQL, and browser coverage plus ADR-0055 and the slice
  implementation/evidence records.
- Hardened local-pilot port selection against a connectable listener that can
  coexist with an apparently successful second loopback bind on some platforms.

### Security

- Owner authority is checked before proposal readiness is disclosed and repeated
  atomically when the proposal or decision is committed.
- Decisions emit no outbox event and authorize no GitHub, provider, merge,
  deployment, customer-origin, or production operation.

## Slice 0055 - Collision-Safe Local Pilot Port - 2026-09-13

### Changed

- Made the disposable pilot select port `3001` when an unrelated process already
  owns its default dashboard port `3000`, without stopping that process.
- Kept browser and identity authority exact by allowing only ports `3000` and
  `3001`, registering both exact callback URIs in the synthetic Keycloak realm,
  and composing CSRF/CSP/OIDC state from the selected origin.
- Added focused default, fallback, occupied API, and invalid override tests plus
  implementation, evidence, status, and runbook updates.

## Slice 0054 - Durable Fixture Finding - 2026-09-13

### Added

- Added forward-only migration `0030` with immutable tenant/site-scoped evidence,
  guarded current findings, append-only evidence links, forced RLS, and two narrow
  authenticated functions without direct runtime table privileges.
- Added a deterministic bounded metadata detector for one fixed local HTML fixture,
  exact completed-audit provenance, idempotent and concurrent retry convergence,
  and later-audit evidence advancement.
- Added strict authenticated API read/mutation contracts, a same-origin dashboard
  action, and a Pages result that exposes exact evidence identity, digest, time,
  and confidence while stating the configured origin was not read.
- Added positive, negative, failure, migration, privilege, API, dashboard,
  repository, real PostgreSQL, and browser qualification plus ADR-0054 and
  implementation/evidence records.

## Slice 0053 - Inspectable Audit Evidence - 2026-09-12

### Added

- Added a Pages evidence view for the latest authorized terminal audit with full
  manifest identity, content digest, coverage, collection time, scope release,
  crawl-policy release, and source provenance.
- Added actual latest-work state to Overview and direct navigation from Work to
  the committed evidence record.
- Added explicit synthetic/no-network and no-customer-findings boundaries instead
  of generating sample page inventory, SEO issues, metrics, or recommendations.
- Added positive, empty, malformed-version, type, build, browser, and repository
  qualification plus ADR-0053 and implementation/evidence records.

## Slice 0052 - Visible Durable Work Flow - 2026-09-12

### Added

- Added a Work-page action that accepts one selected-site snapshot command through
  the same-origin dashboard boundary, PostgreSQL outbox, and real local Temporal
  server and worker before rendering the durable terminal receipt.
- Added a current-user latest-work database/API read boundary with complete live
  authority rechecks, forced RLS, exact response validation, and no direct table
  access from the identity role.
- Added an explicitly local-only no-network executor and per-invocation browser
  cookie namespace so the disposable pilot is testable without contacting the
  configured site or weakening production write gates.
- Added positive, negative, failure, rollback, real PostgreSQL, dashboard, and
  browser qualification; ADR-0052; implementation/evidence records; and updated
  product, service, database, dashboard, and operations documentation.

## Slice 0051 - Disposable Local Owner Journey - 2026-09-12

### Added

- Added `npm run pilot` to compose invocation-owned PostgreSQL, OpenBao, Keycloak,
  the real API, and the real dashboard into one disposable loopback product path.
- Added a fixed synthetic owner/organization realm and real browser qualification
  for OIDC sign-in, organization selection, owner site creation, active-site state,
  clean shutdown, port release, and provider cleanup.
- Added exact local-only cookie, redirect, CSP, and null-Origin handling without
  weakening production `__Host-*; Secure` cookie or HTTPS requirements.
- Required a fresh OIDC authentication time and strengthened the real Keycloak lab
  to qualify a session-ready signed ID token.
- Added focused positive, negative, and failure tests; ADR-0051; implementation,
  evidence, and operations records; and updated product status and startup guides.

### Fixed

- Unified the dashboard session reader with the configured tenant-cookie contract.
- Made host-cookie deletion deterministic for the dashboard's strict relay parser.

## Slice 0050 - Read-Only GSC Property Discovery - 2026-09-12

### Added

- Added a fixed-endpoint Google Search Console `sites.list` boundary with a pinned
  read-only scope, mandatory TLS verification, no redirects or environment proxy
  use, bounded request/response behavior, and fixed nonsecret failures.
- Added exact domain and URL-prefix property classification, readable-permission
  checks, and canonical verified-origin eligibility without persisting credentials
  or inventing provider state.
- Added 25 focused connector cases, a repository contract, and a real Google
  authorization-rejection check using only a synthetic credential.
- Added ADR-0050, the implementation/evidence records, a provider runbook, and
  updated product status without claiming OAuth, binding, or analytics import.

## Slice 0049 - Dashboard Origin Verification - 2026-09-12

- Added strict same-origin dashboard routes for issuing and verifying the exact
  public-origin challenge through one host-only tenant cookie and transient
  tenant-CSRF proof.
- Added bounded exact response validation, fixed timeouts, manual redirect
  handling, closed provider failure mapping, and rejection of API cookie mutation.
- Added an owner-only selected-site proof workflow with exact copy/open controls,
  transient challenge state, explicit errors, and server-truth refresh on success.
- Extended the site directory to accept all three closed ownership states and
  reject non-HTTPS origins.
- Standardized navigation, command, and state symbols on pinned Lucide icons and
  removed the parallel custom glyph family.
- Consolidated dashboard hierarchy into a cool working ground, white elevated
  operational modules, compact fact bands, and solid-blue actions; removed the
  permanently disabled search field and decorative prerequisite-row glyphs.
- Kept graph modules visible but removed empty axes, legends, and simulated chart
  furniture until a real tenant/site-scoped source supplies observations.
- Found and repaired a real mobile defect where backdrop filtering constrained the
  navigation overlay to the toolbar; all fourteen destinations now fill and scroll
  within the remaining viewport.
- Added positive, negative, failure, route, projection, owner-visibility,
  repository, type, build, static-design, and desktop/mobile browser checks;
  ADR-0049; implementation/evidence records; and updated dashboard/product status.
- Production identity/resolver composition, automatic stale-proof enforcement,
  connectors, agents, approval, external writes, and undo remain unavailable.

## Slice 0048 - Exact Public-Origin Verification - 2026-09-12

- Added forward-only migration `0028` with immutable forced-RLS challenge,
  attempt, and successful-verification evidence plus a private global exact-origin
  claim registry.
- Added owner/current-session/current-site challenge issuance with UUIDv4
  idempotency, 30-minute expiry, and ten-open-challenge/ten-attempt bounds.
- Extended the pinned HTTP boundary with exact bounded plaintext retrieval. Proof
  requires status 200, `text/plain`, exact no-redirect URL/body/digest, a public
  numeric peer, and bounded timing evidence.
- Split verification into prepare, network, and record phases so no database
  transaction spans hostile I/O and live authority is rechecked afterward. Failed
  observations commit before their typed error is returned.
- Added strict tenant-CSRF-protected challenge and verification API contracts,
  exact site promotion, one permitted origin, a 30-day recheck, and closed
  revocation conditions without granting crawl or external-write authority.
- Added positive, negative, failure, expiry, replay, limit, authority-reduction,
  cross-tenant claim, migration rollback, API, pure boundary, and isolated real-
  network tests; ADR-0048; implementation and operations records; and updated API,
  database, roadmap, status, and service documentation.
- Dashboard controls, production identity/resolver composition, automatic stale-
  proof enforcement, origin changes, connectors, agents, and production writes
  remain unavailable.

## Slice 0047 - Full-Viewport Dashboard Polish - 2026-09-12

- Removed the outer desktop mockup frame so Signal fills the browser viewport with
  a white navigation rail and toolbar, cool-gray working canvas, and white detail
  rail.
- Replaced pale semantic washes with a neutral high-contrast system, solid Signal
  Blue actions, and explicit green/amber/red icon and border states; CSS contains
  no gradients.
- Replaced ambiguous navigation glyphs with domain-specific Lucide icons and added
  a real safety banner for the development write boundary.
- Added responsive evidence-empty charts to Overview, Analytics, and Usage with
  expected-series legends, neutral grids, source state, and prerequisite actions.
  No customer metrics, trends, dates, costs, or provider state are simulated.
- Added chart truth-boundary and full-viewport repository tests, refreshed the
  normative design system and sidecar, documented ADR-0047, and recorded browser,
  dependency, secret-scan, and aggregate verification evidence.
- This is a visual-system slice only; it adds no connector, data read model,
  mutation, agent operation, approval, recovery, undo, or production authority.

## Slice 0046 - Reference-Led Dashboard Surfaces - 2026-09-12

- Redesigned the owner dashboard around the supplied operational references with a
  light navigation rail, flexible working canvas, contextual detail rail, compact
  flat sections, Lucide controls, and responsive mobile navigation.
- Added allowlisted routes for Signal Chat, Work, Strategy, Pages, Changes,
  Approvals, Analytics, Recipes, Policy, Connectors, Usage, Settings, and Help,
  backed by the same no-store server loader as Overview.
- Preserved every implemented identity/site boundary and replaced reference mock
  activity with domain-specific prerequisite ledgers, disabled controls, explicit
  blocked-write state, and a statement that no customer data is being simulated.
- Added all-destination rendering and repository contract tests; refreshed the
  design system and sidecar; and verified all pages at desktop and mobile widths
  with no horizontal overflow or browser diagnostics.
- This is an information-architecture and visual-system slice, not a chat,
  workflow, analytics, approval, connector, execution, recovery, or undo release.

## Slice 0045 - Owner-Controlled Site Onboarding - 2026-09-12

- Added forward-only migration `0027` with a private tenant/site/origin lifecycle
  route mirror and immutable forced-RLS onboarding evidence.
- Added owner-only atomic site creation that derives tenant and actor from the
  exact current session, creates one `onboarding`/`unverified` site and narrow
  owner grant, selects it, and extends the existing active-site hash chain.
- Added canonical HTTPS DNS origin, IANA timezone, reporting currency, UUIDv4
  idempotency, optimistic session version, duplicate-origin, and 100-site bounds.
  Exact retries converge; stale, conflicting, over-limit, and reduced-authority
  requests fail without partial rows.
- Added strict tenant-CSRF-protected `POST /v1/sites`, an exact-cookie same-origin
  dashboard BFF, and a compact owner-only Add site form with explicit unverified,
  conflict, rejected, and unavailable states.
- Added positive, negative, failure, concurrency, collision, migration, privilege,
  API, dashboard, repository, and responsive-browser tests; ADR-0045; implementation
  and runbook records; and updated API, database, product-status, roadmap, design,
  and service documentation.
- Site creation is configuration intake, not ownership proof or an external SEO
  operation. Origin verification, connectors, crawling, agents, approval, undo,
  provider writes, and production identity authority remain unavailable.

## Slice 0044 - Server-Owned Active-Site Context - 2026-09-09

- Added forward-only migration `0026` with a nullable tenant-bound active site on
  each tenant session and immutable forced-RLS context-transition evidence.
- Added optimistic session-version selection that rechecks current identity,
  recovery, membership, permission, tenant, and site authority; exact reselection
  is idempotent and concurrent stale changes fail closed.
- Added a per-session SHA-256 evidence chain, atomic event-ID collision recovery,
  immutable/hash validation, and function-only runtime authority.
- Advanced current-session responses to schema version 2 and added tenant-CSRF-
  protected `PUT /v1/session/site` with strict unauthorized, denied, conflict,
  unavailable, and failure states.
- Added a same-origin dashboard selection route and active-site controls without
  exposing tenant/user authority, bearer values, API configuration, or cookie
  mutation. Snapshot command authority now requires the server-selected site.
- Added positive, negative, failure, concurrency, revocation, migration, API,
  dashboard, and repository tests; ADR-0044; implementation and runbook records;
  and updated product, design, database, roadmap, and status documentation.
- Site selection is reversible session navigation, not an external operation. Site
  onboarding, ownership verification, connectors, production identity, approvals,
  provider writes, and product undo remain unavailable.

## Slice 0043 - Dashboard Identity BFF - 2026-09-09

- Added same-origin Next.js route handlers for existing-user login initiation,
  callback, explicit organization selection, audited server logout, and separately
  labeled local browser-state cleanup.
- Added strict provider/local redirect allowlists, exact host-only cookie parsing
  and relay, bounded input/response schemas, timeout/no-store/manual-redirect
  transport, and deletion-only cookie handling on API errors.
- Added a server-only pre-tenant organization reader and owner-facing Sign in,
  Choose organization, Logout, Clear browser state, organization-row, and closed
  transition-notice states without exposing credentials or granting site authority.
- Added `GET /v1/session/logout-csrf`, binding logout proof to the tenant cookie
  first and pre-tenant identity second so CSRF issuance and revocation select the
  same authority.
- Added focused positive, negative, and failure tests; route/topology repository
  assertions; dashboard/API configuration guidance; ADR-0043; an implementation
  record; an operations runbook; and updated product/design/status records.
- The default identity gateway remains unconfigured. Real Keycloak regression
  qualifies the unchanged protocol adapter only; the deployed multi-service
  browser journey and production customer authentication remain unavailable.

## Slice 0042 - Authenticated Site Context - 2026-09-09

- Added forward-only migration `0025` with a private UUID-only site-membership
  route index and exact trigger maintenance, leaving forced tenant/site RLS intact.
- Added a hash-bound, recovery-generation-aware PostgreSQL site directory that
  rechecks both session layers and current tenant/site authority, distinguishes a
  valid empty directory, excludes reduced/archived/cross-tenant state, and fails
  closed above 100 sites.
- Added validated control-plane models, composed recovery/database access, and a
  strict `GET /v1/sites` API contract with stable unauthorized/unavailable/failure
  behavior and no browser-supplied tenant scope.
- Added a server-only dashboard site reader with exact-cookie forwarding, bounded
  no-store transport, strict response validation, duplicate rejection, and tenant
  reconciliation against the independently verified session.
- Added an authenticated organization/site panel and truthful zero, unavailable,
  rejected, onboarding, and ownership-unverified states without enabling selection,
  work, connectors, commands, or external writes.
- Qualified migration backfill/rollback, least privilege, current revocation, and
  wrong-tenant behavior across 492 real PostgreSQL cases; API, dashboard,
  repository, build, documentation, and browser results are recorded in the Slice
  0042 evidence. No customer credentials or production authority were used.

## Slice 0041 - Dashboard Session Read Boundary - 2026-09-09

- Added a server-only dashboard current-session reader that forwards only one exact
  validated host-only tenant token rather than the browser's full cookie header.
- Added shared trusted API-origin validation plus no-store, no-redirect, bounded
  timeout/body/declared-length handling for session and capability reads.
- Strictly validate the version-one current-session projection, canonical tenant/
  user IDs, closed roles, primary/MFA level, and future at-most-24-hour expiry.
- Map missing, duplicate, malformed, unauthorized, unavailable, transport-failed,
  unsafe-origin, expired, and invalid-response conditions without exposing raw
  credentials or errors.
- Added responsive signed-out/rejected/unavailable/authenticated account states,
  shortened tenant context, role/MFA display, and explicit absence of site and
  production authority. The server reader discards the validated user ID before
  the view, and the bearer never enters render state.
- Expanded dashboard coverage from nine to 17 tests and extended repository
  assertions for the server-only cookie boundary. Production build, typecheck,
  mobile/desktop browser checks, and the unchanged 624-case Python regression pass
  locally; GitHub Actions remains blocked before checkout by account billing state.
- Added ADR-0041, an implementation/evidence record, dashboard/data-flow guidance,
  design-system account-state guidance, and current roadmap/status documentation.
  Live login, session cleanup/logout, site scope, and production authority remain
  unavailable.

## Slice 0040 - Operational Dashboard Shell - 2026-09-09

### Post-push CI observation

- GitHub accepted commit `6c53eb1`, then rejected both Quality jobs before
  checkout because the account's payments or Actions spending limit requires
  attention. Run `34397200180` executed no repository code and is recorded as
  `NOT_EXECUTED`; it is neither a passing remote gate nor an observed code failure.
- The same account-level block prevented run `34393013320` from remotely verifying
  Slice 0039's Linux bind correction. Both slices retain passing local evidence and
  remain remotely unverified.

- Added the first owner-facing product surface as a pinned Next.js/React/TypeScript
  workspace with a responsive operational Overview and the complete Section 24
  navigation structure.
- Connected server-side rendering to the real public readiness and capability APIs
  with no-store parallel reads, redirect rejection, bounded timeout/body/count,
  exact schema validation, safe errors, and fail-closed production-write posture.
- Rendered connected, not-ready, malformed, misconfigured, and unavailable states
  without invented work, metrics, provider health, or customer authority.
- Kept onboarding, navigation destinations, chat, search, notifications, pause,
  connectors, approvals, changes, analytics, and production writes visibly and
  semantically unavailable until their real contracts exist.
- Added restrictive response headers, production-only removal of development eval
  authority, keyboard focus, non-color status cues, reduced-motion handling, and
  desktop/mobile layouts with no horizontal overflow at 390 pixels.
- Added nine dashboard tests and three repository contract tests, integrated test,
  typecheck, and production build into the root quality gate, and pinned all new
  dependencies in the root lockfile with zero production audit findings. The
  unchanged API, identity, and tooling regression passes 624 cases, and the
  documentation graph passes across 112 Markdown files.
- Added the product register, normative design system, Impeccable design sidecar,
  dashboard guide, ADR-0040, implementation record, evidence record, roadmap/status
  updates, and explicit limits. No customer session, site scope, mutation, undo,
  provider connector, or production authority is enabled.

## Slice 0039 - Durable Crawl Page Attempts - 2026-09-09

### Post-push CI reliability correction

- Reproduced the same fail-closed Keycloak readiness timeout on the first remote
  Quality run and its failed-job rerun after all preceding checks passed.
- Extended only the digest-pinned disposable provider's bounded cold-start window
  from 90 to 180 seconds; request timeout, polling bound, TLS, loopback exposure,
  synthetic identity, resource limits, exact cleanup, and protocol checks remain
  unchanged.
- Extracted the readiness loop and added three positive, transient, and deadline
  tests. All 14 focused tests and five real Keycloak scenarios pass locally with
  refreshed source-hashed evidence and no production authority; all three real
  Temporal cases also pass with refreshed tooling-test provenance.
- The first 180-second correction run disproved the timeout-only hypothesis. The
  Linux runner retained its host UID on the private TLS bind while Keycloak ran as
  UID 1000, making the `0600` key unreadable even though Docker Desktop passed.
- Bound the container process to the invoking non-root host UID and Keycloak's
  existing GID 0, rejecting root-host and non-POSIX execution before creation.
  Private key modes, resource limits, provider pinning, and cleanup remain intact;
  all 16 focused, five real Keycloak, and three real Temporal cases pass locally.

- Added forced-RLS durable page-attempt receipts that bind one exact frontier
  lease/worker, current robots snapshot/reason, global HTML permit, URL, and
  dispatch time before HTTP I/O.
- Added an authority-free coordinator using separate short database operations
  around pinned network and encrypted artifact I/O. Only a newly inserted dispatch
  may fetch; an unresolved dispatch never authorizes a blind duplicate request.
- Added exact historical observation lookup and recovery. Observation-backed or
  closed-failure page settlement now completes the global permit atomically with
  the one-way terminal attempt transition.
- Kept database commit uncertainty as unresolved dispatch and reject failed
  settlement whenever exact observation evidence already exists.
- Serialized first dispatch against legacy observation commit and guarded late
  observation insertion so concurrent paths cannot create contradictory evidence.
- Added bounded `429`/`503` Retry-After classification with provider/fallback/cap
  provenance and conservative local-persistence degradation without inventing an
  HTTP observation.
- Added migration rollback, negative, failure, authority-reduction, privilege,
  immutability, and concurrent exact-call tests, plus a joint disposable
  PostgreSQL/isolated-network qualification and mandatory CI gate.
- Added ADR-0039, an implementation record, a page-attempt runbook, and current
  frontier/robots/admission/artifact guidance. Frontier/byte settlement, parsing,
  coverage, Temporal registration, production resolver/egress, distributed
  artifacts, and production crawl authority remain absent.
- Stable-tree verification passes 624 non-database Python cases, 477 real
  PostgreSQL cases, one joint page-attempt case, seven crawler-network cases, three
  Temporal cases, one joint consumer case, six consumer-image cases, five Keycloak
  cases, seven OpenBao cases, 16 repository cases, and checks across 107 Markdown
  files. Every provider report records completed cleanup and no production authority.

## Slice 0038 - Global Origin Admission - 2026-09-09

- Added transactional canonical-origin buckets shared across tenants, with one
  profile-V1 request token, one in-flight request, at least one-second spacing,
  and stricter per-request delay up to 60 seconds.
- Added short exact `robots` and `html_navigation` permits bound to the current
  frontier lease, worker, workflow, origin, and active tenant/site lifecycle through
  an opaque authority fingerprint and function-only crawler-admission role.
- Added retry-safe completion and global `429`, `503`, transport, and slow-response
  backoff without holding database locks across network work. Completed/expired
  receipts cannot be reused as request authority.
- Added bucket-local and bounded global recovery for abandoned permits, immutable
  lease identity/completion guards, cross-tenant concurrency tests, exact retry and
  conflict tests, authority-reduction tests, and transactional migration rollback.
- Hardened database integration fixtures to use PostgreSQL's clock, avoiding
  host/container skew in expiry tests without changing production behavior.
- Added ADR-0038, an implementation record, an origin-admission runbook, schema/
  ownership guidance, and explicit limits for fairness, live composition, egress,
  retention, telemetry, and production authority.
- Verification covers 22 new non-database cases, 17 new and 462 cumulative real
  PostgreSQL cases, 607 cumulative non-database Python cases, seven crawler-network,
  three Temporal, one joint consumer, six consumer-image, five Keycloak, seven
  OpenBao, 16 repository cases, and 104 Markdown files. Every provider report
  records completed cleanup and no production authority.

## Slice 0037 - RFC-Aware Robots Retrieval And Snapshots - 2026-09-09

- Added exact `/robots.txt` retrieval through the public-address-screened pinned
  HTTP boundary, with admitted redirects, a 500 KiB text limit, closed response
  metadata, and conservative status outcomes.
- Pinned Protego 0.6.2 behind a bounded versioned RFC 9309 profile with strict
  UTF-8 line isolation, tested group/match behavior, resource limits, and no raw
  rule logging.
- Added forced-RLS immutable robots snapshots bound to one crawl run, origin, and
  fetch profile, with exact/concurrent retry, expiry, current-selection, and
  function-only ingest authority.
- Reused encrypted artifacts and upload-readback attestations for successful raw
  robots evidence; current decisions fail closed for absent, expired, mismatched,
  unreadable, or non-verified bodies. Only cached `404` allows.
- Added ADR-0037, an implementation record, a robots runbook, migration guidance,
  dependency/security provenance, and explicit no-production-authority limits.
- Verification covers 23 focused non-database cases, 13 new and 445 cumulative
  PostgreSQL cases, seven isolated-network cases, 585 non-database Python cases,
  three Temporal cases, one joint consumer case, six consumer-image cases, five
  Keycloak cases, seven OpenBao cases, 16 repository cases, and 101 Markdown files.
  Every provider report records completed cleanup and no production authority.

## Slice 0036 - Encrypted Artifact And Fetch Observation Durability - 2026-09-09

- Added a private local artifact backend with AES-256-GCM authenticated envelopes,
  random nonces, deterministic immutable keys, owner-only paths, atomic no-overwrite
  publication, readback verification, and conflict-safe retries.
- Added forced-RLS artifact, append-only attestation, and exact lease-bound fetch-
  observation records with sanitized headers, public-address/time validation,
  scoped foreign keys, deterministic identities, and guarded durability transitions.
- Added function-only `signal_crawl_ingest` authority with no direct table access,
  plus fail-closed reads for missing/corrupt/unreadable evidence and verified restore
  transitions.
- Added bounded grace-period reconciliation that preserves recent and registered
  objects, deletes only old parseable unregistered objects, and leaves unreadable
  candidates for investigation.
- Added ADR-0036, an implementation record, an artifact runbook, dependency/license
  notes, database ownership/migration guidance, and explicit production limits.
- All 562 non-database Python, 171 API, 432 real PostgreSQL, three real Temporal,
  one joint consumer, six consumer-image, five crawler-network, five Keycloak,
  seven OpenBao, and 16 repository cases pass; documentation checks cover 98
  Markdown files. No production authority is enabled.

## Slice 0035 - Durable Crawl Run And Frontier Admission - 2026-09-09

- Added forced-RLS crawl run, stable URL inventory, and lease-safe frontier tables
  bound to one exact running Temporal workflow and immutable scope/limit snapshots.
- Added deterministic run/URL/frontier identities, normalized discovery provenance,
  exact-origin, depth, URL-count, duration, and attempt ceilings, and guarded
  current-lease transitions.
- Added a function-only non-owner crawler-admission role with current workflow,
  tenant, and site checks for new work while preserving exact accepted retries.
- Added transactional migration failure, concurrent open/enqueue/claim, lease
  expiry/reassignment, authority reduction, isolation, mutation, and malformed
  configuration tests against disposable PostgreSQL 17.11.
- Added ADR-0035, an implementation record, and a crawl-frontier runbook. Fetch
  observations, artifacts, robots, global origin admission, workflow composition,
  and production crawl authority remain disabled.
- All 553 non-database Python, 171 API, 411 real PostgreSQL, three real Temporal,
  one joint consumer, six consumer-image, five crawler-network, five Keycloak,
  seven OpenBao, and 16 repository cases pass; documentation checks cover 95
  Markdown files.

## Slice 0034 - Crawl URL And Network Boundary - 2026-09-09

- Added separate original, fetch, display, origin, request, and dedup URL identities
  with exact-origin scope and preserved path/query semantics.
- Added complete-set public IPv4/IPv6 admission and a resolver-injected HTTP fetcher
  that pins the numeric socket peer, retains TLS hostname verification, and manually
  revalidates every redirect.
- Added GET-only authority-free request headers, sanitized response metadata,
  framing/truncation checks, bounded HTML/XHTML retention, hashes, and explicit
  oversized, unsupported-media, and unsupported-encoding outcomes.
- Added a digest-pinned non-root real-network lab with a synthetic origin on an
  internal non-masqueraded public-shaped subnet, no host publication or mount, and
  invocation-owned cleanup.
- Added ADR-0034, an implementation record, an operator runbook, CI/static gates,
  60 focused positive/negative/generated/failure cases, and five real-network
  cases. Production crawl authority and workflow registration remain disabled.
- All 553 non-database Python, 171 API, 390 real PostgreSQL, three real Temporal,
  one joint consumer, six consumer-image, five Keycloak, seven OpenBao, and 16
  repository cases pass; documentation checks cover 92 Markdown files.

## Slice 0033 - Workflow Consumer Packaging And Health - 2026-09-09

- Added a release-bound four-phase consumer health state with separate loopback
  liveness/readiness probes, recent clean-cycle semantics, staleness, dependency
  degradation, and readiness closure before cooperative drain.
- Required production database DSNs to come from bounded owner-only regular files
  opened without following symlinks, while preserving explicit literal-loopback
  development and verified database/Temporal TLS boundaries.
- Added a digest-pinned Python 3.12.14 OCI image with an exact runtime-only graph,
  fixed non-root identity, health check, and immutable source release plus a
  private digest-only Compose contract with secrets and resource limits.
- Added an invocation-owned real Docker lab covering image metadata, networkless
  read-only startup, missing-authority failure, health transition, clean SIGTERM,
  bounded external-supervisor retry exhaustion, and exact cleanup; no image was
  published or granted production authority.
- Added ADR-0033, package/deployment documentation, CI coverage, and explicit
  blockers for signing, SBOM/provenance, vulnerability scanning, monitoring,
  private-pilot deployment, workflow-worker build IDs, crawling, and artifacts.
- All 493 non-database Python, 171 API, 390 real PostgreSQL, three real Temporal,
  one joint consumer, six real Docker image, and 14 repository cases pass;
  documentation checks cover 88 Markdown files and production authority remains
  disabled.

## Slice 0032 - Crawl Workflow State And Terminal Projection - 2026-09-09

- Added the deterministic production `CrawlSite` workflow definition with exact
  input identity, pinned scope/policy versions, bounded activity timeouts, typed
  retries, heartbeats, sanitized failures, cooperative cancellation, and replay.
- Added an injected crawl-executor boundary and bounded manifest-reference contract
  while deliberately leaving network crawling, SSRF/redirect enforcement, and
  artifact persistence disabled for their own qualification slice.
- Added atomic success, failure, and cancellation projection across command state,
  workflow reference, and append-only event four through one function-only
  workflow-role operation, plus authorized terminal status responses.
- Kept start receipts stable after terminal completion and allowed already-running
  work to record its truthful outcome after scope suspension, closing fast-workflow
  and acknowledgement-loss races without moving current state backwards.
- Upgraded the joint PostgreSQL/Temporal lab to run the production workflow with a
  synthetic executor. All 468 non-database Python, 171 API, 390 real PostgreSQL,
  three real Temporal, one joint consumer, and 12 repository cases pass;
  documentation checks cover 84 Markdown files. No production crawler, deployed
  worker, or external authority exists.

## Slice 0031 - Workflow Command Consumer - 2026-09-08

- Added an executable asynchronous consumer that composes fenced outbox claim,
  exact workflow admission, deterministic Temporal start, durable first-run
  recording, and acknowledgement without holding database transactions across I/O.
- Kept definite rejection separate from unknown outcomes, validated every adapter
  result before delivery, drained the active envelope on `SIGINT`/`SIGTERM`, and
  emitted only closed sanitized JSON-lines observations.
- Added strict separate-role runtime configuration, verified transport outside
  explicit literal loopback, bounded TLS material, and an operator runbook while
  leaving health, deployment, and external supervision explicitly unimplemented.
- Fixed duplicate workflow admission to return its stable original receipt after
  later projection progress, enabling ack-loss redelivery to converge on one
  Temporal workflow and first run.
- Added unit, migration, and a disposable joint PostgreSQL/Temporal integration lab
  with source-hashed evidence. All 443 non-database Python, 372 real PostgreSQL,
  one standalone Temporal, one joint consumer, and 12 repository cases pass; no
  production workflow or production authority exists.

## Slice 0030 - GitHub-First Core V1 Contract - 2026-09-08

- Added preserved engineering specification Revision 3.2, a Core V1 PRD, an
  implementation roadmap, and ADR-0030 to make the owner's GSC, GitHub, public
  site, dashboard, and Telegram journey the first real product test.
- Required all nine non-CMS roles in Core V1 with typed permissions, handoffs,
  evidence, budgets, evaluations, disagreement handling, and deterministic
  authority boundaries; roles do not require nine separate services.
- Moved the clean dashboard, GSC, Telegram, one certified repository recipe,
  PR-only GitHub delivery, deployment observation, live verification, measurement,
  and recovery into Core V1 while explicitly deferring CMS teams and broader GA1
  scope.
- Updated working agreements, indexes, status, and documentation checks without
  claiming new runtime capability or pilot authority. All 12 repository cases and
  documentation checks across 77 Markdown files pass in the recorded clean
  checkout.

## Slice 0029 - Deterministic Temporal Workflow Start - 2026-09-08

- Added a real Temporal Python SDK boundary that starts admitted `CrawlSite`
  commands under their committed deterministic identity, rejects closed-ID reuse,
  deduplicates open executions, and bounds both local and RPC waits.
- Added atomic first-run recording, strict start evidence, guarded
  `admitted`-to-`running` and `workflow_admitted`-to-`processing` projections,
  conflict handling, and authorized HTTP status visibility.
- Added an isolated loopback Temporal lab with source-hashed evidence and caught a
  real serializer incompatibility before commit by replacing a generic mapping
  contract with a shared deterministic dataclass.
- All 388 non-database Python, one real Temporal, 370 real PostgreSQL 17.11, and
  11 repository cases pass. No production workflow implementation, consumer
  process, crawler, provider operation, or production authority exists.

## Slice 0028 - Bounded Outbox Worker - 2026-09-08

- Added a bounded, cooperative delivery loop with deterministic tenant paging,
  one-envelope synchronous claims, and fresh database contexts around every
  scheduler operation.
- Classified positive rejection separately from ambiguous publication, leaving
  unknown outcomes and failed acknowledgements to fenced lease expiry rather than
  claiming false delivery.
- Added sanitized structured observations, per-tenant failure isolation, bounded
  active/idle waits, and observer-failure containment.
- Added 27 focused cases; all 353 non-database Python, 161 API, 350 real
  PostgreSQL 17.11, and 11 repository cases pass. No concrete transport, deployed
  process, Temporal workflow, or production authority exists.

## Slice 0027 - Deduplicated Workflow Admission - 2026-09-08

- Added exact-envelope consumer admission with a durable inbox receipt and one
  deterministic tenant-qualified workflow identity for every accepted command.
- Added atomic command progress and append-only admission evidence while keeping
  idempotent acceptance retries fixed to their original `accepted` receipt.
- Added current tenant/site lifecycle locking, post-suspension duplicate receipts,
  forced RLS, guarded command mutation, and a separate function-only workflow
  role so the publisher cannot fabricate consumer progress.
- Added 18 PostgreSQL and three API cases; all 350 real PostgreSQL 17.11, 161 API,
  and 326 non-database Python cases pass. No Temporal call, workflow run, SEO
  operation, or production authority exists.

## Slice 0026 - Lease-Safe Outbox Dispatch - 2026-09-08

- Added active-tenant paging and bounded `FOR UPDATE SKIP LOCKED` outbox claims
  with database-clock leases and monotonic attempt fencing.
- Added exact live-lease acknowledgement and retry scheduling while retaining the
  stable outbox/event identity required for at-least-once redelivery.
- Replaced blanket outbox immutability with guarded bookkeeping transitions,
  retained immutable payloads, revoked scheduler table access, and restricted API
  inserts to initial envelope columns.
- Added 18 PostgreSQL cases; all 332 real PostgreSQL 17.11 cases pass. No running
  publisher, Temporal consumer, workflow progress, provider call, or production
  authority exists.

## Slice 0025 - Human Command HTTP Ingress - 2026-09-08

- Added CSRF-protected `site.snapshot` acceptance using an exact tenant cookie,
  trusted Origin, same-origin fetch metadata, strict versioned JSON, and one
  bounded idempotency header.
- Added 202 and `Location` response contracts for durable acceptance and an
  actor-owned status route that repeats current recovery and site authorization.
- Composed both operations through clean per-operation database connections after
  reading the independent recovery generation, with stable redacted errors and
  fail-closed default configuration.
- Added HTTP and composition coverage for exact retries, malformed or duplicate
  inputs, proof failures, authorization outcomes, output validation, and dependency
  ordering. All 158 API, 323 non-database Python, 314 real PostgreSQL, and 11
  repository cases pass. No dispatcher, workflow progress, provider call, or
  production authority exists.

## Slice 0024 - Human Command Authority - 2026-09-08

- Added one transaction that derives tenant/user scope from a hash-only tenant
  session, rechecks current recovery and exact site authority, and accepts a
  harmless human-attributed snapshot command with its event and outbox record.
- Expanded the command actor contract compatibly, preserved existing service
  intent, and computed the human idempotency fingerprint inside PostgreSQL from
  tenant, actor, route, target, and canonical body.
- Added an own-command status read that repeats live authority checks while the
  identity role retains no direct command-table write privilege.
- Added 28 PostgreSQL cases; all 314 real PostgreSQL cases pass. No HTTP command
  route, dispatcher, workflow progress, provider call, or production authority
  exists.

## Slice 0023 - Invitation Browser Acceptance - 2026-09-08

- Composed immutable invitation-purpose OIDC completion into a separate
  short-lived host-only proof cookie without issuing a user session or reading the
  recovery authority.
- Added proof-bound CSRF and strict body-only invitation acceptance with exact
  Origin/browser checks, generic denials, success-only proof clearing, and no
  credential reflection in responses or application logs.
- Added two closed sanitized completion-failure reasons while preserving ordinary
  login outage ordering and the existing atomic proof/invitation transaction.
- Added 24 API and eight PostgreSQL cases; all 130 API, 295 non-database Python,
  and 286 real PostgreSQL cases pass. The gateway remains unconfigured by default;
  five real Keycloak and seven real OpenBao regressions also pass. Delivery, UI,
  abuse controls, deployed cleanup, and production access are absent.

## Slice 0022 - Atomic Invitation Proof Acceptance - 2026-09-08

- Required both the short-lived identity proof and invitation bearer in one
  PostgreSQL transaction before creating user, membership, site grant, or audit
  authority, with proof consumption committed only after acceptance succeeds.
- Revoked the identity runtime role's older raw issuer/subject/email acceptance
  function and moved the service contract to hash both opaque credentials.
- Added scheduler-only, forced-RLS cleanup of bounded expired-proof batches while
  preserving direct table denial and exact guarded mutation.
- Added eight net-new PostgreSQL cases; all 278 real PostgreSQL, 106 API, and 271
  non-database Python cases pass. Browser transport and deployed cleanup scheduling
  remain disabled.

## Slice 0021 - Purpose-Bound Invitation Identity Proofs - 2026-09-08

- Bound each durable OIDC attempt to immutable ordinary-login or invitation-
  acceptance purpose before the provider redirect, with existing callers defaulted safely.
- Added short-lived opaque invitation identity proofs from fresh signed identities
  with verified normalized email; only proof hashes and a bounded projection persist.
- Kept proofs outside session and tenant authority, forced exact-hash RLS, denied
  direct mutation/enumeration, and extended pooled-connection contamination checks.
- Added 20 PostgreSQL cases; all 270 real PostgreSQL, 106 API, and 271
  non-database Python cases pass. Browser issuance, proof consumption, acceptance
  HTTP, cleanup, delivery, and production authority remain disabled.

## Slice 0020 - Current Session And Audited Logout - 2026-09-08

- Added current tenant-session inspection that rechecks child and parent expiry,
  recovery generation, enabled identity, active tenant, and current membership.
- Added CSRF-protected logout for selected and pre-tenant browser states without
  allowing identity-proof downgrade when a tenant cookie is present.
- Revoked the parent identity session and appended one strict immutable event in
  the same transaction; repeated and concurrent logout remain idempotent.
- Added 21 non-database and 17 PostgreSQL cases; all 106 API, 271 non-database
  Python, and 250 real PostgreSQL cases pass. Provider logout, session cleanup,
  customer authentication, and production authority remain disabled.

## Slice 0019 - Explicit Tenant Session Selection - 2026-09-08

- Added a protected, trigger-maintained identity-to-membership routing index and
  narrow hash-scoped discovery function without runtime table enumeration.
- Added organization listing, pre-tenant CSRF issuance, and strict same-origin
  tenant selection that rechecks current authority before setting a tenant cookie.
- Kept pre-tenant and tenant credentials separate, hash-only at rest, and bounded
  invalid identity handling to generic failures that clear stale browser authority.
- Added 28 non-database and 16 PostgreSQL cases; all 85 API, 250 non-database
  Python, and 233 real PostgreSQL cases pass. The default process, logout,
  invitation HTTP journey, abuse controls, and customer authentication remain disabled.

## Slice 0018 - Bounded OIDC HTTP Ingress - 2026-09-08

- Added fail-closed login-start and callback routes that compose the existing
  OIDC/OpenBao/session pipeline only when a reviewed gateway is injected.
- Added separate secure HttpOnly host-only browser-binding and pre-tenant identity
  cookies; the latter grants no tenant, site, connector, or write authority.
- Rejected duplicate callback proofs, revalidated redirect/session output, removed
  callback credentials from the ASGI access-log scope, and preserved only
  retryable pre-consumption bindings.
- Added 21 API cases; all 57 API and 222 non-database Python cases pass. The
  default process, tenant selection, logout, invitation HTTP journey, upstream
  ingress redaction, abuse controls, and production customer access remain disabled.

## Slice 0017 - Verified Invitation Acceptance - 2026-09-08

- Added forward migration `0008` with a protected invitation-routing index and
  one narrow identity-role acceptance function; no recipient email or bearer
  material is exposed through the route.
- Bound acceptance to a fresh signed OIDC identity with boolean verified email,
  while preserving exact issuer/subject identity and refusing email-based account
  linking or implicit role changes.
- Provisioned the user, one tenant membership, one-site permission, guarded
  invitation consumption, and chained `invitation.accepted` evidence in one
  transaction with deterministic locks and bounded collision retries.
- Added 20 PostgreSQL cases and four protocol cases; all 217 database cases, 201
  non-database Python cases, and five real Keycloak checks pass. Public invitation
  routes, delivery, throttling, revocation, and production authority remain disabled.

## Slice 0016 - Site Invitation Issuance - 2026-09-08

- Added forward migration `0007` for hash-only, one-site invitation records and
  the first strict tenant `invitation.created` audit event.
- Rechecked the exact current actor, role, membership/site epochs, tenant, and
  site while locking authority rows against concurrent demotion or suspension.
- Enforced owner/admin grant ceilings, excluded owner creation, serialized live
  recipient collisions, and bounded random token-hash collision retries.
- Added exact column privileges that prevent API reads of token hashes and the
  audit stream, plus immutable records and atomic event rollback.
- Added 26 PostgreSQL cases; all 197 database cases pass. Acceptance, identity
  provisioning, delivery, public routes, and production authority remain disabled.

## Slice 0015 - Consumed Login Failure Audit - 2026-09-08

- Added forward migration `0006` with a strict `identity.login.failed` contract
  for an existing consumed OIDC login attempt and six closed reason values.
- Required original state and browser-binding proofs, consumed-object integrity,
  one event per attempt, and no additional runtime read or mutation authority.
- Composed sanitized events into every post-consumption callback failure while
  leaving anonymous and recovery-authority pre-consumption rejects write-free.
- Added eleven PostgreSQL-backed cases for migration atomicity, sanitization,
  proof scope, event limits, failure mappings, and explicit audit failure; all
  171 database cases pass alongside the 197-case Python, 11-case repository,
  seven-scenario OpenBao, and five-scenario Keycloak regressions.
- Kept public login, identity provisioning, audit queries/export, reconciliation,
  checkpoints, production authority, and external writes disabled.

## Slice 0014 - Identity Session Audit - 2026-09-08

- Added forward migration `0005` with a narrowly typed, append-only
  `identity.session.issued` platform event contract.
- Committed each hash-only identity session and its audit event atomically; an
  event failure rolls back issuance before any raw credential is returned.
- Added forced hash-scoped event insertion and exact column-only identity-role
  grants without read, update, delete, truncate, or migration access.
- Added five PostgreSQL cases for migration rollback, strict facts, immutability,
  scoped privileges, and transaction rollback; all 160 database cases pass.
- Kept failed-login events, customer audit APIs, checkpoints, event export, and
  production authority explicitly disabled.

## Slice 0013 - OpenBao Recovery Authority - 2026-09-08

- Added a strict read-only OpenBao boundary for the current recovery generation,
  stored outside the business database under an exact path and scoped credential.
- Reused one bounded HTTPS/JSON transport for OpenBao clients without combining
  PKCE and recovery credentials or capabilities.
- Changed OIDC callback composition to fetch the external generation before
  consuming PostgreSQL state, leaving callbacks retryable during authority outages.
- Added 33 authority cases, one PostgreSQL-backed outage-ordering case, and seven
  passing real OpenBao scenarios including an observed 403 for reader mutation.
- Recorded source-hashed OpenBao and 155-case PostgreSQL evidence; recovery
  rotation, restriction replay, restore drills, and production OpenBao remain disabled.

## Slice 0012 - OIDC Login Composition - 2026-09-08

- Composed Keycloak discovery/exchange/validation, one-time PostgreSQL browser
  proofs, OpenBao PKCE consumption, and existing-user session issuance.
- Added explicit fail-closed commit ordering, fixed redacted stage errors, shared
  pre-side-effect validators, UUID-bound references, and worker-thread DB calls.
- Added 17 PostgreSQL-backed flow cases for success, failure ordering, replay,
  unknown identity, and no raw credential persistence; 154 DB cases pass in total.
- Kept all customer login routes, cookie issuance, authority provisioning,
  production configuration, and release claims disabled.

## Slice 0011 - OpenBao PKCE Secrets - 2026-09-08

- Added a strict HTTPS OpenBao KV v2 client for CAS-zero PKCE verifier creation
  and version-one consumption with permanent deletion confirmed before return.
- Split writer and consumer privileges into create-only and read/delete-only
  policies without list, broad CRUD, embedded token, or database secret storage.
- Added a digest-pinned TLS OpenBao 2.6.1 lab with bounded resources, synthetic
  credentials, sanitized source-hashed evidence, and invocation-owned cleanup.
- Added 59 focused client/tooling cases and five passing real-server scenarios;
  complete customer login and production OpenBao operations remain disabled.
- Reran all 137 PostgreSQL isolation/session cases and recorded source-hashed
  regression evidence after adding the secret client to the control-plane package.

## Slice 0010 - Scoped Session Issuance - 2026-09-07

- Added global and tenant session issuance from a strictly verified OIDC identity
  and current existing user/membership authority; login never provisions access.
- Added fixed ACR and authentication-age policy, parent-bounded expiry, external
  recovery-generation checks, hash-only credentials, and bounded collision retry.
- Added forced hash-scoped RLS for global sessions and exact column-level session
  insert grants without update, delete, truncate, schema, or migration authority.
- Added 42 real PostgreSQL cases, bringing the cumulative suite to 137 passing
  cases with migration rollback, source-hashed evidence, and confirmed cleanup.

## Slice 0009 - Keycloak OIDC Protocol Qualification - 2026-09-07

- Added a Keycloak-specific authorization-code adapter with exact discovery
  endpoints, mandatory S256, bounded HTTP behavior, and Authlib over HTTPX2.
- Added strict RS256/JWKS and issuer, audience, time, nonce, authorized-party, and
  access-token-hash validation that returns no provider credentials.
- Added an invocation-owned Keycloak 26.7.3 TLS lab with a synthetic realm,
  digest-pinned image, bounded resources, sanitized evidence, and exact cleanup.
- Added 50 protocol and 12 Keycloak-specific lab/repository cases; the real
  provider passed five positive and negative protocol scenarios.

## Slice 0008 - Durable OIDC Login Attempts - 2026-09-07

- Added forward-only migration `0003` for short-lived pre-tenant OIDC attempts
  containing state, nonce, and separate browser-binding hashes only.
- Added forced two-proof RLS, immutable records, one-time consumption, and a
  column-limited non-owner identity role with no delete or migration authority.
- Added strict OIDC registration, return-path, TTL, and secret-reference validation
  plus atomic concurrency-safe create/consume services.
- Added 32 real PostgreSQL cases, bringing the cumulative suite to 95 passing cases
  with migration rollback, source-hashed evidence, and confirmed cleanup.

## Slice 0007 - Browser Mutation Security - 2026-09-07

- Added host-only secure session-cookie helpers and session-bound HMAC CSRF proofs
  for future cookie-authenticated mutation routes.
- Added exact trusted-origin and Fetch Metadata checks, duplicate security-input
  rejection, constant-time comparison, and stable non-disclosing denial responses.
- Centralized opaque session-token validation and hashing across the API and
  database authorizer without changing stored credential behavior.
- Added 26 browser-security cases, bringing the API suite to 36 passing cases, and
  reran all 63 PostgreSQL 17.11 cases with source-hashed cleanup evidence.

## Slice 0006 - Truthful Read-Only API Boundary - 2026-09-07

- Added a runnable FastAPI process with liveness, fail-closed readiness, and a
  machine-readable inventory that keeps customer authentication and writes disabled.
- Added bounded correlation IDs, stable safe errors, restrictive response headers,
  redacted catch-all logging, and production-disabled interactive documentation.
- Added 10 direct asynchronous ASGI contract tests and included API source/tests in
  the least-privilege CI lint, format, and test gates.
- Pinned the resolved API dependency graph and reran all 63 PostgreSQL 17.11 cases;
  the source-hashed report records no production authority and completed cleanup.

## Slice 0005 - Least-Privilege Quality CI - 2026-09-07

- Added separate repository and Python/PostgreSQL GitHub Actions jobs on the
  explicit Ubuntu 24.04 hosted image.
- Limited workflow authority to read-only contents, disabled persisted checkout
  credentials, avoided secrets and deployment, and bounded job/concurrency time.
- Pinned official checkout, Node, and Python setup actions to reviewed full commit SHAs.
- Added a parser-based repository test for workflow triggers, privileges, action
  pins, runner bounds, and required commands. Remote execution remains pending.

## Slice 0004 - Session-Derived Site Authorization - 2026-09-07

- Added OIDC issuer/subject identities, global and tenant session records, current
  memberships, and exact site grants in forward-only migration `0002`.
- Added recovery-generation, expiry, revocation, disabled-user, and authentication-level
  checks without accepting a caller-supplied tenant identity.
- Added a hash-matched pre-tenant RLS lookup and a read-only, non-owner
  `signal_identity` role with tested effective privileges.
- Added 25 real PostgreSQL cases for identity and authorization behavior, bringing
  the complete database suite to 63 passing cases with confirmed lab cleanup.
- Recorded ADR-0004 and kept Keycloak, cookie/CSRF, session issuance, public APIs,
  and human command attribution explicitly disabled.

## Slice 0003 - Durable Command Foundations - 2026-09-07

- Added an explicit Alembic migration for six narrowly scoped command-foundation
  tables with forced tenant/site RLS and composite scope integrity.
- Added non-owner runtime roles, a reviewed privilege manifest, and transaction-local
  Python scope handling that rejects contaminated or nested connections.
- Added atomic, idempotent acceptance for internal `site.snapshot` intents with an
  immutable acceptance event and outbox record.
- Added 38 PostgreSQL integration cases and 8 lab safety tests using a digest-pinned
  PostgreSQL 17.11 container, source-hashed evidence, and confirmed cleanup.
- Recorded ADR-0003 and explicit limits: there is no identity API, dispatcher,
  command execution, approval flow, or production authority yet.

## Slice 0002 - WordPress Core Feasibility - 2026-09-07

- Added an isolated, digest-pinned WordPress/MariaDB lab and CLI-only prototype.
- Added 21 real-stack scenarios for conditional changes, concurrent editors and
  duplicate operations, receipts, crash rollback, metadata, hooks, and recovery.
- Added Compose isolation checks, a source-hashed report, and ADR-0002. The tests
  demonstrate provider limitations; production publishing remains disabled.
- Full Milestone 0 is incomplete: synthetic metadata is not Yoast support and
  persistent caches/CDN and actual pilot side effects remain unqualified.

Changes are recorded per completed implementation slice. This is not a list of
production releases; deployment and certification status live in the implementation
records and release evidence.

## 2026-09-07 - Slice 0001: Repository Baseline

- Preserved original Revision 3.0 and corrected Revision 3.1 specifications.
- Added implementation status, decision records, contribution rules, and slice documentation.
- Added locked, parser-based documentation checks and negative repository tests.
- Established local Git history on `main`; no remote publication or production deployment.
