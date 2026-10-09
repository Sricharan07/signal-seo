# Signal Business Database

This is the initial internal persistence layer, not a production installer or a
customer-facing API. Start with [slice 0003](../docs/implementation/0003-durable-commands.md),
[slice 0004](../docs/implementation/0004-session-derived-authorization.md),
[slice 0008](../docs/implementation/0008-durable-oidc-login-attempts.md),
[slice 0010](../docs/implementation/0010-scoped-session-issuance.md),
[slice 0016](../docs/implementation/0016-site-invitation-issuance.md),
[slice 0017](../docs/implementation/0017-invitation-acceptance.md),
[slice 0019](../docs/implementation/0019-tenant-selection.md),
[slice 0020](../docs/implementation/0020-session-lifecycle.md),
[slice 0021](../docs/implementation/0021-invitation-identity-proofs.md),
[slice 0022](../docs/implementation/0022-atomic-invitation-proof-acceptance.md),
[slice 0023](../docs/implementation/0023-invitation-browser-acceptance.md),
[slice 0024](../docs/implementation/0024-human-command-authority.md),
[slice 0026](../docs/implementation/0026-lease-safe-outbox-dispatch.md),
[slice 0027](../docs/implementation/0027-deduplicated-workflow-admission.md),
[slice 0029](../docs/implementation/0029-temporal-workflow-start.md),
[slice 0031](../docs/implementation/0031-workflow-command-consumer.md),
[slice 0032](../docs/implementation/0032-crawl-workflow-state.md),
[slice 0035](../docs/implementation/0035-durable-crawl-run-frontier.md),
[slice 0036](../docs/implementation/0036-encrypted-artifact-fetch-observations.md),
[slice 0037](../docs/implementation/0037-rfc-aware-robots-snapshots.md),
[slice 0038](../docs/implementation/0038-global-origin-admission.md),
[slice 0039](../docs/implementation/0039-durable-crawl-page-attempts.md),
[slice 0042](../docs/implementation/0042-authenticated-site-context.md), and
[slice 0044](../docs/implementation/0044-active-site-context.md),
[slice 0045](../docs/implementation/0045-site-onboarding.md),
[slice 0048](../docs/implementation/0048-origin-verification.md), and
[slice 0054](../docs/implementation/0054-durable-fixture-finding.md), and
[slice 0056](../docs/implementation/0056-supervised-local-proposal-flow.md), and
[slice 0057](../docs/implementation/0057-model-backed-fixture-proposal.md), and
[slice 0059](../docs/implementation/0059-verified-homepage-observation.md), and
[slice 0065](../docs/implementation/0065-shared-egress-proxy.md), and
[slice 0080](../docs/implementation/0080-isolated-candidate-build.md), and
[slice 0102](../docs/implementation/0102-authority-restriction-journal.md), and
[slice 0103](../docs/implementation/0103-recipe-release-registry.md)
for evidence.
The AUTONOMY track migration sequence is 0041 authority-restriction journal,
then 0042 recipe-release registry; these follow the GitHub PR migrations through
0040 without renumbering them. The candidate-build journal follows at 0043,
and technical-recipe evidence and sealed revisions follow at 0044.
Their ADRs define the deliberately narrow command, authorization, pre-tenant
login-state, and session-issuance boundaries.

## Data Ownership

| Table | Stores | Writer |
| --- | --- | --- |
| `app.tenants` | Organization identity and lifecycle | Bootstrap only |
| `app.sites` | Site identity, lifecycle, and explicit ownership-verification projection | Bootstrap or narrow owner onboarding/verification functions |
| `app.commands` | Immutable service or human command intent, exact actor, idempotency fingerprint, and bounded current result reference | Scoped API role or narrow identity/workflow functions |
| `app.command_events` | Append-only acceptance and workflow progress history | Scoped API role or narrow identity/workflow functions |
| `app.outbox` | Immutable acceptance envelope plus guarded delivery lease and acknowledgement bookkeeping | Scoped API insertion, narrow identity acceptance, and scheduler functions |
| `app.consumer_inbox` | Stable accepted-event deduplication receipt for workflow admission | Narrow workflow admission function only |
| `app.workflow_refs` | Rebuildable workflow identity, first-run reference, and monotonic state projection | Narrow workflow admission/start/terminal functions only |
| `app.crawl_runs` | Exact workflow-bound immutable scope and limits snapshot for a running crawl | Narrow crawler-admission open function only |
| `app.urls` | Stable original/fetch/origin identity for one site and normalization cohort | Narrow crawler-admission open/enqueue functions only |
| `app.crawl_frontier` | Same-run discovery provenance, queue state, attempts, and current short lease | Narrow crawler-admission enqueue/claim functions only |
| `app.crawl_frontier_leases` | Immutable issued lease identities, workers, attempts, and expiry receipts | Narrow crawler-admission claim function only |
| `app.artifacts` | Immutable private-object identity, hash, size, media/key reference, retention metadata, and attested durability projection | Narrow crawl-ingest commit/attestation functions only |
| `app.artifact_attestations` | Append-only upload, integrity, and restore-verification evidence | Narrow crawl-ingest commit/attestation functions only |
| `app.fetch_observations` | Immutable exact-lease response metadata and optional raw-artifact reference | Narrow crawl-ingest commit function only |
| `app.crawl_robots_dispatches` | Durable robots pre-dispatch and terminal receipt | Narrow crawl-admission functions only |
| `app.crawl_page_records` | Bounded parsed metadata tied to immutable fetch observations | Narrow crawl-ingest record function only |
| `app.crawl_frontier_settlements` | One terminal URL classification, evidence binding, and byte count | Narrow crawl-ingest settle function only |
| `app.crawl_manifests` | Immutable complete/partial bounded-frontier coverage | Narrow crawl-ingest finalize function only |
| `app.crawl_audit_reports` | Immutable detector-release findings and explicit evidence coverage for one completed manifest | Narrow crawl-ingest report function only |
| `app.github_read_bindings` | Owner-selected exact installation/repository/base, provider observation and revocable state; no key or installation token | Narrow identity binding functions only |
| `app.github_read_binding_events` | Immutable prepared, activated, failed and revoked receipts | Narrow identity binding functions only |
| `app.github_pr_extensions` | Owner-attributed, revocable one-binding PR permission and bounded format evidence; no token or write receipt | Narrow identity extension functions only |
| `app.github_pr_extension_events` | Immutable preparation, observation, failure, and revocation receipts | Narrow identity extension functions only |
| `app.candidate_build_intents` | Owner/site-bound exact base, patch, toolchain, and one-way dispatch state without credentials or source content | Narrow identity candidate functions only |
| `app.candidate_build_receipts` | Immutable bounded exit, log digest, and artifact manifest for one dispatched build | Narrow identity candidate functions only |
| `app.crawl_page_image_evidence` | Immutable missing-alt references tied to an observed page body digest | Narrow crawl-ingest function only |
| `app.candidate_recipe_revisions` | Immutable finding, release, build, patch, and canonical review manifest | Narrow identity sealing function only |
| `app.indexnow_key_intents`, `app.indexnow_keys`, `app.indexnow_key_revisions`, `app.indexnow_key_retirements` | Immutable site/key generation identities, digests, reviewed revision links and retirements; authoritative key in OpenBao, deliberate public-file patch in Inbox | Narrow owner identity functions only |
| `app.indexnow_outbox` | One committed verified change/URL with bounded dispatch lease, retry and journal acknowledgement state | Verified-delivery receipt trigger and narrow owner identity functions only |
| `app.indexnow_receipts` | Immutable exact URL, status, time, skip or unknown outcome and egress/journal provenance | Narrow owner identity completion/expiry functions only |
| `app.crawl_page_attempts` | Durable pre-dispatch robots/permit binding and one-way terminal observation/failure receipt | Narrow crawl-ingest page-attempt functions only |
| `app.egress_operations` | Immutable pre-dispatch request digest, robots and global-permit binding, and one-way sanitized terminal network evidence | Narrow crawl-admission shared-egress functions only |
| `app.page_observation_intents` | Immutable pre-network binding to current actor, site, origin proof, command, manifest, and idempotency | Narrow authenticated homepage-prepare function only |
| `app.page_observation_results` | Immutable bounded homepage outcome, metadata facts, body digest, and timing | Narrow authenticated homepage-record function only |
| `app.evidence_records` | Immutable exact audit-manifest provenance, bounded source identity, quality/rights facts, extracted facts, and content digest | Narrow authenticated fixture/homepage analysis functions only |
| `app.findings` | Guarded current detector result with first/latest command provenance and monotonic observation window | Narrow authenticated fixture/homepage analysis functions only |
| `app.finding_evidence` | Append-only support relation from a current finding to immutable evidence history | Narrow authenticated fixture/homepage analysis functions only |
| `app.proposals` | Immutable proposal identity bound to one current source finding and creating owner | Narrow authenticated proposal functions only |
| `app.proposal_revisions` | RFC 8785 canonical manifest, SHA-256 revision identity, evidence reference, and version | Narrow authenticated proposal functions only |
| `app.approval_requests` | Exact revision authority request, class, channel, and fixed expiry | Narrow authenticated proposal functions only |
| `app.approval_decisions` | One immutable owner decision with exact revision, authentication, authority epochs, and recovery generation | Narrow authenticated proposal-decision function only |
| `app.agent_runs` | Durable bounded role release, source evidence, prompt/input identity, attempt, and terminal outcome | Narrow authenticated model functions only |
| `app.model_calls` | Exact requested/reported model, provider receipt, validated output, token usage, hashes, and closed failure state | Narrow authenticated model functions only |
| `app.decision_records` | Immutable Jev or labelled-fallback recommendation, input/question digests, validated answers, probabilities, confidence, deterministic ceiling, and outcome | Narrow workflow decision-record function only |
| `control.tenant_directory` | Minimal organization scheduling directory | Bootstrap only |
| `control.users` | Global OIDC issuer/subject identity | Identity role through the narrow invitation-acceptance function only |
| `control.identity_sessions` | Hash-only global session status and recovery generation | Identity role; hash-scoped column-limited issuance |
| `control.oidc_login_attempts` | Hash-only, short-lived OIDC state/nonce/browser binding, immutable purpose, and PKCE secret reference | Identity role; one-time consumption only |
| `control.invitation_identity_proofs` | Hash-only short-lived invitee proof and bounded verified identity projection | Identity role issues and invokes proof-backed acceptance; scheduler invokes expired-row cleanup |
| `control.platform_events` | Strict append-only identity, restriction receipt, and recipe-revocation evidence | Identity role or narrow platform functions; proof-scoped inserts only |
| `control.recipe_signing_keys` | Immutable platform recipe verification keys | Dedicated release-manager function |
| `control.recipe_releases` | Immutable canonical signed recipe manifests and content hashes | Dedicated release-manager function |
| `control.recipe_release_events` | Append-only draft, review, and revocation history | Dedicated release-manager transition or authority replay function |
| `control.authority_restriction_outbox` | Immutable stable platform restriction intents awaiting independent durability | Revocation event trigger; read by dedicated authority dispatcher function |
| `control.authority_denial_tombstones` | Typed deny-only replay for present or absent restored session and recipe-release targets | Dedicated authority replay functions |
| `control.authority_replay_checkpoints` | Append-only verified journal head and fresh recovery-generation evidence | Dedicated authority replay function |
| `control.invitation_routes` | Recipient-free invitation UUID to tenant/site scope routing | Security-definer trigger and identity acceptance function only |
| `control.user_membership_routes` | Minimal user/membership UUID routing for pre-tenant discovery | Security-definer trigger and identity listing function only |
| `control.user_site_membership_routes` | Minimal user/tenant/site/membership UUID routing for hash-bound site discovery | Security-definer trigger and identity directory function only |
| `control.tenant_site_routes` | Minimal tenant/site/origin/lifecycle routing for bounded onboarding count and origin collision checks | Security-definer site trigger only; read by narrow onboarding function |
| `control.origin_buckets` | Global canonical-origin token, concurrency, readiness, and provider-degradation state without tenant ownership/content | Narrow crawler-admission functions only |
| `control.admission_leases` | Short request-permit identity, opaque authority fingerprint, expiry, and closed completion without tenant/site columns | Narrow crawler-admission functions only |
| `app.memberships` | Current organization role and authority epoch | Bootstrap or narrow invitation-acceptance function |
| `app.sessions` | Hash-only tenant-selected application session, monotonic context version, and nullable active site | Identity role through narrow issuance/selection functions |
| `app.site_memberships` | Exact site permission and authority epoch | Bootstrap or narrow invitation-acceptance/owner-onboarding functions |
| `app.session_site_context_events` | Immutable hash-chained active-site transitions with bounded authority facts | Narrow identity selection function only |
| `app.site_onboarding_events` | Explicitly tenant-scoped immutable owner request identity, created-site reference, configuration, authority facts, and receipt hash | Narrow owner-onboarding function only |
| `app.site_origin_challenges` | Immutable owner/session-bound proof digest, exact origin/path, issue/expiry, and idempotency receipt | Narrow origin-challenge function only |
| `app.site_origin_verification_attempts` | Immutable sanitized exact-HTTP proof outcomes and authority epochs | Narrow origin-verification record function only |
| `app.site_origin_verifications` | Successful proof method, resource, one permitted origin, revocation conditions, and recheck time | Narrow origin-verification record function only |
| `control.public_origin_claims` | One protected global canonical-origin claim without customer content | Narrow origin-verification record function only |
| `app.invitations` | Hash-only one-site recipient condition, role, inviter authority, and expiry | API role; authority-locked column-limited issuance |
| `app.audit_events` | Strict append-only tenant invitation creation/acceptance evidence | Narrow issuance and acceptance functions |
| `control.alembic_version` | Applied schema revision | Migrator only |

