# Slice 0168: Cleanup

Status: **IMPLEMENTED; FOCUSED QUALIFICATION PASSED; NOT DEPLOYED**.

Full gate: **DEFERRED_TO_MERGE_TRAIN (infrastructure: disk guard under three parallel tracks)**.

## Classification And Scope

Owner-approved track C, one product slice on main `a519a65`. Reuses existing
owner authorization, fresh-MFA preflight, reviewed releases and candidate builds;
no authority is minted or enlarged. The IndexNow key/secrets and browser startup
boundaries receive security-critical positive, negative and failure qualification.
No migration or new dependency. No ADR: existing ADR-0102/0103 and sandbox
contracts continue unchanged. SQL dispatch/authority and connector/egress
refactoring remain exclusively with tracks A and B.

## Reverification

- 0157 already removed dead CSS, unused icons, the fixture BFF and old labels;
  DataForSEO already uses the correct API base. These are not reimplemented.
- 0149 already repaired Activity/Inbox copy and supplies fresh-MFA owner PR
  paths, weekly pause and visibility schedule controls.
- 0156 already supplies Webflow owner setup and WordPress selects; initial
  WordPress binding remains operator-provisioned.
- 0154 already supplies owner question versions and structured-data producers;
  0161 already rebuilt the owner pages. The README live-binding banner is current.
- 0144 supplies exact Astro publicDir placement, but no caller created keys.
  The unused recipe is retained and exercised by the new owner route and tests.

## Implemented

- POST `/v1/sites/{site_id}/indexnow/key`, same-origin BFF and owner card action.
  A closed UUIDv4 request is browser-proven and byte-bounded; current owner,
  verified site, recovery generation and five-minute MFA are independently
  checked using the existing GitHub preflight. The configured recipe resolves
  an exact reviewed release and builds the one key-file patch before sealing
  the exact Inbox revision. No branch, PR, default-branch, deployment, merge,
  deletion or CI write is performed. Existing later Inbox/PR gates remain intact.
- The read projection explicitly reports whether key/build composition exists;
  an absent release, OpenBao key port, private GitHub transport, runner or release
  connection disables creation. Default/self-host/integration compositions do
  not silently acquire these ports. Live key creation/publication is NOT_EXECUTED.
- Capabilities follow actually composed gateways. Added all missing provider and
  evidence keys; failed optional chat probes stay disabled. `internal_only`
  means a component is composed, not current provider health, site authority or
  production readiness. Production writes and release certification stay off.
- Shared `relayJson`, API endpoint validation and streamed UTF-8 byte reader.
  Relays reject cookie mutation/redirects, cancel overflowing streams and retain
  each domain's strict response schema and endpoint budget. Cookie-mutating
  identity redirects remain in their separate validated identity boundary.
- One input-shape helper in authorization, one schedule create/update/pause
  helper, and one fail-closed runtime readiness probe. Domain SQL authority and
  eligibility checks remain in place; JSON duplicate-key hooks belong to track B.
- Shared readable outcome labels; misleading fallback state claims neutralized,
  unrendered ledger rows removed. Direction C CSS/tokens and repository design
  guards are untouched. Exact technical evidence is retained.
- Test-only synchronous worker, fallback classifier, page classifier and empty
  candidate port moved into test support; live async worker/decision service
  remain unchanged. Dead constants/type removed. Unused read/fixture routes
  remain compatibility/internal qualification ports, not claimed owner actions.
- Stale dashboard documentation, model-provider description, next-work text and
  duplicate status rows corrected. Accepted specification bytes are untouched.

## Deadlines And Qualification

The observation test checks the recorded exact 30-second interval and tests
backoff inside one PostgreSQL transaction using transaction time. It cannot
drift while unrelated assertions execute. Browser startup has a bounded
60-second allowance, then the existing action/session limit starts at readiness;
worker and host continue enforcing it. Temporal dev-server startup alone retries
recognized startup failures at most three times with two-second spacing; other
failures and every activity/workflow/action timeout are unchanged.

Commands and final counts are recorded in [evidence](../evidence/0168-cleanup.json).
The single full gate passed eight of 27 steps: npm 412 cases, Python 3,113 cases,
Ruff check/format, pip check, AI answers, Ask Signal and authority journal. Delivery
shards passed 57 and 55 cases, with twelve standalone worker-import failures and
one unrelated PostgreSQL fixture connection timeout. The browser lab failed before
a receipt, and sixteen later steps could not start below the unchanged 4 GiB guard.

After restoring capacity, the corrected script/package import passed both smoke
checks and all twelve worker-loss cases on real PostgreSQL/Temporal. Focused
Temporal passed three horizon cases, browser-worker eleven cases (rendering,
denied actions, time budgets and cleanup), IndexNow five cases (exact build and
all three observation-backoff paths), and disposable PostgreSQL forty-four cases
for shared-input callers and owner key preflight. Total: 75 passed, zero failed.
Each lab ran alone after a disk check above 4 GiB; invocation-owned resources were
cleaned up, with shared resources untouched. Both article replay cases already
passed in the original gate and were not repeated. The owner defers the full gate
to the merge train; it is not rerun locally. Ruff and npm run once on the committed
tip before push, with results reported in the PR. Live providers, deployed
composition, live key publication and first unattended execution remain
NOT_EXECUTED. Tests use disposable PostgreSQL/Temporal/containers and synthetic
provider doubles behind existing shared-egress ports.

## First Live Run Inputs

Dedicated owner session and fresh MFA, current verified origin, certified GitHub
read/PR extension, existing approved key recipe release, private OpenBao and
GitHub App ports, isolated build runner and artifact/registry ports where required.
An operator must explicitly compose these reviewed ports. The owner then reviews
the sealed Inbox revision and separately authorizes the existing PR flow; the
human publishes the file. Deployed-key observation is independently required
before changed-URL notification. No new live/provider run is authorized or claimed.
