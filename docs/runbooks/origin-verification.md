# Origin Verification Runbook

## Purpose

Operate and diagnose the exact public-origin proof without broadening a successful
result into crawl, connector, repository, or production-write authority.

## Expected Journey

1. An existing organization owner has one selected `onboarding`/`unverified` site
   whose origin is a canonical HTTPS DNS origin.
2. The selected-site dashboard control issues a challenge through the same-origin
   BFF. Signal returns one fixed proof URL, exact plaintext body, and 30-minute
   expiry; the browser retains it only until reload.
3. The owner publishes that body exactly, including the final newline, with a
   `text/plain` response at the exact URL.
4. Verification prepares under current authority, closes the database transaction,
   performs one pinned public-network request with redirects disabled, then opens a
   new transaction and rechecks authority.
5. Exact proof records a global protected claim and changes the site to
   `active`/`verified`. The dashboard reloads the server-owned directory. Any other
   result remains explicit and unverified.

The control appears only for a server-projected owner with the selected site in
`unverified` or `reverification_required` state. Its absence for a viewer is
expected and must not be bypassed with a direct route call; the API independently
rechecks live authority.

## State Interpretation

| State | Meaning | Operator action |
| --- | --- | --- |
| Challenge issued | A current 30-minute proof exists; ownership is not verified | Publish the exact body at the exact URL |
| Proof not found | The exact resource returned 404 | Check deployment path and retry with a new idempotency key |
| Proof mismatch | The response body or digest was not exact | Remove wrappers/whitespace changes and issue a new verification request |
| Proof rejected | Redirect, media type, framing, size, address, or response policy failed | Correct the origin response; do not relax the boundary |
| Proof unavailable | Resolution, connection, TLS, or timeout failed | Restore public reachability, then retry; do not infer success |
| Claim conflict | Another protected claim already owns the exact origin | Escalate for reviewed claim recovery; no tenant identity is disclosed |
| Verified | Exact proof committed with one permitted origin and recheck time | Continue only to separately authorized connector/read-only preflight work |
| Reverification required | Intended stale/revoked state; automatic transition is not implemented | Do not authorize production work; complete the future reviewed recheck flow |

## Evidence Diagnosis

Use a reviewed administrative session. Query only the exact tenant/site and never
log session cookies or proof response bodies. Inspect, in order:

1. `app.sites` state, ownership status, origin, and row version;
2. `app.site_origin_challenges` request identity, digest, issue time, and expiry;
3. `app.site_origin_verification_attempts` outcome and sanitized HTTP observation;
4. `app.site_origin_verifications` method, resource, permitted origins, revocation
   conditions, verification time, and recheck time; and
5. `control.public_origin_claims` only through privileged incident procedures.

A failed attempt must exist even though the API returns a typed failure. A success
must have one matching attempt and verification, the exact global claim, and a
verified site. Any partial combination is an integrity incident. Do not manufacture
or edit missing evidence.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api/test_origin_verification.py \
  tests/api/test_authentication.py tests/tooling/test_origin_verification_policy.py \
  tests/tooling/test_crawl_http.py -q
.venv/bin/python scripts/run-crawler-network-tests.py
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
```

The database lab uses disposable loopback PostgreSQL. The network lab uses a
disposable Docker internal network, a numeric peer, and synthetic plaintext proof.
Both runners verify invocation-owned cleanup.

For dashboard failures, first confirm exact `SIGNAL_DASHBOARD_ORIGIN`, one tenant
cookie, and the expected same-origin request headers. A 403 is a rejected browser
or owner-authority request, 409 is changed challenge/proof state, 503 is an
unconfigured or unavailable API/resolver path, and 500 is an invalid internal
response. Do not surface raw provider bodies or add a browser-to-API exception.

## Recovery

Reuse the same idempotency key only for an exact retry after ambiguous client
delivery. A failed verification outcome is final for that key; retry with a new
UUIDv4 request identity while the challenge remains current. After challenge
expiry, issue a new challenge. Never extend expiry, delete attempts, overwrite a
digest, change the site origin, or release/reassign a global claim manually.

The current slice records 30-day recheck and revocation conditions but has no
automatic stale transition or claim-release operation. Treat overdue, changed, or
disputed proof as a production no-go and use a separately reviewed future recovery
procedure. Migration `0028` is forward-only; failed application remains at `0027`
without partial tables, functions, claim state, or relaxed ownership constraint.

## Production No-Go Boundary

Do not enable these routes for customers until production identity, a controlled
resolver/egress path, abuse limits, monitoring, retention, restore, automatic stale
proof enforcement, and the fresh-browser dashboard journey are qualified. A
verified origin alone must never enable crawling, GSC/GitHub/Telegram, model tools,
approval, merge, deployment, CMS writes, or undo.
