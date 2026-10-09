# Control Plane Core

This package contains deterministic internal service boundaries. It is not a
deployed service bundle and does not load production credentials by itself.

| Module | Current responsibility |
| --- | --- |
| `authorization` | Resolve tenant/site authority from current server-side session state |
| `commands` | Accept idempotent snapshot intent and read exact or current-user latest authorized progress |
| `outbox_dispatch` | Page tenants and claim, acknowledge, or reschedule fenced outbox rows |
| `outbox_worker` | Compose bounded publication cycles without holding database work across I/O |
| `workflow_admission` | Deduplicate accepted events and persist stable workflow admission |
| `workflow_contracts` | Define deterministic serialization-safe workflow, heartbeat, result, and terminal contracts |
| `workflow_start` | Start one stable Temporal workflow and record first-run evidence |
| `workflow_consumer` | Compose admission, Temporal start, and durable recording before publication success |
| `workflow_consumer_runtime` | Validate transport and roles, then run the cooperatively stoppable consumer |
| `workflow_consumer_health` | Track release-bound lifecycle/readiness and serve closed loopback probes |
| `crawl_workflow` | Orchestrate bounded crawl execution and require terminal projection before closing |
| `crawl_workflow_activities` | Adapt an injected crawl executor and PostgreSQL terminal store to Temporal activities |
| `crawl_urls` | Normalize separate crawl URL identities and enforce exact-origin/public-address admission |
| `crawl_http` | Execute GET against one admitted numeric peer with manual redirect and bounded response handling |
| `crawl_robots` | Persist and apply expiring robots evidence under an exact run/profile |
| `crawl_admission` | Issue cross-tenant global-origin request permits with shared delay/backoff |
| `crawl_page` | Compose robots, permit, pinned HTTP, encrypted observation, and exact completion through a durable dispatch receipt |
| `crawl_frontier` | Bind immutable crawl-run snapshots and admit provenance-bearing URL leases through PostgreSQL |
| `crawl_artifacts` | Encrypt immutable crawl evidence and persist lease-bound observations, attestations, and orphan reconciliation |
| `audit_findings` | Detect one fixed local metadata fixture and persist/read its authenticated finding provenance |
| `crawl_audit` | Derive conservative technical findings from a completed crawl and seal one evidence-bound report with explicit coverage gaps |
| `page_observations` | Prepare, fetch, record, and read one authorized verified-homepage metadata observation |
| `proposals` | Seal fixture or exact verified-homepage evidence into an RFC 8785 revision and record an exact owner decision without external dispatch |
| `model_credentials`, `model_reasoning` | Read one exact OpenBao model secret and run the fixed no-tool Luna fixture or verified-page metadata drafting contract |
| `decision_contracts`, `jev_decisions`, `decision_records` | Validate bounded typed Jev questions, return only ceiling-reduced recommendations with a labelled fallback, and persist immutable decision evidence |
| `workflow_terminal` | Atomically record exact success, failure, or cancellation evidence |
| `oidc_protocol`, `oidc_login`, `login_flow` | Validate and compose the internal OIDC protocol flow |
| `session_issuance`, `session_management` | Issue, inspect, revoke, and version hash-only scoped sessions; project and select current authorized sites |
| `site_onboarding` | Validate and atomically create one owner-controlled unverified site under exact session authority |
| `origin_verification` | Issue, observe, and record owner-gated proof for one exact public origin |
| `gsc_properties` | Discover bounded read-only Search Console properties and match them to one verified origin |
| `github_app` | Mint one repository-scoped contents-read installation token and inspect exact repository/base identity without retaining credentials |
| `invitations`, `invitation_identity_proofs`, `invitation_acceptance` | Create and consume one-site invitation authority |
| `pkce_secrets`, `openbao_http`, `recovery_authority` | Keep login secrets and recovery generation behind narrow OpenBao clients |

The PostgreSQL helpers require idle autocommit connections and own each short
transaction. Callers must use the role intended by the function: identity,
bootstrap, API, scheduler publisher, workflow admission, crawler admission, or
crawl ingestion.
Roles are not interchangeable and none owns application tables or bypasses RLS.

`site_onboarding` accepts only canonical HTTPS DNS origin configuration and derives
tenant, actor, and owner role from the exact current session. Its narrow identity
function atomically creates the unverified site and owner site grant, advances the
active-site context, extends that context's hash chain, and records a separate
immutable idempotency receipt. A private tenant/site/origin route mirror supports
bounded tenant-wide collision and count checks without direct runtime table access.
This module proves no origin ownership and grants no crawl, connector, approval,
undo, or external-write authority.

