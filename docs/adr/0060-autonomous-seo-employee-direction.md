# ADR-0060: Autonomous SEO Employee Direction

- Status: Accepted
- Date: 2026-09-24
- Owners: Signal product and engineering
- Supersedes: The Core V1 scope, deferrals, and milestone sequence in [ADR-0030](0030-github-first-core-v1.md)
- Preserves: Every safety, identity, tenancy, evidence, and recovery decision in ADR-0001 through ADR-0059

## Context

Revision 3.2 and ADR-0030 defined a supervised Core V1 pilot: one site, one
repository, all nine roles before any customer use, one certified recipe, exact
human approval for every change, and explicit deferral of new-page publication,
CMS writes, standing autonomy, paid research data, and GA4.

After 62 slices the trusted foundations are strong, but the product a customer can
use produces one metadata draft for one page. Commercial autonomous SEO agents
already research, plan, write articles, fix technical issues, publish through pull
requests or a CMS, and run continuously. The Core V1 deferral list excludes most of
what those customers pay for, and the requirement that all nine roles exist before
any pilot delays every useful outcome.

The owner decided (2026-09-24) to keep the existing security architecture and build
an autonomous SEO employee on top of it: GitHub-hosted sites first, fully
autonomous operation by default with automatic pull requests, confidence gating by
the Jev model from TypeSafe AI (to which the owner has access), Slack and Telegram,
an open-source self-hosted edition with a paid managed SaaS, and data sources built
in-house wherever that is legal and practical.

## Decision

Adopt [Revision 4.0](../../Signal_Production_Engineering_Specification_Revision_4_0.md)
as an amending revision of Revision 3.2, together with the
[product requirements](../product/prd.md) and the
[roadmap](../implementation/roadmap.md).

1. **Product:** Signal is an autonomous SEO and AI-search employee for startups and
   small companies, delivering research, strategy, articles, refreshes, technical
   fixes, verification, and measured results every week.
2. **Autonomy:** autonomy is a human-granted standing authorization (Revision 3.2
   section 18.4) per site and work type, with thresholds and weekly volume and spend
   caps. Deterministic policy decides first; the Jev gate can then only keep or
   reduce autonomy. Signal never merges, deploys, deletes, or edits CI workflows.
3. **Decision layer:** Jev answers routine typed decisions (autonomy gate, browser
   actions, classification, triage, routing) with calibrated confidence. A
   frontier-model plus deterministic-rule fallback keeps Signal working without Jev.
4. **Browser agent:** sandboxed, credential-free, deny-by-default sessions that share
   the crawler's egress controls, used for rendering, competitor research, live
   verification, and Lighthouse.
5. **Scope brought in:** new-article pull requests with claim grounding and volume
   caps, Slack, Bing Webmaster Tools, IndexNow, brand documents, GA4, Webflow and
   WordPress delivery, Notion and Google Docs, AI-search visibility, and optional
   DataForSEO for search results, volumes, and competitor backlinks only.
6. **Sequence:** four customer-usable releases (R1 Insight, R2 Work, R3 Employee,
   R4 AI Search and Breadth) replace the Core V1 milestone order. Roles ship in
   release order.
7. **Distribution:** the whole engine is open source and self-hostable with owner
   keys; the managed SaaS adds hosting, managed connections, AI-search tracking, and
   maintained playbooks.
8. **Process:** full slice discipline stays mandatory for security-critical work;
   product features use a lighter record (Revision 4.0 section 19).

## Alternatives Considered

### Keep Revision 3.2 Core V1 Unchanged

Rejected. It protects safety but defers the outputs customers value most and cannot
produce a product comparable to autonomous SEO agents within a useful time.

### Rebuild As A Lean Application Without The Existing Architecture

Rejected by the owner. It would ship faster at first but discard qualified identity,
tenancy, secrets, durable workflow, crawl, and evidence foundations that customers
need to trust an agent with their site.

### Autonomy Decided By Model Confidence Alone

Rejected. A confidence score is not authority (INV-001). Autonomy must come from a
recorded human grant, deterministic policy, and budgets; the model may only
escalate.

### Build All Search Data In-House

Rejected in part. Search-result data, search volume, and web-wide backlinks require
scraping search engines or crawling the whole web, which breaks provider terms or is
impractical. These come only from an optional licensed provider; everything else is
built.

## Consequences

- New work follows the R1–R4 roadmap. The next slices are the Jev decision service,
  egress proxy, browser sandbox, full-site crawl, and GSC/Bing bindings.
- ADR-0030's safety conditions remain in force; only its scope, deferral list, and
  milestone order are superseded.
- The dashboard, PRD, roadmap, status, and agent instructions must describe the
  autonomous direction consistently.
- A project license and Google OAuth verification become prerequisites for the
  public open-source release and the managed SaaS.
- Autonomous publication raises content-quality risk; claim grounding, originality
  review, and volume caps are mandatory, not optional polish.

## Verification

This decision is a documentation contract, not runtime evidence. Repository checks
must prove that:

- Revisions 3.0, 3.1, 3.2, and 4.0 are byte-for-byte protected baselines;
- Revision 4.0 declares requirements REQ-019 to REQ-027, invariants INV-026 to
  INV-034, edge cases EC-124 to EC-146, the four releases, the connector set, and
  `NOT_EXECUTED` runtime evidence;
- the README, agent instructions, PRD, roadmap, status, indexes, and changelog point
  to Revision 4.0 as the active direction; and
- no implementation or release admission is claimed by this documentation change.
