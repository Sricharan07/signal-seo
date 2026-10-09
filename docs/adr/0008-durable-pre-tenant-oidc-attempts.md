# ADR-0008: Persist Pre-Tenant OIDC Attempts as One-Time Hashed Records

Status: Accepted. Date: 2026-09-07.

## Context

The authentication contract requires OIDC authorization code with PKCE, state and
nonce binding, exact redirects, and a server-side session. Redirect callbacks can
arrive after process restarts and can be replayed or raced. Keeping login state in
process memory would make restarts lose the transaction and horizontal instances
disagree about whether a callback was already used.

OIDC login starts before Signal knows the user's tenant. The persistence boundary
therefore cannot rely on tenant RLS, must not store raw browser tokens or a PKCE
verifier, and must not let the identity role enumerate other pending attempts.

## Decision

Add `control.oidc_login_attempts` in forward-only migration `0003`. Each row stores
SHA-256 hashes of a random OIDC state, ID-token nonce, and separate host-only
browser-binding token. It stores the fixed issuer, client ID, exact redirect URI,
bounded local return path, expiry, one-time consumption time, and a reference to
the PKCE verifier in a future secret manager. It never stores any raw token,
authorization code, provider token, or PKCE verifier.

Require both the state hash and browser-binding hash as transaction-local database
scope. Forced RLS uses both values for reads, inserts, and consumption. The
`signal_identity` role receives table-level `SELECT` and `INSERT`, column-level
`UPDATE` only for `consumed_at`, no `DELETE`, no schema creation, and no migration
authority. A trigger makes every other field immutable and allows consumption
only once before expiry.

The service validates 256-bit unpadded base64url browser values, HTTPS issuer and
redirect URLs with loopback HTTP only for disposable labs, bounded client IDs,
secret references, local return paths, and a 60-to-600-second TTL before database
access. Consumption is one atomic `UPDATE ... RETURNING`; expired, missing,
already-consumed, wrong-state, and wrong-binding cases have the same application
error. Nonce hashes are compared in constant time after a validated ID token is
available.

Extend connection-contamination checks to both new OIDC scope variables. A pooled
connection with any residual identity, tenant, site, session, state, or binding
scope is discarded rather than silently reused.

## Consequences

OIDC callback state can survive API restarts and concurrent callbacks converge on
one consumer. A leaked state value alone is insufficient for database visibility
or consumption because the separate host-only browser binding is also required.
Rows remain useful for bounded security investigation without retaining raw
browser secrets.

This does not perform OIDC discovery, build an authorization URL, write or read a
PKCE secret, exchange a code, validate a signed token, create a user, issue an
application session, or expose a login route. Consuming before code exchange means
a crash or provider failure requires a fresh login; that conservative behavior is
accepted for this foundation and must be visible in the eventual user flow.

Expired/consumed-row cleanup is not yet granted to a runtime role. A later
retention task must delete only eligible rows through a separately reviewed
boundary. The secret reference has no usable verifier until a secret manager is
integrated, and the production composition must forbid loopback registrations.

## Alternatives

In-memory state was rejected because it fails across restarts and replicas. Storing
raw state, nonce, browser binding, or verifier in PostgreSQL was rejected because
those are short-lived credentials or replay defenses. State-only RLS was rejected
because possession of a callback URL should not reveal the attempt without the
initiating browser binding. A reusable row was rejected because callback replay
must fail atomically.

## Verification

Migration and service tests run under real non-owner roles on PostgreSQL 17.11.
The cumulative suite has 95 passing cases, including 32 new cases for hash-only
storage, state-and-binding isolation, wrong and expired input, one-time and
concurrent consumption, column-level privilege, immutability, URL/input bounds,
secret-safe representations, residual scope rejection, and migration rollback to
revision `0002`. The source-hashed report records no production authority and
completed cleanup.
