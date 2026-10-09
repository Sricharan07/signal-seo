# Slice 0156: Publishing Owner Paths

Status: IMPLEMENTED; TARGETED LOCAL QUALIFICATION PASSED; LIVE CMS NOT_EXECUTED.

## Scope And Classification

Security-critical: connector credentials, OAuth, exact owner approval and content
destined for an external CMS. Revision 4.0 sections 4, 12, 14 and 19 and Revision
3.2 sections 2, 10, 18 and 24 apply. INV-002/003/004/005/015/024/025 and EC-136,
EC-141 and EC-144 remain enforced. [ADR-0166](../adr/0166-owner-publishing-paths.md)
records the owner boundary; [evidence](../evidence/0156-publishing-paths.json)
records actual command outcomes, not production readiness.

## Interfaces And Ownership

- POST `/v1/sites/{site_id}/webflow/begin`: exact provider site and collection;
  authenticates before OpenBao client lookup. Returns a validated authorization URL.
- POST `/webflow/complete`: exact attempt/state/code and owner-confirmed mapping;
  existing state consumption, grant/domain/collection/schema validation and OpenBao
  CAS-zero storage are reused. The dashboard callback validates origin, unique
  parameters, current session and host-only attempt cookie, clears the cookie and
  redirects without sensitive parameters. No browser JSON completion route exists.
- POST `/webflow/seal`: exact binding/candidate/source digest. A current exact
  owner delivery approval is required even for a replay. A separate CMS Inbox
  decision still covers the generated immutable CMS payload.
- POST `/webflow/revoke`: local denial first, independent restriction outbox,
  best-effort upstream revocation and OpenBao cleanup. Pending journal durability
  stays AUTHORITY_DURABILITY_PENDING; the UI does not claim durable revocation.
- GET `/webflow/options`: at most 100 named approved articles and 100 reviewable
  original new-article drafts. Current owner, verified origin and scoped joins
  gate this function-only projection; no credentials or secret references appear.
- WordPress GET gains the bounded draft selections. The owner chooses a current
  binding by origin/role and an Article by title. Seal still sends exactly
  `{operation:"seal",binding_id,draft_id}`. All other command shapes are unchanged.
  With no binding, the operator must provision a dedicated least-privilege user,
  an application password in scoped OpenBao, and bind it to the verified site.

The Webflow connection card is in Content sources and publishing with the shared
ConnectionStatus pill, ink primary commands and identifiers under Technical
details. dashboard-view.tsx has only an import and one component hook. IndexNow,
weekly-loop modules and the existing dashboard design guards are untouched.

## Authority And Egress

Migration 0095 follows 0094 (merge train 5; written as 0090 after main's 0089), adds no tables and wraps the existing Webflow
command without exposing its old function to signal_api. Current owner/MFA and
fresh authentication precede public mutations; current generation, origin, pause,
exact article approval and binding checks are deterministic. Revocation preserves
its existing ability to remove local authority even if site verification was lost.

The optional ComposedWebflowGateway is injected as create_app's browser_webflow.
Supply an idle autocommit signal_api connection factory, recovery authority,
OpenBaoWebflowSecrets, HTTPS dashboard origin and WebflowOwnerEgressFactory.
The latter takes the existing identity-role egress factory, encrypted artifact
store/key and public-screened pinned fetcher. No fallback token store or direct
HTTP client is added. These are optional host composition dependencies, not a new
public environment setting or a production-readiness claim.

Owner egress permits only exact token exchange/revocation and bound validation
reads; current consumed OAuth attempt, site/collection, MFA, recovery, verified
origin, robots and global origin admission are checked. Item creation, item
listing, update, publish, delete and unrelated routes are denied. Existing Webflow
draft-write profiles and deliver's dual lab gate are unchanged. The global Webflow
API origin guard gains only the owner connector's exact GET
`https://api.webflow.com/robots.txt`; queries and other non-profile API requests
remain denied. No production delivery route exists.

## Qualification

Positive: browser-proof protected commands; named WordPress choices; owner OAuth
through actual PostgreSQL/shared egress; mapping validation; sealed article,
separate Inbox review, replay and pending revocation; real TLS OpenBao ACL/storage
qualification; disposable real WordPress stack.

Negative: absent/cross-origin mutation proof, unknown fields, token-bearing
requests/responses, duplicate/cancelled callbacks, wrong site/digest, stale or
non-MFA sessions, unapproved article, out-of-scope egress and unavailable writes.

Failure: bounded upstream/API response rejection, provider/secret errors without
exception disclosure, unchanged quarantined unknown CMS outcomes and pending
restriction durability. Provider doubles supplement PostgreSQL and OpenBao;
they do not establish live Webflow compatibility.

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
npm test
.venv/bin/python scripts/run-database-tests.py --shards 3
.venv/bin/python scripts/run-webflow-tests.py
.venv/bin/python scripts/run-wordpress-draft-tests.py
npm run test:wordpress
.venv/bin/python scripts/openbao_lab.py
```

The bounded WordPress draft runner deliberately selects only its PostgreSQL
delivery cases; it does not start Temporal or run the full delivery suite. Labs
own only their disposable containers and never prune or stop another session's
resources. Disk admission is checked before heavy labs. Staged gitleaks uses
main's unchanged configuration and precedes the repository-local-identity commit.

Final results on 2026-10-04:

| Command | Result |
| --- | --- |
| Fast pytest: API, identity, tooling and connectors | 2,886 passed; two existing cookie deprecation warnings |
| Ruff check | Passed |
| Ruff format check | 638 files already formatted |
| Root npm test | 46 repository and 274 dashboard tests passed; documentation, typecheck and build passed |
| Database lab, three shards | 1,226 passed, no skips or failures |
| Webflow lab | 60 passed; PostgreSQL, independent journal, TLS OpenBao and provider doubles |
| Bounded WordPress draft lab | 35 passed; PostgreSQL and independent journal, no Temporal |
| Real WordPress feasibility lab | 21/21 passed |
| OpenBao TLS/ACL lab | 45/45 checks passed |
| Static UI detector | Zero findings |

The first database run passed 1,219/1,226; seven upgrade/downgrade assertions still
expected the prior 0089 head. Only those exact expected revisions advance to 0090;
data preservation, admission, privilege and destructive-downgrade assertions are
unchanged. The complete rerun passed. The owner OAuth journey also exposed the
missing exact robots admission; the bounded exception above now has allow/deny
tests. A stale-binding copy assertion remains satisfied inside Technical details,
with the revocation action preserved. No design guard is modified.

## Remaining Limits

Live Webflow OAuth/provider schema, upstream revocation and dedicated runtime
deployment are NOT_EXECUTED. Live customer WordPress and production composition
remain NOT_EXECUTED. No production CMS writes, updates or publishing are enabled.
The Webflow lab-only deliver gate is preserved exactly. No accepted specification,
existing migration, git identity or secret-scanner allowlist is modified.

Desktop/mobile browser screenshots were attempted but NOT_EXECUTED: the available
headless browser did not complete and computer-use reported no enabled browser.
The actual components were server-rendered and checked by dashboard tests and the
static detector; these checks are not a claim of visual browser qualification.
