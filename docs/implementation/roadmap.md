# Implementation Roadmap: R1–R4

Status: **ACTIVE SEQUENCE; NOT A RELEASE CERTIFICATE**.

This roadmap turns the [product requirements](../product/prd.md) and
[Revision 4.0](../../Signal_Production_Engineering_Specification_Revision_4_0.md)
into ordered, bounded slices. It supersedes the
[Core V1 roadmap](core-v1-roadmap.md), which is preserved as history. The
[status page](status.md), not this plan, records what exists.

## Delivery Rules

- Build one bounded slice at a time. Each slice names the release it serves and the
  customer journey it advances.
- Classify every slice before starting (Revision 4.0 section 19):
  - **Security-critical** (identity, tenancy, secrets, egress, browser sandbox,
    autonomy gate, standing authorization, external writes, publishing authority,
    recovery): implementation record, ADR where consequential, evidence record,
    real-provider qualification, status, and changelog.
  - **Product** (uses existing authority without changing it): positive, negative,
    and failure tests; a status line; a changelog entry.
- Keep domain logic separate from provider I/O, credentials, and orchestration.
  Reuse existing modules before adding new ones; add no dependency without a use.
- A capability stays visibly unavailable in the dashboard until its slice is done.
- Never weaken an existing safety test or gate to make a new feature pass.

## Current Position (Carried Forward)

Slices 0001–0062 built the trusted foundation that R1 starts from. Existing modules
under `services/control_plane/src/signal_core/` that the next slices reuse:

| Foundation | Modules | State |
| --- | --- | --- |
| Identity, sessions, tenancy, invitations | `oidc_*`, `session_*`, `identity_*`, `invitation*`, `authorization.py` | Locally qualified; production identity gateway unconfigured |
| Durable commands and workflows | `commands.py`, `outbox_*`, `workflow_*` | PostgreSQL outbox and Temporal qualified locally |
| Crawl safety | `crawl_urls.py`, `crawl_http.py`, `crawl_robots.py`, `crawl_admission.py`, `crawl_frontier.py`, `crawl_page.py`, `crawl_artifacts.py`, `full_site_crawl.py` | Bounded verified-origin composition qualified locally; production crawler unavailable |
| Site ownership | `site_onboarding.py`, `origin_verification.py` | Owner onboarding and exact-origin proof qualified locally |
| Evidence and findings | `page_observations.py`, `audit_findings.py` | Verified-homepage observation and one deterministic finding |
| Model and proposals | `model_reasoning.py`, `model_credentials.py`, `proposals.py` | Bounded OpenAI drafting, sealed RFC 8785 revisions, owner decisions |
| Providers | `gsc_properties.py`, `github_app.py`, `github_read_binding.py` | Read-only GSC discovery; GitHub App inspection and locally durable owner binding |
| Secrets | `openbao_http.py`, `pkce_secrets.py` | OpenBao boundaries qualified locally |

The dashboard (`apps/dashboard`) and API (`apps/api`) expose the local pilot journey
described in the [local pilot runbook](../runbooks/local-pilot.md).

## Release Sequence

| Release | Outcome | Estimate |
| --- | --- | --- |
| R1 Insight | Business Brain, SEO Baseline, AI-visibility baseline, and 90-day Strategy for a real site | 4–5 weeks |
| R2 Work | Articles and technical fixes delivered as verified pull requests | 3–4 weeks |
| R3 Employee | Weekly autonomous loop with standing authorization, Jev gate, Slack, Telegram, reports | 2–3 weeks |
| R4 AI Search and Breadth | AI-visibility agent, Webflow, WordPress, GA4, Notion and Google Docs | 3–4 weeks |

Estimates assume the process tiers above and are planning inputs, not commitments.

## GitHub-First Beta Path