All 52 `app` tables force RLS. Site references include tenant/site identity,
and outbox event references include the exact command. The scheduler can read the
minimal directory but has no direct outbox table access; three narrow functions
claim, acknowledge, or reschedule one tenant's delivery work under forced RLS.

No customer analytics, conversations, embeddings, raw secret values, artifact
bodies, Temporal history, Keycloak internal state, provider approvals, or
external-write authority is stored here. One verified-homepage read stores bounded
metadata facts and a body digest, not the HTML body. A verified-page model proposal
stores the exact draft, rationale, provider receipt, usage, and cryptographic
identities needed for review; it does not store the model credential or grant an
external operation. Decision records contain canonical digests, validated answers,
and provider/model provenance but no model credential and no authorization. The
full product adds repository content and remaining provider state through
separately owned services and later migrations.

## Fixture Finding Flow

`control.record_authenticated_local_fixture_finding` derives tenant and actor from
the opaque tenant session, requires the selected exact site and current recovery
generation, and locks the current actor's latest completed `api.site.snapshot`
command. It stores one immutable evidence record for the fixed local source,
including exact manifest UUID/hash provenance, SHA-256 fixture content, bounded
quality/rights facts, and `customer_origin_read=false`.

The same transaction creates or advances one current missing-meta-description
finding and appends its supporting evidence link. An exact retry for the same
audit/source reuses the original evidence. Concurrent retries converge through
named unique constraints. A later completed audit appends evidence and advances
only the finding's latest command and last-seen time; historical evidence is not
rewritten or deleted.

`control.read_authenticated_findings` repeats live session, recovery, membership,
selected-site, and actor authorization, then returns at most 50 current findings
with their latest evidence. The identity role can execute these two functions but
has no direct privileges on the three tables. This role reuse is limited to the
explicit local fixture path; customer evidence requires a separately reviewed
analysis role and provider composition.

## Verified Homepage Observation Flow

`control.prepare_authenticated_page_observation` requires the current selected
site, exact owner verification and global claim, recovery generation, and latest
completed audit. It commits one immutable idempotent intent before network I/O.

`control.record_authenticated_page_observation` rechecks those authorities before
committing the bounded outcome. Known failure outcomes create no evidence or
finding. Success appends a `verified_origin` evidence record with extracted facts
and body SHA-256; a missing description creates or advances the deterministic
finding and evidence link. `control.read_latest_authenticated_page_observation`
returns only the latest successful authorized result. The identity role can call
these functions but has no direct table access.

## Fixture Proposal And Approval Flow

`control.prepare_authenticated_local_fixture_proposal` requires a current owner,
selected exact site, recovery generation, and open fixture finding. The caller
constructs one closed proposal manifest and RFC 8785 canonical byte string; the
database independently reconstructs the expected manifest from the current
finding and newest supporting evidence, verifies the bytes parse to that manifest,
and verifies SHA-256 before commit.

Exact or concurrent preparation converges on one proposal, evidence-bound
revision, and 24-hour approval request. `control.read_authenticated_local_fixture_proposals`
returns at most 50 current-site projections after fresh authorization.
`control.decide_authenticated_local_fixture_approval` accepts only one current
owner decision bound to the exact request and digest. A retry with the same
decision identity is stable; stale digests, expired requests, and conflicting
decisions fail closed. No function writes to the outbox. The identity role has
execute authority on these three functions and no direct privilege on the four
tables.

The model-backed variant first calls
`control.begin_authenticated_fixture_model_run`, which commits one exact run/call
intent before provider I/O and permits at most three attempts after known failures.
Requested or unknown outcomes block blind repetition. After strict adapter
validation, `control.complete_authenticated_fixture_model_proposal` rechecks live
authority and current evidence, records the terminal model call, and reconstructs
the exact model manifest before sealing its proposal revision and approval request
in the same transaction. `control.fail_authenticated_fixture_model_run` records
only a sanitized known or unknown terminal state. The identity role has function-
only access and no direct privileges on either model table.

The verified-homepage variant uses
`control.begin_authenticated_verified_homepage_model_run`,
`control.complete_authenticated_verified_homepage_model_proposal`, and
`control.fail_authenticated_verified_homepage_model_run`. Admission requires the
latest successful observation and its current open `verified_origin` finding,
binds the canonical title/H1/URL packet and verified prompt release before provider
I/O, and preserves the same known-failure retry and unknown-outcome rules.
Completion rechecks live owner authority and exact current evidence, then rebuilds
the verified model manifest before sealing the revision and request. The existing
read and decision functions intentionally project and decide all supported local
proposal kinds; neither emits outbox work.

## Site Onboarding Flow

`control.onboard_site` hashes and resolves the opaque tenant session, locks that
session plus the current owner membership and tenant lifecycle, then uses the
private route mirror to reject duplicate non-archived origins and enforce the
100-site active/onboarding bound. Tenant and user identity never come from the
browser request.

One transaction creates an `onboarding`/`unverified` site and active owner site
membership, selects the site by advancing `app.sessions.session_version`, appends
the next `app.session_site_context_events` hash-chain entry, and inserts one
immutable `app.site_onboarding_events` receipt. Exact idempotency replay returns the
original site and session version without new rows; stale context or changed input
conflicts. Generated UUID collisions roll back the complete transaction before a
bounded retry.

The onboarding receipt declares `scope_kind = tenant`: idempotency, origin
collision, and count authority apply to site creation for the organization, while
`site_id` is the exact created resource reference. The separate active-context
event remains site-scoped through both tenant and current-site RLS.

`control.tenant_site_routes` exists because exact-site forced RLS intentionally
prevents tenant-wide count and origin queries over `app.sites`. The trigger-owned
mirror contains no user, membership, secret, or evidence body and has no runtime
table grants. Historical site values are copied as-is during migration; current
onboarding accepts only one canonical HTTPS DNS origin. This is not ownership
proof, reachability evidence, crawl admission, or provider authority.

