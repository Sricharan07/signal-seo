# ADR-0007: Require Session-Bound Browser Mutation Proofs

Status: Accepted. Date: 2026-09-07.

## Context

Signal's browser will authenticate with an opaque server-side session cookie.
Cookies are sent automatically by browsers, so every state-changing endpoint also
needs a CSRF boundary. SameSite cookie behavior helps but is not a complete
authorization or request-origin contract. Ambiguous duplicate headers and cookies
must also fail closed rather than being interpreted differently by proxies,
frameworks, and application code.

No state-changing customer endpoint is ready yet. The protection contract should
exist and be tested before one is introduced, while preserving the distinction
between request-possession proof and current database authorization.

## Decision

Use the host-only `__Host-signal_session` cookie with `Secure`, `HttpOnly`,
`SameSite=Lax`, and `Path=/`, and never set a `Domain` attribute. The opaque token
is a 256-bit unpadded base64url value. Centralize its validation and SHA-256 lookup
hash in the trusted control-plane package so HTTP and database code cannot drift.

For every future cookie-authenticated `POST`, `PUT`, `PATCH`, or `DELETE`, require:

- exactly one configured HTTPS origin, with loopback HTTP allowed for local work;
- exactly one Origin header that normalizes to that configured origin;
- no contradictory cross-site fetch metadata;
- exactly one valid session cookie, including across repeated Cookie headers; and
- exactly one session-bound CSRF header validated with an HMAC-SHA-256 proof and
  constant-time comparison.

Reject missing, malformed, duplicated, cross-site, and mismatched proofs through
one safe `403` response. If browser protection was not configured, fail with a
safe `503`. Keep the HMAC key and session token out of object representations.
Limit cookie issuance to 24 hours; the server-side session remains the authority
and may expire or be revoked earlier.

Expose the check as an explicit FastAPI dependency. A protected route must still
resolve the opaque session against current server-side identity, recovery
generation, tenant membership, site grant, permission, and resource state. A CSRF
proof never grants that authority by itself.

## Consequences

Future browser mutations have a reviewed guard available before their route is
implemented. HMAC derivation avoids a second readable CSRF cookie and does not
persist raw credentials. Rotating the HMAC key invalidates outstanding CSRF proofs
without making browser session cookies self-authorizing.

The process still has no login, callback, session issuance, session read, logout,
or customer mutation endpoint. HMAC-key provisioning, Keycloak authorization-code
flow with PKCE, durable login transactions, trusted proxy/TLS deployment, and
current database authorization remain separate required work.

Cross-origin dashboard deployment is allowed only through an exact configured
origin and still needs a deliberate CORS response policy. No CORS middleware is
enabled in this slice. The guard deliberately rejects safe HTTP methods if it is
mistakenly attached to one, making route misconfiguration visible during tests.

## Alternatives

Relying on SameSite alone was rejected because cookie behavior is not a substitute
for explicit origin and CSRF validation. A conventional double-submit cookie was
rejected because it adds another browser cookie and does not bind the proof to the
opaque application session. Storing a bearer JWT in browser storage was rejected
because it enlarges credential exposure and conflicts with the server-side session
contract. Silently taking the first duplicate header or cookie was rejected as an
interpretation ambiguity.

## Verification

The API suite has 36 passing cases. It covers successful same-site proof,
missing/invalid/duplicate origins, fetch metadata, cookies, and CSRF headers,
unconfigured protection, key and session binding, cookie attributes, origin
validation and normalization, secret-safe representations, input bounds, and
guard misuse. The full 63-case PostgreSQL 17.11 suite also passes after the shared
token refactor, with source-hashed evidence and completed disposable cleanup.
