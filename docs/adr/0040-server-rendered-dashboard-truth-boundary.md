# ADR-0040: Render Dashboard Truth Through a Server-Only Read Boundary

- Status: Accepted
- Date: 2026-09-09
- Scope: Initial owner-facing dashboard shell

## Context

Signal had a public readiness endpoint and a versioned capability inventory but no
owner-facing product surface. Building disconnected mock screens would make visual
progress while violating the product rule against fabricated state. Calling the
API directly from browser JavaScript would expose deployment topology, create a
second cross-origin trust boundary, and make it easier to mix future provider or
session credentials into client state.

The current API is intentionally unconfigured for customer identity and reports
its real dependency state as not ready by default. The first dashboard slice must
show that incomplete condition rather than bypassing it, and it must not imply
that site onboarding, chat, approvals, connectors, analytics, or external writes
exist.

## Decision

Implement the Overview route as a dynamic Next.js server component. A narrow
server-only client reads `GET /health/ready` and `GET /v1/capabilities` from one
operator-configured trusted origin on every render with no cache, rejected
redirects, a 2.5-second request timeout, and strict response bounds.

Validate the capability document against its exact version-one top-level and entry
schema, unique bounded keys, closed availability values, a maximum of 128 entries,
and 64 KiB total body size. Invalid, unavailable, oversized, or unsafe
configuration returns a bounded display projection with production writes off.
Raw response bodies and transport errors never reach the UI.

Render only verified readiness and capabilities as live data. Keep all other
destinations and controls visibly disabled with explicit copy. Apply production
response security headers and omit React's development-only `unsafe-eval`
permission outside development. Keep the shell independent of future mutable
draft state and authenticated authority.

## Consequences

- Users can see a real, responsive Signal surface now without invented work,
  analytics, approvals, or provider health.
- Browser JavaScript receives rendered status, not the API origin, provider
  credentials, raw errors, or an authority token.
- Malformed or unreachable API behavior fails closed and remains understandable.
- The public capability endpoint remains a deployment-health contract, not a
  customer-data or authorization channel.
- Dynamic rendering adds one bounded API read to page delivery and intentionally
  avoids stale cross-tenant caches before tenant scope exists.
- Inline Next.js runtime scripts remain allowed by the initial CSP. Replacing that
  allowance with a production nonce contract is deferred to the authenticated BFF
  deployment slice.
- This decision grants no customer session, site scope, connector, approval,
  mutation, undo, or production-write authority.

## Alternatives Rejected

- **Static demo data:** creates visible progress by fabricating product state and
  cannot be used as operational truth.
- **Direct browser-to-API fetches:** expose internal topology and introduce CORS,
  client-cache, and future credential-handling risk without a product benefit.
- **Enable all navigation with placeholder pages:** presents nonfunctional controls
  as capabilities and weakens user trust.
- **Build authentication and onboarding in the same slice:** expands the trust and
  failure surface beyond one independently testable vertical increment.

## Verification

Nine dashboard tests cover valid, not-ready, malformed, duplicate, oversized,
unreachable, and unsafe-configuration API behavior, honest rendered states, and
environment-specific security headers. Nineteen repository tests enforce pinned
dependencies, root quality-gate inclusion, server-only reads, response bounds,
disabled product boundaries, and security headers. Production build and manual
responsive checks cover desktop, 390-pixel mobile, navigation disclosure, connected
not-ready state, and API-unavailable state.
