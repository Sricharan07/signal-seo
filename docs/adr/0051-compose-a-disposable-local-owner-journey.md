# ADR-0051: Compose A Disposable Local Owner Journey

- Status: Accepted
- Date: 2026-09-12
- Scope: Local multi-service browser qualification

## Context

Signal had independently tested browser identity, tenant selection, site
onboarding, PostgreSQL authority, Keycloak OIDC, and OpenBao secret boundaries.
Those parts had never been run as one owner-visible browser journey. The gap hid
integration failures and made the implemented product difficult to evaluate.

A production composition is not yet available. Treating an unrestricted local
stack as production would create a second, weaker authority path. The useful next
step is therefore a deliberately disposable composition that proves the real
services work together without granting customer or external write authority.

## Decision

Provide one `npm run pilot` command that starts invocation-owned PostgreSQL,
OpenBao, and Keycloak containers, applies the real migrations, seeds one fixed
synthetic owner and empty organization, and runs the real FastAPI and Next.js
processes on loopback. Stopping the command destroys the provider containers and
database state.

Keep the production cookie contract unchanged. Because loopback HTTP cannot store
`Secure` host-only cookies, enable explicit `signal_local_*` names only when the
dashboard is in development and `SIGNAL_LOCAL_PILOT=1`. Adapt those names at the
loopback API edge only. Continue to use `__Host-*; Secure` everywhere else.

Permit an HTTP identity-provider redirect and `Origin: null` form submission only
when both the dashboard boundary and destination are exact loopback HTTP origins.
Add the exact identity-provider origin to `form-action`; reject malformed,
credential-bearing, non-loopback HTTP, and production-HTTP destinations. Request
fresh OIDC authentication with `max_age=0` and require Keycloak's `basic` client
scope so the signed ID token carries the `auth_time` required by session issuance.

This composition may create only disposable Signal records. It does not enable
origin verification against an uncontrolled public site, provider OAuth,
connectors, workflow execution, external writes, or production authority.

## Consequences

- A developer can prove sign-in, organization selection, and unverified site
  onboarding through the real browser and real persistence boundary with one
  command.
- Browser integration defects become visible before production deployment work.
- The local-cookie exception is explicit, narrowly named, and impossible to
  activate in a production build.
- The pilot requires Docker, Node.js 22, the Python environment, and free loopback
  ports 3000 and 8000.
- Data is intentionally lost on shutdown and cannot be mistaken for a customer
  environment or durability qualification.

## Alternatives Rejected

- **Continue testing each layer independently:** already missed cookie, CSP,
  redirect, and ID-token integration failures.
- **Run the dashboard with HTTPS locally:** adds certificate distribution and
  browser trust setup before it is needed for this disposable proof.
- **Remove `Secure` from production cookie names:** weakens the reviewed browser
  authority boundary for the convenience of local development.
- **Seed fake sites, metrics, connectors, or work:** makes the product look more
  complete while concealing missing evidence and authority.
- **Use a shared development database or identity service:** creates stale-state,
  cleanup, credential, and cross-project risks.

## Verification

Unit tests cover exact local/production cookie selection, loopback-only redirects,
origin proof, CSP form destinations, deterministic cookie deletion, OIDC request
shape, realm restrictions, and cleanup preconditions. The real
Keycloak lab requires a session-ready signed ID token. The real browser journey is
qualified against invocation-owned PostgreSQL, OpenBao, and Keycloak, and graceful
shutdown is checked for zero exit status, released ports, and removed containers.