## Origin Verification Flow

`control.issue_site_origin_challenge` derives tenant and actor from the opaque
tenant session, requires the selected exact site and current owner authority, and
inserts one immutable 30-minute proof digest. The challenge UUID reconstructs the
public plaintext body; no separate raw proof body column is stored. Exact retries
return the original challenge and no more than ten unexpired challenges may exist
for one site.

`control.prepare_site_origin_verification` rechecks session, owner, selected site,
origin, expiry, replay, and the ten-attempt ceiling before network I/O. The caller
closes that transaction, performs the bounded pinned fetch, then invokes
`control.record_site_origin_verification`. Recording rechecks authority and commits
every sanitized terminal observation. Exact success atomically appends the
verification, reserves the canonical origin in `control.public_origin_claims`, and
promotes the site to `active`/`verified`; another tenant/site receives only a closed
claim conflict.

The verification records bind the exact origin as both resource identity and sole
permitted origin. They record a 30-day recheck plus origin/ownership/claim/expiry
revocation conditions. No scheduler currently enforces stale transitions or claim
release, and no origin mutation operation exists. Runtime roles cannot directly
read or write any of these four tables. Ownership proof does not grant crawl,
connector, repository, approval, undo, or external-write authority.

## Command Flow

`accept_snapshot` receives an internal scope and service identity, rejects invalid
keys or unavailable scope, and hashes a versioned intent. The separate
`accept_authenticated_snapshot` boundary hashes an opaque tenant session and asks
one narrow database function to derive the tenant/user actor, recheck current
recovery and site authority, compute the required fingerprint, and persist intent.
Neither path trusts a browser-supplied tenant or actor.

One transaction inserts the command, event, and outbox row. Any failure rolls back
all three. Concurrent duplicates converge on one command; a conflicting scope
receives no other site's details. Reconnection after a lost acknowledgement uses
the same idempotency key. The identity role can execute only the narrow human
acceptance and own-command read functions; it has no direct command-table writes.

The browser command route now exists as an unconfigured internal contract. An
executable stoppable consumer composes outbox claim, deterministic workflow
admission, Temporal start, first-run-backed `processing` projection, and delivery
acknowledgement. A deterministic `CrawlSite` state machine can then commit success,
failure, or cancellation through the workflow role before closing in Temporal. The
consumer has locally qualified image, health, and bounded Compose contracts, but no
published/signed deployment or production worker. A separate URL and
address-pinned HTTP fetch boundary is isolated-network-qualified. A separate
PostgreSQL boundary now persists immutable crawl runs, stable URL identity,
provenance, and short frontier leases. A separate encrypted local artifact and
append-only fetch-observation boundary retains exact lease evidence, integrity
attestations, and orphan-reconciliation state. None is registered as the workflow
executor, distributed artifact service, or production crawler. This does not
constitute a long-running employee or an observed site snapshot.

The future authenticated backend must verify session/membership before creating
scope. The `Scope` dataclass and PostgreSQL session variables are not authorization.
Only idle autocommit connections with no residual session scope may enter the
transaction helper; scope resets after commit or rollback. Never expose SQL or
these internal methods directly as model/chat tools.

## Outbox Delivery Flow

`list_active_dispatch_tenants` pages only the minimal active tenant directory.
For each selected tenant, `claim_outbox_batch` invokes a narrow database function
that also locks and rechecks the business tenant lifecycle, applies transaction-
local tenant scope, and claims at most 100 available rows with `FOR UPDATE SKIP
LOCKED`. The claim transaction commits before an envelope is returned for network
publication.

Each claim carries a short database-clock lease and incrementing attempt fence.
Only that exact worker/fence pair can mark delivery or reschedule the event while
the lease remains live. An expired lease is eligible for redelivery under the same
outbox, event, and command IDs; a stale worker cannot acknowledge a replacement
claim. Suspension blocks new claims but does not block recording an outcome for a
publish already in flight.

The scheduler can execute only the three dispatch functions and cannot directly
read or mutate `app.outbox`. The API role can insert only immutable envelope
columns and cannot forge lease, attempt, or delivery values. A trigger allows only
reviewed bookkeeping transitions and rejects payload changes and deletion.
Delivery acknowledgement follows durable workflow admission and start recording;
it is still not workflow completion evidence. The consumer inbox and workflow-start
projection preserve their own receipts. Migration 0018 keeps duplicate admission's
original `admitted` receipt stable after later progress. Migration 0019 likewise
keeps event three's original `running` start receipt stable after terminal progress,
allowing acknowledgement-loss redelivery to converge even when the workflow
finishes quickly.

The terminal function accepts only the exact running workflow and canonical first
run. It atomically writes command and workflow `succeeded`, `failed`, or `cancelled`
state plus event four. Success carries a bounded crawl-manifest reference; failure
and cancellation carry one closed reason and no result. Exact retries return the
original event, conflicting evidence is rejected, and running work may finish after
scope suspension. Published supervision, dead-letter policy, complete
crawler/artifact execution, and production operational replay remain future work.

## Crawl Admission Flow

`open_crawl_run` binds the exact running `CrawlSite` command, deterministic
workflow ID, and canonical first execution run ID to one immutable scope/limits
snapshot and root frontier item. It serializes concurrent opens on the workflow
and command rows, returns exact retries, and rejects a different configuration for
the same command. The snapshot records public-HTML purpose, allowed origins, seed,
user agent, normalization version, and URL/depth/attempt/redirect/body/byte/time
ceilings. It is not a completion manifest.

`enqueue_crawl_url` serializes on the run, deduplicates normalized URL identity,
and requires an exact same-run source, allowed origin, valid depth transition,
remaining URL count, open duration, running workflow, and current tenant/site
authority. `claim_crawl_frontier` returns at most one database-clock lease under
the immutable attempt and duration ceilings; lease expiry is capped by the run
deadline. Exact live lease retries return the same evidence. An append-only
lease-receipt primary key and unique per-item attempt prevent an expired, delayed,
or concurrent claim identity from acquiring different work; expiration allows a
fresh identity to reassign the item until it becomes permanently failed.
Current authority reductions block new leases while leaving an already accepted
live retry identifiable.

Global origin admission adds a second, shorter permit around each future robots or
HTML request. One canonical origin/profile bucket is shared across every tenant.
Profile V1 serializes one in-flight request, spaces starts by at least one second,
accepts a stricter delay up to 60 seconds, and shares bounded `429`, `503`,
transport, and slow-response backoff. Acquisition rechecks the exact frontier,
workflow, directory, tenant, and site; exact active retries recheck that authority
again. Completion and bounded expiry reconciliation remain available after an
authority reduction so accepted or ambiguous work can release capacity.

`begin_crawl_page_attempt` binds the exact current frontier lease, URL, worker,
robots snapshot/allow reason, and active HTML permit before network dispatch. The
attempt and permit use the same UUID. Only the caller that creates the `dispatched`
row may fetch. `finish_crawl_page_attempt` validates an exact observation or closed
failure, completes the permit, and advances the attempt in one transaction. Exact
historical lookup permits response-persistence recovery after authority reduction;
absence of an observation leaves dispatch explicitly unknown and cannot authorize
another request.

The `signal_crawl_admission` role can execute only the frontier and global-origin
functions. It has
no direct table read or write, no migration privilege, and no RLS bypass.
The `signal_crawl_ingest` role owns the narrow page-attempt and historical
observation functions without direct table access. The composed attempt remains
disconnected from byte/frontier settlement, coverage finalization, Temporal, and
production crawl authority.

## Crawl Artifact And Observation Flow

`commit_fetch_observation` binds one immutable result to the exact run, frontier,
URL, lease receipt, and worker. It independently validates the canonical final URL
and redirect chain against the run snapshot, a screened public numeric address,
closed response headers, time/byte ceilings, and the network-profile hash. Fetched
HTML/XHTML atomically creates the artifact reference, initial upload-readback
attestation, and observation. Body-free terminal HTTP outcomes create only the
observation. Exact retries return the original evidence; conflicting evidence is
not substituted.

`record_artifact_attestation` appends scheduled-integrity or restore-verification
evidence and changes the artifact durability projection in the same transaction.
The artifact guard accepts a durability transition only when the exact transaction-
local attestation already exists. Missing, corrupt, and unreadable states block
ordinary object use until a successful restore verification returns the projection
to `verified`.

The `signal_crawl_ingest` role can execute only the exact commit, lookup, and
attestation functions. It has no direct table read/write, migration, ownership, or
RLS-bypass privilege. PostgreSQL stores references, hashes, metadata, and state;
the AES-GCM ciphertext lives in the private artifact backend and key material stays
outside both. The local backend, key/retention lifecycle, integrity/orphan schedules,
frontier settlement, and workflow composition are not production-deployed.

## Authorization Flow

`authorize_snapshot` accepts an opaque 256-bit tenant-session token, a requested
site UUID, and the externally obtained current recovery generation. The login
coordinator now obtains that generation from an exact read-only OpenBao boundary;
future authorization adapters must use the same trusted source rather than this
database. It hashes the token and invokes the same narrow authority function as
human command acceptance. PostgreSQL derives tenant/user identity from the
matching server-side session and rechecks both session layers, enabled identity,
current membership, exact site grant, tenant lifecycle, and site state. It never
accepts a claimed tenant ID.

Only token hashes are stored. Email remains profile data rather than identity; OIDC
issuer plus subject is the unique identity key. The current recovery generation is
an external trust input and cannot be self-declared from restored business data.
The `signal_identity` role can read identity and authority state and insert only
the reviewed columns of hash-scoped session records. Account-authority creation is
available solely through the narrow invitation-acceptance function; the role has
no corresponding direct table writes. It cannot update, delete, truncate, or
migrate sessions. There is no public authorization endpoint. The browser
cookie/CSRF primitive is implemented separately but has no customer mutation route.

