# Signal Core V1 Product Requirements

> **Superseded 2026-09-24.** The active product direction is the
> [product requirements](prd.md) with
> [Revision 4.0](../../Signal_Production_Engineering_Specification_Revision_4_0.md)
> ([ADR-0060](../adr/0060-autonomous-seo-employee-direction.md)). This Core V1 PRD is
> preserved as history; its safety conditions still apply, but its scope, deferrals,
> and sequence do not.

Status: **SUPERSEDED BY REVISION 4.0; PRESERVED HISTORY**.

Owner: Signal product and engineering.

## 1. Authority And Purpose

This PRD defines the first coherent, owner-tested Signal product. It is subordinate
to the normative [Revision 3.2 engineering specification](../../Signal_Production_Engineering_Specification_Revision_3_2.md)
and must be read with the [Core V1 roadmap](../implementation/core-v1-roadmap.md),
[implementation status](../implementation/status.md), and applicable ADRs.

The documents have these responsibilities:

| Document | Authority |
| --- | --- |
| Engineering specification Revision 3.2 | Normative safety, state, interface, data, and release contract |
| This PRD | Core V1 product scope, user journey, requirements, and acceptance |
| Core V1 roadmap | Implementation order and evidence sequence |
| ADRs | Consequential implementation decisions and supersessions |
| Implementation records and status | What actually exists and what was actually tested |

Core V1 narrows breadth, not engineering quality. "MVP" never means skipping
current authorization, tenant isolation, idempotency, failure handling,
observability, independent verification, recovery, or real-provider testing.

## 2. Product Objective

Signal should behave like a trustworthy SEO AI employee for one owner-controlled
website. The owner connects the website's Google Search Console property, GitHub
repository, public deployment, and Telegram account, then sees Signal continually:

```text
Observe -> Research -> Plan -> Propose -> Test -> Review -> Request approval
        -> Create PR -> Observe delivery -> Verify live -> Measure -> Report
        -> Learn or recover
```

The product must reduce useful SEO work without inventing evidence, hiding
uncertainty, or receiving authority to merge or deploy. It should feel persistent
and coordinated: work survives restarts, specialists have visible responsibilities,
decisions have evidence, blocked work says why, and the dashboard and Telegram
show the same durable truth.

## 3. Pilot User And Fixed Resources

The first real user is the product owner operating one invited organization. Pilot
admission binds all of the following before any consequential run:

- one verified, owner-controlled public website origin;
- one matching Google Search Console property with least-privilege read access;
- one GitHub App installation, repository, and base branch;
- one documented framework, dependency, build, test, and preview/deployment path;
- one paired Telegram identity and bot environment;
- one reviewed repository-change recipe selected for the actual site stack;
- one approved OpenAI model/prompt/tool cohort and nine role versions; and
- explicit budgets, recovery window, success scorecard, and operating owners.

Changing any bound resource is expansion or requalification, not an invisible
configuration edit. Synthetic second tenants and wrong-resource fixtures remain
required for isolation tests even though the live pilot has one organization.

## 4. Non-Negotiable Employee Behaviors

Core V1 must:

1. Maintain a durable queue of work and recover coherently after restarts.
2. Separate observed facts, inferences, recommendations, approved intent, external
   receipts, live verification, and later outcome measurements.
3. Explain priorities, evidence, uncertainty, expected effect, cost, and risk.
4. Coordinate specialists through typed work rather than an unbounded group chat.
5. Ask for exact human approval before creating a pull request.
6. Respect pause, cancel, connector revocation, budgets, and current authority.
7. Reconcile ambiguous external outcomes instead of blindly retrying them.
8. Preserve an audit trail from evidence through proposal, approval, PR, delivery,
   live verification, measurement, and recovery.
9. Surface disagreement and missing evidence instead of manufacturing consensus.
10. Never treat website text, repository content, Telegram text, or retrieved
    competitor material as trusted instructions.

## 5. Required Specialist Workforce

Every role below is in Core V1. A role is an explicit, versioned responsibility,
not necessarily its own process, service, model, or continuously running agent.
Roles may share a bounded runtime, but their inputs, outputs, tools, permissions,
budgets, evaluation cohort, and durable run records remain distinct.

