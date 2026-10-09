# Signal Product Requirements

Status: **ACCEPTED 2026-09-24; ACTIVE PRODUCT DIRECTION; NOT A RELEASE CERTIFICATE.**

This document is the product half of
[Revision 4.0](../../Signal_Production_Engineering_Specification_Revision_4_0.md),
accepted by [ADR-0060](../adr/0060-autonomous-seo-employee-direction.md). Revision 4.0
holds the binding engineering contracts; this document explains the product, the
customer, and the decisions behind them. The [roadmap](../implementation/roadmap.md)
orders the work, and the [implementation status](../implementation/status.md) is the
only source of truth for what exists today. It supersedes the
[Core V1 PRD](core-v1-prd.md), which is preserved as history.

## 1. The Job

[Revision 4.1](../../Signal_Production_Engineering_Specification_Revision_4_1.md)
adds one explicit owner risk acceptance for an unprotected default branch. Normal
binding still requires protection. Acceptance requires dashboard Owner authority
and MFA within five minutes, retains a persistent warning, and requires an exact
dashboard owner Inbox approval for every PR. Standing dispatch is never allowed
on that base. Identity, branch, installation, permission, and recovery changes
require fresh acceptance; later protection supersedes the exception. No workflow,
secret, protected-path, default-branch-write, merge, or deployment guard changes.

> Grow a company's organic and AI-search traffic every week without hiring an SEO
> person, and never ship anything the owner would not have approved.

Signal is an SEO employee, not an SEO tool. Tools (OpenSEO, Surfer, Semrush) tell
you what to do. Signal does the work: research, strategy, content, technical fixes,
publishing, and measurement. It keeps the existing evidence, approval, and security
architecture as the reason customers can trust it with their site.

## 2. Customer

| | Choice |
| --- | --- |
| Primary | Seed–Series B startups and small companies with no dedicated SEO hire |
| Buyer | Founder, head of marketing, or growth lead |
| Site | Marketing site or blog in a GitHub repository (Next.js, Astro, Hugo, etc.) first; Webflow and WordPress second |
| Alternative they pay today | Freelancer or agency at $3–8k/month, or nothing |
| Target price | $300–700/month per site (market band for agents: $200–500) |

## 3. Market Position

| | Sunbeam | Frase / Surfer | OpenSEO | Signal |
| --- | --- | --- | --- | --- |
| Proactive (finds its own work) | Yes | No | No | Yes |
| Full pipeline: research → strategy → write → publish → measure | Yes | Partial | Research only | Yes |
| Fixes technical SEO itself | Yes (PRs) | No | No | Yes (PRs) |
| Evidence behind every claim | No | No | Data only | **Yes** |
| Owner-controlled autonomy per work type | Partial | n/a | n/a | **Yes** |
| Measured result per change | No public results | No | Rank tracking | **Yes, before/after per change** |
| Self-serve onboarding | No (demo, waitlist) | Yes | Yes | **Yes** |
| Transparent pricing | No | Yes | Yes | **Yes** |

**Positioning:** Sunbeam's output, with proof and control. Every change shows why it
was made, what was approved, what went live, and what it did to traffic.

## 4. What the Customer Gets

### First two hours (onboarding)
1. Sign up, add site, prove ownership, connect GSC, GitHub, and Bing Webmaster
   Tools, and upload brand documents (connectors in section 7c).
2. Signal builds the **Business Brain**: crawls every page, extracts products,
   audiences, positioning, brand voice, and factual claims; identifies competitors
   (owner-named, LLM-suggested, or from search results when DataForSEO is enabled);
   reads their top pages with the browser agent.
3. Delivers the **SEO Baseline**: technical health, what is ranking, what is slipping,
   AI-search visibility score, and keyword gaps vs competitors (search volumes and
   ranking competitors when DataForSEO is enabled).
4. Delivers a **90-day Strategy**: prioritized topics, pages to fix, pages to refresh,
   expected impact, each linked to evidence.

