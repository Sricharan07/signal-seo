# Slice 0122: Dedicated Provider Callback Qualification

Classification: **security-critical live identity, connector and ingress qualification**.
Status: **SLACK OAUTH AND GITHUB READ BINDING LIVE-QUALIFIED; GSC CONSENT PENDING**.

This is the live follow-through of [0120](0120-live-test-connector-runtime.md)
and [0121](0121-test-callback-metadata-and-binding-failure.md), under the exact
owner exception in ADR-0094. It is dedicated integration evidence, not a release
certificate or authority for production writes.

Published personal and deployment identifiers in this record and its evidence
are redacted placeholders. Actual exact values are protected operator
configuration, not runtime defaults. The historical image digests below are
retained as historical observations, not regenerated or newly qualified images.
The original source checkpoint predates the owner-authorized history rewrite.

## Deployment And Recovery

Committed source `653c212` was archived without credentials and built on the
existing 4-GB/80-GB test VM. The deployed API is
`sha256:dee77830969e1481395dbda69edc9bd1e299b9e6889bba616edda0abe81a0e47`;
the dashboard is
`sha256:84cd5a0cccbdb3a38c779c78b5d4e23429aa1ccdf4a0376f16474316e6fd4457`.
Migration remains `0062`. Non-root, private TLS, read-only filesystems,
resource bounds and existing private networks remain in force. Fresh narrow
workload credentials were installed privately, with temporary upload copies
removed. The existing human Owner/MFA session and selected site survived the
restarts; its ordinary expiry is 2026-10-03 06:25:09 UTC. No cookies, MFA, account
records or authentication lifetime were weakened or reset.

The temporary root was already revoked and the original secure OpenBao listener
restored in 0120. Subsequently the actual Slack bot credential was read with an
exact narrow reader; cross-provider secret and token-creation access returned
403. That reader and the deployment operator were explicitly revoked and denied
403. An encrypted Raft snapshot after the Slack binding passed; restore was not
executed. This snapshot does not claim to include a later GSC credential.

## Actual Provider Results

**Slack:** The owner approved the exact final installation and displayed terms
for Test Workspace, with `chat:write` only. The repaired real browser callback reached the
normal application, exchanged the code through shared egress, privately stored
the bot credential in OpenBao and created the active binding. Safari showed
Connected; PostgreSQL independently confirms workspace `T0000000000`, channel
`C0000000000`, A0 and no revocation. One real provider POST and one real robots
GET were durably recorded. No message/history read scope was added. Signed
interactivity, per-user linking and Slack approval delivery remain unqualified;
OAuth installation is not proof of those capabilities.

**GitHub:** At action time the owner explicitly approved making only
`example-owner/integration-test` public and completed personal GitHub
reauthentication. Native Safari readback confirmed public visibility. The exact
`main` classic rule requires a pull request, one approval, dismissal of stale
approvals and no bypass; force pushes and deletions remain disabled. GitHub
confirmed the rule applies to one branch. The owner UI revoked the previous
unprotected prepared binding, then created a new exact read-only binding through
the real App JWT, repository-scoped installation token and repository/branch
reads. A separate Verify access action succeeded. PostgreSQL independently
confirms active, public and protected `main`; nine provider and nine real robots
operations include the initial failed attempt, successful binding and fresh
inspection. No code, default-branch push, workflow, PR, merge or deployment was
written. App access remains contents/metadata read on the sole approved repo.

**Google:** The owner explicitly approved publishing the verification file and
verifying only `https://signal-test.example.invalid/`. The selected Google identity
was the sole approved test owner, not another Safari account. The public file
`/google-placeholder.html` serves Google's exact 53 bytes through a GET/HEAD-only
handler. Existing Signal origin-proof and all private-route denies were retained.
The candidate Caddy configuration passed validation in a bounded, non-root,
read-only, no-network disposable container before replacing the unchanged live
configuration and restarting only ingress. Public bytes matched, the complete
isolation qualification passed again, and Google visibly confirmed Ownership
verified by HTML file. Root-domain DNS, email and other properties were unchanged.
The real read-only Search Console OAuth flow was then started; its final consent,
token exchange and owner-confirmed property binding are pending. No Gmail API,
write scope or paid Google resource was enabled.

## Qualification

```sh
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public --owner-connectors
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
```

The credential-free live command passed after image replacement and again after
the Google verification handler. It exercises real public/private TLS, bad CA
and hostname, missing/wrong callback state, secure PKCE correlation, external
return rejection, CSRF/anonymous mutations and private route/port denial. Its
human/provider fields deliberately remain pending/not-executed: the separate
human UI and durable receipts above establish those positive outcomes instead.
The unchanged 0121 source regression passed 874 real PostgreSQL, 1,442
API/identity/tooling, 41 repository and 153 dashboard cases, including positive,
negative and failure behavior. This record does not claim those regressions were
newly rerun after a documentation-only change.
The documentation-only `npm test` gate then passed 41 repository cases,
153 dashboard cases, 245 Markdown files, TypeScript and the production build.

