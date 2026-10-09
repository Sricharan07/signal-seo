# ADR-0016: Issue Site Invitations Under Locked Current Authority

Status: Accepted. Date: 2026-09-08.

## Context

Login intentionally recognizes only existing `(issuer, subject)` users. A
separate invitation path is required before identity provisioning can be added
without turning authentication into an authority-creation operation. The first
path must preserve the pilot's one-site boundary, avoid storing invitation bearer
tokens, prevent role escalation, and leave durable evidence.

An authorization result can become stale after it is derived. Rechecking role and
site grant without locking still permits a concurrent demotion, suspension, or
site archive to race the invitation insert. Invitation recipient collisions also
need deterministic behavior under concurrent requests.

## Decision

Add a site-scoped `app.invitations` record for the bounded pilot. It stores a
normalized ASCII mailbox condition, one non-owner role, SHA-256 token hash,
inviter identity and authorization epochs, database timestamps, and one exact
site. The 256-bit raw token is returned once and excluded from representations,
audit facts, and every readable database column granted to the API role.

Only an `AuthorizedSite` principal previously derived from a live tenant session
may enter the application method. PostgreSQL receives its tenant, site, actor,
membership epoch, and site-authorization epoch as transaction-local context. A
narrow no-argument `SECURITY DEFINER` function locks, in order, the scoped tenant,
site, membership, and site grant. Forced RLS still applies to its owner. The
subsequent insert rechecks active state, exact epochs, role, tenant lifecycle, and
site lifecycle in the same transaction.

An owner may invite `viewer`, `analyst`, `editor`, `approver`, or `admin`. An
admin may invite through `approver` but may not create another admin. No caller
can invite an owner; ownership transfer remains a separate acceptance and MFA
flow. Other roles cannot issue invitations.

Serialize live invitations by tenant, site, and normalized recipient with a
transaction-scoped advisory lock. Reject a second unconsumed, unrevoked,
unexpired invitation. Expired rows do not block a replacement. Retry only an
unexpected cryptographic token-hash collision, with a fixed bound of three.

Create the invitation and its first `app.audit_events` record atomically. This
initial tenant audit contract accepts only `invitation.created`, sequence one,
the verified inviter, schema version, and target role. It includes a deterministic
SHA-256 envelope hash but no email or bearer data. Both records are immutable in
this slice. An audit insert failure rolls back invitation issuance.

`signal_api` receives only the membership/site columns needed for the recheck,
invitation columns excluding `token_hash`, exact invitation insert columns, and
exact audit insert columns. It cannot update or delete invitations, read tokens,
read the audit stream, invoke migration helpers, or call the authority lock
without its scoped database credential.

## Consequences

Invitation issuance is durable, site-bounded, role-bounded, race-aware, and
audited, but is internal only. The recipient cannot accept an invitation yet, no
identity or membership is provisioned, and no email is sent. Immutable null
consumption/revocation fields are deliberate placeholders that a later forward
migration must expand with guarded one-time transitions.

The authority-lock function is a privileged capability and remains deliberately
narrow: it takes no IDs, uses only transaction-local scope, returns a boolean,
and is executable only by `signal_api`. The service credential remains a trusted
boundary; RLS cannot make arbitrary compromised application code safe.

The event hash is not yet a hash chain, signature, or off-host checkpoint. A
database administrator can still rewrite storage outside ordinary DML. Audit
query authorization, export, retention, redaction, checkpointing, and recipient
notification are deferred.

## Alternatives

Provisioning users during login was rejected because authentication must not
create authority. Storing raw invitation tokens was rejected as a bearer-secret
risk. Email-only invitations without a token were rejected because mailbox text
is not proof of possession. Allowing owner invitations was rejected because
ownership transfer has stronger requirements. Application-only authority checks
were rejected because stale results and concurrent demotion need database
coordination. Granting broad table reads or updates was rejected in favor of
column grants and one narrow lock function.

## Verification

Twenty-six new PostgreSQL cases cover migration rollback, hash-only persistence,
canonical event hashing, atomic rollback, immutability, exact grants, role
ceilings, stale epochs, concurrent recipient serialization, token collisions,
input validation, residual-scope rejection, and authority-lock behavior. The
complete disposable PostgreSQL 17.11 suite passes 197 cases under non-owner roles
with cleanup confirmed.