## OIDC Login-Attempt Flow

`create_oidc_login_attempt` stores hashes of random state, nonce, and a separate
browser binding together with an exact registration, expiry, local return path,
immutable `login` or `invitation_acceptance` purpose, and `secret://`
PKCE-verifier reference. Both state and browser binding are required by forced
RLS. `read_oidc_login_purpose` reads one live purpose without consumption so the
coordinator can preserve ordinary-login recovery ordering. The subsequent
`consume_oidc_login_attempt` atomically consumes one live row and returns the same
immutable purpose; all invalid cases are indistinguishable.

This persistence survives process restarts but does not contact Keycloak or a
secret manager. Raw values, authorization codes, provider tokens, and the PKCE
verifier are not stored in this database. The separately qualified OpenBao client
stores the verifier and returns only the `secret://` reference persisted here.
The internal login coordinator now binds that reference to the same UUIDv4 as the
attempt and consumes the row before any callback exchange. It does not add tables
or grant broader database privileges.

After consumption, a failed callback can append one `identity.login.failed`
event for that attempt. The event has no actor, schema-only facts, and one closed
reason; original state and browser-binding hashes are required by forced RLS.
Malformed proofs, replay, and recovery-authority failures before consumption do
not append rows. No callback values, provider payloads, secret references,
exception text, or guessed identity are copied into the event.

## Session Issuance Flow

`issue_identity_session` maps the strict OIDC protocol's verified projection to an
already provisioned, enabled user by exact issuer and subject. A fixed ACR policy,
provider times, maximum authentication age, and externally validated recovery
generation are checked before a 256-bit opaque global token is generated. Only its
SHA-256 hash is inserted. The session and its exact `identity.session.issued`
platform event commit atomically; event failure rolls both back before token return.

`issue_tenant_session` hashes that global token and rechecks the live parent,
enabled user, recovery generation, active membership, active tenant, and expiry.
It creates another hash-only credential for exactly the requested tenant and
cannot outlive its parent. It creates no authority, and site access still requires
`authorize_snapshot`. Both functions require an idle autocommit identity
connection with clean transaction-local context.

## Membership Discovery And Tenant Selection

`list_identity_memberships` hashes the pre-tenant identity token and requires the
current recovery generation from the independent OpenBao reader. The identity
role can execute one narrow function but cannot read the protected routing table.
That function validates the exact live identity session, visits only route rows
for its user, and applies transaction-local tenant RLS before returning current
active tenant names and roles. A private sentinel distinguishes a valid identity
with no organizations from an invalid credential and is removed by the service.

`control.user_membership_routes` contains only user, tenant, and membership UUIDs
plus creation time. It is not an authorization cache: names, roles, lifecycle, and
membership state remain in tenant-owned tables and are checked on each request.
The trigger keeps routes synchronized, while foreign keys bind them to current
users and exact memberships. Tenant-session issuance independently repeats the
current authority checks so a concurrent suspension or removal fails closed.

## Session Inspection And Logout

`inspect_tenant_session` begins with the SHA-256 hash of one opaque tenant token
and the current OpenBao recovery generation. It rechecks the child and parent
sessions, enabled user, active tenant, and current membership before returning a
bounded tenant/user/role/authentication/expiry projection. It does not derive a
site scope or change durable state.

`list_tenant_sites` starts from the same exact token hash and external recovery
generation. A private UUID-only route index identifies candidate site memberships;
the function then applies exact tenant and site scope for each candidate and
rechecks current membership, permissions, lifecycle, and the ordinary forced-RLS
rows. Runtime roles cannot read the route table. Valid sessions with no current
sites return an explicit empty result, invalid authority remains indistinguishable,
and more than 100 current sites fails closed rather than truncating the directory.
The result is a transient read projection, not an authorization cache.

`select_session_site` starts from the exact tenant-session hash and current
external recovery generation, locks the session, and requires the caller's
expected `session_version`. It rechecks current identity, user, tenant membership,
site membership, permission, and lifecycle before updating `active_site_id` and
incrementing the version. The same transaction appends one immutable event whose
SHA-256 hash includes the preceding event hash for that session. Exact reselection
is idempotent; stale/concurrent selection conflicts; event-ID collision retries
only after the complete transaction rolls back. The runtime role has no direct
session-update or event-table access.

`inspect_tenant_session` returns the durable version but projects the selected site
only while its ordinary site authority remains current. The stored site UUID is
therefore navigation state, never proof of authorization. Snapshot command
authority additionally requires its requested site to equal this server-selected
site and then repeats all live checks.

`revoke_browser_session` does not require recovery-authority availability. The
identity role can execute one narrow security-definer function but still has no
direct session update privilege. An exact identity or tenant token hash resolves
and locks its parent identity session. The first request sets parent revocation,
marks the presented tenant child when applicable, and appends one strict
`identity.session.revoked` event atomically. Parent revocation invalidates sibling
tenant sessions without a cross-tenant rewrite. Repeated or unknown proofs return
an idempotent result and append no event.

## Invitation Issuance Flow

`issue_site_invitation` accepts a server-derived site principal and then rechecks
its exact current actor, role, authority epochs, tenant, and site under the API
database role. A narrow RLS-scoped function locks those authority rows before the
insert. Owners can invite through admin; admins can invite through approver; no
invitation creates an owner.

The bearer invitation token is returned once and only its SHA-256 hash is stored.
The API role cannot read that hash. Recipient collisions are transactionally
serialized, lifetimes are bounded, and invitation plus `invitation.created`
tenant audit evidence commit atomically. Revocation, delivery, public routes, and
production authority remain absent.

## Invitation Acceptance Flow

`accept_site_invitation` requires the short-lived opaque identity proof, random
invitation UUID, and opaque invitation bearer. The service validates the public
input shapes and hashes both credentials before calling PostgreSQL; neither raw
value reaches the database.

The protected `control.invitation_routes` table resolves only invitation UUID to
tenant/site scope. It contains no email, role, token, or token hash and has no
runtime table grants. The proof-backed function first locks one exact live proof,
obtains its issuer/subject/verified-email projection, and then applies transaction-
local RLS to lock active tenant, site, and invitation rows. It serializes the exact
provider identity and rechecks expiry, token hash, recipient email, consumption,
user state, and existing membership.

One transaction creates or reuses the exact enabled provider identity, creates an
active tenant membership and one-site permission, consumes both proof and
invitation, and appends sequence-two `invitation.accepted` evidence linked to the creation hash.
Email never merges identities, existing profile data is not silently rewritten,
and ordinary login still creates no authority. The identity runtime role can no
longer call the older raw issuer/subject/email acceptance function directly.

## Invitation Identity Proof Flow

`issue_invitation_identity_proof` converts one fresh `VerifiedOidcIdentity` into
a dedicated ten-minute maximum credential for a future invitation browser flow.
It accepts only an exactly normalized provider-verified email and bounded signed
identity times. The proof expires no later than the provider assertion.

Only the SHA-256 hash of the random 256-bit token is stored. The protected row
contains the exact issuer, subject, verified email, provider issuance/expiry,
proof expiry, and an internal UUID. It contains no provider token, PKCE verifier,
invitation token or scope, tenant/site ID, role, or granted authority. Forced RLS
allows the identity role to read only the row selected by the exact transaction-
local proof hash; it has only reviewed-column insert and no update/delete access.

Migration `0012` permits only one exact proof-consumption transition inside the
same transaction as invitation acceptance. Failed acceptance leaves it live for a
corrected retry until expiry. A scheduler-only function deletes at most 1,000
expired rows per call without granting direct table access. There is still no
browser route, proof cookie, deployed cleanup schedule, or retention alert.

## Local Verification

Use Node 22, Python 3.12, Docker, and at least 3 GiB free space:

```sh
npm ci
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm test
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -v
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/ruff check apps/api services/control_plane database/migrations scripts tests/api tests/consumer tests/control_plane tests/identity tests/temporal tests/tooling
.venv/bin/ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/consumer tests/control_plane tests/identity tests/temporal tests/tooling
.venv/bin/pip check
```

Run from the repository root. The runner rejects insufficient space and an
unresponsive Docker daemon before creating resources. It never accepts an
external database URL. An invocation receives a private bridge network, a pinned
PostgreSQL image, a loopback-only ephemeral port, random passwords, and tmpfs
storage. No customer data, mounted home directories, or application credentials
are used. Docker administrators can inspect lab credentials; this is not a
production secrets-management mechanism.

Cleanup uses both an invocation label and exact resource name. It is attempted on
normal exit, failure, and catchable interruption, even when creation lost its
acknowledgement. Cleanup failure is nonzero and reports the invocation name. A
SIGKILL or unavailable daemon can still leave resources; inspect that printed
run name only. Never use a global Docker prune to clean up a test.

Results are written under `.runtime/database-tests/`: JUnit XML and a sanitized
JSON summary with versions, exact source hashes, and cleanup status. Generated
files are ignored; reviewed evidence is stored under `docs/evidence/`. A source
change during testing invalidates the run. A skipped test does not count as a pass.

## Migrations

### Revision 0001

- Owner: control-plane persistence.
- Forward plan: explicit one-time `bootstrap.sql` on an empty dedicated business
  database, then Alembic `upgrade head` under `signal_migrator` on the primary.
- Bootstrap creates NOLOGIN roles without passwords. Only the disposable runner
  enables temporary test logins. Do not reuse its credentials or provisioning for production.
- Lock risk: creation-only DDL, serialized migration advisory lock, 3-second lock
  timeout and 30-second statement timeout. No live-data backfill is needed.
- Compatibility: no deployed application. Future changes must expand these narrow
  contracts before supporting new actors, dispatch, or mutable progress.
- Recovery: transactional rollback on migration failure. Automatic destructive
  downgrade is disabled. Dropping a live database is not per-change Undo.
