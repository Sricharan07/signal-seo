# ADR-0015: Gate Login Failure Audit On Consumed Proofs

Status: Accepted. Date: 2026-09-08.

## Context

ADR-0014 made successful identity-session issuance atomic with a durable event,
but failures after a valid OIDC attempt was consumed had no matching business
evidence. Provider discovery, PKCE consumption, provider assertion, identity
authorization, or session persistence can fail after the one-time attempt has
been burned. Ordinary process logs are not an adequate durable record of those
outcomes.

Recording every malformed or untrusted callback would create a durable write
amplification path for anonymous traffic. It would also tempt the audit contract
to retain supplied state, authorization codes, provider responses, exception
text, or guessed actor identity. An external provider or secret operation cannot
share a PostgreSQL transaction with the attempt-consumption update.

## Decision

Expand `control.platform_events` by forward migration with one additional event:
`identity.login.failed`. It is accepted only for an existing, already consumed
`oidc_login_attempt` selected under the original state and browser-binding hash
scope. The event has no actor because a provider assertion has not necessarily
established one. Its facts are exactly `{"schema_version":1}` and its reason is
one value from a closed internal set:

- `provider_configuration_failed`
- `pkce_unavailable`
- `pkce_consume_failed`
- `provider_assertion_failed`
- `identity_not_authorized`
- `session_persistence_failed`

Do not record malformed callback input, wrong browser proofs, replayed attempts,
or recovery-authority failures that occur before attempt consumption. Those
requests remain fixed, non-disclosing failures and cannot allocate arbitrary
durable audit rows.

After a legitimate attempt is consumed, append the failure event in a clean,
separate PostgreSQL transaction before returning the stage's existing redacted
error. Forced RLS reuses the original two proofs, the insert trigger verifies the
consumed object, and the unique object/type key permits at most one failure event
per attempt. The runtime role keeps only its existing exact insert columns and
does not receive platform-event read access. The insert avoids `RETURNING`
because that would require read authority on the destination event table.

If this mandatory append fails, return the distinct fixed error
`LOGIN_CALLBACK_AUDIT_FAILED`. Do not report the earlier stage as durably audited,
and do not create a session. Never persist state, browser binding, nonce, PKCE
references or values, authorization codes, provider tokens or bodies, exception
text, contact data, or database error detail in this event.

## Consequences

Every supported post-consumption failure path now either commits one sanitized
event or surfaces an explicit audit failure. A compromised identity process
cannot manufacture failure rows for unknown or unconsumed attempt IDs and cannot
use this path to enumerate platform events.

The attempt-consumption update and failure event cannot be atomic across the
intervening external operations. A database outage after consumption can still
leave a burned attempt without a durable failure event; the fixed audit-failure
response makes that gap visible to the caller but does not repair it. Future
workflow or reconciliation work must treat this as a first-class operational
condition rather than claiming exactly-once external orchestration.

This is still not complete authentication audit. Pre-consumption rejection,
login initiation failures, tenant selection, logout, revocation, invitation,
provisioning, audit export, retention, checkpointing, and customer audit views
remain absent.

## Alternatives

Auditing every inbound callback was rejected because unauthenticated traffic
could create unbounded durable state. Including provider or exception details was
rejected because it creates secret and personal-data leakage paths. Guessing an
actor from unverified claims was rejected because audit identity must be proven.
Best-effort logging was rejected because it could silently lose a required event.
Granting `SELECT` on platform events for `INSERT ... RETURNING` was rejected as
unnecessary privilege.

## Verification

Eleven new PostgreSQL-backed cases cover migration rollback, exact sanitized
shape, closed reasons, consumed-object validation, original-proof scoping,
single-use insertion, all composed failure categories, explicit audit failure,
and the absence of events before consumption. The complete disposable
PostgreSQL 17.11 suite passes 171 cases under non-owner runtime roles with cleanup
confirmed.
