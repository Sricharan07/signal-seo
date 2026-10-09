# Dashboard Identity Runbook

## Purpose

Operate and diagnose the same-origin existing-user identity surface without
mistaking local browser cleanup, synthetic tests, or an unconfigured gateway for
production authentication.

## Required Configuration

1. Set `SIGNAL_API_BASE_URL` to the exact private FastAPI origin. Credentials,
   paths, queries, and fragments are rejected.
2. Set `SIGNAL_DASHBOARD_ORIGIN` to the exact public dashboard origin. Production
   requires HTTPS; loopback HTTP is accepted only outside production.
3. Set `SIGNAL_IDENTITY_PROVIDER_ORIGIN` to the exact HTTPS origin used by the
   reviewed Keycloak authorization endpoint. Do not include a path or trailing
   slash.
4. Configure FastAPI separately with its browser-security key/origin and reviewed
   OIDC/session gateway dependencies. Dashboard variables contain no API,
   Keycloak, OpenBao, database, or provider credential.

If any identity dependency is absent, leave customer access disabled. The correct
user-visible result is an unavailable notice with no new browser authority.

## Expected Journey

1. `POST /auth/login` validates same-origin browser proof and requests the API
   login start without forwarding ambient cookies.
2. The BFF accepts only the configured provider origin and the exact OIDC binding
   cookie transition before redirecting.
3. `GET /auth/callback` forwards one bounded state/code pair and only the binding
   cookie. Success returns to `/?auth=identity-ready` with a pre-tenant identity
   cookie.
4. The overview loads `/v1/organizations` server-side. The owner explicitly posts
   one membership to `/auth/select-organization`.
5. The BFF obtains identity-bound CSRF and the API rechecks current membership
   before issuing the tenant cookie.
6. `POST /auth/logout` obtains a proof for the tenant session when present,
   otherwise the identity session, and requires confirmed API revocation plus all
   four cookie deletions.

## Diagnosis

| Visible state | Meaning | Operator action |
| --- | --- | --- |
| Sign-in service unavailable | Provider allowlist, API gateway, browser security, or private API transport is not ready | Check server configuration and safe API logs by correlation ID; do not add client-side bypasses |
| Sign-in response rejected | Callback state, code, browser binding, provider response, redirect, or cookie contract failed validation | Start a fresh login; inspect sanitized API/BFF telemetry and identity audit events |
| Choose organization unavailable | Identity exists but membership service is unavailable or returned an invalid contract | Verify current recovery generation, identity session, membership state, database availability, and response contract |
| Organization selection rejected | Form proof, tenant UUID, identity CSRF, or current membership was rejected | Refresh membership state; never force a tenant ID into a session |
| Logout service unavailable | Server revocation was not confirmed | Preserve the warning; retry when API authority is healthy or use local cleanup only with its weaker semantics understood |
| Clear browser state | Local cookies are malformed, expired, or API recovery is unavailable | Clear only Signal cookies; record that server revocation remains unconfirmed |

Never log callback queries, cookie values, authorization codes, CSRF values,
provider bodies, or raw upstream errors. User-facing notices are a closed local
set and must remain free of upstream text.

## Verification

```sh
.venv/bin/python -m pytest tests/api/test_browser_security.py \
  tests/api/test_session_lifecycle.py -q
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
```

For provider regression, run `.venv/bin/python scripts/keycloak_lab.py`. It uses a
disposable loopback-only Keycloak container and synthetic identity, then confirms
cleanup. It does not qualify the deployed multi-service browser journey.

## Rollback And Recovery

Rolling back the dashboard removes the browser routes but does not restore or
revoke sessions already issued by FastAPI. Use the authoritative server logout
path when available. The local clear control only removes browser copies and must
not be recorded as revocation or undo. Do not clear unrelated cookies, delete
identity rows, rotate recovery authority, or invalidate customer sessions as a
UI troubleshooting shortcut.

## Production No-Go Boundary

Do not enable customer sign-in until the combined TLS/proxy/host-cookie behavior,
Keycloak/OpenBao/PostgreSQL/API composition, browser journey, audit trail, abuse
controls, recovery, monitoring, and fresh-browser release gate have been qualified
in the intended deployment environment.
