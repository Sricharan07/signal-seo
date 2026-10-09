# Architecture Decisions

Ask Signal grounded prose and its reduction-only budgeted verification are
recorded in [ADR-0176](0176-ask-signal-grounded-prose.md).

Slice 0155: [ADR-0165](0165-provider-data-in-strategy.md), dual-budget DataForSEO
research in Strategy and owner/weekly Bing page data with existing authority and
per-source provenance retained.

Slice 0141: [ADR-0151](0151-evidence-only-keyword-topics.md), deterministic observed
topics with optional labelled budgeted ideas and unchanged proposal authority.

Latest dedicated test composition: [ADR-0100](0100-dedicated-test-origin-proof.md).

| ID | Decision | Status |
| --- | --- | --- |
| [0168](0168-owner-team-invitations.md) | Owner-only fresh-MFA team invitations and purpose-bound OIDC acceptance | Accepted for local boundary |
| [0104](0104-optional-site-bound-dataforseo.md) | Keep optional DataForSEO credentials site-bound behind closed shared egress | Accepted for local internal boundary |
| [0105](0105-reserve-paid-research-before-egress.md) | Reserve paid research before egress and never replay an ambiguous charge | Accepted for local internal boundary |

| [0123](0123-proposal-only-ai-visibility-agent.md) | Proposal-only visibility gaps and existing brief handoff; broader recipes and scheduling unavailable until 0131 | Accepted |