- Verification: repeated upgrade, injected mid-migration failure, persisted version,
  downgrade refusal, non-owner RLS, effective grants, and command integrity tests.

The [privilege manifest](privileges.json) is reviewed with each migration. No
runtime principal receives blanket grants, schema creation, or migration access.
Production bootstrap/auditing, migration release checksums, large-data upgrades,
backups/restores, recovery rotation, restriction replay, and restore reconciliation
are not implemented here. The external recovery-generation read client exists,
but production OpenBao operations do not.

### Revision 0002

- Owner: identity and account authority.
- Forward plan: create global identity/session records and tenant-scoped membership,
  application-session, and site-grant records after revision `0001`.
- Lock risk: creation-only tables, indexes, policies, and grants under the existing
  bounded migration transaction; no live rows or backfill exist yet.
- Compatibility: revision `0001` command writers do not depend on the new tables.
  No customer identity route is enabled until a later compatible application release.
- Recovery: a failed revision rolls back to `0001`; destructive downgrade remains
  disabled. Recovery-generation validity must come from independent authority.
- Verification: repeated head upgrade, injected revision collision, hash-only token
  storage, issuer separation, scope cleanup, current-state invalidation, and grants.

### Revision 0003

- Owner: pre-tenant identity boundary.
- Forward plan: create hash-only short-lived OIDC login-attempt records after
  revision `0002`; no existing row is rewritten.
- Lock risk: creation-only table, functions, index, policy, trigger, and grants in
  the bounded migration transaction; no backfill is needed.
- Compatibility: existing command and session authorization paths continue to work.
  Customer authentication remains disabled until provider and secret boundaries exist.
- Recovery: a failed revision rolls back to `0002`; automatic destructive downgrade
  remains disabled.
- Verification: forced two-proof RLS, one-time concurrent consumption, expiry,
  immutable columns, least privilege, residual-scope rejection, and injected
  migration collision.

### Revision 0004

- Owner: identity and session boundary.
- Forward plan: force hash-scoped RLS on global identity sessions and grant only
  reviewed session-insert columns to `signal_identity` after revision `0003`.
- Lock risk: policy and grant changes take table locks in the bounded migration
  transaction. The current development database has no customer rows or backfill.
- Compatibility: existing tenant-session authorization remains valid through the
  exact child-session hash path; customer authentication remains disabled.
- Recovery: a failed revision rolls back to `0003`; automatic destructive
  downgrade remains disabled. Existing sessions can be revoked or allowed to expire.
- Verification: exact insert columns, no update/delete/truncate, forced RLS,
  non-enumerability, hash-only storage, current authority checks, collision retry,
  authorization composition, and injected migration failure.

### Revision 0005

- Owner: platform identity audit.
- Forward plan: add the first strict global platform-event type after revision
  `0004` and make successful identity-session issuance atomic with that event.
- Lock risk: creation-only table, indexes, trigger, policy, and grants in the
  bounded migration transaction; no existing session row is rewritten.
- Compatibility: every new identity session now requires its event insert. Older
  application code lacking that insert would still issue unaudited sessions and
  must not be deployed with this schema as a release combination.
- Recovery: a failed revision rolls back to `0004`; destructive downgrade remains
  disabled. A failed event insert rolls back its new session transaction.
- Verification: strict event/facts shape, typed session/user reference,
  append-only protection, forced hash-scoped RLS, exact insert columns, event
  collision rollback, and injected migration failure.

### Revision 0006

- Owner: platform identity audit.
- Forward plan: expand the event contract after revision `0005` with one strict
  failure event for an existing consumed OIDC attempt; no existing event is rewritten.
- Lock risk: altering constraints, nullability, the reference trigger, and the RLS
  policy takes locks in the bounded migration transaction. No customer data exists.
- Compatibility: successful-session events keep their exact prior contract. New
  callback code requires this schema to persist mandatory post-consumption failures.
- Recovery: a failed revision rolls back to `0005`; destructive downgrade remains
  disabled. A post-consumption database outage is exposed as a fixed audit failure
  but cannot be made atomic with prior external operations.
- Verification: migration rollback, strict facts and reasons, consumed-object
  integrity, two-proof RLS, single-use insertion, redacted stage mapping, no
  pre-consumption events, and unchanged least privilege.

### Revision 0007

- Owner: account authority and tenant audit.
- Forward plan: create the pilot's one-site invitation and first tenant audit
  contracts after revision `0006`; no existing account row is rewritten.
- Lock risk: creation-only tables, indexes, functions, policies, triggers, and
  grants. Runtime issuance locks tenant, site, membership, and site-grant rows in
  deterministic order, then takes one recipient advisory lock.
- Compatibility: existing authentication and commands do not depend on invitations.
  Acceptance code must not deploy until a later migration adds guarded transitions.
- Recovery: a failed revision rolls back to `0006`; destructive downgrade remains
  disabled. An event failure rolls back its new invitation transaction.
- Verification: exact role ceilings, current authority and concurrency locks,
  hash-only tokens, strict event/hash shape, forced RLS, exact column grants,
  duplicate and collision handling, atomic rollback, and migration failure.

### Revision 0008

- Owner: identity and account authority.
- Forward plan: add protected invitation scope routing, guarded single-use
  consumption, verified identity provisioning, and the second tenant invitation
  event after revision `0007`.
- Lock risk: migration backfill briefly removes forced owner RLS within the same
  transaction, fills the recipient-free route, and restores forced RLS before
  commit. Runtime acceptance uses deterministic row and identity advisory locks.
- Compatibility: issuance remains compatible and now creates routes atomically.
  Acceptance code requires this schema; ordinary login remains lookup-only.
- Recovery: a failed migration remains at `0007`. Any provisioning, consumption,
  or accepted-event failure rolls back the complete acceptance transaction.
- Verification: exact identity/email proof, replay and concurrency, disabled and
  existing-member denial, cross-issuer separation, bounded collisions, immutable
  consumption, event chaining, least privilege, atomic rollback, and migration
  failure.

### Revision 0009

- Owner: identity and account authority.
- Forward plan: add the minimal global membership routing index and one hash-scoped
  listing function after revision `0008`.
- Lock risk: creation takes ordinary DDL locks. The migration briefly disables
  forced owner RLS within its transaction to backfill existing memberships, then
  restores it before commit; the backfill and index build scale with membership
  volume and require a reviewed production maintenance window.
- Compatibility: prior login and invitation flows remain valid. The new API must
  not deploy before this schema because organization listing depends on the narrow
  function. Older code does not call it.
- Recovery: a failed migration remains at `0008`. Destructive downgrade stays
  disabled; tenant sessions already issued are bounded by their parent and live
  authorization checks.
- Verification: existing-row backfill, trigger synchronization, exact function
  and table privileges, active-state filtering, invalid-session indistinguishability,
  deterministic output, forced-RLS scope cleanup, and migration rollback.

### Revision 0010

- Owner: identity, session authority, and platform audit.
- Forward plan: expand the platform-event contract with user logout, add exact
  tenant-session update RLS for the migrator function, and expose one hash-scoped
  revocation function after revision `0009`.
- Lock risk: replacing event constraints and RLS policies takes bounded table
  locks. There is no data backfill; existing sessions remain compatible and
  initially unrevoked.
- Compatibility: prior authorization and issuance continue to require live parent
  sessions. New logout code requires this schema before it can revoke or audit.
- Recovery: a failed migration remains at `0009`. A failed audit insert or event-ID
  collision rolls back revocation; destructive downgrade remains disabled.
- Verification: parent and exact-child revocation, sibling invalidation through
  the parent, strict immutable event shape, exact function/table privilege,
  idempotency, concurrency, event collision retry and rollback, current-session
  checks, role-scoped RLS, and failed migration rollback.

### Revision 0011

- Owner: identity and account authority.
- Forward plan: bind immutable ordinary-login or invitation-acceptance purpose to
  durable OIDC attempts and add a separate hash-only short-lived invitee proof
  after revision `0010`.
- Lock risk: adding the attempt column briefly locks the attempts table; its
  constant default preserves existing rows and writers. Proof table, function,
  policy, trigger, index, and grants are creation-only. A production rollout must
  still assess live table size and PostgreSQL version behavior.
- Compatibility: prior attempt writers default to `login`. New proof issuance
  requires this schema, but no customer route invokes it and no authority is
  created. Older readers that select named attempt columns remain compatible.
- Recovery: a failed migration remains at `0010`; destructive downgrade is
  disabled. Revision `0012` later adds reviewed cleanup; revision `0011` alone
  must stay production-disabled.
- Verification: migration rollback, immutable purpose, verified-email and
  provider-time validation, hash-only storage, bounded expiry, collision retry,
  exact privileges, forced-RLS non-enumerability, immutable proof rows, and
  pooled-connection scope cleanup.

### Revision 0012

- Owner: identity, account authority, and retention operations.
- Forward plan: replace proof immutability with one guarded consumption
  transition, compose it with invitation authority in one function, revoke direct
  runtime use of the raw-identity primitive, and add bounded expired-row cleanup.
- Lock risk: replacing the proof check and trigger and adding RLS policies requires
  bounded table locks. No row backfill or rewrite is required. Production rollout
  must still assess lock timing against the small, short-lived proof table.
- Compatibility: the identity service must deploy with the schema because its
  acceptance signature now carries a proof hash. Existing proof issuance remains
  valid. Slice `0023` browser composition requires this schema before accepting a
  request.
- Recovery: a failed migration remains at `0011`. Authority, both consumption
  transitions, and the acceptance event roll back together. Destructive downgrade
  remains disabled; the cleanup function deletes only already-expired proofs.
- Verification: atomic success and failure, proof/invitation replay, expiry,
  recipient mismatch, concurrency, collision rollback, guarded mutation, legacy
  grant revocation, scheduler-only bounded cleanup, and migration rollback.

### Revision 0013

