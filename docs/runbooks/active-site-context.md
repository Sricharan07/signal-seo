# Active-Site Context Runbook

## Purpose

Operate and diagnose the tenant-session site selector without treating browser
state, a listed site, or a persisted UUID as authority.

## Expected Journey

1. Existing-user login and organization selection establish one exact tenant
   session. A new session has version 1 and no active site.
2. The server renders response schema version 2, including the current monotonic
   session version, alongside the independently validated authorized-site directory.
3. The owner posts one site UUID and the rendered session version to the
   same-origin `/auth/select-site` route.
4. The BFF obtains tenant-bound CSRF and calls `PUT /v1/session/site` with only the
   exact tenant cookie and bounded request.
5. PostgreSQL rechecks live authority, commits at most one version transition and
   one context event, then the BFF redirects to a fresh server render.

## State Interpretation

| Visible state | Meaning | Operator action |
| --- | --- | --- |
| Select a site | Tenant authority is current but no current site is selected | Choose one listed site; do not infer authority from directory order |
| Current | The current session projection and directory agree on this authorized site | Continue only with operations whose separate capability gate is enabled |
| Site selection conflict | Another request changed the context after this page rendered | Refresh and intentionally select again if still needed |
| Site selection rejected | Session, CSRF, origin proof, site grant, or authority is invalid | Reauthenticate or repair membership; never force the stored site UUID |
| Site selection unavailable | API composition or transport is not ready | Restore the private API/database path; do not add browser-to-database or client-side fallback |
| Account state rejected | Current session and site directory do not reconcile | Clear only local state when appropriate, then investigate current server authority |

## Database Diagnosis

Inspect only through a reviewed administrative session and explicit tenant/site
scope. Never log or query raw session tokens; the database stores their SHA-256
hashes only. Useful facts are the session ID, `active_site_id`, `session_version`,
revocation/expiry, current membership states and epochs, and ordered
`session_site_context_events` metadata.

One real transition must increment the session version by one and append exactly
one event with the same version. Reselecting the current site changes neither.
Concurrent requests with one expected version must produce one success and one
conflict. An event with a missing or incorrect previous hash is an integrity
incident; do not edit or delete audit rows.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api/test_session_lifecycle.py \
  tests/api/test_authentication.py -q
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
```

The PostgreSQL command provisions a disposable loopback-only PostgreSQL 17.11
container, applies all migrations with the non-bypass migrator, runs the authority
suite through dedicated roles, and confirms cleanup.

## Recovery

Site selection itself is reversed by selecting another currently authorized site.
Do not call that product undo: no external provider change occurred. On a stale
version, refresh rather than rewriting `session_version`. On revoked authority,
repair the authoritative membership or select another authorized site; never
patch `active_site_id` directly. A failed transaction must leave both session and
event unchanged, and event-ID collisions are retried only after full rollback.

Migration `0026` is forward-only. A failed application leaves the database at
`0025`. Destructive downgrade, audit deletion, manual version decrement, and
session-row replacement are prohibited recovery methods.

## Production No-Go Boundary

Do not enable customer identity or site-scoped operations until the combined
identity/database/API/dashboard deployment, monitoring, audit export/retention,
abuse controls, recovery, and fresh-browser journey are qualified. This context
adds no site onboarding, ownership verification, connector, approval, undo, or
production-write authority.
