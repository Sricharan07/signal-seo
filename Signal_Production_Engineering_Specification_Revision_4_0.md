# Signal
## Production Engineering Specification and Build Contract

**Revision:** 4.0, autonomous SEO employee amendment
**Prepared:** September 24, 2026
**Amends:** Revision 3.2 of `Signal_Production_Engineering_Specification_Revision_3_2.md`, which remains normative wherever this revision does not change it. Revision 3.1 (`Signal_Production_Engineering_Specification_Corrected.md`) and Revision 3.0 (`Signal_Production_Engineering_Specification_Final.md`) remain preserved baselines.
**Product:** Open-source, self-hostable autonomous SEO and AI-search employee, with a paid managed SaaS edition
**Document status:** Product direction, architecture additions, contracts, and release sequence. This is not an implemented or independently certified product.

**Revision scope:** Changes Signal's product from a supervised one-change pilot into an autonomous SEO employee comparable in output to commercial autonomous SEO agents, while keeping every Revision 3.2 safety, identity, tenancy, evidence, and recovery control. It brings into scope new-article production, autonomous pull requests under standing authorization, a Jev confidence-gated decision layer, a sandboxed browser agent, AI-search visibility, Slack alongside Telegram, Bing Webmaster Tools, IndexNow, brand documents, Google Analytics 4, and Webflow/WordPress delivery. It replaces the Core V1 milestone order with four customer-usable releases (R1–R4). It establishes no new runtime test result and grants no production authority.

> Signal must earn production authority through evidence. Autonomy is a standing authorization that a human granted, bounded by deterministic policy and budgets. A model's confidence can reduce what Signal does on its own; it can never enlarge it.

## Contents

