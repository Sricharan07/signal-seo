# Engineering Documentation

| Document | Purpose |
| --- | --- |
| [Revision 4.1](../Signal_Production_Engineering_Specification_Revision_4_1.md) | Narrow owner-accepted unprotected-default-branch amendment; no other guard changed |
| [Owner-accepted unprotected base](implementation/0125-unprotected-base-acceptance.md) | Immutable acceptance, invalidation, dashboard warning and standing-dispatch denial |

| [Page speed and Core Web Vitals](implementation/0128-page-speed.md) | Closed PSI egress, optional OpenBao key, source-separated performance evidence, bounded sampling and owner projection |
| [Agent working agreement](../AGENTS.md) | Current direction, safety rules, reading order, and slice workflow for every coding agent |
| [Product requirements](product/prd.md) | Active product: autonomous SEO employee, customer, autonomy, Jev, browser agent, data sources, connectors, releases, decisions |
| [R1–R4 roadmap](implementation/roadmap.md) | Active ordered slices, process tiers, reused foundations, and the next slice |
| [Core V1 PRD](product/core-v1-prd.md) | Superseded Core V1 product scope, preserved as history |
| [Core V1 roadmap](implementation/core-v1-roadmap.md) | Superseded Core V1 sequence, preserved as history |
| [Implementation status](implementation/status.md) | What exists, what was tested, and what is next |
| [GA4 binding and import](implementation/0097-ga4-binding.md) | Optional read-only owner property binding, bounded report evidence, incomplete coverage, and durable disconnect |