| [0125](0125-closed-pagespeed-egress.md) | Admit exact PSI requests through current-owner shared egress and keep optional query keys in OpenBao | Accepted |
| [0126](0126-source-separated-core-web-vitals.md) | Keep page/origin field evidence distinct from lab data and bound weekly sampling/daily admission | Accepted |
| [0001](0001-incremental-delivery.md) | Incremental delivery with evidence and preserved specification baselines | Accepted; future pilot sequence superseded by ADR-0030 |
| [0002](0002-wordpress-lab-boundary.md) | Provider experiment versus execution authority | Accepted |
| [0003](0003-durable-command-foundations.md) | Narrow durable commands before public APIs | Accepted |
| [0004](0004-session-derived-site-authorization.md) | Derive site scope from current server-side authority | Accepted |
| [0005](0005-least-privilege-ci.md) | Run quality gates in least-privilege GitHub CI | Accepted |
| [0006](0006-truthful-read-only-api-boundary.md) | Establish a truthful read-only API boundary first | Accepted |
| [0007](0007-session-bound-browser-mutation-proof.md) | Require session-bound browser mutation proofs | Accepted |
| [0008](0008-durable-pre-tenant-oidc-attempts.md) | Persist pre-tenant OIDC attempts as one-time hashed records | Accepted |
| [0009](0009-keycloak-oidc-protocol.md) | Qualify a narrow Keycloak OIDC protocol boundary | Accepted |
| [0010](0010-scoped-hash-only-session-issuance.md) | Issue scoped hash-only application sessions | Accepted |
| [0011](0011-openbao-pkce-secret-boundary.md) | Keep PKCE verifiers in a narrow OpenBao boundary | Accepted |
| [0012](0012-fail-closed-oidc-login-composition.md) | Compose OIDC login as a fail-closed pipeline | Accepted |
| [0013](0013-external-recovery-generation-authority.md) | Anchor recovery generation outside the business database | Accepted |
| [0014](0014-atomic-identity-session-audit.md) | Commit identity session audit with issuance | Accepted |
| [0015](0015-proof-gated-consumed-login-failure-audit.md) | Gate login failure audit on consumed proofs | Accepted |
| [0016](0016-authority-locked-site-invitations.md) | Issue site invitations under locked current authority | Accepted |
| [0017](0017-verified-invitation-acceptance.md) | Bind invitation acceptance to verified identity | Accepted |
| [0018](0018-bounded-oidc-http-ingress.md) | Expose OIDC login through a bounded HTTP ingress | Accepted |
| [0019](0019-hash-scoped-membership-directory.md) | Route pre-tenant membership discovery through a hash-scoped directory | Accepted |
| [0020](0020-audited-browser-session-revocation.md) | Revoke the parent identity session on browser logout | Accepted |
| [0021](0021-purpose-bound-invitation-identity-proofs.md) | Separate invitation identity proofs from login sessions | Accepted |
| [0022](0022-atomic-invitation-proof-acceptance.md) | Consume invitation proof and authority atomically | Accepted |
| [0023](0023-dedicated-invitation-browser-proof.md) | Expose invitation acceptance through a dedicated browser proof | Accepted |
| [0024](0024-atomic-human-command-authority.md) | Bind human commands to live authority in one transaction | Accepted |
| [0025](0025-bounded-human-command-http-ingress.md) | Expose harmless human commands through bounded HTTP ingress | Accepted |
| [0026](0026-lease-safe-outbox-dispatch.md) | Dispatch outbox events with short fenced leases | Accepted |
| [0027](0027-deduplicated-workflow-admission.md) | Admit workflow work before calling the orchestrator | Accepted |
| [0028](0028-bounded-outbox-worker.md) | Keep publication outside short database operations | Accepted |
| [0029](0029-deterministic-temporal-workflow-start.md) | Preserve one logical workflow across start ambiguity | Accepted |
| [0030](0030-github-first-core-v1.md) | Make the first real product journey GitHub-first with all nine non-CMS roles | Accepted; scope and sequence superseded by ADR-0060 |
| [0031](0031-ack-after-durable-workflow-start.md) | Acknowledge delivery only after durable workflow start | Accepted |
| [0032](0032-durable-crawl-terminal-projection.md) | Project crawl completion before closing the workflow | Accepted |
| [0033](0033-release-bound-consumer-health-and-packaging.md) | Bind consumer health and packaging to an immutable release | Accepted |
| [0034](0034-pin-crawl-connections-to-admitted-public-addresses.md) | Pin crawl connections to admitted public addresses | Accepted |
| [0035](0035-bind-crawl-leases-to-immutable-run-snapshots.md) | Bind crawl leases to immutable run snapshots | Accepted |
| [0036](0036-encrypt-artifacts-before-authoritative-registration.md) | Encrypt artifacts before authoritative registration | Accepted |
| [0037](0037-persist-robots-before-page-admission.md) | Persist robots evidence before page admission | Accepted |
| [0038](0038-admit-every-crawl-request-through-a-global-origin-bucket.md) | Admit every crawl request through a global origin bucket | Accepted |
| [0039](0039-record-dispatch-before-composing-crawl-page-io.md) | Record dispatch before composing crawl page I/O | Accepted |
| [0040](0040-server-rendered-dashboard-truth-boundary.md) | Render dashboard truth through a server-only read boundary | Accepted |
| [0041](0041-forward-only-one-validated-session-cookie.md) | Forward only one validated tenant session cookie | Accepted |
| [0042](0042-hash-bound-site-directory.md) | Enumerate sites through a hash-bound authority directory | Accepted |
| [0043](0043-same-origin-dashboard-identity-bff.md) | Terminate browser identity at a same-origin dashboard BFF | Accepted |
| [0044](0044-server-owned-active-site-context.md) | Persist active site in the server session | Accepted |
| [0045](0045-owner-controlled-site-onboarding.md) | Onboard sites through owner authority | Accepted |
| [0046](0046-truthful-full-dashboard-information-architecture.md) | Expose the full dashboard information architecture without synthetic state | Accepted |
| [0047](0047-full-viewport-evidence-first-dashboard.md) | Use a full-viewport evidence-first dashboard frame | Accepted |
| [0048](0048-exact-public-origin-verification.md) | Verify one exact public origin before binding authority | Accepted |
| [0049](0049-same-origin-dashboard-verification-bff.md) | Complete dashboard verification behind a same-origin BFF | Accepted |
| [0050](0050-fixed-read-only-gsc-property-discovery.md) | Fix Search Console discovery to one read-only boundary | Accepted |
| [0051](0051-compose-a-disposable-local-owner-journey.md) | Compose a disposable local owner journey | Accepted |
| [0052](0052-prioritize-a-user-testable-durable-work-flow.md) | Prioritize a user-testable durable Work flow | Accepted |
| [0053](0053-project-manifest-evidence-without-inventing-findings.md) | Project manifest evidence without inventing findings | Accepted |
| [0054](0054-prove-the-finding-pipeline-with-a-fixed-fixture.md) | Prove the finding pipeline with a fixed fixture | Accepted |
| [0055](0055-supervise-local-proposals-with-exact-immutable-decisions.md) | Supervise local proposals with exact immutable decisions | Accepted |
| [0056](0056-bound-luna-drafting-to-durable-supervision.md) | Bound Luna drafting to durable supervision | Accepted |
| [0057](0057-bind-homepage-evidence-to-current-owner-proof.md) | Bind homepage evidence to current owner proof | Accepted |
| [0058](0058-bind-luna-drafts-to-verified-page-evidence.md) | Bind Luna drafts to verified page evidence | Accepted |
| [0059](0059-downscope-github-app-repository-inspection.md) | Downscope GitHub App inspection tokens | Accepted |
| [0060](0060-autonomous-seo-employee-direction.md) | Adopt the autonomous SEO employee direction (Revision 4.0) | Accepted |
| [0061](0061-isolate-jev-behind-a-recommendation-only-boundary.md) | Isolate Jev behind a recommendation-only boundary | Accepted |
| [0062](0062-record-shared-egress-before-network-io.md) | Record shared egress before network I/O | Accepted |
| [0063](0063-settle-every-crawl-frontier-with-evidence.md) | Settle every crawl frontier with evidence | Accepted |
| [0064](0064-prioritize-github-first-beta-path.md) | Prioritize a GitHub-first beta path without changing release gates | Accepted |
| [0065](0065-seal-crawl-audit-evidence-under-ingest-role.md) | Seal crawl audit evidence under a narrow ingest-role grant | Accepted |
| [0066](0066-bind-github-read-authority-to-owner-site.md) | Bind GitHub read authority to one owner and site | Accepted |
| [0067](0067-observe-github-pr-permission-separately.md) | Observe PR permission separately from repository writes | Accepted |
| [0068](0068-isolate-candidate-builds-before-repository-writes.md) | Isolate candidate builds before repository writes | Accepted |
| [0070](0070-independent-authority-restriction-journal.md) | Keep restrictions outside primary rollback | Accepted for internal foundation |
| [0071](0071-platform-reviewed-recipe-release-registry.md) | Keep recipe releases platform-owned and immutable | Accepted for internal foundation |
| [0078](0078-isolate-browser-egress-through-a-policy-pipe.md) | Isolate browser egress through a credential-free proxy and trusted policy pipe | Accepted for internal boundary |
| [0079](0079-bound-browser-choice-to-recorded-link-ids.md) | Bound browser choices to recorded link ids and immutable step evidence | Accepted for internal boundary |
| [0080](0080-gsc-read-only-owner-binding.md) | Bind read-only Search Console properties through owner proof and OpenBao | Accepted |
| [0081](0081-bing-read-only-source-evidence.md) | Bind read-only Bing sites with separate performance and inbound-link evidence | Accepted |
| [0082](0082-assistant-provider-boundaries.md) | Keep assistant APIs in bounded, evidence-linked model egress | Accepted |
| [0083](0083-scope-shared-egress-by-profile.md) | Scope shared egress by origin-bound request profiles | Accepted |
| [0072](0072-bind-standing-grants-to-reviewed-releases-and-recovery.md) | Bind owner standing grants to exact reviewed releases and recovery | Accepted for internal foundation |
| [0073](0073-keep-jev-below-deterministic-authority.md) | Keep Jev recommendations below deterministic authority | Accepted for internal foundation |
| [0074](0074-keep-weekly-work-record-only-until-pr-integration.md) | Keep weekly work record-only until PR integration | Accepted for internal foundation |
| [0075](0075-bind-slack-actions-to-current-person-and-revision.md) | Bind Slack actions to current person and exact revision | Accepted for internal optional boundary |
| [0077](0077-provider-reported-change-measurements.md) | Preserve provider-reported change measurements without completeness or causation claims | Accepted for local product implementation |
| [0076](0076-private-telegram-exact-revision-approvals.md) | Keep Telegram private and bound to exact revisions | Accepted for internal optional boundary |
| [0084](0084-owner-brand-document-boundary.md) | Keep owner brand documents encrypted and untrusted | Accepted for local R1 boundary |
| [0090](0090-journal-git-objects-before-pr.md) | Journal exact Git objects before opening a PR | Accepted for internal beta path |
| [0091](0091-independent-live-observation.md) | Independently observe exact customer deployment and live results | Accepted for internal beta path |
| [0092](0092-repository-bound-github-write-profile.md) | Bind GitHub writes to one repository and installation token | Accepted for internal beta path |
| [0093](0093-exact-standing-authority-delivery.md) | Bind exact eligible technical delivery to one owner approval or standing dispatch | Accepted for internal beta path |
| [0094](0094-owner-authorized-integration-testing.md) | Record owner-authorized dedicated HTTPS testing while keeping administration private and product invariants unchanged | Accepted for operator-managed testing |
| [0095](0095-private-persistent-integration-secrets.md) | Private persistent TLS OpenBao with independently held recovery material for live test credentials | Accepted for operator-managed testing |
| [0096](0096-private-persistent-test-identity.md) | Private persistent identity with exact Google broker/client boundaries and no public readiness claim | Accepted for operator-managed testing |
| [0097](0097-dedicated-test-login-ingress.md) | Dedicated HTTPS login with private scoped workloads, real OTP policy and disabled unqualified connectors | Accepted for operator-managed testing |
| [0087](0087-evidence-only-onboarding-strategy.md) | Pin evidence-only onboarding snapshots and proposal-only decisions | Accepted for local product implementation |
| [0102](0102-owner-reviewed-indexnow-key-publication.md) | Publish OpenBao-backed IndexNow keys through owner-reviewed PRs only | Accepted for internal qualification |
| [0103](0103-verified-change-indexnow-egress.md) | Notify only committed verified changes through closed IndexNow egress | Accepted for internal qualification |