`origin_verification` uses three narrow phases: prepare under current owner/session/
selected-site authority, fetch after the transaction closes through the existing
public-address-screened numeric-peer-pinned boundary, and record under freshly
rechecked authority. It accepts only the fixed well-known path, exact plaintext,
status 200, no redirect, and the configured canonical HTTPS origin. PostgreSQL
keeps immutable sanitized attempts, one-origin permitted authority, a 30-day
recheck, closed revocation reasons, and a private cross-tenant claim. A successful
proof grants no crawl, connector, repository, approval, undo, or provider-write
authority; production resolver composition and automatic stale-proof enforcement
remain unavailable.

`gsc_properties` calls only Google's fixed Search Console sites endpoint with an
in-memory bearer token, mandatory TLS verification, no redirects or environment
proxies, and a bounded JSON response. It distinguishes domain and URL-prefix
properties, preserves Google's exact resource identity and permission level, and
marks a property eligible only when it is readable and matches the configured
canonical HTTPS origin. It does not perform OAuth, persist tokens or bindings,
import analytics, or qualify a real authorized Google account.

`github_app` signs a short-lived App JWT from an injected transient private key,
mints a short-lived installation token restricted to one repository and
`contents: read`, and uses it only in memory to inspect canonical repository and
selected base-branch identity. It fixes the GitHub API origin/version, disables
redirect and environment-proxy behavior, enforces TLS and response bounds, and
rejects broadened permissions, repository mismatch, archived/disabled state,
invalid refs, hidden/traversing content paths, and malformed provider evidence.
It does not store the private key or token, persist a binding, clone content,
create a branch or pull request, merge, deploy, or qualify an authorized App
installation.

`outbox_worker` now provides synchronous and asynchronous composition cores. The
workflow consumer runtime supplies bounded Temporal transport, separate scheduler
and workflow connection factories, a closed JSON-lines observer, cooperative
process signals, and a release-matched health state. Readiness requires Temporal
startup and a recent scheduler cycle without storage failure or ambiguous publish;
it closes before drain. Production DSNs are file-only and opened without following
symlinks. The digest-pinned non-root image and bounded private Compose shape are
qualified locally, but no image is published, signed, deployed, monitored, or
approved for production. An ambiguous publish is deliberately left to lease expiry;
workflow admission deduplicates the stable event identity.

`workflow_start` applies fixed `CrawlSite` type, `signal.crawl.v1` task queue,
closed-workflow rejection, open-workflow reuse, and bounded start timeouts. It
records only a canonical Temporal first execution run ID through the function-only
workflow role. Timeout or malformed SDK evidence remains unknown and advances no
database projection.

`crawl_workflow` is the deterministic production workflow definition. It schedules
one injected, heartbeat-capable execution activity and one separately retryable
terminal-projection activity. Success returns bounded manifest metadata; failure
and cancellation use closed reasons. The workflow role commits event four and both
terminal projections atomically without direct table access. Real Temporal replay,
real PostgreSQL, and joint provider labs qualify this state machine with a synthetic
executor. A separate isolated-network lab qualifies exact URL/origin handling,
complete public-address screening, numeric-peer pinning, manual redirect checks,
and bounded authority-free HTTP responses. It requires an injected controlled
resolver and is intentionally not registered as the workflow executor.

The disposable local pilot registers that workflow with a deliberately separate
no-network executor and the real PostgreSQL terminal activity. This proves the
browser-to-outbox-to-Temporal state path only. It does not qualify or replace the
production crawler executor described above.

`audit_findings` applies one deterministic missing-meta-description detector to a
fixed bounded HTML fixture. After the current actor has a completed audit, its
narrow database operation stores immutable command/manifest-bound evidence,
creates or advances one guarded finding, and appends the supporting evidence
relation. Exact and concurrent retries converge. The authenticated read repeats
current selected-site authority and returns at most 50 records. This path does not
accept arbitrary HTML or URLs, read the configured origin, or establish customer
SEO evidence; customer observations require a dedicated analysis role and reviewed
crawler composition.

`page_observations` provides that first narrow customer read without claiming a
site crawl. It commits current actor/site/origin-proof/manifest intent before I/O,
uses the pinned public HTTP boundary for one verified homepage, extracts only
bounded title, first H1, and meta-description fields, and records the body digest
plus immutable facts under rechecked authority. Known failures commit no evidence;
a successful missing description produces one deterministic finding. The module
has no repository, provider-write, approval, or deployment authority.

