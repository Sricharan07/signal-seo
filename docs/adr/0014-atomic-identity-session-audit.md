# ADR-0014: Commit Identity Session Audit With Issuance

Status: Accepted. Date: 2026-09-08.

## Context

Slice 0012 could issue a hash-only identity session after a valid OIDC callback,
but the issuance transaction created no immutable audit record. Writing an event
after commit would permit a live session without matching evidence if the process
or database connection failed between operations. Writing before commit could
record a session that never existed.

The production schema calls for append-only `control.platform_events` for global
account and platform authority history. Starting that table as an unconstrained
generic JSON sink would make the first audit claim broad but weak. The initial
contract therefore needs one typed event with exact scope and atomicity.

## Decision

Create `control.platform_events` with only the
`identity.session.issued` contract enabled. Its object kind is exactly
`identity_session`; its object and actor must reference the same
`control.identity_sessions` row and user. Facts contain exactly schema version one
and the validated authentication level. The event carries no raw token, token
hash, issuer, subject, provider token, PKCE verifier, browser proof, email, recovery
generation, or tenant data.

Insert the session and event in the same PostgreSQL transaction. Return the event
UUID with the issued-session value so a future HTTP and telemetry adapter can
correlate the response without querying the global event table. Any event failure
rolls back the new session; a caller never receives an unaudited credential.

Make platform events append-only with both privilege and trigger protection.
`signal_identity` receives only column-level insert permission and no select,
update, delete, truncate, reference, trigger, schema, or migration permission.
Forced row-level security allows insertion only when the referenced session and
actor match the transaction's current hash-scoped identity session. The database
administrator can inspect events for qualification but cannot update or delete
them through ordinary DML.

Validate the typed session/user object with an insert trigger rather than a
permanent foreign key. This lets the immutable event retain its object identifier
after a future session-retention job removes the short-lived session and token
hash. The actor user remains referenced because account identities are retained
while audit records depend on them.

Future event types must be added by forward migration with their own typed object,
facts, actor, and reason constraints. They must not weaken the accepted issuance
contract into arbitrary text or JSON.

## Consequences

Every session issued through `issue_identity_session` now has one immutable event
at commit. Token-allocation retries do not create orphan events, and an event-ID
collision fails the issuance transaction rather than omitting evidence.

This is not complete login auditing. Rejected anonymous callbacks, provider or
secret failures after attempt consumption, session revocation, tenant selection,
logout, and operator actions do not yet append events. The table has no dispatcher,
off-host checkpoint, signature, hash chain, retention job, redaction workflow, or
customer query API. It is a narrow durable source record, not an observability
backend or a release claim.

ADR-0015 subsequently adds a narrow event for proof-matched failures after
attempt consumption. Anonymous and other pre-consumption rejects remain outside
the durable event stream.

## Alternatives

Best-effort logging was rejected because logs are not durable business evidence.
A second post-commit transaction was rejected because it allows unaudited live
sessions. A generic unvalidated facts document was rejected because it invites
sensitive-data leakage and contract drift. Giving the identity role read access
was rejected because session issuance does not require global event enumeration.
Adding tenant audit rows was deferred because an identity session is pre-tenant
and must not manufacture tenant scope.

## Verification

Five new PostgreSQL cases cover the forward migration, exact event shape,
append-only enforcement, hash-scoped and column-limited privileges, and rollback
when event insertion fails. Existing login and session tests prove the event is
created on both direct and composed issuance paths. The cumulative suite passes
160 cases under non-owner runtime roles and confirms invocation-owned cleanup.
