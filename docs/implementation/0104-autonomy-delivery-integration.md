# Slice 0104: Autonomy-to-Delivery Integration

Classification: **security-critical**: connects standing authority to repository writes.
Base: `2e2dd24`. Contract: Revision 4.0 sections 4, 6, 7, 8 and 19; Revision 3.2
sections 18, 19 and 21; INV-004 through INV-013, INV-020, INV-027, INV-030,
INV-034, EC-129 and EC-130. [ADR-0093](../adr/0093-exact-standing-authority-delivery.md)
records the two distinct authorization kinds.

**Current state: implemented and locally qualified; live providers NOT_EXECUTED.**
The full gate on the rebased source passed: 831 PostgreSQL, 36 unsharded delivery,
1,243 API/identity/tooling/connector, 24 repository and 148 dashboard tests;
all other requested provider/container labs, typecheck/build, lint/format,
dependency and range-secret checks passed. Migration `0060` follows unchanged
`0059`. The transient OpenBao outage cleared without bypassing qualification.
Exact commands, counts and development corrections are recorded in the evidence.
This does not qualify production writes, live providers or release admission.

## Implemented Boundary

- `WeeklyTechnicalDelivery` loads only a committed 0068 crawl report linked to the
  cycle's successful observation command. It uses the observed repository format,
  reviewed 0103 release, 0081 exact patch and RFC 8785 seal, and 0080 disposable
  no-network build. No unsupported format is guessed.
- Separate platform autonomy attestations authorize no write on their own. Their
  closed family and exact-manifest checks exclude canonical/robots/redirect and
  other always-ask changes, even with a claimed A2 release. Text needing business
  claim review remains owner-only. No model clears that flag.
- Deterministic 0089 policy, current 0088 grant, caps, then Jev and its threshold
  precede a distinct immutable standing dispatch record. An exact owner Inbox
  approval uses the same 0084 path without any synthesized owner session.
- Operations, PR bodies and Changes identify the authorization kind, source record
  and real owner. Owner approvals come only from the existing exact-revision
  decision table, regardless of dashboard, Slack or Telegram channel. The channel
  is bound into the intent digest and sealed execution audit, and persisted
  immutably on the operation. Slack's and Telegram's risk step-up is unchanged; editorial Content Writer approvals are not accepted.
  Only a current exact authority can consume a dispatch record.
  Pause, revocation, expiry, changed grant digest/threshold, revoked release and
  base drift stop new effects. The two authority kinds cannot be combined.
  A later owner approval cannot revive or change the authority of an already
  journaled standing operation; its recorded source remains immutable.
- Independent journal acknowledgement and immutable exact execution receipt precede
  GitHub writes. The narrow installation/repository-bound JSON write profile is
  unchanged. Workers remain fenced. Lost responses become `OUTCOME_UNKNOWN` and
  read-only reconciliation, including after authority reduction, never blind retry.
- 0085 observations use read-only GitHub credentials and crawl GET admission,
  robots and origin controls. Only an exact correlated customer deployment plus
  matched sealed HTML postconditions can report `verified`.
- Current-cycle and backlog effects feed one evidence-only owner report. Pending
  revisions remain in the exact Inbox. Current-week report reads use the existing
  owner API and server-cookie BFF; no new browser write route or public secret.
- Exact unreserved cap deferrals receive a new cycle/gate identity, not a new patch.
  A reserved or dispatched revision cannot be requeued as a different operation.
- `WeeklyDeliveryRuntime` registers the existing deterministic Temporal workflow,
  current-authority schedule reconciler, exact owner-decision maintenance and
  bounded observation maintenance. Configuration is explicit, single-site and
  unavailable by default; no service is deployed or production write enabled.

## Migration and Role Ownership

Migration `0060_autonomy_delivery`, down `0059`, is self-contained. It adds three
private immutable forced-RLS app tables for workloads, standing dispatches and
execution receipts, plus platform attestations and a private scope directory.
Operations require exactly one owner decision or standing dispatch reference.
Only `signal_release_manager` can attest. Only `signal_workflow` can use the closed
workload ports, whose handle is not a human session and cannot mutate owner
decisions, grants or repository bindings. `signal_api` has owner-scoped report
and authorization projections. Migrations `0001` through `0059` are untouched.
Destructive downgrade is refused. Roll forward with reviewed corrective migrations.

## Verification

Run the full local gate from the repository root:

```sh
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-autonomy-delivery-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling tests/connectors
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/python -m pip check
npm test
gitleaks git --log-opts="main..HEAD" .
```

The dedicated delivery lab uses two disposable PostgreSQL clusters, real Temporal,
the actual shared-egress gateway and repository write profile, a synthetic GitHub
provider, and real disposable build containers. It exercises committed crawl to
live verification, exact human escalation, deterministic replay, worker restart,
stage-response loss, authority/cap/base denial, and lost responses at all four Git
mutation steps. Registry policy cases also run in the general database suite.
Separate-journal end-to-end tests live in `tests/delivery`; both labs require every
collected test to pass without skips against an unchanged source tree. No public
provider double runs.
Actual final counts are recorded in the [evidence](../evidence/0104-autonomy-delivery-integration.json).