`proposals` retains the fixed fixture as internal qualification and now makes the
verified-homepage finding the visible proposal source. The pilot records a Content
Strategy run before calling the fixed no-tool `gpt-5.6-luna` adapter with only the
exact URL, title, H1, absent description, and evidence identities. It combines the
validated output with deterministic technical, review, and coordination checks.
RFC 8785 canonical bytes and SHA-256 bind the immutable revision to its 24-hour
approval request. The database independently rebuilds the expected manifest from
current evidence and durable model facts, checks owner authority before commit,
and stores one immutable approve, reject, or request-edits decision. No decision
emits an outbox event or grants GitHub, provider-write, merge, deployment, or
production authority.

`jev_decisions` calls TypeSafe's fixed System One endpoint with text/JSON-only
Choice, Noul, and Score questions validated by `decision_contracts`. It rejects
unknown options, malformed or non-normalized probabilities, inconsistent Score
answers, out-of-range values, wrong response identities, excess usage, redirects,
oversized bodies, and untrusted transport outcomes. `DecisionService` only returns
a `DecisionRecommendation`; every primary or labelled fallback outcome is reduced
to the deterministic `reject < ask_owner < ship` ceiling before the function-only
workflow role records it through `decision_records`. The TypeSafe key comes from a
single-version read-only OpenBao capability. This boundary has no authorizer,
executor, connector, browser, merge, deployment, or write capability, and live
TypeSafe success remains unqualified.

`crawl_frontier` persists one exact running workflow's immutable scope and limit
snapshots, stable normalized URL identities, discovery provenance, and current
short leases plus immutable claim receipts through a dedicated function-only role.
Database locks serialize run opening and URL budgets; exact retries converge,
expired work is reassigned only within its attempt ceiling, and current
workflow/tenant/site reductions stop new discovery and leases. Returned records
are reconstructed and opaque in logs.

`crawl_artifacts` writes authenticated AES-256-GCM envelopes to a canonical private
local root, reads them back before atomically registering the artifact, initial
attestation, and fetch observation through a dedicated function-only PostgreSQL
role. It rejects mismatched lease/network/time evidence, blocks ordinary reads for
non-verified durability, records scheduled/restore attestations, and reconciles
only old parseable unregistered objects after a grace period. Key material remains
caller-supplied and absent from ordinary records and representations.

`crawl_robots` retrieves exact `/robots.txt` resources through the pinned HTTP
boundary, applies the versioned Protego 0.6.2 RFC profile, and persists immutable
run/origin/fetch-profile snapshots through the function-only ingest role. Only a
cached `404` allows otherwise-authorized crawling; authorization failures, rate
limits, server/transport failures, unsafe redirects, and invalid bodies fail closed.
Successful text is encrypted through `crawl_artifacts` and reparsed only from a
current, verified exact artifact. Composed page attempts enforce its recorded
crawl delay; robots retrieval remains a separate operation.

`crawl_admission` binds one short `robots` or `html_navigation` permit to the exact
live frontier lease and a global canonical-origin bucket. Profile V1 allows one
in-flight request with at least one second between starts, carries stricter caller
delay, shares provider/latency backoff, rejects active retry after current
authority reduction, and reconciles abandoned permits. Acquisition and completion
are separate transactions; no network call runs under a database lock.

`crawl_page` composes a current robots decision, HTML permit, pinned fetch, and
encrypted observation for one exact frontier lease. It commits an immutable
dispatch receipt before HTTP, permits only the inserting caller to fetch, recovers
an observation committed before finalization, and atomically closes the permit
with the terminal page-attempt record. An unresolved dispatch remains explicit and
cannot authorize a blind retry. The module is jointly qualified on synthetic
PostgreSQL/network infrastructure but is not a registered workflow executor or a
production crawler.

`full_site_crawl` composes one currently verified primary origin through robots
bootstrap, shared egress, encrypted fetch observations, bounded HTML parsing,
frontier settlement, byte accounting, and an immutable complete/partial manifest.
The optional disposable pilot registers this real executor only in its explicit
verified-crawl mode; the default walkthrough remains synthetic. Completed robots
dispatch is reusable, but an unresolved robots or page dispatch settles as
`dispatch_unknown` and is never blindly refetched. A complete manifest describes
the admitted, link-discovered bounded frontier, not all unlinked site URLs.

The package still has no distributed artifact service, production key/retention
lifecycle, sitemap discovery, production workflow-worker executable or build-ID
rollout, controlled production crawler egress, monitoring backend, or private-pilot
deployment. Production crawl and R1 release authority remain unavailable.

Run the repository verification commands documented in the root
[README](../../README.md#verification-and-release). Implementation claims and
known limits are tracked in the [status index](../../docs/implementation/status.md).
