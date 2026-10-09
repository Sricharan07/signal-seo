# ADR-0024: Bind Human Commands To Live Authority In One Transaction

- Status: Accepted
- Date: 2026-09-08
- Owners: Command service, identity authorization, and API ingress

## Context

The original durable command primitive accepts only a trusted internal service and
an already derived `Scope`. The browser session work can prove a user and tenant,
but composing `authorize_snapshot` and command insertion as separate transactions
would leave an authority race between the check and durable acceptance. Granting
the identity role direct writes to command tables would also let a compromised
identity process forge actor attribution or bypass the intended request shape.

The production contract requires the idempotency fingerprint to include tenant,
actor, route, target, and canonical body. The actor is known only after the opaque
session has been resolved from server-side state, so the caller cannot safely
construct that fingerprint from browser claims.

## Decision

Add a narrow `SECURITY DEFINER` database boundary that resolves the SHA-256 hash of
one opaque tenant session, rechecks the parent identity session, independent
recovery generation, enabled user, active membership, exact site grant, tenant
lifecycle, and site state, and holds key-share locks through command acceptance.
It derives tenant and user identity inside PostgreSQL and applies transaction-local
tenant/site scope before accessing tenant records.

The same transaction computes a SHA-256 fingerprint from a versioned,
domain-separated byte representation containing tenant ID, actor user ID, stable
route key, site ID, and canonical body. It then inserts the immutable command,
sequence-one acceptance event, and outbox record. A matching retry returns the
original command; a different-site or different-intent collision returns one
indistinguishable idempotency conflict.

Human and service actors share `app.commands` with these enforced invariants:

- exactly one of `actor_user_id` and `actor_service` is present;
- a human actor references a membership in the same tenant;
- the persisted principal is derived from that actor;
- human and internal service routes occupy separate idempotency namespaces; and
- existing service-attributed rows remain valid without rewriting intent.

The identity role receives execute permission on only the authority, acceptance,
and own-command status functions. It receives no direct command, event, or outbox
write privilege. Status lookup repeats current authority checks and exposes only
the requesting actor's human snapshot command.

## Alternatives

- Authorize and insert in two application transactions. Rejected because current
  authority could change between the check and acceptance.
- Trust tenant or actor identifiers from the request. Rejected because opaque
  browser possession is not a scope claim.
- Grant the identity role direct command-table writes. Rejected because RLS alone
  does not constrain actor, route, event, and outbox composition narrowly enough.
- Compute the fingerprint before resolving the actor. Rejected because it would
  omit a required idempotency dimension or trust a browser-supplied identity.
- Replace existing internal service commands. Rejected because the schema can
  expand compatibly and the internal path still has valid uses.

## Consequences

- Human intent now has durable, queryable actor attribution without granting any
  execution or provider authority.
- The authority helper is shared by the existing authorization adapter and the
  command functions, avoiding divergent session and permission rules.
- Key-share locks serialize acceptance against concurrent changes that require
  conflicting row locks, but they are held only for the short database transaction.
- Human status is currently always `accepted`; dispatch, workflow progress,
  cancellation, results, and streaming remain unimplemented.
- No browser route is exposed by this decision. A later HTTP slice must add exact
  Origin/CSRF/session proof, bounded JSON, one idempotency header, and safe errors.

## Verification

The disposable PostgreSQL 17.11 suite passes 314 cases. New coverage includes
human attribution, byte-canonical fingerprints, retries, concurrent duplicates,
cross-site conflicts, stale sessions and recovery generations, every current
authority reduction, event/outbox rollback, own-command status, exact function
privileges, actor constraints, and upgrade preservation of existing service
commands. The run used non-owner runtime roles, completed cleanup, and granted no
production authority.
