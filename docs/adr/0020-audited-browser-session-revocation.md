# ADR-0020: Revoke The Parent Identity Session On Browser Logout

- Status: Accepted
- Date: 2026-09-08
- Owners: Identity, API, and audit

## Context

Tenant selection introduced two browser credentials: a pre-tenant identity token
and a selected tenant token. A current-session view must not trust the tenant,
role, or expiry implied by browser state. Logout must be a server-side revocation,
not merely cookie deletion, and it should remain possible when the external
recovery-generation service is unavailable.

A user may log out before selecting a tenant, while a selected browser normally
holds both cookies. Accepting the weaker identity-bound CSRF proof whenever both
are present would permit proof downgrade. Updating session tables directly would
also require broad mutation privileges that the identity role does not have.

## Decision

Add `GET /v1/session` to inspect only the exact tenant token hash. The service
rechecks the child session, parent identity session, external recovery generation,
enabled user, active tenant, and current active membership before returning the
server-derived tenant, user, role, authentication level, and expiry. It grants no
site capability and does not mutate `last_seen_at`.

Add `POST /v1/session/logout`. When a tenant cookie is present, it takes precedence
and the request must carry the CSRF proof bound to that tenant token. The identity
proof is accepted only when no tenant cookie is present. Duplicate cookies,
untrusted origin, cross-site metadata, and missing or invalid CSRF all fail before
the gateway.

Expose one `control.revoke_browser_session(bytea, text, uuid)` function to the
identity role. The caller hashes the presented token before PostgreSQL. The
function resolves only that exact identity or tenant session, locks the parent
identity session, and changes its `revoked_at` once. A tenant logout also marks the
presented child session revoked. Other child rows remain stored, but every one is
immediately invalid because all authorization paths require the live parent.

The first revocation atomically appends one strict, immutable
`identity.session.revoked` platform event with only schema version, presented
session kind, actor, parent session ID, and reason `user_logout`. Concurrent and
repeated requests converge on the same state without duplicate events. Event-ID
collision retry rolls the revocation back before retrying.

Logout deliberately does not consult OpenBao or require an unexpired session.
Possession of an exact cookie and its same-origin CSRF proof is sufficient to
remove authority. The API returns `204` and clears identity, tenant, and in-flight
OIDC-binding cookies only after the database operation completes. Unknown or
already revoked credentials receive the same successful response and create no
event, avoiding a session-validity oracle.

## Alternatives

- Delete only browser cookies. Rejected because copied credentials would remain
  valid at the server.
- Revoke only the selected tenant row. Rejected because the retained identity
  session could immediately mint another tenant session and would not behave as
  full logout.
- Update every child session across every tenant. Rejected because parent
  revocation already invalidates them and a cross-tenant update would broaden the
  function unnecessarily.
- Require recovery-generation availability for logout. Rejected because an
  authority outage must not prevent a user from removing existing authority.
- Accept either CSRF proof when both cookies exist. Rejected because the selected
  tenant proof is the stronger and least ambiguous current-browser signal.
- Grant `UPDATE` on session tables to `signal_identity`. Rejected in favor of one
  exact hash-scoped function with tested event atomicity.

## Consequences

- Current-session responses are derived from live server state and do not imply
  site access or connector authority.
- Logout invalidates all Signal sessions descended from the parent identity
  session and leaves one append-only reasoned event.
- Physical cleanup of expired and revoked rows remains a separate retention job.
- Revocation is local to Signal. Keycloak RP-initiated logout, provider session
  termination, multi-device session management, and administrator revocation are
  not implemented.
- The default API remains unconfigured and cannot authenticate a customer.

## Verification

Seventeen new PostgreSQL cases cover live inspection, invalid current state,
tenant and identity logout, strict immutable audit, exact privilege, retry and
rollback, concurrency, migration rollback, malformed input, and idempotent unknown
sessions. Twenty-one non-database cases cover the two routes, gateway ordering,
strict browser proofs, proof precedence, output validation, cookie clearing,
dependency failure, and secret-safe errors. The real PostgreSQL suite passes 250,
the API suite passes 106, and the complete non-database Python suite passes 271.
The unchanged OpenBao and Keycloak labs pass seven and five real-service scenarios.