The same four exact provider hosts were renewed with fresh screened pins and an
API-source-only window. The old all-state network expiry was applied first, and
the replacement persistent timer was armed before admission. Its expiry is
2026-10-03 00:10:13 UTC. No broad Internet, metadata, identity-admin, OpenBao,
database, Temporal or metrics access was opened. Shared robots evidence, origin
admission, forced-RLS immutable receipts and owner authority remain required.
There are zero standing authorizations; worker command acceptance and production
writes remain disabled. See [evidence](../evidence/0122-dedicated-provider-callback-qualification.json).

## Privacy Correction

The owner authorized replacing the nineteen unpushed checkpoints with a scrubbed
branch based on `404eeb9`. Environment identifiers now load from validated,
owner-only configuration; identity, exact-site/resource guards, imports,
deployment rendering and callback qualification no longer pin a real person,
network, App or installation in source. Missing configuration is visibly
unavailable and grants no authority. Tests pin placeholders or the configuration
loading contract instead. No accepted specification, authority gate, MFA,
session lifetime, public-address screening or provider scope is weakened.

The [protected environment runbook](../runbooks/integration-environment.md)
documents the complete configuration contract and exact owner steps to restart
Search Console consent. Google Audience was read back in Safari without signing
in or clicking consent: Testing mode, one test user, the approved owner. The
existing normal Owner/MFA session and Slack/GitHub bindings were read back
before and after redeployment. GSC consent and Slack interactivity remain unqualified.

All twelve requested local lab commands passed: 994 lab checks, including 874
real PostgreSQL cases, 36 delivery cases, 23 OpenBao and 5 Keycloak checks.
The API/identity/tooling/connector suite passed 1,659 cases; `npm test` passed
43 repository and 154 dashboard cases, 246 Markdown checks, TypeScript and the
production build. Ruff checked all application/service/script/test paths,
including delivery and page-attempt tests, and confirmed 354 files formatted;
`pip check` passed. A separate real disposable connector ACL lab also passed.
Ignored synchronized duplicate generated Next type files were preserved in
quarantine before a successful gate rerun, not treated as source changes.

The nonempty replacement history and its author/committer metadata have zero
matches for all 22 inventoried values. Gitleaks reports zero range findings using
main's unchanged configuration. The replacement commits use the owner's approved
noreply Git identity. The old local slice branch was reset to its remote at
`404eeb9`; no ref contains the retired unpublished tip. Main was not rewritten.
The final evidence commit receives another full-range scan before publication.

The approved private two-share recovery paused the API and validated the exact
existing seven-role deployment policy before issuing its one-hour operator.
The temporary root was revoked and denied (403), and the original secure listener
was restored byte-for-byte with recovery disabled (405) before application resume.
An initial operator configuration-permission error was restored safely without
issuing a root; the retry used the existing container UID and owner-only access.
Seven fresh one-use workload credentials and protected exact-scope configuration
were installed with the replacement API/dashboard images. Database, identity,
TLS, accounts, MFA, session expiry and the actual origin/proof handlers stayed intact.
The live database remains at migration `0062`; migrations `0061` and `0062` were
already applied and were neither renumbered nor reapplied by this redeploy.

Post-deployment TLS, PKCE, CSRF, missing/invalid callback rejection and private
route checks passed. Eight administrative ports were closed over both IPv4 and
IPv6, including probes from the authorized Mac source. The normal Owner/MFA
session survived without a fresh sign-in or session extension. Exact Slack A0
and protected GitHub read bindings survived. A fresh **Verify access** inspection
completed through the application egress/robots boundary: three provider operations
(201/200/200), three robots operations, and the visible verified result. Historical
Slack OAuth success and its privately stored bot were independently read back;
negative callbacks were rerun, not misreported as a new human consent exchange.

Three existing connector readers passed private configuration reads, one-use
replay rejection and nine cross-provider/authority/policy denials, then were
revoked and denied (403). A new encrypted Raft backup was captured without a
live restore. The operator was revoked and denied (403); application health and
the disabled recovery endpoint were independently rechecked afterward. All 116
RLS tables remain forced, standing authorizations remain zero and production
writes remain disabled. Only the same four API provider hosts were renewed for
one hour, with expiry armed before admission. No local lab resources were left
running; other worktrees' resources were not stopped. The ten digest-bearing lab
reports still match all 1,297 recorded source hashes, and the built runtime archive
matches all 201 checked runtime source files.

Current results, exact commands, source digests and remaining owner actions are in
[privacy evidence](../evidence/0122-integration-history-privacy.json).