| [0106](0106-closed-tls-smtp-submission.md) | Add a closed TLS SMTP submission path to shared egress | Accepted for internal optional boundary |
| [0107](0107-identity-verified-email-opt-in.md) | Require same-person IdP verification and token-free dashboard opt-in | Accepted for internal optional boundary |
| [0108](0108-exact-owner-editorial-delivery.md) | Bind exact owner-approved article delivery to dashboard MFA authority | Accepted for internal beta path |
| [0109](0109-article-currentness-and-observation.md) | Recheck article currentness, output caps and exact live artifacts | Accepted for internal beta path |

| [0110](0110-core-rest-wordpress-drafts-only.md) | Keep WordPress Core REST delivery draft-only | Accepted for internal optional boundary |
| [0111](0111-wordpress-authority-and-egress.md) | Bind WordPress draft dispatch to current authority and journal | Accepted for internal optional boundary |
| [0112](0112-google-docs-selected-read-boundary.md) | Read only owner-selected Google Docs through the shared connector boundary | Accepted for internal optional boundary |
| [0116](0116-webflow-draft-only-without-atomic-preconditions.md) | Keep Webflow draft-only without a documented atomic precondition | Accepted for local R4 boundary |
| [0117](0117-webflow-bound-draft-delivery.md) | Bind Webflow draft creation to OAuth, owner mapping and exact Inbox approval | Accepted local boundary; live NOT_EXECUTED |

