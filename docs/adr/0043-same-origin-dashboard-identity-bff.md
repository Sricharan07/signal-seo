# ADR-0043: Terminate Browser Identity At A Same-Origin Dashboard BFF

- Status: Accepted
- Date: 2026-09-09
- Scope: Dashboard existing-user identity transition

## Context

The API already has purpose-bound OIDC initiation/callback, a pre-tenant
organization directory, tenant-session selection, current-session inspection,
and audited logout. The dashboard could display current authority but had no safe
way for a browser to invoke those contracts. Sending browser cookies, CSRF proofs,
API origins, or identity-provider details through client JavaScript would expand
the credential and network boundary. Blindly relaying API redirects or
`Set-Cookie` headers would let a compromised/misconfigured upstream change browser
authority or redirect an owner to an untrusted destination.

Logout also needs a CSRF token bound to whichever session level it will revoke.
The existing identity-only CSRF read cannot authorize logout after a tenant
session exists, because logout deliberately gives the tenant session precedence.

## Decision

Next.js route handlers are the browser-facing backend-for-frontend (BFF) for
existing-user identity. The browser submits ordinary forms only to the dashboard
origin. Login, organization selection, logout, and explicit local cleanup require
`POST`, one exact configured `Origin`, and `Sec-Fetch-Site: same-origin`. Production
requires an explicit HTTPS dashboard origin; loopback HTTP is development-only.

The BFF forwards only the credential required by each API operation:

- login start sends no browser cookie and accepts only an exact configured HTTPS
  identity-provider redirect;
- callback sends the one exact OIDC binding cookie and accepts only the fixed
  local `/?auth=identity-ready` return;
- organization listing and selection send one exact pre-tenant identity cookie;
- logout sends at most the exact tenant and identity cookies, with tenant
  precedence matching the API; and
- explicit cleanup performs no API call and deletes only Signal's four host-only
  browser cookies while making no server-revocation claim.

All API calls are no-store, use manual redirects where relevant, and have a
2.5-second timeout. Request inputs, response bodies, field sets, counts, UUIDs,
roles, authentication levels, expiries, authorization codes, and opaque tokens
are bounded and validated. The BFF accepts only the four known Signal cookie
names with exact `Secure`, `HttpOnly`, `Path=/`, `SameSite=Lax`, `Max-Age`, token,
and lifecycle rules. Successful operations require the complete expected cookie
set. Error responses may relay only validated cookie deletions, never new
authority. Notices come from a closed local key set rather than upstream text or
query reflection.

FastAPI adds `GET /v1/session/logout-csrf`. It selects the same strongest exact
cookie as logout and derives the HMAC proof from that token. No database or
provider operation is performed by this read.

## Consequences

- Provider tokens and Signal opaque credentials do not enter React props, client
  state, browser-visible API configuration, or dashboard persistence.
- Browser-to-API CORS is unnecessary; the existing API can remain private to the
  dashboard server tier.
- A valid pre-tenant identity displays current organization choices, but a posted
  tenant UUID is only a request. The API rechecks current membership before
  issuing tenant authority.
- Upstream redirect/cookie contract drift fails closed and is shown as an
  unavailable/rejected state rather than being passed through.
- The same-origin form proof protects BFF mutations; the BFF then obtains and
  presents the API's identity/session-bound CSRF proof over its private hop.
- Local cleanup is deliberately weaker than logout and is labeled accordingly.
  It recovers a browser from malformed/stale state but cannot claim server
  revocation or act as undo.
- The default API identity gateway and provider origin remain unconfigured, so
  the visible Sign in command safely returns a not-ready notice in local default
  operation.

## Alternatives Rejected

- **Call FastAPI directly from client JavaScript:** exposes the API origin and
  broadens CORS, cookie, error, and credential handling into the browser.
- **Use an authentication library as a second session authority:** duplicates the
  already implemented hash-only Signal session lifecycle and risks divergent
  revocation and tenant semantics.
- **Relay arbitrary Location and Set-Cookie headers:** turns the BFF into an open
  redirect and ambient cookie-writing proxy.
- **Treat local cookie deletion as logout or undo:** falsely claims server-side
  revocation and confuses credential cleanup with reversal of an external change.
- **Choose the first organization automatically:** hides tenant selection and can
  establish the wrong authority when memberships change or multiply.
- **Derive logout CSRF from only the identity cookie:** does not prove possession
  of the tenant session that logout selects when both cookies exist.

## Verification

Dashboard unit tests cover valid login/callback/selection/logout relays, exact
cookie minimization, same-origin proof, redirect allowlists, fixed returns,
malformed and duplicate input, bounded schemas, expiry limits, error-cookie
deletions, unsafe cookie issuance, unavailable dependencies, organization states,
and rendered identity commands/notices. API tests cover tenant precedence,
pre-tenant fallback, malformed/duplicate/missing cookies, and unconfigured browser
security. Repository tests pin the server-only route topology and prohibit
`NEXT_PUBLIC_` identity configuration.

The unchanged Keycloak adapter is rerun against its disposable real provider. The
new BFF is HTTP-contract tested but not claimed as jointly qualified against a
deployed Keycloak/OpenBao/PostgreSQL/API stack; that remains a release gate.