The final delivery lab passed all 36 cases with zero failures, skips or deselections
against frozen source. The real PostgreSQL lab passed all 831 cases without skips.
An earlier 36-pass run was rejected by the unchanged source-integrity guard after
a concurrent formatting edit; it is not counted as qualification. A subsequent
database run caught an overly broad channel-trigger lookup during legacy lease
updates. Exact decisions are now checked on insertion; updates reject any changed
authority fields without repeating an unrelated forced-RLS lookup. Both original
dashboard/Slack recovery tests and the immutable-channel negative test pass.

CI keeps the existing 15-minute job bound. Its two complementary delivery shards
must both pass; each is explicitly recorded as partial, not full qualification.
The local command above runs the entire delivery suite without deselection.

## Accepted Train: Telegram Channel

Migration 0070 extends the 0084 operation channel constraint without changing
migrations 0001-0062 or replacing `control.bind_github_pr_decision_channel`.
The trigger derives `telegram` from the exact immutable owner decision, not a
caller-provided authority. 0104, Changes and weekly report projections retain
that channel. The new unsharded delivery regressions exercise exactly one PR
on repeated dispatch, a stale callback with no decision, and an approval that
becomes stale before dispatch. Risk and dashboard MFA refusals remain intact.
The accepted train's full unsharded delivery lab passes all 40 cases; its source-bound
[receipt](../evidence/0092-autonomy-delivery.json) includes the Telegram regressions.
The database execution budget remains a separate incomplete gate, not a passing
qualification. See [0092](0092-telegram-approvals.md) for the current checkpoint;
the counts above describe the original 0104 qualification, not the final train.

## First Live Run: Owner Setup

1. A verified active test site with current ownership claim, fresh robots evidence,
   a real 0066 outbox consumer/CrawlSite worker, encrypted artifact storage and
   proven origin admission. Never use a customer repository for an unqualified test.
2. One GitHub App installed on one disposable repository, owner-selected protected
   base branch, durable 0070 binding and current 0079 observation. Use the supported
   Eleventy/static HTML `index.html` subset with a network-free bounded build and
   no workflow, secret, lockfile or other protected paths. App permissions:
   contents/read-write, pull requests/read-write, checks/read, deployments/read and
   commit statuses/read; no merge, Actions, administration or secrets access.
3. A signed, reviewed structured-data or broken-link release, its separate platform
   autonomy attestation, and a current human grant for `metadata_pr`, exact release
   IDs, threshold, weekly volume/spend caps and recovery generation. The first
   deterministic test page must have a committed supported technical finding.
4. Trusted HTTPS OpenBao with the actual recovery anchor and read-only tokens for
   `signal-authority/data/recovery/current`, `signal-github/data/github/app` and
   the separate Jev credential path. Supply a real TypeSafe key. Optional natural
   text drafting must use the existing bounded credential/model port; unconfigured
   text generation is unavailable, never fabricated.
5. Primary PostgreSQL role DSNs and a physically independent intent-journal cluster,
   its append-only writer, Ed25519 key and encryption key with an independently
   retained verifier. Keys belong in protected deployment secret files, never the
   repository, build archive, logs or CLI arguments.
6. A dedicated single-site TLS Temporal namespace/worker connection, and already admitted site/tenant-bound
   GitHub, Jev and live crawl egress contexts. A context is evidence of an actual
   admitted run, not authority created by editing JSON. Running services must
   replenish admitted contexts and artifact keys through the existing controls.
7. The customer's own merge/deployment process, exact deployment environment and
   trusted deployment actor ID. The owner merges and deploys; Signal does neither.

`scripts/qualify_weekly_delivery.py` lists required configuration without execution:

```sh
.venv/bin/python scripts/qualify_weekly_delivery.py
```

It returns `NOT_EXECUTED` and exit 2 until explicitly configured. With the private
`SIGNAL_WEEKLY_*` settings named by that command, run:

```sh
.venv/bin/python scripts/qualify_weekly_delivery.py --execute \
  --tenant-id "$TEST_TENANT_ID" --site-id "$TEST_SITE_ID" --grant-id "$TEST_GRANT_ID" \
  --site-origin "$TEST_SITE_ORIGIN" --environment production \
  --deployment-actor "$TEST_DEPLOYMENT_ACTOR_ID" --reserved-cost-cents 25
```

This can open a real PR only under the recorded human grant and gate. `EXECUTED`
means the cycle ran, not that delivery passed. Inspect its immutable per-stage
evidence, authorize/merge externally, deploy, then run configured observation
maintenance with freshly admitted live contexts. A repeated current-week workflow
attaches to its existing identity instead of opening a different cycle.

## Explicit Limits

Live GitHub, live site, Jev and natural-language model calls are `NOT_EXECUTED`.
Production deployment, replenishing admission composition, signed delivery
certification, restore qualification, effect measurement at 7/28/90 days and R1-R4
release admission remain separate gates. This is an internal beta composition,
not a managed SaaS launch or a release certificate. No fabricated metrics appear.