This is an **internal milestone, not an R1-R4 release admission**. Revision 4.0,
the release gates, safety invariants, and slice identifiers are unchanged. The
ordered critical path is 0068 technical findings, 0070 durable GitHub read
binding, 0079 narrow pull-request authority, 0080 isolated candidate build,
0081 sealed technical recipes, 0083 exact-revision Inbox, 0084 reconciled PR
creation, and 0085 check/deployment observation with shared-egress GET live
verification. Then 0088 standing authorization, 0089 the deterministic-first
Jev autonomy gate, and 0090 the weekly loop add unattended work. The later
browser, Business Brain, GSC/Bing, AI-visibility, writing, CMS, and SaaS slices
remain required for their releases and visibly unavailable meanwhile.

**0104 autonomy-to-delivery integration** connects those existing foundations:
separately reviewed A2 recipe eligibility, immutable exact standing dispatch or
owner Inbox authority, idempotent PR creation, customer deployment observation,
live verification and one cycle report. It is security-critical and locally
qualified on the Business Brain, Slack and Content Writer mainline; migration
`0060` follows unchanged `0059`. Its 36-case delivery lab and full requested
gate passed. Completion qualifies the internal beta composition only,
not live-provider or release admission.

[ADR-0064](../adr/0064-prioritize-github-first-beta-path.md) records why this
path moves ahead of release breadth without weakening any gate.

## R1 Insight

Ordered slices. Numbers are planned identifiers; a slice may split if it grows.

| Slice | Scope | Tier | Builds on |
| --- | --- | --- | --- |
| 0064 | Jev decision client: typed Choice/Noul/Score questions, OpenBao credential, strict response validation, immutable decision records, frontier-model fallback, labelled fallback evidence | Security-critical | `model_credentials.py`, `model_reasoning.py` |
| 0065 | Egress proxy shared by crawler, browser, and connectors: public-address screening, pinning, robots evidence, origin admission | Security-critical | `crawl_http.py`, `crawl_admission.py`, `crawl_robots.py` |
| 0066 | Full-site crawl composition: frontier settlement, page attempts, parsing, coverage, Temporal registration for one verified site | Security-critical | `crawl_*`, `workflow_*` |
| 0067 | Sandboxed browser worker: disposable Playwright container, action allowlist, accessibility-tree reduction, Jev action choice, step evidence | Security-critical | 0064, 0065 |
| 0068 | Technical audit findings across the crawl: titles, descriptions, headings, canonicals, structured data, links, sitemaps, robots | Security-critical (internal evidence grant) | 0066, `audit_findings.py` |
| 0069 | GSC binding: OAuth, OpenBao storage, durable property binding, import generations with explicit coverage | Security-critical | `gsc_properties.py` |
| 0070 | Durable GitHub read binding: owner-selected App installation, repository, and base branch; one-repository `contents: read`; App secret in OpenBao; wrong-binding and revocation checks | Security-critical | `github_app.py`, `openbao_http.py` |
| 0071 | Bing Webmaster Tools binding and import: performance and own-site inbound links | Security-critical | 0069 pattern |
| 0072 | Brand-document upload: encrypted storage, extraction, injection screening | Security-critical | `crawl_artifacts.py`, 0064 |
| 0073 | [Business Brain](0073-business-brain.md): implemented record/extraction/owner API/dashboard; live providers and production composition `NOT_EXECUTED` | Product | 0064, 0066, 0072 |
| 0074 | Assistant-provider boundaries: OpenAI, Perplexity, and Gemini credentials in OpenBao; shared-proxy egress; bounded requests and responses; immutable provider evidence | Security-critical | 0065, model and connector boundaries |
| 0075 | AI-visibility baseline through the qualified assistant-provider boundaries | Product | 0064, 0074 |
| 0076 | Optional DataForSEO adapter: search results, volumes, competitor backlinks; visibly unavailable when unconfigured | Security-critical (new provider) | Connector pattern |
| 0077 | SEO Baseline and 90-day Strategy with evidence links; dashboard Overview, Pages, Strategy, Analytics views | Product | 0068–0076 |
| 0078 | R1 owner journey qualification on a real site | Security-critical | All R1 slices |

