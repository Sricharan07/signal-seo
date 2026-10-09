# Slice 0097: GA4 Binding and Import

Status: **LOCAL OPTIONAL BOUNDARY IMPLEMENTED; LIVE GA4 AND PRODUCTION COMPOSITION
NOT EXECUTED; BROWSER VISUAL QUALIFICATION BLOCKED**. Security-critical R4 slice
under Revision 4.0 sections 11, 12, 16, 19 and Revision 3.2 connector lifecycle
section 10 and analytics sections 12.2, 12.4. This does not certify R4 measurement.

## Implemented

- Reused Google OAuth/PKCE and OpenBao CAS lifecycle without changing GSC defaults
  or its existing tests. Only `analytics.readonly` is accepted by GA4. Hashed state
  is ten-minute, one-use, session/owner/site/origin/redirect/recovery-bound; the
  verifier is permanently consumed before code exchange.
- Isolated `signal-ga4` KV v2 paths: `oauth-client`, `verifiers/<attempt>`, and
  `refresh/<attempt>`. Runtime rows contain only a `secret://ga4/<attempt>` reference.
  Access tokens are memory-only. Rotation is serialized and persisted with CAS
  before further reads. Errors have fixed codes, not provider payloads.
- Owner with MFA explicitly confirms one property discovered from Admin account
  summaries. Its web stream must have the exact normalized verified site origin.
  Confirmation rechecks current authority and the matching observed stream receipt.
- Data API reports request `pagePath`, sessions, engaged sessions, engagement rate,
  and key events with absolute dates, a maximum 92-day difference, 1,000-row pages,
  and at most 5,000 rows. Imports retain immutable coverage, property quota and raw
  metadata per page with observed egress IDs/digests. Sampling, thresholding, and
  `(other)` are flagged. Coverage is always incomplete and missing data unknown.
- Typed closed `ga4_admin` and `ga4_data` profiles reuse shared crawler admission,
  robots evidence, public-address pinning, origin politeness, size and time limits.
  Admin uses only account summaries and the bound property's web streams. Data uses
  only the bound property's `runReport`; no property/tracking write exists.
- Forced-RLS, function-only owner lifecycle; immutable bindings/imports/restrictions.
  Local denial precedes upstream disconnect. Restrictions use the independent
  authority journal, pending/durable acknowledgement and deny-only restore replay.
  The API composition rereads external recovery authority before committing after
  provider I/O. Current owner/site/session checks run again on import persistence.
- Optional cookie/CSRF API GET/POST `/v1/sites/{site_id}/ga4`, same-origin dashboard
  BFF `/auth/ga4`, exact callback `/auth/ga4/callback`, and Connectors controls for
  consent, property selection, date-bounded import, coverage, refresh, and disconnect.
  Missing gateway configuration returns 503/unavailable, never a pretend connection.
- Alembic migration `0067` follows `0066` on the accepted merge train.
  No existing migration or hash-protected specification was changed. Head assertions
  now expect `0067`. ADRs [0088](../adr/0088-ga4-read-only-owner-binding.md) and
  [0089](../adr/0089-ga4-import-coverage-and-egress.md) record the decisions.

## Limits and Unknown Outcomes

Live GA4 consent, Admin/Data reports, scope grants, token rotation, and revocation
are `NOT_EXECUTED`; synthetic provider doubles run behind the actual shared egress
gateway, with real PostgreSQL and separate real OpenBao qualification. There are no
customer credentials or customer data. Browser screenshots are `NOT_EXECUTED`:
the computer-use environment exposes no available browser and app selection fails.
The UI has server-render contract tests, typecheck, build, and an HTTP 200 smoke
check, not visual approval. Independent finish review requires desktop/mobile
recapture. Incumbent design tokens/files remain unchanged.

No background import scheduling, orphan-token cleanup, production gateway wiring,
GA4 tracking validation, consent-setup assessment, effect measurement, or property
mutation is implemented. Expired staged attempts cannot be selected, but an
operator must remove orphan OpenBao refresh paths. Local disconnect is effective
even if Google revocation fails; `upstream_revoked=false` is explicit. If OpenBao
destruction is unconfirmed, the API reports a fixed failure rather than claiming
cleanup success; the local restriction still denies use. Operators must retry
cleanup with the retained opaque binding reference.

## Verification

Run these exact gates from the worktree with Docker available:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-autonomy-delivery-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
.venv/bin/pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/pip check
npm test
git diff --check
gitleaks git --config .gitleaks.toml --log-opts=main..HEAD --redact --no-banner
```

Counts, sanitized source-hashed lab receipts and explicit unexecuted items are in
[0097 evidence](../evidence/0097-ga4-binding.json). Tests include wrong state,
redirect, PKCE and replay; role/tenant/MFA denial; exact scope; mismatched stream;
typed profile negatives; pagination/row bounds; verbatim sampling/threshold/other
coverage; token exclusion from rows/errors; CAS failure; provider reauthorization;
revocation during import; recovery changes; migration rollback; journal replay;
and unchanged GSC regression coverage. Labs clean up only their own containers.

## Owner Inputs for a First Live Run

1. A disposable Google Cloud project with Analytics Admin and Analytics Data APIs
   enabled, a Web application OAuth client, consent/test-user configuration, and
   the exact HTTPS dashboard `/auth/ga4/callback` redirect. Public onboarding also
   needs Google's applicable OAuth verification; this is not locally qualified.
2. A disposable GA4 property readable by that Google user, with a web stream whose
   URL has the site's exact current verified origin, plus approved report dates.
   No property or tracking setup is changed by Signal.
3. Store the client ID and client secret privately in `signal-ga4/oauth-client`;
   provide a least-privilege OpenBao credential for reading that path and KV CAS/
   deletion of verifier/refresh paths, with no GSC or unrelated-mount access.
4. Current owner MFA identity/session composition and independent recovery authority
   and restriction-journal custody/dispatcher. Provision admitted connector contexts
   with current robots evidence for `https://oauth2.googleapis.com`,
   `https://analyticsadmin.googleapis.com`, and `https://analyticsdata.googleapis.com`.
   All transport must remain inside the shared egress gateway.
5. Inject `ComposedGa4Gateway` into `create_app(browser_ga4=...)` using the identity
   connection factory, `OpenBaoGa4Secrets`, recovery-authority reader, exact redirect,
   and per-site shared-egress factory. Configure the dashboard's trusted API base
   and HTTPS origin privately. This dependency injection is not production wiring.
6. Independently qualify actual consent, origin refusal, bounded import coverage,
   refresh/revocation, restore and browser desktop/mobile journeys before enabling
   customer use. No credentials are required for the synthetic local gates.

## Merge-Train Verification

PR #17 is rebased onto the preceding accepted train head. Migration 0067
follows unchanged 0066; the cumulative app-table assertion is 121.
All five requested fast checks passed. The original qualification above is
historical; current counts and commands are in the `merge_train` entry of
[the evidence](../evidence/0097-ga4-binding.json). No production authority changed.
