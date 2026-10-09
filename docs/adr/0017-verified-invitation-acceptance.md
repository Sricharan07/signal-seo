# ADR-0017: Bind Invitation Acceptance To Verified Identity

- Status: Accepted
- Date: 2026-09-08
- Owners: Identity and account authority

## Context

Slice 0016 could issue a one-site invitation but deliberately could not consume
it or create authority. Acceptance must resolve a random invitation without
weakening forced tenant RLS, bind the recipient to a signed provider assertion,
remain single-use under concurrency, and create no authority during ordinary
login. Email text alone cannot identify or merge users because Signal identity
is the exact `(issuer, subject)` pair.

The raw invitation token is unsuitable as a global routing record. Tenant-owned
invitation rows also cannot be globally searched by a runtime role without a
separate scope-discovery mechanism.

## Decision

Request the exact OIDC scopes `openid email`. Treat an email as verified only
when a validated signed ID token contains boolean `email_verified: true` and an
email accepted by the shared restricted ASCII mailbox normalizer. Retain that
condition as a representation-hidden field on `VerifiedOidcIdentity`; unverified
email is never an acceptance condition.

Add `control.invitation_routes`, a protected routing index containing only the
random invitation UUID, tenant/site UUIDs, and creation time. It contains no
email, role, token, or token hash. An `AFTER INSERT` security-definer trigger
creates the route atomically with every future invitation. Migration 0008
temporarily removes forced owner RLS only inside its transaction to backfill
existing invitation routes, then restores forced RLS before commit.

Expose one exact `control.accept_site_invitation` function only to
`signal_identity`. The identity service supplies a fresh `VerifiedOidcIdentity`,
the invitation UUID, and a SHA-256 token hash; raw bearer material never reaches
PostgreSQL. The function resolves scope, locks tenant then site then invitation,
requires active resources and an unexpired matching email/token pair, and
serializes the exact issuer/subject identity.

Acceptance reuses only an enabled exact issuer/subject user. A matching email
under another issuer creates a distinct user. Existing profile email is not
silently rewritten. Any existing membership in the target tenant makes the
invitation ineligible rather than turning acceptance into an implicit role
change.

In one transaction, successful acceptance creates a missing user, active
membership, one-site grant, guarded invitation consumption, and sequence-two
`invitation.accepted` audit evidence. The accepted event references the creation
event hash and has a deterministic SHA-256 envelope. The initial two-event chain
is not presented as a signature or off-host checkpoint.

## Alternatives

- Grant a runtime role global invitation reads. Rejected because it defeats
  tenant isolation and exposes recipient data.
- Encode tenant/site in the bearer token. Rejected because it complicates the
  already-issued opaque token contract and still requires server-side validation.
- Link users by normalized email. Rejected because providers and subjects, not
  mutable email strings, define identity.
- Create membership during ordinary login. Rejected because authentication alone
  is not organization authorization.
- Split user, membership, consumption, and audit into separate commits. Rejected
  because retries and failures could leave partial authority or a consumed token
  without its evidence.

## Consequences

- Invitation acceptance can provision exact account/site authority and then use
  the existing hash-only session issuance path.
- The identity database credential remains a sensitive trusted boundary. It can
  invoke only the narrow acceptance function; it receives no direct route,
  invitation-update, user-insert, membership-insert, or audit-insert privilege.
  [ADR-0022](0022-atomic-invitation-proof-acceptance.md) later revokes direct
  runtime execution of this raw-projection function in favor of proof-backed
  atomic acceptance.
- UUID allocation collisions are retried a bounded number of times. Existing
  membership, disabled identity, wrong proof, stale assertion, expiry, and replay
  return one generic denial to the service boundary.
- Public invitation URLs, callback transport, browser CSRF/origin integration,
  email delivery, throttling, IdP account creation, revocation, and owner transfer
  remain separate work. [ADR-0021](0021-purpose-bound-invitation-identity-proofs.md)
  later defines the dedicated post-callback proof, and ADR-0022 composes its
  consumption with this authority transaction. Browser transport remains absent.

## Verification

Twenty new PostgreSQL cases cover provisioning, session composition, replay,
wrong and unverified proofs, cross-issuer email, exact-user reuse, disabled and
already-member denial, concurrent acceptance, bounded collisions, atomic audit
rollback, immutable consumption, exact privileges, and failed migration rollback.
The complete PostgreSQL 17.11 suite passes 217 cases under non-owner runtime roles.

Four additional protocol cases cover verified-email extraction, unverified-email
discard, malformed verification claims, and the exact expanded scope. The
non-database Python suite passes 201 cases. Five disposable Keycloak 26.7.3
protocol checks pass over locally trusted TLS, including the signed verified
email claim. Evidence is recorded in
[0017-postgresql.json](../evidence/0017-postgresql.json) and
[0017-keycloak.json](../evidence/0017-keycloak.json).
