# ADR-0021: Separate Invitation Identity Proofs From Login Sessions

- Status: Accepted
- Date: 2026-09-08
- Owners: Identity and account authority

## Context

Invitation acceptance can create a new user and therefore cannot require an
existing Signal identity session. It still requires a fresh signed OIDC identity
with provider-verified email. Reusing ordinary login would either provision
authority during authentication or force an unknown invitee through a session
issuer that correctly rejects unknown users.

The callback also needs to know whether the browser initiated ordinary login or
invitation verification. A query parameter at callback time is not trustworthy;
the purpose must be bound to the same durable state, nonce, browser binding, and
PKCE transaction as the provider response.

## Decision

Add an immutable `purpose` to each durable OIDC attempt with exactly two values:
`login` and `invitation_acceptance`. Existing rows and callers default to `login`.
The purpose is written before the provider redirect, protected by the attempt's
existing forced RLS and update guard, and returned only after exact one-time state
and browser-binding consumption.

Add `control.invitation_identity_proofs` for a dedicated post-callback credential.
`issue_invitation_identity_proof` accepts only a `VerifiedOidcIdentity` carrying a
normalized provider-verified email and an ID-token assertion issued no more than
eleven minutes ago. The proof expires at the earlier of the signed identity expiry
or ten minutes after issuance.

The browser credential is a random 256-bit opaque value. Only its SHA-256 hash is
stored. The protected row also keeps the exact issuer, subject, normalized verified
email, signed issuance/expiry times, proof expiry, and internal UUID required for
later atomic acceptance. It stores no ID token, access token, provider session ID,
PKCE verifier, invitation token, tenant ID, role, or site authority.

The identity role can insert reviewed columns and select only a row matching the
transaction-local proof hash. It cannot update, delete, truncate, enumerate, or
migrate proofs. Rows are immutable in this slice; a later migration must replace
that guard with one exact consumption transition composed atomically with
invitation acceptance.

## Alternatives

- Issue a normal identity session to unknown invitees. Rejected because ordinary
  login must never provision or imply account authority.
- Put invitation purpose in the callback query or an unsigned browser field.
  Rejected because it would not be bound to the original authorization request.
- Store the provider ID token until acceptance. Rejected because the bounded
  verified projection is sufficient and avoids retaining a reusable assertion.
- Put invitation ID or bearer token into the OIDC state or callback URL. Rejected
  because invitation bearer material belongs in the later POST body and must not
  enter URL logs.
- Consume the proof in a standalone service now. Rejected because consumption and
  authority creation must commit together; an intermediate commit could strand a
  valid invitation after a transient failure.

## Consequences

- The next composition can authenticate fresh invitees without weakening
  existing-user login or trusting callback-selected purpose.
- [ADR-0022](0022-atomic-invitation-proof-acceptance.md) later implements the exact
  proof transition, atomic authority composition, legacy grant revocation, and
  bounded expired-row cleanup described here.
- A proof grants no user, organization, site, connector, or write authority by
  itself and is not yet browser-reachable.
- Verified email and provider identifiers are temporarily persisted as sensitive
  identity conditions. No production path may enable issuance until bounded
  consumption and expired-row cleanup exist and retention is configured.
- Existing login-attempt writers remain compatible through the database and
  service default of `login`.
- The default API remains unconfigured; invitation acceptance HTTP, delivery,
  throttling, and account-recovery behavior remain absent.

## Verification

Twenty new PostgreSQL cases cover default and invitation purposes, immutable
attempt binding, verified-email and provider-time validation, hash-only storage,
proof expiry, collision retry and rollback, RLS non-enumerability, exact
privileges, immutable rows, residual connection scope, malformed inputs, and
migration rollback. The real PostgreSQL 17.11 suite passes 270 cases. The API
suite remains at 106 and the complete non-database Python suite passes 271.
