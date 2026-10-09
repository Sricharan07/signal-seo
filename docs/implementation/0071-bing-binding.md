# Slice 0071: Bing Webmaster Binding and Import

Status: **INTERNAL BOUNDARY IMPLEMENTED AND LOCALLY TESTED; OWNER UI AND LIVE BING
QUALIFICATION NOT EXECUTED**. Security-critical R1 slice under Revision 4.0
sections 4, 11, 12, EC-143 and Revision 3.2 section 12.

## Implemented

- Fixed `webmaster.read` OAuth request. An owner with a current selected site and
  verified exact origin creates a five-minute, hash-stored, one-use callback state.
  The callback consumes state before token exchange. Discovery preserves Bing's
  exact site URL and verification flag; only the owner can confirm the matching
  verified-origin site. An unverified, wrong-origin, stale, or replayed attempt
  cannot bind.
- The OAuth client secret and refresh token reside in `signal-bing` OpenBao KV v2;
  PostgreSQL stores only `secret://bing/<attempt>`. Refresh rotation uses a
  per-binding lock and OpenBao CAS before proceeding. Disconnect appends an
  immutable restriction before destroying the local token. Provider rejection,
  reduced scope when reported, or unpersistable rotation restricts further use.
- Token exchange, site discovery, performance, link counts, and first-page
  inbound-link details all go through the shared egress gateway. The external
  JSON/HTTP methods are `GetUserSites`, `GetRankAndTrafficStats`, `GetLinkCounts`,
  and `GetUrlLinks`; no submission or settings method is available. The token
  form and JSON API use separate origin-bound profiles from ADR-0083.
- Immutable import generations carry `source=bing_webmaster`, kind, exact site
  URL, bounded rows, response digest, egress receipt, and coverage. Every
  observation declares `complete=false` and `missing_data=unknown`. Counts
  identify inbound links to own-site target pages; detail import selects one
  exact same-origin target without query data and first result page. Bing and GSC remain separate
  source records, with no implicit reconciliation or zero-filled gaps.
- Migration `0046` follows the rebased GSC revision `0045`; migrations `0001` to
  `0042` remain unchanged.

## Boundaries and Limits

This is an internal service and SQL boundary, not a dashboard connector or R1
release certificate. There is no background schedule, pagination beyond page
zero for links, or provider-total completeness claim. Bing's performance endpoint
reports combined vertical traffic; this is not directly comparable to GSC web
search. An empty result is provider-returned emptiness, not verified zero demand
or zero inbound links. Bing's OAuth guide does not document PKCE, so no
unsupported parameter is sent; the one-use state, exact redirect, short expiry,
and consume-before-exchange rule protect the callback. The local disconnect
does not assert upstream token revocation, because no revocation endpoint is
documented for this API. The owner should also revoke app access in Bing.

No customer credentials exist in the repository. Authorized OAuth consent,
site discovery, performance, and inbound-link provider results are
`NOT_EXECUTED`. Unconfigured Bing is visibly unavailable through the existing
capability surface; production composition remains disabled.

## Verification

```sh
.venv/bin/pytest -q tests/connectors/test_bing_protocol.py tests/connectors/test_bing_secrets.py tests/tooling/test_bing_qualification.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/pip check
npm test
git diff --check
gitleaks git --log-opts="5cf0b02..HEAD" .
```

Executed counts and unexecuted live checks are in
[0071 evidence](../evidence/0071-bing-binding.json).

## Owner Inputs for Live Qualification

Create a **Bing Webmaster Tools account** with the exact site added and
verified. In Settings > API Access, register an **OAuth Client** with a fixed
HTTPS callback and obtain its client ID and client secret. Grant only
`webmaster.read`; do not create a manage-scope grant for this connector. Store
`client_id` and `client_secret` in the `signal-bing` OpenBao KV-v2
`oauth-client` path. The exact Bing site URL, including scheme, host, and
trailing-slash spelling, must identify the verified Signal origin exactly. An API
key is not needed.

After obtaining a refresh token through the fixed owner consent flow, prepare
the private shared-egress context with current robots evidence for exactly
`https://www.bing.com`. Set `SIGNAL_BING_EGRESS_CONTEXT` to its context JSON,
the admission/ingest role DSNs in `SIGNAL_BING_EGRESS_ADMISSION_DSN` and
`SIGNAL_BING_EGRESS_INGEST_DSN`, and the exact origin/site URL in
`SIGNAL_BING_QUALIFY_ORIGIN` and `SIGNAL_BING_QUALIFY_SITE_URL`. Then run:

```sh
.venv/bin/python scripts/qualify_bing_live.py
```

The command prompts silently for client ID, secret, and refresh token, sends
only read requests through shared egress, prints counts and incomplete coverage
only, and writes no customer data or credentials. It has not been run with a
real account. Microsoft's contracts are linked in [ADR-0081](../adr/0081-bing-read-only-source-evidence.md).
