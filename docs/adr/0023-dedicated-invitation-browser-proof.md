# ADR-0023: Expose Invitation Acceptance Through A Dedicated Browser Proof

- Status: Accepted
- Date: 2026-09-08
- Owners: Identity, API ingress, and account authority

## Context

ADR-0021 and ADR-0022 provide purpose-bound OIDC state, short-lived verified
identity proofs, and atomic proof-backed invitation acceptance. None was reachable
through the browser. Reusing the normal identity-session cookie for an unknown
invitee would weaken the rule that ordinary login never provisions a user. Putting
the invitation bearer in OIDC state, a callback URL, or a route path would expose it
to browser, proxy, and access-log surfaces.

The shared OIDC callback must also preserve a useful outage property: ordinary
login reads the independent recovery generation before consuming its callback,
while invitation verification does not need or receive that authority.

## Decision

Read the immutable attempt purpose under the existing state and browser-binding
scope before callback consumption. For ordinary login, fetch the recovery
generation before consumption exactly as before. For invitation verification,
skip that external authority read. After provider and PKCE validation, issue only
the artifact selected before redirect: an identity session for `login`, or an
invitation identity proof for `invitation_acceptance`.

Expose four bounded browser operations:

| Route | Contract |
| --- | --- |
| `GET /v1/invitations/verify` | Starts only an `invitation_acceptance` OIDC attempt; invitation ID and bearer are not accepted |
| `GET /v1/session/callback` | Uses the durable purpose and sets either the normal identity cookie or the invitation-only proof cookie |
| `GET /v1/invitations/csrf` | Derives one CSRF token from the exact invitation-proof cookie |
| `POST /v1/invitations/accept` | Requires exact Origin, optional safe Fetch Metadata, proof-bound CSRF, the proof cookie, and a strict bounded JSON body |

The invitation ID and invitation bearer exist only in the acceptance body. The
proof is a separate `Secure`, `HttpOnly`, host-only, `SameSite=Lax` cookie with a
maximum lifetime of ten minutes. It grants no login, tenant, site, connector, or
general write authority. Successful acceptance clears it. A generic invitation
denial leaves it available for a corrected request until expiry because the
database transaction consumed neither credential.

Add two closed failure reasons to the immutable platform-event contract:
`invitation_identity_not_verified` and
`invitation_proof_persistence_failed`. They describe only the stage and never
retain email, subject, tokens, callback values, provider payloads, or exception
text. Invalid or unconsumed callbacks still append no event.

The application factory remains unconfigured by default. Merely importing or
running the API does not install identity credentials or enable customer access.

## Alternatives

- Issue a normal identity session to every invitee. Rejected because a session
  represents an existing enabled Signal user and ordinary login must not create one.
- Put the invitation bearer in OIDC state or the callback query. Rejected because
  URLs are routinely retained outside the application and the bearer is not needed
  to verify provider identity.
- Store the raw provider assertion in a browser cookie. Rejected because the
  purpose-specific random proof is narrower, hash-only at rest, and independently
  bounded.
- Consume the proof on every denied POST. Rejected because mistyped or stale
  invitation input would force another provider round trip despite no authority
  change.
- Automatically create a login session after acceptance. Deferred because that is
  a separate transition with its own recovery-generation and UX contract.

## Consequences

- Fresh invitees can complete the internal browser acceptance journey without
  weakening existing-user login or exposing invitation credentials in route URLs.
- An already logged-in browser retains its existing identity and tenant cookies
  while proving an invitation identity; the new proof is still independently
  checked against the invitation recipient by PostgreSQL.
- Callback purpose selection incurs one bounded database read before consumption.
  Purpose is immutable, and concurrent consumption still has exactly one winner.
- Application responses and logs are redacted, but a deployment must also disable
  request-body logging at every upstream ingress before enabling the route.
- Delivery, throttling, lockout policy, deployed cleanup scheduling, post-acceptance
  login, and production credentials remain required before customer onboarding.

## Verification

The API suite passes 130 cases, including exact cookies, purpose selection,
callback output validation, CSRF and Origin checks, duplicate and malformed body
rejection, denial retry behavior, success-only clearing, gateway isolation, and
log redaction. The real PostgreSQL 17.11 suite passes 286 cases, including purpose
reads, ordinary-login outage ordering, invitation completion without a recovery
read, hash-only proof issuance, closed failure audits, and migration rollback.
The complete non-database Python suite passes 295 cases.
The unchanged adapters also pass five disposable real-Keycloak and seven
real-OpenBao scenarios with completed cleanup and no production authority.