## R2 Work

| Slice | Scope | Tier |
| --- | --- | --- |
| 0079 | Extend the R1 GitHub binding with pull-request authority and framework/content-format detection; retain one-repository scope and no merge, deploy, default-branch push, workflow edit, or secret access | Security-critical |
| 0080 | Isolated credential-free checkout and candidate build | Security-critical |
| 0081 | Technical SEO recipes as sealed candidate patches | Security-critical |
| 0082 | [Content Writer](0082-content-writer.md): versioned briefs, grounded drafts, originality/caps, sealed one-file Eleventy HTML articles/refreshes and editorial Inbox; live composition `NOT_EXECUTED` | Product |
| 0083 | Inbox: exact revision review with evidence and diff | Product |
| 0084 | Idempotent pull-request creation with write-intent journal and reconciliation | Security-critical |
| 0085 | Check and deployment observation; shared-egress GET live verification for beta, with browser verification later | Security-critical |
| 0086 | [IndexNow key file and verified-change notification](0086-indexnow.md), internal entrypoints; live qualification pending | Security-critical |
| 0087 | R2 owner journey qualification | Security-critical |

## R3 Employee

| Slice | Scope | Tier |
| --- | --- | --- |
| 0102 | Independent authority-restriction journal, verified receipts, pending durability, and deny-only restore replay | Security-critical |
| 0103 | Platform-owned immutable reviewed recipe-release registry and grant-time range resolution | Security-critical |
| 0088 | Standing authorization records, work types, thresholds, volume and spend caps, revocation through the restriction journal | Security-critical |
| 0089 | Jev autonomy gate composed with deterministic policy and the authorizer | Security-critical |
| 0090 | Weekly loop: Temporal schedule for observe, plan, work, gate, ship, verify, measure, report, with pause | Security-critical |
| 0091 | Slack app with per-user linking and exact-revision approvals | Security-critical |
| 0092 | Telegram pairing and approvals | Security-critical |
| 0093 | Email reports and alerts | Security-critical (SMTP egress, provider secret, recipient identity) |
| 0094 | Per-change measurement at 7, 28, and 90 days; weekly report | Product |
| 0095 | R3 unattended-week qualification | Security-critical |

Train 2 integrates verified owner-editorial delivery with 0094 measurement in
migration 0071: new pages explicitly have no pre-change window; refreshes use the
normal baseline. Post windows remain provider-reported observations, not causal
claims. Three focused real PostgreSQL/Temporal correction/regression cases passed;
final-stack qualification is pending. No production authority is added.

## R4 AI Search and Breadth

| Slice | Scope | Tier |
| --- | --- | --- |
| 0096 | AI-visibility optimization agent | Product |
| 0097 | GA4 binding and import | Security-critical |
| 0098 | [Webflow delivery with conditional writes](0098-webflow.md): no documented atomic precondition; draft-only local boundary, live NOT_EXECUTED | Security-critical |
| 0099 | WordPress delivery through a least-privilege user | Security-critical |
| 0100 | [Google Docs sync for the Business Brain](0100-google-docs-sync.md); Notion unavailable pending sanitized live read-only scope qualification | Security-critical |
| 0101 | R4 qualification | Security-critical |

## Parallel Lanes

- **Managed SaaS readiness:** project license, Google OAuth verification, public
  signup, billing, production identity and resolver composition, hosted deployment.
- **Operations:** published and signed images, monitoring, backup and restore drills,
  worker build-ID rollout.

## Accepted Merge Train

The reviewed breadth slices form a linear stack above dedicated integration
(main migration 0062). Migrations 0001-0062 remain unchanged. Qualification is
local and does not enable production writes. Current results are recorded in
each slice's evidence and status line.