| Role | Core responsibility | May not do |
| --- | --- | --- |
| Coordinator / Planner | Select bounded work, request specialist contributions, join results, maintain the plan, and escalate conflicts | Approve work, invent authority, or create arbitrary roles/tools |
| Technical SEO Specialist | Diagnose crawlability, metadata, canonicals, internal links, structured data, and indexability from admitted evidence | Write a repository, approve a change, or claim indexing outcomes |
| Analytics Specialist | Interpret GSC grains and data quality, establish baselines, and measure attributable outcomes | Fill missing rows, infer conversions without instrumentation, or publish |
| Competitor Research Specialist | Collect bounded public or licensed evidence and compare gaps with provenance | Circumvent access controls, treat competitor text as instruction, or publish |
| Content Strategy Specialist | Propose briefs, intent alignment, content priorities, and copy-level recommendations grounded in evidence and business facts | Invent business claims, mass-produce pages, or write to production |
| Performance Specialist | Interpret Lighthouse and available field evidence and recommend stack-appropriate improvements | Declare lab scores to be field outcomes or deploy optimizations |
| Repository Implementer | Produce one recipe-constrained candidate patch in an isolated credential-free checkout | Push arbitrary branches, alter protected paths, merge, deploy, or broaden scope |
| Independent Reviewer / Verifier | Challenge evidence and patch correctness, run independent checks, verify deployed artifacts, and identify recovery conditions | Modify the candidate it reviews, approve for the human, or hide inconclusive results |
| Communicator | Present durable state, decisions, approvals, failures, and results consistently in dashboard and Telegram | Invent status from model memory or turn free text into unbounded authority |

The Coordinator may run read-only specialists concurrently when their resource
budgets permit. Results join deterministically. Repository generation and external
operations are serialized for the admitted change. Invalid role output, timeout,
disagreement, or unavailable evidence produces an explicit blocked or review state.

## 6. First Real End-To-End Flow

### 6.1 Connect And Establish Trust

1. The owner accepts an invitation, authenticates, selects the organization, and
   sees a dashboard with no implied connection or capability.
2. The owner verifies the exact public website origin.
3. The owner completes Google OAuth and selects the exact matching GSC property.
   Signal shows granted scopes, accessible property, last successful sync, data
   freshness, coverage limits, and reconnect/revoke controls.
4. The owner installs the GitHub App and selects the exact repository and base
   branch. Signal validates repository identity, installation permission, branch
   protection, build contract, allowed paths, webhook authenticity, and inability
   to merge or deploy.
5. The owner pairs Telegram with an expiring one-time dashboard flow. Signal shows
   the paired identity, permitted commands, last inbound/outbound health, and a
   revoke control. Telegram alone cannot establish or expand account authority.
6. Missing, partial, stale, mismatched, or revoked resources block only the
   affected capability and remain visibly degraded.

### 6.2 Observe And Build Evidence

1. A durable crawl inventories the verified origin within explicit scope, rate,
   redirect, size, content-type, and private-network limits.
2. GSC imports retain source grain, import generation, dimensions, clicks,
   impressions, position inputs, freshness, and quality flags.
3. Performance collection records Lighthouse lab evidence and permitted field
   evidence without conflating them.
4. Competitor research is bounded by declared competitors or an approved discovery
   policy, public/licensed sources, provenance, robots/access rules, and cost limits.
5. Approved business facts, goals, constraints, prior decisions, crawl evidence,
   analytics, and research are stored separately and retrieved only for the current
   tenant, site, role, and task.

### 6.3 Analyze, Plan, And Propose

1. The Coordinator creates a bounded task and sends typed requests to all relevant
   specialists. Every required Core V1 role participates in the pilot journey;
   not every role must produce a recommendation for every finding.
2. Specialists return schema-valid evidence references, conclusions, uncertainty,
   risks, cost, and proposed next steps. Unsupported claims are rejected or marked.
3. The Coordinator joins results, preserves disagreement, ranks work against the
   predeclared goals, and selects one supported change under the certified recipe.
4. The Repository Implementer creates an exact candidate patch from a pinned base
   commit in an isolated, credential-free sandbox.
5. Build, test, lint, route, artifact, and policy checks required by the selected
   recipe run against the exact candidate. Dependency installation and scripts
   follow the recipe's network and execution policy.
6. The Independent Reviewer validates scope, evidence, diff, tests, expected live
   marker, deployment observation plan, and inverse/recovery patch. It cannot edit
   the candidate it is reviewing.

