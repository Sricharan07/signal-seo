# Slice 0018: Bounded OIDC HTTP Ingress

Status: **HTTP LOGIN CONTRACT IMPLEMENTED; DEFAULT PROCESS AND CUSTOMER ACCESS REMAIN DISABLED**.

## Outcome

The FastAPI application now exposes a narrow existing-user login start and
callback contract. When a reviewed gateway is injected, those routes compose the
already tested durable attempt, Keycloak, PKCE, recovery-authority, identity-session,
and audit boundaries. With the repository's default application configuration,
both routes fail closed because there are no credentials or connection factory.

| Route | Behavior |
| --- | --- |
| `GET /v1/session/login` | Validate one local return path, create durable login state, and redirect to the exact provider authorization endpoint |
| `GET /v1/session/callback` | Require exact state/code/browser binding, complete the composed login, and set a pre-tenant identity cookie |

The routes return `303` only after their required durable work succeeds. They
never place an application session, browser binding, provider token, or PKCE
verifier in response bodies or redirect locations.

## Cookie And Logging Contract

`__Host-signal_oidc_binding` binds the callback to the initiating browser and
expires with the 60-to-600-second login attempt. `__Host-signal_identity` contains
the random pre-tenant session token and cannot outlive its server-side session.
Both are `Secure`, `HttpOnly`, `SameSite=Lax`, host-only cookies with `Path=/`.

The callback rejects missing, malformed, or duplicate state, code, and binding
values before invoking the gateway. Middleware retains the parsed values only in
request-local state and removes the raw query from the ASGI scope before the
application server can produce its access-log line. Signal's own failure logger
records only method and correlation ID.

This does not control a reverse proxy or load balancer that observes the URL before
ASGI. Production admission requires a tested ingress rule that disables or redacts
callback query logging end to end.

## Dependency Boundary

`ComposedBrowserLogin` owns one fresh autocommit identity connection for each start
or callback and closes it after use. It supplies only validated OIDC registration,
narrow writer/consumer OpenBao clients, recovery authority, TLS transports, and
session policy to the existing core flow. Connection factories and transports are
excluded from representations, and nested OpenBao tokens were already
representation-hidden.

An unavailable or malformed gateway returns a stable `503`. Internal
`LoginFlowError` stages are collapsed into public start, rejected-callback, or
failed-callback responses. Retryable pre-consumption failures preserve the browser
binding; terminal failures clear it.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Twenty-one new API cases bring the API suite to 57 cases and the complete
non-database Python suite to 222. The unchanged real PostgreSQL 17.11 suite passes
217 cases. Repository and documentation checks pass 11 and 50 cases/files
respectively.

## Explicit Limits

- The default application does not construct `ComposedBrowserLogin`; no production
  OIDC, OpenBao, or PostgreSQL credential is configured.
- The callback issues only a pre-tenant identity session. There is no organization
  listing or tenant selection in this slice. Slice 0019 adds both internal HTTP
  contracts, and slice 0020 adds current-session inspection and audited Signal
  logout. Upstream provider logout remains absent.
- Unknown identities remain denied by ordinary login. Invitation acceptance has
  no HTTP journey and no invitation bearer token is accepted by these GET routes.
- No account registration, password recovery, MFA enrollment/recovery, email
  delivery, login throttling, lockout, or production Keycloak topology exists.
- Upstream ingress callback-query redaction has not been configured or tested.
- There is no dashboard, Telegram surface, customer data route, connector
  credential, or production write authority.

See [ADR-0018](../adr/0018-bounded-oidc-http-ingress.md) for the route, cookie,
connection, logging, and failure decisions.
