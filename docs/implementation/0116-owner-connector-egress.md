# Slice 0116: Owner Connector Egress

Classification: **security-critical owner authority, egress and external I/O**.
Status: **IMPLEMENTED; LOCAL REGRESSION QUALIFIED; NOT DEPLOYED**.
[ADR 0101](../adr/0101-current-owner-connector-egress.md), Revision 4.0 sections 4,
12 and 19 and Revision 3.2 sections 10 and 11 apply.

## Implemented

The existing provider boundary accepts a distinct current MFA-owner context for
connector onboarding. Migration `0061` records owner operations under forced RLS,
immutable identity and single terminal transitions. Runtime roles have only
function access. SQL resolves the actual selected-site owner, recovery generation
and verified public origin before dispatch, including session/origin expiry
rechecks after obtaining the global bucket lock.

Only existing GitHub read, Google OAuth/read-only Search Console and Slack profiles
are permitted, with closed endpoints and bounds. No crawl/workflow/session/site
or standing grant is invented. Model, repository-write and autonomous authority
remain separate and cannot use this context.

Each operation first admits an actual robots request into the existing global
origin bucket, then uses the existing public-address screening, numeric-peer TLS,
redirect rejection and RFC-aware parser. A fetched HTTP 200 plaintext object is
encrypted, read back and linked to the immutable receipt. Only proved allowed
rules or a real HTTP 404 admit the exact provider URL. Robots snapshots last at
most five minutes; request permits last at most 30 seconds and the owner session
deadline. Provider dispatch/completion use the same shared bucket and backoff.

Unknown dispatch is never repeated. Exact robots replay reuses recorded target
evidence, not synthetic retrieval. A terminal provider response is explicitly
body-unavailable on replay. SQL stores body digests, not credentials, OAuth codes
or request/response plaintext. Owner robot artifacts require a dedicated protected
backend root; the crawler orphan cleanup must not scan that separate root.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/ruff check services/control_plane/src/signal_core/owner_connector_egress.py services/control_plane/src/signal_core/shared_egress.py services/control_plane/src/signal_core/egress_profiles.py tests/control_plane/test_owner_connector_egress.py database/migrations/versions/0061_owner_connector_egress.py
npm test
```

All 856 real PostgreSQL cases passed, including 19 current-owner, closed-path,
malformed evidence, unknown dispatch and replay cases. The initial full run had
two test-fixture failures: the new table increased the expected forced-RLS count,
and a retry fixture needed to wait through existing transport backoff. Neither
repair weakened a safety gate. API/identity/tooling passed 1,366 cases; real pinned
network passed 14; repository and dashboard checks passed 38 and 148 cases,
TypeScript and the Next build. Changed Python files pass Ruff and the diff check.
[Evidence](../evidence/0116-owner-connector-egress.json) records completed results.

## Remaining Live Gates

This slice does not enable a public callback or a provider credential. The live
test API remains on the previously qualified `0115` image and database `0060`.
Actual connector composition needs private provider workload roles, protected
artifact key/storage, screened network admission, owner HTTP/BFF routes and real
provider/browser qualification. GitHub repository protection, Slack installation
and selected channel, and an eligible Search Console property are not simulated.
The owner's complete integration request remains in progress, not complete.