### 6.4 Human Control And Pull Request Delivery

1. The dashboard displays the exact immutable revision, base/head identities,
   changed files, evidence, specialist rationale/disagreement, tests, risks, cost,
   live verification plan, and recovery plan.
2. Telegram may notify, summarize, reject, pause, cancel, or initiate an approval
   handoff. High-impact approval completes only through the authenticated,
   step-up-protected flow named by policy.
3. Approval binds the exact manifest and expires or becomes stale when its base,
   target, permission, recipe, policy, recovery generation, or candidate changes.
4. Signal creates one pull request using the stable operation identity. It never
   merges the PR, approves branch protection, writes workflow files, alters admin
   settings, or directly triggers deployment.
5. Lost GitHub responses, rate limits, base drift, duplicate webhooks, or ambiguous
   PR identity enter reconciliation or conflict states. They do not cause a blind
   second PR.

### 6.5 Observe, Verify, Measure, And Recover

1. Signal observes the customer-controlled CI and deployment path and binds the
   deployed artifact to the approved commit when the configured provider permits.
2. An external success signal or green build is not live verification. The
   Independent Reviewer fetches the bound public origin and checks the expected
   artifact marker and relevant page behavior.
3. A timeout, wrong commit, wrong origin, cache ambiguity, or missing marker remains
   inconclusive or failed and is visible to the owner.
4. Later GSC and crawl measurements compare compatible source grains and windows.
   Ranking or traffic improvement is never guaranteed or fabricated.
5. Recovery creates a new reviewed revert pull request using a three-way,
   current-state-aware inverse. It preserves unrelated later edits and blocks on
   overlap. Signal still has no merge or deployment authority.

## 7. Functional Requirements

### 7.1 Identity, Tenancy, And Onboarding

- Invitation, OIDC login, MFA/step-up where required, tenant selection, session
  inspection, logout, invitation revocation, and cleanup must be complete.
- Every read and write derives tenant/site authority from current server-side
  state. URL, body, Telegram, model, and connector identifiers are not authority.
- Fresh-browser onboarding must work without an operator editing the database.
- Error messages are actionable without exposing credentials, provider payloads,
  tenant existence, internal topology, or cross-tenant identifiers.

### 7.2 Connections

- Connection state is explicit: unconfigured, connecting, connected, degraded,
  expired, revoked, permission-reduced, error, and requalification-required.
- GSC, GitHub, public origin, and Telegram expose identity, scope, freshness,
  health, last success/error, reconnect/revoke, and affected-capability impact.
- Secrets remain in the secret boundary. Logs and ordinary records contain
  references and sanitized evidence, never reusable tokens.
- Webhook signatures, delivery IDs, timestamps, replay bounds, resource binding,
  and idempotent processing are verified before state changes.

### 7.3 Evidence, Memory, And Research

- Findings cite immutable or content-addressed evidence with capture time,
  source, scope, and quality.
- Search and semantic retrieval are tenant/site scoped, versioned, bounded, and
  observable. Retrieved text is untrusted context, not instructions or authority.
- Business facts distinguish proposed, approved, superseded, and contradicted
  state. A user can inspect and correct them.
- Data retention, export, deletion, legal/operation holds, and backup limitations
  are visible and tested for all Core V1 stores.

### 7.4 Work, Agents, And Plans

- Every work item has durable state, owner, site, trigger, priority, budget,
  dependencies, evidence, role runs, and terminal/blocking reason.
- Every role run records input/output schema version, model/prompt/tool release,
  allowed tool set, evidence references, token/cost use, latency, and outcome.
- Tool authorization is deterministic and checked again at execution time.
- Role quality is evaluated through unit/contract cases, hostile content,
  held-out tasks, disagreement cases, missing-data cases, and end-to-end pilot work.

### 7.5 Dashboard

The Core V1 dashboard is a clean operational interface, not a marketing shell. It
must include:

