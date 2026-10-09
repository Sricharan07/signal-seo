# Site Onboarding Runbook

## Purpose

Operate and diagnose owner-controlled site creation without treating submitted
metadata, a listed origin, or the resulting site membership as ownership proof or
external-write authority.

## Expected Journey

1. Existing-user login and organization selection establish one exact tenant
   session. The current server projection includes the owner role and monotonic
   session version.
2. The owner opens Add site in Authorized sites and submits one name, canonical
   HTTPS DNS origin, IANA timezone, and reporting currency.
3. The same-origin dashboard route validates the bounded form, forwards only the
   exact tenant cookie, obtains tenant-bound CSRF, and calls `POST /v1/sites`.
4. PostgreSQL locks the exact session and owner/tenant authority, checks current
   origin uniqueness and the 100-site bound, then atomically creates, grants,
   selects, and audits the unverified site.
5. The dashboard redirects to a fresh server render. The new row is current and
   explicitly reads Ownership unverified.

## State Interpretation

| Visible state | Meaning | Operator action |
| --- | --- | --- |
| Site added | Atomic creation committed and the exact session selected the new unverified site | Continue only to the later ownership-verification step |
| Site setup changed | The session version changed or the request identity/origin conflicts | Refresh and inspect the current directory before retrying |
| Site setup rejected | Browser proof, metadata, session, owner authority, or site limit was rejected | Correct the request or authority; do not bypass the API |
| Site setup unavailable | API/database composition failed or returned invalid evidence | Restore the private dependency path and reconcile before retrying |
| Ownership unverified | No independent control proof exists | Do not crawl, connect, or act on this origin as owner-controlled |

## Database Diagnosis

Use only a reviewed administrative session. Never query or log raw session
cookies; only SHA-256 token hashes persist. For one tenant and onboarding request,
inspect the current session version and active site, owner membership state/epoch,
`app.sites`, `app.site_memberships`, ordered
`app.session_site_context_events`, and `app.site_onboarding_events`.

One first-time success must produce exactly one site, owner site membership,
context event, and onboarding event, while incrementing the session version once.
The private `control.tenant_site_routes` row must match the site. An exact replay
returns the same site and original version without adding rows. Any partial set is
an integrity incident; do not synthesize missing rows manually.

The onboarding event request hash binds name, origin, timezone, and currency. Its
event hash additionally binds actor/session/authority/generation metadata. The
context event must continue the exact session's previous hash chain. These hashes
are tamper-evident records, not digital signatures or customer-facing proofs.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api/test_session_lifecycle.py \
  tests/api/test_authentication.py -q
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
```

The database command provisions a disposable loopback-only PostgreSQL 17.11
container, applies every migration with the non-bypass migrator, exercises the
dedicated runtime roles, and confirms cleanup.

## Recovery

On a stale version, refresh; never decrement or overwrite `session_version`. On an
exact retry after transport ambiguity, reuse the same idempotency key and identical
metadata. A different payload under that key must remain a conflict. On a UUID
allocation collision, the application retries only after the whole transaction
rolls back.

Site creation currently has no delete or archive operation. Do not delete the site,
membership, context event, onboarding event, or route row by hand. A mistaken site
requires a future reviewed lifecycle transition. Selecting another authorized site
changes navigation but does not undo creation. No external provider effect exists
to compensate in this slice.

Migration `0027` is forward-only. It temporarily relaxes forced RLS only inside
the migrator transaction to backfill the private route mirror, then restores it
before commit. A failed application leaves the database at `0026`. Destructive
downgrade and historical-origin rewriting are prohibited recovery methods.

## Production No-Go Boundary

Do not enable customer onboarding until the combined identity, recovery,
PostgreSQL, API, dashboard, abuse-control, monitoring, retention, restore, and
fresh-browser path is qualified. Do not treat onboarding as origin ownership,
crawl authorization, connector access, approval, undo, or production-write
authority.