| PR / Slice | Migration | Down Revision | App Tables | State |
| --- | --- | --- | --- | --- |
| #13 / 0077 | 0063 | 0062 | 106 | Fast checks passed; no release authority |
| #14 / 0076 | 0064 | 0063 | 110 | Fast checks passed; no release authority |
| #19 / 0086 | 0065 | 0064 | 116 | Fast checks passed; no release authority |
| #16 / 0094 | 0066 | 0065 | 118 | Fast checks passed; no release authority |
| #17 / 0097 | 0067 | 0066 | 121 | Fast checks passed; no release authority |
| #15 / 0093 | 0068, 0069 | 0067, 0068 | 124 | Fast checks passed; no release authority |
| #11 / 0092 | 0070 | 0069 | 129 | Verification incomplete: database execution budget; publication held |

## Merge Train 2

Accepted breadth stack above merged main `7754746` (migration 0070).
Migrations 0001-0070 stay byte-for-byte; local gates grant no release authority.

| PR | Migration | Down Revision | App Tables | State |
| --- | --- | --- | --- | --- |
| #24 | 0071 | 0070 | 130 | Fast checks passed; no release authority |
| #20 | 0072 | 0071 | 133 | Fast checks passed; no release authority |
| #25 | 0073 | 0072 | 138 | Fast checks passed; no release authority |
| #21 | 0074 | 0073 | 142 | Fast checks passed; no release authority |
| #23 | 0075 | 0074 | 148 | Fast checks passed; no release authority |
| #22 | 0076 | 0075 | 150 | Fast checks passed; no release authority |
| #26 | 0077 | 0076 | 152 | Fast checks passed; no release authority |
| #28 | 0078 | 0077 | 154 | Fast checks passed; no release authority |
| #27 | 0079 | 0078 | 154 | Fast checks passed; no release authority |
| #29 | None | Inherits 0079 | 154 | Fast checks passed; no release authority |

## Merge Train 3

Accepted stack above main `168510b` (migration 0079).
Migrations 0001-0079 remain unchanged; local gates grant no release authority.

| PR | Migration | Down Revision | App Tables | State |
| --- | --- | --- | --- | --- |
| #39 | None | Inherits 0079 | 154 | Fast checks passed; no release authority |
| #30 | 0080 | 0079 | 157 | Fast checks passed; no release authority |
| #38 | 0081 | 0080 | 157 | Fast checks passed; no release authority |
| #41 | 0082 | 0081 | 157 | Fast checks passed; no release authority |
| #31 | 0083 | 0082 | 160 | Fast checks passed; no release authority |
| #34 | 0084 | 0083 | 164 | Fast checks passed; no release authority |
| #33 | 0085 | 0084 | 167 | Fast checks passed; no release authority |
| #35 | 0086 | 0085 | 171 | Fast checks passed; no release authority |
| #36 | 0087 | 0086 | 173 | Fast checks passed; no release authority |
| #37 | 0088 | 0087 | 173 | Fast checks passed; no release authority |
| #40 | 0089 | 0088 | 175 | Fast checks passed; no release authority |

## Merge Train 4

Accepted stack above main `856a996` (migration 0089).
Migrations 0001-0089 remain unchanged; local gates grant no release authority.
Final-tip full-gate receipts and PR comments govern integrated qualification.

| PR | Migration | Down Revision | App Tables | State |
| --- | --- | --- | --- | --- |
| #47 | 0090 | 0089 | 176 | Fast checks passed; no release authority |
| #43 | 0091 | 0090 | 177 | Fast checks passed; no release authority |
| #50 | 0092 | 0091 | 177 | Fast checks passed; no release authority |

## Next Slice

**Owner-approved cleanup tracks A, B and C.** Main includes owner paths,
gap closure and the Direction C dashboard. Track A refactors SQL dispatch;
track B consolidates connector/egress code; [0168](0168-cleanup.md), track C,
handles the remaining audit cleanup without changing their ownership boundaries.
After integration and qualification, proceed to dedicated-environment live
qualification. Signal still never merges, deploys, pushes to a default branch,
edits CI or reads repository secrets. See [current status](status.md#next-work).
