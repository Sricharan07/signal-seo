# Slice 0069: GSC Binding and Import Generations

Status: **INTERNAL BOUNDARY IMPLEMENTED AND LOCALLY TESTED; OWNER UI AND LIVE GOOGLE
QUALIFICATION NOT EXECUTED**. Security-critical R1 slice under Revision 4.0
sections 4, 11, 12 and Revision 3.2 sections 10.1-10.2, 12.1, 12.4, INV-018.

## Implemented

- Fixed-scope Google web OAuth URL with offline access, S256 PKCE, exact redirect,
  non-incremental consent, ten-minute hashed one-use state, and current owner plus
  verified-origin checks in PostgreSQL.
- OpenBao KV v2 stores the client secret, verifier, and refresh token. Runtime records
  contain only `secret://gsc/<attempt>`; the verifier is permanently consumed and
  the refresh path permanently deleted on disconnect. Refreshes serialize per
  binding and persist a rotated token with OpenBao CAS before continuing. All
  external Google calls use
  the shared egress gateway. Form bodies are marked credentialed even without an
  Authorization header.
- Discovery preserves exact Google `siteUrl`, property type, permission, and
  eligibility. Owner confirmation rechecks the current verified origin and a
  discovered eligible candidate; wrong resources and stale attempts fail closed.
- Append-only binding events make disconnect and provider-driven reauthorization
  restrictions durable. Active projection requires current origin verification and
  no restriction event. Owner disconnect denies use before attempting upstream
  revocation and destroying local refresh-token access.
- Bounded Search Analytics query (at most 92 days, 5,000 top rows) records request
  dimensions, search type, dates, aggregation, pagination, response digest, shared
  egress receipt, source timezone, first incomplete date, and `complete=false` with
  `missing_data=unknown_not_zero`. Empty rows are not treated as zero demand.
- Migration `0045` follows candidate builds (`0043`) and technical SEO recipes
  (`0044`) without changing any earlier revision.
  ADR-0083 fixes closed, origin-admitted egress profiles for crawl, GitHub, Google
  OAuth, GSC, and model calls. Headers and media types are profile-owned, and the
  immutable operation and completion carry the profile identity.

## Boundaries and Limits

This is an internal service/SQL boundary, not a dashboard connector or R1 release
certificate. The dashboard stays visibly unavailable. There is no Google account
identifier in the read-only OAuth grant; the exact selected property is shown
instead of inventing an account identity. There are no customer credentials in this
repository. OAuth consent, authorized Google success, and production Google app
verification are `NOT_EXECUTED`. An unpersistable refresh-token rotation restricts
the binding to reauthorization rather than silently continuing. There is no
background import schedule or automatic recent-window reimport yet. A 5,000-row
response is explicitly top-row/partial evidence, not complete demand coverage.

## Verification

Run from the worktree with Docker available:

```sh
.venv/bin/pytest -q tests/connectors/test_gsc_properties.py tests/connectors/test_gsc_oauth.py tests/connectors/test_gsc_secrets.py tests/tooling/test_gsc_qualification.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/pip check
npm test
git diff --check
.venv/bin/python scripts/check-gsc-provider-boundary.py
gitleaks git --log-opts="5cf0b02..HEAD" .
```

Executed counts and explicit unexecuted live checks are in
[0069 evidence](../evidence/0069-gsc-binding.json). The existing slice-0050
direct-network negative script now fails closed without a shared-egress context;
it is not proof of authorized provider success.

## Owner Inputs for Live Qualification

Create one Google Cloud project with Search Console API enabled and an OAuth
consent screen; create a **Web application** OAuth client with an exact HTTPS
callback URI, and supply its client ID and client secret to the `signal-gsc`
OpenBao mount's `oauth-client` path. A Google account must have verified access
to the exact Search Console URL-prefix or Domain property for the site. Authorize
only `https://www.googleapis.com/auth/webmasters.readonly`; no write scope is
accepted. For public managed-SaaS onboarding, complete Google's OAuth app
verification. No credential is needed to run the local synthetic checks.

Once the owner has an actual refresh token obtained through the fixed consent
flow, prepare a running, synthetic shared-egress context with current robots
evidence for exactly `https://oauth2.googleapis.com` and
`https://www.googleapis.com`. Set `SIGNAL_GSC_EGRESS_CONTEXT` to its private
context JSON, the admission/ingest role DSNs in
`SIGNAL_GSC_EGRESS_ADMISSION_DSN` and `SIGNAL_GSC_EGRESS_INGEST_DSN`, plus
`SIGNAL_GSC_QUALIFY_ORIGIN` and the exact `SIGNAL_GSC_QUALIFY_PROPERTY`. Then run:

```sh
.venv/bin/python scripts/qualify_gsc_live.py
```

It prompts silently for client ID, client secret, and refresh token, uses shared
egress only, prints counts and coverage state only, and writes no customer data or
credential. It is not a substitute for the owner browser journey or production
OAuth app verification. This command has not been run with a real account.