- Owner: platform identity audit.
- Forward plan: expand the closed consumed-login failure reasons after revision
  `0012` for invitation identity verification and proof persistence. No row is
  rewritten and no new runtime privilege is granted.
- Lock risk: replacing the platform-event check constraint takes a bounded table
  lock and validates existing events. Production rollout must assess event volume
  and lock timing before deployment.
- Compatibility: existing event types and reason values retain their exact
  contract. Invitation-purpose callback code requires this revision before it can
  append mandatory failure evidence.
- Recovery: a failed migration remains at `0012`; destructive downgrade remains
  disabled. OIDC/PKCE proofs already consumed before an audit outage cannot be
  recreated and the callback fails closed.
- Verification: existing-event compatibility, both new closed reasons, exact
  proof-scoped insertion, purpose-read ordering, redaction, and failed migration
  rollback.

### Revision 0014

- Owner: command service and identity authorization.
- Forward plan: expand command actors for human attribution, preserve service
  commands, and add exact authority/acceptance/status functions after revision
  `0013`.
- Lock risk: changing command constraints and adding the same-tenant actor foreign
  key takes a bounded table lock and validates existing commands. No row is
  rewritten. Production rollout must assess command volume and lock timing.
- Compatibility: existing internal service commands keep their actor, principal,
  route, payload, and fingerprint. New human-command code requires this schema;
  older service writers continue to satisfy the expanded constraints.
- Recovery: a failed migration remains at `0013`; destructive downgrade remains
  disabled. Runtime authority denial writes nothing, and an event or outbox error
  rolls back the human command.
- Verification: production-shaped upgrade with existing service intent, exact
  actor constraints and foreign keys, live session/authority reductions,
  cross-site conflict privacy, concurrent idempotency, atomic rollback,
  own-command visibility, and least-privilege function grants.

### Revision 0015

- Owner: command delivery and scheduler.
- Forward plan: replace blanket outbox immutability with guarded bookkeeping,
  add tenant-bounded claim/acknowledge/reschedule functions, and revoke direct
  scheduler table access after revision `0014`.
- Lock risk: replacing the trigger, adding a validated delivery-state constraint,
  creating a partial index, and changing grants take table or catalog locks. No
  existing row is rewritten. Production rollout must assess pending outbox volume
  and build timing in a reviewed maintenance window.
- Compatibility: existing pending rows satisfy the new state constraint and keep
  their event identity and availability. API inserts continue with database-owned
  bookkeeping defaults. Scheduler code must deploy with the migration because
  direct outbox reads are revoked.
- Recovery: a failed migration remains at `0014`; destructive downgrade is
  disabled. A worker crash expires into redelivery. A publish with lost
  acknowledgement remains ambiguous and is deliberately redelivered rather than
  deleted or silently treated as complete.
- Verification: production-shaped pending-row upgrade, failed migration rollback,
  bounded concurrent claims, active lifecycle locks, short lease expiry,
  monotonic attempt fencing, retry scheduling, immutable payload and identity,
  exact function and column privileges, direct scheduler denial, and scope cleanup.

### Revision 0016

- Owner: command delivery, workflow admission, and command service.
- Forward plan: add a separate non-owner workflow runtime role, forced-RLS inbox
  and workflow-reference tables, one exact command progress transition, typed
  admission evidence, and function-only admission after revision `0015`.
- Lock risk: changing command and event constraints, replacing the command
  trigger, and creating two tables plus an index require catalog and bounded table
  locks. Existing accepted commands and events satisfy the new constraints; no
  row is rewritten. Production rollout must assess command volume and lock timing.
- Compatibility: existing outbox envelopes remain valid. Internal and human
  acceptance keep returning an `accepted` receipt on exact retry. Status readers
  must deploy with the migration because the read function adds nullable workflow
  projection columns. Existing installations must provision `signal_workflow`
  before running the migration; fresh labs create it in `bootstrap.sql`.
- Recovery: a failed migration remains at `0015`; destructive downgrade remains
  disabled. Admission writes inbox, workflow reference, command progress, and the
  progress event atomically. A crash before commit writes nothing; a retry after
  commit returns the original receipt.
- Verification: production-shaped pending-event upgrade, failed migration
  rollback, exact-envelope matching, concurrent deduplication, lifecycle gates
  and locks, post-suspension duplicate handling, injected rollback, command and
  event guards, publisher/consumer role separation, and authenticated progress
  reads.

### Revision 0017

- Owner: workflow projection and command service.
- Forward plan: allow one guarded admitted-to-running workflow transition, bind
  the canonical Temporal first execution run ID, append strict start evidence,
  advance command progress, and extend authorized status after revision `0016`.
- Lock risk: replacing command/event/workflow constraints and two triggers takes
  bounded table and catalog locks while validating existing rows. Existing
  admitted references satisfy the expanded progress constraint without a rewrite.
  Production rollout must assess command and event volume before migration.
- Compatibility: existing accepted and admitted projections remain readable. New
  start-recording code requires this revision and the updated status reader;
  older writers cannot create `processing` state directly. The workflow role
  receives one additional exact function and no table access.
- Recovery: a failed migration remains at `0016`; destructive downgrade remains
  disabled. Start evidence, command progress, workflow projection, and event
  append commit atomically. If Temporal accepted but recording fails, repeat the
  same deterministic start and record calls rather than generating a new ID.
- Verification: admitted-row upgrade, failed migration rollback, exact and
  concurrent retry, first-run conflict, post-suspension recording, strict event
  shape, guarded identities, injected rollback, function-only least privilege,
  and authorized running-status projection.

### Revision 0018

- Owner: workflow admission and command delivery.
- Forward plan: replace only the workflow-admission function so an exact event
  redelivery returns event two's immutable `admitted` receipt after the current
  workflow reference advances to `running`.
- Lock risk: function replacement takes catalog locks but rewrites no table or row.
  Production rollout must still deploy it before relying on consumer redelivery
  after first-run recording.
- Compatibility: new and existing admitted/running rows are unchanged. The return
  contract stays `admitted`; consumers no longer receive the later current state
  in place of the earlier receipt.
- Recovery: a failed migration remains at `0017`; destructive downgrade remains
  disabled. Retry uses the same event, command, and workflow identity.
- Verification: running-projection duplicate receipt, post-suspension retry, one
  event/inbox/workflow identity, exact privileges, and failed migration rollback.

### Revision 0019

- Owner: workflow projection and command status.
- Forward plan: add guarded running-to-terminal command/workflow transitions,
  append exact event-four evidence, expose terminal status to the authorized read,
  and keep event three's start receipt stable after terminal progress.
- Lock risk: replacing validated command, event, and workflow constraints and
  their progress triggers takes bounded table and catalog locks. Existing accepted,
  admitted, and running rows satisfy the expanded constraints without a rewrite.
  Production rollout must assess command/event volume and deploy schema before the
  workflow definition can schedule terminal projection.
- Compatibility: existing status rows remain readable. New code requires the
  expanded status-reader signature. Older writers cannot create a terminal state
  directly, and the workflow role receives one exact function without table access.
- Recovery: command state, workflow state, and event four commit together. An event
  failure rolls back both projections. An unavailable projection is retried with
  identical evidence; different committed evidence is a terminal conflict.
  Destructive downgrade remains disabled.
- Verification: migration rollback, strict SQL input, exact/concurrent retries,
  conflicting evidence, post-suspension completion, stable start redelivery,
  immutable projections, atomic failure rollback, function-only privilege, and
  authorized success/failure/cancellation reads.

### Revision 0020

- Owner: crawler workflow, inventory, and frontier admission.
- Forward plan: add immutable running-crawl scope/limit records, stable normalized
  URL identity, provenance-bearing frontier state, guarded leases, and a dedicated
  function-only runtime role after revision `0019`.
- Lock risk: adding one exact workflow-reference uniqueness constraint validates
  existing workflow rows and takes a bounded table/catalog lock. The four new
  tables, policies, indexes, triggers, functions, and grants are creation-only.
  Production rollout must still measure existing workflow-reference volume and
  lock timing before migration.
- Compatibility: existing command consumers, workflows, and terminal projection
  remain valid. New crawler-admission code requires this schema. No current process
  invokes the new functions, so older application releases do not gain crawl
  behavior merely by applying the migration.
- Recovery: a failed migration remains at `0019`; destructive downgrade stays
  disabled. Run opening, root creation, enqueue, lease reassignment, and exhaustion
  each commit or roll back in one database transaction. External fetch and artifact
  reconciliation are not part of this revision.
- Verification: immutable snapshot validation, exact workflow binding, stable URL
  deduplication, scoped foreign keys, migration rollback, concurrent open/enqueue/
  claim behavior, count/depth/duration/attempt ceilings, exact lease retry, expiry,
  current authority reduction, mutation guards, and function-only least privilege.

### Revision 0021

- Owner: crawl evidence, artifact integrity, and fetch observation ingestion.
- Forward plan: add immutable artifact metadata, append-only integrity/restore
  attestations, exact lease-bound fetch observations, and a dedicated function-only
  ingest role after revision `0020`.
- Lock risk: adding one exact frontier-identity uniqueness constraint validates
  existing frontier rows and takes a bounded table/catalog lock. The three new
  tables, policies, indexes, triggers, functions, and grants are creation-only.
  Production rollout must assess existing frontier volume and lock timing.
- Compatibility: existing workflows, frontier claims, and HTTP fetching remain
  unchanged and disconnected. New observation code requires this schema. Applying
  it grants no network, Temporal, retention, or external-write authority.
- Recovery: a failed migration remains at `0020`; destructive downgrade stays
  disabled. Artifact publication before SQL failure leaves a non-authoritative
  orphan for grace-period reconciliation. An unreadable registered object is
  blocked through attested durability state and requires verified restoration.
