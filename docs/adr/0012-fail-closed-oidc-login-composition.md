# ADR-0012: Compose OIDC Login As A Fail-Closed Pipeline

Status: Accepted. Date: 2026-09-08.

ADR-0013 supersedes this record's caller-supplied recovery-generation placeholder
and the first callback ordering step. The remaining composition decision is current.

## Context

The durable login attempt, Keycloak protocol adapter, OpenBao PKCE store, and
hash-only session issuer were previously qualified as separate boundaries. A
usable login must compose them without weakening their guarantees, blocking the
application event loop on synchronous PostgreSQL access, creating authority from
identity claims, or returning a session after an ambiguous failure.

The operations cannot be one distributed transaction. PostgreSQL, OpenBao, and
Keycloak have independent commit points. Attempting to roll their effects back as
if they were one transaction would create replay paths and false recovery claims.

## Decision

Use a deliberately ordered, fail-closed pipeline. Login initiation validates the
configured registration, local return path, TTL, entropy shape, and UUID before
network access. It validates exact Keycloak metadata, constructs but does not yet
release the authorization URL, creates the verifier at an immutable OpenBao path,
and only then commits the hash-only PostgreSQL attempt with the same UUID. The
authorization URL and browser binding are returned only after both durable writes
are acknowledged.

Callback completion validates deployment policy, time, and authorization-code
syntax, then reads the external generation as amended by ADR-0013. PostgreSQL selects one
winner using the state and browser-binding hashes. That winner validates exact
provider metadata and JWKS, permanently consumes the PKCE verifier, exchanges the
code, validates the signed ID token and nonce, and issues a hash-only global
session for an existing enabled issuer/subject identity. It never provisions a
user, membership, tenant, site grant, or other authority.

Synchronous PostgreSQL calls run through AnyIO's bounded worker-thread mechanism
so provider I/O does not turn database access into application event-loop stalls.
The caller remains responsible for supplying a dedicated idle autocommit identity
connection. The coordinator now obtains the current generation through the
read-only authority client defined by ADR-0013.

Use fixed stage codes and discard underlying provider, database, and secret
exception text at this boundary. Returned dataclasses hide the authorization URL,
browser binding, and raw session credential from representations. Provider tokens
and the PKCE verifier remain process-local and are never persisted by the flow.

## Failure Semantics

| Last confirmed point | Result |
| --- | --- |
| Local validation fails | No network or persistence effect |
| Provider discovery fails during initiation | No secret or database attempt |
| OpenBao creation fails | No database attempt |
| PostgreSQL attempt creation fails | Unreachable secret expires under the bounded OpenBao policy |
| Recovery authority fails before callback consumption | Attempt remains live; no provider, PKCE, or session operation |
| Callback proof loses or is invalid | No provider, PKCE, or session operation after the authority read |
| Provider metadata/JWKS fails after callback win | Attempt is burned; verifier expires; fresh login required |
| Verifier consume is unavailable or ambiguous | Attempt is burned; no code exchange or session |
| Code exchange or token validation fails | Attempt and verifier are burned; no session |
| Existing-user/session check fails | No authority or session is created; fresh login required |
| Session commit succeeds but response is lost | The inaccessible hash-only session expires naturally; fresh login required |

Burning the attempt on any post-consumption failure is intentional. The flow never
revives an attempt, reuses a verifier, or guesses whether an external operation
succeeded.

## Consequences

The internal control plane now has one coherent login operation with explicit
commit ordering and deterministic failure classes. A state collision after secret
creation can leave one unreachable short-lived verifier; the initiation credential
cannot broaden its authority to clean that up. OpenBao's bounded expiry is the
compensation.

This is not a public authentication feature. There is no HTTP route, browser
cookie issuance, logout/revocation route, invitation workflow, recovery rotation,
immutable login audit event, production configuration,
or combined real Keycloak/OpenBao/PostgreSQL deployment test.

## Alternatives

A distributed rollback protocol was rejected because none of these boundaries
offers a shared atomic commit and replaying callbacks would be less safe. Storing
the verifier in PostgreSQL was rejected by ADR-0011. Reading the login attempt
without consuming it before external calls was rejected because concurrent
callbacks could both progress. Creating users from token claims was rejected
because authentication must not manufacture authorization.

## Verification

Seventeen new PostgreSQL-backed cases cover success, hash-only persistence,
pre-network rejection, provider and secret failures, state conflict residue,
one-time callback behavior, proof-burning order, invalid tokens, unknown users,
and replay. The cumulative database suite passes 154 cases under the non-owner
identity role. The Keycloak protocol is rerun separately against its digest-pinned
TLS lab; OpenBao behavior remains covered by the source-hashed Slice 0011 lab.
