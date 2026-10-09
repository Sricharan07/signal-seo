# ADR-0022: Consume Invitation Proof And Authority Atomically

- Status: Accepted
- Date: 2026-09-08
- Owners: Identity, account authority, and retention operations

## Context

ADR-0021 introduced a short-lived verified-identity proof but intentionally made
it impossible to consume. The earlier invitation function accepted a verified
identity projection directly from trusted service code. Exposing that function to
a browser adapter would leave proof consumption separate from authority creation,
allow a failed transaction to burn only one credential, and retain a runtime path
that could bypass the dedicated browser proof.

The proof table also contains a minimal but sensitive verified-email/provider
projection. Expired rows need a bounded deletion mechanism that does not grant a
scheduler general table access or permit deletion while a proof is live.

## Decision

Add `control.accept_site_invitation_with_proof`. It receives hashes of the opaque
identity proof and invitation bearer plus bounded public acceptance fields. The
function selects and locks exactly one unconsumed, unexpired proof under forced
RLS, invokes the existing invitation authority transaction with the proof's
stored identity projection, and marks the proof consumed only after authority and
audit creation succeed. Any denial, collision, or database failure rolls back
both credentials and all authority changes.

Revoke `signal_identity` execution of the older raw-projection
`control.accept_site_invitation` function. It remains an implementation primitive
owned by the migrator and can be invoked only from the new security-definer
function. The service boundary now accepts two opaque values, hashes both before
SQL, and never accepts issuer, subject, email, or provider token from its caller.

Replace proof immutability with a trigger that permits only the migrator-owned
function's exact null-to-transaction-time consumption transition. All identity
fields, hashes, identifiers, and expiry values remain immutable. Direct runtime
update/delete privileges remain absent.

Add `control.cleanup_invitation_identity_proofs(batch_size)` for
`signal_scheduler`. Each call deletes at most 1,000 expired rows in deterministic
order with `FOR UPDATE SKIP LOCKED`. Forced-RLS policies and the transition trigger
both require expiry before deletion. The scheduler receives execute only and no
proof-table read, update, or delete grant.

## Alternatives

- Consume the proof before calling invitation acceptance. Rejected because a
  later denial or outage could strand a valid invitation and force the user to
  repeat provider authentication.
- Consume the invitation first and proof second in separate transactions.
  Rejected because the proof could be replayed against another invitation after a
  partial failure.
- Keep direct raw-identity acceptance available to the runtime role. Rejected
  because it would bypass the browser-proof condition once HTTP is added.
- Delete a proof immediately on success. Rejected for now because a short bounded
  consumed record helps diagnose retries without adding identity data; it becomes
  cleanup-eligible at the same ten-minute maximum expiry.
- Grant the scheduler table delete permission. Rejected because a narrow bounded
  function can enforce eligibility and batch size without exposing identity rows.

## Consequences

- Proof use, invitation use, account/site authority, and acceptance audit evidence
  now share one PostgreSQL commit and one failure boundary.
- A wrong invitation token or recipient mismatch leaves the proof available for a
  corrected retry until its original expiry; a successful proof is one-time.
- Consumed and unconsumed proof rows remain stored only until their short expiry
  plus scheduler delay. Deployments must run and monitor cleanup before enabling
  browser issuance.
- The default API still has no invitation start, callback-cookie, CSRF, or
  acceptance route. This decision does not enable customer authentication.

## Verification

The real PostgreSQL 17.11 suite passes 278 cases. Coverage includes atomic success
and rollback, wrong invitation and email conditions, proof and invitation replay,
expired proof denial, concurrent acceptance, identifier and audit collisions,
revocation of the raw-projection runtime grant, exact guarded mutation, bounded
expired-row cleanup, scheduler least privilege, invalid batch rejection, and
migration rollback. Source-hashed evidence is recorded with the implementation
slice.