| [Self-host package](self-host.md) | Generic private deployment candidate, owner bootstrap, optional provider storage, backups and upgrades |
| [Self-host implementation](implementation/0130-self-host.md) | Security boundaries, runnable component evidence and NOT_EXECUTED full-stack qualification |
| [Repository baseline](implementation/0001-repository.md) | Reproducible repository checks and development conventions |
| [WordPress experiment](implementation/0002-wordpress-feasibility.md) | Real provider evidence and explicit capability limits |
| [Durable commands](implementation/0003-durable-commands.md) | PostgreSQL persistence and verification evidence |
| [Session-derived authorization](implementation/0004-session-derived-authorization.md) | Opaque sessions and current tenant/site authority checks |
| [Quality CI](implementation/0005-quality-ci.md) | Least-privilege GitHub quality workflow and local verification |
| [Read-only API](implementation/0006-read-only-api.md) | Runnable health/capability boundary with fail-closed readiness and safe errors |
| [Browser mutation security](implementation/0007-browser-mutation-security.md) | Host-only cookie and session-bound CSRF/origin proof primitive |
| [Durable OIDC login attempts](implementation/0008-durable-oidc-login-attempts.md) | Hash-only, pre-tenant state/nonce/browser-binding persistence |
| [Keycloak OIDC protocol](implementation/0009-keycloak-oidc-protocol.md) | Strict authorization-code client and disposable real-provider evidence |
| [Scoped session issuance](implementation/0010-scoped-session-issuance.md) | Hash-only global and tenant sessions with current membership checks |
| [OpenBao PKCE secrets](implementation/0011-openbao-pkce-secrets.md) | One-login verifier storage with scoped credentials and real-server evidence |
| [OIDC login composition](implementation/0012-oidc-login-composition.md) | Fail-closed internal initiation and callback ordering across identity boundaries |
| [OpenBao recovery authority](implementation/0013-openbao-recovery-authority.md) | Independently anchored generation reads and fail-closed callback composition |
| [Identity session audit](implementation/0014-identity-session-audit.md) | Atomic append-only event for successful hash-only session issuance |
| [Consumed login failure audit](implementation/0015-consumed-login-failure-audit.md) | Proof-gated sanitized events for failures after attempt consumption |
| [Site invitation issuance](implementation/0016-site-invitation-issuance.md) | Hash-only, authority-locked one-site invitations with atomic tenant audit evidence |
| [Verified invitation acceptance](implementation/0017-invitation-acceptance.md) | Verified OIDC binding with atomic identity and one-site authority provisioning |
| [Bounded OIDC HTTP ingress](implementation/0018-oidc-http-ingress.md) | Existing-user login routes with one-time browser binding and pre-tenant cookies |
| [Explicit tenant selection](implementation/0019-tenant-selection.md) | Hash-scoped membership listing and CSRF-protected tenant-session exchange |
| [Current session and logout](implementation/0020-session-lifecycle.md) | Live tenant-session inspection and atomic audited parent-session revocation |
| [Invitation identity proofs](implementation/0021-invitation-identity-proofs.md) | Purpose-bound OIDC attempts and hash-only short-lived invitee proof issuance |
| [Atomic invitation proof acceptance](implementation/0022-atomic-invitation-proof-acceptance.md) | One-transaction proof and invitation consumption with bounded cleanup |
| [Invitation browser acceptance](implementation/0023-invitation-browser-acceptance.md) | Purpose-bound callback, proof cookie, CSRF, and body-only atomic acceptance ingress |
| [Human command authority](implementation/0024-human-command-authority.md) | Atomic live authorization, actor attribution, durable intent, and own-command status |
| [Human command HTTP ingress](implementation/0025-human-command-http-ingress.md) | CSRF-protected durable snapshot acceptance and live-authority status routes |
| [Lease-safe outbox dispatch](implementation/0026-lease-safe-outbox-dispatch.md) | Tenant-bounded event claims, attempt fencing, retry scheduling, and delivery acknowledgement |
| [Deduplicated workflow admission](implementation/0027-deduplicated-workflow-admission.md) | Exact-envelope inbox receipts, stable workflow identity, and durable command progress |
| [Bounded outbox worker](implementation/0028-bounded-outbox-worker.md) | Stoppable delivery cycles, explicit publish outcomes, and sanitized observations |
| [Deterministic Temporal workflow start](implementation/0029-temporal-workflow-start.md) | No-reuse workflow start, first-run evidence, atomic progress, and real-server qualification |
| [Core V1 contract revision](implementation/0030-core-v1-contract-revision.md) | Documentation-only GitHub-first scope and sequence change |
| [Workflow command consumer](implementation/0031-workflow-command-consumer.md) | Ack-after-record composition, cooperative shutdown, strict runtime configuration, and joint PostgreSQL/Temporal evidence |
| [Crawl workflow state](implementation/0032-crawl-workflow-state.md) | Deterministic activity policy, terminal projection, cancellation, replay, and joint boundary evidence |
| [Workflow consumer packaging](implementation/0033-workflow-consumer-packaging.md) | Release-bound readiness, non-root OCI packaging, private Compose supervision, and real image evidence |
| [Crawl URL and network boundary](implementation/0034-crawl-url-and-network-boundary.md) | Exact URL/origin identity, public-address admission, pinned socket transport, and isolated real-network evidence |
| [Durable crawl run and frontier](implementation/0035-durable-crawl-run-frontier.md) | Immutable run snapshots, stable URL inventory, provenance, budgets, and lease-safe PostgreSQL admission |
| [Encrypted artifact and fetch observations](implementation/0036-encrypted-artifact-fetch-observations.md) | AES-GCM local objects, lease-bound observations, integrity attestations, and orphan reconciliation |
| [RFC-aware robots snapshots](implementation/0037-rfc-aware-robots-snapshots.md) | Pinned robots retrieval/parser profile, immutable expiring evidence, and fail-closed cached decisions |
| [Global origin admission](implementation/0038-global-origin-admission.md) | Cross-tenant request concurrency, shared politeness/backoff, exact permits, and expiry reconciliation |
| [Durable crawl page attempts](implementation/0039-durable-crawl-page-attempts.md) | Pre-dispatch receipts, robots/permit/fetch/observation composition, and conservative retry recovery |
| [Operational dashboard shell](implementation/0040-operational-dashboard-shell.md) | Responsive server-rendered Overview with real readiness/capability state and explicit unavailable boundaries |
| [Dashboard session read boundary](implementation/0041-dashboard-session-read-boundary.md) | Server-only exact-cookie forwarding and bounded current-authority projection |
| [Authenticated site context](implementation/0042-authenticated-site-context.md) | Hash-bound site directory from forced-RLS PostgreSQL authority to the dashboard |
| [Dashboard identity BFF](implementation/0043-dashboard-identity-bff.md) | Same-origin login, organization selection, logout, and local browser-state cleanup |
| [Server-owned active-site context](implementation/0044-active-site-context.md) | Versioned session selection, immutable transition evidence, and same-origin dashboard control |
| [Owner-controlled site onboarding](implementation/0045-site-onboarding.md) | Atomic unverified site creation, owner grant, active context, and immutable evidence |
| [Reference-led dashboard surfaces](implementation/0046-reference-led-dashboard-surfaces.md) | Full navigable dashboard information architecture with truthful unavailable states and responsive qualification |
| [Full-viewport dashboard polish](implementation/0047-full-viewport-dashboard-polish.md) | Edge-to-edge dashboard frame, domain icons, safety banner, and truthful evidence-empty chart structure |
| [Exact public-origin verification](implementation/0048-origin-verification.md) | Owner-gated expiring plaintext proof, immutable outcomes, and global exact-origin claims |
| [Dashboard verification and UI hardening](implementation/0049-dashboard-origin-verification.md) | Same-origin owner proof controls, strict BFF validation, honest graph surfaces, and responsive repair |
| [Read-only GSC property discovery](implementation/0050-gsc-property-discovery.md) | Fixed least-privilege provider boundary and exact verified-origin property matching |
| [Disposable local owner journey](implementation/0051-disposable-local-owner-journey.md) | One-command real local identity, organization, and owner onboarding composition |
| [Visible durable Work flow](implementation/0052-visible-durable-work-flow.md) | Browser-started PostgreSQL/Temporal work with a truthful synthetic terminal receipt |
| [Inspectable audit evidence](implementation/0053-inspectable-audit-evidence.md) | Full manifest provenance on Pages and real latest-work state on Overview without invented findings |
| [Durable fixture finding](implementation/0054-durable-fixture-finding.md) | Authenticated fixture detector, immutable evidence, current finding projection, and visible provenance without customer-site claims |
| [Collision-safe local pilot port](implementation/0055-collision-safe-local-pilot-port.md) | Exact alternate dashboard port selection without interrupting unrelated local services or broadening browser authority |
| [Supervised local proposal flow](implementation/0056-supervised-local-proposal-flow.md) | Signal Chat preparation, RFC 8785-sealed fixture revision, and exact immutable owner decision without external dispatch |
| [Model-backed fixture proposal](implementation/0057-model-backed-fixture-proposal.md) | Bounded Luna drafting with durable intent, OpenBao credential isolation, exact provenance, and unchanged human authority |
| [Bounded system resolver](implementation/0058-bounded-system-resolver.md) | Deadline- and capacity-bounded host resolution composed into the pilot's pinned public-origin proof |
| [Verified homepage observation](implementation/0059-verified-homepage-observation.md) | Owner-authorized homepage metadata read, immutable evidence, deterministic finding, and visible provenance |
| [Verified homepage model proposal](implementation/0060-verified-homepage-model-proposal.md) | Evidence-bound Luna metadata draft, exact immutable revision, and owner decision without external dispatch |
| [Approved change continuation](implementation/0061-approved-change-continuation.md) | Exact approved revision projected through honest repository, candidate, PR, and live-verification stages without inventing delivery |
| [GitHub App repository inspection](implementation/0062-github-app-repository-inspection.md) | App-signed, repository-scoped, read-only provider inspection for exact repository and base identity |
| [Autonomous direction revision](implementation/0063-autonomous-direction-revision.md) | Documentation-only adoption of Revision 4.0, the PRD, ADR-0060, and the R1–R4 roadmap |
| [Jev decision client](implementation/0064-jev-decision-client.md) | Recommendation-only typed Jev boundary, conservative fallback, immutable decision evidence, and live qualification limit |
| [Sandboxed browser worker](implementation/0067-sandboxed-browser-worker.md) | Internal disposable Chromium sessions, proxy-only reads, bounded Jev links, encrypted snapshots and immutable scoped evidence |
| [Autonomy-to-delivery integration](implementation/0104-autonomy-delivery-integration.md) | Two exact authorization kinds, closed A2 eligibility, journaled PR delivery, weekly evidence and live qualification limits |
| [Closed development base VM](implementation/0105-closed-development-base-vm.md) | Owner-requested Lightsail capacity, restricted SSH, manual bootstrap recovery and no application readiness claim |
| [Owner-approved integration test scope](implementation/0106-owner-approved-integration-test-scope.md) | Explicit human exception for dedicated HTTPS integration testing without production or runtime write authority |
| [Private integration secret store](implementation/0107-private-integration-secret-store.md) | Persistent private TLS OpenBao, owner-only recovery, hidden key entry and real disposable restore qualification |
| [Private integration secrets runbook](runbooks/integration-secrets.md) | Operator-only provisioning, hidden input, seal/restart, encrypted backup and disposable restore commands |
| [Private persistent test identity](implementation/0110-private-test-identity.md) | Real private Keycloak/PostgreSQL qualification without public login or readiness claims |
| [Dedicated test login ingress](implementation/0111-dedicated-test-login-ingress.md) | Real HTTPS/private application qualification with human owner and connector gates still pending |
| [Incomplete test identity recovery](implementation/0112-incomplete-test-identity-recovery.md) | Approved one-record repair with encrypted recovery and real rollback qualification; no account linking or MFA bypass |
| [Integration application runbook](runbooks/integration-application.md) | Exact public test boundary, private workload/bootstrap lifecycle, expiring identity admission and human gates |
| [Private identity runbook](runbooks/integration-identity.md) | Create-only material, private deployment, rejection checks and explicit restart/recovery gates |
| [IndexNow](implementation/0086-indexnow.md) | Owner-reviewed key publication, exact deployed-key checks, verified-change notification and unknown-outcome limits |
| [Workflow consumer runbook](runbooks/workflow-command-consumer.md) | Development startup, transport, observations, shutdown, and first-safe-action procedures |
| [Workflow consumer deployment runbook](runbooks/workflow-consumer-deployment.md) | Package preflight, immutable rollout, health, drain, and rollback boundaries |
| [Crawler HTTP boundary runbook](runbooks/crawler-http-boundary.md) | Isolated network qualification, evidence, failure diagnosis, and production no-go boundary |
| [Crawl run and frontier runbook](runbooks/crawl-frontier.md) | PostgreSQL qualification, state interpretation, lease handling, and production no-go boundary |
| [Crawl artifact runbook](runbooks/crawl-artifacts.md) | Private storage, integrity/restore states, orphan reconciliation, and production no-go boundary |
| [Crawl robots runbook](runbooks/crawl-robots.md) | Retrieval/status policy, cache interpretation, parser upgrades, incidents, and production no-go boundary |
| [Crawl origin admission runbook](runbooks/crawl-origin-admission.md) | Global permit interpretation, shared backoff, expiry reconciliation, and production no-go boundary |
| [Crawl page attempt runbook](runbooks/crawl-page-attempts.md) | Joint qualification, terminal/unknown outcomes, recovery, and production no-go boundary |
| [Crawl workflow runbook](runbooks/crawl-workflow.md) | Terminal-state interpretation, development diagnostics, replay, and recovery rules |
| [Dashboard identity runbook](runbooks/dashboard-identity.md) | Same-origin identity configuration, journey, diagnosis, cleanup, and production no-go boundary |
| [Active-site context runbook](runbooks/active-site-context.md) | Selection state, concurrency conflicts, database diagnosis, and recovery boundaries |
| [Site onboarding runbook](runbooks/site-onboarding.md) | Owner creation state, replay/conflict diagnosis, recovery, and origin-proof boundary |
| [Origin verification runbook](runbooks/origin-verification.md) | Exact challenge operation, proof outcomes, claim conflicts, recovery, and production no-go boundary |
| [GSC property discovery runbook](runbooks/gsc-property-discovery.md) | Fixed-endpoint qualification, failure interpretation, and production no-go boundary |
| [Local pilot runbook](runbooks/local-pilot.md) | Disposable real-stack startup, browser journey, shutdown, and safety boundaries |
| [API guide](../apps/api/README.md) | Current routes, local process command, and explicit service limits |
| [Dashboard guide](../apps/dashboard/README.md) | Local startup, server-side data boundary, tests, security, and explicit UI limits |
| [Control-plane guide](../services/control_plane/README.md) | Internal service ownership, role boundaries, and deployment limits |
| [Database guide](../database/README.md) | Data ownership, migrations, permissions, and local test commands |
| [Decision index](adr/README.md) | Architectural decisions, alternatives, and consequences |
| [Contributing](../CONTRIBUTING.md) | Slice, test, documentation, and commit workflow |
| [Changelog](../CHANGELOG.md) | Completed changes, grouped by implementation slice |
| [Product register](../PRODUCT.md) | Intended users, purpose, personality, anti-references, and design principles |
| [Design system](../DESIGN.md) | Normative visual tokens and product-interface guardrails |
| [Active specification, Revision 4.0](../Signal_Production_Engineering_Specification_Revision_4_0.md) | Amending revision: autonomous employee contract, invariants, Jev, browser agent, connectors, releases |
| [Specification, Revision 3.2](../Signal_Production_Engineering_Specification_Revision_3_2.md) | Preserved baseline; normative wherever Revision 4.0 is silent (schemas, APIs, safety, gates) |
| [Preserved specification, Revision 3.1](../Signal_Production_Engineering_Specification_Corrected.md) | Reviewed safety correction baseline superseded by Revision 3.2 |
| [Preserved specification, Revision 3.0](../Signal_Production_Engineering_Specification_Final.md) | Original reviewed baseline |

Implementation records describe the code as it exists. The specification describes
the target. When they differ, record the limitation or approved deviation rather
than silently presenting the target as delivered. Revision 4.0 takes precedence for
new work and amends Revision 3.2, which stays normative wherever Revision 4.0 is
silent. Revisions 3.1 and 3.0 remain unchanged historical baselines.