| View | Required content and controls |
| --- | --- |
| Onboarding and connections | Exact resource identity, permissions, health, freshness, reconnect/revoke, and blocked capability |
| Overview | Site health, current work, approvals, blocked/degraded items, recent verified outcomes, and budget state |
| Findings and evidence | Filterable findings, severity/confidence, source captures, analytics quality, and specialist analysis |
| Plan and work queue | Priorities, dependencies, role participation, status, blockers, cost, and next action |
| Change review | Exact diff, base/head, evidence, tests, risks, approval scope/expiry, verification, and recovery |
| Activity and conversations | Durable audit-linked timeline shared with Telegram, with source and delivery state |
| Settings and safety | Site, GSC, GitHub, Telegram, budgets, pause, sessions, members, export/deletion, and support diagnostics |

All views require loading, empty, degraded, stale, permission-lost, validation,
conflict, retry/reconciliation, success, and no-access states. Keyboard operation,
focus, labels, contrast, responsive layout, error recovery, and long-content behavior
are acceptance requirements. The UI never labels proposed work as applied or a PR
as live.

### 7.6 Telegram

- Pairing begins from an authenticated dashboard session with an expiring,
  single-use, purpose-bound proof.
- Accepted intents are a closed schema such as status, explain, list approvals,
  reject, pause, cancel eligible work, and begin an authenticated approval handoff.
- Chat/user identity, bot environment, tenant/site binding, replay identity, and
  current membership are rechecked. Replayed, reordered, forged, stale, or
  cross-tenant messages fail closed.
- Notifications derive from committed durable events, include no secrets, observe
  user preferences, and do not claim delivery from an API acknowledgement alone.
- Free text may inform a proposed task but cannot grant authority, expand scope,
  alter policy, or approve a consequential operation.

### 7.7 GitHub Candidate And PR

- The selected recipe pins allowed paths, forbidden paths, base branch, build and
  test commands, expected artifacts, network policy, maximum change size, and
  recovery method.
- Checkout, dependency resolution, model/tool execution, and builds run without
  production credentials or unrestricted host access.
- The final candidate is content-addressed and tied to its base commit, exact diff,
  test evidence, recipe, policy, role releases, and approval manifest.
- GitHub operations use a least-privilege App installation and one stable operation
  identity. PR discovery and reconciliation handle lost acknowledgements.
- Branch protection, merge, deployment, repository administration, secret access,
  workflow modification, and arbitrary command execution are outside authority.

### 7.8 Verification, Measurement, And Recovery

- CI state, deployment state, live state, and measured outcome are distinct.
- Verification uses the exact bound origin and expected candidate identity; redirects
  to another origin, stale caches, unavailable markers, and partial observation are
  explicit outcomes.
- Measurement retains comparable dimensions, dates, import generations, and data
  quality. It can conclude insufficient data or no attributable change.
- Recovery is planned before approval, produces a new reviewable PR, and is tested
  for exact state, unrelated later edits, overlapping edits, ambiguity, and restart.

### 7.9 Operations And Data Protection

- Health, readiness, metrics, traces, logs, audit events, connector diagnostics,
  work state, cost ledgers, and alert ownership cover the entire pilot path.
- Bounded timeouts, retries, backoff, concurrency, request size, crawl scope, token
  use, spend, and artifact retention exist at every external boundary.
- Backup, restore, restriction replay, new recovery generation, frozen egress,
  reconciliation, and fresh authority are tested before consequential authority.
- Pause and connector revocation block new work promptly while preserving enough
  state to reconcile already accepted external actions.

## 8. Core V1 Definition Of Done

Core V1 is pilot-admissible only when all items below have reproducible evidence:

- the current repository status marks every required capability implemented and
  tested without contradicting its implementation records;
- all nine roles pass their permission, schema, budget, hostile-input, failure,
  disagreement, and held-out quality checks;
- a fresh browser can complete invitation, login, site verification, GSC/GitHub/
  Telegram connection, initial evidence sync, work review, and logout;
- the exact owner-controlled resources complete the full flow in section 6;
- dashboard and Telegram remain consistent with durable state through restart,
  delayed events, duplicate delivery, revocation, and partial outages;
- the chosen recipe passes isolated build, negative path, protected-path, stale-base,
  reviewer independence, approval-staleness, and cost-limit tests;
- PR creation, lost-response reconciliation, webhook replay, external deployment
  observation, live verification, measurement, and conflict-aware revert PR work;
- cross-tenant, wrong-site, wrong-property, wrong-repository, wrong-origin, and
  wrong-Telegram-identity tests fail closed;