- [1. Amendment model and precedence](#section-1)
- [2. Product contract](#section-2)
- [3. Requirements](#section-3)
- [4. Invariants](#section-4)
- [5. Architecture additions](#section-5)
- [6. Autonomy and authority](#section-6)
- [7. Jev decision layer](#section-7)
- [8. Frontier model providers](#section-8)
- [9. Browser agent](#section-9)
- [10. Business Brain and memory](#section-10)
- [11. Data sources](#section-11)
- [12. Connectors](#section-12)
- [13. Workforce](#section-13)
- [14. Content production and publication](#section-14)
- [15. AI-search visibility](#section-15)
- [16. Measurement and reporting](#section-16)
- [17. Release sequence](#section-17)
- [18. Distribution: open source and managed SaaS](#section-18)
- [19. Engineering process tiers](#section-19)
- [20. Additional edge cases](#section-20)
- [21. Amendments to Revision 3.2](#section-21)
- [22. Evidence status](#section-22)

---

<a id="section-1"></a>
## 1. Amendment model and precedence

Revision 4.0 is an amending revision. Read it together with
[Revision 3.2](Signal_Production_Engineering_Specification_Revision_3_2.md):

1. Where Revision 4.0 states a requirement, invariant, contract, or sequence, it
   takes precedence over Revision 3.2.
2. Where Revision 4.0 is silent, Revision 3.2 remains the normative contract,
   including its schemas, APIs, state machines, safety invariants, edge cases,
   runbooks, and release gates.
3. [Section 21](#section-21) lists every Revision 3.2 section this revision changes.
   A section not listed there is unchanged.
4. Revisions 3.2, 3.1, and 3.0 stay byte-for-byte preserved. Later material
   changes require a new explicit revision and a superseding ADR.

The product requirements for this revision are in the
[product requirements document](docs/product/prd.md). The implementation order is
in the [roadmap](docs/implementation/roadmap.md). The
[implementation status](docs/implementation/status.md) is the only source of truth
for what currently exists.

<a id="section-2"></a>
## 2. Product contract

### 2.1 The job

Grow a company's organic and AI-search traffic every week without the company
hiring an SEO person, and never ship anything the owner would not have approved.

Signal is an SEO employee, not an SEO tool. It learns the business, researches,
plans, writes, fixes, publishes through controlled channels, verifies what went
live, measures the effect, and reports. It finds its own work. It asks only when
its confidence is low, the stakes are high, or policy requires a human.

### 2.2 Customer

| Attribute | Contract |
| --- | --- |
| Primary customer | Seed to Series B startups and small companies without a dedicated SEO hire |
| Buyer | Founder, head of marketing, or growth lead |
| First supported site | Marketing site or blog stored in a GitHub repository (for example Next.js, Astro, Hugo, Eleventy) |
| Later sites | Webflow and WordPress (R4) |
| Replaced spend | Freelancer or agency at roughly $3,000–8,000 per month, or no SEO work at all |

### 2.3 Positioning

Signal delivers the output of an autonomous SEO agent with proof and control:
every change shows why it was made, what authorized it, what went live, and what
it did to traffic. Differentiators that the implementation must preserve:

- evidence linked to every claim, finding, and change;
- owner-controlled autonomy per work type, with confidence-gated escalation;
- measured before/after results per change;
- self-serve onboarding and transparent pricing;
- an open-source engine that a customer can run without Signal's services.

### 2.4 What the customer receives

| Moment | Deliverable |
| --- | --- |
| Onboarding (target under two hours) | Verified site; connected GSC, GitHub, and Bing Webmaster Tools; uploaded brand documents; Business Brain; SEO Baseline; 90-day Strategy |
| Every week | New articles in brand voice, refreshed decaying pages, technical fixes as pull requests, AI-search visibility check, weekly report |
| Always | Inbox of work awaiting a decision, confidence-gated autonomy, complete history from evidence to measured result |

<a id="section-3"></a>
## 3. Requirements

Revision 3.2 requirements REQ-001 to REQ-018 remain in force except REQ-016, which
is amended below. REQ-019 to REQ-027 are added.

| Requirement | Binding implementation requirement | Release evidence |
|---|---|---|
| REQ-016 (amended): Frontier reasoning | Frontier models (OpenAI today; Anthropic Claude permitted) behind Signal's own reasoner boundary, selected per task by release configuration | Per-provider capability, privacy, cost, and quality evaluations |
| REQ-019: Autonomous weekly operation | Scheduled durable loop that finds, prioritizes, performs, and reports work under standing authorization, budgets, and pause | Multi-week schedule, pause/resume, budget-exhaustion, and restart tests |
| REQ-020: Confidence-gated decisions | Jev decision layer with typed outputs, per-work-type thresholds, deterministic policy precedence, and a fallback path | Threshold, fallback, calibration, and authority-cannot-increase tests |
| REQ-021: Business Brain | Crawl- and document-derived business facts, brand voice, products, audiences, and competitors with provenance and owner correction | Extraction accuracy, provenance, correction, and isolation tests |
| REQ-022: Content production | Briefs, articles, and refreshes grounded in approved business facts, delivered as pull requests within volume limits | Claim-grounding, originality, policy, and volume-limit evaluations |
| REQ-023: Browser agent | Sandboxed headless browser with Jev action selection, deny-by-default actions, and the crawler's egress controls | Egress, action-denial, injection, and replay tests |
| REQ-024: AI-search visibility | Citation tracking through official assistant APIs and fixes proposed from the results | Provider-contract, parsing, and trend tests |
| REQ-025: Connector breadth | The connector set in [section 12](#section-12), each least-privilege, site-bound, revocable, and credential-isolated | Per-connector contract, revocation, wrong-binding, and isolation tests |
| REQ-026: Measured outcomes | Before/after measurement for every shipped change at 7, 28, and 90 days, with missing data explicit | Measurement correctness and missing-data tests |
| REQ-027: Open source and managed SaaS | One engine that runs self-hosted without Signal-operated services, and a managed multi-tenant edition | Self-hosted install test with owner-supplied keys; SaaS isolation and billing tests |

<a id="section-4"></a>
## 4. Invariants

Revision 3.2 invariants INV-001 to INV-025 remain acceptance conditions without
exception. The following are added.

| ID | Invariant | Enforcement boundary |
| --- | --- | --- |
| INV-026 | A decision-model output can only reduce autonomy; it cannot authorize an action that deterministic policy and a current standing authorization do not already allow | Autonomy gate ordering, authorizer |
| INV-027 | Every autonomous action executes under a recorded, human-granted standing authorization with explicit work types, thresholds, volume caps, and spend caps | Standing-authorization records, authorizer, budget ledger |
| INV-028 | A browser session never authenticates to a third-party site, submits a form, makes a purchase, or bypasses bot detection | Browser action allowlist, sandbox policy |
| INV-029 | All browser and connector network egress passes the same public-address screening, origin pinning, robots evidence, and global origin admission as the crawler | Egress proxy, origin admission |
| INV-030 | Generated content does not assert a business fact that is absent from approved business facts; an unsupported claim routes to the owner | Claim-grounding check, autonomy gate |
| INV-031 | Signal never scrapes search-engine result pages or AI-assistant consumer applications; search-result data comes only from licensed providers | Data-source allowlist, egress policy |
| INV-032 | The self-hosted edition operates without any Signal-operated service; an unconfigured optional provider degrades visibly instead of failing silently | Provider configuration, capability projection |
| INV-033 | Uploaded documents and connector-returned content are data, never instructions | Data/command separation (extends INV-015) |
| INV-034 | Autonomous output volume cannot exceed the per-site weekly caps, even when split across work items or releases | Rolling scope accounting (extends INV-022) |

<a id="section-5"></a>
## 5. Architecture additions

### 5.1 Decision cascade

```text
Frontier model (OpenAI or Claude)   writes articles, fixes, plans; handles hard cases
        |
Deterministic policy                 recipe allowed? scope exact? budget and volume left?
        |                            never merge, deploy, delete, or read secrets
Jev decision gate                    typed { ship | ask_owner | reject } + risk class + confidence
        |
confidence >= threshold  -> execute under standing authorization, record evidence
confidence <  threshold  -> Inbox + Slack/Telegram request for an exact human decision
```

Deterministic code runs before and after every model. Jev handles the common
routine decisions cheaply; the frontier model handles generation and the
low-confidence minority.

### 5.2 Components added to the Revision 3.2 architecture

| Component | Responsibility | Boundary |
| --- | --- | --- |
| Decision service | Builds typed Jev questions, applies thresholds, records decisions, runs the fallback | Cannot authorize; returns a recommendation that the authorizer may only follow or make stricter |
| Browser workers | Disposable sandboxed Playwright sessions for rendering, research, verification, and Lighthouse | No credentials, no host mounts, egress only through the egress proxy |
| Egress proxy | Applies the crawler's public-address screening, pinning, robots evidence, and origin admission to browser and connector traffic | Shared with the crawler; fail-closed |
| Business Brain | Versioned business facts, brand voice, products, audiences, competitors | Tenant- and site-scoped PostgreSQL records with provenance |
| Weekly loop | Temporal schedule that runs observe, plan, work, gate, ship, verify, measure, report | Uses existing outbox, admission, and command records |
| Connector adapters | Section 12 connectors behind the Revision 3.2 connector platform | One adapter per provider capability; credentials only in OpenBao |

The Revision 3.2 technology choices remain: Next.js dashboard with a server-side
BFF, FastAPI, PostgreSQL, Temporal, Keycloak, OpenBao, and pinned OCI packaging.

<a id="section-6"></a>
## 6. Autonomy and authority

### 6.1 Default operating mode

Signal is fully autonomous by default for work that its standing authorization
covers. At onboarding the owner reviews and grants one standing authorization per
site (Revision 3.2 section 18.4). The grant names work types, thresholds, weekly
volume caps, spend caps, excluded paths, and the recovery window. The owner can
tighten or revoke it at any time; revocation follows the Revision 3.2 authority
restriction journal.

### 6.2 Work types

| Work type | Revision 3.2 approval class | Default mode | Threshold policy |
| --- | --- | --- | --- |
| Research, audits, crawl, visibility checks | A0 | Autonomous | Budget only |
| Drafts and sandbox patches | A1 | Autonomous | Budget only |
| Pull request: metadata, alt text, structured data, internal links, broken links, sitemap/robots hygiene | A2 | Autonomous PR | Low threshold |
| Pull request: content refresh of an existing page | A2 | Autonomous PR | High threshold |
| Pull request: new article | A2 | Autonomous PR | High threshold and weekly volume cap |
| Anything touching pricing, legal, medical, financial, or product claims | A2 or higher | Always ask | Not eligible for autonomy |
| CMS draft (R4) | A2 | Autonomous | High threshold |
| CMS publish (R4) | A3 | Ask unless separately granted after recipe eligibility | Owner grant required |
| Indexing, canonical, redirect, shared-template, or large-impact changes | A4 | Always ask with step-up | Not eligible for autonomy |
| Destructive or uncertified operations | A5 | Disabled | — |

Signal never merges, deploys, pushes to a default branch, deletes content, or edits
CI workflows. "Ship" means opening a pull request, or from R4 a CMS draft or an
owner-granted publish.

### 6.3 Gate order

1. Deterministic policy evaluates recipe eligibility, exact scope, current authority,
   budgets, weekly volume, and excluded paths. A denial ends the decision.
2. The Jev gate classifies the sealed revision and returns `ship`, `ask_owner`, or
   `reject`, a risk class, and a confidence.
3. The authorizer executes only when policy allows it, the standing authorization
   covers the work type, Jev returns `ship`, and confidence meets the work type's
   threshold. Any other combination produces an exact human decision request.
4. Every step is recorded as immutable evidence before any external operation.

### 6.4 Calibration and override

Owner approvals, rejections, and edits of escalated work are recorded as
calibration evidence. Threshold changes are versioned policy changes that the
owner or an operator approves; a model cannot change a threshold. The owner can set
any work type to "always ask".

<a id="section-7"></a>
## 7. Jev decision layer

### 7.1 Model

[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) by TypeSafe AI
is a "System One" model. It returns typed answers with probabilities and calibrated
confidence rather than generated text. Documented question types are `Choice` (up
to 255 options), `Noul` (yes/no probability), and `Score` (2 to 10 levels).
Documented limits that Signal's contracts respect: text or JSON input only, about
64,000 tokens shared across the questions of one call, about 32,000 tokens per
question, and 1,200 requests per minute.

### 7.2 Uses

| Decision | Question type |
| --- | --- |
| Ship, ask the owner, or reject a sealed revision | Choice with confidence |
| Next browser action | Choice over at most 255 interactive elements |
| Page type for the Business Brain (product, pricing, blog, docs, legal, other) | Choice |
| Crawl URL priority | Score |
| Finding validity and severity | Noul and Score |
| Keyword intent and cluster assignment | Choice |
| Prompt-injection presence in fetched or uploaded text | Noul |
| Draft claim absent from approved business facts | Noul, escalated to a frontier model when uncertain |
| Task routing to a role and model tier | Choice |

### 7.3 Decision record

Every Jev call and every fallback decision stores an immutable record. Example:

```json
{
  "decision_id": "3f6a2a52-6d2f-4b58-9d0e-6a1f3a4b7c10",
  "site_id": "0b9d1f7e-3c55-4a8b-8d6e-2f3c4b5a6d7e",
  "purpose": "autonomy_gate",
  "provider": "typesafe",
  "model": "jev",
  "question_schema_sha256": "5e1c2f0d9a7b3c4e6f8a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60",
  "input_sha256": "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b",
  "subject_revision_sha256": "1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d",
  "answer": "ship",
  "probabilities": { "ship": 0.94, "ask_owner": 0.05, "reject": 0.01 },
  "confidence": 0.93,
  "threshold": 0.9,
  "threshold_policy_version": "autonomy-thresholds-1.0.0",
  "outcome": "authorized_by_standing_grant",
  "fallback_used": false,
  "decided_at": "2026-09-24T12:00:00Z"
}
```

### 7.4 Fallback

The decision service sits behind a typed interface. When Jev is unavailable,
rate-limited, returns an invalid answer, or is not configured (for example in a
self-hosted install without a Jev key), the fallback uses frontier-model structured
classification plus deterministic rules. The fallback may produce `ask_owner` or
`reject` freely; it produces `ship` only for work types whose deterministic rules
alone are sufficient. A fallback decision is labelled as such in evidence and in
the dashboard.

<a id="section-8"></a>
## 8. Frontier model providers

Revision 3.2 section 14 is amended from a single OpenAI brain to a provider
boundary. OpenAI remains the implemented provider. Anthropic Claude may be added
behind the same adapter contract. Model selection per task is release
configuration, not a runtime model decision. Every provider keeps the Revision 3.2
controls: credentials in OpenBao, durable intent before the call, exact prompt,
input, and output identity, usage and cost records, and no tool that can mint or
exercise authority.

<a id="section-9"></a>
## 9. Browser agent

### 9.1 Operation

A planner (frontier model) sets a bounded goal. For each step the page's
accessibility tree is reduced to its interactive elements. Jev selects the next
element as a `Choice`. A low-confidence step returns to the planner, which may
request a screenshot for a vision-capable frontier model. Because Jev can only
choose among listed elements, page text cannot make it invent an action.

### 9.2 Allowed purposes

- render client-side pages as a search engine would;
- read competitor pages for positioning, pricing, features, and content;
- verify a merged and deployed change on the live site;
- run Lighthouse and collect Core Web Vitals lab data;
- read product, documentation, and review pages during onboarding.

### 9.3 Prohibited actions

Enforced by the browser action allowlist, not by model instructions: signing in,
entering any credential, submitting forms, purchasing, downloading executables,
solving or bypassing CAPTCHAs or bot detection, scraping search-engine results or
AI-assistant consumer applications, and ignoring robots evidence or origin rate
limits.

### 9.4 Isolation and evidence

Each session runs in a disposable, non-root, read-only container with no host
mounts and no credentials. All traffic leaves through the egress proxy
([INV-029](#section-4)). Each step records the goal, the element-list digest, the
Jev choice and confidence, the resulting URL, and a snapshot digest.

<a id="section-10"></a>
## 10. Business Brain and memory

The Business Brain extends Revision 3.2 section 13. It holds versioned business
facts (products, pricing statements, audiences, positioning, proof points), a
brand voice profile, competitors, and approved claims. Every fact records its
source (page evidence, uploaded document, owner statement) and time. The owner can
correct or remove any fact; corrections supersede rather than overwrite. Generated
content may assert only approved facts ([INV-030](#section-4)).

<a id="section-11"></a>
## 11. Data sources

Signal builds every data source that it legally and practically can, and buys only
data that would require scraping search engines or crawling the whole web.

| Need | Source | Cost |
| --- | --- | --- |
| Site crawl and technical audit | Signal crawler and browser agent | Compute |
| Own-site rankings, clicks, impressions, queries | Google Search Console API | Free |
| Own-site Bing performance and inbound links | Bing Webmaster Tools API | Free |
| AI-search visibility | Official OpenAI, Perplexity, and Gemini APIs with web search | Model usage |
| Keyword ideas | GSC and Bing queries, the crawl, competitor pages, frontier-model expansion | Model usage |
| Page speed and Core Web Vitals | PageSpeed Insights API and Lighthouse | Free / compute |
| Traffic and conversions (R4) | Google Analytics Data API | Free |
| Ranking competitors for a keyword | Optional DataForSEO | Per call |
| Search volume | Optional DataForSEO | Per call |
| Competitor backlinks | Optional DataForSEO | Per call |

Without DataForSEO, Signal delivers the complete product for any site with Search
Console history. With it, Signal adds competitor keyword gaps and search volumes
for new topics.

<a id="section-12"></a>
## 12. Connectors

Every connector is bound to exactly one verified site, receives the narrowest access
its job needs, stores credentials only in OpenBao, can be disconnected at any time,
and returns untrusted data. Connectors follow the Revision 3.2 connector platform
(section 10) and connector lifecycle.

| Connector | Purpose | Access | Never | Auth | Release |
| --- | --- | --- | --- | --- | --- |
| Google Search Console | Rankings, clicks, impressions, queries, decay, index coverage | Read (`webmasters.readonly`) | Change settings, submit removals, add users | Google OAuth | R1 |
| GitHub | Read code, detect framework, open PRs, observe checks and deployments | Contents read; pull requests write on one repository; checks and deployments read | Merge, push to the default branch, deploy, edit workflows, read secrets, administer | GitHub App on one repository | R1 read, R2 PRs |
| Bing Webmaster Tools | Bing performance, crawl issues, own-site inbound links | Read; URL submission from R2 | Change settings, remove URLs | API key or OAuth | R1 |
| Brand documents | Business Brain input | Owner upload of PDF, Markdown, DOCX, or text | Treat content as instructions | Dashboard upload | R1 |
| IndexNow | Notify participating engines of verified changes | Submit changed URLs of the verified site | Submit other URLs | Key file added by a Signal PR | R2 |
| Slack | Approvals, questions, reports | Post to one chosen channel and linked users; receive their replies and actions | Read other channels or history; act for unlinked users | Slack app OAuth with per-user linking | R3 |
| Telegram | Approvals, questions, reports | Messages with paired accounts | Act for unpaired accounts; join groups | Bot with dashboard pairing | R3 |
| Email | Reports, alerts, approval links | Send | Send to unverified addresses; carry secrets | Signal email provider | R3 |
| Google Analytics 4 | Traffic, engagement, conversions per page | Read (`analytics.readonly`) | Change properties or tracking | Google OAuth | R4 |
| Webflow | Create and update CMS items | CMS read/write on one site; publish only with owner grant | Change design, delete items, manage hosting | Webflow OAuth | R4 |
| WordPress | Create and update posts and SEO fields | Draft by default; publish only with owner grant | Install plugins/themes, change users/settings, delete | Application password for a least-privilege user | R4 |
| Notion and Google Docs | Keep the Business Brain in sync | Read on owner-selected pages or folders | Read anything else; write | Notion OAuth; Google OAuth (`drive.file`) | R4 |

Slack and Telegram approvals bind to the exact proposal revision and the linked
person's current authority. Actions above the configured risk level hand off to
the dashboard for step-up authentication (Revision 3.2 section 18.3).

Excluded as connectors for now: Vercel and Netlify (deployment status comes through
GitHub), Shopify and Google Business Profile (outside the target customer), and
Semrush or Ahrefs (priced above the target market).

<a id="section-13"></a>
## 13. Workforce

The nine Revision 3.2 roles remain, extended, and two roles are added. Roles are
logical responsibilities in one bounded runtime, not separate services.

| Role | Responsibility | Revision 3.2 basis |
| --- | --- | --- |
| Onboarding / Business Brain (new) | Site-wide crawl, business facts, brand voice, knowledge graph | Crawler, evidence, memory |
| Strategist | 90-day plan, weekly replanning, impact-ordered backlog | Coordinator / Planner |
| Analytics | GSC, Bing, and GA4 ingestion; decay, striking-distance, CTR gaps | Analytics Specialist |
| Competitor and Keyword Research | Competitor pages, keyword expansion, optional DataForSEO gaps | Competitor Research Specialist |
| Content Writer (expanded) | Briefs, articles, refreshes, internal links, structured data, grounded in approved facts | Content Strategy Specialist |
| Technical SEO | Titles, descriptions, headings, canonicals, structured data, links, sitemaps, robots | Technical SEO Specialist |
| Performance | Core Web Vitals and page-speed findings | Performance Specialist |
| AI-Search Visibility (new) | Citation tracking and fixes | New |
| Publisher | GitHub PRs; Webflow and WordPress from R4 | Repository Implementer |
| Reviewer / Verifier | Independent review before gating, live verification, measurement | Independent Reviewer / Verifier |
| Communicator | Slack, Telegram, email, and dashboard reports and requests | Communicator |

Roles ship in release order ([section 17](#section-17)); the Revision 3.2
requirement that all nine roles exist before any customer use is removed.

<a id="section-14"></a>
## 14. Content production and publication

New-page publication is brought into scope for repository delivery.

- A new article is a pull request that adds one content file (and any required
  index or sitemap entry) in the repository's existing content format.
- Every article has a brief with target intent, sources, internal links, and the
  approved business facts it relies on.
- Claims are checked against approved business facts ([INV-030](#section-4)).
  Unsupported claims, regulated subjects, and comparisons that name competitors
  route to the owner.
- Weekly volume caps per site apply to new articles and refreshes
  ([INV-034](#section-4)). The default cap is conservative and owner-adjustable
  within platform limits, so autonomous publication cannot become scaled low-value
  content.
- Content must be original. Competitor text is research input only; copying or
  close paraphrasing is rejected by review.
- Bulk generation, programmatic page sets, and mass redirects remain excluded.

<a id="section-15"></a>
## 15. AI-search visibility

For a versioned set of target questions per site, Signal queries the official
OpenAI, Perplexity, and Gemini APIs with web search enabled, parses cited sources,
and records whether the site is cited, which page, and which competitors appear.
API answers can differ from consumer applications; reports state the provider,
model, and date of each observation. R1 establishes the baseline; R4 adds an agent
that proposes content and structured-data changes to earn citations.

<a id="section-16"></a>
## 16. Measurement and reporting

Every shipped change records a pre-change baseline and is measured at 7, 28, and 90
days using GSC, Bing, and later GA4. Missing or partial data stays explicit
(INV-018). Weekly reports state what shipped, what it did, what is next, and what
needs a decision, through the dashboard, email, Slack, and Telegram.

<a id="section-17"></a>
## 17. Release sequence

Four releases replace the Core V1 milestone order of Revision 3.2 sections 1.4 and
33. Each release is usable by a real customer on its own. Existing Milestone 1 and
Milestone 2 foundation work carries forward into R1.

| Release | Customer outcome | Scope |
| --- | --- | --- |
| R1 Insight | "It understands my business and told me exactly what to do." | Full-site crawl, egress proxy, sandboxed browser, Jev decision service, Business Brain with document upload, GSC and Bing bindings, AI-visibility baseline, optional DataForSEO, SEO Baseline, 90-day Strategy |
| R2 Work | "It wrote the articles and fixed the pages." | Content Writer, Technical SEO recipes, Inbox, GitHub PR delivery, live verification, IndexNow |
| R3 Employee | "It runs every week and shows me what it moved." | Weekly loop, standing authorization with the Jev gate, autonomous PRs, Slack, Telegram, email, reports, per-change measurement |
| R4 AI Search and Breadth | "It gets us cited, and it works on Webflow." | AI-visibility agent, Webflow and WordPress delivery, GA4, Notion and Google Docs sync |

A release is admitted only when every capability it enables passes its applicable
Revision 3.2 gates (section 32) and the edge cases in [section 20](#section-20),
with real-provider evidence where the contract depends on the provider. A
capability that is not admitted stays visibly unavailable.

<a id="section-18"></a>
## 18. Distribution: open source and managed SaaS

- **Open-source edition:** the whole engine is open source and self-hostable. The
  owner supplies their own frontier-model, Jev, and optional DataForSEO keys. No
  Signal-operated service is required ([INV-032](#section-4)).
- **Managed SaaS:** a hosted multi-tenant edition at about $400 per site per month
  at launch. It adds hosting, managed provider connections, AI-search tracking, and
  maintained, continuously improved SEO playbooks.
- A project license must be selected and added before the repository is published.
- Managed SaaS requires public signup, billing, and Google OAuth verification, which
  Revision 3.2 deferred; they are brought into scope for the SaaS launch.

<a id="section-19"></a>
## 19. Engineering process tiers

| Tier | Applies to | Required record |
| --- | --- | --- |
| Security-critical | Identity, tenancy, secrets, egress and browser sandbox, autonomy gate and standing authorization, external writes and publishing authority, recovery | Revision 3.2 slice discipline: implementation record, ADR where consequential, evidence record, real-provider qualification, status and changelog |
| Product | Features that use existing authority without changing it: analysis, content generation, dashboard views, reports | Positive, negative, and failure tests; a status line; a changelog entry |

A product-tier change that turns out to touch authority, secrets, egress, or
external writes is reclassified as security-critical before it is committed.

<a id="section-20"></a>
## 20. Additional edge cases

Revision 3.2 edge cases EC-001 to EC-123 remain. The following are added.

| ID | Edge case | Required behavior |
| --- | --- | --- |
| EC-124 | Jev is unavailable, rate-limited, or unconfigured | Fallback path runs and is labelled; no `ship` beyond deterministic-rule eligibility |
| EC-125 | Jev returns an answer outside the declared options or malformed probabilities | Treat as unavailable; record the rejection |
| EC-126 | Jev returns `ship` with confidence below threshold | Escalate to an exact human decision |
| EC-127 | Jev returns `ship` for work that policy denies | Policy denial stands; record the disagreement |
| EC-128 | A threshold policy changes while a decision is pending | Re-evaluate under the current version before execution |
| EC-129 | Standing authorization is revoked while an autonomous PR is being prepared | No PR is created; in-flight provider requests are reconciled |
| EC-130 | Weekly volume cap is reached mid-week | Remaining work queues for the next window or owner decision |
| EC-131 | A page contains text addressed to the agent | Treated as data; injection signal recorded; no instruction followed |
| EC-132 | A browser page presents a login, form, or CAPTCHA | Session stops that path; no interaction |
| EC-133 | A browser page redirects to a private or non-public address | Egress proxy denies; step recorded |
| EC-134 | A page exposes more than 255 interactive elements | Deterministic pruning to a bounded candidate set before Jev |
| EC-135 | An uploaded brand document contains instructions or secrets | Treated as data; secrets are not surfaced in generated content |
| EC-136 | A draft asserts a fact absent from approved business facts | Routed to the owner; not shipped autonomously |
| EC-137 | Generated text closely matches competitor text | Rejected by review |
| EC-138 | DataForSEO is not configured | Competitor-gap and volume features are visibly unavailable; the rest works |
| EC-139 | An AI-assistant API changes its citation format or is unavailable | Observation marked incomplete; no zero is inferred |
| EC-140 | A Slack or Telegram approval arrives from an unlinked or de-provisioned user | Rejected; audit recorded |
| EC-141 | An approval arrives for a superseded revision | Rejected; the current revision is presented |
| EC-142 | IndexNow key file is missing or not yet deployed | Submissions are skipped and reported |
| EC-143 | Bing Webmaster Tools and GSC disagree | Both are shown with source; no silent merge |
| EC-144 | A CMS item changed after Signal read it | Conditional write rejected; re-read and re-review |
| EC-145 | A self-hosted install lacks a Jev key | Fallback path is the default and is labelled |
| EC-146 | The repository's content format is unrecognized | New-article work is unavailable for that site until a recipe supports it |

<a id="section-21"></a>
## 21. Amendments to Revision 3.2

| Revision 3.2 section | Disposition |
| --- | --- |
| Header "Product" | Amended: open-source, self-hostable autonomous employee with a managed SaaS edition |
| 1.2 GA1 commitments | Amended: new-article publication via PR brought into scope; Webflow write support planned for R4 under certification |
| 1.3 Requirement traceability | Amended by [section 3](#section-3) |
| 1.4 GitHub-first Core V1 pilot | Superseded as the next target by R1–R4 ([section 17](#section-17)); its safety conditions still apply to each release |
| 2 Invariants | Extended by [section 4](#section-4) |
| 3 Architecture decisions | Extended by [section 5](#section-5) |
| 10 Connector platform | Extended by [section 12](#section-12) |
| 11 Crawl subsystem | Extended: browser rendering and the shared egress proxy ([section 9](#section-9)) |
| 12 Search and analytics ingestion | Extended: Bing Webmaster Tools; GA4 in R4 ([section 11](#section-11)) |
| 13 Evidence and memory | Extended: Business Brain ([section 10](#section-10)) |
| 14 OpenAI reasoning subsystem | Amended: provider boundary and Jev decision layer ([sections 7](#section-7) and [8](#section-8)); roles per [section 13](#section-13) |
| 15 Competitor research | Amended: optional licensed DataForSEO; no search-engine scraping ([section 11](#section-11)) |
| 16 Recipes | Extended: article and refresh recipes ([section 14](#section-14)) |
| 18.2 and 18.4 Approval classes and standing authorization | Applied as the autonomy contract ([section 6](#section-6)); classes unchanged |
| 22 Verification and measurement | Extended: 7/28/90-day per-change measurement ([section 16](#section-16)) |
| 25 Telegram and conversational control | Extended: Slack alongside Telegram with the same authority rules |
| 29 and 35 Self-hosting and operations | Extended: open-source edition and managed SaaS ([section 18](#section-18)) |
| 32 Release gates | Applied per release ([section 17](#section-17)) |
| 33 Implementation sequence | Replaced by R1–R4 and the [roadmap](docs/implementation/roadmap.md) |

<a id="section-22"></a>
## 22. Evidence status

All runtime evidence for capabilities introduced by this revision is
`NOT_EXECUTED`. This revision is a documentation contract. It does not implement,
configure, connect, or qualify Jev, the browser agent, the egress proxy, the
Business Brain, any new connector, content production, autonomous pull requests,
AI-search visibility, measurement, Slack, or the managed SaaS. No release is
admitted and no production write is enabled.