| [0120](0120-owner-accepted-unprotected-base.md) | Explicit fresh-MFA owner acceptance of an unprotected default branch, with immutable invalidation and no standing dispatch | Accepted for internal boundary; live GitHub NOT_EXECUTED |
| [0121](0121-astro-offline-build-boundary.md) | Closed integrity-bound registry cache and credential-free offline Astro build, without delivery authority | Accepted for internal qualification; live providers NOT_EXECUTED |
| [0122](0122-exact-owner-approved-astro-delivery.md) | Exact source fragments and paired all-HTML scope receipts with owner Inbox and fresh MFA | Accepted for internal qualification; live providers NOT_EXECUTED |

| [0129](0129-separate-generic-self-host-topology.md) | Separate generic self-host topology without transferring dedicated test qualification | Accepted for deployment candidate |
| [0130](0130-quorum-and-proof-bound-self-host-bootstrap.md) | Quorum recovery and signed OTP proof-bound first-owner bootstrap | Accepted for deployment candidate |

F1 decisions: [0147](0147-exact-front-matter-content-adapter.md) and
[0148](0148-front-matter-offline-toolchain-availability.md).

Next.js metadata delivery records [ADR-0155](0155-exact-nextjs-metadata-delivery.md)
and [ADR-0156](0156-nextjs-offline-static-export-verification.md) cover exact
Owner/MFA source changes and fail-closed offline static-export verification.

