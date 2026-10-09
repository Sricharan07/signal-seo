# Slice 0098: Webflow Draft Delivery

Status: **INTERNAL DRAFT PATH LOCALLY QUALIFIED; LIVE WEBFLOW NOT_EXECUTED; PRODUCTION WRITES DISABLED; R4 PARTIAL**.

## Objective And Tier

Security-critical: external CMS writes and OAuth credentials. Revision 4.0
sections 6.2/12 and Revision 3.2 sections 2/10/18/19 apply.
[ADR-0116](../adr/0116-webflow-draft-only-without-atomic-preconditions.md)
records the official-v2-only finding: no documented atomic update/publish
precondition. [ADR-0117](../adr/0117-webflow-bound-draft-delivery.md)
records binding, authority and recovery.

## Implemented Boundary

- Migration `0075` follows `0074` in the accepted merge train.
  Six function-only tables force RLS; revisions, reviews and events are immutable.
- Current owner, verified origin, one provider site and one selected collection.
  Ten-minute hashed OAuth state is single-use and recovery-bound. Actual grant
  must have exactly `cms:read`, `cms:write`, `sites:read`, selected site only, and
  no workspace/user authority. `sites:read` supplies published-domain evidence.
  Nonconforming live grants remain unavailable, never silently accepted.
- Collection membership and details are checked; a published custom domain must
  equal the verified HTTPS origin. Owner-confirmed name/description/body mapping
  requires compatible editable PlainText/RichText fields. Extra required fields,
  nonempty validations and schema drift refuse delivery; no schema/design write.
- OAuth material is OpenBao KV-v2 only, CAS-zero with restricted client-read,
  token-create/read and metadata-destroy ACLs. Rows have opaque references. Local
  revocation precedes best-effort provider revocation and token destruction; the
  restriction journal acknowledges durability and replays missing-target denial.
- Source is a sealed 0082 original `new_article`, A2 threshold 0.95, never autonomy
  eligible, with current approved facts and passed candidate provenance. RichText
  escapes source content. Sealing persists one operation identity, deterministic
  slug suffix, canonical payload and digest. A separate exact CMS Inbox owner
  approval is mandatory. Epochs, recovery, verification, pause, binding and source
  are rechecked at the last network fence; no standing grant substitutes.
- Independent encrypted/signed write intent precedes a 30-second nonce permit.
  One POST at most to `/v2/collections/{bound_collection}/items/insert`: one new
  primary-locale item, explicit `isDraft: true`, no archive or locale selectors.
  The closed `WEBFLOW` profile binds exact token/site/collection/body hash; database
  admission binds the same operation. Only introspection/domain/collection reads,
  marker reads and that insert route exist. Request/response caps are 64/128 KiB,
  five-second timeout, zero redirects. OAuth/revoke use separate exact profiles.
- After any possible transmission, only reads reconcile: one exact untouched
  draft records; zero stays `OUTCOME_UNKNOWN` and quarantined; multiple/conflicting
  matches escalate permanently. A delayed commit may resolve without a second
  POST. The marker is not native provider idempotency. Restored primary state
  cannot erase an independent intent; unresolved site intents block more writes.
- Owner dashboard/API read and exact review reuse browser authentication and CSRF.
  No public dispatch route exists. Update and publish expose their exact
  atomic-precondition-unavailable reasons; the owner publishes in Webflow.
  DELETE, archive, live, design, hosting, users and schema mutation are forbidden.

## Qualification

[Evidence](../evidence/0098-webflow.json) records exact results. The dedicated lab
uses two disposable real PostgreSQL clusters, TLS OpenBao and Webflow doubles
behind actual shared egress. No customer data, credentials or public test service;
owned resources clean up on exit. No new workflow, service or dependency.

The final gate passed 17/17 commands: 2,201 Python cases, 175 JavaScript cases,
14 independent-journal and 23 OpenBao checks, with zero failures or skips.
Ruff checked all targets and formatting passed for 377 files. The initial API
regression rejected the newly introduced review route; its exact mutation
allowlist now includes only that bounded owner review, and 1,249 cases passed
on rerun. No delivery, update, publish or DELETE route was added. Provider and
core behavior did not change after their lab qualification.

Staged secret scanning precedes commit; the unchanged-main-config commit-range
scan is recorded in the PR after the commit exists. The interface detector's
incumbent-CSS findings remain outside this slice; the new heading uses the
existing body token. Browser screenshots are `NOT_EXECUTED`: no browser surface
is available in this environment. Escaping, truthful states and the BFF boundary
are covered by the dashboard tests; production build and type checking pass.

```sh
.venv/bin/python scripts/run-webflow-tests.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-autonomy-delivery-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling tests/connectors
.venv/bin/ruff check apps services scripts tests database/migrations
.venv/bin/ruff format --check apps services scripts tests database/migrations
.venv/bin/python -m pip check
npm test
gitleaks git --config .gitleaks.toml --log-opts="main..HEAD" .
```

## Live Qualification And Owner Inputs

Live OAuth, provider reads, draft creation, ambiguity/late-commit behavior,
upstream revocation and deployed composition/key lifecycle are **NOT_EXECUTED**.
Production dispatch is hard disabled. An explicitly constructed disposable-test
service and `SIGNAL_WEBFLOW_LAB=1` exercise doubles only; not a dashboard setting
or owner grant. No R4 release or provider-readiness claim is made.

A first live qualification needs a dedicated disposable Webflow OAuth app with
the exact scopes and registered private dashboard callback; one consenting
owner-selected site with a published domain matching a Signal verified origin;
a compatible collection and confirmed mapping; current approved Business Brain,
sealed article and separate CMS approval; scoped TLS OpenBao; independent journal
and managed signing/encryption keys plus restriction dispatcher; qualified shared
egress; and a reviewed live qualification harness. Existing items must not be
touched. The owner publishes manually. Production or future conditional writes
need subsequent qualification and a superseding decision, never gate weakening.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0075` follows `0074`; 148 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2073 API/identity/tooling/connectors, 994 PostgreSQL,
43 repository and 221 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