### Every week after
- New articles drafted in brand voice (briefs → drafts → internal links → schema).
- Decaying pages refreshed.
- Technical issues fixed as GitHub pull requests.
- AI-search visibility checked and content adjusted to earn citations.
- Weekly report in Slack, Telegram, or email: what shipped, what it did to clicks and rankings,
  what is next, what needs approval.

### Always
- An **Inbox** of work waiting for approval, reviewable in one click with a diff and evidence.
- **Confidence-gated autonomy** (section 6): Signal ships on its own and asks only when unsure or when the stakes are high.
- A full history: evidence → proposal → approval → PR/publish → live check → measured result.

## 5. The Workforce

Built on the nine existing Revision 3.2 roles, extended and with two added.

| Agent | Responsibility | Built on |
| --- | --- | --- |
| **Onboarding / Business Brain** (new) | Site-wide crawl, business facts, brand voice profile, product knowledge graph | Crawler, evidence store, scoped memory |
| **Strategist** | 90-day plan, weekly replanning, prioritization by expected impact | Coordinator/Planner |
| **Analytics** | GSC (and later GA4) ingestion: decay, striking-distance queries, CTR gaps | Analytics |
| **Competitor & Keyword Research** | Keyword ideas from GSC queries, the crawl, competitor pages, and LLM expansion; with optional DataForSEO: search volumes, SERPs, ranking competitors, backlinks | Competitor Research |
| **Content Writer** (expanded) | Briefs, full articles, refreshes, internal links, schema, in brand voice, no invented claims | Content Strategy |
| **Technical SEO** | Titles, metas, headings, canonicals, schema, broken links, sitemaps, robots | Technical SEO |
| **Performance** | Core Web Vitals and page-speed findings | Performance |
| **AI Search Visibility** (new) | Tracks whether ChatGPT, Perplexity, and Gemini cite the site for its target questions; proposes fixes | New, via the official OpenAI, Perplexity, and Gemini APIs with web search |
| **Publisher** | GitHub PRs first; Webflow/WordPress drafts second | Repository Implementer |
| **Reviewer / Verifier** | Independent review before approval; live check after publish; measurement after 7/28/90 days | Independent Reviewer/Verifier |
| **Communicator** | Slack and Telegram approvals, email and chat weekly reports, questions to the owner | Communicator |

## 6. Autonomy: Fully Autonomous by Default, Confidence-Gated

Signal works like an employee: it finds, does, and ships work without being asked,
opening pull requests automatically. It stops and asks only when its confidence is
low or the action is costly.

### Decision layer: Jev by TypeSafe

Every proposed action passes through a **decision gate** before it ships.
[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) is a
"System One" model: it returns a typed decision with a calibrated confidence score
instead of generating text, at a fraction of LLM cost and latency.

```text
Frontier model (Claude/GPT)   writes the article, fix, or plan
        ↓
Deterministic policy          hard limits: recipe allowed? budget left? scope exact?
        ↓                     never merge, deploy, delete, or touch secrets
Jev decision gate             typed output: { ship | ask_owner | reject }, risk class, confidence
        ↓
confidence ≥ threshold → auto-PR / publish, recorded with evidence
confidence <  threshold → Inbox + Slack/Telegram approval request
```

- **Thresholds are per work type and scale with cost of being wrong.** Metas, alt
  text, schema, internal links: low threshold. New articles and page rewrites:
  high threshold. Anything touching pricing, legal, or product claims: always ask.
- **Deterministic policy stays the final authority.** Jev can only lower autonomy
  (route to a human), never grant authority the policy does not already allow.
- **Every gate decision is evidence:** input digest, model version, typed output,
  confidence, threshold, and outcome are stored immutably, like model calls today.
- **Owner override:** the owner can raise any work type to "always ask" or lower
  thresholds after trust builds. Approval and rejection outcomes calibrate thresholds.
- **Provider risk:** the owner has Jev access, but Jev is still early access. The
  gate sits behind a typed interface with a fallback (frontier-model structured
  classification plus deterministic rules), so Signal keeps working if Jev is
  unavailable and self-hosters without Jev keys can still run it.

