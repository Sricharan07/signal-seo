# Signal

A source-available, self-hostable **autonomous SEO and AI-search employee**, with a paid
managed SaaS edition.

Signal learns a company's business, researches, plans, writes articles, fixes
technical SEO, delivers changes as GitHub pull requests, verifies what went live,
measures the effect, and reports every week. It works on its own under a standing
authorization that the owner grants, and asks when its confidence is low or the
stakes are high. Every claim and change links to its evidence.

> **Status: product releases R1–R4 not yet implemented; no production release.**
> Many of their capabilities now have tested local implementations: identity,
> tenancy, secrets, durable workflows, verified-site crawling, evidence, the weekly
> loop, Business facts, Strategy, the Content Writer, owner review in the
> dashboard, Slack and Telegram, internal GitHub pull-request delivery,
> measurement and weekly reports. Live qualification covers the dedicated test
> environment's sign-in, Slack OAuth and GitHub read binding; Search Console
> consent, pull-request delivery, Jev and deployment are not yet qualified. No
> production writes are enabled. The [implementation status](docs/implementation/status.md)
> is the source of truth for what exists.

## Contents

- [Documentation](#documentation)
- [Product Overview](#product-overview)
- [How Autonomy Works](#how-autonomy-works)
- [Releases](#releases)
- [Connectors](#connectors)
- [Try The Local Pilot](#try-the-local-pilot)
- [Architecture](#architecture)
- [Data and Memory](#data-and-memory)
- [Safety and Recovery](#safety-and-recovery)
- [Working With This Repository](#working-with-this-repository)
- [Verification and Release](#verification-and-release)
- [License](#license)

## Documentation

Coding agents start with [AGENTS.md](AGENTS.md) (Claude Code loads it through
[CLAUDE.md](CLAUDE.md)). Then read the [product requirements](docs/product/prd.md),
[Revision 4.0](Signal_Production_Engineering_Specification_Revision_4_0.md),
[roadmap](docs/implementation/roadmap.md), and
[current status](docs/implementation/status.md).

| Document | Role |
| --- | --- |
| [Engineering specification, Revision 4.1](Signal_Production_Engineering_Specification_Revision_4_1.md) | Narrow owner-accepted unprotected-default-branch amendment; all other contracts unchanged |
| [Agent working agreement](AGENTS.md) | Current direction, product safety rules, reading order, and slice workflow |
| [Product requirements](docs/product/prd.md) | Customer, job, market position, autonomy, Jev, browser agent, data sources, costs, connectors, releases, and decisions |
| [Engineering specification, Revision 4.0](Signal_Production_Engineering_Specification_Revision_4_0.md) | Active amending contract: requirements, invariants, autonomy, decision layer, browser, connectors, content, releases, edge cases |
| [R1–R4 roadmap](docs/implementation/roadmap.md) | Ordered slices, process tiers, reused foundations, and the next slice |
| [Implementation status](docs/implementation/status.md) | What exists and what was tested |
| [Self-host package](docs/self-host.md) | Owner-operated deployment candidate, private bootstrap and explicit production limits |
| [Decision records](docs/adr/README.md) | Architecture decisions; [ADR-0060](docs/adr/0060-autonomous-seo-employee-direction.md) adopts this direction |
| [Engineering specification, Revision 3.2](Signal_Production_Engineering_Specification_Revision_3_2.md) | Preserved baseline, normative wherever Revision 4.0 is silent: schemas, APIs, state machines, safety invariants, runbooks, and release gates |
| [Product register](PRODUCT.md) | Users, purpose, personality, anti-references, and design principles |
| [Design system](DESIGN.md) | Normative visual tokens and interface guardrails |
| [Revision 3.1](Signal_Production_Engineering_Specification_Corrected.md) and [Revision 3.0](Signal_Production_Engineering_Specification_Final.md) | Preserved historical baselines |
| [Engineering documentation index](docs/README.md), [changelog](CHANGELOG.md), [contribution guide](CONTRIBUTING.md) | Records, runbooks, and workflow |

**Revision 4.1 takes precedence only for owner-accepted unprotected default branches.**
Revision 4.0 takes precedence for new work outside that exception. It amends Revision 3.2, which stays
normative wherever Revision 4.0 is silent. Preserve every accepted revision rather
than editing its history; material scope changes require a new explicit revision
and ADR, with the product, roadmap, status, and repository assertions updated
together.

Useful starting points in the active specification:

- [Product contract](Signal_Production_Engineering_Specification_Revision_4_0.md#section-2) and [requirements](Signal_Production_Engineering_Specification_Revision_4_0.md#section-3)
- [Invariants](Signal_Production_Engineering_Specification_Revision_4_0.md#section-4) (added to [Revision 3.2's](Signal_Production_Engineering_Specification_Revision_3_2.md#section-2))
- [Autonomy and authority](Signal_Production_Engineering_Specification_Revision_4_0.md#section-6) and the [Jev decision layer](Signal_Production_Engineering_Specification_Revision_4_0.md#section-7)
- [Browser agent](Signal_Production_Engineering_Specification_Revision_4_0.md#section-9) and [connectors](Signal_Production_Engineering_Specification_Revision_4_0.md#section-12)
- [Release sequence](Signal_Production_Engineering_Specification_Revision_4_0.md#section-17) and [amendments to Revision 3.2](Signal_Production_Engineering_Specification_Revision_4_0.md#section-21)

## Product Overview

Signal's weekly operating cycle:

```text
Learn the business -> Research -> Plan -> Write and fix -> Gate (policy + Jev)
      -> Ship as pull request -> Verify live -> Measure -> Report -> repeat
```

- **Business Brain:** onboarding crawls the site, reads uploaded brand documents,
  and builds versioned business facts, brand voice, audiences, and competitors with
  provenance.
- **Strategy:** an SEO Baseline and a 90-day plan from the crawl, Search Console,
  Bing Webmaster Tools, AI-assistant citation checks, and optional DataForSEO.
- **Work:** articles, page refreshes, and technical fixes, each grounded in approved
  business facts and delivered as a pull request (Webflow and WordPress later).
- **Proof:** every change links evidence, authorization, the pull request, live
  verification, and measured results at 7, 28, and 90 days.
- **Control:** the owner sees everything in the dashboard and decides escalations in
  the dashboard, Slack, or Telegram.

Customers are startups and small companies without an SEO hire. The managed SaaS is
planned at about $400 per site per month; the engine is source-available and runs
self-hosted with the owner's own model, Jev, and optional DataForSEO keys.

## How Autonomy Works

```text
Language model (OpenAI)            writes the article, fix, or plan
        |
Deterministic policy                 recipe allowed? scope exact? budget and volume left?
        |
Jev decision gate                    ship | ask_owner | reject, with confidence
        |
confident and covered by the standing authorization -> open the pull request
otherwise                                           -> exact request to the owner
```

- Autonomy exists only because the owner granted a standing authorization per site,
  with work types, thresholds, and weekly caps. The owner can tighten or revoke it.
- [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) (TypeSafe AI)
  makes fast typed decisions with calibrated confidence. It can only keep or reduce
  autonomy; a labelled fallback keeps Signal working without it.
- Signal never merges, deploys, pushes to a default branch, deletes content, or edits
  CI workflows.

## Releases

| Release | Customer outcome |
| --- | --- |
| **R1 Insight** | Business Brain, SEO Baseline, AI-visibility baseline, and 90-day Strategy for a real site |
| **R2 Work** | Articles and technical fixes delivered as verified pull requests |
| **R3 Employee** | Weekly autonomous loop, Slack and Telegram, reports with measured results |
| **R4 AI Search and Breadth** | AI-citation optimization, Webflow, WordPress, GA4, Notion, and Google Docs |

The [roadmap](docs/implementation/roadmap.md) lists the ordered slices and next work.
The [sandboxed browser worker](docs/implementation/0067-sandboxed-browser-worker.md)
now has a bounded internal implementation; live-site qualification and production
composition remain unavailable.

## Connectors

| Connector | Access | Release |
| --- | --- | --- |
| Google Search Console | Read only | R1 |
| GitHub App | Code read, then pull requests on one repository; never merge or deploy | R1 / R2 |
| Bing Webmaster Tools | Read; URL submission from R2 | R1 |
| Brand documents | Owner upload | R1 |
| IndexNow | Changed-URL notification for the verified site | R2 |
| Slack, Telegram, email | Approvals, questions, reports | R3 |
| Google Analytics 4 | Read only | R4 |
| Webflow, WordPress | Drafts; publish only with an owner grant | R4 |
| Notion, Google Docs | Read selected pages | R4 |

Every connector is bound to one verified site, least-privilege, revocable, and keeps
credentials in OpenBao. Details are in
[Revision 4.0 section 12](Signal_Production_Engineering_Specification_Revision_4_0.md#section-12).

## Try The Local Pilot

With Docker Desktop running and repository dependencies installed:

```sh
npm run pilot
```

To enable the bounded Luna proposal step without placing the key in shell history
or a repository file, read it silently and pass it only to the pilot process:

```sh
read -rs SIGNAL_OPENAI_API_KEY
SIGNAL_OPENAI_API_KEY="$SIGNAL_OPENAI_API_KEY" npm run pilot
unset SIGNAL_OPENAI_API_KEY
```

Open the exact dashboard URL printed in the terminal and use the synthetic
credentials shown there. The pilot uses `http://localhost:3000` by default and
falls back to the separately allowlisted port `3001` when `3000` is occupied. The
browser journey signs in through real local Keycloak, selects the
seeded organization through the real API and PostgreSQL authority boundary, and
creates one disposable unverified site through the product UI. Open **Work** and
select **Start audit** to watch a real PostgreSQL/outbox/Temporal operation reach a
terminal synthetic manifest without contacting that site. Open **Pages** to inspect
the full manifest identity, digest, coverage, and policy versions. For an origin
you control, use **Connectors** to issue its proof, publish the exact value at the
displayed well-known path, and verify it. Return to **Pages** and select
**Analyze verified homepage** to read one bounded page and inspect its URL, title,
H1, meta-description state, digest, evidence identity, and deterministic finding.
The fixed fixture remains internal pipeline test coverage. Open **Signal Chat**
and select **Prepare proposal**, then open
**Approvals** to inspect the exact evidence, model receipt and hashes, diff,
checks, authority, recovery, expiry, and revision digest before approving,
rejecting, or requesting edits.
The bounded resolver rejects non-public destinations. The proof and page analysis
grant one exact read but no crawl or write authority.
The decision closes only the verified-page draft and performs no external
operation. Ctrl-C stops the processes and destroys the invocation-owned provider
state. See the
[local pilot runbook](docs/runbooks/local-pilot.md) for the exact steps and limits.

This pilot has no customer-site credentials, provider bindings, analytics, GitHub
changes, public-site crawl beyond the optional owner-controlled proof and homepage
GETs in its default mode, external
recovery, or production writes. Its proposal uses one model-backed bounded content
role; policy, scope, and approval remain deterministic and evidence-bound.

For a separately consented local crawl of an origin you control, run
`npm run pilot:verified-crawl`. That mode disables the Work action until the exact
origin is verified, then runs the bounded robots-aware crawl through the shared
egress gateway. It uses a disposable artifact key and store and is not a
production crawler or an R1 release qualification. See the
[local pilot runbook](docs/runbooks/local-pilot.md).

## Architecture

```text
Dashboard / Slack / Telegram -> Authenticated command and approval services
        |
PostgreSQL records and transactional outbox -> Temporal workflows (weekly loop)
        |
Crawler + sandboxed browser workers -> shared egress proxy -> public web
        |
Business Brain, GSC, Bing, AI-visibility, optional DataForSEO evidence
        |
Specialist roles (frontier model) -> sealed candidate revisions
        |
Deterministic policy -> Jev decision gate -> standing authorization / owner decision
        |
Controlled GitHub PR operation -> customer CI and deployment observation
        |
Browser live verification -> 7/28/90-day measurement -> reports
```

Authority reductions, such as revoked approvals, revoked standing authorizations, or
removed members, also go to an independent restriction journal so a database
restore cannot silently revive old access.

| Responsibility | Selected technology or boundary |
| --- | --- |
| Dashboard | Next.js and TypeScript with a server-side session/BFF pattern |
| Command and domain API | FastAPI with typed contracts |
| Durable orchestration | Self-hosted Temporal |
| Business records and retrieval | PostgreSQL, full-text search, and pgvector |
| Reasoning | OpenAI (Luna) Responses API behind an internal adapter |
| Typed decisions | Jev (TypeSafe AI) behind a decision service with a labelled fallback |
| Identity and secrets | Keycloak and OpenBao, with separate ownership |
| Policy | Open Policy Agent with reviewed, signed bundles |
| Integrations | Private connector gateway and capability-specific certified adapters |
| Crawling, browsing, and builds | Controlled HTTP fetching, sandboxed Playwright browser workers behind a shared egress proxy, and credential-free build sandboxes |
| Performance and telemetry | Lighthouse, PageSpeed Insights, CrUX, OpenTelemetry, Prometheus, and Grafana |
| Packaging | Pinned OCI images, Compose, and declarative provisioning |

Self-hosting applies to Signal's control plane, workflows, and state. Model
providers, Jev, the optional DataForSEO, and the customer's GitHub, Google, Bing,
Slack, Telegram, and CMS remain external dependencies. There is no Kubernetes,
Kafka, or separate vector-database requirement for the initial releases.

## Data and Memory

Signal separates business truth, workflow progress, large evidence files, secrets, and operational telemetry.

| Store | Intended contents |
| --- | --- |
| Signal PostgreSQL database | Organizations, sites, grants, standing authorizations, findings, plans, Business Brain facts, decision records, conversation records, analytics, operations, budgets, and audit history |
| PostgreSQL full-text search and pgvector | Searchable knowledge passages and embeddings for retrieving relevant business facts and evidence |
| Temporal's separate PostgreSQL database | Workflow histories, timers, completed activities, and waiting states |
| Keycloak's separate PostgreSQL database | Identity-provider and authentication state |
| Private encrypted artifact storage | HTML, screenshots, patches, reports, exact before/after snapshots, and receipt payloads; PostgreSQL stores references, versions, and hashes |
| OpenBao | Connector credentials and other secrets; ordinary records contain secret references, not reusable tokens |
| Independent journals | Possible external-write intents and authority restrictions outside the main database's recovery boundary |
| Monitoring backends | Health metrics, diagnostic logs, and traces; these are not substitutes for the durable audit ledger |

Within the business database, `app` contains customer-owned records and `control` contains platform-managed configuration and releases. Tenant-owned rows carry `tenant_id`; site-owned rows also carry `site_id`. Scoped foreign keys, current permission checks, and row-level security must prevent both cross-customer and wrong-site access.

Business memory includes approved facts, goals, and prior decisions. Workflow state records what is running or blocked. Evidence history records what was actually observed. Retrieval selects a bounded, authorized context for a model call; neither embeddings nor remembered chat messages confer permission to publish.

Drafts can change. Sealed revisions, approvals, receipts, and audit events retain their history through immutable records or explicit superseding events. Retention and deletion policies must preserve evidence required by unresolved operations and the promised recovery window.

The complete catalog describes 120 logical tables, not 120 independent databases or a requirement to implement every subsystem before the pilot.

## Safety and Recovery

The implementation must satisfy these boundaries before receiving production authority:

- Models, including Jev, cannot approve their own changes, expand permissions, or obtain unrestricted production credentials. A confidence score can only reduce autonomy.
- Autonomy exists only under a human-granted standing authorization with work types, thresholds, and weekly volume and spend caps.
- The browser agent never signs in, submits forms, purchases, or bypasses bot detection, and shares the crawler's egress controls. Search engines and AI-assistant consumer apps are never scraped.
- Generated content asserts only approved business facts; unsupported claims go to the owner.
- Approval covers an exact immutable revision. Relevant changes to the target, permissions, policy, or recipe require revalidation.
- MCP provides connectivity, not automatic safety. Every writable capability needs its own scope, concurrency, idempotency, verification, and recovery contract.
- A lost provider response means an unknown outcome, not a failed write that can be blindly repeated.
- Pause blocks new authority; requests already accepted by a provider may still finish and must be reconciled.
- Undo is an inverse patch or compensation with current-state and dependency checks. It cannot recall viewed content, sent email, or search-engine history.
- Disaster recovery requires independently preserved restrictions, a new recovery generation, reconciliation, and fresh authority before new writes.
- Untrusted website content and repository code cannot become system instructions or access the trusted execution boundary.

Signal does not promise guaranteed rankings, universal CMS compatibility, guaranteed legal compliance, or reversal of every external effect. A successful API response, merged PR, or green build is not proof of verified live delivery.

## Working With This Repository

The repository contains documentation, executable repository checks, disposable
WordPress, Keycloak, OpenBao, and Temporal labs, control-plane persistence and
identity/session modules, an external recovery-generation reader, bounded login,
invitation, tenant-selection, session-lifecycle, and human-command API ingress,
plus deterministic workflow and terminal-projection contracts:

```text
.
|-- README.md
|-- .gitignore
|-- AGENTS.md                # working agreement for every coding agent
|-- CLAUDE.md                # imports AGENTS.md for Claude Code
|-- CONTRIBUTING.md
|-- CHANGELOG.md
|-- apps/                    # FastAPI boundary and Next.js dashboard
|-- database/
|-- package.json
|-- package-lock.json
|-- pyproject.toml
|-- requirements.in
|-- requirements.txt
|-- docs/
|-- scripts/
|-- services/
|-- tests/
|-- Signal_Production_Engineering_Specification_Revision_4_0.md
|-- Signal_Production_Engineering_Specification_Revision_3_2.md
|-- Signal_Production_Engineering_Specification_Corrected.md
`-- Signal_Production_Engineering_Specification_Final.md
```

Use Node.js 22 to run the repository checks:

```sh
npm ci
npm test
```

These commands install locked JavaScript development tooling and validate the
repository. They do not start a customer-facing application. The disposable
`npm run pilot` command is the user-testable local product path. The Python tests and
read-only process command are documented in the [API guide](apps/api/README.md).
The durable command slice uses Python 3.12 and a disposable Docker PostgreSQL lab;
setup and all checks are documented in the [database guide](database/README.md).
No production credentials are needed.

The authority-free workflow-consumer image qualification additionally requires a
working Docker daemon:

```sh
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
```

This builds and removes a local test image. It is not registry publication or a
production deployment; see the [deployment runbook](docs/runbooks/workflow-consumer-deployment.md).

The authority-free crawler HTTP qualification also requires Docker:

```sh
.venv/bin/python scripts/run-crawler-network-tests.py
```

It uses a non-root client and synthetic origin on an internal, non-masqueraded
network with no host publication. It is not public crawl authority; see the
[crawler boundary runbook](docs/runbooks/crawler-http-boundary.md).

Durable crawl-run and frontier admission is qualified with the full disposable
PostgreSQL lab. It is not connected to the HTTP fetcher or Temporal executor; see
the [crawl frontier runbook](docs/runbooks/crawl-frontier.md).

Encrypted local crawl artifacts, append-only fetch observations, integrity
attestations, and bounded orphan reconciliation are qualified separately. They are
not a distributed artifact service or production crawler; see the
[artifact runbook](docs/runbooks/crawl-artifacts.md).

Cross-tenant origin concurrency, minimum spacing, provider backoff, and abandoned-
permit recovery are qualified in PostgreSQL; see the
[origin admission runbook](docs/runbooks/crawl-origin-admission.md).

The durable page-attempt coordinator jointly composes current robots evidence,
global HTML admission, the pinned HTTP boundary, encrypted observation, and exact
permit completion on disposable PostgreSQL and an isolated synthetic network:

```sh
.venv/bin/python scripts/run-page-attempt-tests.py
```

It records dispatch before HTTP and does not blindly repeat an unknown outcome.
It is not frontier/byte settlement, Temporal registration, production egress, or
crawl authority; see the [page-attempt runbook](docs/runbooks/crawl-page-attempts.md).

For implementation work:

1. Read AGENTS.md, the product requirements, Revision 4.0 (and the Revision 3.2
   sections it leaves in force), the roadmap, and current status before choosing one
   bounded slice.
2. Follow the slice order in the R1–R4 roadmap and classify each slice as
   security-critical or product before starting.
3. Map changes to the relevant requirements, invariants, edge cases, and release gates.
4. Keep contracts, schemas, permissions, documentation, and tests consistent. Introduce the proposed repository structure incrementally as working modules are added.
5. Keep secrets, customer captures, local runtime data, and backups out of Git. The `.gitignore` is housekeeping, not a substitute for secret scanning or access controls.
6. Preserve all accepted specification revisions and record material design changes
   in a new explicit revision and superseding ADR.

## Verification and Release

Revisions 4.0 and 3.2 record document-level checks and distinguish historical
abstract-reference results from production evidence. Repository and
real PostgreSQL tests now cover only their documented slices; they do not establish
a working product. The local consumer image test does not prove a published,
signed, monitored deployment. The isolated crawler-network test proves URL,
pinned HTTP, and robots retrieval transport behavior. The joint page-attempt lab
proves their authority-free composition with PostgreSQL records on a synthetic
internal origin. No test proves frontier/byte settlement, workflow execution, or
public egress. The local pilot separately proves workflow execution with a
synthetic no-network activity and disposable state; it does not qualify that
activity as the production crawler.
Production crawler, worker rollout, connector, load, complete recovery,
security-assurance, and user-journey tests remain to be implemented and executed.

Each enabled capability must pass its applicable gates. An excluded capability remains visibly disabled; a mandatory failed safety check cannot be waived while keeping the capability active. Each release (R1–R4) is admitted separately.

Release evidence must identify the exact application, schema, worker, model, recipe, connector, and policy versions evaluated. Document changes or model confidence must never be reported as successful implementation tests.

## License

Signal is source-available under the [Elastic License 2.0](LICENSE).
Copyright 2026 Sricharan ([@Sricharan07](https://github.com/Sricharan07)).

In plain words (the [license text](LICENSE) is what counts):

- **You may** use, copy, modify and self-host Signal, for yourself or for your own
  company's websites.
- **You may not** offer Signal to others as a hosted or managed service.
- **You must** keep the [NOTICE](NOTICE), the license and every copyright notice,
  and mark modified copies as changed.

Signal is not "open source" in the OSI sense, because it restricts hosting it as a
service. Contributions are welcome under the [Contributor License Agreement](CLA.md);
see [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md),
[SECURITY.md](SECURITY.md) and [SUPPORT.md](SUPPORT.md).