- Verification: encrypted object identity, exact and concurrent observation retry,
  cross-site/worker rejection, time/header/address validation, append-only rows,
  integrity failure and restore, orphan preservation/deletion, exact privileges,
  transaction hygiene, and migration rollback.

### Revision 0022

- Owner: RFC-aware robots evidence and cached decisions.
- Forward plan: add one forced-RLS append-only snapshot table and function-only
  commit/current lookup operations after artifact revision `0021`; no new role or
  direct table grant is introduced.
- Lock risk: the migration creates one empty table, two indexes, one trigger, and
  two functions. It does not rewrite existing crawl, URL, artifact, or observation
  rows. Production rollout must still assess catalog lock timing.
- Compatibility: existing workflows, frontier, fetch observations, and artifact
  readers remain valid. New robots code requires this schema, but applying it
  grants no resolver, network, frontier, workflow, or production crawl authority.
- Recovery: a failed transaction remains at `0021`; destructive downgrade remains
  disabled. An encrypted object published before a failed snapshot commit is a
  non-authoritative orphan handled by the existing grace-period reconciler.
- Verification: run/profile/origin binding, conservative status constraints,
  encrypted body registration, current/expiry selection, newer restrictions,
  exact and concurrent retries, conflict/cross-site rejection, append-only rows,
  function-only privileges, and migration rollback.

### Revision 0023

- Owner: global crawler request admission.
- Forward plan: add canonical-origin buckets, one-request permits, global delay/
  backoff, exact completion, and bounded failed-worker reconciliation after robots
  revision `0022`; reuse the existing function-only crawl-admission role.
- Lock risk: the migration creates two empty control tables, three indexes, one
  trigger, and three functions without rewriting tenant crawl/evidence rows. The
  runtime lock order is current private frontier/lifecycle rows, then one origin
  bucket, then its permit rows; completion/reconciliation start at the bucket.
- Compatibility: existing workflows, frontier, robots, artifact, and observation
  readers remain valid and disconnected. Applying the migration creates no network
  caller, public resolver, or production authority. New admission code requires
  schema `0023`.
- Recovery: a failed migration remains at `0022`; destructive downgrade remains
  disabled. A worker crash leaves a permit until its bounded expiry. Acquisition
  or the bounded sweeper records `lease_expired` and recomputes bucket capacity.
- Verification: cross-tenant races, independent origins, exact active/terminal
  retry, identity conflict, current-authority reduction, strict delay, provider/
  latency backoff, expiry reconciliation, immutable identity, function-only
  privilege, database-clock fixture stability, and migration rollback.

### Revision 0024

- Owner: authority-free crawl page-attempt composition.
- Forward plan: add one forced-RLS page-attempt table plus exact begin, finish,
  lookup, and historical observation functions after origin admission revision
  `0023`; reuse the function-only crawl-ingest role.
- Lock risk: the migration creates one empty table, two indexes, one trigger, and
  four functions without rewriting existing crawl/evidence rows. Fresh begin locks
  current private authority before the exact origin bucket/permit. Completion
  locks the page attempt and delegates bucket-before-permit completion through the
  established origin function, all within one transaction.
- Compatibility: existing frontier, robots, admission, and observation APIs remain
  available. The production composition requires schema `0024`, but applying it
  grants no resolver, egress, workflow registration, or customer crawl authority.
- Recovery: a failed migration remains at `0023`; destructive downgrade remains
  disabled. A crash after dispatch is retained as unknown. An exact observation
  committed before page finalization can converge without refetch.
- Verification: pre-dispatch durability, exact/concurrent retry, robots denial,
  origin deferral, known and unknown failure, authority-reduced reconciliation,
  encrypted observation, provider/fallback backoff, immutable/function-only rows,
  joint PostgreSQL/network execution, and migration rollback.

### Revision 0025

- Owner: identity and current site-directory authority.
- Forward plan: add one private UUID-only site-membership route table, transactional
  backfill/trigger maintenance, and one hash-bound listing function after crawl
  page-attempt revision `0024`; grant only function execution to `signal_identity`.
- Lock risk: backfill briefly takes the locks required to toggle forced row-level
  security on `app.site_memberships` inside the migration transaction. Forced RLS
  is restored before commit; the trigger then adds only same-transaction route-row
  maintenance. Schedule the migration away from membership write bursts.
- Compatibility: existing exact-site authorization remains unchanged. Applying the
  migration grants no direct cross-site table read, active-site selection, browser
  login, mutation, connector, or production authority. Directory callers require
  schema `0025` and the current external recovery generation.
- Recovery: a failed migration remains at `0024` and rolls back the route table,
  function, trigger, and temporary RLS change atomically. Destructive downgrade is
  disabled. Triggered insert/update/delete keeps the route index synchronized;
  authority is still rechecked from ordinary tenant/site rows on every read.
- Verification: preexisting-membership backfill, injected migration failure,
  active/empty/multi-site directories, cross-tenant exclusion, revocation and
  lifecycle reductions, route deletion, malformed stored projection, exact
  function privilege, no direct route access, and 492 cumulative PostgreSQL cases.

### Revision 0026

- Owner: identity and tenant-session context authority.
- Forward plan: add one nullable tenant/site foreign key to `app.sessions`, one
  immutable forced-RLS context-event table, and narrow selection/snapshot-authority
  functions after site-directory revision `0025`.
- Lock risk: the migration briefly locks `app.sessions` while adding its nullable
  column and foreign key. Existing rows require no backfill and begin with no
  selected site. Schedule away from session write bursts.
- Compatibility: current-session HTTP consumers must adopt response schema version
  2. Existing sessions stay valid but cannot authorize site-scoped commands until
  a current site is explicitly selected. No site membership or external-write
  authority is created.
- Recovery: a failed migration remains at `0025`. Destructive downgrade is
  disabled. Selection failure rolls back both session and event; stale versions
  must refresh rather than be manually decremented.
- Verification: exact/idempotent selection, stale and concurrent conflict,
  cross-tenant denial, authority reduction, selected-site command enforcement,
  event allocation retry, hash-chain validation, immutability, function-only
  privilege, migration upgrade/rollback, and 508 cumulative real PostgreSQL cases.

### Revision 0027

- Owner: owner-controlled site configuration and identity authority.
- Forward plan: add one private tenant/site/origin lifecycle route mirror with
  transactional backfill/trigger maintenance, one immutable forced-RLS onboarding
  event table, and one narrow onboarding function after active-site revision
  `0026`.
- Lock risk: backfill temporarily toggles forced RLS on `app.sites` inside the
  migrator transaction and the trigger installation takes a bounded table lock.
  Forced RLS is restored before commit. Schedule away from site write bursts.
- Compatibility: existing rows and origins are mirrored without normalization or
  new uniqueness constraints. Applying the migration grants no browser login,
  ownership proof, crawl, connector, approval, undo, or external-write authority.
  New callers require schema `0027`, current recovery generation, owner role, and
  current-session response schema 2.
- Recovery: a failed migration remains at `0026` and removes its backfill, route,
  trigger, event table, and function atomically. Destructive downgrade remains
  disabled. Failed onboarding rolls back site, membership, session transition, and
  both events; exact ambiguous retries reuse the original idempotency key.
- Verification: preexisting-site backfill, migration failure rollback, atomic
  create/grant/select/audit, context-chain continuity, exact/concurrent replay,
  stale session, duplicate origin, owner and parent authority reduction, tenant
  limit, generated-ID collision retry, immutable/validated events, malformed input,
  and function-only least privilege across 537 cumulative real PostgreSQL cases.

### Revision 0028

- Owner: exact public-origin ownership proof and protected claim authority.
- Forward plan: widen the site ownership projection to explicit verified/recheck
  states; add forced-RLS challenge, attempt, and verification records; add a
  private global exact-origin claim; and grant the identity role only three narrow
  functions.
- Lock risk: the migration replaces the `app.sites` ownership check and creates
  new tables, indexes, triggers, and functions. The constraint change takes a table
  lock. Apply away from site writes and confirm no unreviewed ownership values.
- Compatibility: existing sites remain `unverified`. Current clients may continue
  to read that value; directory/API consumers must accept the new `verified` and
  `reverification_required` projections before proof can be enabled.
- Recovery: failed application remains at `0027` with the original ownership
  constraint and no partial verification tables/functions. Destructive downgrade,
  evidence deletion, manual expiry extension, site-origin edits, and claim
  reassignment are prohibited.
- Verification: owner/current-session/current-site authority, exact replay,
  challenge/attempt limits, expiry, failed-attempt durability, post-network
  authority reduction, global cross-tenant conflict, site promotion, immutable
  records, function-only privilege, malformed inputs, and migration rollback are
  qualified in disposable PostgreSQL 17.11.

### Revision 0029

- Owner: authenticated human-command read boundary.
- Forward plan: add one read-only function that reuses the complete current
  snapshot authority resolver and returns only the current actor's newest
  `api.site.snapshot` command for the selected exact site.
- Lock risk: function creation and one function grant only; no table rewrite,
  index, backfill, trigger, or row mutation.
- Compatibility: additive API for schema `0029`. Existing exact-command readers
  are unchanged. The identity role receives execute authority on this function
  only and still has no direct command, event, or workflow table read.
- Recovery: failed application remains at `0028` and leaves no partial function or
  grant. Destructive downgrade remains disabled.
- Verification: newest-command ordering, current actor/site filtering, absent
  state, authority reduction, function-only privilege, and injected migration
  collision rollback pass in the 550-case disposable PostgreSQL 17.11 suite.

### Revision 0030

- Owner: local evidence-to-finding boundary.
- Forward plan: add immutable evidence and evidence-link tables, one guarded
  current-finding projection, forced tenant/site RLS, and two exact authenticated
  functions after latest-work revision `0029`.
- Lock risk: creates three tables, indexes, triggers, functions, and grants without
  rewriting existing rows. Apply away from schema changes; no customer body or
  credential is backfilled.