Signal never merges, deploys, or deletes on its own. "Ship" means opening a PR (or,
from R4, a CMS draft/publish where the owner explicitly granted it). Weekly budget
caps limit the number of autonomous PRs and articles.

## 6a. Jev as the Decision Layer Everywhere

The owner has Jev access. Beyond the autonomy gate, Signal uses Jev for every
routine decision and keeps frontier models for writing and hard reasoning:

```text
Jev (≈100 ms, near-zero cost)   decides the common case with typed output + confidence
Deterministic code               enforces hard limits and does what rules can do
Frontier model (Claude/GPT)      writes content and handles the low-confidence minority
```

| Decision | Jev question type | Replaces |
| --- | --- | --- |
| Ship, ask the owner, or reject an action | Choice + confidence | Human approval of every item |
| Next browser action (section 6b) | Choice over ≤255 interactive elements | LLM step-by-step browsing |
| Page type (product, pricing, blog, docs, legal…) for the Business Brain | Choice | LLM classification per page |
| Which crawl URLs matter next | Score | Fixed crawl order |
| Is this finding real, and how severe | Noul + Score | LLM triage |
| Keyword intent and cluster assignment | Choice | LLM clustering |
| Does page text contain instructions aimed at the agent (injection) | Noul | Heuristics |
| Does a draft make a claim absent from business facts | Noul, escalated to frontier model | Manual review |
| Which agent and model tier handles a task | Choice | Hard-coded routing |

Every Jev call is recorded like model calls today: input digest, question schema,
typed answer, probabilities, confidence, threshold, and resulting action.
Documented limits the design respects: text/JSON input only (no screenshots),
~64k tokens shared per call, ≤255 options per Choice, 1,200 requests/minute.

## 6b. Browser Agent

A real headless browser gives Signal the same view of a site that Google and users
get, which a plain HTTP fetch cannot.

**How it works:** a planner (frontier model) sets a goal such as "find the pricing
page and record the plans". Each step, the page's accessibility tree is reduced to
its interactive elements, and Jev picks the next one as a Choice. Low-confidence
steps go back to the planner (with a screenshot to a vision model if needed).
Because Jev can only choose among predefined options, text on a page cannot make
it invent a new action. That limits prompt injection by design.

**What it is used for:**
- **JavaScript rendering:** crawl client-rendered sites as Googlebot sees them.
- **Competitor research:** navigate competitor sites to record positioning, pricing, features, and top content.
- **Live verification:** after a PR is merged and deployed, open the live page and confirm the exact change is present.
- **Performance:** run Lighthouse / Core Web Vitals in the same browser.
- **Business Brain onboarding:** read product, docs, and review pages that only render in a browser.

**What it never does** (enforced by deterministic policy, not by the model):
- log in to third-party sites, enter credentials, submit forms, or make purchases;
- solve or bypass CAPTCHAs or bot detection;
- scrape Google search results or the ChatGPT/Perplexity consumer apps (AI visibility uses their official APIs; SERP data comes only from the optional DataForSEO provider);
- ignore robots.txt or the existing global per-origin rate limits.

**Security (same bar as the existing crawler):** each browser session runs in an
isolated, disposable container. All its traffic leaves through an egress proxy
that applies the existing public-address screening, origin pinning, robots
snapshots, and global origin admission. Page content is untrusted data. Every step
(goal, element list digest, Jev choice and confidence, resulting URL, snapshot
digest) is stored as immutable evidence.

## 7. Scope Changes Against Revision 3.2

**Brought into scope (currently deferred):**
- New-page and article publication (bounded volume per week).
- CMS delivery: Webflow first, then WordPress (the WordPress lab evidence is reused).
- An **optional** paid SEO data provider: DataForSEO for search volumes, SERPs,
  ranking competitors, and backlinks only (section 7a).
- Standing autonomy by default, gated by deterministic policy plus the Jev
  confidence gate (section 6).
