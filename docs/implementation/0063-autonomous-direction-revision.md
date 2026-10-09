# Slice 0063: Autonomous SEO Employee Direction (Revision 4.0)

Status: **DOCUMENTATION CONTRACT COMPLETE; NO NEW RUNTIME CAPABILITY**.

## Outcome

Signal has one active direction: an autonomous SEO and AI-search employee built on
the existing security architecture. Revision 4.0, the product requirements,
ADR-0060, and the R1–R4 roadmap agree on the customer, autonomy model, Jev decision
layer, browser agent, data sources, connectors, content production, distribution,
process tiers, and release order.

This slice changes documentation and repository consistency checks only. It does
not implement, configure, connect, or qualify any capability that Revision 4.0
introduces, and it grants no release admission or production authority.

## Contract Changes

| Area | Decision |
| --- | --- |
| Specification | Added amending Revision 4.0; preserved Revisions 3.2, 3.1, and 3.0 unchanged |
| Product | Autonomous SEO employee for startups and small companies; GitHub-hosted sites first |
| Autonomy | Fully autonomous by default under a human-granted standing authorization, deterministic policy first, Jev gate may only reduce autonomy |
| Decision layer | Jev for routine typed decisions with a labelled frontier-model fallback |
| Browser | Sandboxed, credential-free, deny-by-default agent sharing the crawler's egress controls |
| Scope in | New-article PRs, Slack, Bing Webmaster Tools, IndexNow, brand documents, GA4, Webflow, WordPress, Notion, Google Docs, AI-search visibility, optional DataForSEO |
| Sequence | R1 Insight, R2 Work, R3 Employee, R4 AI Search and Breadth replace the Core V1 milestone order |
| Distribution | Open-source self-hosted engine and a paid managed SaaS |
| Process | Full slice discipline for security-critical work; lighter record for product features |

## Files

- `Signal_Production_Engineering_Specification_Revision_4_0.md`
- `docs/product/prd.md`
- `docs/implementation/roadmap.md`
- `docs/adr/0060-autonomous-seo-employee-direction.md`
- `docs/implementation/0063-autonomous-direction-revision.md`
- `docs/product/core-v1-prd.md` (superseded notice)
- `docs/implementation/core-v1-roadmap.md` (superseded notice)
- `docs/adr/0030-github-first-core-v1.md` (superseded-in-part link)
- `docs/adr/README.md`
- `docs/README.md`
- `docs/implementation/status.md`
- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `CONTRIBUTING.md`
- `PRODUCT.md`
- `CHANGELOG.md`
- `scripts/check-repository.mjs`
- `tests/repository/documentation.test.mjs`

## Verification

```sh
npm run test:repo
npm run check:docs
```

The repository suite verifies:

- protected SHA-256 hashes for all four accepted specification revisions;
- resolved internal links, anchors, and parseable structured examples, including
  the Revision 4.0 decision-record example;
- the preserved Revision 3.2 Core V1 contract;
- Revision 4.0 requirements REQ-019 to REQ-027, invariants INV-026 to INV-034,
  edge cases EC-124 to EC-146, the four releases, the connector set, and
  `NOT_EXECUTED` evidence; and
- a README, PRD, roadmap, ADR, status, and agent instructions that point to the
  active direction without claiming implementation.

Result on 2026-09-24: `npm test` passed with 24 of 24 repository cases,
documentation checks across 165 Markdown files, 106 of 106 dashboard cases, the
dashboard typecheck, and the dashboard build. The first typecheck attempt failed on
iCloud-created duplicate files inside the git-ignored `apps/dashboard/.next` build
output; that directory was moved aside and regenerated. The protected Revision 4.0
SHA-256 is `a1be2da9549381914a2e1c0996ffa9efb795c9aefefbd41a4a3bb888ccef2cca`.

No Python, PostgreSQL, Temporal, browser, provider, or model test is needed to
prove a documentation-only change, and none is claimed.

## Preserved Evidence And Limits

Earlier implementation records and provider evidence remain accurate within their
stated limits. ADR-0060 supersedes only the Core V1 scope, deferrals, and milestone
order in ADR-0030; every safety decision stays in force.

All Revision 4.0 runtime evidence begins `NOT_EXECUTED`. The
[roadmap](roadmap.md) orders the work, starting with the Jev decision client.
