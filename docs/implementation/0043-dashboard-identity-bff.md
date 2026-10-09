# Slice 0043: Dashboard Identity BFF

- Status: Implemented and locally qualified; production identity composition unavailable
- Date: 2026-09-09
- Milestone: M1/M2 partial
- Specification: Revision 3.2 sections 2 INV-002/INV-003, 3.1, 8.1, 8.2,
  8.4, 24.1, 24.2, 24.4, and 24.5
- Decision: [ADR-0043](../adr/0043-same-origin-dashboard-identity-bff.md)
- Runbook: [Dashboard identity](../runbooks/dashboard-identity.md)

## Scope

This slice connects the owner-facing dashboard to the existing unconfigured
identity HTTP contracts through a same-origin Next.js BFF. An owner can now start
existing-user login, complete its callback, see current organization memberships,
select one membership, log out at either browser-session level, or explicitly
clear stale local browser state. The overview presents each transition and failure
without exposing bearer values or raw provider/API errors.

The default process still cannot complete a customer login because its
credential-bearing API identity gateway and provider origin are not configured.
This slice does not add invitation UI, account creation/recovery, active-site
selection, site onboarding, connectors, commands, approvals, chat, undo, or
production authority.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Logout CSRF API | `GET /v1/session/logout-csrf` binds a proof to the tenant cookie first, then the pre-tenant identity cookie, exactly matching logout precedence |
| Login BFF | Same-origin POST calls the API without browser credentials, requires an exact HTTPS provider origin and complete OIDC binding cookie transition, then redirects the browser |
| Callback BFF | Bounded state/code plus one exact binding cookie reach the API; only the fixed local destination and regular-login cookie transition are relayed |
| Organization reader | One exact identity token is forwarded server-side to `/v1/organizations`; response size, fields, count, UUIDs, names, roles, and duplicates are checked |
| Organization selection | Same-origin form input is bounded to one tenant UUID; BFF obtains identity-bound CSRF and API rechecks current membership before tenant-cookie issuance |
| Logout BFF | Same-origin form obtains logout CSRF for the strongest current session, requests audited server revocation, and requires all four browser credentials to clear |
| Local cleanup | A separately labeled route clears the four Signal cookies without claiming server revocation |
| UI | Top-bar Sign in/Choose organization/Logout/Clear state controls, flat organization rows, and closed success/rejection/unavailable notices render from server state |

## Trust And Data Flow

```text
Browser form/callback
  -> same-origin Next.js route
  -> exact cookie/input selection and bounded private API request
  -> existing FastAPI identity/session authority
  -> strict redirect/body/Set-Cookie validation
  -> closed local notice and server-rendered authority read
```

The dashboard stores nothing. OIDC state, authorization code, browser binding,
identity token, tenant token, and CSRF proof exist only for the bounded request or
host-only cookie lifecycle. Provider redirect details are not rendered. The
organization response is transient, validated, capped at 100, and never treated
as site or mutation authority. Tenant issuance remains authoritative only after
the API's server-side membership recheck.

Error handling is intentionally asymmetric: a successful operation must return
every exact expected browser-cookie mutation; an error may return no cookies or a
valid deletion-only subset. Any unknown cookie, duplicate, missing success cookie,
new authority on error, unsafe attribute, excessive lifetime, oversized body,
unexpected field, unsafe redirect, transport failure, or timeout fails closed.

## Verification

Run:

```sh
.venv/bin/python -m pytest tests/api/test_browser_security.py \
  tests/api/test_session_lifecycle.py -q
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/keycloak_lab.py
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm run check:docs
```

The focused API suite passes 65 cases; the complete API suite passes 192; and the
API/identity/tooling regression passes 645. The disposable PostgreSQL 17.11 suite
remains green at 492 cases with confirmed cleanup. Forty-eight dashboard tests
cover positive, negative, and dependency-failure behavior for every new boundary,
and 20 repository tests pin the source topology. The optimized build emits all
five dynamic auth routes. Desktop and 390-pixel browser checks cover the
signed-out command and safe callback failure with no horizontal overflow. Five
real Keycloak cases qualify the unchanged OIDC protocol adapter, not a complete
deployed BFF identity journey.

- [0043 dashboard identity qualification](../evidence/0043-dashboard-identity-bff.json)

## Explicit Limits

- `SIGNAL_IDENTITY_PROVIDER_ORIGIN` is absent by default and is only a redirect
  allowlist. It does not configure FastAPI's credential-bearing gateway.
- Existing-user login only is surfaced. Invitation acceptance and account
  recovery remain inaccessible from the dashboard.
- Provider RP-initiated logout remains undecided; Signal revokes its own parent
  identity and child tenant sessions only.
- Local cleanup cannot revoke server state and is not an undo mechanism.
- No complete Keycloak/OpenBao/PostgreSQL/FastAPI/Next.js browser journey has been
  qualified in a deployable environment. Production customer authentication
  remains disabled.
- Organization selection creates tenant scope, not site selection, ownership,
  connector health, command authority, or external-write authority.

## Next Safe Dependency

Implement explicit server-owned active-site selection and the first bounded site
onboarding transition. Keep origin ownership, GSC, GitHub, and Telegram binding
separate so each can be qualified against its real provider before evidence or
work execution is enabled.
