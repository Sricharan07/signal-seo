# ADR-0006: Establish a Truthful Read-Only API Boundary First

Status: Accepted. Date: 2026-09-07.

## Context

Signal has a tested database command and authorization foundation, but it does not
yet have Keycloak login, browser sessions, CSRF protection, or an end-to-end
human-attributed command boundary. Exposing a state-changing route before those
controls exist would turn an internal persistence primitive into misleading or
unsafe product authority.

The service still needs a runnable HTTP boundary for deployment composition,
health checks, stable contracts, and later API integration. That boundary must
state what is and is not available without implying that a live process is ready
to accept customer work.

## Decision

Add a FastAPI application that exposes only process liveness, dependency
readiness, and a machine-readable capability inventory. The application has no
state-changing route, customer data route, authentication flow, or connector
authority.

Liveness reports only that the process can answer. Readiness is driven by an
injected asynchronous probe and fails with `503` by default, so an uncomposed
service cannot claim its dependencies are ready. Capabilities distinguish
`internal_only` database foundations from customer-facing features that remain
`disabled`; production writes are always reported as disabled in this slice.

Apply a bounded correlation identifier, stable error envelope, generic exception
message, no-store policy, and restrictive response security headers to every
response. Do not log exception objects, request paths, headers, bodies, or stack
traces in the catch-all path. Interactive API documentation is disabled by default
and rejected by configuration in production.

Use the ASGI application factory as the composition boundary. Tests inject probes
and use HTTPX's ASGI transport instead of starting a socket or using FastAPI's
deprecated synchronous test client. Add the API and its tests to the existing
least-privilege CI gates.

## Consequences

The repository now has a real, runnable HTTP process and versioned `/v1`
capability response, but it is intentionally not a customer command API. A `200`
from liveness is not dependency readiness, product availability, or release
approval. The default readiness response remains `503` until a later composition
slice supplies authoritative dependency checks.

The safe catch-all log provides a correlation ID and HTTP method but deliberately
omits diagnostic exception detail. A later observability boundary may record
redacted exception classes and traces in an access-controlled backend after the
redaction contract is designed and tested.

FastAPI, Uvicorn, HTTPX, and their transitive dependencies expand the supply-chain
surface. Versions are pinned and dependency consistency is checked, but Python
artifacts are not yet hash locked and no vulnerability or license report exists.
TLS termination, trusted proxy handling, authentication, CSRF, bounded request
bodies, rate limits, CORS, OpenTelemetry, and a real dependency probe remain
required before adding customer or state-changing endpoints.

## Alternatives

Exposing the existing command service immediately was rejected because a caller
could not yet be authenticated and attributed through the required browser/session
controls. Returning ready unconditionally was rejected because it would conceal
missing dependencies. Building the dashboard first was rejected because it would
have no truthful backend contract. A custom HTTP server was rejected in favor of
the specification's selected framework and typed contract ecosystem.

## Verification

Ten API contract tests cover liveness, fail-closed readiness, capability truth,
correlation-ID bounds, safe HTTP and validation errors, response and log redaction,
the GET-only OpenAPI surface, documentation restrictions, and strict environment
parsing. CI policy tests prove that API source and tests are included in lint,
format, and test commands. The complete 63-case PostgreSQL 17.11 suite was rerun
after the dependency-lock change; its source-hashed report records no production
authority and completed cleanup.