- Slack alongside Telegram as chat channels for approvals and reports; GA4 ingestion.
- Distribution: source-available (Elastic License 2.0) self-hosted edition plus a paid managed SaaS.

**Moved later:**
- The requirement that all nine roles exist before any customer pilot. Roles ship
  in value order (section 8).

**Unchanged:** identity, tenancy, secrets, durable workflows, evidence, approvals,
safety gates, no merge/deploy authority, recovery.

## 7a. Data Sources: Build First, Buy Only What Cannot Be Built

Signal builds every data source it legally and practically can. It buys only data
that requires scraping Google or crawling the whole web.

| Need | Source | Cost |
| --- | --- | --- |
| Site crawl and technical audit | Signal's crawler plus the browser agent | Compute only |
| Rankings, clicks, and queries for the owner's own site | Google Search Console API | Free |
| AI-search visibility | Official OpenAI, Perplexity, and Gemini APIs with web search; citations parsed from answers | LLM usage |
| Keyword ideas | GSC queries, the crawl, competitor pages read by the browser agent, LLM expansion | LLM usage |
| Page speed and Core Web Vitals | Google PageSpeed Insights API and Lighthouse in the browser agent | Free / compute |
| Traffic and conversions (R4) | Google Analytics Data API | Free |
| Who ranks on Google for a keyword | **Optional DataForSEO.** Scraping Google breaks its terms and needs CAPTCHA bypass, which Signal forbids; Bing's search API is retired and Google's Custom Search API is closed to new customers | Pay per call |
| Search volume for keywords | **Optional DataForSEO.** Only Google has it (via Google Ads) | Pay per call |
| Backlinks to the owner's site | Bing Webmaster Tools API | Free |
| Search performance on Bing (which also feeds ChatGPT search and Copilot) | Bing Webmaster Tools API | Free |
| Backlinks to competitor sites | **Optional DataForSEO.** Requires crawling the whole web | Pay per call |

**Without DataForSEO**, Signal delivers the full product for any site with Search
Console history: crawl, audit, business brain, GSC opportunities, AI visibility,
content, fixes, and measurement. **With DataForSEO enabled**, it adds competitor
keyword gaps and search volumes for new topics, which matter most for new sites with
little GSC data. Self-hosted users can run without it.

## 7b. Paid Services

| Service | Required? | Purpose | Rough cost |
| --- | --- | --- | --- |
| LLM API (Claude or OpenAI) | Yes | Writing, strategy, planning, hard reasoning, AI-visibility checks | Largest cost, ≈ $10–40 per site per month |
| Jev (TypeSafe) | Recommended (fallback exists) | All routine decisions and browser steps | Cents per site per month |
| Cloud hosting | Yes for SaaS | API, dashboard, workers, PostgreSQL, Temporal, Keycloak, OpenBao, browser containers | ≈ $50–150 per month to start; browser containers are the heaviest part |
| DataForSEO | Optional | SERPs, search volumes, backlinks | A few dollars per site per month |
| Email (Resend or Postmark) | At SaaS launch | Reports and notifications | Free tier, then ≈ $20 per month |
| Object storage (Cloudflare R2 or S3) | At SaaS launch | Encrypted crawl evidence artifacts | A few dollars per month |
| Stripe | At SaaS launch | Billing | ≈ 2.9% + 30¢ per payment |
| Error monitoring (Sentry) | Optional | Production errors | Free tier |
| Domain | At SaaS launch | Product domain | ≈ $15 per year |

**Free:** Google Search Console, Google Analytics, Bing Webmaster Tools, IndexNow,
PageSpeed Insights, GitHub App, Slack, Telegram, Webflow, WordPress, and Notion APIs; Google OAuth verification; Keycloak, OpenBao, Temporal,
PostgreSQL, and Playwright (open source); GitHub Actions minutes once the repository
is public.

**Unit economics:** self-hosted users bring their own LLM, Jev, and (optional)
DataForSEO keys, so they cost nothing to serve. Managed SaaS sites cost roughly
$30–80 per month against a ≈ $400 per month price.

## 7c. Connectors

