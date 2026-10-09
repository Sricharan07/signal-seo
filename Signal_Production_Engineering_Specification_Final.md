# Signal
## Production Engineering Specification and Build Contract

**Revision:** 3.0, final reviewed specification  
**Prepared:** September 7, 2026  
**Supersedes:** `Signal_Self_Hosted_Build_Blueprint.md`  
**Product:** Self-hosted, persistent SEO AI employee with an OpenAI reasoning brain  
**Document status:** Architecture, implementation contracts, verification requirements, and release criteria. This is not an implemented or independently certified product.

> Signal must earn production authority through evidence. A model's confidence, a successful API response, a green dashboard, or the existence of this specification is not evidence that a change is safe, deployed, effective, or recoverable.

This specification deliberately separates what the system must guarantee within its own boundaries from what depends on a CMS, deployment platform, search engine, human decision, or external service. No document can establish a defect-free system or guarantee search rankings. Production readiness requires the implementation to pass the specified tests, recovery exercises, security review, and release gates. A failed mandatory gate blocks the corresponding capability rather than becoming a launch-day disclaimer.

“Delivery” below means publishing or deploying SEO content and code and verifying the actual result. Paid-ad buying, ad serving, and advertising-budget execution are not silently included in this SEO product.

## Contents

- [1. Product contract and supported release boundary](#section-1)
- [2. Non-negotiable system invariants](#section-2)
- [3. Architecture decisions and self-hosting policy](#section-3)
- [4. Trust boundaries and deployment responsibilities](#section-4)
- [5. End-to-end business flows](#section-5)
- [6. State machines and sources of truth](#section-6)
- [7. Database design and management](#section-7)
- [8. Authentication, authorization, and organization lifecycle](#section-8)
- [9. Command API and application contracts](#section-9)
- [10. Connector platform and MCP gateway](#section-10)
- [11. Crawl subsystem: safe discovery to trustworthy evidence](#section-11)
- [12. Search, analytics, and website-performance ingestion](#section-12)
- [13. Evidence, memory, and knowledge provenance](#section-13)
- [14. OpenAI reasoning subsystem](#section-14)
- [15. Competitor research, strategy, and prioritization](#section-15)
- [16. Versioned SEO recipe system](#section-16)
- [17. Current-policy, legal, and ethical gate](#section-17)
- [18. Exact changes, approvals, and authorization](#section-18)
- [19. External execution protocol and concurrency](#section-19)
- [20. WordPress delivery contract](#section-20)
- [21. GitHub, sandbox builds, and deployment delivery](#section-21)
- [22. Verification, optimization, and outcome measurement](#section-22)
- [23. Undo, compensation, and recovery](#section-23)
- [24. Complete dashboard specification](#section-24)
- [25. Telegram and conversational control](#section-25)
- [26. Budgets, resource scheduling, and capacity](#section-26)
- [27. Security, data protection, and audit engineering](#section-27)
- [28. Observability, reliability targets, and support](#section-28)
- [29. Self-hosted deployment, backups, and disaster recovery](#section-29)
- [30. Test architecture and quality requirements](#section-30)
- [31. Systematic edge-case discovery](#section-31)
- [32. Release gates](#section-32)
- [33. Implementation sequence and dependency plan](#section-33)
- [34. Clean engineering and repository structure](#section-34)
- [35. Customer readiness and operating ownership](#section-35)
- [36. Review corrections incorporated into this revision](#section-36)
- [37. Final engineering position](#section-37)
- [Appendix A. Canonical logical database catalog](#appendix-a)
- [Appendix B. Critical relational, transaction, and authorization patterns](#appendix-b)
- [Appendix C. Public API, model tools, and adapter contracts](#appendix-c)
- [Appendix D. Operational runbooks](#appendix-d)
- [Appendix E. Document review and executed reference checks](#appendix-e)
- [Appendix F. Research references and verification date](#appendix-f)

---

<a id="section-1"></a>
## 1. Product contract and supported release boundary

### 1.1 What Signal owns

Signal maintains a site's SEO operating cycle: establish a trustworthy baseline, crawl safely, ingest first-party performance information, research competitors, identify opportunities, select reviewed recipes, prepare bounded changes, check current policy, obtain authority, execute, verify, measure, and report. It maintains the backlog and asks for missing business facts or approvals through the dashboard and connected chat.

Signal is persistent as a business process, not as an endless model request. A customer has a persistent employee identity, goals, memory, work history, and permissions. Individual model calls, browser sessions, and build environments are temporary.

### 1.2 First general-availability release

The first general-availability release, called **GA1**, is a complete product for a declared compatibility set, not a demonstration of universal CMS control.

| Area | GA1 commitment | Boundary |
|---|---|---|
| Identity | Working signup or invitation, login, email verification, password recovery, MFA, sessions, organizations, roles, and site access | Anonymous visitors never receive connector or write authority |
| Dashboard | All screens in Section 24, including empty, loading, stale, denied, partial, and failure states | No fabricated analytics or nonfunctional controls |
| Website scope | Public HTTPS sites with explicit verified origins | Private-network websites require a separately certified customer-side relay; disabled in GA1 |
| WordPress | Auditing, content inventory, drafts, and certified conditional writes through a narrowly scoped Signal Bridge | Only tested post types, fields, editor formats, plugin versions, and database behavior |
| GitHub | Repository analysis, sandbox patches, PR creation, checks, and deployment observation | Merge/deployment authority only for repositories with a certified Delivery Contract |
| Google | Search Console and GA4 read-only integrations, plus CrUX where data exists | No promise of complete query data, immediate indexing, or available CrUX data for every URL |
| Chat | Telegram and in-product chat with shared commands and permissions | High-risk approval may begin in chat but requires authenticated step-up |
| Recipes | Reviewed technical, metadata, internal-link, content-refresh, and performance recipes | A recipe is enabled only for adapters and site conditions that satisfy its contract |
| Recovery | Conflict-aware recovery for certified operations | No blanket undo guarantee for irreversible effects |
| Operation | Installation, upgrades, backups, incident handling, export, deletion, support, and cost controls | Public availability claims require demonstrated deployment evidence |

Webflow and other CMSs remain connector expansion targets. Their UI must say exactly which capabilities are available. Shipping a read-only adapter is acceptable when it is presented as read-only. Advertising automatic write support for an uncertified adapter is not acceptable.

A new content publication is available only when its publication, slug allocation, side effects, and recovery behavior have passed certification. Protected or regulated subject matter requires the relevant editorial or legal review. Bulk publication, hard deletion, domain transfers, arbitrary production SQL, mass redirects, unrestricted plugin installation, and automatic CI configuration changes are excluded from GA1.

### 1.3 Requirement traceability

| Requirement | Binding implementation requirement | Release evidence |
|---|---|---|
| REQ-001: Long-running employee | Durable workflows, recoverable tasks, external event handling, bounded reasoning episodes | Workflow replay, crash recovery, and delayed-approval tests |
| REQ-002: Human approvals | Exact revision, authenticated approver, expiry, current authority, and impact-specific policy | Approval race and revocation suite |
| REQ-003: MCP connectors | Private gateway, typed capabilities, certified adapters, tenant-scoped credentials | Connector contract and isolation tests |
| REQ-004: Persistent memory and state | Separate workflow state, business records, evidence, and provenance-aware memory | Rebuild, retrieval isolation, and conflict tests |
| REQ-005: SEO and performance | Deterministic audits, functional preservation, lab and field measurement separation | Golden-site and performance regression suites |
| REQ-006: Competitor research | Public or appropriately licensed evidence, freshness, original analysis, no copying pipeline | Research provenance and content-rights review |
| REQ-007: Multiple specialists | Bounded coordinator, analyst, implementer, and verifier roles | Role/tool permission tests and delegation budgets |
| REQ-008: Auditable and observable | Durable change ledger, evidence links, traces, costs, recovery history | Audit reconstruction exercise |
| REQ-009: Reliable and secure | Tenant isolation, restricted execution, idempotency, reconciliation, tested restoration | Security and failure-injection gates |
| REQ-010: Dashboard | Auth, connectors, analytics, work, approvals, policy, recovery, settings, and support | End-to-end acceptance and accessibility review |
| REQ-011: Chat control | Same command service as web, explicit context, authenticated account binding | Web/chat parity and ambiguous-command tests |
| REQ-012: Undo | Inverse patches or compensation with conflict and dependency detection | Later-human-edit recovery tests |
| REQ-013: Self-hosting | Own control plane, state, crawl, policy, memory, workers, and telemetry | Reproducible installation and restore exercise |
| REQ-014: Reviewed SEO recipes | Immutable recipe releases, tests, policy dependencies, staged promotion | Recipe release evidence |
| REQ-015: Current policy | Authoritative source monitoring, applicability, reviewed signed bundles, execution-time checks | Policy-change and stale-source tests |
| REQ-016: OpenAI brain | Responses API behind Signal's own reasoner boundary | Model capability, privacy, cost, and quality evaluations |
| REQ-017: Complete engineering | Explicit schemas, APIs, ownership, edge cases, migrations, runbooks, and CI gates | Traceability report and release manifest |
| REQ-018: User-ready delivery | Every advertised capability works under normal and failure conditions | GA1 release checklist, not a feature-count judgment |

<a id="section-2"></a>
## 2. Non-negotiable system invariants

These are acceptance conditions. They are not statements that the unbuilt implementation already satisfies them.

| ID | Invariant | Enforcement boundary |
|---|---|---|
| INV-001 | A model cannot mint, enlarge, or directly exercise production authority | Reasoner tools, authorizer, execution gateway |
| INV-002 | Every tenant-owned record and artifact is accessed through authenticated tenant context | API, database roles/RLS, storage authorization, retrieval |
| INV-003 | A same-tenant but wrong-site reference is rejected where the record is site-specific | Composite foreign keys and service validation |
| INV-004 | Approval applies to one immutable manifest revision and its exact targets | Revision hash, approval records, dispatch checks |
| INV-005 | A changed relevant policy, permission, recipe release, target, or scope invalidates dispatch eligibility | Current-epoch checks and revalidation |
| INV-006 | Every external write has a durable operation identity before transmission | Operation ledger and independent write-safety journal |
| INV-007 | An ambiguous result is not classified as an unapplied write | `OUTCOME_UNKNOWN` and reconciliation |
| INV-008 | A retry cannot silently become a different write | Same operation identity, intent hash, and certified idempotency behavior |
| INV-009 | A stale worker cannot acquire new write authority after losing its lease | Fencing and per-dispatch gateway validation |
| INV-010 | Internal locks are not represented as protection against external human edits | Certified upstream compare-and-write or no automatic publishing |
| INV-011 | A successful provider response is not equivalent to verified live delivery | Independent postconditions and deployment identity |
| INV-012 | Recovery preserves unrelated later edits or reports a conflict | Three-way/inverse-patch recovery |
| INV-013 | A site pause stops new authorization; in-flight effects are still tracked | Primary-database pause state and draining/reconciliation |
| INV-014 | Budget holds cannot disappear while potentially chargeable work is unresolved | Budget ledger and conservative reconciliation |
| INV-015 | Source content, tool output, and repository instructions never become system authority | Data/command separation and allowlisted tools |
| INV-016 | A policy approval is not a universal legal certification | Scope, jurisdiction, evidence, and review state |
| INV-017 | A dashboard assertion is traceable to committed evidence | Event projections and verification records |
| INV-018 | Missing or partial analytics are not silently converted into zero | Coverage and quality metadata |
| INV-019 | Backup restoration cannot resume writes before external-state reconciliation | Recovery epoch, frozen egress, and operator release gate |
| INV-020 | No application tenant can modify platform policy, model releases, or connector trust | Separate control schema and release identities |
| INV-021 | A deleted or disconnected identity cannot keep acting through an old chat binding | Action-time membership and integration checks |
| INV-022 | A safe-looking batch cannot exceed aggregate impact limits by splitting into smaller jobs | Site/tenant rolling scope accounting |
| INV-023 | Immutable records are corrected by supersession, not hidden replacement | Append-only revisions, receipts, decisions, and audit events |
| INV-024 | A failed mandatory check is visible and blocks the relevant transition | State machine and release gates |

The unavoidable boundary matters: a remote request accepted before a pause or credential revocation may still complete afterward. Signal must report that operation as in flight, verify it, and offer safe recovery. It cannot truthfully promise to recall a network request that another system already accepted.

<a id="section-3"></a>
## 3. Architecture decisions and self-hosting policy

### 3.1 Selected stack

| Component | Decision | Reason and operational obligation |
|---|---|---|
| Web | Next.js, TypeScript, server-side session/BFF pattern | Complete authenticated interface; do not expose provider tokens to browser JavaScript |
| Backend | FastAPI with strict typed request/domain models | Modular application with explicit contracts and transaction boundaries |
| Workflows | Self-hosted Temporal | Durable timers, activities, retries, and recovery; own upgrades and persistence |
| Business database | PostgreSQL | Relational integrity, explicit transactions, tenant boundaries, reporting |
| Retrieval | PostgreSQL full-text search plus pgvector | Avoid a second stateful search system until measurements justify it |
| Policy | Open Policy Agent with signed, versioned bundles | Deterministic authorization inputs and independently released rules |
| Identity | Keycloak | Established OIDC and account-security flows rather than custom authentication |
| Secrets | OpenBao | Controlled credential access, rotation, auditable administration, tested key recovery |
| Reasoning | OpenAI Responses API | User-selected reasoning provider; Signal retains orchestration and memory |
| Crawl/render | Own HTTP crawler plus isolated Playwright workers | Direct control over scope, evidence, rate, and cost |
| Performance | Pinned Lighthouse runner, CrUX ingestion | Reproducible lab checks plus available field measurements |
| Artifacts | Private Artifact Service over encrypted immutable objects/files | A single abstraction for snapshots, patches, receipts, and reports |
| Telemetry | OpenTelemetry collector, Prometheus, Grafana, selected log/trace backends | Separate observability from durable audit history |
| Packaging | OCI images pinned by digest, Compose, declarative host provisioning | Reproducible deployment without mandatory Kubernetes |
| Sandboxes | Dedicated execution hosts with hardened isolation | Untrusted browsing and builds cannot share the trusted control-plane boundary |

Temporal documents production self-hosting; PostgreSQL and pgvector provide the core persistence and retrieval capabilities. These components still require operational ownership, not merely a Docker file. [S09] [S18]

Keycloak requires production hostname/TLS/proxy configuration. OpenBao integrated storage needs appropriate quorum and recovery planning. Follow tested deployment manifests rather than copying development-mode startup commands. [S20] [S21]

### 3.2 External-service exceptions

The OpenAI API is an explicit required exception. The customer's CMS, GitHub, Google data sources, and Telegram are external systems that Signal operates or reads, not services we can recreate by self-hosting Signal.

An encrypted off-site object store and a transactional email relay can be justified exceptions. Running a mail server is not inherently cheaper once deliverability, abuse handling, and recovery are included. A paid research-data provider is optional and requires terms, rights, privacy, and spend review.

Each exception has an architecture decision record containing the purpose, exchanged data, contract, region, current price schedule, hard application budget, outage behavior, replacement path, and review date. Do not make a free tier a hidden reliability dependency.

R2 currently has a free monthly Standard allowance and no internet-egress charge, but storage beyond the allowance and request classes are billable. Use a procurement-time snapshot rather than permanently embedding its price into business logic. [S54]

### 3.3 Deliberate non-decisions

No Kubernetes requirement for GA1. No Kafka requirement. No Redis as authoritative state. No autonomous multi-agent chat swarm. No separate vector database without a measured bottleneck. No local or Gemini fallback brain: the prior model-hosting recommendation is superseded by the OpenAI requirement.

Do not build authentication, cryptography, a database, an object-storage cluster, or a search-engine dataset from scratch. Custom engineering belongs in Signal's domain contracts, controlled execution, connector behavior, evidence processing, and recovery.

### 3.4 Model release selection

The official model catalog checked for this revision lists `gpt-6-astra`, `gpt-5.6-terra`, and `gpt-5.6-luna`. Treat these as candidates, not automatically trusted production configurations. [S01]

Recommended evaluation order: Astra for coordination and difficult implementation; Terra for measured cost/quality comparison; Luna only for bounded tasks whose held-out evaluations demonstrate sufficient quality. Every enabled role references an approved `model_release_id`, not an unreviewed string supplied by a user or model.

Prefer an explicit provider snapshot when available. When only an alias is available, document that it is not immutable, monitor provider changes, retain resolved response metadata, run drift evaluations, and suspend affected autonomous capabilities when behavior changes. Do not claim an alias is pinned merely because its text is stored in a configuration file.

<a id="section-4"></a>
## 4. Trust boundaries and deployment responsibilities

```text
User browser                    Telegram / provider webhook
     |                                      |
Edge TLS / request limits / verified webhook ingress
     |
Web BFF and authenticated command API
     |
Domain services + primary PostgreSQL
     |                         |
Transactional outbox           Identity / membership / policy context
     |
Temporal workflow workers
     |
Bounded OpenAI reasoning activities
     |                         |
Evidence/retrieval API          OpenAI API, minimized task context
     |
Prepared immutable change revision
     |
Policy evaluator + authorizer + budget reservation
     |
Write-safety journal + controlled execution gateway
     |
Tenant-scoped MCP adapter
     |
CMS / GitHub / certified delivery integration
     |
Provider receipt -> independent verifier -> live evidence -> measurement
```

The untrusted execution network contains browser sessions and repository builds. It cannot connect to PostgreSQL, Temporal administration, OpenBao, internal metadata endpoints, control-plane localhost addresses, or other tenants' sandboxes. It receives task-scoped input artifacts and can return only bounded output artifacts through an authenticated upload interface.

A separate trusted connector runtime holds narrow provider credentials. It never executes arbitrary repository code. The model sees tool descriptions and redacted results, not the credential that performs the operation.

### 4.1 Component ownership

| Domain | Owns | Must not own |
|---|---|---|
| Identity | Authentication, memberships, role/site scopes, security events | SEO execution state |
| Commands | Authenticated intent, idempotency, command lifecycle | Direct external mutation |
| Workflows | Ordering, durable waiting, retries, compensation scheduling | Independent permission truth |
| Crawl | Safe discovery and observations | Publication authority |
| Evidence | Immutable observations, provenance, source rights | Invented or unversioned facts |
| Planning | Goals, opportunities, recipe selection, drafts | Policy promotion |
| Policy | Applicable rule releases and deterministic decisions | Model-generated self-approval |
| Execution | Exact-operation dispatch, credential mediation, receipts | Unrestricted shell access |
| Verification | Independent postconditions and delivery evidence | Fabricating success from intention |
| Measurement | Data quality, comparison windows, outcome estimates | Automatic causal claims |
| Product UI | Accurate projections and user controls | Authorization implemented only in buttons |

<a id="section-5"></a>
## 5. End-to-end business flows

### 5.1 New customer to first verified change

1. Create or accept access to an organization, verify identity, enroll required MFA, and establish an owner.
2. Add a site and prove authority over each intended origin or upstream resource. Record the chosen markets, languages, site timezone, business goals, protected paths, and content restrictions.
3. Connect data sources and publication systems separately. Selecting a Search Console property does not grant CMS write permission.
4. Run capability discovery and connector certification checks for the exact installed stack. Display what Signal can read, draft, publish, verify, and recover.
5. Set crawl budgets, AI budgets, maximum change impact, publication windows, and approval rules. No inferred unlimited authority.
6. Crawl a bounded initial sample, validate source freshness, and establish a coverage-aware baseline.
7. Produce an evidence-backed prioritized backlog. Ask for missing business facts instead of inventing them.
8. Prepare one narrow recipe revision, its exact patch, before-state artifacts, test evidence, recovery plan, and expected cost.
9. Evaluate applicable policy. Request an exact approval through the web or Telegram flow.
10. Revalidate authority and target versions, journal intent, execute one certified operation, and independently verify the actual result.
11. Report exactly what changed and what remains uncertain. Schedule delayed measurement separately.
12. Exercise recovery on the customer's test/staging content before increasing autonomy.

### 5.2 Proactive recurring work

Schedules create bounded audit/research/measurement workflows. Incremental observations update findings; deduplication prevents repeated identical work. Each site has a prioritization policy, a cost ceiling, and an active-change exclusion map. The planner cannot repeatedly rewrite the same page because a daily metric fluctuated.

New evidence can supersede a plan without rewriting its history. Work awaiting approval can become stale. Work already sent to a provider must be reconciled before a replacement plan is allowed to touch the same target.

### 5.3 Steering through chat

A user instruction becomes a proposed typed command. Read-only questions can execute under current read permissions. Changes to goals, drafts, or settings have explicit scope and version checks. Publication and permission expansion use the same approval and step-up requirements as the dashboard.

“Stop everything” pauses new writes for the resolved scope immediately in the domain database, then signals workflows and shows in-flight operations. “Undo that” identifies one exact change revision or asks the user to choose among visible candidates. It never guesses a destructive target.

<a id="section-6"></a>
## 6. State machines and sources of truth

### 6.1 Do not collapse five different lifecycles into one status

| Entity | Source of truth | Key states |
|---|---|---|
| Command | PostgreSQL command record | `ACCEPTED`, `DISPATCHED`, `PROCESSING`, `SUCCEEDED`, `REJECTED`, `FAILED`, `CANCELLED` |
| Workflow | Temporal history | Bounded business stages, timers, waiting, retries, completion |
| Change revision | Immutable PostgreSQL record plus lifecycle projection | `PREPARING`, `READY_FOR_REVIEW`, `WAITING_APPROVAL`, `ELIGIBLE`, `EXECUTING`, `VERIFYING`, `VERIFIED`, `BLOCKED`, `SUPERSEDED`, `CANCELLED` |
| External operation | PostgreSQL operation ledger | `PLANNED`, `AUTHORIZED`, `DISPATCHING`, `ACKNOWLEDGED`, `OUTCOME_UNKNOWN`, `APPLIED`, `NOT_APPLIED`, `CONFLICT`, `FAILED_FINAL` |
| Measurement | PostgreSQL experiment/measurement records | `BASELINING`, `OBSERVING`, `INSUFFICIENT_DATA`, `ANALYZED`, `INCONCLUSIVE`, `STOPPED` |

A change may be technically verified while its business impact remains inconclusive. A command to approve can succeed even though the associated publication later becomes blocked. The UI must preserve these distinctions.

`ELIGIBLE` means a prior review made execution possible. It is not a durable permission to publish. The dispatch gateway must re-evaluate current conditions for each externally visible write.

### 6.2 Workflow families

| Workflow | Durable input | Completion evidence |
|---|---|---|
| OnboardSite | Site ID and onboarding revision | Verified bindings, compatibility results, accepted scope |
| CrawlSite | Scope and crawl-policy versions | Complete/partial manifest with frontier terminal classifications |
| ImportAnalytics | Provider/property/scope/date window | Atomic import generation and coverage metadata |
| ResearchOpportunity | Evidence references and research budget | Provenance-backed report and hypotheses |
| PrepareChange | Opportunity, recipe release, base versions | Immutable manifest, tests, before-state, recovery plan |
| ExecuteChange | Revision ID and authorization references | Operation receipts and independent verification |
| RecoverChange | Original revision and recovery request | Conflict result or verified compensation |
| MeasureChange | Verified deployment and experiment contract | Dataset versions and qualified interpretation |
| RefreshPolicy | Registered source and prior snapshot | No-change result or reviewed-update work item |
| ReconcileOperations | Site/integration and unresolved operations | Classified outcomes and blocked-risk list |
| ExportTenant / DeleteTenant | Authorized request and retention policy | Signed export manifest or deletion receipt |

Activities perform I/O. Workflow code makes deterministic decisions from recorded inputs and results. Temporal retries can re-run an activity, and cancellation depends on activity cooperation; external effects need Signal's separate operation protocol. [S10] [S12]

### 6.3 Commands, outbox, and message delivery

An authenticated command transaction inserts the command, audit event, and outbox entry together. The outbox dispatcher starts or signals a deterministic workflow identity. The receiver deduplicates by command/event ID. A duplicate delivery must not create a second independent logical workflow.

Durable business-command deduplication remains in PostgreSQL for the relevant business lifetime. Do not rely exclusively on Temporal's finite history retention. Sending a signal is not proof it was processed; a committed command receipt or workflow event establishes that. Temporal provides external message mechanisms, but Signal defines the business idempotency contract. [S11]

### 6.4 Timeouts, cancellation, and upgrades

Every activity declares a start-to-close timeout, total retry window, backoff, retryable-error set, and cleanup behavior. Long browser/build activities heartbeat. Approval waits use workflow timers and event messages, not a model or an activity sleeping for days.

Model, browser, and provider timeouts are different budgets. A provider write timeout enters reconciliation; it does not trigger an ordinary blind retry. Approval expiry uses database UTC time and an explicit manifest expiry, not the user's browser clock.

Pin worker build/deployment versions and replay representative histories before upgrade. Continue-As-New only at a documented safe boundary, carrying business identifiers rather than large payloads. Drain old workers or preserve compatible handlers until outstanding workflows finish. Rolling deployment must not strand a pending approval created by the previous release.

### 6.5 Projection recovery

A projection contains `source_event_id`, aggregate version, and last projection time. Consumers enforce monotonic versions and deduplicate events. Gaps trigger a replay or authoritative refresh, not an invented intermediate state. The UI shows stale state when freshness exceeds the configured limit.

Rebuilding projections cannot recreate approvals or provider receipts that do not exist. Those are authoritative domain records, not disposable dashboard caches.


<a id="section-7"></a>
## 7. Database design and management

### 7.1 Storage ownership

Use separate PostgreSQL databases and login roles for Signal business data, Temporal persistence/visibility, and Keycloak. They may share a tested physical cluster in the smaller deployment profile, but do not share migration ownership or runtime credentials. OpenBao retains its own supported storage configuration.

Signal's business database has `control` and `app` schemas. `control` contains operator-managed releases, connector definitions, policy sources, and platform configuration. `app` contains tenant-owned data. Public application roles cannot write `control`. Narrow identity, bootstrap, release, and admission services receive only their explicitly owned control-table privileges. A minimal tenant directory lets scheduled dispatchers enumerate authorized work scopes; each tenant's outbox is then accessed in its own RLS-scoped transaction, not through a hidden `BYPASSRLS` worker role.

The complete table catalog is in Appendix A. It is the canonical logical schema contract. Appendix B defines the most safety-sensitive relational and row-security patterns. Migration implementation and real-PostgreSQL verification are mandatory release work, not assumed to have happened because SQL appears in this document.

### 7.2 Data-model conventions

| Concern | Binding rule |
|---|---|
| IDs | Application-generated UUIDs; never use an email, URL, Telegram username, or mutable provider name as the identity |
| Tenant keys | Every tenant-owned row has non-null `tenant_id`; no security-relevant table uses a nullable tenant meaning “global” |
| Site keys | Every site-owned row has non-null `site_id`; optional organization-wide scope is explicitly typed and separately validated |
| References | Tenant parents use `(tenant_id, id)`; site parents expose `(tenant_id, site_id, id)` for site-scoped child references |
| External IDs | Store provider IDs as text unless the contract explicitly requires another type; preserve leading zeros and large integer values |
| Time | `timestamptz` for instants; `date` plus explicit provider/site timezone for reporting buckets |
| Money | Integer micro-units of a named currency; never floating-point dollar balances |
| Counts | Nonnegative `bigint`; ratios are computed from numerator and denominator |
| Hashes | SHA-256 represented as 32 bytes internally or validated 64-character lowercase hex at interfaces |
| JSON | Only for versioned structured payloads with runtime schemas; not a substitute for foreign keys, authority records, or queryable domain fields |
| Mutability | Separate mutable drafts/projections from sealed immutable revisions and append-only events |
| Deletion | Explicit lifecycle and deletion jobs; no cascade that silently destroys change evidence while active references remain |
| Defaults | Fail closed; missing scope, authority, policy, or cost limit never means unrestricted |
| Version checks | Mutable command targets use a monotonically increasing `row_version` and conditional updates |

A canonical URL is an observation about search preference, not a universal primary key. A CMS resource, a locale, its draft, its live version, and several public URLs can differ. Model them through explicit resource/URL bindings with validity intervals.

### 7.3 Tenant and site isolation

The browser never connects directly to PostgreSQL. The trusted backend derives identity from a verified session and loads current membership and site grants. It sets transaction-local tenant context only after this authorization step.

All tenant tables have row-level security enabled and forced. Runtime roles must not own those tables and must not have `BYPASSRLS`, superuser, schema-creation, replication, or migration privileges. PostgreSQL owners and privileged roles can bypass normal row-security behavior, so testing with only a superuser proves nothing about isolation. [S13]

Use `SET LOCAL` or transaction-local `set_config`, parameterized values, and a transaction for every scoped operation. Pooled connections must never retain a previous tenant context. Tenant-context functions must not be marked `IMMUTABLE`, because the context changes between requests.

Site-scoped request repositories include site filters in addition to tenant RLS. High-risk tables receive a site-scoped RLS policy using a transaction-local site context. Cross-site dashboards use explicitly authorized aggregate queries rather than turning off row security. Same-tenant/wrong-site foreign-key tests remain required.

RLS is defense in depth, not a defense against arbitrary privileged code execution in the trusted application. A service credential able to set arbitrary session context is itself a trust boundary. Do not expose arbitrary SQL tools to Signal or assume RLS repairs a compromised control plane.

### 7.4 Role and privilege matrix

| Database principal | Allowed work | Prohibited work |
|---|---|---|
| `signal_identity` | Verified global identity, pre-tenant sessions, scoped sessions, and membership lookup | Production changes, policy release, arbitrary tenant content |
| `signal_bootstrap` | Audited tenant/site ownership bootstrap and matching directory records | Routine agent execution or unrestricted ongoing tenant administration |
| `signal_scheduler` | Minimal tenant directory plus per-tenant outbox/schedule dispatch | Bypassing RLS or reading arbitrary private evidence |
| `signal_crawl_admission` | Owned origin buckets and bounded fetch admission leases | Connector credentials, approvals, production mutation |
| `signal_release` | Reviewed signed release records and lifecycle events under operator authentication | Routine customer execution or self-approval by a model |
| `signal_migrator` | Reviewed schema migrations in a deployment job | Routine API or agent use |
| `signal_api` | Scoped reads, drafts, commands, invitation/settings requests | Direct operation dispatch, policy release, secret reads |
| `signal_authorizer` | Validated approvals, policy decisions, execution permits | Running repository code or editing content |
| `signal_executor` | Operation/receipt lifecycle through reviewed methods | Creating approvals or changing recipe/policy releases |
| `signal_ingest` | Scoped observations and analytics import generations | Production writes and permission changes |
| `signal_projector` | Read domain events and update projection tables | Authoritative approval or operation mutation |
| `signal_retention` | Reviewed deletion procedures with legal-hold checks | Online user impersonation |
| `signal_report_reader` | Authorized views/read replicas for reports | Any authoritative mutation |
| Human break-glass role | Time-limited emergency work with recorded reason | Persistent routine access or hidden tenant impersonation |

Avoid generic `GRANT ALL ON ALL TABLES` provisioning. Each migration updates a reviewed privilege manifest. Build a test that compares effective grants with that manifest.

### 7.5 Integrity and transaction rules

Foreign keys validate tenant and, where applicable, site identity. A child cannot refer to another site's integration merely because the integration belongs to the same tenant. Every frequently joined foreign key has an appropriate supporting index unless a measured reason is documented.

Use short transactions. Never keep row locks open across a model call, browser run, or provider network call. Record intent, commit, perform external work, then record outcome in a new transaction.

Use row locks or serializable transactions for contested domain decisions such as budget reservation, last-owner removal, scope leases, and approval transitions. Retry serialization failures by repeating the entire database transaction from fresh inputs. PostgreSQL's isolation behavior does not automatically extend to an external provider. [S14]

Acquire multiple locks in a deterministic order: tenant, site, resource key, operation, budget account. Batch membership changes and parallel jobs must use the same ordering. Set bounded lock and statement timeouts; an unresolved lock is not a reason to bypass the lock.

### 7.6 Immutability and append-only records

Before sealing, content lives in mutable draft tables. Sealing creates immutable `change_revisions` and `change_items`; subsequent lifecycle is in `change_progress` and events. A revision is never edited after its hash has been presented for approval.

Approval grants and revocations are separate records. A revocation does not erase that a person previously approved something. Provider receipts, policy decisions, artifact manifests, and audit events are append-only. Corrections link to the superseded record.

Use restricted database privileges and immutability triggers for defense in depth. Retention uses a separate reviewed procedure and role. An append-only application table alone does not prevent a database administrator from rewriting history; off-host signed checkpoints provide tamper evidence, not magical tamper prevention.

### 7.7 Query and index strategy

Primary query shapes determine indexes: tenant/site/status/next-attempt for queues; tenant/site/time for observations; tenant/site/resource/version for change history; tenant/user/state for memberships; tenant/site/date/series for metrics; integration/event-id for webhook deduplication.

Use keyset pagination for large histories and inventories. Never use an unbounded `OFFSET` scan as the normal million-row navigation path. Cap list sizes, export asynchronously, and attach query timeouts. Prevent arbitrary user-selected JSON paths from becoming unindexed public queries.

Use full-text filtering and tenant/site authorization before vector retrieval. HNSW/approximate filtering can affect recall, so measure recall on filtered datasets and retain exact-search fallback for small result sets. An embedding migration requires a new model/dimension cohort and dual-read evaluation, not mixing vectors from different spaces. [S18]

Start with normal tables where scale permits. Partition high-volume captures, metric points, and event archives only through tested migrations. PostgreSQL unique constraints on partitioned tables must include the partitioning columns; do not preserve an impossible global-uniqueness claim when introducing monthly partitions. Keep small authoritative operation/approval tables unpartitioned initially. [S15]

### 7.8 Analytics import consistency

An import has a provider, property, date interval, metric/dimension specification, filters, source timezone, cursor, source version, coverage flags, and a generation ID. Write into a new generation, validate counts and totals, then atomically switch the active generation for that exact query scope.

Never combine half of yesterday's import with half of today's rerun. Never deduplicate different metric grains because they share a date and URL. A total-property report and a page/query report are different series, not interchangeable rows.

Retain source numerators and denominators. Recalculate CTR as total clicks divided by total impressions, not the average of row CTRs. Preserve the source's aggregation semantics for average position; do not average arbitrary averaged positions across incompatible groups.

### 7.9 Schema migration and database operations

Every migration has an owner, forward plan, lock-risk analysis, backfill strategy, compatibility window, verification queries, and recovery approach. Use expand/contract: add nullable/new fields, deploy compatible writers, backfill in bounded batches, validate constraints, switch reads, and remove old structures only after old workers and restore windows are accounted for.

Create large indexes without holding unnecessary long write-blocking locks; operations that cannot run inside a transaction must be declared as such. Test migration on production-shaped data, not an empty database. Never automatically run destructive migrations on application startup.

Monitor connection exhaustion, long/idle transactions, deadlocks, replication lag, disk usage, WAL growth, index bloat, query latency, and transaction-ID age. Configure autovacuum for actual update patterns and inspect it continuously; PostgreSQL documents vacuuming as an essential maintenance activity. [S16]

Read replicas serve stale-tolerant reports only. Permissions, pauses, budget authorization, approvals, and dispatch checks read the current primary. When replica lag is too large, show stale status or route permitted reads to primary; never silently authorize from stale data.

### 7.10 Retention and deletion

Proposed defaults are operating choices, subject to customer contract and applicable requirements: raw crawl bodies 30 days; derived observations 180 days; analytics daily aggregates 16 months where permitted; redacted debug logs 14 days; traces 7 days; durable change/approval/audit records 12 months; recovery artifacts at least the promised undo window and longer while an operation or hold is unresolved.

The retention engine uses references and legal holds. It cannot delete an artifact needed by an active approval, an unresolved operation, or an advertised recovery. The UI displays the exact recovery expiry. After expiry, it changes the recovery affordance rather than leaving a misleading active Undo button.

Tenant deletion pauses work, revokes authority, resolves or quarantines in-flight effects, exports when requested, deletes tenant data according to policy, and records a minimal deletion receipt. It does not delete the customer's website content. Restored backups must replay deletion tombstones before becoming available to users.

<a id="section-8"></a>
## 8. Authentication, authorization, and organization lifecycle

### 8.1 Authentication flow

Use Keycloak OIDC authorization-code flow with PKCE, verified issuer/audience, state and nonce binding, exact redirect URIs, and a maintained client library. OAuth security guidance requires careful redirect, token, and replay handling; do not implement a custom password-to-token shortcut. [S19]

Store the application session server-side. The browser gets only an opaque, secure, HttpOnly, host-only session cookie. Do not put provider refresh tokens, OpenAI keys, or long-lived access tokens in local storage. Apply CSRF protection to state-changing cookie-authenticated requests and validate Origin where applicable.

A user identity is `(issuer, subject)`. Matching email strings must not automatically link identities from different issuers. Account linking requires authenticated control of the existing identity and the new identity. Handle email changes, unverified claims, invitations to already-registered users, and disabled accounts explicitly.

A newly authenticated user may not yet have an organization. Use a short-lived pre-tenant identity session for account setup and listing only that user's memberships. It has no site or connector authority. Tenant selection or bootstrap creates a separate tenant-scoped session linked to the verified identity session. Global logout/account disable revokes both levels, and tenant switches clear all site-scoped client state.

### 8.2 Required account flows

Implement registration policy, email verification, login, logout, logout-all-sessions, password recovery, MFA enrollment, MFA recovery, password change, email change, session review, account lock/rate limiting, and owner recovery. Configure transactional email and test actual delivery before enabling these paths.

A session has created time, last seen, absolute expiry, idle expiry, authentication strength, revocation state, and a session version. Use short-lived backend tokens and current membership checks. Removing a member invalidates their organization access without waiting for their browser token to expire.

### 8.3 Roles and permissions

| Role | Read/report | Prepare drafts | Approve low-risk | Approve high-risk | Manage connections/settings | Manage members/ownership |
|---|---:|---:|---:|---:|---:|---:|
| Viewer | Yes, within site scope | No | No | No | No | No |
| Analyst | Yes | Yes | No | No | No | No |
| Editor | Yes | Yes | By explicit grant | No by default | Limited content settings | No |
| Approver | Yes | Yes | Yes | Only with high-risk grant and step-up | No implicit connector authority | No |
| Admin | Yes | Yes | Yes | By policy and step-up | Yes | Member administration, not ownership transfer |
| Owner | Yes | Yes | Yes | By policy and step-up | Yes | Yes, with safeguards |
| Signal service | Task-scoped reads/drafts | Yes within recipe | Never | Never | No permission expansion | No |

Store roles as permission bundles and site grants, not scattered `if is_admin` branches. Platform operators are not customer owners by default. Break-glass support requires a reason, limited duration, scope, and a visible audit record.

High-risk two-person review is available for organizations with multiple eligible approvers. A single-owner organization cannot satisfy two-person control by clicking twice. Such actions remain blocked or are deliberately excluded from that organization's enabled capabilities.

### 8.4 Organization edge cases

Prevent removal or demotion of the final active owner in one locked transaction. Ownership transfer requires acceptance, current MFA, and a visible audit event. An invitation is single-use, expires, is bound to the invited identity condition and allowed site scopes, and cannot create a higher role than the inviter can grant.

A user in multiple organizations must have a visible active organization/site context. Do not carry a selected site across organization switches. The server derives the active scope and rejects mismatches even when the UI accidentally sends an old resource ID.

### 8.5 Site verification and control claims

Prove control using an expiring DNS/HTTP challenge or a verified upstream administrative binding appropriate to the operation. Verification of `www.example.com` does not automatically authorize `admin.example.com`, another port, or a redirected host.

Record proof method, proof time, resource identity, permitted origins, recheck time, and revocation conditions. Recheck when the site changes owner, origin, CMS identity, or connector account. A global protected control-claim registry prevents two Signal tenants from independently holding write authority over the same external resource without an explicit transfer procedure. Do not expose another tenant's identity in the conflict message.

<a id="section-9"></a>
## 9. Command API and application contracts

### 9.1 HTTP conventions

All state-changing commands require authenticated scope, CSRF protection where relevant, a bounded body, and an idempotency key. The idempotency fingerprint includes tenant, actor, route/action, target, and canonical request body. Reusing a key with different content returns `409 IDEMPOTENCY_CONFLICT`.

Use `202 Accepted` for durable work acceptance and return `command_id`, `status_url`, current state, and a correlation ID. Do not return “published” from the command-acceptance endpoint. Synchronous settings edits use version/ETag preconditions and return a new version.

Cursor pagination is opaque and authenticated to the filter/sort context. Dates, timezones, units, data freshness, and coverage are explicit in responses. Error responses have stable machine codes, a safe human message, retry guidance, and a trace/correlation ID without secrets or raw stack traces.

### 9.2 API surface

| Group | Required operations |
|---|---|
| Session/account | Read current identity, session list/revoke, account-security links, organization switch |
| Organizations | Create/read/update, invitations, memberships, roles, site grants, ownership transfer |
| Sites | Create, verify origins, configure profile/goals, pause/resume, archive, retention/export |
| Connectors | Catalog, start authorization, callback, resource selection, capability probe, health, reconnect, disconnect, scope change |
| Crawl | Preview scope/cost, start, progress, frontier classifications, URL evidence, stop |
| Analytics | Overview, metric series, dimensions, quality/freshness, import history, comparison windows |
| Strategy | Findings, opportunities, priority explanation, accept/defer/reject, plan versions |
| Work | Task list/details, dependencies, blockers, cancel/pause, evidence and model-run summaries |
| Changes | Draft, revision list, exact diff, policy/test results, approve/reject, revoke, dispatch status |
| Recovery | Preview, conflict details, authorization, execute, verification, expiry |
| Recipes/policy | Enabled versions, capability compatibility, source freshness, review requests, change impact |
| Conversations | Messages, threads, typed command proposals, citations, attachments, stream/resume |
| Budgets/usage | Limits, holds, settled usage, forecasts, exhaustion state, admin changes |
| Notifications | Preferences, unread feed, delivery status, escalation contacts |
| Support/data | Export, deletion request, system status, connector diagnostics, support bundle |

No production provider credentials are submitted through normal chat. Connector authorization has dedicated forms or OAuth flows whose content is excluded from conversational context and telemetry.

### 9.3 Streaming and events

Use server-sent events for progress where sufficient. Events contain tenant/site, aggregate ID, aggregate version, event ID, type, timestamp, and safe payload. The server authorizes the subscription and every resumed scope. `Last-Event-ID` reconnects do not replay another tenant's events.

A disconnected browser does not cancel durable work. A cancelled user command does not imply already-applied external effects were reversed. Stream token output only as a draft; factual operational status comes from committed domain events.

<a id="section-10"></a>
## 10. Connector platform and MCP gateway

### 10.1 Connection lifecycle

`DISCONNECTED -> AUTHORIZING -> SELECTING_RESOURCES -> PROBING -> READY`

Alternative states: `INSUFFICIENT_SCOPE`, `READ_ONLY`, `DEGRADED`, `RATE_LIMITED`, `REAUTH_REQUIRED`, `REVOKED`, `INCOMPATIBLE`, `DISCONNECTING`.

Every connection shows its provider account, selected resources, granted scopes, effective capabilities, last successful check, credential health, and why a capability is unavailable. “Connected” must not conceal that publication or recovery is impossible.

### 10.2 OAuth and secret lifecycle

Persist OAuth state as a one-time hashed nonce bound to the user, tenant, intended provider, redirect, requested scopes, and expiry. Use provider-supported PKCE and exact redirect matching. Do not trust arbitrary discovery endpoints supplied by a model.

Serialize refresh for a credential generation. If the provider rotates refresh tokens, persist the new secret version before discarding the old usable state. A failed refresh or `invalid_grant` cannot cause endless retries. Transition to reauthorization, stop affected writes, and alert the customer.

Google integrations require enabled APIs, production consent configuration, correct scopes, refresh-token handling, and any required application verification. A connection working for a developer test account is not evidence that customer onboarding is ready. [S47] [S48]

Disconnect first denies new authority and revokes pending permits, then attempts upstream revocation, removes active secret access, reconciles known in-flight operations, and records the result. Reconnection does not resurrect approvals made under an old resource binding without review.

A global write claim uses the adapter's certified conflict scope, not merely an OAuth installation ID or a convenient URL string. Shared templates and repository branches may require a broader claim than one page or file. Disjoint paths are not assumed independent. Overlapping scope is read-only or blocked until a certified ownership/serialization contract exists.

### 10.3 Capability contract

Every certified capability states the provider/version range, resource type, readable/writable fields, locale behavior, draft/live semantics, authentication scope, atomic preconditions, idempotency mechanism, scope of publication, eventual-consistency window, rate limits, side effects, verification method, and recovery support.

Classify write safety as `NATIVE_CAS`, `CERTIFIED_BRIDGE_CAS`, `DRAFT_ONLY`, or `MANUAL_APPLY`. Do not use a vague `supports_updates=true` field. A read-check-write sequence without upstream atomicity is not CAS.

Capabilities are signed release artifacts. Runtime discovery can disable an unavailable capability but cannot promote a more powerful one. Any upstream schema/plugin change outside the certified range disables affected writes and starts recertification.

### 10.4 MCP mediation

Signal's own tool dispatcher is the MCP client. OpenAI receives narrow custom function definitions that call this dispatcher. Do not expose production MCP servers directly through a hosted model tool path that bypasses the same authorizer, budget checks, and receipt protocol.

Pin the negotiated MCP protocol version and certified server release; the documented 2025-11-25 authorization contract is a baseline, not a claim it is the newest version. Use protocol negotiation tests before upgrades. MCP authorization must remain distinct from the customer's upstream provider authorization. [S22]

Allowed tools are semantic, such as `read_resource`, `get_evidence`, `prepare_patch`, `inspect_operation`, and `request_review`. Production execution is initiated by the workflow and authorizer, not a free-form model `publish` call. No generic production SQL, unrestricted HTTP, arbitrary filesystem, or shell tool is available to the reasoning role.

Treat tool schemas, descriptions, outputs, pagination links, and resource URLs as untrusted until checked against the registered connector. Reject tool-name collisions, schema substitution, redirected credential destinations, oversized outputs, and unknown fields that affect execution.

### 10.5 Webhook ingestion

Validate provider authentication over the required raw request bytes before parsing or accepting business effects. GitHub supports signature verification and delivery identities; Telegram supplies a webhook secret-token mechanism. Use constant-time comparisons and provider-specific validation. [S44] [S46]

Write a bounded inbox record and acknowledge promptly. Deduplicate by integration and provider event ID where available. An unsigned or unauthenticated event cannot start expensive crawling or model work. Out-of-order events trigger authoritative provider reads; do not assume arrival order is resource version order.

<a id="section-11"></a>
## 11. Crawl subsystem: safe discovery to trustworthy evidence

### 11.1 Crawl contract

A crawl has immutable scope/policy versions, allowed origins, an explicit purpose, a user agent identifying Signal, request/render/byte/time ceilings, robots handling, authorized content classes, and a manifest describing coverage. Connected-site auditing and competitor research have different budgets and access rights.

The crawler performs GET/HEAD inspection only. It does not submit forms, add items to carts, authenticate into arbitrary admin pages, click destructive actions, solve CAPTCHAs, bypass paywalls, or evade access blocks.

### 11.2 URL identity and normalization

Preserve the originally observed URL in encrypted evidence when necessary. Produce a separate safe display URL, fetch URL, and deduplication key. Normalize scheme/host casing and default ports. Remove fragments from HTTP fetch identity, while separately recording meaningful client-side route fragments where the site adapter requires them.

Do not lowercase paths, discard trailing slashes, reorder repeated query parameters, remove arbitrary parameters, decode reserved separators, or merge HTTP/HTTPS/www/non-www solely for convenience. Query order and encoding can change resource identity. Tracking-parameter removal requires an explicit tested rule and never changes the public website automatically.

Reject userinfo, unsupported schemes, invalid hostnames, excessive URL length, hidden control characters, malformed escapes, and disallowed ports. Handle IDNA display safely. Do not let percent encoding or Unicode normalization escape protected path rules. Retain the distinction between a discovered link, a fetchable URL, a CMS resource, and a canonical preference.

### 11.3 SSRF and network restrictions

Validate every resolved address and every redirect target. Block loopback, private, link-local, multicast, reserved, metadata, and prohibited IPv4/IPv6 ranges, including mapped/encoded forms. Validate actual connection destinations, not only the input hostname. Recheck DNS changes and prevent rebinding between validation and connection.

Use a controlled egress proxy/resolver with authenticated task identity. Preserve correct TLS hostname verification and SNI when connecting to an approved resolved address. Never forward cookies or Authorization headers across origins. Limit redirects and detect loops.

Browser subresources, frames, WebSockets, downloads, service workers, and navigation require the same network boundary. Blocking only the initial URL is insufficient. Restrict or disable nonessential network types and local/file access. SSRF prevention requires network controls as well as input validation. [S23]

### 11.4 Robots handling

Implement and test the Robots Exclusion Protocol rather than a collection of substring checks. The protocol defines matching, retrieval, error behavior, and caching; `crawl-delay` is not a standard directive in RFC 9309. Signal may voluntarily honor a recognized delay as an additional politeness constraint. [S26]

Signal's conservative policy: a robots 404 may allow the otherwise authorized public crawl; 401/403 deny; 429 backs off; persistent 5xx/network failures suspend new origin requests rather than assuming access is allowed. Handle redirects under the same SSRF/scope rules. If a robots redirect cannot be safely followed, stop rather than treating the file as absent.

Cache with retrieval time and expiry, typically no longer than 24 hours unless a documented failure policy applies. Newly observed restrictions apply to unsent frontier work. Preserve the robots decision for every fetched page.

A robots permission is not proof of legal permission, ownership, or content-reuse rights. A robots block also does not reliably remove a URL from Google; indexing controls and access control are different concerns. [S29]

### 11.5 Frontier scheduling and politeness

The frontier unique key is crawl/scope plus normalized fetch identity. Each item has discovery provenance, depth, priority, not-before time, lease owner, lease expiry, attempt count, and terminal classification.

Schedule fairly across tenants and origins. Maintain an origin-wide budget so two Signal customers cannot collectively overload the same public site. Initial proposed connected-site limits are one in-flight HTML request per origin and at most one request per second, with lower competitor defaults and explicit customer-controlled increases. Respect `Retry-After`, reduce rate after 429/503 or latency increases, and enforce total budgets.

Prevent crawl traps: calendars, infinite filters, session IDs, search endpoints, repeated near-duplicate paths, excessive query combinations, pagination cycles, and arbitrarily expanding hostnames. Use per-template/query-pattern caps, novelty measures, and a visible `TRAP_LIMIT` reason. Never report a budget-truncated crawl as complete.

### 11.6 HTTP fetching and parsing

Use bounded connect/read/total timeouts, maximum compressed and decompressed sizes, MIME checks, and decompression-ratio controls. A proposed HTML body cap is 5 MiB decompressed; larger responses become an explicit unsupported/truncated finding, not a silently partial source used for rewriting.

Record HTTP status, redirect chain, timing, headers needed for analysis, content type, encoding, ETag/Last-Modified, raw-body hash, parser version, and structured extraction. Conditional requests may reduce bandwidth, but a 304 needs a valid retained prior body; otherwise refetch safely. HTTP conditional semantics do not guarantee that every provider implements reliable write preconditions. [S27]

Handle malformed HTML, mixed encoding, duplicate tags, invalid JSON-LD, conflicting headers/meta, `<base>` elements, and soft-404 candidates. A parser failure is not an absence of content. PDF/media URLs may be inventoried as non-HTML resources; automatic PDF content rewriting is not a GA1 capability.

Disable XML external entities. Treat sitemap compression, recursive indexes, duplicate entries, external hosts, and implausible `lastmod` values cautiously. Google documents sitemap size/URL limits; Signal additionally imposes bounded index depth and total discovery budgets. [S32]

### 11.7 Rendering strategy

Fetch HTML first. Render only when a deterministic trigger or site profile indicates it is needed: missing meaningful content, client-side navigation, important markup differences, or a performance/visual verification task.

Use pinned browser images, viewport/device settings, language, locale, cookie/consent state, and resource limits. Avoid indefinite `networkidle` waits on analytics-heavy sites; use a tested readiness contract and a hard deadline. Record which readiness condition was satisfied.

Keep raw HTML, rendered DOM, screenshot, and extracted text as separate artifacts. A rendered page is an observation under a specific environment, not proof that all users or Google see the same content. Do not impersonate Googlebot to claim equivalent indexing behavior.

### 11.8 Crawl completion and coverage

Every discovered item ends as fetched, unchanged, redirected, robots-blocked, out-of-scope, rate-deferred, budget-truncated, unsupported, parse-failed, or permanently failed. The final manifest counts these categories and records the remaining frontier.

Use CMS/sitemap/search inventory to identify pages with no observed incoming internal links. Call them “no incoming links observed in this crawl” unless the graph coverage supports a stronger orphan-page claim. Missing a page in an incomplete crawl is not evidence it was deleted.

<a id="section-12"></a>
## 12. Search, analytics, and website-performance ingestion

### 12.1 Search Console

Connect using read-only scopes. Map the selected property to verified site origins, preserving domain-property and URL-prefix differences. Store the precise query dimensions, search type, filters, aggregation mode, date range, pagination, and coverage metadata.

The Search Analytics API does not guarantee all rows, and fresh results can be incomplete. Pagination is not proof of complete demand coverage. Preserve reported incompleteness and treat unavailable query rows separately from observed zero counts. [S35]

Keep source reporting dates and timezone semantics explicit. Do not join daily GA4 and Search Console rows as if their day boundaries and attribution were identical. Reimport a configurable recent window to capture delayed corrections, switching complete generations atomically.

### 12.2 GA4

Map the actual property, timezone, currency, event/key-event definitions, and consent/reporting setup. Query only compatible dimensions and metrics and request quota metadata. Respect property-level concurrency and token quotas. [S36]

Persist response metadata for thresholding, sampling where present, and data loss into “other” rows. A thresholded or privacy-limited report cannot be treated as a complete census. Validate that configured business conversions actually exist and are firing before optimizing against them. [S37]

Do not send customer-level identifiers or raw personal analytics events to OpenAI by default. The first release uses aggregated business evidence sufficient for the task.

### 12.3 Performance data

Use pinned Lighthouse runs for repeatable diagnostics. Separate lab measurements, CrUX field results, and any optional first-party real-user monitoring. Record URL/origin granularity, device/form factor, window, and sample availability.

CrUX represents a rolling collection window and may not have data for a specific URL. Do not silently substitute origin-level values for page-level values. [S38]

Current good Core Web Vitals thresholds are LCP at most 2.5 seconds, INP at most 200 milliseconds, and CLS at most 0.1 at the 75th percentile. These are field-experience targets, not permission to break a website to improve a synthetic score. [S39]

A lab runner normally uses repeated baseline/candidate runs under the same conditions, reports medians and variance, and checks functional/visual guardrails. It does not claim a Lighthouse score is a live INP measurement. Browser timings polluted by a failed consent banner or blocked resources invalidate the comparison.

### 12.4 Dataset quality gates

Before planning from a dataset, check provenance, property/site match, freshness, completeness flags, requested versus returned scope, timezones, sample size, obvious discontinuities, duplicate generations, and currency/metric units.

If conversion instrumentation is broken, create a diagnostic task and label optimization goals as unavailable. Never invent conversion lift or backfill missing provider data with model estimates presented as observations.

<a id="section-13"></a>
## 13. Evidence, memory, and knowledge provenance

### 13.1 Evidence contracts

Evidence records contain origin, tenant/site, collection time, source effective time if known, collector version, immutable artifact hashes, access rights, confidence category, and limitations. Evidence can be superseded but not silently rewritten.

Distinguish observed facts, customer-approved business facts, externally sourced claims, inferred hypotheses, and model-generated drafts. Each proposed factual publication links to a verified claim record or explicit editorial review.

### 13.2 Memory hierarchy

Authority order: current executable policy and authenticated settings; approved business/brand facts; verified current source evidence; prior decisions with provenance; hypotheses; untrusted source text. Recency does not automatically outrank authority.

A remembered instruction to “approve all future changes” is not an approval grant. It can become a proposal to update a standing policy through the actual authenticated settings flow.

Memory has scope, source, effective period, review deadline, supersession links, and permitted uses. Conflicting facts produce a conflict record and a clarification task. Model-generated summaries never overwrite the underlying source.

### 13.3 Retrieval

Authorize tenant/site/document visibility first. Retrieve through keyword plus semantic search, rerank if evaluated, then assemble a bounded context with source IDs and dates. A citation must resolve to evidence the requesting user is authorized to inspect.

Use an approved OpenAI embedding configuration, initially a tested fixed-dimensional deployment of `text-embedding-3-small` if it meets retrieval evaluations. Store model/dimension/version with each embedding cohort. OpenAI documents its embedding models and dimensional controls; a model migration changes the retrieval space and requires reindexing. [S08]

Untrusted source instructions are quoted as data, never concatenated into developer instructions. Strip active HTML before display, but do not assume sanitizing HTML removes semantic prompt injection. The defense is restricted authority and validation, not a promise that malicious text can always be detected. [S24]


<a id="section-14"></a>
## 14. OpenAI reasoning subsystem

### 14.1 Responsibilities and limits

OpenAI supplies reasoning, planning, content drafting, code-patch proposals, and explanations. Signal supplies identity, task state, source selection, schemas, permissions, policy, budgets, execution, and verification.

The reasoner receives a bounded task packet: goal; business constraints; relevant evidence with source IDs; current resource versions; allowed recipe set; role-specific tools; maximum tool/model steps; and required output schema. It does not receive unrestricted credentials or an entire tenant database dump.

Use the Responses API through one internal adapter. Set explicit strict schemas for custom functions and structured final outputs. Handle refusal, incomplete generation, invalid/missing items, transport errors, and unsupported parameters separately. Structured Outputs constrains shape, not truth, authorization, or business correctness. [S02] [S03]

### 14.2 Specialist roles

| Role | Inputs | Outputs | Tools |
|---|---|---|---|
| Coordinator | Goals, backlog, budget, task status | Ranked work plan, bounded delegations, clarification requests | Read status/evidence, propose work |
| Analyst | Crawl, analytics, research evidence | Findings, hypotheses, uncertainty, candidate recipes | Read/compute/search within permitted scope |
| Implementer | Reviewed recipe, exact base artifacts | Draft content or sandbox patch with claim/source mapping | Read artifacts and produce candidate artifacts |
| Verifier | Candidate, base, recipe contract, independent observations | Test interpretation and unresolved semantic issues | Read verification evidence; no mutation |
| Communicator | Committed events and allowed evidence | Accurate user update or command clarification | Read scoped status only |

A specialist cannot create arbitrary child agents. Delegations have a parent run, purpose, scope, maximum cost, deadline, and return schema. The coordinator remains responsible for merging results and cannot treat agreement among models as a substitute for technical proof.

### 14.3 Bounded tool loop

A reasoning episode has a proposed default maximum of 8 model turns and 20 permitted read/tool actions, with recipe-specific lower or higher reviewed limits. Exceeding a limit produces a partial result and blocker, not an unbounded self-repair loop.

Set `parallel_tool_calls=false` where a single model-selected action per turn simplifies validation. Independent safe reads may still be parallelized by Signal's deterministic scheduler. The API setting is not a security boundary; every call is separately validated and authorized. [S02]

Process returned function-call IDs exactly. Validate arguments against the registered schema, then enforce semantic constraints and scope independently. Tool responses include success/error class, source IDs, freshness, truncation state, and bounded data. Never fabricate a successful tool result to keep a reasoning loop moving.

Before accepting a candidate, validate all referenced evidence IDs, URLs, CMS fields, repository paths, locale identifiers, metric units, recipe releases, and target versions. A schema-valid proposal that refers to nonexistent evidence is rejected.

### 14.4 Persistence and privacy

Signal retains necessary task/context state in its own encrypted stores. Use foreground calls with `store=false` as the default design, not provider-side conversation state as the durable employee memory. Store any protocol continuation items required by the selected API in protected task artifacts, without displaying private model reasoning as an audit trail.

OpenAI states that API data is not used for training by default unless the customer opts in. That is distinct from retention: abuse-monitoring, application-state, cache, endpoint, account, and region behavior still matter. `store=false` is not a universal zero-retention promise. Approve the actual account/model/endpoint data controls before sending customer data. This design does not depend on background mode; its temporary-state and regional behavior should be reviewed separately if later enabled. [S04]

Redact secrets and unnecessary personal identifiers before transmission. Prefer relevant snippets and aggregate metrics to whole private repositories or complete analytics exports. Record what categories of data were sent, to which approved model release, under which tenant policy. Customer data-export and deletion behavior must account for any persisted task artifacts.

### 14.5 Reliability and cost

Handle 429, quota exhaustion, transient 5xx, validation failures, and account/model unavailability distinctly. Apply application-owned bounded retries and global/tenant concurrency limits. Avoid hidden SDK retries that escape the usage ledger; configure retry ownership explicitly. OpenAI documents rate-limit handling and backoff, but Signal must still enforce its own budgets. [S06]

A timed-out model request can still incur charges. Preserve a conservative budget hold until usage is reconciled or a reviewed accounting procedure closes the uncertainty. A fallback is permitted only to an already evaluated OpenAI release with compatible privacy, schema, and cost settings. Do not silently switch to another vendor or an unevaluated model.

Track input tokens, cached input, output tokens, reasoning-related usage where reported, other billable tools, and actual provider usage fields. Do not double-count a reasoning subcategory already included in output tokens. Price according to a versioned procurement snapshot and reconcile against provider usage. [S07]

### 14.6 Model and prompt promotion

Prompt templates, schemas, model configuration, retrieval rules, and tool registries have release IDs. Changes pass held-out evaluations, adversarial tests, cost/latency checks, and a limited rollout before becoming the default. A successful single example is not a release gate.

Evaluate factual support, relevance, unintended edits, correct abstention, instruction-injection resistance, tool selection, cost, and completion. Include human expert review for subjective SEO/content quality. OpenAI's evaluation guidance supports systematic, representative evaluation rather than relying on informal impressions. [S05]

Do not train a production prompt to the same cases used for its acceptance score. Keep a holdout set and record evaluation-set versions. A model can be strong at drafting but still be disallowed for a specific autonomous recipe.

<a id="section-15"></a>
## 15. Competitor research, strategy, and prioritization

### 15.1 Research scope

Separate customer-named business competitors from domains competing in search results for the same intent. Maintain evidence-backed mappings rather than a single static competitor list.

Research public information through permitted crawling, approved search/data APIs, customer-provided sources, and appropriately licensed datasets. Do not treat a paid provider as automatic proof of collection or reuse rights. Record the provider's permitted use and freshness.

Compare intent coverage, original information, page type, product positioning, useful features, internal architecture, visible content changes, and user experience. Third-party traffic/keyword volumes are estimates and must be labeled accordingly. Never present them as access to a competitor's private analytics.

### 15.2 Opportunity record

Every opportunity includes a problem statement, supporting evidence, affected pages/resources, intended audience, business objective, candidate recipe, alternative explanations, expected benefit range or qualitative band, confidence basis, implementation cost, dependencies, risk, measurement plan, and expiry/review condition.

Use prioritization as an explainable heuristic, not an invented probability of ranking success. A useful form is business relevance multiplied by evidence strength and estimated benefit, adjusted for effort and cost. Prohibited or unacceptably risky actions are excluded before scoring; a high estimated benefit cannot average away a hard restriction.

### 15.3 Strategy constraints

Reserve page/template cohorts for active interventions to prevent conflicting experiments and repeated optimization churn. A traffic decline triggers diagnosis first: availability, tracking changes, seasonality, demand, ranking changes, query mix, site edits, and data incompleteness are competing explanations.

Do not automatically canonicalize two pages merely because they share keywords. Distinguish intentional audience, language, product, and funnel differences. Do not create one near-duplicate page per location without substantive local value and current policy review.

New-content briefs require an original contribution: customer expertise, verified product knowledge, useful analysis, original examples, or genuinely helpful organization. Competitor text is not a source to paraphrase at scale. Google's spam guidance explicitly addresses scaled low-value content, deceptive practices, and unauthorized automated querying. [S30]

<a id="section-16"></a>
## 16. Versioned SEO recipe system

### 16.1 Executable contract

A recipe release is an immutable signed bundle containing a human-readable procedure, a machine-readable contract, code/check references, source dependencies, supported connectors, evidence requirements, risk classification rules, approval requirements, cost/impact bounds, verification assertions, and a recovery plan.

Recipe execution is a bounded DAG of registered activity types. Arbitrary embedded shell/Python/JavaScript is not executed in the trusted workflow process. Any plugin code is reviewed, signed, sandboxed where appropriate, and version-pinned. The model cannot add an unknown step type at runtime.

Recipes have lifecycle `DRAFT -> TESTED -> REVIEWED -> CANARY -> ACTIVE`, with `SUSPENDED`, `DEPRECATED`, and `REVOKED` exits. Runtime evidence or a policy change can suspend affected uses. Only a release identity, not the Signal service identity, can promote a recipe.

### 16.2 Required recipe fields

```yaml
recipe_id: internal_link_repair
version: 1.0.0
contract_version: 1
status: REVIEWED
purpose: Repair a verified broken link without changing unrelated content.
allowed_resource_types: [wordpress_post, wordpress_page]
required_capabilities: [read_exact_version, conditional_patch, inspect_operation]
required_evidence: [source_snapshot, broken_destination_check, replacement_relevance]
allowed_fields: [body_link_target]
excluded_scopes: [checkout, authentication, legal_policy, shared_template]
max_resources_per_revision: 5
max_changed_links_per_resource: 3
preconditions:
  - target_origin_authorized
  - source_version_matches
  - replacement_is_relevant
  - replacement_is_public_and_eligible
  - recovery_artifacts_durable
policy_dependencies: [search_link_quality, source_rights, customer_protected_paths]
authority: exact_approval_or_explicit_standing_policy
steps: [validate_evidence, prepare_patch, validate_diff, authorize, apply, verify]
postconditions:
  - only_approved_links_changed
  - replacements_resolve_as_expected
  - page_function_and_structure_preserved
recovery_mode: inverse_patch_with_three_way_conflict_check
measurement:
  immediate: broken_links_resolved
  delayed: affected_page_business_metrics_with_uncertainty
```

The example caps are proposed safety defaults, not Google rules. Scope limits are also enforced cumulatively across site/tenant time windows so many small revisions cannot evade the intended limit.

### 16.3 Recipe-specific obligations

| Recipe | Preconditions | Critical validation | Recovery |
|---|---|---|---|
| Internal-link repair | Observed broken target; relevant valid replacement; exact source version | Change only selected link targets; preserve text, attributes, blocks, and locale | Inverse attribute patch, conflict on changed node |
| Title/description improvement | Verified page purpose and factual claims; correct plugin/field mapping | No unsupported claims or intent mismatch; preserve site naming/locales | Restore only those metadata fields |
| Content refresh | Outdated claim identified with reliable replacement evidence | Claim-by-claim support, editorial quality, no unrelated rewrite | Three-way paragraph/block patch |
| Contextual internal linking | Useful semantic relationship and approved destination | Natural anchor, no fabricated relevance, no circular or excessive additions | Remove only Signal-added link markup |
| Image performance | Reproducible bottleneck and licensed original asset | Preserve aspect ratio, quality, alt purpose, responsive behavior, and accessibility | Restore prior asset/reference without deleting originals prematurely |
| JavaScript performance | Identified expensive script or rendering problem | Functional tests, third-party business dependencies, security and consent behavior | Revert scoped code artifact and verify deployment |
| Structured-data repair | Visible supporting facts and type eligibility | No invented ratings/prices/authorship; validate required relationships | Restore exact prior structured-data block |
| Sitemap correction | Agreed canonical/indexability policy and URL inventory | No accidental removal of valid pages; respect scope and formats | Restore prior sitemap artifact/configuration |
| Canonical/indexing correction | Explicit intended behavior and blast-radius analysis | No cycles, accidental deindexing, locale loss, or robots/noindex confusion | High-risk reviewed restoration |
| Page consolidation | Sufficient intent/history/conversion evidence and approved redirect plan | Preserve valuable content, inbound destinations, language, and dependencies | Compensation plan; search effects are not immediately reversible |
| New publication | Original-value brief, claims/rights review, unique resource/slug | Draft/live correctness, metadata, links, schema, duplication, side effects | Unpublish/redirect only under an approved recovery policy |

Google requires structured data to reflect actual visible content and does not guarantee a rich result merely because markup validates. Canonicalization is a search preference mechanism with multiple signals; it should not be treated as a universal deduplication command. [S31] [S33]

For multilingual sites, preserve locale-specific content and reciprocal language relationships. Google's localized-version guidance is a source dependency for related recipes, not a reason to automatically canonicalize all translations to one language. [S34]

### 16.4 Recipe acceptance and retirement

Every recipe needs positive, negative, boundary, malformed-input, stale-version, policy-change, injection, and recovery fixtures. Track acceptance rate, verification failures, unintended differences, recovery conflicts, cost, and delayed outcomes by connector/site category.

A security failure suspends the capability immediately. A weak SEO result does not necessarily indicate implementation failure, but repeated poor quality triggers recipe review. An agent may propose recipe changes; it cannot self-approve a more permissive version after a failed run.

<a id="section-17"></a>
## 17. Current-policy, legal, and ethical gate

### 17.1 Policy layers and authority

Evaluate search/platform rules, applicable legal requirements, Signal's ethical prohibitions, customer restrictions, and technical safety together. A customer may make a policy stricter, not override a hard Signal prohibition or grant rights they do not possess.

The initial US consumer-review policy should cover fabricated reviews/testimonials and related deceptive behavior. The FTC publishes specific guidance; interpretation and applicability must be reviewed for the customer's actual activity. Other jurisdictions and regulated industries require their own reviewed source sets. [S52]

Do not claim that a generic “legal check” covers every country, copyright license, privacy obligation, advertising rule, or industry. Onboarding must record audience/market, business sector, content claims, data categories, and declared rights. Missing applicability information blocks relevant publication rather than being guessed.

### 17.2 Policy data model

A source has an official owner, URL, category, jurisdictions, scope, observed publication/effective dates, retrieval policy, freshness deadline, and human reviewer. A source snapshot has retrieval time, content hash, archived evidence, detected changes, and interpretation status.

A policy bundle references exact source snapshots, reviewed rules, test fixtures, approvers, effective interval, signature, and revoked/superseded status. A decision records the exact bundle, normalized input facts, evidence, reason codes, required review, and expiry.

Policy sources are fetched through a restricted source-monitoring pipeline, not through arbitrary instructions from competitor webpages. New source domains require review. Treat altered, unreachable, redirected, or compromised source pages as a source-integrity event.

### 17.3 Update workflow

Check high-change platform sources on a scheduled basis, initially daily, and maintain risk-specific legal/editorial review intervals. These are monitoring choices, not a guarantee of discovering every change instantly.

`Fetch -> Archive -> Compare -> Classify scope -> Review interpretation -> Test -> Sign -> Release -> Revalidate affected work`

Cosmetic changes need not invalidate all work. A substantive unreviewed change affecting a recipe can suspend that recipe before a new interpretation is approved. An LLM may draft the change analysis but cannot publish new executable policy.

OPA can verify signed bundles. Signature validation must be configured and tested; enabling a policy server alone does not establish trusted rule distribution. [S25]

### 17.4 Gate outputs

Return `ALLOW`, `DENY`, `REQUIRES_REVIEW`, or `STALE_POLICY`, with reason codes, affected rules, evidence, missing facts, and permitted next actions. Timeouts, invalid signatures, unsupported input schemas, unknown required categories, and unavailable current policy fail closed for the affected write.

Run the gate at recipe selection, after the concrete patch, at approval presentation, immediately before each external write, and before recovery that could restore problematic material. Unrelated read-only work may continue when safe.

### 17.5 Rules that require deterministic checks

Enforce protected paths/fields, verified origins, valid actor scope, recipe/capability certification, forbidden operations, required approvals, current permissions, cost limits, source freshness, content-rights status, and the absence of unresolved conflicting operations through code and policy facts.

Semantic checks, such as whether a claim is genuinely substantiated or content is misleading, combine evidence rules, model assistance, and human review. A second model saying “compliant” is not a deterministic legal control. Unknown high-impact claims route to review.

<a id="section-18"></a>
## 18. Exact changes, approvals, and authorization

### 18.1 Sealed change manifest

The manifest includes tenant/site, change/revision ID, recipe release, connector release and binding, target resource IDs, locales, expected versions, exact field patches or code artifacts, dependency order, evidence/claim references, impact analysis, test results, approval class, cost ceiling, recovery mode/artifacts, publication window, and expiry.

Store canonical manifest bytes and their hash. Use a vetted JSON canonicalization implementation rather than assuming ordinary JSON serialization is stable; RFC 8785 defines a canonicalization scheme. Restrict manifest numeric fields to unambiguous representations and hash artifact bytes separately. [S28]

A rendered approval page is generated from the sealed manifest. It must not display one diff and execute another. Show Unicode/invisible-character anomalies, changed destinations, removed content, metadata, and affected templates. Do not hide critical changes in collapsed “advanced” sections.

### 18.2 Approval classes

| Class | Example | Initial rule |
|---|---|---|
| A0 | Read-only audit/research | Autonomous within approved data/cost scope |
| A1 | Local drafts and sandbox patches | Autonomous within recipe scope |
| A2 | Outbound nonproduction action such as a PR | Explicit onboarding standing grant or exact approval; disclose notifications/public visibility |
| A3 | Narrow production metadata/content patch | Exact human approval in GA1; standing grant possible only after recipe eligibility gates |
| A4 | Indexing, canonical, redirect, shared-template, or large-impact changes | Explicit high-risk approval and step-up; two-person review when required |
| A5 | Destructive/uncertified/forbidden operation | Disabled in GA1 |

A proposed default approval lifetime is 24 hours for A3 and one hour for A4, shortened where evidence or publication windows require it. These values live in policy, not scattered code. Underlying state and current permissions are checked regardless of remaining lifetime.

### 18.3 Approval identity and revocation

An approval records authenticated actor, authentication strength, channel, exact revision hash, target scope, decision, timestamp, expiry, and current permission context. Telegram identity is not inferred from display name or username. A high-risk chat approval uses a one-time dashboard link and fresh step-up authentication.

An approver cannot approve on behalf of a different user by supplying an actor ID. The authorizer derives identity from authenticated context. Two-person rules require two distinct eligible identities and separation-of-duties policy.

Revocation, role removal, integration disconnect, resource transfer, recipe revocation, scope changes, relevant policy updates, and site pause invalidate dispatch eligibility. Already accepted external work is reconciled and reported, not erased from history.

### 18.4 Standing authorization

Standing authorization specifies explicit recipe release IDs, or a compatible range resolved at grant time into an immutable reviewed set of release IDs, sites, resource/field scopes, excluded paths, maximum impact per operation/day, maximum spend, publication windows, start/end dates, and recovery behavior. It is a structured policy record approved by an authorized human, not a remembered chat sentence.

Its use creates an auditable authority decision per change. Signal cannot increase its own allowance or split a large job to evade aggregate limits. High-risk operations never inherit low-risk standing permission because the recipe initially described itself as low risk.

<a id="section-19"></a>
## 19. External execution protocol and concurrency

### 19.1 Dispatch protocol

For each change item or dependency step:

1. Resolve the immutable manifest and certified adapter. Validate hashes and all tenant/site references.
2. Acquire a short resource lease with a monotonically increasing fence. Check for unresolved operations affecting the resource or dependency scope.
3. Read current provider state through the adapter. Validate exact base/version, effective publication scope, and no-op conditions.
4. Read current permissions, site pause/execution epoch, integration generation, recipe status, and applicable policy from authoritative sources.
5. Evaluate policy and reserve cost/impact budgets in short transactions. Create the operation with a stable identity and intent hash.
6. Ensure before-state and recovery artifacts are durably readable. Write the minimal operation intent and artifact references to an independent write-safety journal and wait for its configured durable acknowledgement.
7. Issue a short-lived permit bound to this operation, exact intent, current epochs, fence, and maximum side effect. The gateway rechecks its current validity immediately before dispatch.
8. Atomically mark the dispatch attempt, commit, and call the provider using the certified idempotency/CAS mechanism. Do not hold a database lock across the network call.
9. Record response/receipt or mark `OUTCOME_UNKNOWN`. Classify application state through provider status and independent reads.
10. Verify live delivery separately, settle or retain budget holds, release the resource only when safe, and emit factual events.

The journal is not a second independent workflow authority. It is a disaster-recovery record of which externally consequential intents may have been sent. It contains no reusable provider secrets and is stored outside the primary database's failure domain.

### 19.2 Idempotency and retries

A stable operation identity survives activity retry, worker restart, approval-message replay, and database recovery. A new transport attempt does not get a new logical intent. An internal unique constraint prevents a second operation for the same revision item/step.

Use native provider idempotency when certified. CAS alone can prevent repeated state transitions only to the extent the provider truly enforces the condition and associated side effects. It is not a blanket guarantee that notifications, hooks, or downstream systems execute exactly once.

If current state already satisfies the intended result before dispatch, record `ALREADY_SATISFIED` as a no-op reason with operation state `NOT_APPLIED`. The goal may be satisfied, but no Signal write occurred. Do not claim Signal caused the change.

### 19.3 Ambiguous outcomes

A timeout, connection reset, worker death, or lost acknowledgement after sending a request means the outcome may be unknown. Probe operation status, resource version/history, provider receipts, and postconditions according to the adapter's consistency contract.

Seeing old content once does not prove the write failed; a provider or CDN may be eventually consistent. Reconciliation waits within a bounded certified window. If no safe conclusion is possible, retain the hold, block conflicting writes, and request operator/customer review. Never guess “not applied” simply to keep the queue moving.

### 19.4 Resource conflicts and fencing

Leases coordinate Signal workers. Fences stop a worker that lost its lease from acquiring fresh authority. The credential-bearing gateway validates the fence; a sandbox never receives a token that would let it bypass this check.

External humans do not honor Signal's locks. Safe automatic publishing therefore requires a native or certified atomic compare-and-write operation. A provider that supports only read-then-overwrite remains draft/manual apply for that capability.

A lease expiry does not prove an old request stopped executing. An unresolved dispatched operation keeps its target quarantined even if the lease expires. A new writer cannot race a late completion.

### 19.5 Partial success and compensation

A multi-resource change is a DAG of operations, not a cross-provider ACID transaction. Declare which steps can proceed independently, which must stop on failure, and which need compensation. Record every applied step and actual provider version.

Do not automatically compensate if a human has edited the affected resource or if compensation violates current policy. Surface a partial-delivery state with precise completed/blocked items. A fully compensated workflow still retains the original delivery and recovery history.

Creation is modeled explicitly as an expected-absence operation against a normalized destination/creation key. It does not pretend a nonexistent page has an existing CMS version or resource ID. The connector must certify conditional creation and stable identity reconciliation. After application, an immutable operation-to-resource result maps the creation intent to the actual provider resource. Quarantine covers both the creation destination and any resolved resource while the outcome is uncertain.

### 19.6 Failure classifications

`AUTH_REVOKED`, `POLICY_DENIED`, `POLICY_STALE`, `VERSION_CONFLICT`, `CAPABILITY_UNCERTIFIED`, `RATE_LIMITED`, `BUDGET_EXHAUSTED`, `OUTCOME_UNKNOWN`, `PROVIDER_REJECTED`, `VERIFICATION_FAILED`, and `RECOVERY_CONFLICT` are different conditions with different next actions. A generic retry button must not bypass those distinctions.

<a id="section-20"></a>
## 20. WordPress delivery contract

### 20.1 Bridge requirements

Build a narrowly scoped Signal Bridge plugin with dedicated capabilities and routes. Use a dedicated integration user and HTTPS application-password or other certified authentication flow. WordPress documents application-password authentication and custom endpoints with permission callbacks; the bridge must apply its own per-operation permission checks. [S40] [S41]

Do not connect an administrator credential merely for convenience. The dedicated role should expose only the bridge capability and required read access, not unrestricted core content administration. All bridge methods independently validate target, field allowlist, tenant binding, operation identity, signature/permit, expected version, and request size.

### 20.2 Atomicity certification

For each supported WordPress/database/plugin combination, prove compare-and-write on the exact supported fields. A bridge cannot advertise atomic behavior merely because it takes an application-level mutex.

The implementation must coordinate the actual database records it updates, verify their current raw values/version under an appropriate transaction/conditional operation, apply only approved fields, and persist its operation receipt consistently with the content mutation. Validate storage-engine transaction support and the behavior of missing/duplicate metadata records.

WordPress hooks, plugins, caches, mailers, and external automations can create effects outside that transaction. Enumerate and test them. Unknown or irreversible side effects make the capability ineligible for automatic execution until reviewed. Do not claim a MySQL transaction rolls back an email already sent by a plugin.

### 20.3 Content and metadata correctness

Support WordPress core post/page content first. Preserve Gutenberg block structure, shortcodes, custom fields, localization relationships, author, publication timestamps, and status unless explicitly approved. Generic HTML reserialization can break editor blocks; use a certified block-aware or exact-substring patch with unique-match checks.

Yoast's REST API is read-only. Any Yoast metadata writes require a separately tested bridge mapping for the installed version, correct capability checks, and verification of the actually rendered metadata. Do not assume a generic REST/MCP wrapper makes those writes supported. [S42]

WordPress revisions may not cover all SEO metadata or plugin state. Signal captures its own exact before-state for supported fields. A database backup is not the per-change Undo mechanism.

### 20.4 Draft and publication semantics

A proposal to change a published post remains a Signal draft or a separately certified staging revision. Do not change an existing published post to `draft` merely to prepare edits. Preserve scheduled publication and pending editorial workflows unless the approved manifest explicitly changes them.

New resource creation needs stable operation identity, slug collision handling, language/site binding, and a provider receipt that survives response loss. If a create request times out, look up the exact operation before attempting another create. Do not duplicate an article because its first creation succeeded without an acknowledgement.

### 20.5 Cache and live verification

After commit, run certified idempotent cache invalidation where required and record it as a separate operation if externally consequential. Verify both authoritative content state and public rendered output. Confirm the right locale, canonical URL, SEO fields, page status, and unchanged protected elements.

A stale CDN response produces `PROPAGATION_PENDING`, not immediate success or an automatic repeated write. Unexpected plugin rewrites, stripped attributes, or unapproved field changes trigger verification failure and incident/recovery review.

### 20.6 Other CMSs

Each CMS must satisfy the same contract using its own semantics. Webflow's publication model distinguishes staged/live state and can involve broader publication scope, so approval must match the actual operation's effects. Do not port WordPress assumptions into another adapter. [S49]

<a id="section-21"></a>
## 21. GitHub, sandbox builds, and deployment delivery

### 21.1 Repository onboarding

Install a least-privilege GitHub App on selected repositories. Discover default branch, branch protections/rulesets, build commands, lockfiles, required checks, deployment environments, preview behavior, protected paths, and workflow side effects.

PR/branch creation can trigger workflows and notifications even when no production merge occurs. Certify the repository workflow behavior before permitting automated outbound branch/PR work. Treat dangerous trigger patterns and untrusted build-script access to secrets as a blocker. GitHub's secure-use guidance describes workflow and untrusted-input risks. [S45]

### 21.2 Sandbox contract

Clone only the approved commit and required history. Disable or separately review submodules and Git LFS network expansion. Do not reuse a writable workspace across tenants. Pin base images and dependency lockfiles; bound CPU, memory, disk, processes, time, and network access.

Build dependencies may run arbitrary code. Resolve approved dependencies through a controlled package mirror/proxy where practical, then run builds without credentials and with restricted egress. Never mount the host Docker socket or trusted control-plane filesystem into a customer build.

gVisor can add isolation between workload and host kernel, but it is not a complete security or egress policy. Use disposable VMs when the workload's risk exceeds the sandbox profile. [S55]

### 21.3 Patch validation

The implementer outputs a patch artifact. The gateway validates allowed paths, actual symlink resolution, file types, executable bits, line endings, binary changes, generated files, and total impact. Disallow traversal, symlink escapes, hidden credential changes, and edits to protected workflows/authentication/payments/infrastructure paths.

Run formatting, type checks, unit tests, relevant integration tests, build, link checks, accessibility checks, visual comparison, and performance checks against the actual candidate. The patch cannot update the test suite to hide its own regression without separate approval.

### 21.4 PR and merge semantics

The PR includes reason, evidence, exact changes, test results, risks, recovery plan, and Signal revision/operation IDs. Capture the head SHA and reviewed base. GitHub's merge API can require a matching head SHA, but that does not itself freeze the base branch or prove a deployment is safe. [S43]

Require branch/ruleset protections, current required checks, and the repository's certified merge/deployment flow. If the base changes materially, regenerate the candidate and revalidate. When an exact resulting artifact is required, approve that artifact after merge-queue/test-merge validation and before production promotion.

Do not use a merge operation that unexpectedly includes a stack of other PRs or unrelated pending changes. The adapter declares the actual merge scope and supported mode. Unknown asynchronous outcomes are reconciled through the provider's operation/result and repository state, not guessed from a webhook alone.

### 21.5 Delivery Contract

A repository is eligible for automated delivery only when its contract identifies: approved source commit/tree; artifact digest; build/test provenance; trusted CI identity; intended environment; deployment initiator; allowed URL/origins; promotion gate; current deployment identity; health checks; and recovery mechanism.

The protected CI workflow is installed/reviewed by a human and pinned. Signal's ordinary code-edit role cannot alter it. Preview environments are access-controlled and nonindexable without leaking preview indexing directives into production.

A signed deployment receipt and an independent live check establish delivery. A PR merge, a successful build, or a green GitHub check alone is insufficient. For sites without this contract, Signal stops at a reviewable PR and accurately reports deployment as external/manual.

<a id="section-22"></a>
## 22. Verification, optimization, and outcome measurement

### 22.1 Verification layers

| Layer | Required question |
|---|---|
| Structural | Is the patch well formed and within approved fields/paths? |
| Semantic | Are facts supported and changes appropriate for page intent and locale? |
| Policy | Do current applicable rules permit the concrete result? |
| Functional | Do navigation, forms, consent, layout, accessibility, and business functions still work? |
| Provider | Did the intended resource/version change, without unrelated mutations? |
| Delivery | Is the intended artifact/content actually live at the approved origin? |
| Measurement | Is there enough quality data to assess the business outcome? |

The verifier has separate observations and test fixtures from the implementer. A model reviewing its own generated prose is useful as one check, not sufficient verification. Some content claims require a named human reviewer.

### 22.2 Technical postconditions

Check exact target and locale, status/redirect behavior, intended field values, unchanged protected fields, supported schema, canonical/indexability intent, functional smoke tests, public delivery identity, and recovery artifact availability. For performance changes, compare repeatable baseline/candidate runs and verify no material functional or visual regression.

Define a propagation window per adapter/CDN. During it, use bounded polls and a visible pending state. After it expires, open an incident or manual review; do not automatically publish the same change repeatedly.

### 22.3 Search-engine observation limits

Search Console URL Inspection reports Google's indexed information, not an immediate live-page test. The Indexing API is restricted to eligible content types rather than a universal SEO publication endpoint. Signal must not advertise instant indexing for arbitrary pages. [S50] [S51]

Technical verification is immediate or bounded by delivery propagation. Ranking/click/conversion effects require an observation period. Record any external ranking-update annotations as context, not automatic proof of causation.

### 22.4 Experiment contract

Before deployment, record the primary metric, guardrails, baseline interval, eligible page/query cohort, comparison cohort where defensible, minimum information requirement, observation interval, confounders, and stop conditions.

Do not create different SEO content for search-engine bots and human users as a testing strategy. Prefer page-cohort comparisons, staged legitimate changes, or time-series analysis. Avoid comparing overlapping interventions as if each acted alone.

Report sample counts, completeness, effect estimate where meaningful, uncertainty, and alternative explanations. Repeated looks at noisy data and selective stopping can produce misleading apparent improvements; predeclare the analysis approach. A small site may remain inconclusive, and Signal must say so.

### 22.5 Optimization safeguards

Do not automatically roll back on a single weak traffic day. Do roll back or pause promptly for confirmed technical harm such as broken navigation, accidental noindex, wrong redirects, or failed critical functionality, under the approved emergency recovery policy.

Exclude conversions or money from optimization claims when tracking is not validated. Do not label an increase in impressions as revenue improvement. A page can gain traffic while reducing qualified conversions; guardrails must reflect the customer's goals.

<a id="section-23"></a>
## 23. Undo, compensation, and recovery

### 23.1 Recovery preview

Undo starts with current-state inspection, not direct restoration. Show the original change, current provider state, later edits, dependent changes, policy checks, intended inverse patch, possible irreversible exposure, and recovery expiry.

Recovery modes are `EXACT_RESTORE`, `INVERSE_PATCH`, `COMPENSATION`, `MANUAL_REVIEW`, and `UNAVAILABLE`. The UI may display “Undo” for convenience but must explain the actual mode before consequential execution.

### 23.2 Three-way recovery

Let B be the before-state, S the state after Signal's change, and C the current state. Compute the inverse of B-to-S against C. Preserve changes in C that do not overlap Signal's touched fields/nodes. If a touched element has changed ambiguously, stop with a conflict and show a proposed resolution.

For code, create a scoped revert/recovery PR against current HEAD and run the same build/deployment checks. Do not reset the branch or restore an old whole-repository snapshot to undo one SEO patch.

For CMS metadata, restore only certified affected fields. For new content, unpublishing may break links or remove later human work, so it is compensation subject to impact review, not necessarily an exact reversal.

### 23.3 Dependencies and policy

Recover child changes in a safe dependency order. If another approved change relies on a new URL or content element, show the dependency and require a combined recovery plan. Do not delete media still referenced by another page.

Current legal/ethical policy can block restoring previous prohibited content. Emergency recovery is separately authorized and narrowly scoped; it cannot become a universal policy bypass. Already viewed content, distributed emails, search-engine history, and third-party caches cannot be recalled by restoring a database field.

### 23.4 Recovery verification

A recovery has its own immutable manifest, approval/standing authority, operation IDs, before/current artifacts, provider receipts, and verification. It links to the original change without changing that change's history.

Test repeated Undo requests, recovery after partial publication, recovery after a human edit, recovery after recipe revocation, missing artifacts, expired credentials, and a failure halfway through compensation. The result must be verified recovered state, an explicit partial state, or a conflict, never an unsupported “undone” label.


<a id="section-24"></a>
## 24. Complete dashboard specification

### 24.1 Navigation and context

Persistent navigation contains organization/site selection, Overview, Signal Chat, Work, Strategy, Pages, Changes, Approvals, Analytics, Recipes, Policy, Connectors, Usage, Settings, and Help. Show current environment and site execution state prominently. A production/staging distinction must not depend on color alone.

Global controls include pause, notifications, command search, account/session settings, and support. Show an always-visible indicator for unresolved externally consequential operations. Do not bury an unknown publication outcome under a generic “task failed” notification.

### 24.2 Screen contracts

| Screen | Required content | Actions and edge states |
|---|---|---|
| Sign in / account security | Identity provider, verification, MFA, recovery, session management | Expired/replayed links, rate limits, disabled account, unavailable mail/IdP |
| Organization setup | Organization name, owner, invitations, permission explanation | Existing membership, invitation collision, final-owner protection |
| Site onboarding | Origins, ownership proof, business profile, goals, locale, timezone | Verification failure, wrong property, changed origin, incomplete setup resume |
| Connector catalog | Provider, supported scope, prerequisites, data access, write/recovery capability | Unavailable provider, authorization cancelled, capability unsupported |
| Connector details | Account/resource binding, scopes, health, sync state, credential generation | Reconnect, disconnect, reduce scope, diagnose missing permission |
| Overview | Verified activity, qualified business metrics, freshness, blockers, next actions | No data, partial import, paused site, stale metrics, tracking failure |
| Signal Chat | Conversation, evidence links, typed command cards, approval handoff | Interrupted stream, ambiguous site, permission denial, sensitive-data warning |
| Work queue | Task state, owner/agent, dependency, priority, cost, blocker | Pause/cancel, retry only when safe, stale evidence, exhausted budget |
| Strategy | Opportunities, evidence, alternatives, confidence basis, expected effort | Accept/defer/reject, revise goal, resolve missing business facts |
| Pages | URL/resource/locale inventory, status, indexability evidence, issues, history | Redirected/duplicate/blocked URLs, partial graph, unsupported content |
| Page details | Raw/rendered observations, source versions, links, metadata, changes | No snapshot, outdated capture, permission-limited fields |
| Approval inbox | Exact revision, impact, risk, policy/tests, cost, recovery, expiry | Approve/reject/request edits, expired/revoked/superseded approval |
| Change details | Before/after diff, artifacts, operation timeline, provider/live evidence | Partially applied, propagation pending, unknown result, later human edits |
| Recovery | Current-versus-before comparison, dependencies, conflict preview | Authorize recovery, choose reviewed resolution, unavailable/expired recovery |
| Analytics | Source-specific charts, date windows, quality flags, cohorts, deployment annotations | Insufficient data, thresholding, gaps, timezone/currency differences |
| Recipes | Enabled/certified versions, source dependencies, limits, outcomes | Suspended recipe, unsupported connector, pending promotion |
| Policy center | Applicable bundles, source freshness, decisions, required reviews | Changed source, stale category, hard prohibition, disputed interpretation |
| Usage/budgets | Actual spend, reservations, unknown costs, forecasts, quotas | Limit changes with authority, overspend accounting, recovery headroom |
| Settings | Business profile, protected paths, autonomy, approval roles, publication windows | Conflicting edits, invalid settings, permission/step-up requirements |
| Audit/export | Filterable event history and evidence-safe exports | Redacted data, expired artifacts, long-running export, access revocation |
| Support/status | Current incidents, integration diagnostics, runbook guidance | Redacted support bundle, scoped support-access request |

### 24.3 Approval interaction

Default to a semantic summary plus a complete inspectable diff. Display changed destinations and claims, not only word counts. Include resource count, template reach, irreversible side effects, exact authority requested, maximum approved cost, policy/test state, and recovery limitations.

Approval controls are disabled when the revision is stale, missing required evidence, blocked by policy, expired, or outside the user's permissions. The server independently enforces the same conditions. Bulk approval requires each included revision to remain eligible and makes partial acceptance explicit.

After approval, show “Approval recorded; revalidation pending” until the actual operation advances. Never optimistically display “Published” before verified live delivery.

### 24.4 Security and accessibility

Render provider HTML and model Markdown through a maintained sanitizer. Raw captures open as downloads or isolated previews with restrictive sandbox/CSP and no privileged same-origin access. External resources in previews are blocked or safely proxied under the same authorization and egress policy.

Avoid unsanitized HTML injection, clickable untrusted `javascript:` links, and embedded forms. Redact credential-bearing URLs, including bot-token path components, before logs or UI display. File uploads have size/type checks, quarantine, content inspection, safe storage keys, and no executable preview privileges.

Target WCAG 2.2 AA for the application: keyboard operation, focus management, readable status/error messages, sufficient contrast, accessible dialogs/diffs/charts, reduced-motion support, and screen-reader labels. Automated checks are necessary but not sufficient for accessibility review. [S53]

### 24.5 Frontend state and performance

Use typed generated API clients and stable error codes. Keep server state separate from unsaved draft state. Abort obsolete read requests on site switches and scope cache keys by tenant/site/filter. Never let a cached response from one organization appear in another.

Use optimistic updates only where reversible and truthful, such as a local draft edit. Authority changes, approvals, publication, recovery, and connector state wait for server confirmation. Long work shows stages and evidence, not fabricated percentage progress.

Charts preserve missing-data gaps and source quality. Display site/source timezone and a clear comparison interval. Avoid showing a percentage change when the baseline is zero or insufficient; use an explicit explanation.

<a id="section-25"></a>
## 25. Telegram and conversational control

### 25.1 Pairing and account binding

Begin pairing from an authenticated dashboard session. Issue an expiring, single-use nonce tied to user, organization, allowed site scopes, and expected channel. Complete it using Telegram's numeric user and chat IDs, not mutable usernames. Store chat identifiers as strings or safe 64-bit values; never assume JavaScript's unrestricted numeric precision.

Group chats are read-only by default and require explicit organization configuration. A group administrator is not automatically a Signal approver. Forwarded messages and copied buttons do not convey authority.

### 25.2 Command interpretation

Parse natural language into a typed proposal with resolved scope, requested action, parameters, and expected consequences. Read-only requests can proceed when unambiguous. Settings/content modifications show a confirmation card when interpretation could materially affect scope. An LLM interpretation cannot override server permission checks.

Supported intent classes include explain, inspect, research, revise draft, reprioritize, adjust approved settings, connect via secure link, approve exact revision, revoke, pause, resume, and request recovery. Unsupported operations receive a precise capability explanation rather than a simulated success.

### 25.3 Approval and recovery messages

A callback token refers to server-side state and is bound to intended user/channel/revision/action/expiry. It contains no long-lived credential or editable approval payload. Deduplicate callback updates. Verify current membership, current revision, and current policy at processing time.

High-risk actions require the dashboard step-up flow. Chat offers the entry point and reports the result. Pairing a Telegram account does not grant permanent MFA equivalence or permission to expand connector scopes.

### 25.4 Notifications and delivery truth

Generate updates from committed events. Coalesce low-priority progress; escalate policy/security blocks, unresolved publication outcomes, and required human actions according to preferences. Quiet hours can suppress routine updates, not silently disable an explicitly configured emergency channel.

Track notification queued, transport accepted, failed, and acknowledged states separately where supported. Provider acceptance is not proof the human read the message. A blocked bot or unreachable channel cannot cause the workflow to assume approval.

Notifications use an outbox and deduplication key. Delivery may still be at least once when the transport lacks idempotency, so messages include stable operation/revision references and duplicate clicks remain safe.

<a id="section-26"></a>
## 26. Budgets, resource scheduling, and capacity

### 26.1 Budget model

Maintain separate measured units: currency micro-units, model tokens, paid research calls, HTML navigations, subresource requests, rendered-page milliseconds, build milliseconds, bytes, and externally modified resource counts. A limit in one unit cannot be compared with another.

Budget accounts have an explicit period start/end, timezone rule, hard limit, spent amount, reserved amount, and overage state. Operations and model requests reserve against tenant/site/task accounts atomically in a deterministic lock order.

Preparing a proposal estimates cost but does not hold scarce execution capacity for days while waiting for approval. Before dispatch, reserve the current worst-case permitted work and verify it fits the user's approved ceiling. A material increase requires reapproval or a smaller revision.

### 26.2 Settlement and unknown cost

Reservations are converted to actual usage with immutable ledger entries. Record real overage even when actual cost exceeds an estimate; never reject the accounting record merely because it would cross the limit. Block new discretionary work and explain the discrepancy.

Do not release a reservation simply because a worker lease or HTTP timeout expired. A chargeable request may still have completed. Retain a conservative hold, reconcile available provider data, and close uncertainty only through a documented procedure.

Reserve headroom for verification, reconciliation, and authorized emergency recovery before allowing a production write. Exhausting an ordinary research budget must not leave a partially applied production operation unexamined. Emergency spending remains bounded and explicitly preauthorized.

### 26.3 Fair scheduling

Use separate work classes for interactive commands, approvals/reconciliation, critical verification, ordinary crawl, rendering, builds, research, and background measurement. Critical safety work has priority but still has quotas and anti-abuse limits.

Enforce per-tenant, per-site, per-provider-account, and global concurrency. For crawling, HTML navigation politeness and browser subresource budgets are distinct; all traffic remains accounted for. A laboratory performance run must not be compared against an unthrottled baseline while secretly applying the crawler's navigation throttle to one side.

Use short queue leases and heartbeat/visibility metrics. A failing tenant cannot monopolize all workers through retries. A provider-level circuit breaker pauses the relevant integration class rather than consuming every available slot.

### 26.4 Initial capacity test envelope

These are proposed qualification loads, not measured hardware promises: 50 organizations, 100 connected sites, one million known URLs, 50,000 HTTP page fetches per day, a controlled rendered subset, 100 concurrent dashboard sessions, and a bounded number of simultaneous publication workflows. Certify higher per-site limits separately.

Storage is calculated from actual capture size and retention. For example, 50,000 captures/day at 100 KB each is approximately 5 GB/day before replication, indexes, rendered artifacts, or backups. Do not estimate total infrastructure from model tokens alone.

Load tests must include large tenants, hot sites, skewed query distributions, slow providers, long reports, and policy-update bursts. Scale execution workers separately from the database and command API.

<a id="section-27"></a>
## 27. Security, data protection, and audit engineering

### 27.1 Threat model

Model at least these adversaries: malicious internet content; compromised customer CMS/repository; malicious tenant member; stolen session/chat identity; malicious connector server; compromised build dependency; compromised sandbox; stolen provider credential; privileged operator misuse; and accidental actions from a well-intentioned model or human.

For each boundary, record attacker capability, protected asset, entry point, prevention, detection, containment, and recovery. Security review includes the customer-side bridge and CI integration, not only Signal's API.

### 27.2 Credentials and network controls

Use workload identities or short-lived credentials between services where supported. Restrict secret paths by integration/tenant and action. Credential-bearing services never execute untrusted scripts. Rotate keys and test revocation, failed rotation, and a secret manager that is unavailable or sealed.

TLS is required externally and for sensitive internal service links. Keep database, Temporal administration, policy administration, secrets administration, and observability administration private. A reverse proxy is not authorization for internal APIs.

Block sandbox egress to private networks and the control plane. Use separate production/test credentials and provider projects. Test that accidental environment-variable inheritance cannot expose secrets to builds or crawls.

### 27.3 Application security

Test object-level and function-level authorization on every endpoint, not only role checks on pages. Use parameterized SQL, bounded schemas, safe redirect handling, content-type validation, upload controls, secure cookies, CSRF protection, CSP, and rate limits.

Do not log full authentication headers, OAuth codes, tokens, private URLs, raw analytics identifiers, or unrestricted model prompts by default. Redaction must cover structured logs, error traces, request paths, telemetry attributes, support bundles, and dead-letter queues.

### 27.4 Supply chain

Pin application dependencies and container images; produce a software bill of materials and license inventory. Verify recipe, policy, connector, bridge, and worker release signatures. Scan dependencies/images and triage findings with documented ownership and deadlines.

Do not let an AI-generated PR change protected CI checks, security policy, or release signing configuration as part of an SEO fix. New dependencies require justification, license/security review, and reproducible lockfile changes.

### 27.5 Audit design

Every consequential transition records actor/workload identity, tenant/site, action, target, manifest and artifact hashes, recipe/model/connector/policy releases, approval/permit references, provider operation/request identity, previous/new domain versions, event time, and reason codes.

Use per-aggregate sequence numbers for ordering, avoiding a single global sequence lock on all writes. Sign periodic checkpoints and store them in an independently controlled location. Verify chains/checkpoints during support and recovery exercises.

Keep personal data out of immutable audit payloads where possible. Store sensitive evidence by encrypted reference so retention/deletion can remove payloads while preserving a minimal lawful operational record. A hash can still have privacy implications; do not assume hashing automatically anonymizes every identifier.

### 27.6 Data export and deletion

Exports are authenticated asynchronous jobs with a manifest, scope, expiry, encrypted delivery, and access revalidation at download. Exclude secrets and unauthorized cross-site data. Spreadsheet/CSV exports neutralize formula injection; raw HTML exports are attachments rather than executable app pages.

Deletion requires owner authority and confirmation, is resumable, and tracks each storage system. Delete derived embeddings, caches, artifacts, conversation attachments, and exported packages as well as SQL rows. Backups age out under an explicit schedule; restored systems replay deletion tombstones before serving data.

<a id="section-28"></a>
## 28. Observability, reliability targets, and support

### 28.1 Telemetry

Propagate a correlation ID across command, workflow, model run, operation, connector request, verification, and notification. OpenTelemetry is the instrumentation layer; select and operate actual metrics/log/trace storage rather than treating instrumentation as the backend. [S56]

Metrics include command acceptance latency, queue age, workflow age, model/tool failure rates, provider latency, unknown-outcome count/age, approval expiry, policy freshness, verification failures, recovery conflicts, crawl coverage, quota saturation, database lag, artifact integrity, and cost per verified change.

Use bounded labels. Do not put every URL, query, user, or operation ID into Prometheus label dimensions. Detailed identifiers belong in authorized traces or event records.

### 28.2 Proposed operating targets

| Measure | Initial qualification target | Important qualification |
|---|---|---|
| API durable command acceptance | p95 at most 500 ms under qualified load, excluding user authentication round trips | Does not include task completion |
| Ordinary dashboard reads | p95 at most 2 seconds under qualified load | Large exports are asynchronous |
| Visible workflow-event projection | p95 at most 5 seconds when dependencies are healthy | UI labels stale data during degradation |
| New-write pause propagation | Healthy gateway observes committed pause before its next dispatch check; qualification test bounds idle-gateway response to 2 seconds | Already accepted requests may finish |
| Business DB recovery point | At most 15 minutes under tested backup policy | Externally consequential intents require independent pre-dispatch journal durability |
| Service recovery time | At most 4 hours under the tested recovery profile | Not an advertised SLA until demonstrated |
| Unsafe cross-tenant or unauthorized writes | Zero accepted in qualification tests; any incident is critical | Not a statistical claim of impossible failure |

Availability objectives must match deployment topology and operational coverage. A two-host pilot is not highly available. Do not publish a 99.9% commitment based on architecture diagrams alone.

### 28.3 Alerting and runbooks

Page the responsible operator for unauthorized-write evidence, tenant isolation failure, missing recovery artifacts for an active operation, prolonged unknown outcomes, compromised keys, failed journal durability, broken backups, or a production indexing regression.

Create actionable alerts with scope, evidence links, first safe action, and runbook ID. Avoid paging on every transient model failure. Route ordinary data freshness issues to the product with clear degraded status.

Support diagnostics are redacted. Support access is time-limited, customer-visible where appropriate, and auditable. Runbooks include exactly which operations are safe while the system is paused.

<a id="section-29"></a>
## 29. Self-hosted deployment, backups, and disaster recovery

### 29.1 Deployment profiles

**Development:** local Compose, synthetic tenants, local fake providers, no production credentials. Development conveniences must be impossible to enable silently in a production manifest.

**Private pilot:** a trusted control/data host plus a separate isolated execution host and independent encrypted backups/journal. Recoverable but not highly available. Low concurrency and no public uptime promise.

**Customer production:** separate trusted application/workflow capacity, PostgreSQL primary and tested standby/restore path, appropriately distributed OpenBao quorum, isolated execution hosts, private administration, and independent backup/journal storage. Co-location is permitted only where the failure-domain and security analysis remains acceptable.

Use declarative provisioning, immutable image digests, environment-specific configuration, health/readiness checks, and a deployment manifest. Do not run a production Temporal development server or put all security-recovery keys only on the host they must recover.

### 29.2 Installation preflight

The installer validates DNS/TLS, public base URL, reverse-proxy behavior, clock synchronization, database connectivity/version/extensions, migration status, Keycloak realm/client setup, OpenBao readiness/recovery material, policy signatures, artifact storage, off-site journal, email delivery, OpenAI access/capabilities, provider callback URLs, sandbox isolation, backup restore smoke test, and owner bootstrap.

Fail visibly on missing required inputs. Never replace a missing OpenAI key, mail service, or connector with mock data in production. Optional integrations are shown as not configured, with honest missing capabilities.

### 29.3 Backup scope

Back up Signal PostgreSQL, Temporal persistence, Keycloak database/configuration, OpenBao supported snapshots and separately protected recovery keys, immutable artifact manifests/payloads, release manifests, encryption-key metadata, and write-safety journal checkpoints.

Use PostgreSQL-aware backup/WAL tooling such as pgBackRest and regularly restore it. Copying a running data directory casually is not the backup procedure. [S17]

Backups are encrypted, checksum-verified, access-limited, and stored outside the primary failure domain. Test missing keys, corrupted objects, an interrupted upload, and a backup older than a successful provider write.

### 29.4 Recovery protocol

1. Freeze new dispatch and isolate or stop old credential-bearing executors. Treat old workers as potentially alive until fenced.
2. Establish a new execution epoch/signing-key generation through a recovery control not silently rolled back with the business database. Revoke/rotate provider access where necessary.
3. Restore databases, identity, secret services, artifacts, and compatible worker releases into an isolated environment.
4. Reapply deletion/revocation tombstones and policy/credential changes that occurred after the restored snapshot.
5. Load write-safety journal intents after the recovery point. Reconstruct the set of operations that may have reached providers.
6. Reconcile each affected resource against provider state/history/receipts. Quarantine unresolved targets; do not infer success or failure from stale local state.
7. Rebuild projections and validate cross-store references, artifact hashes, current policy, and active approvals. Old approvals require revalidation.
8. Run smoke/security/connector tests with write egress still disabled.
9. A designated operator records recovery evidence and releases narrow write capability incrementally.

Restoring independently backed-up systems is not automatically a globally consistent restore. The epoch and reconciliation procedure is mandatory precisely because the CMS, GitHub, database, and workflow history may represent different moments.

### 29.5 Upgrades and maintenance

Apply expand/contract migrations, worker-version compatibility, connector recertification, and signed release manifests. Test rollback of application code separately from rollback of data migrations. Never downgrade data blindly after a forward-only migration.

Schedule certificate/key rotation, dependency updates, database maintenance, backup drills, policy reviews, provider deprecation checks, and capacity reviews. These are product operations, not optional work after launch.

### 29.6 Cost model

Maintain a measured cost sheet with compute, storage/IOPS, backups, journal writes, traffic, OpenAI usage, research APIs, email, monitoring, and operator time. Avoid fixed vendor prices in the architecture because prices, regions, availability, and plan terms change.

The customer sees actual usage plus reservations and uncertain costs. Operations can compare cost per audit, per approved change, per verified change, and per useful business outcome. Cheaper inference that produces more failed or unsafe work is not necessarily cheaper engineering.

<a id="section-30"></a>
## 30. Test architecture and quality requirements

### 30.1 Test layers

| Layer | Scope | Mandatory behavior |
|---|---|---|
| Static | Types, formatting, schemas, dependency rules, secrets, licenses | Errors block merge; generated clients/contracts stay synchronized |
| Unit | Pure domain logic, policy input derivation, parsers, diff/recovery, accounting | Boundary and negative tests, not only happy paths |
| Property-based | URL normalization, scope checks, canonical hashes, ledger arithmetic, patch inversion | Generate malformed/ambiguous inputs and compare invariants |
| Database integration | Real supported PostgreSQL with production-equivalent roles/RLS | No SQLite substitute for authorization/concurrency/constraint tests |
| Contract | Every provider/MCP capability | Recorded/synthetic fixtures plus a maintained sandbox-provider test |
| Workflow | Replay, timers, cancellation, duplicate messages, worker upgrades | Histories remain compatible and effects remain deduplicated |
| End-to-end | Browser onboarding through verified delivery and recovery | Test real auth/session boundaries and all relevant UI states |
| Fault injection | Crash/timeout/drop/reorder at every side-effect boundary | Unknown outcomes and reconciliation remain correct |
| Security | SSRF, injection, IDOR, XSS, CSRF, token leaks, sandbox escape attempts | Negative assertions and containment checks |
| Model/recipe evaluation | Held-out domain tasks and adversarial content | Quality, abstention, provenance, safe tool use, cost, latency |
| Load/soak | Qualified tenant/URL/user/queue distribution | Fairness, bounded growth, recovery, and no data corruption |
| Recovery | Full restore with external systems ahead of local state | No resumed write until reconciliation and operator release |

### 30.2 Test infrastructure

Use deterministic fake CMS/GitHub providers that can apply a write then lose its response, return stale reads, reject versions, rotate credentials, delay publication, duplicate hooks, and emit reordered webhooks. Keep a real provider sandbox for behaviors the fake might model incorrectly.

Database tests run as the same non-owner roles used in production. Include tests where a pooled connection serves tenant A then B and where a record references the wrong site inside the correct tenant. Test query authorization separately from UI visibility.

Use a synthetic site corpus containing malformed markup, multilingual pages, query traps, robots failures, redirect cycles, JS rendering, consent banners, large media, broken links, duplicate metadata, and deliberately adversarial instructions. Never use live customer sites as the uncontrolled fault-injection environment.

### 30.3 Model evaluation thresholds

Before autonomous eligibility for a narrow recipe, evaluate at least 200 held-out representative tasks for that intended capability, with documented expert labels and negative cases. Proposed acceptance target: at least 95% reviewer-acceptable outputs, zero critical unauthorized or unsupported-publication cases, and no regression beyond the release's approved cost/latency limits.

Report uncertainty and sample composition. Zero observed critical failures is not proof of zero failure probability. Small subgroups need additional cases rather than borrowing confidence from unrelated tasks. Repeat stochastic evaluations and monitor production drift.

High-risk content still requires appropriate human approval even after strong evaluation performance. Quality scores cannot override hard policy or technical checks.

### 30.4 Coverage and mutation testing

Proposed minimums: 90% branch coverage for authority/execution/recovery/budget domain modules and 80% for the overall backend, with all invariant-critical branches explicitly exercised. These are floors, not evidence of adequacy by themselves.

Use mutation testing on critical logic and investigate surviving mutants. A test suite that passes after removing tenant scope or approval expiry is unacceptable even if line coverage is high. Never lower thresholds or delete tests to make an AI-generated patch green.

### 30.5 Flaky tests and release evidence

Critical safety tests cannot be quarantined to ship. Investigate nondeterminism, isolate external dependencies, and fix the underlying cause. Noncritical flaky tests require an owner, tracked reason, and expiry; they cannot silently disappear.

Every release stores test runs, model/recipe evaluations, schema migration evidence, vulnerability triage, connector certification, recovery drill results, known limitations, and approval signatures in a release manifest. Claims must identify whether a check was actually executed, manually reviewed, or merely specified.


<a id="section-31"></a>
## 31. Systematic edge-case discovery

### 31.1 Discovery method

Do not use the case registry below as a substitute for analysis. For every state transition and external boundary, examine failure before the operation, during transmission, after remote acceptance, before local commit, after commit but before event delivery, during recovery, and during a concurrent human change.

For each capability, complete a hazard review covering authority, target identity, data quality, scope expansion, concurrency, partial effects, irreversibility, detection latency, and recovery. Record severity, affected parties, prevention, detection, residual risk, owner, and required test. Unknown high-impact behavior disables automatic execution until characterized.

Use production incidents, support reports, fuzzing, provider changes, model drift, dependency updates, and restore exercises to expand the registry. A previously unknown case becomes a regression fixture before the fix is considered complete.

### 31.2 Edge-case and solution registry

The identifiers below are also required test-case identifiers. Each case must be exercised against the appropriate unit, integration, contract, or end-to-end layer, with the expected result asserted rather than merely logged.

| ID | Failure or edge case | Required handling and test assertion |
|---|---|---|
| EC-001 | Tenant A submits Tenant B's resource ID | No data disclosure or action; generic not-found/denied response; security event |
| EC-002 | Correct tenant but wrong site/integration reference | Composite scope validation rejects it before provider access |
| EC-003 | Pooled DB connection retains previous tenant context | Transaction-local reset plus RLS test proves no cross-tenant rows |
| EC-004 | Member removed while an approval page is open | Action-time membership check rejects approval/dispatch |
| EC-005 | Last owner is removed by two concurrent requests | Locked transaction preserves at least one active owner |
| EC-006 | Invitation link replayed or accepted by wrong identity | Single-use and identity condition reject misuse |
| EC-007 | Matching email from another identity provider | No automatic account merge or permission inheritance |
| EC-008 | Stolen/expired low-strength session attempts high-risk approval | Fresh verified step-up required; no production permit |
| EC-009 | User changes organization while old requests are in flight | Scoped cache/request cancellation; server scope still enforced |
| EC-010 | Site ownership transfers outside Signal | Reverification/disconnect event suspends old write authority |
| EC-011 | OAuth callback state missing, wrong, or replayed | Reject without storing credentials or creating a binding |
| EC-012 | User cancels provider consent or grants fewer scopes | Explicit incomplete/read-only connection; no pretend success |
| EC-013 | Two workers refresh a rotating token concurrently | Serialized credential-generation update; no lost valid token |
| EC-014 | Provider token revoked during a workflow | Stop new affected calls, reauthorize, reconcile sent writes |
| EC-015 | Reconnection selects a different account/resource | New binding generation; old approvals do not carry over |
| EC-016 | Webhook signature invalid or body changed by proxy | Reject before business processing or expensive work |
| EC-017 | Webhook duplicate or out of order | Durable deduplication; reconcile current provider version |
| EC-018 | Connector tool/schema changes unexpectedly | Quarantine capability and require recertification |
| EC-019 | Disconnect races with dispatch | Current binding/epoch checked; sent request remains tracked |
| EC-020 | One external site connected for writes by two tenants | Protected control claim prevents concurrent independent ownership |
| EC-021 | URL resolves to private or metadata IP | Egress denies before connection; include IPv6/mapped forms |
| EC-022 | DNS rebinding after hostname validation | Connection-level validated destination prevents private access |
| EC-023 | Redirect crosses origin with credentials attached | Do not forward credentials; revalidate target scope |
| EC-024 | Browser script requests internal host or opens WebSocket | Same egress policy applies to subresources and network types |
| EC-025 | Robots is 404, 401, 429, 5xx, or redirected unsafely | Apply distinct conservative outcomes and preserve evidence |
| EC-026 | Robots changes midway through a crawl | Unsent frontier work respects new restrictions |
| EC-027 | Calendar/faceted navigation creates infinite URLs | Pattern/depth/novelty limits terminate with coverage warning |
| EC-028 | Query order, case, or encoded slash changes meaning | Normalization preserves identity; property tests prevent false merges |
| EC-029 | Gzip bomb, huge body, or XML external entity | Bounded parsing and entity restrictions stop processing safely |
| EC-030 | HTML charset or malformed markup breaks extraction | Explicit parse failure; no fabricated empty-content diagnosis |
| EC-031 | 304 returned but prior body is missing | Safe unconditional refetch or explicit incomplete observation |
| EC-032 | 429/503 and long Retry-After | Fair bounded delay; no aggressive retry loop |
| EC-033 | Page uses endless requests so network-idle never occurs | Tested readiness condition and hard render deadline |
| EC-034 | Crawl is budget-truncated or pages are robots-blocked | Partial coverage visible; no complete-site or orphan claim |
| EC-035 | Malicious GET link resembles logout/delete/action endpoint | Exclude unsafe action patterns and never submit forms |
| EC-036 | Wrong Search Console property selected | Property/origin validation blocks ingestion or labels mismatch |
| EC-037 | Search rows are incomplete or privacy-limited | Preserve quality flags; no zero imputation |
| EC-038 | Import crashes halfway through pagination | Active generation unchanged until a full validated swap |
| EC-039 | Provider revises recent historical data | Reimport window creates new generation without duplication |
| EC-040 | GA4/Search Console day boundaries differ | Preserve source timezones and qualify comparisons |
| EC-041 | GA4 metric renamed/incompatible or key event absent | Metadata validation and configuration blocker |
| EC-042 | Thresholding, sampling, or other-row aggregation | Flag/report limitations; do not claim a complete count |
| EC-043 | CrUX lacks page data but has origin data | Explicit granularity; no silent substitution |
| EC-044 | CTR baseline is zero or row ratios differ | Aggregate numerators/denominators; undefined percent shown honestly |
| EC-045 | Tracking breaks during an experiment | Suspend outcome claims and diagnose instrumentation |
| EC-046 | Page/repository tells model to ignore policy | Content remains untrusted data; production authority unchanged |
| EC-047 | Model returns valid JSON with nonexistent source IDs | Semantic validation rejects unsupported proposal |
| EC-048 | Model refuses or output is truncated | Explicit partial/refusal state; no forced unsafe continuation |
| EC-049 | Model repeats tools or delegates indefinitely | Episode, delegation, cost, and tool caps stop the loop |
| EC-050 | Model alias changes behavior | Drift evaluation; suspend affected autonomy; no silent release |
| EC-051 | OpenAI timeout may have incurred charges | Retain conservative hold and record unknown cost |
| EC-052 | Policy source changes while approval waits | Re-evaluate affected revision before dispatch |
| EC-053 | Policy bundle signature invalid or evaluator unavailable | Deny affected writes, preserve safe read-only work |
| EC-054 | Jurisdiction/rights/claim evidence is missing | Required review rather than model-invented compliance |
| EC-055 | Recipe tries to promote itself or expand permissions | Release identity and immutable contract prevent escalation |
| EC-056 | Approval replayed from Telegram and web simultaneously | One logical grant/action; duplicate command is harmless |
| EC-057 | Approved content edited afterward in Signal | New sealed revision/hash; previous approval invalid |
| EC-058 | CMS human edit occurs before dispatch | Atomic provider version/field precondition rejects stale patch |
| EC-059 | CMS only offers read-then-overwrite | Automatic write capability disabled; draft/manual apply |
| EC-060 | Worker crashes before sending request | Journal/ledger show safe unsent or reconcile if uncertain |
| EC-061 | Provider applies write then response is lost | `OUTCOME_UNKNOWN`; reconcile, never blind retry |
| EC-062 | Worker crashes after response but before DB receipt | Recover from provider/journal identity and postconditions |
| EC-063 | Old worker wakes after lease expiry | Fence/permit denies new dispatch; late sent effect reconciled |
| EC-064 | Provider returns stale reads after success | Propagation window and status probe; no duplicate mutation |
| EC-065 | User pauses after remote request was accepted | No new authority; in-flight completion accurately tracked |
| EC-066 | Aggregate limit evaded using many small jobs | Rolling site/tenant impact budget blocks excess |
| EC-067 | Cost limit lowered below current spend/holds | Account remains accurate; new discretionary work blocked |
| EC-068 | Budget reservation expires while provider work is unknown | Hold remains until authoritative or reviewed settlement |
| EC-069 | One operation in a multi-page batch fails | Respect DAG independence/compensation; show partial state |
| EC-070 | Provider sanitizes or plugin rewrites approved fields | Postcondition failure; investigate unexpected mutation |
| EC-071 | WordPress draft preparation would unpublish a live post | Separate draft/staging strategy preserves live status |
| EC-072 | Slug collision or timed-out content creation | Stable operation lookup, no duplicate publication |
| EC-073 | WordPress hooks trigger email or other external effects | Side effects certified/disclosed or automatic capability blocked |
| EC-074 | PR head or base changes after review | Revalidate exact candidate; head-SHA condition alone is insufficient |
| EC-075 | Merge operation would include unrelated PRs/staged changes | Scope mismatch blocks operation |
| EC-076 | Build succeeds but deployed artifact differs | Delivery identity mismatch; not marked verified |
| EC-077 | CDN serves old or inconsistent content | Propagation pending, bounded verification, escalation |
| EC-078 | Performance score improves while checkout/navigation breaks | Functional guardrail blocks promotion or triggers recovery |
| EC-079 | Lab baseline and candidate use different throttling/settings | Comparison invalid; rerun with matched contract |
| EC-080 | Undo requested after human edited the same field | Conflict, no blind overwrite |
| EC-081 | Undo requested after unrelated human edit | Inverse/three-way patch preserves unrelated work |
| EC-082 | Later change depends on earlier content/URL | Dependency-aware recovery plan and approval |
| EC-083 | Recovery would restore now-prohibited content | Current policy blocks or requires safer compensation |
| EC-084 | Recovery artifacts missing or expired | Honest unavailable/manual state; incident if promise was still active |
| EC-085 | Compensation fails halfway or Undo is repeated | Own operation identities, partial state, safe reconciliation |
| EC-086 | Database restored behind external systems | Frozen egress, new epoch, journal/provider reconciliation |
| EC-087 | Old executor remains alive during failover | Isolate/fence old generation before enabling new dispatch |
| EC-088 | Backup corrupted or encryption key missing | Restore gate fails; alert before claiming recoverability |
| EC-089 | Artifact upload succeeds but SQL commit fails | Orphan cleanup after grace period; no unreferenced authority |
| EC-090 | SQL reference commits but artifact is unreadable | Publication blocked; integrity monitor detects inconsistency |
| EC-091 | Replica lag exposes stale permissions | Authoritative checks use primary, never replica |
| EC-092 | Deadlock/serialization failure during budget or owner update | Retry whole bounded transaction, not partial statements |
| EC-093 | Partition migration loses uniqueness or history compatibility | Migration/constraint/replay tests block release |
| EC-094 | Full disk, WAL growth, or connection exhaustion | Backpressure, safe write freeze, actionable operational alert |
| EC-095 | Tenant deletion followed by backup restore | Deletion tombstones reapplied before serving tenant data |
| EC-096 | Approval HTML contains script or deceptive hidden characters | Sanitized isolated rendering and full diff visibility |
| EC-097 | Telegram bot blocked or callback comes from another person | Delivery failure/current identity validation, no implicit approval |
| EC-098 | UI stream reconnects with another tenant's event cursor | Scope check rejects replay and discloses no data |
| EC-099 | User double-clicks or refreshes during a write command | Idempotency returns existing command/result |
| EC-100 | Export contains spreadsheet formulas or raw active HTML | Safe CSV neutralization and attachment/isolation behavior |
| EC-101 | Support bundle or URL path leaks a token | Redaction tests fail the release; rotate leaked secret if real |
| EC-102 | Email reset flow configured but delivery fails | Explicit retry/support path; production onboarding gate fails |
| EC-103 | Advertised feature lacks a certified capability | UI says unavailable with reason; no fake control or claimed support |
| EC-104 | Dependency update weakens protected tests/policy | Ownership, signature, and CI rules block unauthorized change |
| EC-105 | Healthy demo but sustained load starves verification | Priority/fairness/soak tests preserve critical safety capacity |

<a id="section-32"></a>
## 32. Release gates

All gate outcomes must be backed by executed evidence. A planned test is `NOT_EXECUTED`, not `PASS`. A waived mandatory safety gate means the corresponding production capability remains disabled.

| Gate | Required evidence | Failure behavior |
|---|---|---|
| G-00: Contract consistency | Schemas, APIs, states, capabilities, error codes, and requirement mapping agree | No implementation release candidate |
| G-01: Identity and tenancy | Auth/session/MFA/invitation tests, privilege audit, tenant/site isolation, final-owner race | No customer access or secrets |
| G-02: Secure execution boundary | SSRF/subresource tests, sandbox containment, credential separation, supply-chain review | No untrusted crawl/build work |
| G-03: Crawl correctness | Golden-site corpus, robots/URL/parser tests, coverage/politeness under load | Read-only crawl not released |
| G-04: Data integrity | Real-DB constraints/RLS, import generations, metric quality, timezone handling | Analytics/planning source unavailable |
| G-05: OpenAI and recipes | Model/schema/privacy/budget tests, held-out quality, source/claim validation | No enabled AI execution recipe |
| G-06: Policy authority | Source update workflow, signed bundles, stale/denied cases, applicability review | Affected production writes blocked |
| G-07: Approval and mutation safety | Exact-diff grants, revocation races, provider CAS/idempotency, journal durability | No production publishing |
| G-08: Verification and recovery | Live postconditions, later-human-edit tests, partial compensation, recovery expiry | No recoverable-write claim |
| G-09: GitHub delivery | Workflow trust, protected paths, candidate/build/deploy identity, CI secret isolation | PR-only capability or disabled outbound work |
| G-10: Product completeness | Real onboarding/connectors, all screen states, web/chat parity, accessibility | No GA1 user-ready claim |
| G-11: Operations | Load/soak results, on-call owner, alerts, backup restore, post-restore reconciliation | No production operating approval |
| G-12: General availability | Signed release manifest, supported-version matrix, resolved critical/high-risk blockers, customer docs | Launch blocked or narrowed to honestly advertised certified scope |

### 32.1 Gate-specific minimum artifacts

G-07 must include fault injection at every dispatch boundary and no unauthorized/unintended writes in the qualification suite. G-08 must demonstrate both a successful non-overlapping undo and a blocked overlapping conflict. G-11 must restore with the external provider intentionally ahead of the restored database.

G-10 includes signup/invitation from a fresh browser, a real provider authorization round trip, incomplete consent, successful reconnect, role revocation, one full approval-delivery-recovery flow, missing-data analytics, and a failed-notification path. A set of static screenshots does not satisfy it.

G-12 requires a named engineering owner, security reviewer, SEO/domain reviewer, database/recovery owner, and operating/support owner. A small team may combine responsibilities, but mandatory two-person approval cannot be simulated by one person holding two titles.

### 32.2 Evidence manifest

```yaml
release_id: signal-ga1-candidate-001
status: NOT_RELEASED
artifacts:
  app_image_digests: []
  schema_migration_set_hash: null
  worker_release_ids: []
  connector_certifications: []
  recipe_release_ids: []
  policy_bundle_ids: []
  model_release_ids: []
gates:
  G-00: NOT_EXECUTED
  G-01: NOT_EXECUTED
  G-02: NOT_EXECUTED
  G-03: NOT_EXECUTED
  G-04: NOT_EXECUTED
  G-05: NOT_EXECUTED
  G-06: NOT_EXECUTED
  G-07: NOT_EXECUTED
  G-08: NOT_EXECUTED
  G-09: NOT_EXECUTED
  G-10: NOT_EXECUTED
  G-11: NOT_EXECUTED
  G-12: NOT_EXECUTED
```

This is a release-record template. The empty evidence is intentional: this document does not invent successful tests for software that has not been implemented.

<a id="section-33"></a>
## 33. Implementation sequence and dependency plan

### Milestone 1: Executable foundations

Create the monorepo, typed contracts, migration framework, production-equivalent local stack, identity/session flow, tenant/site model, scoped database roles, commands/outbox/inbox, artifact service, audit ledger, secrets integration, and CI. Complete the identity/isolation and state-contract tests before attaching customer credentials.

Deliverable: a user can sign in, create/select an organization and site, see accurate empty states, issue a harmless durable command, observe its result, and verify that another tenant cannot access it.

### Milestone 2: Read-only evidence system

Implement verified origins, connector authorization/resource binding, safe crawl/frontier, deterministic extraction, first-party analytics import generations, quality flags, evidence storage, and inventory/dashboard views. Establish data retention and support diagnostics.

Deliverable: reproducible audit findings with source evidence and honest coverage; a real reconnect and permission-revocation flow.

### Milestone 3: Reasoning, strategy, and recipes

Implement OpenAI adapter, bounded role loops, provenance-aware retrieval, typed proposals, opportunity scoring, recipe registry, evaluation corpus, and policy-source registry. Drafts remain local/nonproduction. Add source/claim checks and current-policy decisions.

Deliverable: Signal identifies an issue, explains evidence, proposes a reviewed recipe, prepares an exact draft, and abstains appropriately on missing facts or unsafe requests.

### Milestone 4: Execution protocol against hostile fake providers

Implement sealed manifests, exact approvals, standing policy, epochs/fencing, budget reservations, write-safety journal, operation attempts, reconciliation, independent verification, and three-way recovery. Use deliberately unreliable fake providers before real publishing.

Deliverable: duplicate messages, lost responses, concurrent human edits, pauses, and restore scenarios all have safe, visible outcomes.

### Milestone 5: Certified WordPress vertical slice

Implement the Bridge, exact version/field handling, core content and selected metadata support, editor preservation, operation receipts, cache handling, and live checks for the supported stack. Certify three narrow initial recipes before expanding publication scope.

Deliverable: one customer-like site passes discovery through approved publication and conflict-aware undo without unapproved changes.

### Milestone 6: GitHub and delivery

Implement repository trust inspection, isolated builds, patch validation, PR work, check attestation, deployment observation, and optional certified promotion. Protect CI/secrets and test branch/head/base changes.

Deliverable: a performance change produces a reviewed PR and, on a certified pipeline, a verified deployment and scoped recovery.

### Milestone 7: Product and operations completion

Complete all dashboard states, Telegram controls, notifications, settings, export/deletion, documentation, installation, upgrades, backups, monitoring, support, and load/failure exercises. Do not treat these as optional polish after the engine is built.

Deliverable: every GA1 gate has evidence, every advertised capability has a supported-version contract, and unsupported paths are clearly disabled.

### Milestone 8: Bounded autonomy and expansion

Enable standing authorization only for proven recipes under narrow limits and measured customer conditions. Expand CMS support one capability at a time with the same certification and recovery standards.

Do not remove gates to accelerate expansion. A new provider integration inherits none of another provider's concurrency, publication, or undo guarantees.

<a id="section-34"></a>
## 34. Clean engineering and repository structure

```text
signal/
  apps/
    web/                       # Authenticated dashboard and BFF
    api/                       # Typed API and command entrypoints
  services/
    identity/                  # Memberships, roles, site scope, sessions
    commands/                  # Command/outbox/inbox contracts
    evidence/                  # Artifacts, observations, provenance
    crawl/                     # URL policy, frontier, fetch, parse
    analytics/                 # Import generations, metrics, quality
    planning/                  # Goals, opportunities, bounded reasoning
    policy/                    # Applicable rules and authorization inputs
    execution/                 # Permits, operations, dispatch, reconciliation
    verification/              # Independent technical/live checks
    recovery/                  # Inverse patches and compensation
    notifications/             # Event-driven delivery
  workers/
    temporal/                  # Deterministic workflows
    activities/                # Trusted I/O activities
    sandbox/                   # Isolated browser/build runner
  packages/
    contracts/                 # Schemas, generated clients, error taxonomy
    openai_adapter/            # API calls, usage, privacy, bounded retries
    mcp_gateway/               # Certified tool/connector mediation
    connector_sdk/             # Capability and result contracts
    artifact_client/
    observability/
  connectors/
    wordpress/
    github/
    google_search_console/
    google_analytics/
    telegram/
  wordpress_bridge/
  recipes/
  policies/
    sources/
    rules/
    fixtures/
    releases/
  db/
    migrations/
    roles/
    constraints/
    fixtures/
  tests/
    unit/
    property/
    database/
    contracts/
    workflow_replay/
    fault_injection/
    security/
    e2e/
    load/
    recovery/
    model_evals/
  infra/
    compose/
    provisioning/
    release_manifests/
  docs/
    adr/
    runbooks/
    customer/
    compatibility/
```

### 34.1 Dependency rules

Domain logic depends on typed ports, not provider SDKs. Provider adapters implement ports. Workflow code calls registered activities rather than arbitrary database/network clients. UI calls typed APIs rather than reading tables directly.

Use one canonical schema for each contract and generate clients where practical. Do not maintain slightly different handwritten versions of the same status, permission, or tool schema across Python, TypeScript, policy, and UI.

### 34.2 Definition of done

A change is done when its behavior, error paths, security/scope implications, schema/API compatibility, tests, observability, migration/rollback plan, documentation, and operational ownership are complete. New externally consequential behavior also needs connector/recipe certification and recovery evidence.

No swallowed exceptions, catch-all “success” responses, production mocks, TODO authorization checks, arbitrary sleeps as synchronization, unbounded loops/queries, hidden retries, hardcoded customer IDs, runtime `latest` images, duplicate domain logic, or tests edited merely to accommodate broken behavior.

AI-generated code is reviewed under the same rules. The coding agent may not weaken quality gates, change unrelated architecture, add unjustified dependencies, or declare completion from unit tests alone. Every implementation PR maps to requirements, invariants, and edge cases.

### 34.3 Documentation that ships

Ship an installation guide, owner onboarding guide, connector permissions guide, supported-version matrix, autonomy/approval explanation, recovery limitations, privacy/subprocessor information, backup/upgrade instructions, incident/support process, and user-visible error remedies.

Examples and screenshots must reflect the actual released UI. Avoid a documentation-only feature that the application does not implement.

<a id="section-35"></a>
## 35. Customer readiness and operating ownership

### 35.1 Required external setup

Before accepting customers, configure the production OpenAI project and approved model releases; real OAuth applications/callbacks/scopes; Google production consent/verification as required; GitHub App installation/webhooks; Telegram bot/webhook; mail relay and delivery; DNS/TLS; encrypted backup/journal destination; and operator access/recovery.

These dependencies cannot be replaced by a paragraph saying “connect later” while claiming the product is ready. The installer and dashboard must identify missing setup and block the affected feature.

### 35.2 Commercial boundary

GA1 supports authenticated organizations with explicitly administered entitlements and usage limits. No payment-card collection is required for a private/self-hosted deployment. If self-service paid signup is enabled, hosted checkout, verified payment webhooks, entitlement reconciliation, invoices, cancellation, refunds/disputes, and a clear grace-period policy become additional mandatory release work.

A billing failure may stop new discretionary work but must not silently destroy data or prevent already-authorized safety reconciliation. Do not build a custom card-data vault in the name of self-hosting.

### 35.3 Customer-facing promises

Promise supported capabilities, explicit permissions, traceable changes, measured verification, visible limitations, and tested recovery within a defined window. Do not promise universal CMS compatibility, guaranteed rankings, guaranteed legal compliance, irreversible-change undo, or absolute immunity to failure.

A customer must be able to understand what Signal is doing, stop new work, revoke connectors, export their records, request deletion, inspect a proposed change, reject it, and see what happened after approval.

<a id="section-36"></a>
## 36. Review corrections incorporated into this revision

| Issue in the earlier direction | Resolution in this specification |
|---|---|
| Local/Gemini model routing conflicted with the selected brain | OpenAI is the reasoning provider; self-hosting remains the infrastructure default |
| “Connected CMS” implied broad safe write support | Field/version-specific certification and explicit capability classes |
| A local lock could be mistaken for external concurrency protection | Native/certified upstream compare-and-write is required for automatic publishing |
| Durable workflow implied exactly-once remote effects | Separate operation identity, provider contract, ambiguity state, and reconciliation |
| A successful API response could be called delivery | Independent provider/live/artifact verification |
| A merge approval could ignore changing base/deployment artifact | Head/base/candidate checks plus a certified Delivery Contract |
| Undo could overwrite a later human edit | Three-way inverse patches, dependencies, and explicit conflicts |
| Policy freshness might become an autonomous rule rewrite | Reviewed source interpretation and signed policy releases |
| A backup could resume old writes after restore | Independent intent journal, new execution epoch, frozen egress, reconciliation |
| Reservations could expire while spend was unresolved | Conservative holds and explicit settlement |
| “Dashboard” omitted identity and connector lifecycle | Complete account, authorization, reconnect, setting, error, and support flows |
| Table-level tenant IDs alone implied full isolation | Composite tenant/site references, runtime roles, RLS, request scope, and adversarial tests |
| Analytics averages and partial rows could mislead | Import generations, source grains, numerators/denominators, quality flags |
| More infrastructure could be confused with better engineering | Modular application, measured scaling, minimal necessary stateful systems |
| A detailed document could be confused with a release certificate | Explicit `NOT_EXECUTED` gates and evidence-based production acceptance |

<a id="section-37"></a>
## 37. Final engineering position

The core deliverable is not an autonomous prompt loop. It is a controlled change system around an OpenAI reasoning engine: trustworthy observations, provenance, reviewed procedures, current policy, exact authority, constrained execution, independent verification, and safe recovery.

The implementation is production-ready only when those properties are demonstrated across the supported release scope, including failures and human interference. Unsupported scope must remain visibly unsupported. Serious engineering means making limits explicit and enforcing them, not presenting an untested universal guarantee.


<a id="appendix-a"></a>
# Appendix A. Canonical logical database catalog

## A.1 How to implement this catalog

This is the normative logical field and integrity inventory. It is not an assertion that migrations already exist or have run. Generate reviewed migrations, typed repositories, API schemas, and fixtures from explicit application contracts; gate them against real PostgreSQL as specified in section 30. Every table below has an owner, retention behavior, and defined query path. Split a table only to address measured performance, a different security boundary, or a genuinely different lifecycle.

**Notation:** `?` means nullable; all other listed domain columns are required. `hash` means `bytea` with length 32 or validated lowercase 64-character hex at the API. `enum(...)` means a named contract enum implemented through a migration-safe database check/type. `jsonb`, arrays, and generic references require versioned schemas and explicit validation; authority-bearing relationships require real relational references. `vector(D)` describes a fixed embedding cohort, not runnable SQL without choosing D.

**Common columns:** Global (`G`) tables have `id uuid PRIMARY KEY` and `created_at timestamptz NOT NULL`. Tenant (`T`) tables have `id uuid`, `tenant_id uuid NOT NULL REFERENCES app.tenants(tenant_id)`, `created_at`, and primary key `(tenant_id,id)`. Site (`S`) tables additionally have non-null `site_id`, FK `(tenant_id,site_id)` to `app.sites(tenant_id,id)`, primary key `(tenant_id,id)`, and unique `(tenant_id,site_id,id)` for scoped references. `T-root` is the documented tenant-root exception. These implicit fields are not repeated in every row. Mutable records add `updated_at` and nonnegative `row_version bigint`; immutable records must not expose ordinary update/delete privileges.

A same-site child always references the parent's **three-column** scoped key. Do not reduce that to a globally unique UUID reference simply because UUID collisions seem unlikely. Tenant-level integrations may support several sites, but every binding, resource, and change is site scoped. Any mixed-scope table explicitly lists nullable site_id and its required scope discriminator or action-time validation. Root tenant rows are provisioned by an audited bootstrap service, not by an anonymous API role.

Tables that mention an external object key also require the storage service's canonical reference validation, encryption, scope check, and existence/integrity semantics. The database cannot prove remote object durability. Signed release hashes and signatures do not grant a release permission to execute without current lifecycle checks.

Where a parent relationship has a kind discriminator, enforce the allowed kind with a relational subtype, constrained foreign-key design, or validated database function. Do not rely on an unverified JSON field for policy-release or model-release identity. Generated SQL must also add supporting indexes for foreign keys not already served by the indexes listed here.

**JSON-array duplication rule:** Arrays such as plan opportunity IDs or grant binding IDs are human-readable manifest material, not parallel authority. Normalize the authoritative edges into junction tables or immutable manifest-validation records with actual scoped FK checks. The sealed manifest hash covers both the manifest and normalized interpretation. Revalidate the exact materialized set at execution.

## A.2 Platform releases and identities

### `control.users` [G]

**Domain fields:** oidc_issuer text; oidc_subject text; display_name text; contact_email text?; disabled_at timestamptz?.

**Integrity:** Unique (oidc_issuer, oidc_subject); email is not identity. **Query indexes:** Unique issuer/subject.

**Owner:** Identity. **Lifecycle:** Mutable profile; retain pseudonymous actor identity while referenced; erase optional personal fields under retention policy.

### `control.releases` [G]

**Domain fields:** kind enum(model,recipe,connector,policy,application); semantic_version text; manifest_hash hash; manifest_object_key text; signing_key_id text; signature bytea; published_at timestamptz; effective_at timestamptz.

**Integrity:** Unique (kind, semantic_version); signed bytes and hash immutable. Kind-specific details must match the manifest schema. **Query indexes:** (kind, published_at DESC).

**Owner:** Release management. **Lifecycle:** Immutable; retain while referenced by any evidence, execution, or supported recovery.

### `control.release_events` [G]

**Domain fields:** release_id uuid; event_type enum(activate,suspend,revoke,supersede); actor_user_id uuid; reason text; replacement_release_id uuid?.

**Integrity:** FK release_id/replacement to releases and actor to users; append-only. Activation requires recorded gate evidence. **Query indexes:** (release_id, created_at, id).

**Owner:** Release management. **Lifecycle:** Append-only lifecycle; no deletion of revocation history while release referenced.

### `control.model_profiles` [G]

**Domain fields:** release_id uuid; provider text; provider_model_id text; requested_snapshot text?; api_family text; input_schema_version int; toolset_hash hash; prompt_hash hash; evaluation_artifact_key text; data_control_profile jsonb.

**Integrity:** Unique release_id; FK release of kind model; provider must be OpenAI in GA1; every JSON field is schema-versioned. **Query indexes:** (provider, provider_model_id).

**Owner:** Model release. **Lifecycle:** Immutable release detail; credentials are never stored here.

### `control.connector_definitions` [G]

**Domain fields:** release_id uuid; provider text; adapter_version text; mcp_protocol_version text; supported_versions jsonb; capability_schema_version int; certification_artifact_key text.

**Integrity:** Unique release_id; FK release of kind connector. Feature certification is version-specific, not a provider-wide boolean. **Query indexes:** (provider, adapter_version).

**Owner:** Connector release. **Lifecycle:** Immutable release detail.

### `control.recipe_definitions` [G]

**Domain fields:** release_id uuid; recipe_key text; contract_version int; graph jsonb; allowed_scope jsonb; test_artifact_key text.

**Integrity:** Unique release_id; FK recipe release; graph references registered activities, never unrestricted executable strings. **Query indexes:** (recipe_key, created_at).

**Owner:** Recipe release. **Lifecycle:** Immutable release detail.

### `control.policy_sources` [G]

**Domain fields:** source_key text; owner_name text; source_url text; category text; jurisdiction_codes text[]; fetch_policy jsonb; review_interval_seconds int; active bool.

**Integrity:** Unique source_key; positive review interval; URL must be an approved source-monitoring origin. **Query indexes:** (active, category).

**Owner:** Policy operations. **Lifecycle:** Mutable registry; revisions audited.

### `control.policy_source_snapshots` [G]

**Domain fields:** source_id uuid; retrieved_at timestamptz; content_hash hash; artifact_key text; published_at timestamptz?; effective_at timestamptz?; integrity_status text; interpretation_status text.

**Integrity:** FK source; unique (source_id, content_hash, retrieved_at); unknown dates remain null, not invented. **Query indexes:** (source_id, retrieved_at DESC).

**Owner:** Policy monitor. **Lifecycle:** Immutable observations.

### `control.policy_bundle_sources` [G]

**Domain fields:** policy_release_id uuid; source_snapshot_id uuid; interpretation_hash hash; reviewer_user_id uuid; reviewed_at timestamptz.

**Integrity:** FK policy release, source snapshot, and user; unique (policy_release_id, source_snapshot_id). **Query indexes:** (source_snapshot_id).

**Owner:** Policy review. **Lifecycle:** Immutable release provenance.

### `control.resource_write_claims` [G]

**Domain fields:** provider text; resource_namespace text; resource_fingerprint text; tenant_id uuid; site_id uuid; claim_epoch bigint; active bool.

**Integrity:** FK (tenant_id, site_id) to sites; partial unique active claim on provider/namespace/resource fingerprints. A caller receives only a generic conflict, never another tenant identity. **Query indexes:** Namespace is the provider canonical resource namespace, never the OAuth installation or credential ID; Unique active external resource; (tenant_id, site_id).

**Owner:** Connector authority. **Lifecycle:** Mutable claim protected by transaction and audit; retain release history separately.

### `control.platform_events` [G]

**Domain fields:** event_type text; actor_user_id uuid?; object_kind text; object_id uuid; facts jsonb; reason text.

**Integrity:** Typed object reference validated against approved kinds; no tenant secrets in global facts. **Query indexes:** (object_kind, object_id, created_at).

**Owner:** Platform authority. **Lifecycle:** Append-only operator audit, including resource-claim history and release actions.

### `control.execution_epoch` [G]

**Domain fields:** environment_key text; epoch bigint; external_generation_ref text; changed_at timestamptz.

**Integrity:** Unique environment_key; epoch increases only. External recovery generation must survive restoration of this database. **Query indexes:** Unique environment_key.

**Owner:** Recovery authority. **Lifecycle:** Mutable local mirror; never the sole authority for post-restore fencing.

### `control.identity_sessions` [G]

**Domain fields:** user_id uuid; token_hash hash; auth_time timestamptz; authentication_level text; expires_at timestamptz; revoked_at timestamptz?.

**Integrity:** FK user; unique token_hash; this pre-tenant session permits only account onboarding and authorized tenant selection. It does not authorize tenant data access. **Query indexes:** (user_id, expires_at).

**Owner:** Identity. **Lifecycle:** Short-lived server-side identity context; global logout/disable revokes derived tenant sessions.

### `control.tenant_directory` [G]

**Domain fields:** tenant_id uuid; lifecycle text; provisioning_generation bigint.

**Integrity:** Unique tenant_id; FK tenant root; minimal operator-owned scheduling directory, without business content. Bootstrap and lifecycle changes update it transactionally with tenant records. **Query indexes:** (lifecycle, tenant_id).

**Owner:** Tenant bootstrap/scheduler. **Lifecycle:** Current directory for authorized worker tenant enumeration; not exposed as a global customer list.

### `control.origin_buckets` [G]

**Domain fields:** origin text; profile_version int; request_tokens bigint; in_flight_count int; last_refill_at timestamptz; next_allowed_at timestamptz; degraded_until timestamptz?.

**Integrity:** Unique origin/profile version; nonnegative counters; canonical scheme/host/port key; global admission obeys stricter applicable per-request policy and provider backoff. **Query indexes:** (next_allowed_at).

**Owner:** Crawler admission. **Lifecycle:** Mutable counters; separate bounded request leases reconcile failed workers. No tenant business data in bucket.

### `control.admission_leases` [G]

**Domain fields:** bucket_id uuid; workload_id text; permit_kind text; issued_at timestamptz; expires_at timestamptz; released_at timestamptz?.

**Integrity:** FK origin bucket; unique workload/permit_kind; bounded network timeout and worker cancellation reconcile expired leases. Permits for crawling are not production-write permits. **Query indexes:** (bucket_id, expires_at).

**Owner:** Crawler admission. **Lifecycle:** Short-lived accounting and failure recovery; retained aggregate metrics contain no private URL.

## A.3 Organizations, sites, account access, and settings

### `app.tenants` [T-root]

**Domain fields:** tenant_id uuid; name text; lifecycle enum(active,suspended,deleting,deleted); home_region text; settings_schema_version int.

**Integrity:** Special root: tenant_id is PK; do not add a redundant id. All tenant FKs refer to tenant_id. **Query indexes:** (lifecycle, created_at).

**Owner:** Account service. **Lifecycle:** Mutable; deletion is orchestrated, not cascading.

### `app.memberships` [T]

**Domain fields:** user_id uuid; role_key text; state enum(invited,active,suspended,removed); authorization_epoch bigint.

**Integrity:** FK global user; unique (tenant_id, user_id); last active owner rule is transactional. **Query indexes:** (tenant_id, state, role_key).

**Owner:** Account authority. **Lifecycle:** Mutable current state plus immutable audit events; removed identity retained while referenced.

### `app.invitations` [T]

**Domain fields:** email_normalized text; role_key text; token_hash hash; inviter_user_id uuid; expires_at timestamptz; consumed_at timestamptz?; revoked_at timestamptz?.

**Integrity:** FK inviter membership; unique token_hash; conditional single consumption. Never save the bearer invitation token. **Query indexes:** (tenant_id, email_normalized, expires_at).

**Owner:** Account authority. **Lifecycle:** Expire unused records; retain minimal audit.

### `app.sessions` [T]

**Domain fields:** identity_session_id uuid; user_id uuid; session_token_hash hash; oidc_session_ref text?; auth_time timestamptz; mfa_level text; expires_at timestamptz; revoked_at timestamptz?; last_seen_at timestamptz.

**Integrity:** FK membership and global identity session for the same verified user; unique session_token_hash; one session is bound to one selected tenant. Tenant switching issues a new scoped session. **Query indexes:** (tenant_id, user_id, expires_at).

**Owner:** Identity. **Lifecycle:** Mutable expiry/revocation; short retention after expiry; never store raw bearer cookies.

### `app.sites` [T]

**Domain fields:** name text; primary_origin text; timezone text; reporting_currency text; state enum(onboarding,active,archived); ownership_status text; active_profile_revision_id uuid?.

**Integrity:** Unique (tenant_id, id); exact normalized origin plus verified-origin records determine authority, not display name. Active profile FK uses (tenant_id, site id, profile revision id); pause authority lives only in site_control. **Query indexes:** (tenant_id, state, created_at).

**Owner:** Site service. **Lifecycle:** Mutable settings; archive before final retention.

### `app.site_memberships` [S]

**Domain fields:** user_id uuid; permission_set jsonb; authorization_epoch bigint; state text.

**Integrity:** FK membership and site; unique (tenant_id, site_id, user_id). Typed grants cannot exceed organization authority. **Query indexes:** (tenant_id, user_id, state).

**Owner:** Account authority. **Lifecycle:** Mutable grants plus audit; revocation invalidates action-time authority.

### `app.site_origins` [S]

**Domain fields:** origin text; purpose enum(primary,redirect,asset,preview); proof_method text; proof_hash hash?; verified_at timestamptz?; expires_at timestamptz?; state text.

**Integrity:** Unique (tenant_id, site_id, origin, purpose); redirects/assets do not inherit production-write authority. **Query indexes:** (tenant_id, site_id, state).

**Owner:** Site authority. **Lifecycle:** Versioned verification lifecycle; expired proof rechecks are scoped by risk.

### `app.site_control` [S]

**Domain fields:** paused bool; pause_epoch bigint; authorization_epoch bigint; connector_epoch bigint; emergency_state text; reason text?.

**Integrity:** Unique (tenant_id, site_id); epochs monotonic; mutable authoritative gate state. **Query indexes:** Unique tenant/site.

**Owner:** Execution authority. **Lifecycle:** Keep current row and audit; every outbound dispatch rechecks.

### `app.site_profile_revisions` [S]

**Domain fields:** revision_number int; business_description text; goals jsonb; markets text[]; sector text; protected_scopes jsonb; editorial_policy jsonb; actor_user_id uuid.

**Integrity:** FK actor membership; unique (tenant_id, site_id, revision_number); approved facts distinguished from guesses. **Query indexes:** (tenant_id, site_id, revision_number DESC).

**Owner:** Site configuration. **Lifecycle:** Immutable profile revisions; active profile pointer on sites references the exact current configuration revision.

### `app.tenant_entitlements` [T]

**Domain fields:** entitlement_key text; granted_value jsonb; valid_from timestamptz; valid_until timestamptz?; issuer_user_id uuid; reason text.

**Integrity:** FK issuer where tenant-scoped; platform entitlement issuance goes through audited operator command. Nonoverlapping effective versions enforced by transaction. **Query indexes:** (tenant_id, entitlement_key, valid_from).

**Owner:** Platform/account authority. **Lifecycle:** Append-only grants with supersession/revocation events; not a payment-card store.

### `app.notification_preferences` [T]

**Domain fields:** user_id uuid; event_category text; channel text; enabled bool; quiet_hours jsonb?.

**Integrity:** FK membership; unique (tenant_id, user_id, event_category, channel); critical security policy can impose required notifications. **Query indexes:** (tenant_id, user_id).

**Owner:** Account settings. **Lifecycle:** Mutable and audited.

## A.4 Integrations, protected resources, and webhooks

### `app.integrations` [T]

**Domain fields:** provider text; external_account_id text; credential_reference text; credential_generation bigint; connector_release_id uuid; state text; granted_scopes text[]; expires_at timestamptz?.

**Integrity:** FK connector release; credential_reference points into the secrets service. Unique active account semantics are provider-specific, not email-based. **Query indexes:** (tenant_id, provider, state).

**Owner:** Connector control. **Lifecycle:** Mutable lifecycle; revocation advances generation before cleanup.

### `app.oauth_attempts` [T]

**Domain fields:** integration_id uuid?; actor_user_id uuid; state_hash hash; pkce_secret_reference text; redirect_uri text; expires_at timestamptz; consumed_at timestamptz?.

**Integrity:** FK integration when present and membership; unique state_hash; exact redirect allowlist; one-time consumption. **Query indexes:** (tenant_id, expires_at).

**Owner:** Connector auth. **Lifecycle:** Short-lived; delete PKCE secret on completion/expiry; never log codes.

### `app.integration_bindings` [S]

**Domain fields:** integration_id uuid; external_resource_id text; resource_type text; purpose text; binding_epoch bigint; state text.

**Integrity:** FK tenant integration; unique (tenant_id, site_id, integration_id, external_resource_id, purpose); validate resource ownership and global write claim. **Query indexes:** (tenant_id, integration_id, state).

**Owner:** Connector control. **Lifecycle:** Mutable assignment and generation with audit; never silently rebind an approved change.

### `app.capability_snapshots` [S]

**Domain fields:** binding_id uuid; connector_release_id uuid; observed_at timestamptz; provider_version text; capability_class text; capabilities jsonb; certification_ref text; expires_at timestamptz.

**Integrity:** FK same-site binding and global connector release; immutable typed observations; expiry required for write certification. **Query indexes:** (tenant_id, site_id, binding_id, observed_at DESC).

**Owner:** Connector probe. **Lifecycle:** Immutable; invalidation is separate state/event.

### `app.resource_inventory` [S]

**Domain fields:** binding_id uuid; external_resource_id text; kind text; locale text; stage enum(draft,live,shared); observed_version text; observed_at timestamptz; state text.

**Integrity:** FK same-site binding; unique (tenant_id, site_id, binding_id, external_resource_id, locale, stage). **Query indexes:** (tenant_id, site_id, kind, state).

**Owner:** Inventory ingest. **Lifecycle:** Mutable latest observation; history remains in snapshots and operations.

### `app.resource_url_bindings` [S]

**Domain fields:** resource_id uuid; url_id uuid; relation text; valid_from timestamptz; valid_until timestamptz?.

**Integrity:** FK same-site resource_inventory and urls; temporal overlap checked for exclusive relationships; URLs are not resource IDs. **Query indexes:** (tenant_id, site_id, resource_id, valid_until).

**Owner:** Inventory ingest. **Lifecycle:** Append validity intervals; retain historical mapping for interpretation.

### `app.webhook_inbox` [T]

**Domain fields:** integration_id uuid; provider_event_id text; event_type text; verified_at timestamptz; body_hash hash; body_secret_reference text?; process_state text; available_at timestamptz; attempt_count int.

**Integrity:** FK integration; unique (tenant_id, integration_id, provider_event_id); signed duplicate with different body hash is a security event. **Query indexes:** (process_state, available_at) restricted worker access; (tenant_id, integration_id, created_at).

**Owner:** Webhook ingress. **Lifecycle:** Durable dedup record; minimize encrypted raw body retention.

### `app.chat_bindings` [S]

**Domain fields:** channel text; integration_id uuid; external_user_id text; external_chat_id text; user_id uuid; binding_epoch bigint; state text; paired_at timestamptz.

**Integrity:** FK tenant integration and membership; unique active channel/user/chat binding within authorized context. Usernames are display only. **Query indexes:** (tenant_id, site_id, user_id, state).

**Owner:** Chat identity. **Lifecycle:** Mutable binding; unlinking invalidates outstanding callbacks.

### `app.chat_pairing_attempts` [S]

**Domain fields:** actor_user_id uuid; token_hash hash; requested_channel text; expires_at timestamptz; consumed_at timestamptz?.

**Integrity:** FK membership; unique token_hash; claim consumes token and creates binding in one transaction. **Query indexes:** (tenant_id, site_id, expires_at).

**Owner:** Chat identity. **Lifecycle:** Short-lived; no persistent raw pairing token.

### `app.chat_action_tokens` [S]

**Domain fields:** binding_id uuid; revision_id uuid; manifest_hash hash; action text; token_hash hash; expires_at timestamptz; consumed_at timestamptz?.

**Integrity:** FK same-site binding and exact change revision/hash; unique token_hash; no user-editable scope embedded as authority. **Query indexes:** (tenant_id, site_id, expires_at).

**Owner:** Chat authority. **Lifecycle:** One-time action record; approval remains in its own authority table.

## A.5 Commands, workflows, scheduling, and projections

### `app.commands` [T]

**Domain fields:** actor_user_id uuid?; actor_service text?; site_id uuid?; kind text; schema_version int; principal_key text; route_key text; scope_kind enum(tenant,site); idempotency_key text; request_fingerprint hash; payload jsonb; accepted_at timestamptz; status text; result_reference jsonb?.

**Integrity:** Exactly one actor type; tenant/member/site constraints as applicable. Unique (tenant_id, principal_key, route_key, idempotency_key); principal_key and route_key are explicit persisted text columns. Scope_kind enum(tenant,site) enforces site_id presence. **Query indexes:** (tenant_id, accepted_at DESC); (tenant_id, site_id, status).

**Owner:** Command service. **Lifecycle:** Intent immutable; progress/result projection mutable. Sensitive payload values redacted or secret references.

### `app.command_events` [T]

**Domain fields:** command_id uuid; event_number bigint; event_type text; facts jsonb.

**Integrity:** FK command; unique (tenant_id, command_id, event_number); events must not change original scope. **Query indexes:** (tenant_id, command_id, event_number).

**Owner:** Command service. **Lifecycle:** Append-only.

### `app.outbox` [T]

**Domain fields:** event_id uuid; aggregate_kind text; aggregate_id uuid; event_type text; schema_version int; payload jsonb; available_at timestamptz; lease_owner text?; lease_until timestamptz?; delivered_at timestamptz?; attempt_count int.

**Integrity:** Unique (tenant_id, event_id); typed aggregate references validated at insertion in same transaction. Publication is at-least-once. **Query indexes:** Partial pending (available_at, id) for authorized dispatcher.

**Owner:** Domain services. **Lifecycle:** Payload immutable; delivery bookkeeping mutable; dedup horizon exceeds retained retry/replay horizon.

### `app.consumer_inbox` [T]

**Domain fields:** consumer_key text; event_id uuid; processed_at timestamptz; result_hash hash?.

**Integrity:** Unique (tenant_id, consumer_key, event_id); processing side effect and inbox insert share transaction where possible. **Query indexes:** Unique dedup key.

**Owner:** Each event consumer. **Lifecycle:** Retain for maximum replay window; separate cleanup release review.

### `app.workflow_refs` [S]

**Domain fields:** command_id uuid; workflow_id text; first_run_id text; workflow_type text; state_projection text; projected_event_sequence bigint; projected_at timestamptz.

**Integrity:** FK tenant command with matching site; unique (tenant_id, workflow_id); Temporal history remains workflow authority. **Query indexes:** (tenant_id, site_id, state_projection, projected_at).

**Owner:** Workflow projector. **Lifecycle:** Rebuildable projection/reference; never stores an alternative editable workflow state machine.

### `app.schedules` [S]

**Domain fields:** schedule_key text; workflow_type text; expression text; timezone text; overlap_policy text; catchup_policy text; enabled bool; configuration jsonb.

**Integrity:** Unique (tenant_id, site_id, schedule_key); DST/overlap/catch-up semantics explicit. **Query indexes:** (tenant_id, site_id, enabled).

**Owner:** Workflow configuration. **Lifecycle:** Versioned changes; Temporal schedule reconciler applies intended configuration.

### `app.work_items` [S]

**Domain fields:** kind text; finding_id uuid?; plan_revision_id uuid?; priority int; status text; owner_kind text; not_before timestamptz?; deadline timestamptz?; workflow_ref_id uuid?.

**Integrity:** FK same-site finding/plan/workflow; stable task identity across retry. **Query indexes:** (tenant_id, site_id, status, priority, not_before, id).

**Owner:** Planning/workflow. **Lifecycle:** Mutable backlog with event history.

### `app.work_dependencies` [S]

**Domain fields:** predecessor_id uuid; successor_id uuid; condition text.

**Integrity:** Same-site work_item FKs; unique pair/condition; no self-edge; cycle check at transactionally sealed plan revision. **Query indexes:** (tenant_id, site_id, successor_id).

**Owner:** Planning. **Lifecycle:** Immutable within plan version; do not mutate running DAG silently.

## A.6 Crawling, artifacts, and evidence

### `app.artifacts` [S]

**Domain fields:** object_key text; object_version text; sha256 hash; byte_length bigint; media_type text; encryption_key_ref text; durability_state text; retain_until timestamptz; legal_hold bool.

**Integrity:** Unique (tenant_id, site_id, object_key, object_version); byte_length >= 0; immutable object identity and hash. Durability attestations are separate append-only records. **Query indexes:** (tenant_id, site_id, retain_until).

**Owner:** Artifact service. **Lifecycle:** Immutable content; extend retention without shortening active recovery obligations.

### `app.artifact_attestations` [S]

**Domain fields:** artifact_id uuid; replica_location text; verified_hash hash; verified_at timestamptz; check_type text; result text.

**Integrity:** Same-site artifact FK; attested hash must equal artifact hash for successful integrity assertion. **Query indexes:** (tenant_id, site_id, artifact_id, verified_at DESC).

**Owner:** Artifact integrity. **Lifecycle:** Append-only durability/integrity observations.

### `app.urls` [S]

**Domain fields:** original_url text; fetch_url text; normalized_key text; origin text; normalization_version int; discovered_at timestamptz.

**Integrity:** Unique (tenant_id, site_id, normalization_version, normalized_key); preserve query semantics; key length bounded. **Query indexes:** (tenant_id, site_id, origin).

**Owner:** Crawler inventory. **Lifecycle:** Stable URL identity for a normalization cohort; rekey via migration, never silent rewrite.

### `app.crawl_runs` [S]

**Domain fields:** trigger_command_id uuid; scope_snapshot jsonb; limits_snapshot jsonb; fetch_profile_hash hash; started_at timestamptz?; ended_at timestamptz?; status text; coverage_summary jsonb.

**Integrity:** FK command scoped to site; immutable scope/config once started; explicit partial/blocked coverage. **Query indexes:** (tenant_id, site_id, started_at DESC).

**Owner:** Crawler workflow. **Lifecycle:** Mutable run progress with immutable final summary.

### `app.crawl_frontier` [S]

**Domain fields:** crawl_run_id uuid; url_id uuid; depth int; discovery_reason text; status text; next_attempt_at timestamptz; attempt_count int; lease_owner text?; lease_until timestamptz?.

**Integrity:** Same-site run and URL FKs; unique (tenant_id, site_id, crawl_run_id, url_id); depth/attempts >= 0. **Query indexes:** (tenant_id, site_id, crawl_run_id, status, next_attempt_at, id).

**Owner:** Crawler scheduler. **Lifecycle:** Mutable queue; bounded cleanup after finalized run and retained coverage.

### `app.robots_snapshots` [S]

**Domain fields:** origin text; user_agent_profile text; artifact_id uuid?; fetched_at timestamptz; expires_at timestamptz; http_status int?; decision_status text; rules_hash hash?.

**Integrity:** Same-site artifact when present; network failure has no invented HTTP status; parsed rules tied to exact user-agent profile. **Query indexes:** (tenant_id, site_id, origin, expires_at DESC).

**Owner:** Crawler. **Lifecycle:** Immutable observations; latest valid lookup is a projection.

### `app.fetch_observations` [S]

**Domain fields:** crawl_run_id uuid; url_id uuid; fetch_attempt_id uuid; started_at timestamptz; finished_at timestamptz; outcome text; http_status int?; final_url text?; response_headers jsonb; raw_artifact_id uuid?; decoded_bytes bigint; network_profile_hash hash.

**Integrity:** Same-site run/URL/artifact; unique fetch_attempt_id within tenant; secrets/cookies stripped from stored headers. **Query indexes:** (tenant_id, site_id, url_id, started_at DESC); (crawl_run_id, outcome).

**Owner:** Crawler ingest. **Lifecycle:** Append-only; partition later only with correct key strategy.

### `app.render_observations` [S]

**Domain fields:** fetch_observation_id uuid; browser_profile_hash hash; dom_artifact_id uuid?; screenshot_artifact_id uuid?; readiness_status text; request_summary jsonb; measured_at timestamptz.

**Integrity:** Same-site fetch/artifacts; rendered DOM never overwrites raw response. **Query indexes:** (tenant_id, site_id, fetch_observation_id).

**Owner:** Browser ingest. **Lifecycle:** Append-only; short raw/screenshot retention unless attached to active evidence.

### `app.page_facts` [S]

**Domain fields:** fetch_observation_id uuid; parser_release_id uuid; fact_schema_version int; facts jsonb; extraction_status text.

**Integrity:** Same-site fetch; parser application release FK; unique observation/parser/schema version. Validate canonical, robots, headings, schema, language, and parsing confidence separately. **Query indexes:** (tenant_id, site_id, fetch_observation_id).

**Owner:** Deterministic extraction. **Lifecycle:** Immutable derived facts; recompute as a new version.

### `app.link_edges` [S]

**Domain fields:** crawl_run_id uuid; source_url_id uuid; destination_url_id uuid?; destination_text text; node_locator text; rel_tokens text[]; anchor_text text; edge_kind text.

**Integrity:** Same-site run/source/destination when in scope; out-of-scope destinations stored as bounded text without granting fetch authority. **Query indexes:** (tenant_id, site_id, crawl_run_id, source_url_id); (tenant_id, site_id, destination_url_id).

**Owner:** Graph ingest. **Lifecycle:** Observation-level graph, not timeless truth; preserve run coverage.

### `app.evidence_records` [S]

**Domain fields:** evidence_type text; source_kind text; source_identifier text; artifact_id uuid?; observed_at timestamptz; valid_until timestamptz?; quality jsonb; rights_status text; content_hash hash.

**Integrity:** Same-site artifact; typed source reference validated by evidence schema, not arbitrary executable URL. **Query indexes:** (tenant_id, site_id, evidence_type, observed_at DESC).

**Owner:** Evidence service. **Lifecycle:** Immutable; superseding observation is a new row.

### `app.findings` [S]

**Domain fields:** finding_key text; detector_release_id uuid; resource_id uuid?; url_id uuid?; severity text; status text; first_seen_at timestamptz; last_seen_at timestamptz; confidence_class text.

**Integrity:** Same-site target FKs; unique current detector/site/finding key; confidence_class is not an invented calibrated probability. **Query indexes:** (tenant_id, site_id, status, severity, last_seen_at).

**Owner:** Audit engine. **Lifecycle:** Mutable incident lifecycle; supporting observations retained independently.

### `app.finding_evidence` [S]

**Domain fields:** finding_id uuid; evidence_id uuid; relation text.

**Integrity:** Same-site FKs; unique (tenant_id, site_id, finding_id, evidence_id, relation). **Query indexes:** (tenant_id, site_id, evidence_id).

**Owner:** Audit engine. **Lifecycle:** Append-only links; retraction is an explicit event.

## A.7 Analytics, performance, research, and memory

### `app.analytics_scopes` [S]

**Domain fields:** binding_id uuid; provider text; property_id text; dimensions jsonb; metrics jsonb; filters jsonb; source_timezone text; currency text?; scope_hash hash.

**Integrity:** Same-site binding; unique (tenant_id, site_id, scope_hash); scope hash includes query grain and source property. **Query indexes:** (tenant_id, site_id, provider).

**Owner:** Analytics control. **Lifecycle:** Immutable query specification.

### `app.analytics_imports` [S]

**Domain fields:** scope_id uuid; generation_number bigint; interval_start date; interval_end date; cursor jsonb?; status text; completeness jsonb; received_at timestamptz; activated_at timestamptz?.

**Integrity:** Same-site scope; unique scope/generation; end >= start. Import interval inclusive/exclusive convention is part of scope schema. **Query indexes:** (tenant_id, site_id, scope_id, generation_number DESC).

**Owner:** Analytics ingest. **Lifecycle:** Immutable result generation after validation; progress mutable before completion.

### `app.analytics_active_generations` [S]

**Domain fields:** scope_id uuid; interval_start date; interval_end date; import_id uuid; activated_at timestamptz.

**Integrity:** Same-site scope/import with matching interval; unique exact scope/interval; overlapping active scopes must be partitioned or rejected, never double-counted. **Query indexes:** Unique scope/interval.

**Owner:** Analytics authority. **Lifecycle:** Atomic pointer switch; reads pin generation IDs for a whole report.

### `app.metric_series` [S]

**Domain fields:** scope_id uuid; dimension_values jsonb; dimension_hash hash; unit_profile jsonb.

**Integrity:** Same-site scope; unique (tenant_id, site_id, scope_id, dimension_hash); values canonicalized with source-aware null semantics. **Query indexes:** (tenant_id, site_id, scope_id).

**Owner:** Analytics ingest. **Lifecycle:** Immutable series identity.

### `app.metric_points` [S]

**Domain fields:** import_id uuid; series_id uuid; bucket_date date; values jsonb; quality_flags text[].

**Integrity:** Same-site import/series with matching scope; unique (tenant_id, site_id, import_id, series_id, bucket_date); integer counts and decimal strings for nonintegral provider quantities, never float money. **Query indexes:** (tenant_id, site_id, series_id, bucket_date); (import_id).

**Owner:** Analytics ingest. **Lifecycle:** Immutable imported values; source revisions create new generation.

### `app.performance_runs` [S]

**Domain fields:** url_id uuid; run_kind enum(lab,field); provider text; profile_hash hash; observed_at timestamptz; window_start date?; window_end date?; granularity enum(page,origin); artifact_id uuid; quality jsonb.

**Integrity:** Same-site URL/artifact; field windows and page/origin granularity cannot be silently substituted; local lab runs record repeats and environment. **Query indexes:** (tenant_id, site_id, url_id, observed_at DESC).

**Owner:** Performance service. **Lifecycle:** Immutable observations.

### `app.research_runs` [S]

**Domain fields:** objective text; query_scope jsonb; provider_binding_id uuid?; source_rights_profile jsonb; budget_snapshot jsonb; status text; completed_at timestamptz?.

**Integrity:** Same-site optional binding; paid source rights and scope recorded before query. **Query indexes:** (tenant_id, site_id, created_at DESC).

**Owner:** Research workflow. **Lifecycle:** Mutable progress; sealed sourced results.

### `app.competitor_observations` [S]

**Domain fields:** research_run_id uuid; domain text; competitor_kind enum(business,search); query_text text?; locale text; device text; observed_at timestamptz; evidence_id uuid; estimated bool.

**Integrity:** Same-site research/evidence; provider estimates must remain flagged; public data never represented as private analytics. **Query indexes:** (tenant_id, site_id, domain, observed_at DESC).

**Owner:** Research ingest. **Lifecycle:** Immutable observations.

### `app.opportunities` [S]

**Domain fields:** finding_id uuid?; research_run_id uuid?; hypothesis text; score_components jsonb; metric_target jsonb; uncertainty text; status text.

**Integrity:** Same-site optional sources; score components have explicit versioned heuristic; hard policy gates precede ranking. **Query indexes:** (tenant_id, site_id, status, created_at DESC).

**Owner:** Planning. **Lifecycle:** Mutable prioritization with evidence-backed revisions.

### `app.plan_revisions` [S]

**Domain fields:** plan_key text; revision_number int; goals_snapshot jsonb; opportunity_manifest jsonb; dependencies jsonb; allocation jsonb; artifact_id uuid; actor_user_id uuid?.

**Integrity:** Same-site artifact; IDs inside JSON/arrays validated and normalized into plan_items below; unique plan_key/revision. **Query indexes:** (tenant_id, site_id, plan_key, revision_number DESC).

**Owner:** Planning. **Lifecycle:** Immutable sealed plan; use relational plan_items for authoritative membership.

### `app.plan_items` [S]

**Domain fields:** plan_revision_id uuid; opportunity_id uuid; sequence_number int; intended_recipe_release_id uuid.

**Integrity:** Same-site plan/opportunity and global recipe release; unique plan/opportunity and plan/sequence. **Query indexes:** (tenant_id, site_id, plan_revision_id, sequence_number).

**Owner:** Planning. **Lifecycle:** Immutable plan membership.

### `app.knowledge_records` [S]

**Domain fields:** kind enum(approved_fact,source_fact,hypothesis,decision); title text; content_artifact_id uuid; evidence_id uuid?; authority_level text; valid_from timestamptz; valid_until timestamptz?; supersedes_id uuid?; approval_command_id uuid?.

**Integrity:** Same-site artifact/evidence/superseded knowledge; user approval command required for approved_fact promotion. No permissions or execution approvals stored here. **Query indexes:** (tenant_id, site_id, kind, valid_until).

**Owner:** Memory service. **Lifecycle:** Immutable versioned knowledge; contradictions retained for review.

### `app.knowledge_chunks` [S]

**Domain fields:** knowledge_id uuid; chunk_number int; text_content text; text_hash hash; search_document tsvector; sensitivity_class text.

**Integrity:** Same-site knowledge; unique (tenant_id, site_id, knowledge_id, chunk_number); retrieval respects sensitivity and source validity. **Query indexes:** GIN search_document with tenant/site filters.

**Owner:** Memory ingest. **Lifecycle:** Immutable chunks per knowledge version.

### `app.embedding_cohorts` [T]

**Domain fields:** provider text; model_id text; dimensions int; preprocessing_version text; state text; created_from_release_id uuid.

**Integrity:** Global release FK; unique provider/model/dimensions/preprocessing; dimensions > 0. **Query indexes:** (tenant_id, state).

**Owner:** Memory configuration. **Lifecycle:** Immutable identity; lifecycle via audited state changes.

### `app.chunk_embeddings` [S]

**Domain fields:** chunk_id uuid; cohort_id uuid; embedding vector(D); embedded_at timestamptz.

**Integrity:** Same-site chunk and tenant cohort; unique chunk/cohort. D is fixed by physical cohort table/index; do not put mixed dimensions into one HNSW index. **Query indexes:** Tenant/site filtered exact search initially; measured HNSW per compatible cohort.

**Owner:** Memory ingest. **Lifecycle:** Immutable vector; new cohort for model or dimension migration.

## A.8 Agent execution, conversations, and model cost evidence

### `app.conversations` [S]

**Domain fields:** channel text; external_thread_id text?; creator_user_id uuid; title text; state text.

**Integrity:** FK creator membership; unique active external-thread mapping within binding scope when applicable. **Query indexes:** (tenant_id, site_id, updated_at DESC).

**Owner:** Chat service. **Lifecycle:** Mutable container; messages retained under policy.

### `app.conversation_messages` [S]

**Domain fields:** conversation_id uuid; sequence_number bigint; role text; author_user_id uuid?; body_artifact_id uuid; command_id uuid?; workflow_ref_id uuid?; moderation_state text.

**Integrity:** Same-site conversation/artifact/workflow; FK scoped command; unique conversation/sequence; displayed actions link authoritative events. **Query indexes:** (tenant_id, site_id, conversation_id, sequence_number).

**Owner:** Chat service. **Lifecycle:** Immutable messages; redaction/deletion events change display without fabricating history.

### `app.agent_runs` [S]

**Domain fields:** workflow_ref_id uuid; role text; model_release_id uuid; prompt_hash hash; input_manifest_artifact_id uuid; status text; turn_limit int; tool_limit int; completed_at timestamptz?.

**Integrity:** Same-site workflow/artifact; model release FK; positive bounded limits fixed at start. **Query indexes:** (tenant_id, site_id, workflow_ref_id, created_at).

**Owner:** Agent runtime. **Lifecycle:** Mutable progress; inputs and version identities immutable.

### `app.model_calls` [S]

**Domain fields:** agent_run_id uuid; call_number int; request_id text?; provider_response_id text?; model_reported text?; input_tokens bigint?; output_tokens bigint?; cached_input_tokens bigint?; usage_status text; output_artifact_id uuid?; error_code text?.

**Integrity:** Same-site run/artifact; unique (tenant_id, site_id, agent_run_id, call_number); unknown usage remains unknown, not zero. **Query indexes:** (tenant_id, site_id, agent_run_id, call_number).

**Owner:** Model gateway. **Lifecycle:** Append request intent and separate completion events or reviewed mutable bookkeeping; cost settles through ledger.

### `app.tool_calls` [S]

**Domain fields:** agent_run_id uuid; model_call_id uuid; tool_call_id text; tool_name text; tool_schema_hash hash; arguments_artifact_id uuid; result_artifact_id uuid?; authorization_result text; state text.

**Integrity:** Same-site run/call/artifacts; unique run/tool_call_id; rejected tools have no connector side effect. **Query indexes:** (tenant_id, site_id, agent_run_id, created_at).

**Owner:** Tool gateway. **Lifecycle:** Durable call lifecycle; provider production writes reference operations, never hidden here.

## A.9 Changes, policy decisions, approval, and execution

### `app.changes` [S]

**Domain fields:** origin_work_item_id uuid?; purpose text; current_draft_version bigint; creator_kind text.

**Integrity:** Same-site work item; stable change identity, not an approved payload. **Query indexes:** (tenant_id, site_id, created_at DESC).

**Owner:** Change preparation. **Lifecycle:** Mutable container; cannot edit sealed revisions through this row.

### `app.change_drafts` [S]

**Domain fields:** change_id uuid; draft_number bigint; payload jsonb; artifact_ids uuid[]; validation_summary jsonb.

**Integrity:** Same-site change; unique change/draft_number; referenced artifacts checked and normalized when sealing. **Query indexes:** (tenant_id, site_id, change_id, draft_number DESC).

**Owner:** Implementer. **Lifecycle:** Mutable until sealed; draft edits use expected row_version.

### `app.change_revisions` [S]

**Domain fields:** change_id uuid; revision_number bigint; recipe_release_id uuid; manifest_hash hash; manifest_artifact_id uuid; approval_class text; expires_at timestamptz; sealed_at timestamptz.

**Integrity:** Same-site change/artifact; global recipe release; unique change/revision_number; unique (tenant_id, site_id, id, manifest_hash) supports exact-approval FK. **Query indexes:** (tenant_id, site_id, change_id, revision_number DESC).

**Owner:** Change sealer. **Lifecycle:** Immutable canonical manifest reference and authority class.

### `app.change_items` [S]

**Domain fields:** revision_id uuid; item_number int; binding_id uuid; resource_id uuid?; creation_key text?; expected_existence enum(present,absent); expected_version text?; patch_artifact_id uuid; before_artifact_id uuid; recovery_recipe jsonb; effect_scope jsonb.

**Integrity:** Same-site revision/binding/resource/artifacts; existing resource must belong to referenced binding; exactly one resource_id/creation_key; present requires version, absent requires certified conditional creation and no invented version; unique revision/item_number; expose unique revision/id key for operation FK. **Query indexes:** (tenant_id, site_id, revision_id, item_number).

**Owner:** Change sealer. **Lifecycle:** Immutable concrete effects; creation operations use an explicit allocated resource identity/creation contract, never an invented existing version.

### `app.change_item_dependencies` [S]

**Domain fields:** revision_id uuid; predecessor_item_id uuid; successor_item_id uuid; required_result text.

**Integrity:** All same-site and same-revision FKs; no self-link; cycle-free DAG checked on sealing. **Query indexes:** (tenant_id, site_id, revision_id, successor_item_id).

**Owner:** Change sealer. **Lifecycle:** Immutable sealed dependencies.

### `app.change_progress` [S]

**Domain fields:** revision_id uuid; lifecycle_state text; phase_reason text?; event_sequence bigint; projected_at timestamptz.

**Integrity:** Unique same-site revision; schema enum from section 6; projection may lag authority. **Query indexes:** (tenant_id, site_id, lifecycle_state, projected_at).

**Owner:** Projector. **Lifecycle:** Mutable and rebuildable; cannot authorize execution.

### `app.policy_decisions` [S]

**Domain fields:** revision_id uuid; manifest_hash hash; policy_release_id uuid; input_artifact_id uuid; result text; reason_codes text[]; decided_at timestamptz; expires_at timestamptz; source_freshness jsonb.

**Integrity:** FK exact revision/hash, same-site input artifact, and policy release; append-only. **Query indexes:** (tenant_id, site_id, revision_id, decided_at DESC).

**Owner:** Authorizer/policy. **Lifecycle:** Immutable; a new policy evaluation produces a new decision.

### `app.approval_grants` [S]

**Domain fields:** revision_id uuid; manifest_hash hash; actor_user_id uuid; actor_membership_epoch bigint; site_authorization_epoch bigint; policy_decision_id uuid; granted_at timestamptz; expires_at timestamptz; authentication_level text; binding_id uuid?.

**Integrity:** FK exact revision/hash, membership, same-site policy decision and optional chat binding; decision must concern that revision. Distinct-person requirements evaluated against real actor identity. **Query indexes:** (tenant_id, site_id, revision_id, expires_at).

**Owner:** Approval authority. **Lifecycle:** Immutable grant; no mutable approved=true flag.

### `app.approval_revocations` [S]

**Domain fields:** approval_id uuid; revoked_by_user_id uuid?; revoking_service text?; reason text; revoked_at timestamptz.

**Integrity:** Same-site grant and membership where applicable; exactly one revoking actor type; unique approval ID for first effective revocation, later explanations audit events. **Query indexes:** Unique tenant/site/approval.

**Owner:** Approval authority. **Lifecycle:** Append-only and durable across recovery.

### `app.standing_authorizations` [S]

**Domain fields:** actor_user_id uuid; policy_revision_number bigint; recipe_release_ids uuid[]; binding_ids uuid[]; allowed_scopes jsonb; aggregate_limits jsonb; valid_from timestamptz; expires_at timestamptz; revoked_at timestamptz?.

**Integrity:** Membership and same-site binding references normalized into validated grant scope rows when used; cannot authorize A5 operations; recipe ranges cannot silently include future versions. **Query indexes:** (tenant_id, site_id, expires_at).

**Owner:** Approval authority. **Lifecycle:** Immutable scope/revision; revocation event plus current projection.

### `app.standing_authorization_recipes` [S]

**Domain fields:** standing_authorization_id uuid; recipe_release_id uuid.

**Integrity:** Same-site grant FK and global recipe release; unique grant/release; only explicit reviewed releases are authorized. **Query indexes:** (tenant_id, site_id, standing_authorization_id).

**Owner:** Approval authority. **Lifecycle:** Immutable normalized authority scope.

### `app.standing_authorization_bindings` [S]

**Domain fields:** standing_authorization_id uuid; binding_id uuid; allowed_resource_types text[]; allowed_field_keys text[]; path_matcher_schema_version int; path_matcher jsonb.

**Integrity:** Same-site grant and binding; unique grant/binding; typed deterministic scope matcher with negative tests; no arbitrary regular-expression evaluation without limits. **Query indexes:** (tenant_id, site_id, standing_authorization_id).

**Owner:** Approval authority. **Lifecycle:** Immutable normalized binding and field scope.

### `app.standing_authorization_revocations` [S]

**Domain fields:** standing_authorization_id uuid; revoked_by_user_id uuid?; revoking_service text?; reason text; revoked_at timestamptz.

**Integrity:** Same-site grant and membership when applicable; exactly one actor; unique grant for first effective revocation. **Query indexes:** Unique tenant/site/grant.

**Owner:** Approval authority. **Lifecycle:** Append-only authority revocation; revoked_at in grant-facing views is only a projection.

### `app.execution_permits` [S]

**Domain fields:** operation_id uuid; manifest_hash hash; policy_decision_id uuid; authority_set_hash hash; execution_epoch bigint; pause_epoch bigint; authorization_epoch bigint; connector_epoch bigint; fence_token bigint; issued_at timestamptz; expires_at timestamptz; signature bytea.

**Integrity:** FK same-site operation and policy decision; authority set normalized into permit_authorities and validated. Permit single dispatch attempt binding, not an unrestricted token. Use permit_authorities below for actual FKs. **Query indexes:** (tenant_id, site_id, operation_id, issued_at DESC).

**Owner:** Execution authority. **Lifecycle:** Immutable short-lived authorization; gateway checks live revocations/epochs, not signature alone.

### `app.permit_authorities` [S]

**Domain fields:** permit_id uuid; approval_id uuid?; standing_authorization_id uuid?.

**Integrity:** Exactly one authority reference; same-site FKs; unique permit/authority; deny unless referenced scope covers exact operation. **Query indexes:** (tenant_id, site_id, permit_id).

**Owner:** Execution authority. **Lifecycle:** Immutable explicit authority links, potentially several approvers.

### `app.external_operations` [S]

**Domain fields:** revision_id uuid; item_id uuid; step_key text; idempotency_key text; provider_contract_hash hash; expected_existence enum(present,absent); expected_version text?; desired_hash hash; status text; planned_at timestamptz; finalized_at timestamptz?.

**Integrity:** FK same-revision item; unique (tenant_id, site_id, revision_id, item_id, step_key); stable idempotency_key reused across attempts; status enum from section 6. **Query indexes:** (tenant_id, site_id, status, planned_at); (tenant_id, site_id, idempotency_key).

**Owner:** Executor. **Lifecycle:** Immutable operation identity/intent; CAS-checked status bookkeeping plus append-only events.

### `app.operation_attempts` [S]

**Domain fields:** operation_id uuid; attempt_number int; permit_id uuid; request_hash hash; execution_epoch bigint; fence_token bigint; dispatched_at timestamptz?; transport_outcome text; provider_request_id text?.

**Integrity:** Same-site operation/permit and permit must bind this operation; unique operation/attempt_number; request retry cannot alter intended patch. **Query indexes:** (tenant_id, site_id, operation_id, attempt_number).

**Owner:** Executor. **Lifecycle:** Dispatch bookkeeping append-only events or tightly controlled completion update; no new operation on ambiguous retry.

### `app.operation_events` [S]

**Domain fields:** operation_id uuid; event_number bigint; previous_state text; next_state text; reason_code text; facts jsonb.

**Integrity:** Same-site operation; unique operation/event_number; state change and event append share transaction. **Query indexes:** (tenant_id, site_id, operation_id, event_number).

**Owner:** Executor. **Lifecycle:** Append-only status history.

### `app.provider_receipts` [S]

**Domain fields:** operation_id uuid; attempt_id uuid?; provider_operation_id text?; receipt_artifact_id uuid; observed_resource_version text?; effect_result text; received_at timestamptz.

**Integrity:** Same-site operation/attempt/artifact; attempt if supplied must belong to operation; body hash in immutable artifact. **Query indexes:** (tenant_id, site_id, operation_id, received_at).

**Owner:** Executor/reconciler. **Lifecycle:** Append-only; provider acknowledgement and verified application are different results.

### `app.operation_resource_results` [S]

**Domain fields:** operation_id uuid; binding_id uuid; resource_id uuid; provider_resource_id text; observed_version text; observed_at timestamptz.

**Integrity:** Same-site operation/binding/resource; resource must belong to binding; unique operation/resource. Resolves created resources without mutating their sealed creation intent. **Query indexes:** (tenant_id, site_id, resource_id, operation_id).

**Owner:** Executor/reconciler. **Lifecycle:** Immutable applied-resource mapping; unresolved operations quarantine both creation destination and any resolved provider identity.

### `app.resource_leases` [S]

**Domain fields:** binding_id uuid; resource_id uuid?; creation_key text?; resource_key text; holder_operation_id uuid; fence_token bigint; lease_until timestamptz; ambiguity_quarantine bool.

**Integrity:** Same-site resource/binding/operation; exactly one existing resource/creation key; unique active row per binding/resource_key, including normalized creation destinations; fence monotonic. Resource ownership must match binding. Quarantine survives lease expiry. **Query indexes:** Unique resource; (tenant_id, site_id, lease_until).

**Owner:** Execution authority. **Lifecycle:** Mutable coordinator record; upstream CAS still required.

### `app.safety_journal_acks` [S]

**Domain fields:** operation_id uuid; journal_record_id text; journal_generation text; journal_payload_hash hash; durable_ack_at timestamptz; location_ref text.

**Integrity:** Same-site operation; unique journal_record_id; ack must correspond to exact pre-dispatch intent and recovery artifact hashes. **Query indexes:** (tenant_id, site_id, operation_id).

**Owner:** Journal client. **Lifecycle:** Immutable local acknowledgement; authoritative external copy lives outside primary failure domain.

## A.10 Verification, delivery, recovery, and experiments

### `app.verification_runs` [S]

**Domain fields:** revision_id uuid; operation_id uuid?; plan_hash hash; verifier_release_id uuid; started_at timestamptz; completed_at timestamptz?; status enum(pending,running,propagation_pending,passed,failed,blocked,inconclusive).

**Integrity:** Same-site revision/operation; global application verifier release; operation must belong to revision. **Query indexes:** (tenant_id, site_id, revision_id, started_at DESC).

**Owner:** Independent verifier. **Lifecycle:** Mutable run progress; immutable result observations.

### `app.verification_checks` [S]

**Domain fields:** verification_run_id uuid; check_key text; expected_artifact_id uuid?; observed_artifact_id uuid?; result text; reason text; observed_at timestamptz.

**Integrity:** Same-site run/artifacts; unique run/check_key/observed_at; distinguish not-run, unavailable, pass, and fail. **Query indexes:** (tenant_id, site_id, verification_run_id, check_key).

**Owner:** Independent verifier. **Lifecycle:** Append-only evidence.

### `app.delivery_contracts` [S]

**Domain fields:** binding_id uuid; version_number int; source_repository_id text; source_branch text; target_environment text; artifact_identity_schema jsonb; event_verification jsonb; live_identity_probes jsonb; certification_artifact_id uuid.

**Integrity:** Same-site binding/artifact; unique binding/environment/version; contract explicitly covers CI and promotion authority. **Query indexes:** (tenant_id, site_id, binding_id, version_number DESC).

**Owner:** Delivery certification. **Lifecycle:** Immutable contract; deployment uses exact approved version.

### `app.deployments` [S]

**Domain fields:** delivery_contract_id uuid; revision_id uuid; provider_deployment_id text; source_commit text; candidate_tree_hash text; artifact_digest text; environment text; status text; observed_at timestamptz.

**Integrity:** Same-site contract/revision; unique contract/provider_deployment_id; Git commit length/algorithm validated by repository contract, not assumed universally SHA-1. **Query indexes:** (tenant_id, site_id, revision_id, observed_at DESC).

**Owner:** Delivery observer. **Lifecycle:** Append provider state events; record acceptance separately from live confirmation.

### `app.recovery_plans` [S]

**Domain fields:** original_revision_id uuid; recovery_revision_id uuid; mode text; base_artifact_ids uuid[]; current_version_snapshot jsonb; dependency_analysis jsonb; expires_at timestamptz.

**Integrity:** Same-site original/recovery revisions; same artifact rights/scope validation; recovery revision follows normal authorization/execution pipeline. **Query indexes:** (tenant_id, site_id, original_revision_id).

**Owner:** Recovery preparation. **Lifecycle:** Immutable sealed plan; referenced artifacts normalized into revision items.

### `app.recovery_conflicts` [S]

**Domain fields:** recovery_plan_id uuid; resource_id uuid; locator text; base_hash hash; signal_hash hash; current_hash hash; conflict_kind text; resolved_by_revision_id uuid?.

**Integrity:** Same-site plan/resource/revision; resolved status cannot mutate original evidence. **Query indexes:** (tenant_id, site_id, recovery_plan_id).

**Owner:** Recovery verifier. **Lifecycle:** Immutable conflict evidence plus resolution reference/event.

### `app.experiments` [S]

**Domain fields:** revision_id uuid; hypothesis text; primary_metric_scope_id uuid; assignment_artifact_id uuid; baseline_start date; baseline_end date; observation_start date; minimum_evidence jsonb; confounder_policy jsonb; status text.

**Integrity:** Same-site revision/analytics scope/artifact; assignment avoids bot/user content discrimination; pre-register outcome before analysis. **Query indexes:** (tenant_id, site_id, status, observation_start).

**Owner:** Measurement service. **Lifecycle:** Immutable plan after start; progress and amendments explicit.

### `app.measurement_results` [S]

**Domain fields:** experiment_id uuid; report_artifact_id uuid; generation_ids uuid[]; analyzed_at timestamptz; outcome text; uncertainty_summary jsonb; quality_flags text[].

**Integrity:** Same-site experiment/artifact; generation IDs validated to same-site imports; no result without pinned source generations. **Query indexes:** (tenant_id, site_id, experiment_id, analyzed_at DESC).

**Owner:** Measurement service. **Lifecycle:** Immutable reports; later corrections supersede, not overwrite.

## A.11 Budgets, audit, notifications, support, and lifecycle

### `app.budget_accounts` [T]

**Domain fields:** site_id uuid?; scope_kind enum(tenant,site); category text; unit text; currency text?; period_start timestamptz; period_end timestamptz; limit_amount bigint; reserved_amount bigint; spent_amount bigint; safety_reserve_amount bigint.

**Integrity:** Site nullness follows scope_kind; FK site when present; unique normalized scope/category/unit/currency/period; amounts >= 0 and period_end > period_start. No constraint requires actual spend <= budget. **Query indexes:** Unique account scope/period; (tenant_id, period_end).

**Owner:** Budget authority. **Lifecycle:** Mutable balance cache reconciled against append-only ledger; preserve completed periods.

### `app.budget_holds` [T]

**Domain fields:** site_id uuid?; account_id uuid; command_id uuid; operation_id uuid?; external_work_id text; reserved_amount bigint; state enum(reserved,unresolved,settled,released); reserved_at timestamptz; settled_at timestamptz?.

**Integrity:** FK tenant account/command, scoped site/operation when present; unique account/external_work_id; unknown remote usage must not be released by timeout. **Query indexes:** (tenant_id, account_id, state).

**Owner:** Budget authority. **Lifecycle:** Intent/reservation immutable; controlled settlement transitions.

### `app.budget_ledger` [T]

**Domain fields:** account_id uuid; hold_id uuid?; entry_type text; reserved_delta bigint; spent_delta bigint; currency text?; unit text; source_record_hash hash; recorded_at timestamptz.

**Integrity:** FK account/hold; unique account/source_record_hash/entry_type for idempotent settlement; signed deltas allowed, balances may not become negative; unit/currency must match account. **Query indexes:** (tenant_id, account_id, recorded_at, id).

**Owner:** Budget authority. **Lifecycle:** Append-only; corrections are compensating entries.

### `app.audit_events` [T]

**Domain fields:** site_id uuid?; aggregate_kind text; aggregate_id uuid; aggregate_sequence bigint; actor_kind text; actor_identifier text; event_type text; facts jsonb; previous_hash hash?; event_hash hash; occurred_at timestamptz.

**Integrity:** Site FK where present; unique tenant/aggregate_kind/aggregate_id/sequence; facts versioned and redacted. No impossible global order across all concurrent aggregates. **Query indexes:** (tenant_id, site_id, occurred_at DESC); aggregate sequence.

**Owner:** Audit service. **Lifecycle:** Append-only; off-host checkpoints; lawful retention and documented redaction.

### `app.audit_checkpoints` [T]

**Domain fields:** interval_start timestamptz; interval_end timestamptz; aggregate_manifest_hash hash; external_object_key text; signing_key_id text; signature bytea.

**Integrity:** Unique tenant/interval/manifest; durable external object verified before claiming checkpoint success. **Query indexes:** (tenant_id, interval_end DESC).

**Owner:** Audit integrity. **Lifecycle:** Immutable; keys and retention follow evidence policy.

### `app.notifications` [T]

**Domain fields:** site_id uuid?; recipient_user_id uuid; source_event_id uuid; channel text; template_version text; body_artifact_reference text; status text; next_attempt_at timestamptz; provider_message_id text?.

**Integrity:** FK membership and site where present; unique tenant/recipient/source_event/channel; validate referenced event visibility. **Query indexes:** (status, next_attempt_at) restricted worker; (tenant_id, recipient_user_id, created_at DESC).

**Owner:** Notifications. **Lifecycle:** Truthful queued/sent/provider-accepted status; not assumed read.

### `app.notification_attempts` [T]

**Domain fields:** notification_id uuid; attempt_number int; transport_result text; provider_message_id text?; attempted_at timestamptz.

**Integrity:** FK notification; unique notification/attempt_number; no repeated approval side effects even if transport duplicates messages. **Query indexes:** (tenant_id, notification_id, attempt_number).

**Owner:** Notifications. **Lifecycle:** Append-only transport evidence.

### `app.exports` [T]

**Domain fields:** requester_user_id uuid; site_scope jsonb; export_schema_version int; state text; object_key text?; object_hash hash?; expires_at timestamptz?; completed_at timestamptz?.

**Integrity:** FK membership; scope validated against current permission at generation and download; export does not use a public permanent URL. **Query indexes:** (tenant_id, requester_user_id, created_at DESC).

**Owner:** Data lifecycle. **Lifecycle:** Short-lived encrypted deliverables; completion independent from download acknowledgement.

### `app.deletion_requests` [T]

**Domain fields:** requester_user_id uuid; scope_kind text; scope_identifier text; state text; requested_at timestamptz; retention_review jsonb; completed_at timestamptz?.

**Integrity:** FK requester membership; scope typed; destructive lifecycle uses step-up authorization and required retention checks. **Query indexes:** (tenant_id, state, requested_at).

**Owner:** Data lifecycle. **Lifecycle:** Durable progress; never implies deletion of customer CMS content.

### `app.deletion_tombstones` [T]

**Domain fields:** deletion_request_id uuid; object_kind text; object_identifier_hash hash; applied_at timestamptz; external_journal_ack text.

**Integrity:** FK deletion request; unique request/object_kind/hash; minimum retained identifier sufficient for replay without retaining deleted content. **Query indexes:** (tenant_id, applied_at).

**Owner:** Data lifecycle/recovery. **Lifecycle:** Append-only replay protection retained beyond backup horizon.

### `app.support_access_grants` [T]

**Domain fields:** requester_user_id uuid; operator_user_id uuid; allowed_scope jsonb; reason text; granted_at timestamptz; expires_at timestamptz; revoked_at timestamptz?.

**Integrity:** FK global users; customer authority and exact scope validated; operator role alone never grants tenant access. **Query indexes:** (tenant_id, expires_at).

**Owner:** Support authority. **Lifecycle:** Time-limited audited access; private credentials/content excluded by default.

### `app.operational_incidents` [T]

**Domain fields:** site_id uuid?; severity text; category text; state text; opened_at timestamptz; resolved_at timestamptz?; runbook_key text; evidence_references jsonb; remediation_command_id uuid?.

**Integrity:** FK site and command when supplied; internal platform-wide incidents live in operator system, not a nullable-tenant row. **Query indexes:** (tenant_id, state, severity, opened_at).

**Owner:** Operations. **Lifecycle:** Mutable incident state, append-only timeline in audit events.

## A.12 Infrastructure and physical-design exclusions

Temporal and Keycloak schemas belong to their supported migration tools; do not reproduce their internal tables in application migrations. OpenBao storage and recovery keys also follow its supported operating procedures. Global public rate-limit bookkeeping and anonymous pre-login abuse controls may live in a separate operator-owned security store with strict retention; they must not be smuggled into tenant tables with a null tenant.

The catalog intentionally does not introduce payment-card storage, an ad-buying schema, or a generic arbitrary-code task table. Those features are outside GA1. If self-service billing is added, use a certified hosted payment flow and add subscription, entitlement reconciliation, webhook deduplication, cancellation, tax, and failed-payment contracts before making it public.

For initial deployment, keep small authority tables unpartitioned. Introduce time partitions only after query/load evidence, and preserve tenant/site filtering, valid unique/FK constraints, bounded retention, and restore behavior. Replicas may serve explicitly stale reports, not current approvals, budgets, memberships, revocations, or dispatch decisions.


<a id="appendix-b"></a>
# Appendix B. Critical relational, transaction, and authorization patterns

## B.1 Scope of the SQL examples

The following is a **reduced reference schema for the safety relationships**, not the complete production migration set. It uses a separate `signal_contract_example` schema so it cannot be mistaken for an instruction to replace the catalog in Appendix A. Production migrations must include the complete domain fields, grants, reference kinds, and indexes from that catalog.

These examples require real PostgreSQL testing before adoption. This document's review does not substitute for executing migrations under non-owner roles, concurrent sessions, forced failures, and restoration. Do not run example schema provisioning through a production API credential.

The reduced SQL example below covers patches to existing resources. Expected-absence creation uses the distinct nullable-version and creation-key contract in Appendix A; it must not be implemented by inventing an existing resource version.

## B.2 Same-tenant, same-site, exact-revision integrity

```sql
CREATE SCHEMA signal_contract_example;
REVOKE ALL ON SCHEMA signal_contract_example FROM PUBLIC;

CREATE FUNCTION signal_contract_example.current_tenant_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT NULLIF(current_setting('signal.tenant_id', true), '')::uuid
$$;

CREATE FUNCTION signal_contract_example.current_site_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT NULLIF(current_setting('signal.site_id', true), '')::uuid
$$;

CREATE TABLE signal_contract_example.tenants (
  tenant_id uuid PRIMARY KEY,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE signal_contract_example.memberships (
  tenant_id uuid NOT NULL REFERENCES signal_contract_example.tenants,
  user_id uuid NOT NULL,
  state text NOT NULL CHECK (state IN ('active', 'suspended', 'removed')),
  authorization_epoch bigint NOT NULL CHECK (authorization_epoch >= 0),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE signal_contract_example.sites (
  tenant_id uuid NOT NULL REFERENCES signal_contract_example.tenants,
  id uuid NOT NULL,
  paused boolean NOT NULL DEFAULT true,
  pause_epoch bigint NOT NULL DEFAULT 0 CHECK (pause_epoch >= 0),
  authorization_epoch bigint NOT NULL DEFAULT 0
    CHECK (authorization_epoch >= 0),
  connector_epoch bigint NOT NULL DEFAULT 0 CHECK (connector_epoch >= 0),
  PRIMARY KEY (tenant_id, id)
);

CREATE TABLE signal_contract_example.artifacts (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  object_key text NOT NULL,
  object_version text NOT NULL,
  sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
  byte_length bigint NOT NULL CHECK (byte_length >= 0),
  retain_until timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (tenant_id, site_id, id, sha256),
  UNIQUE (tenant_id, site_id, object_key, object_version),
  FOREIGN KEY (tenant_id, site_id)
    REFERENCES signal_contract_example.sites (tenant_id, id)
);

CREATE TABLE signal_contract_example.changes (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  purpose text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id)
    REFERENCES signal_contract_example.sites (tenant_id, id)
);

CREATE TABLE signal_contract_example.change_revisions (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  change_id uuid NOT NULL,
  revision_number bigint NOT NULL CHECK (revision_number > 0),
  manifest_hash bytea NOT NULL CHECK (octet_length(manifest_hash) = 32),
  manifest_artifact_id uuid NOT NULL,
  approval_class text NOT NULL CHECK (approval_class IN ('A2', 'A3', 'A4')),
  sealed_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (tenant_id, site_id, id, manifest_hash),
  UNIQUE (tenant_id, site_id, change_id, revision_number),
  CHECK (expires_at > sealed_at),
  FOREIGN KEY (tenant_id, site_id, change_id)
    REFERENCES signal_contract_example.changes (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id, manifest_artifact_id, manifest_hash)
    REFERENCES signal_contract_example.artifacts
      (tenant_id, site_id, id, sha256)
);

CREATE TABLE signal_contract_example.change_items (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  revision_id uuid NOT NULL,
  item_number integer NOT NULL CHECK (item_number >= 0),
  resource_identity text NOT NULL,
  expected_version text NOT NULL,
  patch_artifact_id uuid NOT NULL,
  before_artifact_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (tenant_id, site_id, revision_id, id),
  UNIQUE (tenant_id, site_id, revision_id, item_number),
  FOREIGN KEY (tenant_id, site_id, revision_id)
    REFERENCES signal_contract_example.change_revisions
      (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id, patch_artifact_id)
    REFERENCES signal_contract_example.artifacts (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id, before_artifact_id)
    REFERENCES signal_contract_example.artifacts (tenant_id, site_id, id)
);

CREATE TABLE signal_contract_example.approval_grants (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  revision_id uuid NOT NULL,
  manifest_hash bytea NOT NULL CHECK (octet_length(manifest_hash) = 32),
  actor_user_id uuid NOT NULL,
  actor_membership_epoch bigint NOT NULL CHECK (actor_membership_epoch >= 0),
  site_authorization_epoch bigint NOT NULL CHECK (site_authorization_epoch >= 0),
  granted_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  CHECK (expires_at > granted_at),
  FOREIGN KEY (tenant_id, site_id, revision_id, manifest_hash)
    REFERENCES signal_contract_example.change_revisions
      (tenant_id, site_id, id, manifest_hash),
  FOREIGN KEY (tenant_id, actor_user_id)
    REFERENCES signal_contract_example.memberships (tenant_id, user_id)
);

CREATE TABLE signal_contract_example.approval_revocations (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  approval_id uuid NOT NULL,
  reason text NOT NULL,
  revoked_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (tenant_id, site_id, approval_id),
  FOREIGN KEY (tenant_id, site_id, approval_id)
    REFERENCES signal_contract_example.approval_grants
      (tenant_id, site_id, id)
);

CREATE TABLE signal_contract_example.external_operations (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  revision_id uuid NOT NULL,
  item_id uuid NOT NULL,
  step_key text NOT NULL,
  idempotency_key text NOT NULL,
  desired_hash bytea NOT NULL CHECK (octet_length(desired_hash) = 32),
  status text NOT NULL CHECK (status IN (
    'PLANNED', 'AUTHORIZED', 'DISPATCHING', 'ACKNOWLEDGED',
    'OUTCOME_UNKNOWN', 'APPLIED', 'NOT_APPLIED', 'CONFLICT', 'FAILED_FINAL'
  )),
  row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (tenant_id, site_id, revision_id, item_id, step_key),
  UNIQUE (tenant_id, site_id, idempotency_key),
  FOREIGN KEY (tenant_id, site_id, revision_id, item_id)
    REFERENCES signal_contract_example.change_items
      (tenant_id, site_id, revision_id, id)
);

CREATE TABLE signal_contract_example.operation_events (
  tenant_id uuid NOT NULL,
  site_id uuid NOT NULL,
  id uuid NOT NULL,
  operation_id uuid NOT NULL,
  event_number bigint NOT NULL CHECK (event_number >= 1),
  previous_state text NOT NULL,
  next_state text NOT NULL,
  reason_code text NOT NULL,
  occurred_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (tenant_id, site_id, operation_id, event_number),
  FOREIGN KEY (tenant_id, site_id, operation_id)
    REFERENCES signal_contract_example.external_operations
      (tenant_id, site_id, id)
);

CREATE INDEX operation_queue_idx
  ON signal_contract_example.external_operations
    (tenant_id, site_id, status, id);
CREATE INDEX approval_revision_idx
  ON signal_contract_example.approval_grants
    (tenant_id, site_id, revision_id, expires_at);
CREATE INDEX change_items_patch_fk_idx
  ON signal_contract_example.change_items
    (tenant_id, site_id, patch_artifact_id);
CREATE INDEX change_items_before_fk_idx
  ON signal_contract_example.change_items
    (tenant_id, site_id, before_artifact_id);
```

The reference deliberately does not encode an `approved` column in a change or operation. Authority is derived from immutable grants, revocations, current membership, current policy, exact versions, and action-time checks. The production schema adds the policy-decision, connector-binding, recipe-release, permit, budget, actor-identity, and delivery relationships from Appendix A.

## B.3 Forced row security and immutable authority records

```sql
DO $$
DECLARE
  relation_name text;
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'artifacts', 'changes', 'change_revisions', 'change_items',
    'approval_grants', 'approval_revocations',
    'external_operations', 'operation_events'
  ] LOOP
    EXECUTE format(
      'ALTER TABLE signal_contract_example.%I ENABLE ROW LEVEL SECURITY',
      relation_name
    );
    EXECUTE format(
      'ALTER TABLE signal_contract_example.%I FORCE ROW LEVEL SECURITY',
      relation_name
    );
    EXECUTE format(
      'CREATE POLICY tenant_site_scope ON signal_contract_example.%I '
      'USING (tenant_id = signal_contract_example.current_tenant_id() '
      'AND site_id = signal_contract_example.current_site_id()) '
      'WITH CHECK (tenant_id = signal_contract_example.current_tenant_id() '
      'AND site_id = signal_contract_example.current_site_id())',
      relation_name
    );
  END LOOP;
END;
$$;

ALTER TABLE signal_contract_example.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_contract_example.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_root_scope ON signal_contract_example.tenants
  USING (tenant_id = signal_contract_example.current_tenant_id())
  WITH CHECK (tenant_id = signal_contract_example.current_tenant_id());

ALTER TABLE signal_contract_example.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_contract_example.memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_tenant_scope ON signal_contract_example.memberships
  USING (tenant_id = signal_contract_example.current_tenant_id())
  WITH CHECK (tenant_id = signal_contract_example.current_tenant_id());

ALTER TABLE signal_contract_example.sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_contract_example.sites FORCE ROW LEVEL SECURITY;
CREATE POLICY site_tenant_scope ON signal_contract_example.sites
  USING (tenant_id = signal_contract_example.current_tenant_id())
  WITH CHECK (tenant_id = signal_contract_example.current_tenant_id());

CREATE FUNCTION signal_contract_example.reject_sealed_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
  RAISE EXCEPTION 'sealed records cannot be updated or deleted'
    USING ERRCODE = '55000';
END;
$$;

DO $$
DECLARE
  relation_name text;
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'change_revisions', 'change_items', 'approval_grants',
    'approval_revocations', 'operation_events'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER immutable_record '
      'BEFORE UPDATE OR DELETE ON signal_contract_example.%I '
      'FOR EACH ROW EXECUTE FUNCTION '
      'signal_contract_example.reject_sealed_mutation()',
      relation_name
    );
  END LOOP;
END;
$$;
```

RLS does not grant any access by itself. Provision separate non-owner runtime roles with the minimal table and column privileges in section 7. The sealer may insert sealed revisions, the authorizer may insert grants, and the executor may update only reviewed operation bookkeeping columns. None may disable triggers, modify role grants, or own these tables. Retention is an offline reviewed process after references and holds have been checked, not a general runtime bypass function.

Scope initialization is inside a transaction using bound parameters, for example `SELECT set_config('signal.tenant_id', $1, true)` and `SELECT set_config('signal.site_id', $2, true)`. The trusted service derives those values from authenticated authorization. Never interpolate a user-supplied SQL literal. Commit or rollback clears transaction-local context. Missing context returns no visible rows; invalid UUID context fails the transaction rather than selecting another tenant.

## B.4 Atomic command acceptance and outbox publication

Command acceptance is a single transaction: validate current scope, insert immutable command identity/payload, insert its accepted event, insert an outbox record, and commit. On a unique-idempotency conflict, retrieve the existing command and compare the request fingerprint. A match returns the existing command. A mismatch returns `409 IDEMPOTENCY_KEY_REUSED` without dispatching anything.

The outbox dispatcher claims a bounded batch with `FOR UPDATE SKIP LOCKED`, writes a short lease, commits, and publishes outside the transaction. It marks delivery in a new transaction. A crash between publish and acknowledgement will redeliver. Consumer inbox uniqueness and stable workflow/operation identifiers absorb that duplicate. Do not keep a database lock while waiting on Temporal or an external broker.

An outbox row is not deleted merely because one worker tried to send it. Retain its deduplication identity for the documented retry, replay, and recovery horizon. Administrative replays preserve the event ID unless they intentionally represent a new authorized command.

## B.5 Budget reservation and settlement algorithm

All participating account rows are locked in deterministic scope/account order inside one transaction. Validate unit and currency, the current accounting period, account scope, and the stable external-work key. A duplicate reservation returns its existing hold only if the request fingerprint matches.

For discretionary work, reserve only when:

```text
spent_amount + reserved_amount + requested_amount
    <= limit_amount - safety_reserve_amount
```

Use checked integer arithmetic and a configured upper bound well below signed-bigint overflow. The limit is an authorization rule for **new work**, not a database assertion about actual past charges. Provider charges may exceed estimates and must still be recorded accurately.

The transaction increments the reserved balance, creates the hold, and appends a ledger entry. The external call occurs afterward. When confirmed usage is known, settlement subtracts the held reservation and adds actual spend in one transaction with a unique settlement identity. If actual spend exceeds the limit, create an incident and block new discretionary work. Never discard a charge because it breaks a budget constraint.

A timeout moves the hold to unresolved. Reconciliation, not an arbitrary expiration timer, determines the actual spend or a defensible upper-bound charge. Account-period rollover cannot erase old holds. Credits, corrections, and refunds are explicit ledger entries, not silent edits to history.

## B.6 Lease, version, and authority are different mechanisms

A resource lease serializes Signal's plans. A fencing token rejects stale Signal dispatch authority. A provider conditional update protects against external edits. A policy decision establishes permitted scope. An approval expresses human authorization. None replaces the others.

Before a dispatch permit is issued, validate all of the following from authoritative records in a consistent short transaction: current membership/site grant, relevant epochs, exact manifest hash, current unrevoked grant or standing authorization, recipe/capability release, fresh applicable policy, budget holds, durable journal acknowledgement, resource lease/fence, and absence of unresolved conflicting operations.

After commit, the gateway checks permit expiry and current revocation/epoch state immediately before forwarding. There is no atomic transaction spanning a database and an arbitrary provider. A request already authorized or crossing the network when pause is committed may still complete. The UI must show draining/reconciling work rather than claiming it was recalled. Native upstream fencing, when available and certified, further limits late requests.

## B.7 Migrations and privilege acceptance matrix

Every migration must be exercised on an empty database and on a production-shaped previous-release snapshot. Validate upgrade, mixed-version application access, backfill resumability, locks, disk/WAL growth, index creation, rollback or forward-fix, read-replica lag, and restoration. Do not run `CREATE INDEX CONCURRENTLY` inside a migration transaction that PostgreSQL disallows; split deployment steps when required. [S57]

The PostgreSQL suite must test at least these principals: an actual API role, authorizer, executor, ingestion role, report reader, migrator, and unauthorized role. Test missing tenant context, connection reuse after rollback, wrong-tenant IDs, same-tenant/wrong-site IDs, direct mutation of sealed records, stale row versions, unsupported enum values, duplicated receipts, duplicate operation identities, cross-scope budget references, and unauthorized `SET ROLE` or schema changes.

A migration is not approved because its ORM models import successfully. The evidence must show effective database behavior under the exact target PostgreSQL and extension versions.

<a id="appendix-c"></a>
# Appendix C. Public API, model tools, and adapter contracts

## C.1 API conventions

Use `/v1` for the application API and separately version payload contracts. All writes require authenticated identity, an idempotency key where retryable, CSRF protection for cookie sessions, current membership/site permission, and bounded request sizes. An `If-Match` row version is required when editing mutable settings or drafts.

Return `202` when a durable command is accepted, not when delivery is complete. Return `200/201` for a completed synchronous read/create only when accurate. Use `400` for invalid syntax, `401` for absent/expired authentication, `403` for an authenticated forbidden action, `404` for inaccessible resource lookups where existence should not be disclosed, `409` for state/version/idempotency conflicts, `422` for a well-formed but invalid contract, `429` for bounded rate limiting, and `503` for unavailable required dependencies. Use a consistent disclosure policy so status-code differences do not reveal another tenant's records.

### Required endpoint inventory

| Endpoint | Permission and behavior |
|---|---|
| `GET /v1/session` | Current server-verified identity, selected organization, effective UI capabilities, and reauthentication requirements |
| `POST /v1/session/switch-tenant` | Verify current membership and issue a newly tenant-scoped session; invalidate client caches |
| `POST /v1/session/logout` | Revoke current session and clear cookie; no GET side effect |
| `GET /v1/organizations` | Only organizations accessible to the authenticated global identity |
| `POST /v1/organizations` | Controlled bootstrap, abuse limits, owner membership, and complete setup transaction |
| `POST /v1/invitations` | Organization authority; exact email/role/site grants, expiry, delivery tracking |
| `POST /v1/invitations/accept` | Authenticated verified recipient, token consumption, membership confirmation; bearer token is in the request body, not a route path, and must not enter logs |
| `PATCH /v1/members/{user_id}` | Current owner/admin authority, expected version, last-owner and separation checks |
| `GET /v1/sites` | Authorized site list with connector/coverage/stale state |
| `POST /v1/sites` | Site creation and ownership-verification workflow |
| `POST /v1/sites/{site_id}/verify-origin` | Approved proof method and explicit origin; does not fetch arbitrary network destinations |
| `GET /v1/sites/{site_id}/connectors` | Lifecycle, granted scopes, selected resources, health, and certified capabilities; no secrets |
| `POST /v1/sites/{site_id}/connectors/{provider}/authorize` | Begin provider-specific verified OAuth/App/credential flow |
| `GET /v1/oauth/{provider}/callback` | One-time validated OAuth callback, not a generic cross-tenant connector-write route |
| `POST /v1/sites/{site_id}/connectors/{binding_id}/select-resources` | Ownership check, write-claim check, capability probe |
| `POST /v1/sites/{site_id}/connectors/{binding_id}/reconnect` | Explicit scope review and fresh credentials; old approvals do not silently transfer |
| `POST /v1/sites/{site_id}/connectors/{binding_id}/disconnect` | Advance generation, block new writes, reconcile accepted work, revoke and remove credentials |
| `POST /v1/sites/{site_id}/commands` | Typed chat/dashboard command entry point with principal-derived authority |
| `GET /v1/commands/{command_id}` | Accepted/processing/final command state with links to verified results |
| `POST /v1/sites/{site_id}/audits` | Bounded audit command; scope and costs explicit |
| `GET /v1/sites/{site_id}/inventory` | Keyset-paginated resources and coverage; no cross-site cache reuse |
| `GET /v1/sites/{site_id}/findings` | Reproducible evidence-backed findings, including false-positive resolution |
| `GET /v1/sites/{site_id}/strategy` | Reviewed plan, priorities, hypotheses, and evidence |
| `GET /v1/sites/{site_id}/changes` | Work/revision states, approvals, blockers, delivery, and recovery status |
| `GET /v1/sites/{site_id}/changes/{revision_id}` | Exact sealed manifest, visual diff, current evidence, policy, and scope |
| `POST /v1/sites/{site_id}/changes/{revision_id}/approve` | Exact hash, current eligible actor, required step-up, grant expiry |
| `POST /v1/sites/{site_id}/changes/{revision_id}/reject` | Record reason and stop preparation/dispatch eligibility without hiding history |
| `POST /v1/sites/{site_id}/approvals/{approval_id}/revoke` | Append revocation and invalidate applicable permits; reconcile in-flight effects |
| `POST /v1/sites/{site_id}/changes/{revision_id}/recovery-plan` | Read current state and generate conflict-aware recovery proposal, not instant overwrite |
| `POST /v1/sites/{site_id}/pause` | Authoritative pause/epoch change; returns draining operations |
| `POST /v1/sites/{site_id}/resume` | Current authority, dependency checks, bounded restart; no automatic replay of stale approvals |
| `GET /v1/sites/{site_id}/analytics` | Pinned source generations, explicit filters/timezones, data freshness and quality |
| `GET /v1/sites/{site_id}/activity` | Tenant/site-scoped durable history with keyset pagination |
| `GET /v1/sites/{site_id}/events` | Authorized resumable event stream; authorization refreshed/revocation closes stream |
| `PATCH /v1/sites/{site_id}/settings` | Expected row version, typed fields, current scope; no hidden authority escalation |
| `POST /v1/sites/{site_id}/standing-authorizations` | Human-reviewed explicit scope and aggregate limits; high-risk changes require step-up |
| `POST /v1/sites/{site_id}/telegram/pair` | One-time short-lived dashboard-issued pairing token |
| `POST /v1/exports` | Authorized export job with current-permission checks at generation and download |
| `POST /v1/deletion-requests` | Step-up, scope, retention/hold review, connector revocation, and auditable progress |
| `GET /v1/health/live` | Process health only; no secrets or dependency inventory |
| `GET /v1/health/ready` | Restricted deployment readiness: migrations, critical dependencies, compatible release |

Provider webhooks use separate ingress routes with raw-body verification, strict request limits, durable inbox deduplication, and no browser-session assumptions. Platform release and support-impersonation operations use a separate operator surface with stronger authentication, never hidden ordinary-user endpoints.

### Accepted-command fixture

The following values are illustrative, not real customer data or real approval hashes.

```json
{
  "contract_version": 1,
  "command_id": "10000000-0000-4000-8000-000000000001",
  "status": "ACCEPTED",
  "site_id": "20000000-0000-4000-8000-000000000001",
  "accepted_at": "2026-09-07T20:00:00Z",
  "status_path": "/v1/commands/10000000-0000-4000-8000-000000000001",
  "message": "Audit accepted. No production changes have been made."
}
```

### Error fixture

```json
{
  "contract_version": 1,
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "The page changed after this revision was prepared.",
    "retryable": false,
    "required_action": "PREPARE_NEW_REVISION",
    "correlation_id": "30000000-0000-4000-8000-000000000001"
  }
}
```

Never put tokens, source body content, another tenant's identifiers, SQL statements, or raw provider stack traces into public error messages.

## C.2 OpenAI tool boundary

The model receives read/propose tools, not `publish`, `approve`, `grant_permission`, `execute_sql`, `change_policy`, or unrestricted HTTP/shell execution. A proposal can start a workflow, but cannot satisfy that workflow's authority checks. The gateway resolves tenant and site from trusted run context; model-supplied IDs are merely requested targets and must be authorized.

Example Responses API function definition:

```json
{
  "type": "function",
  "name": "propose_recipe_execution",
  "description": "Propose a reviewed SEO recipe against scoped resources. This does not approve or execute a production change.",
  "strict": true,
  "parameters": {
    "type": "object",
    "properties": {
      "recipe_release_id": {
        "type": "string"
      },
      "resource_ids": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "evidence_ids": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "rationale": {
        "type": "string"
      },
      "requested_observation_days": {
        "type": ["integer", "null"]
      }
    },
    "required": [
      "recipe_release_id",
      "resource_ids",
      "evidence_ids",
      "rationale",
      "requested_observation_days"
    ],
    "additionalProperties": false
  }
}
```

Validate lengths, UUID parsing, duplicate IDs, allowed scope, evidence freshness, and recipe limits in Signal even after a structurally valid output. Handle refusal, incomplete output, unsupported schema, tool-name mismatch, and input exceeding the selected model's verified limits. Strict structured output is a shape constraint, not a truth or authorization guarantee. [S02] [S03]

### OpenAI runtime configuration contract

```yaml
provider: openai
api: responses
model_selection: approved_control_model_release
foreground_only: true
store: false
parallel_tool_calls: false
retry_owner: signal_model_gateway
sdk_automatic_retries: 0
request_timeout_profile: reviewed_per_model_and_task
maximum_agent_turns: 8
maximum_tool_reads: 20
unknown_usage_behavior: retain_budget_hold_and_reconcile
input_policy: minimize_and_redact_sensitive_data
credentials_location: server_side_secret_reference
failure_behavior: pause_or_use_separately_evaluated_openai_fallback
```

This is Signal configuration, not a literal OpenAI request body. The deployed API request uses only parameters accepted by the selected model. Model ID, SDK version, request limits, timeouts, and supported tool/schema behavior come from the approved release manifest and live preflight checks. A model catalog entry alone does not prove access for our organization or compatibility with every requested parameter.

## C.3 Connector and adapter interface

Every adapter implements the subset it certifies; unsupported methods return `CAPABILITY_UNCERTIFIED` before any side effect.

| Method | Required input | Required result |
|---|---|---|
| `probe_capabilities` | Trusted binding, provider/resource/version scope | Typed capabilities, exact limitations, certification identity, freshness |
| `read_exact` | Resource identity, stage, locale, requested version | Canonical exact representation, upstream version, scope, source receipt |
| `prepare_patch` | Immutable before-state, permitted field changes | Normalized patch, intended hash, side-effect/dependency analysis |
| `validate_patch` | Patch, recipe, current capability | Structural/functional checks and explicit unsupported fields |
| `apply_conditional` | Stable operation ID, expected version, exact patch, permit/fence | Receipt or explicit ambiguous outcome; never silent overwrite |
| `inspect_operation` | Stable operation ID or provider receipt ID | Known applied/not-applied/pending/unknown state and evidence |
| `read_live` | Approved public URL, locale/stage, delivery identity | Independent live observation with cache/propagation metadata |
| `prepare_inverse` | Before, Signal-after, current state, dependencies | Safe inverse patch or explicit conflict and alternatives |
| `revoke` | Trusted binding/generation and provider capability | Credential revocation result and remaining in-flight uncertainty |

Responses include a contract version and error taxonomy. Exact snapshots preserve fields relevant to conflict detection, including plugin-managed metadata and publication state. A generic JSON hash that excludes one writable field cannot protect that field against lost updates.

### Operation receipt fixture

```json
{
  "contract_version": 1,
  "operation_id": "40000000-0000-4000-8000-000000000001",
  "provider_operation_id": "signal-op-40000000-0000-4000-8000-000000000001",
  "transport_result": "ACKNOWLEDGED",
  "application_result": "APPLIED",
  "observed_resource_version": "opaque-provider-version-42",
  "live_verification": "PENDING",
  "observed_at": "2026-09-07T20:05:00Z"
}
```

A source acknowledgement does not set `live_verification` to passed. Verification records belong to the independent verifier and may remain propagation-pending or fail even when provider application is confirmed.

## C.4 Default values and where they may change

| Initial proposal | Configuration owner | Change gate |
|---|---|---|
| A3 approval lifetime: 24 hours | Policy release/site stricter override | Security review; invalidate changed-scope pending work |
| A4 approval lifetime: 1 hour | Policy release/site stricter override | High-risk authority review |
| 8 model turns and 20 tool reads per bounded reasoning run | Model/recipe release | Quality, cost, and termination tests |
| 5 MiB decoded HTML response cap | Crawl profile | Parser/security/load tests; optional scoped larger-page profile |
| Initial owned-origin navigation rate: 1 request/second, 1 in-flight HTML fetch | Crawl/origin profile | Origin health, consent, fairness, and measurement-profile review |
| Daily monitoring of selected high-change platform policy sources | Policy operations | Source integrity and review coverage |
| Baseline reference capacity from section 26 | Deployment profile | Measured load and recovery evidence, not configuration alone |
| Retention periods from section 7 | Data governance/site allowed stricter settings | Legal holds, advertised recovery window, storage, and deletion review |

Defaults are proposals for implementation and validation, not universal constants or vendor rules. One typed configuration source must generate the effective runtime values, dashboard descriptions, and test fixtures. Avoid copying limits independently into the frontend, recipe prompts, policy code, and workers.


<a id="appendix-d"></a>
# Appendix D. Operational runbooks

## D.1 Runbook operating rules

Every incident has an owner, severity, tenant/site scope, correlation IDs, initial state, actions, timestamps, evidence references, and a recorded closure decision. Record what is known separately from what is suspected. Do not modify evidence merely to make the dashboard green.

Containment must use a control that remains available during the incident. The platform requires an independently reachable emergency egress switch and recovery procedure for credential-bearing workers. A pause button hosted on the failed database is not sufficient as the only emergency control.

Only designated operators may use these runbooks. High-impact recovery, key rotation, and cross-tenant investigations require the documented second-person review where applicable. Customer communications describe affected scope, known effects, uncertainties, and the next decision. Notification acceptance by a provider is not proof that the customer read it.

## RB-01: Suspected unauthorized or cross-tenant access

**Trigger:** A scope-isolation test fails in production monitoring, an audit shows an unauthorized dispatch, or a credible customer/security report indicates exposure.

**Contain:** Disable affected write routes and credential-bearing execution. If scope is unknown, disable platform-wide production dispatch through the independent egress control. Revoke suspect sessions/permits and preserve audit checkpoints, relevant snapshots, and request evidence. Do not destroy keys or logs before preserving necessary investigation evidence.

**Diagnose:** Identify the earliest unauthorized identity/scope decision, every potentially accessed tenant/resource, credential exposure, external writes, and whether backups or exports were affected. Distinguish reading exposure from confirmed mutation. Reconcile external state rather than trusting the dashboard projection.

**Recover:** Correct the authorization boundary, rotate exposed credentials, prepare conflict-aware recovery for confirmed changes, and obtain qualified security/legal incident guidance appropriate to the facts and jurisdictions. Do not automatically claim no exposure because no write receipt exists.

**Resume gate:** Independent security review, targeted regression tests, current credentials, verified external-state reconciliation, and a recorded operator decision. No automatic resume on a timer.

## RB-02: External write outcome unknown

**Trigger:** Worker crash after dispatch, timeout, missing acknowledgement, or contradictory provider observations.

**Contain:** Quarantine the resource and dependent writes. Preserve the same operation identity and budget hold. Stop generic retry controls from issuing a new intent.

**Diagnose:** Inspect the stable provider operation ID, receipt history, exact upstream versions, write-safety journal, deployment events, and independent live observations. Respect the connector's consistency/propagation window. A stale CDN response is not evidence that the provider rejected the request.

**Recover:** If applied, record the authoritative receipt and run live verification. If conclusively not applied, record that evidence and decide whether the same authorized intent may be retried. If still ambiguous after the certified window, escalate for explicit reconciliation rather than guessing. Any altered patch needs a new revision and authority.

**Resume gate:** Resolved operation state, valid current authority, safe target version, and reconciled budget usage. A lease timeout alone does not release quarantine.

## RB-03: Policy source or policy engine unavailable

**Trigger:** Required source freshness expired, signature verification failed, material unreviewed source change, or OPA/input validation unavailable.

**Contain:** Block affected writes. Continue unrelated read-only diagnosis when safe. Keep existing approved revisions visibly blocked rather than relabeling them as denied by the customer.

**Diagnose:** Separate transport outage, source-domain change, compromised source evidence, signature/key problem, engine failure, and missing applicability information.

**Recover:** Restore verified source/engine access, review material interpretation changes, run rule fixtures, sign the approved bundle, and reevaluate affected revisions. Do not lower freshness thresholds or bypass signature checks simply to resume work.

**Resume gate:** Valid bundle, current applicable source evidence, passing policy tests, and renewed approvals when the approved action or risk context materially changed.

## RB-04: Connector revoked, expired, or insufficiently scoped

**Trigger:** OAuth invalid-grant response, app uninstall, CMS authorization rejection, missing scope, or binding generation mismatch.

**Contain:** Advance or mark the affected binding generation invalid and block new dispatch. Preserve accepted operation identities for reconciliation. Do not repeatedly request credentials or post the token error into public chat.

**Diagnose:** Determine whether this is expiry, user revocation, resource transfer, provider outage, refresh-token rotation race, missing permission, or adapter incompatibility.

**Recover:** Direct an eligible administrator to the authenticated reconnect flow. Show exact required scopes and selected resources again. Store fresh credentials through the secrets service and rerun ownership, global write-claim, and capability checks.

**Resume gate:** Certified current binding, revalidated resource identity and outstanding work, current approvals, and no unresolved effects from the previous credential generation.

## RB-05: OpenAI unavailable, rate-limited, or degraded

**Trigger:** Sustained API errors, model access/deprecation error, quota exhaustion, malformed output increase, quality regression, or unexpected cost.

**Contain:** Stop starting affected reasoning tasks; keep durable workflows and user-visible status. Preserve unresolved cost reservations. Do not repeatedly replay potentially charged requests without a budget/retry decision.

**Diagnose:** Check provider response/request IDs, model release, account access, quota/rate-limit headers, schema compatibility, recent prompt/tool changes, and whether the failure is platform-wide or tenant-specific.

**Recover:** Apply bounded backoff or use a separately evaluated OpenAI fallback release that passes the same task contract. If no qualified fallback exists, pause reasoning and keep deterministic monitoring running. A cheaper model is not automatically a safe fallback.

**Resume gate:** Successful capability preflight, required evaluation/drift checks, reconciled budget impact, and bounded resumed queue. Do not release a large accumulated queue all at once.

## RB-06: Suspected secret or signing-key compromise

**Trigger:** Leaked token, suspicious connector use, invalid audit/signature chain, compromised worker, or exposed deployment environment.

**Contain:** Isolate the affected worker/service and disable its credentials/permits. Separate provider credential compromise from release-signing compromise, which may affect every consumer of a release.

**Diagnose:** Inventory affected credential paths, tokens, signing keys, artifacts, tenant bindings, provider actions, and potentially signed malicious releases. Preserve investigation evidence in an independently controlled location.

**Recover:** Rotate/revoke credentials using a known-clean control environment, publish explicit key/release revocations, update execution generations, and re-probe connectors. Rebuild compromised hosts rather than trusting an in-place cleanup. Reconcile all uncertain provider writes.

**Resume gate:** Verified clean workloads, current trust roots, invalidated old authority, affected-customer incident handling, and a successful narrowly scoped canary operation with recorded evidence.

## RB-07: Verified live regression after delivery

**Trigger:** Broken page/function, unintended noindex/canonical/redirect behavior, sustained deployment mismatch, or a verified significant performance regression in comparable tests.

**Contain:** Pause related recipes and the affected resource/dependency scope. Distinguish a technical regression from ordinary noisy search metrics.

**Diagnose:** Compare exact before/after/current artifacts, deployed revision, cache state, feature behavior, downstream dependencies, and simultaneous human changes. Check whether the problem affects one page, a template, a locale, or the whole site.

**Recover:** Prepare a current-policy-approved inverse patch or compensating deployment. Use the emergency recovery authorization only within its explicit scope. An overlapping human edit requires conflict review, not blind restoration.

**Resume gate:** Live functional/indexability verification, preserved later edits, dependency recovery, incident review, and recipe regression fixtures. Delayed search outcomes remain separately observed.

## RB-08: Backup or restore-verification failure

**Trigger:** Missed backup/WAL shipping, invalid checksum, unreadable encryption key, off-site replica failure, or failed restore exercise.

**Contain:** Stop new production changes once required recovery guarantees are unavailable. Do not purge local history or before-state artifacts to relieve space without a retention/safety decision.

**Diagnose:** Identify the last tested recoverable point, WAL/archive gaps, storage capacity, key availability, object-version integrity, and whether the independent write-safety journal is still durable.

**Recover:** Repair transport/storage/keys through controlled procedures, create a new consistent backup, and perform an isolated restoration exercise. A successful upload is not enough.

**Resume gate:** Verified restore, acceptable tested recovery point, readable active recovery artifacts, and completed reconciliation of writes made during degraded protection.

## RB-09: Full control-plane restoration

**Trigger:** Lost/corrupted database or region/host failure requiring restoration.

**Contain:** Freeze external write egress and isolate old workers before bringing up restored state. Establish a new recovery/execution generation outside the restored database's history.

**Restore:** Follow section 29.4: restore compatible databases, identity, secrets, artifacts, and workers; replay deletions and authority revocations; load post-backup write intents; reconcile providers; rebuild projections; verify current trust and permissions.

**Important:** The remote CMS or deployment may be newer than both the restored application database and Temporal history. Resume must not replay old operations just because the restored workflow believes they are incomplete.

**Resume gate:** Recovery checklist signed, restored-state isolation tests passed, every potentially sent intent resolved or quarantined, external credentials correctly fenced, and narrow capability-by-capability reenablement.

## RB-10: Missing or corrupted evidence/recovery artifact

**Trigger:** Hash mismatch, unavailable before-state object, unexpected object version, missing encryption key, or failed durability attestation.

**Contain:** Block the dependent operation or recovery action. Do not substitute a different object with the same display name.

**Diagnose:** Compare manifest reference, expected hash, storage version, replication acknowledgement, encryption metadata, retention events, and audit history. Determine whether this is an isolated storage problem or evidence tampering.

**Recover:** Restore and verify the exact artifact from a trusted replica/backup. If unavailable, declare the loss of recovery capability, assess affected writes, and escalate. Re-crawling today's page does not recreate yesterday's before-state.

**Resume gate:** Exact artifact restored and verified, or a newly approved alternative plan that does not falsely claim the lost recovery guarantee.

## RB-11: Queue growth, hot tenant, or crawler overload

**Trigger:** Sustained queue age, origin backoff, worker saturation, memory/disk pressure, or a single tenant consuming disproportionate capacity.

**Contain:** Reduce new discretionary work and per-tenant concurrency while reserving capacity for pause, reconciliation, verification, and recovery. Keep origin and provider limits enforced.

**Diagnose:** Separate legitimate volume from crawl traps, infinite parameter spaces, duplicate scheduling, repeated model failure, slow providers, unbounded rendering, expensive database queries, and projection backlog.

**Recover:** Cancel or supersede redundant queued work through the workflow API, repair the trap/duplicate cause, use incremental scope, and scale only the demonstrated bottleneck. Do not raise all limits globally.

**Resume gate:** Bounded queue age, fair scheduling under skewed-load tests, origin health, cost forecasts within limits, and preserved critical-work capacity.

## RB-12: Analytics unexpectedly missing or inconsistent

**Trigger:** Empty GSC/GA4 import, conflicting totals, late data, wrong property/timezone, quota exhaustion, or unexpected sampling/threshold flags.

**Contain:** Mark the report incomplete/stale and suspend decisions that require missing evidence. Do not replace missing points with zeros or claim a traffic collapse without validation.

**Diagnose:** Verify property permissions, query grain, filters, date boundaries, source timezone, reporting lag, import generation, pagination/row limits, provider metadata, and active-generation pointers.

**Recover:** Reimport into a new generation, validate totals/quality, and atomically activate the corrected generation. Recompute affected reports and notify users of corrected conclusions where necessary.

**Resume gate:** Source-consistent data with known coverage. If the source truly lacks sufficient data, the correct state is insufficient evidence, not manufactured certainty.

## RB-13: Accidental approval or urgent customer stop

**Trigger:** Customer revokes approval, reports the wrong site/change, or requests immediate pause.

**Contain:** Commit pause/revocation immediately through the authority service. Display already dispatched operations separately. Do not wait for a model to interpret whether the customer has a good reason.

**Diagnose:** Determine which operations were not sent, which were accepted, which are uncertain, and which were verified live. Check customer authority and exact change references.

**Recover:** Cancel unsent eligible work, reconcile uncertain operations, and prepare authorized conflict-aware recovery for applied changes. Explain irreversible or externally visible effects accurately.

**Resume gate:** Explicit authorized resume, current scopes, revalidated pending work, and no stale approval inherited from the revoked action.

## RB-14: Incompatible application or workflow deployment

**Trigger:** Workflow nondeterminism, schema incompatibility, failed worker registration, invalid tool schema, or a new release causing critical regressions.

**Contain:** Stop assigning new work to the affected release. Keep compatible workers available for existing histories. Disable writes if version compatibility is uncertain.

**Diagnose:** Compare application, worker, Temporal, database, recipe, connector, prompt, and model release identities. Identify whether a schema change is safely backward-compatible or needs a forward fix.

**Recover:** Route histories to a compatible worker version, roll back application components only where schema compatibility permits, or execute the reviewed forward-fix plan. Never edit workflow histories or remove migration records to hide a mismatch.

**Resume gate:** Replay compatibility, migration tests, artifact provenance, canary verification, and passing critical regression suites.

## RB-15: Identity provider or email unavailable

**Trigger:** Users cannot authenticate, MFA verification fails, invitation/reset delivery fails, or Keycloak/session validation is unavailable.

**Contain:** Keep protected actions fail closed. Never disable MFA or issue a universal admin login to restore convenience. Independently accessible emergency controls remain available to designated operators.

**Diagnose:** Distinguish OIDC configuration/clock/certificate errors, service outage, mail-delivery problems, wrong callback origins, expired realm keys, and database access failures.

**Recover:** Restore identity service and validated configuration, reconcile sessions and revocations, retry transactional email through idempotent notifications, and test a complete login plus step-up flow. Email recovery must not bypass verified identity ownership.

**Resume gate:** Verified authentication, authorization, logout/revocation, and recovery flows. Existing sessions are honored only under the documented fresh-authority policy.

## RB-16: Export or deletion job partially completed

**Trigger:** A lifecycle job fails across SQL, artifacts, vector data, caches, exports, or backups.

**Contain:** Keep requested access revoked where appropriate, preserve progress and deletion tombstones, and prevent the job from deleting unrelated customer/site data.

**Diagnose:** Enumerate exactly which stores completed, which remain blocked by lawful holds or active operation evidence, and which have uncertain outcomes. Verify whether a backup restoration could reintroduce deleted data.

**Recover:** Resume idempotent steps, validate scoped counts and references, replay tombstones where needed, and explain remaining retention/backup limitations accurately. Recheck export authorization before issuing any replacement download.

**Resume gate:** Verified scoped completion or an explicit documented legal/technical retention state. Do not mark “deleted everywhere” while retained backups or required records still exist.


<a id="appendix-e"></a>
# Appendix E. Document review and executed reference checks

## E.1 Review scope

This revision was reviewed against the earlier blueprint, the user's requirements, the architecture's authority boundaries, the logical schema, lifecycle definitions, user flows, and failure handling. The review corrected specific inconsistencies instead of merely appending stronger adjectives to the earlier plan.

The checks executed while preparing this file are **document and abstract-contract checks only**. Signal application code, production database migrations, real-provider integrations, browser journeys, penetration tests, load tests, model quality evaluations, and disaster-recovery drills do not yet exist as verified release evidence in this deliverable. Their status remains `NOT_EXECUTED` until implemented and demonstrated.

## E.2 Corrections made during the final consistency pass

| Finding | Correction |
|---|---|
| Initial model-hosting options conflicted with the user's OpenAI decision | OpenAI-only reasoning boundary and evaluated OpenAI fallback releases |
| Agent/workflow/publication success could be conflated | Separate command, workflow, operation, live verification, and measurement state |
| Immutable revisions could accidentally become mutable progress records | Separate change drafts, sealed revisions/items, and rebuildable progress |
| Same-tenant foreign keys could still cross site boundaries | Explicit composite same-site keys and foreign-key requirements |
| Approval could bind an ID without the exact approved bytes | Manifest hash tied to immutable artifact bytes and exact revision/hash references |
| Two OAuth installations could evade a global resource write claim | Claim keys use provider-canonical resource namespace, not credential installation identity |
| Standing grants risked generic JSON authority and unreviewed future releases | Explicit normalized recipe/binding relationships and immutable resolved release sets |
| Brand/configuration current-state pointer was underspecified | Site references the exact active profile revision; pause authority has one source |
| New users lacked a pre-organization authentication state | Separate limited pre-tenant identity session and selected-tenant session |
| Global dispatch could silently require an RLS bypass | Minimal operator-owned tenant directory and separately scoped tenant transactions |
| Creation operations could pretend a nonexistent resource had a version | Explicit expected-absence and creation destination, then immutable result-to-resource mapping |
| Resource leases could be released while a request might complete late | Ambiguity quarantine survives lease expiry and covers resolved creation identities |
| A retained or settled budget hold could be confused with fresh call authority | Unresolved/settled holds do not authorize new external work; settlement remains idempotent |
| A full snapshot undo could overwrite later edits | Inverse-field/block changes or explicit conflict, with no blind restoration |
| SQL reference blocks contained missing PL/pgSQL block terminators | Terminators corrected during static inspection; real-engine execution still required |
| Invitation acceptance placed a bearer token in a route path | Token is supplied in the authenticated request body and excluded from logs |
| Operational runbooks existed only as a requirement | Sixteen concrete containment, diagnosis, recovery, and resume procedures added |
| “Reviewed document” could be mistaken for implemented software certification | Explicit evidence scope and `NOT_EXECUTED` implementation gates |

## E.3 Checks performed on this artifact

The final assembly is checked for complete numbered requirement/invariant/edge-case/gate/runbook inventories, unique schema table names, balanced code fences, parseable JSON/YAML examples, parseable embedded Python, resolvable source references, valid generated section links, and absence of unfinished assembly placeholders. These are structural checks, not semantic proof of every requirement.

A separate pure-Python reference harness was executed with the following result:

| Executed reference check | Result |
|---|---|
| Unit cases for selected abstract contracts | 29 passed, 0 failures, 0 errors |
| Boolean authorization predicate | All 32,768 assignments across 15 required facts checked |
| Lost acknowledgement and retry identity | One effect under the fake provider's explicitly defined idempotency contract |
| Stale target and paused future authorization | Rejected in the reference model |
| Missing journal and unresolved conflict | Dispatch denied in the reference model |
| Budget reservation, unknown usage, settlement, and overage | Reference cases passed |
| Recovery preserving later edits and detecting overlap | Flat-field reference cases passed |
| Unsafe-design negative controls | Demonstrated repeated effects with a new retry identity and lost human edits with blind restore |

The exhaustive Boolean check is exhaustive only over that small predicate's truth assignments. It is not exhaustive exploration of Signal, a proof of all state transitions, a concurrency proof for PostgreSQL, or a guarantee against malicious provider behavior. The fake provider is intentionally specified to honor conditional writes and idempotency; actual providers must earn that certification through their own tests.

## E.4 Required but not executed for this document

| Implementation evidence | Status |
|---|---|
| Production PostgreSQL migrations, RLS, grants, and concurrency suite | NOT_EXECUTED |
| Temporal replay, activity retry, versioning, and recovery suite | NOT_EXECUTED |
| WordPress Bridge/database/plugin compatibility certification | NOT_EXECUTED |
| GitHub App and real CI/deployment contract certification | NOT_EXECUTED |
| Google production OAuth and complete customer onboarding journeys | NOT_EXECUTED |
| OpenAI held-out task-quality, injection, budget, and model-drift evaluations | NOT_EXECUTED |
| Browser accessibility, security, performance, and end-to-end acceptance | NOT_EXECUTED |
| Penetration testing, isolation testing, production load, and backup restoration | NOT_EXECUTED |

A PostgreSQL runtime was not available for executing the SQL in this environment. The reference SQL therefore remains a reviewed design example, not a tested migration. No real production site, customer connector, or OpenAI account was used to execute Signal operations during document preparation.

## E.5 Reproducible abstract reference harness

The following standard-library Python program is the reference harness used for the selected checks above. It is intentionally not production implementation code. Its flat-field recovery function is not an HTML, Gutenberg, or Git merge engine, and its lock-based budget model is not a substitute for database transaction tests.

```python
"""Reference-only checks for selected Signal specification contracts.

This is not Signal implementation code and does not certify PostgreSQL,
Temporal, OpenAI, a CMS, network fencing, or production concurrency.
Run with Python 3.11+ using only the standard library.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from threading import Lock, Thread
from typing import Any
import json
import unittest


GATE_FACTS = (
    "tenant_matches", "site_matches", "manifest_matches", "actor_authorized",
    "approval_current", "policy_current", "recipe_current", "binding_current",
    "not_paused", "lease_current", "provider_version_matches", "budget_reserved",
    "artifacts_durable", "journal_durable", "no_ambiguous_conflict",
)


def eligible(facts: dict[str, bool]) -> bool:
    """Missing, unknown, or false facts fail closed in this abstract predicate."""
    return all(facts.get(name) is True for name in GATE_FACTS)


class Conflict(Exception):
    pass


@dataclass
class ConditionalProvider:
    """A fake provider that is deliberately stronger than many real CMS APIs."""
    value: str = "before"
    version: int = 1
    effects: int = 0
    receipts: dict[str, tuple[int, str, int]] = field(default_factory=dict)

    def apply(self, op_id: str, expected: int, desired: str) -> int:
        if op_id in self.receipts:
            old_expected, old_desired, resulting_version = self.receipts[op_id]
            if (old_expected, old_desired) != (expected, desired):
                raise Conflict("idempotency identity reused with a different intent")
            return resulting_version
        if self.version != expected:
            raise Conflict("provider version changed")
        self.value = desired
        self.version += 1
        self.effects += 1
        self.receipts[op_id] = (expected, desired, self.version)
        return self.version


@dataclass
class Operation:
    op_id: str
    expected: int = 1
    desired: str = "after"
    status: str = "PLANNED"
    hold: int = 100
    live_verified: bool = False

    def dispatch(self, provider: ConditionalProvider, facts: dict[str, bool],
                 lose_ack: bool = False) -> None:
        if not eligible(facts):
            raise PermissionError("dispatch denied")
        self.status = "DISPATCHING"
        provider.apply(self.op_id, self.expected, self.desired)
        self.status = "OUTCOME_UNKNOWN" if lose_ack else "APPLIED"
        # Application is not live delivery, and usage may remain unresolved.

    def reconcile(self, provider: ConditionalProvider) -> None:
        if self.op_id in provider.receipts:
            self.status = "APPLIED"
        # An absent receipt is not assumed proof of failure in this model.


@dataclass
class Budget:
    limit: int
    reserved: int = 0
    spent: int = 0
    holds: dict[str, tuple[int, str]] = field(default_factory=dict)
    settled: dict[str, int] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def reserve(self, work_id: str, amount: int) -> bool:
        if amount < 0:
            raise ValueError("negative reservation")
        with self.lock:
            if work_id in self.holds:
                if self.holds[work_id][0] != amount:
                    raise Conflict("same work, different reservation")
                if self.holds[work_id][1] != "reserved":
                    raise Conflict("unresolved or settled work is not new authority")
                return True
            if self.spent + self.reserved + amount > self.limit:
                return False
            self.holds[work_id] = (amount, "reserved")
            self.reserved += amount
            return True

    def unknown(self, work_id: str) -> None:
        with self.lock:
            amount, _ = self.holds[work_id]
            self.holds[work_id] = (amount, "unresolved")

    def settle(self, work_id: str, actual: int) -> None:
        if actual < 0:
            raise ValueError("negative usage")
        with self.lock:
            if work_id in self.settled:
                if self.settled[work_id] != actual:
                    raise Conflict("settlement correction needs a new ledger entry")
                return
            amount, _ = self.holds[work_id]
            self.reserved -= amount
            self.spent += actual
            self.holds[work_id] = (amount, "settled")
            self.settled[work_id] = actual


def inverse_fields(before: dict[str, Any], signal_after: dict[str, Any],
                   current: dict[str, Any]) -> dict[str, Any]:
    """Flat-field reference only, not an HTML, block-tree, or Git merge engine."""
    missing = object()
    output = dict(current)
    for key in before.keys() | signal_after.keys():
        old = before.get(key, missing)
        after = signal_after.get(key, missing)
        now = current.get(key, missing)
        if old == after or now == old:
            continue
        if now != after:
            raise Conflict(f"later edit overlaps field {key}")
        if old is missing:
            output.pop(key, None)
        else:
            output[key] = old
    return output


class ReferenceContractTests(unittest.TestCase):
    def facts(self) -> dict[str, bool]:
        return dict.fromkeys(GATE_FACTS, True)

    def test_exhaustive_gate_truth_assignments(self) -> None:
        for bits in product((False, True), repeat=len(GATE_FACTS)):
            self.assertEqual(eligible(dict(zip(GATE_FACTS, bits))), all(bits))

    def test_missing_gate_fact_denies(self) -> None:
        for key in GATE_FACTS:
            facts = self.facts()
            del facts[key]
            self.assertFalse(eligible(facts))

    def test_unknown_fact_is_not_truthy_authority(self) -> None:
        facts = self.facts()
        facts["policy_current"] = "unknown"  # type: ignore[assignment]
        self.assertFalse(eligible(facts))

    def test_denied_gate_never_contacts_provider(self) -> None:
        for key in GATE_FACTS:
            provider = ConditionalProvider()
            facts = self.facts()
            facts[key] = False
            with self.assertRaises(PermissionError):
                Operation("operation-1").dispatch(provider, facts)
            self.assertEqual(provider.effects, 0)

    def test_stale_human_edit_rejected_by_provider(self) -> None:
        provider = ConditionalProvider(value="human edit", version=2)
        with self.assertRaises(Conflict):
            provider.apply("operation-1", 1, "after")
        self.assertEqual(provider.value, "human edit")

    def test_lost_ack_keeps_unknown_and_hold(self) -> None:
        op, provider = Operation("operation-1"), ConditionalProvider()
        op.dispatch(provider, self.facts(), lose_ack=True)
        self.assertEqual((op.status, op.hold, provider.effects),
                         ("OUTCOME_UNKNOWN", 100, 1))

    def test_reconcile_finds_applied_effect(self) -> None:
        op, provider = Operation("operation-1"), ConditionalProvider()
        op.dispatch(provider, self.facts(), lose_ack=True)
        op.reconcile(provider)
        self.assertEqual(op.status, "APPLIED")
        self.assertFalse(op.live_verified)

    def test_absent_receipt_does_not_prove_failure(self) -> None:
        op = Operation("operation-1", status="OUTCOME_UNKNOWN")
        op.reconcile(ConditionalProvider())
        self.assertEqual(op.status, "OUTCOME_UNKNOWN")

    def test_stable_retry_produces_one_effect(self) -> None:
        provider = ConditionalProvider()
        first = provider.apply("operation-1", 1, "after")
        second = provider.apply("operation-1", 1, "after")
        self.assertEqual((first, second, provider.effects), (2, 2, 1))

    def test_reused_identity_different_intent_denied(self) -> None:
        provider = ConditionalProvider()
        provider.apply("operation-1", 1, "after")
        with self.assertRaises(Conflict):
            provider.apply("operation-1", 1, "different")

    def test_pause_after_acceptance_cannot_recall_effect(self) -> None:
        op, provider = Operation("operation-1"), ConditionalProvider()
        op.dispatch(provider, self.facts(), lose_ack=True)
        paused = self.facts()
        paused["not_paused"] = False
        with self.assertRaises(PermissionError):
            Operation("operation-2", expected=2).dispatch(provider, paused)
        self.assertEqual(provider.effects, 1)
        self.assertEqual(provider.value, "after")

    def test_unknown_conflict_blocks_next_write(self) -> None:
        facts = self.facts()
        facts["no_ambiguous_conflict"] = False
        self.assertFalse(eligible(facts))

    def test_missing_journal_blocks_send(self) -> None:
        provider = ConditionalProvider()
        facts = self.facts()
        facts["journal_durable"] = False
        with self.assertRaises(PermissionError):
            Operation("operation-1").dispatch(provider, facts)
        self.assertEqual(provider.effects, 0)

    def test_budget_reservation_retry_is_idempotent(self) -> None:
        budget = Budget(100)
        self.assertTrue(budget.reserve("work", 60))
        self.assertTrue(budget.reserve("work", 60))
        self.assertEqual(budget.reserved, 60)

    def test_budget_retry_different_amount_conflicts(self) -> None:
        budget = Budget(100)
        budget.reserve("work", 60)
        with self.assertRaises(Conflict):
            budget.reserve("work", 80)

    def test_settled_hold_cannot_authorize_new_work(self) -> None:
        budget = Budget(100)
        budget.reserve("work", 60)
        budget.settle("work", 50)
        with self.assertRaises(Conflict):
            budget.reserve("work", 60)

    def test_unresolved_hold_does_not_authorize_repeat_call(self) -> None:
        budget = Budget(100)
        budget.reserve("work", 60)
        budget.unknown("work")
        with self.assertRaises(Conflict):
            budget.reserve("work", 60)

    def test_unknown_usage_retains_reservation(self) -> None:
        budget = Budget(100)
        budget.reserve("work", 60)
        budget.unknown("work")
        self.assertEqual(budget.reserved, 60)
        self.assertFalse(budget.reserve("another", 50))

    def test_actual_overage_is_recorded(self) -> None:
        budget = Budget(100)
        budget.reserve("work", 60)
        budget.settle("work", 120)
        self.assertEqual((budget.reserved, budget.spent), (0, 120))
        self.assertFalse(budget.reserve("another", 1))

    def test_duplicate_settlement_not_double_charged(self) -> None:
        budget = Budget(100)
        budget.reserve("work", 60)
        budget.settle("work", 50)
        budget.settle("work", 50)
        self.assertEqual((budget.reserved, budget.spent), (0, 50))

    def test_concurrent_reference_reservations_are_bounded(self) -> None:
        budget = Budget(100)
        results: list[bool] = []
        workers = [Thread(target=lambda i=i: results.append(
            budget.reserve(f"work-{i}", 60))) for i in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(sum(results), 1)
        self.assertEqual(budget.reserved, 60)

    def test_inverse_preserves_unrelated_later_edit(self) -> None:
        result = inverse_fields({"title": "old", "body": "before"},
                                {"title": "signal", "body": "before"},
                                {"title": "signal", "body": "human"})
        self.assertEqual(result, {"title": "old", "body": "human"})

    def test_inverse_overlapping_edit_conflicts_without_mutation(self) -> None:
        current = {"title": "human"}
        with self.assertRaises(Conflict):
            inverse_fields({"title": "old"}, {"title": "signal"}, current)
        self.assertEqual(current, {"title": "human"})

    def test_inverse_already_restored_is_noop(self) -> None:
        self.assertEqual(inverse_fields({"title": "old"},
                                       {"title": "signal"},
                                       {"title": "old"}), {"title": "old"})

    def test_inverse_added_field_removes_only_added_field(self) -> None:
        self.assertEqual(inverse_fields({}, {"description": "signal"},
                                        {"description": "signal", "body": "human"}),
                         {"body": "human"})

    def test_negative_control_blind_restore_loses_human_edit(self) -> None:
        before = {"title": "old", "body": "before"}
        current = {"title": "signal", "body": "human"}
        naive_restoration = dict(before)
        self.assertNotEqual(naive_restoration["body"], current["body"])

    def test_negative_control_new_operation_can_repeat_side_effect(self) -> None:
        provider = ConditionalProvider()
        provider.apply("original", 1, "after")
        provider.apply("incorrect-new-retry-id", 2, "after")
        self.assertEqual(provider.effects, 2)

    def test_aggregate_ctr_uses_counts_not_mean_of_ratios(self) -> None:
        clicks, impressions = [1, 10], [2, 100]
        correct = sum(clicks) / sum(impressions)
        naive = sum(c / i for c, i in zip(clicks, impressions)) / 2
        self.assertAlmostEqual(correct, 11 / 102)
        self.assertNotAlmostEqual(correct, naive)

    def test_restored_state_requires_fresh_authority(self) -> None:
        facts = self.facts()
        for key in ("binding_current", "lease_current", "approval_current"):
            facts[key] = False
        self.assertFalse(eligible(facts))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReferenceContractTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "scope": "abstract reference contracts only, not product implementation",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "gate_truth_assignments": 2 ** len(GATE_FACTS),
        "gate_facts": len(GATE_FACTS),
        "passed": result.wasSuccessful(),
    }
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
```


<a id="appendix-f"></a>
# Appendix F. Research references and verification date

Official primary sources were consulted on September 7, 2026. Source references support provider behavior and external constraints; proposed architecture, schema choices, safety defaults, and release gates are Signal design decisions. An official documentation page is not proof that a specific customer account has access or that an adapter implementation passes certification.

Provider documentation, product availability, limits, prices, and applicable rules can change. Recheck the relevant sources during release preparation and connector/model preflight. The MCP source is an explicitly pinned interoperability baseline, not a claim that it is the newest protocol revision.

**S01.** [OpenAI model catalog][S01]

**S02.** [OpenAI function calling][S02]

**S03.** [OpenAI structured outputs][S03]

**S04.** [OpenAI API data controls][S04]

**S05.** [OpenAI evaluation best practices][S05]

**S06.** [OpenAI rate limits][S06]

**S07.** [OpenAI API pricing][S07]

**S08.** [OpenAI embeddings][S08]

**S09.** [Temporal self-hosted deployment][S09]

**S10.** [Temporal Activity execution][S10]

**S11.** [Temporal workflow messages][S11]

**S12.** [Temporal workflows][S12]

**S13.** [PostgreSQL row security][S13]

**S14.** [PostgreSQL transaction isolation][S14]

**S15.** [PostgreSQL partitioning][S15]

**S16.** [PostgreSQL routine vacuuming][S16]

**S17.** [pgBackRest user guide][S17]

**S18.** [pgvector official repository and reference][S18]

**S19.** [RFC 9700: OAuth 2.0 Security Best Current Practice][S19]

**S20.** [Keycloak production configuration][S20]

**S21.** [OpenBao integrated storage][S21]

**S22.** [MCP authorization, pinned November 25, 2025 specification][S22]

**S23.** [OWASP server-side request forgery prevention][S23]

**S24.** [OWASP LLM prompt injection prevention][S24]

**S25.** [Open Policy Agent bundle management][S25]

**S26.** [RFC 9309: Robots Exclusion Protocol][S26]

**S27.** [RFC 9110: HTTP Semantics][S27]

**S28.** [RFC 8785: JSON Canonicalization Scheme][S28]

**S29.** [Google robots.txt introduction][S29]

**S30.** [Google web search spam policies][S30]

**S31.** [Google structured-data policies][S31]

**S32.** [Google sitemap construction guidance][S32]

**S33.** [Google canonicalization guidance][S33]

**S34.** [Google localized-version guidance][S34]

**S35.** [Search Console Search Analytics query API][S35]

**S36.** [Google Analytics Data API quotas][S36]

**S37.** [Google Analytics response metadata][S37]

**S38.** [Chrome UX Report API][S38]

**S39.** [Core Web Vitals][S39]

**S40.** [WordPress custom REST endpoints][S40]

**S41.** [WordPress REST authentication][S41]

**S42.** [Yoast REST API][S42]

**S43.** [GitHub pull request REST API][S43]

**S44.** [GitHub webhook signature validation][S44]

**S45.** [GitHub Actions secure use][S45]

**S46.** [Telegram Bot API][S46]

**S47.** [Google server-side OAuth][S47]

**S48.** [Google OAuth production-readiness policy compliance][S48]

**S49.** [Webflow CMS publishing behavior][S49]

**S50.** [Google URL Inspection API][S50]

**S51.** [Google Indexing API use and eligibility][S51]

**S52.** [FTC Consumer Reviews and Testimonials Rule questions and answers][S52]

**S53.** [W3C Web Content Accessibility Guidelines 2.2][S53]

**S54.** [Cloudflare R2 pricing][S54]

**S55.** [gVisor official documentation][S55]

**S56.** [OpenTelemetry overview][S56]

**S57.** [PostgreSQL CREATE INDEX reference][S57]


## Reference links

[S01]: https://developers.openai.com/api/docs/models
[S02]: https://developers.openai.com/api/docs/guides/function-calling
[S03]: https://developers.openai.com/api/docs/guides/structured-outputs
[S04]: https://developers.openai.com/api/docs/guides/your-data
[S05]: https://developers.openai.com/api/docs/guides/evaluation-best-practices
[S06]: https://developers.openai.com/api/docs/guides/rate-limits
[S07]: https://developers.openai.com/api/docs/pricing
[S08]: https://developers.openai.com/api/docs/guides/embeddings
[S09]: https://docs.temporal.io/self-hosted-guide/deployment
[S10]: https://docs.temporal.io/activity-execution
[S11]: https://docs.temporal.io/sending-messages
[S12]: https://docs.temporal.io/workflows
[S13]: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
[S14]: https://www.postgresql.org/docs/current/transaction-iso.html
[S15]: https://www.postgresql.org/docs/current/ddl-partitioning.html
[S16]: https://www.postgresql.org/docs/current/routine-vacuuming.html
[S17]: https://pgbackrest.org/user-guide.html
[S18]: https://github.com/pgvector/pgvector
[S19]: https://www.rfc-editor.org/rfc/rfc9700.html
[S20]: https://www.keycloak.org/server/configuration-production
[S21]: https://openbao.org/docs/concepts/integrated-storage/
[S22]: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
[S23]: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
[S24]: https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
[S25]: https://www.openpolicyagent.org/docs/management-bundles
[S26]: https://www.rfc-editor.org/rfc/rfc9309.html
[S27]: https://www.rfc-editor.org/rfc/rfc9110.html
[S28]: https://www.rfc-editor.org/rfc/rfc8785.html
[S29]: https://developers.google.com/search/docs/crawling-indexing/robots/intro
[S30]: https://developers.google.com/search/docs/essentials/spam-policies
[S31]: https://developers.google.com/search/docs/appearance/structured-data/sd-policies
[S32]: https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap
[S33]: https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls
[S34]: https://developers.google.com/search/docs/specialty/international/localized-versions
[S35]: https://developers.google.com/webmaster-tools/v1/searchanalytics/query
[S36]: https://developers.google.com/analytics/devguides/reporting/data/v1/quotas
[S37]: https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData
[S38]: https://developer.chrome.com/docs/crux/api
[S39]: https://web.dev/articles/vitals
[S40]: https://developer.wordpress.org/rest-api/extending-the-rest-api/adding-custom-endpoints/
[S41]: https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/
[S42]: https://developer.yoast.com/customization/apis/rest-api/
[S43]: https://docs.github.com/en/rest/pulls/pulls
[S44]: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
[S45]: https://docs.github.com/en/actions/reference/security/secure-use
[S46]: https://core.telegram.org/bots/api
[S47]: https://developers.google.com/identity/protocols/oauth2/web-server
[S48]: https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance
[S49]: https://developers.webflow.com/data/docs/working-with-the-cms/publishing
[S50]: https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect
[S51]: https://developers.google.com/search/apis/indexing-api/v3/using-api
[S52]: https://www.ftc.gov/business-guidance/resources/consumer-reviews-testimonials-rule-questions-answers
[S53]: https://www.w3.org/TR/WCAG22/
[S54]: https://developers.cloudflare.com/r2/pricing/
[S55]: https://gvisor.dev/docs/
[S56]: https://opentelemetry.io/docs/what-is-opentelemetry/
[S57]: https://www.postgresql.org/docs/current/sql-createindex.html