- Compatibility: additive schema `0030`. Existing readers and workflow records are
  unchanged. The identity role receives execute authority on two fixture-specific
  functions only and no direct table privilege.
- Recovery: a failed migration remains at `0029` without partial tables, triggers,
  functions, or grants. Destructive downgrade and evidence deletion remain
  disabled. Disable the explicit pilot gateway to remove runtime access while
  retaining committed local evidence.
- Verification: completed-audit provenance, exact and concurrent retry, later
  audit advancement, evidence conflict, wrong-site and stale-generation denial,
  direct privilege denial, immutability, and injected migration failure are
  qualified on disposable PostgreSQL 17.11.

### Revision 0031

- Owner: fixture-only proposal revisions and exact local human decisions.
- Forward plan: add four forced-RLS immutable tables and three authenticated
  functions after evidence revision `0030`. Store canonical bytes and revision
  digest separately from the JSON projection so the exact decision identity is
  durable and inspectable.
- Lock risk: creates tables, indexes, triggers, functions, and function grants
  without rewriting existing rows. No customer body, credential, finding, or
  command is backfilled.
- Compatibility: additive schema `0031`. Existing audit and finding paths are
  unchanged. The identity role receives function-only access; the default API
  composition stays unavailable.
- Recovery: failed application remains at `0030` with no partial proposal or
  approval objects. Destructive downgrade and record mutation remain disabled.
  Disable the explicit local-pilot gateway to remove runtime access while
  retaining immutable records.
- Verification: owner-before-readiness gating, exact evidence, RFC 8785 digest,
  idempotent and concurrent preparation, stale digest, conflicting decision,
  immutable records, direct privilege denial, and migration rollback are
  qualified on disposable PostgreSQL 17.11.

### Revision 0032

- Owner: durable model-run intent and model-backed fixture proposal revisions.
- Forward plan: add two forced-RLS guarded tables and three authenticated
  functions after proposal revision `0031`; extend proposal revisions with a
  discriminated producer and exact model-call reference.
- Lock risk: creates tables, indexes, triggers, functions, constraints, and
  function grants. Existing revisions receive the deterministic producer default;
  no model call, customer record, or credential is backfilled.
- Compatibility: additive schema `0032`. Historical deterministic revisions and
  their decisions remain readable. The identity role gains function-only model
  orchestration access and no direct table authority.
- Recovery: failed application remains at `0031` without partial model tables or
  altered proposal constraints. Destructive downgrade and evidence deletion remain
  disabled. Disable the explicit pilot gateway to remove model-call access while
  retaining committed audit records.
- Verification: intent-before-I/O, exact completion, known failure retry ceiling,
  unknown-outcome blocking, stale evidence, manifest tampering, model-call
  immutability, direct privilege denial, and migration rollback are qualified on
  disposable PostgreSQL 17.11.

### Revision 0033

- Owner: verified-homepage evidence boundary.
- Forward plan: add immutable observation intent/result tables, extend evidence
  source and rights contracts, and add three exact authenticated functions after
  model revision `0032`.
- Lock risk: creates tables, indexes, triggers, constraints, functions, and grants;
  no customer body, credential, or historical finding is backfilled.
- Compatibility: additive schema `0033`. Existing fixture evidence and proposal
  decisions remain readable. The identity role gains function-only access.
- Recovery: failed application remains at `0032` without partial observation
  objects. Destructive downgrade and evidence deletion remain disabled.
- Verification: owner-proof and audit prerequisites, intent-before-I/O, exact
  replay, missing/present metadata outcomes, durable failure, authority reduction,
  immutability, direct privilege denial, and migration rollback are qualified on
  disposable PostgreSQL 17.11.

### Revision 0034

- Owner: evidence-bound verified-homepage model proposal admission and completion.
- Forward plan: extend the closed proposal release and authority constraints and
  add three authenticated function-only operations after observation revision
  `0033`.
- Lock risk: replaces constraints and creates functions/grants in one bounded
  transaction. No historical proposal, observation, model call, or decision is
  rewritten or backfilled.
- Compatibility: additive behavior on schema `0034`. Deterministic and fixture-
  model revisions remain readable and decidable. The identity role gains no table
  privilege and the model credential remains outside PostgreSQL.
- Recovery: failed application remains at `0033` without partial functions or
  altered constraints. Disable the explicit pilot gateway to remove provider-call
  access while retaining immutable records.
- Verification: exact verified evidence, intent-before-I/O, replay, missing title
  and H1, unknown outcome, manifest reconstruction, immutability, function-only
  privilege, and migration rollback are qualified on disposable PostgreSQL 17.11.

### Revision 0035

- Owner: Jev recommendation evidence boundary.
- Forward plan: add one immutable forced-RLS decision table and one function-only
  workflow write after model-proposal revision `0034`.
- Lock risk: creates one empty table, indexes, trigger, policy, function, and grant
  without rewriting historical rows. No credential, customer input body, or model
  authority is backfilled.
- Compatibility: additive schema `0035`. Existing proposal and model records remain
  readable. The workflow role gains only function execution and no direct table
  access; the Jev key remains in OpenBao.
- Recovery: failed application remains at `0034` without a partial decision table,
  function, policy, or grant. Destructive downgrade and decision deletion remain
  disabled.
- Verification: exact replay, conflicting identity, scope mismatch, policy-ceiling
  enforcement, provider/fallback shape, forced RLS, direct privilege denial,
  immutability, and injected migration failure are qualified on disposable
  PostgreSQL 17.11.

### Revision 0036

- Owner: shared egress admission and evidence boundary.
- Forward plan: extend the global origin permit kinds and add one immutable
  forced-RLS egress operation table plus narrow begin/finish functions after Jev
  decision revision `0035`.
- Lock risk: replaces two admission-lease checks and creates an empty table,
  indexes, trigger, policy, functions, and grants in one transaction. No historical
  crawl permit, provider request, credential, request body, or response body is
  rewritten or backfilled.
- Compatibility: additive schema `0036`. Existing crawl permits and decision
  records remain readable. The crawl-admission role gains only two function grants
  and no direct egress-table access.
- Recovery: failed application remains at `0035` without altered permit checks,
  partial egress objects, or grants. Destructive downgrade and operation deletion
  remain disabled.
- Verification: exact intent-before-I/O, shared global capacity, current robots and
  workflow authority, replay uncertainty, terminal completion, provider backoff,
  inconsistent completion rejection, forced RLS, direct privilege denial,
  immutability, and injected migration failure are qualified on disposable
  PostgreSQL 17.11.

### Revision 0065

Migration `0065`, following unchanged `0064`, implements the
[IndexNow boundary](../docs/implementation/0086-indexnow.md). It adds six forced-RLS
tables, narrow functions, and a committed-verification queue trigger; allows null
audit identity only for the closed owner-reviewed public-key recipe; and wraps
existing egress/PR eligibility without widening existing profiles or A2 families.
DDL takes ordinary table/constraint/function locks in one transaction and does not
backfill historical changes, keys, notifications, or credentials. The authoritative
key lives in OpenBao; its intentionally public exact patch is sealed for review.
Failed migration application rolls back to `0064`. Destructive downgrade is
disabled; disable optional composition and use reviewed forward recovery while
retaining immutable receipts. The merged Train 1 migration numbers are fixed.

### Revision 0079: Bing Page Performance

- Owner: existing Bing binding/import boundary, slice 0129.
- Forward plan: follows 0078 in the accepted merge train; adds the distinct
  `page_performance` kind and narrow ingest-only record/read functions. Original
  site-level and inbound-link imports remain unchanged.
- Lock risk: replaces and validates one small generation-kind constraint and
  creates functions in a single migration transaction. No historical generation,
  authority, credential or coverage is rewritten or backfilled.
- Compatibility: function-only additive internal read behavior; no table grants,
  new service, provider origin, write capability or measurement integration.
- Recovery: failed transactional application stays at 0078. Destructive downgrade
  and mutation remain disabled; leave page imports unconfigured while retaining
  immutable evidence. Live Bing and production composition are NOT_EXECUTED.
- Verification: [0129](../docs/implementation/0129-bing-pages.md) and its evidence
  record strict validation, shared-egress provenance, revocation, role and scope
  denial, and coverage readback on disposable PostgreSQL.

## Migration 0085: Chat Reports

Slice 0133 migration 0085 follows head 0084 in the merge train's linear Alembic
chain. It creates three forced-RLS app tables
(preferences, outbox, immutable receipts), private route/configuration/queue-failure
tables, function-only runtime grants and committed report/alert triggers. No
historical notifications, bindings or credentials are backfilled. DDL takes
ordinary creation/function/trigger locks in one transaction; failure leaves 0084
intact. Destructive downgrade is disabled. Disable optional report composition
for forward recovery, retaining all intents and terminal unknown receipts.
The [0133 record](../docs/implementation/0133-chat-reports.md) owns safety,
composition, runnable qualification commands and the NOT_EXECUTED live boundary.

## Dependencies

`requirements.in` lists direct dependencies; `requirements.txt` pins the full
Python 3.12 graph, including SQLAlchemy's architecture-dependent greenlet dependency,
the Authlib, HTTPX2, JOSE, and cryptography identity stack, Protego 0.6.2, RFC8785
0.1.4, and the Temporal Python SDK. RFC8785 provides dependency-free canonical JSON
bytes for exact proposal revision identity; it does not make policy decisions.
The cryptography pin is also the direct AES-GCM artifact-envelope implementation;
upstream declares Apache-2.0 OR BSD-3-Clause. Protego is BSD-3-Clause and 0.6.2 is
the minimum accepted release because it fixes GHSA-wjmf-p669-5m5p. Review and update both together,
install in a fresh environment, run `pip check`,
and rerun the complete suite. This is a version lock, not a wheel-hashed lock,
security audit, or evidence that every dependency supports every platform.
