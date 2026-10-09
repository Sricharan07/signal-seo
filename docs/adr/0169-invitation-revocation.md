# ADR-0169 - Invitation revocation

Status: Accepted for the locally qualified boundary; no release authority.

## Context

Slice 0158 supplies owner-issued one-time invitation links, but its foundations
allow only pending-to-accepted transitions. A leaked link cannot be canceled before
expiry. This is a security-critical identity, tenancy and recovery change under
Revision 4.0 section 19 and Revision 3.2 section 18.5.

## Decision

Migration 0097 follows 0096 (merge train 5; written as 0091 after 0090) without changing either earlier migrations or accepted
specifications. Add only a pending-to-revoked transition, with the revoker recorded.
The current selected site's owner needs current sessions, the current recovery
generation, five-minute-fresh MFA, a verified site and the existing browser mutation
proof. Expired, accepted, revoked and wrong-scope invitations fail closed.

Expose a fixed-search-path SECURITY DEFINER function to the existing API and
identity roles, not direct table mutation privileges. Reuse `team_owner` and its
authority locks. Acceptance and revocation share the tenant/site locking order,
then serialize on the invitation. One terminal transition and one sequence-two
audit event can win; the loser receives the existing generic denial.

Commit the immutable invitation hash-chain audit, platform restriction event and
typed authority-journal outbox in the same transaction. Return local revocation
with `AUTHORITY_DURABILITY_PENDING`, never a fabricated independent receipt.
Extend the existing independent journal with one closed deny-only invitation kind.
Restore replay installs an immutable tombstone; acceptance checks it under the
same locks. Replay never invents accepted membership or edits historical audit
records. An unknown target's tombstone is retained.

Keep Revoke under owner Team settings, only for qualified pending invitations,
with an explicit confirmation describing the effect. Current members are unchanged.
Uncertain outcomes ask the owner to reload; there is no blind retry. Projection
distinguishes local revocation from recorded independent recovery confirmation.

## Consequences

This operation only removes the ability to accept a pending invitation. It does
not remove existing members, revoke accepted grants, add roles, resend links,
deliver email or grant production authority. Existing purpose-bound identity
proofs and account non-enumeration remain intact. A journal outage leaves local
revocation effective but not independently durable until acknowledged.

[0159](../implementation/0159-invitation-revocation.md) records PostgreSQL race,
failure, independent-journal/restore and Keycloak qualification. This supersedes
the unsupported-revocation limitation in [ADR-0168](0168-owner-team-invitations.md)
without changing its issuance, delivery or acceptance decisions.