- backup restoration and restriction replay cannot resurrect stale authority;
- applicable specification gates G-00 through G-11 pass, including G-06 through
  G-08 for PR/recovery side effects and G-09 for GitHub delivery; and
- the predeclared usefulness scorecard is completed honestly and signed by the
  product and operating owners.

Safe execution alone is insufficient. The pilot must also demonstrate that the
recommendation was useful, the output was correct, review effort was reasonable,
cost was fully attributed, delivery was verified, and recovery was understandable.
Numeric thresholds and the exact test task must be fixed before the live run, not
selected afterward to make the result pass.

## 9. Required Test Matrix

| Lane | Minimum evidence |
| --- | --- |
| Positive | Complete owner flow with real dedicated GSC, GitHub, public site, and Telegram resources |
| Authorization | Current membership/site binding, step-up, revocation, pause, session expiry, and least privilege |
| Isolation | Synthetic second tenant/site/property/repository/chat cannot observe or affect the pilot |
| Data quality | Empty, partial, delayed, duplicate, changing-grain, and stale GSC/crawl/performance inputs |
| Agent quality | Valid, invalid, missing, conflicting, injected, over-budget, timed-out, and low-confidence role outputs |
| Repository | Dirty/stale base, protected path, symlink/submodule, oversized diff, dependency/script risk, build/test failure |
| GitHub | Lost acknowledgement, duplicate operation, rate limit, permission reduction, forged/duplicate/reordered webhook |
| Delivery | Wrong commit, wrong environment, failed build, failed deployment, delayed deployment, missing artifact identity |
| Live verification | Redirect mismatch, cache lag, marker absent, origin unavailable, partial page failure, inconclusive state |
| Telegram | Pairing expiry, replay, wrong identity, revoked member, reordered updates, send ambiguity, provider outage |
| Recovery | Restart at each durable boundary, backup restore, stale restriction, exact revert, unrelated edit, overlap conflict |
| UX and operations | Fresh browser, accessibility, responsive layouts, actionable failures, diagnostics, alerts, runbooks, support handoff |

Fakes supply repeatable fault injection. They supplement rather than replace the
real GSC, GitHub, Telegram, public-site, PostgreSQL, Temporal, identity, secret,
and build/deployment-path qualifications on which the contract depends.

## 10. Explicitly Deferred Beyond Core V1

The following are deliberately deferred and must remain visibly unavailable:

- CMS-specific teams and production CMS writes are deferred beyond Core V1;
- production WordPress or other CMS write teams, CMS credentialed delivery, and
  broad CMS/version/plugin compatibility certification;
- GitHub merge, deployment, repository administration, branch-protection changes,
  protected workflow edits, and production-secret access;
- standing grants or unattended consequential autonomy;
- GA4 ingestion or conversion claims that depend on it;
- additional change recipes beyond the one certified for the pilot stack;
- paid or private competitor-data providers not selected and licensed for Core V1;
- broad legal/policy jurisdiction coverage beyond the reviewed pilot policy;
- enterprise billing, support automation, advanced administration, and broad
  product analytics that are not needed to operate the pilot safely;
- multi-site scale, GA1 capacity targets, and the complete GA1 compatibility matrix;
- new-page publication, bulk generation, mass redirects, indexing-control changes,
  shared-template changes, destructive operations, and unrestricted performance
  deployments.

The deferral list does **not** include any of the nine roles, the operational
dashboard, Telegram, GSC, the GitHub PR path, evidence/memory, bounded competitor
research, content strategy, analytics, performance analysis, independent review,
live verification, measurement, observability, or recovery.

## 11. Pilot Configuration Decisions

These values must be recorded before implementation of their dependent slice.
They are configuration decisions, not permission to reinterpret Core V1 scope:

- exact public origin and canonical origin rules;
- exact GSC property and expected access level;
- GitHub organization, App installation, repository, base branch, and allowed paths;
- site framework, package manager, lockfile, runtime, and build/test commands;
- CI provider, preview/production deployment provider, environment identity, and
  the artifact/commit signal available for observation;
- Telegram bot environment and owner account pairing;
- the single change recipe and representative first task;
- model, prompt, tool, and role release cohort;
- budgets, retention windows, alert owners, recovery window, and scorecard thresholds.

Until these are fixed and qualified, dependent functionality remains planned or
blocked. Documentation must never fill an unknown configuration with an assumption.