Grounded structured data and capped visibility scheduling use
[ADR-0131](0131-owner-reviewed-grounded-structured-data.md) and
[ADR-0132](0132-reserved-ai-visibility-reobservation.md).

| [0136](0136-chat-report-outbox-and-unknown-outcomes.md) | Deliver bounded chat reports with terminal unknown outcomes | Accepted for internal local qualification |
| [0137](0137-current-owner-chat-report-preferences.md) | Pin token-free report consent to current owner destinations | Accepted for internal local qualification |

Writing quality, model budgets and claim grounding are recorded in
[ADR-0142](0142-role-models-writing-quality-and-monthly-budget.md) and
[ADR-0143](0143-claim-level-grounding.md).

Weekly standing skill ports and durable budgets are recorded in
[ADR-0140](0140-standing-grant-weekly-skill-ports.md) and
[ADR-0141](0141-durable-weekly-skill-budgets-and-outcomes.md).

| [0144](0144-site-local-observed-effectiveness.md) | Site-local confounder-weighted neutral shrinkage and bounded observed priority | Accepted for local product implementation |
| [0145](0145-evidence-only-seasonal-page-decay.md) | Seasonal evidence-only page decay and unaccepted refresh proposals | Accepted for local product implementation |

| [0153](0153-private-health-observations-and-alerts.md) | Private scheduled health observations and deduplicated alerts through existing verified-owner channels | Accepted for local security-critical implementation |
| [0159](0159-owner-pr-write-grant.md) | Expose current-owner PR permission with fresh MFA and independent deny-only revocation | Accepted for local security-critical implementation |

| [0164](0164-owner-ai-question-sets.md) | Owner-approved immutable AI question versions and accepted grounded visibility proposals into the Candidate Inbox | Accepted for local implementation |

Each new record must include context, decision, alternatives, consequences, and
verification. Superseding a decision requires a new record and links in both directions.

Internal link candidates and weekly proposal ports are recorded in
[ADR-0149](0149-reviewed-contextual-internal-links.md) and
[ADR-0150](0150-weekly-internal-link-candidate-port.md).

Standing-scoped PSI execution and verified weekly chat wiring are recorded in
[ADR-0157](0157-standing-scoped-pagespeed-weekly-execution.md) and
[ADR-0158](0158-weekly-observation-and-verified-chat-wiring.md).
Owner publishing paths are recorded in
[ADR-0166](0166-owner-publishing-paths.md): bounded owner OAuth, exact approved
article sealing, operator-provisioned WordPress and unchanged CMS write gates.

Slice 0129 decisions: [ADR-0127](0127-bing-top-page-api-contract.md), documented
Bing top-page contract, and [ADR-0128](0128-bing-page-read-port.md), incomplete
generations and the restricted measurement read port. Live Bing NOT_EXECUTED.

Migrator-owned SQL dispatch catalogs and extension procedures are recorded in
[ADR-0171](0171-sql-dispatch-tables.md).

Shared owner/worker permission decisions after distinct credential admission are
recorded in [ADR-0172](0172-shared-permission-check.md).
Track B connector lifecycle consolidation is recorded in
[ADR-0173](0173-connector-framework.md); provider contracts and authority are unchanged.

Track B Python egress declaration consolidation is recorded in
[ADR-0174](0174-egress-declarations.md); closed profiles are replayed against the
pre-refactor validator without changing SQL authority or provider availability.

Fail-closed permission and invocation-owned lab isolation are recorded in
[ADR-0177](0177-fail-closed-permission-and-lab-isolation.md), superseding only
ADR-0172's nullable permission compatibility choice.
