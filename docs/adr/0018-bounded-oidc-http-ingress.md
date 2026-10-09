# ADR-0018: Expose OIDC Login Through A Bounded HTTP Ingress

- Status: Accepted
- Date: 2026-09-08
- Owners: API and identity

## Context

Slices 0008 through 0015 implemented durable OIDC attempts, strict Keycloak
protocol validation, one-time PKCE secrets, recovery-generation checks, hash-only
identity sessions, and login audit events. Those pieces had no browser-reachable
route. Exposing them requires cookie and redirect behavior that does not weaken
the internal proof boundaries or accidentally claim tenant authorization.

OIDC callbacks necessarily carry an authorization code and state in the query.
Ambiguous query or cookie values must not be accepted, and those values must not
be retained by the application server's access log. A reverse proxy can still log
the incoming URL before ASGI sees it and therefore remains a separate deployment
gate.

## Decision

Add `GET /v1/session/login` and `GET /v1/session/callback`. Both routes remain
unconfigured in the default process and fail with a stable `503` until a reviewed
`BrowserLoginGateway` is injected. The production-shaped implementation acquires
one fresh autocommit identity connection per operation and composes the existing
OIDC, OpenBao, recovery-authority, and session-issuance services.

Login initiation accepts only one bounded local absolute return path. It redirects
only after provider discovery, authorization-request construction, PKCE storage,
and hash-only attempt persistence succeed. The browser receives a random
`__Host-signal_oidc_binding` cookie with `Secure`, `HttpOnly`, `SameSite=Lax`,
`Path=/`, no domain, and the same bounded lifetime as the attempt.

The callback requires exactly one state, code, and binding cookie. Before routing,
middleware saves parsed callback inputs in request-local memory and removes the
raw query from the ASGI scope so the application-server access logger cannot emit
it. Successful completion clears the binding and sets a separate
`__Host-signal_identity` cookie bounded by the server-side identity-session expiry.
This pre-tenant cookie grants no tenant, site, connector, or write authority.

Retryable pre-consumption configuration and recovery-authority failures preserve
the binding. Terminal, malformed, and post-consumption failures clear it. Public
errors collapse internal stage names and never include provider responses,
credentials, database details, or supplied redirect values.

## Alternatives

- Put state or the PKCE verifier in a browser-readable cookie. Rejected because
  durable hash-only state and OpenBao already provide narrower proof ownership.
- Set the tenant session cookie directly in the callback. Rejected because a user
  may belong to multiple organizations and explicit current tenant selection is a
  separate authorization step.
- Return the identity token in JSON or a URL fragment. Rejected because the
  browser should receive only an opaque HttpOnly cookie.
- Trust scalar framework parsing for repeated security parameters. Rejected
  because duplicate state, code, or cookie values must fail rather than select an
  arbitrary occurrence.
- Rely only on documentation to prevent callback access logging. Rejected for the
  application server; the ASGI scope is redacted in code. Upstream ingress logging
  still requires independent deployment verification.

## Consequences

- A configured process can authenticate an already provisioned user into a
  pre-tenant identity session through the browser.
- The default process still has no credentials and cannot authenticate a customer.
- This decision does not add tenant selection, membership listing, session
  inspection, or logout. Slices 0019 and 0020 later add those internal contracts;
  invitation acceptance over HTTP and abuse controls remain absent.
- Reverse proxies, load balancers, and observability agents must prove callback
  query redaction before this route can be admitted to production.
- Provider redirects are trusted only because the composed gateway uses the
  previously qualified exact-endpoint Keycloak adapter.

## Verification

Twenty-one new API cases cover disabled configuration, exact redirect/cookie flags,
local return paths, duplicate proofs, callback success, retryability mapping,
unsafe gateway output, query redaction, clean connection ownership, and secret-safe
failures. The API suite passes 57 cases and the complete non-database Python suite
passes 222 cases. The unchanged real PostgreSQL suite remains at 217 cases.
