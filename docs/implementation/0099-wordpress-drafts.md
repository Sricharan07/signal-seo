# Slice 0099: Owner-Approved WordPress Core REST Drafts

Status: **INTERNAL OPTIONAL DRAFT-ONLY BOUNDARY LOCALLY QUALIFIED;
LIVE WORDPRESS NOT_EXECUTED; R4 PARTIAL**.

## Objective And Tier

This slice is **security-critical**: outbound CMS writes, an application password
and external-write authority. The accepted Core REST subset implements new drafts
only, not existing-post updates or publishing. [ADR-0110](../adr/0110-core-rest-wordpress-drafts-only.md)
records the boundary; [ADR-0111](../adr/0111-wordpress-authority-and-egress.md)
records authority, secrets, egress and journal composition. Specifications are unchanged.

## Implemented Boundary

- Migration `0073` follows unchanged `0072`: five forced-RLS tables for bindings,
  sealed candidates, immutable reviews, private intents and immutable receipts.
  Runtime roles have no direct table rights. Narrow RPCs require current owner,
  selected tenant/site and recovery generation.
- Binding requires existing verified-origin proof and a dedicated application-password
  user. Administrator, Editor and excessive capabilities are refused. Credentials
  stay in OpenBao; rows contain observed user/roles/capabilities and proof
  generations. The operator-provisioned tenant/site/origin assignment must match
  current owner-derived scope before any credentialed provider request, including
  initial binding. Independent restriction-journal replay preserves revocations.
- Closed `WORDPRESS_REST` JSON Basic-auth egress permits only exact current-user,
  own-marker or recorded-id GET and new-post POST on the bound HTTPS origin. POST
  forces draft. Other methods, paths, origins, overrides, redirects and statuses
  are denied. Robots, shared admission and current exact dispatch authority apply.
- One accepted grounded 0082 new article renders to bounded escaped HTML and a
  canonical sealed digest. The owner sees content and exact approval in the Inbox.
  No autonomy or standing grant applies. Changed facts, superseded briefs,
  membership/site epochs, origin proof, recovery generation or pause deny writes.
- Idempotent queue and deterministic marker bind each intent. A separate encrypted
  journal acknowledges each permitted attempt before POST. Restored primary state
  and concurrent replay cannot repeat an already-journalled attempt. Receipts
  never claim publication or deployment.
- Ambiguity leaves `outcome_unknown` quarantined: one valid own-marker match records,
  zero retains quarantine, multiple escalate. Owner-requested read-only checks are
  limited to twelve, at least five minutes apart; no automatic scheduler is deployed.
  A delayed commit can later be recorded. Ambiguous intents never re-POST.
- Retry once only after explicit pre-dispatch admission deferral or narrowly
  recognized definitive pre-write rejection. Other failures, including post-insert
  4xx metadata errors, remain unknown. Explicit owner confirmation can create a
  fresh intent/marker while the old intent stays quarantined and reconcilable.
  The same bounded per-site weekly writer delivery cap applies.
- Existing updates and publishing show ADR-0110's exact unavailable reasons.
  No bridge exists. After independent owner publication, recorded-id GET compares
  sealed title/content/excerpt and records differences as `edited_before_publish`.
  A changed permalink also records that state through the exact recorded id;
  slug-marker matching stays mandatory only for ambiguous-write reconciliation.
  The optional 0085 credential-free public GET verifies visible article tokens,
  canonical/noindex/HTTP/URL conditions. This is bounded HTML verification, not
  theme/browser visual certification; Signal never changes the recorded post.

## API And Dashboard

`GET /v1/sites/{site_id}/wordpress` reads a bounded owner projection. CSRF-protected
POST accepts only `connect`, `seal`, `review`, `create`, `reconcile`, `observe` and
`revoke`, with closed typed fields. No credentials or arbitrary provider command
are accepted. The same-origin BFF bounds requests/responses, forwards only the
existing session cookie, rejects redirects and validates identities, state, digests
and bound HTTPS links. Connectors/Inbox show unavailable, review, recorded,
quarantine, retry, exhausted and edited-before-publish states honestly.

Responsive UI review used synthetic SSR fixtures at 1440 and 390 pixels with the
real stylesheet/page frame, visually checked with no horizontal overflow. Four
dashboard tests cover escaping, unavailable copy, closed same-origin mutations
and the stale-binding revocation path needed before reconnecting.
These fixtures are not a live authenticated owner journey. Existing design records
are preserved; this is an ordinary operational-dashboard extension.
An unauthenticated local Next dev-server smoke GET returned 200; that temporary
server was stopped and its port was confirmed no longer listening.

## Qualification

Exact counts are in [0099 evidence](../evidence/0099-wordpress-drafts.json).

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-autonomy-delivery-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
.venv/bin/pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/python -m pip check
npm test
gitleaks git --config .runtime/wordpress-0099/main.gitleaks.toml --log-opts="main..HEAD" .
```

Delivery includes 35 WordPress cases on real primary and independent-journal
PostgreSQL, with a loopback stdlib REST double behind real shared egress. The double
models lost/truncated responses, delayed commits after empty reads, duplicates and
pre-/post-write rejection. It is not real WordPress or live provider TLS. Separate
full-gate network/OpenBao labs qualify TLS/ACL behavior; profile tests reject plaintext.

## First Live Run And Limits

Live WordPress/application-password success, customer publication, production
composition and authenticated browser journey are **NOT_EXECUTED**. No R4 release
or default production write authority is granted.

Supply a disposable WordPress site on the verified HTTPS origin, a dedicated Author
or narrower role, application password privately provisioned in OpenBao, current
recovery authority, independent journal and managed signing/encryption keys. Create
a `signal-wordpress` KV v2 mount with exact `username`, `password`, `tenant_id`,
`site_id` and `origin` fields at `bindings/<binding UUID>` and a token with only
that binding's read capability. Tenant/site UUIDs and verified HTTPS origin must
match the server-derived owner scope, not user-supplied assignment metadata.
All identifiers and values belong in private configuration.

Explicitly compose `ComposedWordPressGateway` through
`create_app(browser_wordpress=...)` using API/identity connection factories,
recovery authority and `WordPressService`. Inject its OpenBao reader, independent
`WriteIntentJournal`, real `SharedEgressProvider` factory with pinned verified-origin
policy/private TLS transport, and optional `SharedLiveVerifier`. Supply currently
approved Business Brain facts and an accepted 0082 new-article result. The owner
seals, reviews and creates a draft in the dashboard, then inspects/publishes in
WordPress independently. Preserve ambiguous intents and reconcile read-only.
Customer writes remain disabled until dedicated live qualification passes.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0073` follows `0072`; 138 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2008 API/identity/tooling/connectors, 975 PostgreSQL,
43 repository and 207 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