Customer-owned accounts the owner connects. Each connector gets the narrowest
access that its job needs, is bound to exactly one verified site, stores its
credentials only in OpenBao, and can be disconnected at any time. Everything a
connector returns is untrusted data, never instructions.

| Connector | What Signal uses it for | Access granted | Never | Auth | Release |
| --- | --- | --- | --- | --- | --- |
| **Google Search Console** | Rankings, clicks, impressions, queries, decaying pages, index coverage | Read only (`webmasters.readonly`) | Change settings, submit removals, add users | Google OAuth | R1 |
| **GitHub** | Read site code, detect framework, open PRs with fixes and articles, observe CI and deployment status | Contents read; pull requests write on one repository; checks and deployments read | Merge, push to the default branch, deploy, edit workflows, read secrets, administer the repository | GitHub App installed on one repository | R1 read, R2 PRs |
| **Bing Webmaster Tools** | Bing search performance (Bing's index also feeds ChatGPT search and Copilot), crawl issues, and **free inbound-link data for the owner's site** | Read; URL submission from R2 | Change site settings, remove URLs | Bing API key or OAuth | R1 |
| **Brand documents** | Brand voice, product facts, positioning, and claims for the Business Brain | Owner uploads PDF, Markdown, DOCX, or text | Treat document text as instructions | Upload in the dashboard | R1 |
| **IndexNow** | Notify Bing and other participating engines when a page changes, after live verification confirms it | Submit changed URLs for the verified site | Submit URLs outside the verified site | Key file added to the site by Signal's first PR | R2 |
| **Slack** | Approval requests, questions to the owner, weekly reports | Post to one chosen channel and direct messages to linked users; receive their replies and button actions | Read other channels or message history; act for unlinked Slack users | Slack app (OAuth); each Slack user linked to a Signal account from the dashboard | R3 |
| **Telegram** | Same as Slack | Messages to and from paired accounts only | Act for unpaired accounts; join groups | Telegram bot; account paired from the dashboard | R3 |
| **Email** | Weekly reports, alerts, approval links | Send only | Send to unverified addresses; carry secrets | Signal's email provider | R3 |
| **Google Analytics 4** | Traffic, engagement, and conversions per page | Read only (`analytics.readonly`) | Change properties, audiences, or tracking | Google OAuth | R4 |
| **Webflow** | Create and update CMS items (articles, page fields) | CMS read and write on one site; publish only when the owner grants it | Change site design, delete items, manage hosting or billing | Webflow OAuth | R4 |
| **WordPress** | Create and update posts and page SEO fields | Draft by default; publish only when the owner grants it | Install plugins or themes, change users or settings, delete content | Application password for a dedicated least-privilege user | R4 |
| **Notion and Google Docs** | Keep the Business Brain in sync with the company's own brand and product docs | Read only on pages or folders the owner selects | Read anything not selected; write | Notion OAuth; Google OAuth (`drive.file`) | R4 |

**Approvals from Slack or Telegram** are bound to the exact proposal revision and
the linked person's current authority. Actions above a configured risk level hand
off to the dashboard for step-up authentication instead of approving in chat.

**Deliberately not connectors:** Vercel and Netlify (deployment status comes through
GitHub), Shopify and Google Business Profile (outside the target customer for now),
and Semrush or Ahrefs (priced above the target market; DataForSEO covers the gap).

## 8. Release Sequence (Value Order)

Each release is usable by a real customer on its own.

| Release | Customer value | Main new capability | Rough effort |
| --- | --- | --- | --- |
| **R1 Insight** | "It understands my business and told me exactly what to do" | Full-site crawl, Jev decision layer, sandboxed browser with egress proxy (rendering, competitor research), Business Brain with brand-document upload, GSC and Bing Webmaster bindings, AI-visibility baseline, optional DataForSEO, SEO Baseline, 90-day Strategy | 4–5 weeks |
| **R2 Work** | "It wrote the articles and fixed the pages; I just approved" | Content Writer, Technical SEO recipes, Inbox, GitHub PR delivery, browser live verification, IndexNow | 3–4 weeks |
| **R3 Employee** | "It runs every week and shows me what it moved" | Weekly autonomous loop, Jev confidence gate, auto-PRs, Slack and Telegram, reports, per-change measurement | 2–3 weeks |
| **R4 AI Search & Breadth** | "It gets us cited by ChatGPT, and it works on Webflow" | AI visibility optimization agent, Webflow and WordPress publishing, GA4, Notion and Google Docs sync | 3–4 weeks |

Effort assumes the existing architecture and a lighter documentation cadence for
product layers (decision 6).

## 9. Success Measures

| Measure | Target |
| --- | --- |
| Time from signup to Strategy delivered | < 2 hours |
| Proposals approved without edits | > 60% |
| Opened PRs merged by owner | > 70% |
| Clicks on changed pages, 90 days after change | Measurably up vs pre-change baseline |
| Weekly active owners (approve or read report) | > 70% of paying sites |
| Model + data cost per site per month | < 15% of price |

## 10. Decisions

### Decided by the owner (2026-09-24)

1. **Sites first:** GitHub-hosted sites, published by pull request. Webflow and WordPress in R4.
2. **Autonomy:** fully autonomous by default with automatic PRs, gated by Jev
   (TypeSafe) confidence and deterministic policy (section 6).
3. **Chat:** Slack and Telegram, both for approvals and reports.
4. **Distribution:** source-available (Elastic License 2.0) self-hosted edition; paid managed SaaS.
5. **Jev:** owner has access. Jev is the decision layer for autonomy, triage, routing, and browser actions (sections 6a–6b); the frontier-model fallback stays.

6. **Process:** full ADR, evidence record, and real-provider qualification per
   slice for security-critical layers (identity, tenancy, secrets, egress, browser
   sandbox, autonomy gate, publishing authority). Product features get a lighter
   record: tests, a status line, and a changelog entry.
7. **Pricing:** one managed SaaS plan at about $400 per site per month at launch;
   tiers are decided later from real usage.
8. **Source-available:** the whole engine is source-available under the Elastic
   License 2.0 and self-hostable; only the maintainer may offer it as a hosted
   service. The paid
   SaaS sells hosting, managed provider connections, AI-search tracking, and the
   maintained, continuously improved SEO playbooks.
9. **Data:** build every data source that can be built (section 7a); DataForSEO is
   optional and used only for SERPs, search volumes, and backlinks.

### Open items before public release

- Done: the project is licensed under the Elastic License 2.0 (`LICENSE`, `NOTICE`),
  with a Contributor License Agreement (`CLA.md`).
- Complete Google OAuth app verification for the Search Console, Analytics, and
  Drive scopes before strangers can connect Google accounts to the managed SaaS.
- Confirm Jev production terms (rate limits, data handling) for managed SaaS use.

## 11. Release Acceptance

A release is accepted only when a real owner can complete its journey on a real
site, every enabled capability passes its Revision 4.0 and Revision 3.2 gates, and
everything not yet admitted is visibly unavailable.

| Release | Acceptance journey |
| --- | --- |
| **R1 Insight** | From a fresh browser, sign up or accept an invitation, verify a real site, connect GSC, GitHub (read), and Bing, upload brand documents, and receive a Business Brain, SEO Baseline, AI-visibility baseline, and 90-day Strategy whose every claim links to evidence, within two hours |
| **R2 Work** | Signal drafts an article and a technical fix for that site, the owner reviews each in the Inbox with evidence and diff, Signal opens the pull requests, observes checks and deployment, verifies the live result in the browser, and notifies IndexNow |
| **R3 Employee** | With a standing authorization granted, Signal runs a week unattended: it opens low-risk PRs on its own, escalates high-stakes items to Slack or Telegram, respects caps and pause, and sends a weekly report with measured results |
| **R4 AI Search and Breadth** | Signal proposes changes that improve AI-assistant citations, delivers content to a Webflow or WordPress site, reads GA4, and keeps the Business Brain in sync with selected Notion or Google Docs pages |
