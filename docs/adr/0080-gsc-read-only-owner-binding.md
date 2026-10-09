# ADR-0080: Read-Only Owner Binding for Search Console

Status: accepted for the local R1 connector boundary, 2026-09-29.

## Context

Search Console access must be read-only, exact-site scoped, revocable, and
compatible with the shared egress and OpenBao foundations. Query results can be
partial or freshness-limited even after pagination.

## Decision

Use a one-time owner session, hashed OAuth state, S256 PKCE, and the fixed
`webmasters.readonly` scope. The callback consumes state before provider I/O. Store
the OAuth client secret, PKCE verifier, and refresh token only in separate OpenBao
KV paths; PostgreSQL stores an opaque reference, exact property resource name,
verified origin, owner, and immutable event. The owner must explicitly choose an
eligible discovered property. Domain and URL-prefix properties retain distinct
identity. Revocation appends a restriction event before any upstream request.

Google token, discovery, analytics, and revocation traffic goes through the shared
egress gateway. A provider response is never permission to bind another origin.
Imports preserve source dates, request shape, top-row and freshness limitations,
and unknown missing data rather than zero-filled days.

## Alternatives

- Direct Google SDK requests would bypass durable admission and public-address
  pinning, so they are rejected.
- Persisting access or refresh tokens in PostgreSQL would widen credential exposure,
  so only OpenBao secret references are durable there.
- Automatically binding the first matching property would remove the owner decision,
  so discovery and confirmation remain separate.

## Consequences

The connector needs a prepared shared-egress authority and current robots evidence
for each Google origin. Missing identity composition or absent provider credentials
keeps the dashboard capability unavailable. Refresh-token rotation must win a
per-binding lock and OpenBao CAS before the import continues; failure restricts the
binding to reauthorization rather than using an unpersisted token.
Authorized Google account success and OAuth consent are not yet live-qualified.

## Verification

The PostgreSQL, OpenBao, and isolated-network commands and their results are
recorded in [slice 0069](../implementation/0069-gsc-binding.md) and its evidence.
