# ADR-0004: Derive Site Scope From Current Server-Side Authority

Status: Accepted. Date: 2026-09-07.

## Context

The command foundation accepts a trusted tenant/site scope, but it cannot safely
derive that scope from a browser, Telegram message, model request, email address,
or claimed tenant ID. Specification sections 7.3 and 8 require verified sessions,
current memberships, explicit site grants, and post-recovery fencing before a
customer action can reach a scoped repository.

Keycloak integration, cookie issuance, and the independent recovery-generation
anchor are separate deployable concerns. This slice needs a testable authorization
contract without replacing any of them with custom authentication.

## Decision

Represent a person by immutable `(oidc_issuer, oidc_subject)` identity, never by
email. Link a global identity session to one tenant-selected application session
for the same user. Store only SHA-256 hashes of independently generated 256-bit
opaque session tokens; this construction is for high-entropy bearer tokens, not
human passwords.

The identity authorization repository receives an opaque tenant-session token, a
requested site UUID, and the current recovery generation obtained from a trusted
external authority. It derives the tenant and user from server-side session state,
then checks the current tenant lifecycle, site state, organization membership, and
exact site permission. A tenant ID is never accepted from the caller.

Permit the non-owner `signal_identity` role to locate only the `app.sessions` row
matching a transaction-local session hash before tenant scope is known. After that
lookup, forced RLS protects tenant/site membership reads. The role has reviewed
read-only table grants in this slice and cannot issue, revoke, or alter authority.
It remains a trusted service boundary because it can set PostgreSQL context.

Require the externally supplied recovery generation to match the linked global
identity session on every lookup. Missing or malformed generation state is a
service-configuration failure. A mismatch, expiry, revocation, disabled identity,
or authentication-level mismatch returns the same invalid-session result. Missing
membership, wrong site, or removed grant returns one generic authorization denial
without disclosing another scope.

## Narrowed Contract

- The only site permission is the exact versioned
  `site.snapshot.request` permission set. General role-bundle evaluation is later
  work.
- Session and authority rows are seeded only by the synthetic integration harness.
  No production session issuance or administration method exists.
- Keycloak OIDC verification, PKCE, cookies, CSRF, origin validation, MFA flows,
  invitations, tenant switching, logout, and transactional email are not
  implemented.
- The current generation is an input from a future independent recovery authority;
  this database is not allowed to declare its own restored generation current.
- This repository derives scope but does not yet submit a human-attributed command.

## Alternatives and Consequences

Accepting tenant/user IDs from HTTP or chat would turn caller claims into database
authority. Using email as identity would incorrectly link issuers. Encoding grants
inside a self-contained long-lived browser token would delay revocation. Storing
raw bearer tokens would increase credential exposure. Building local passwords or
an OIDC shortcut would compete with Keycloak and omit required account security.
All are rejected.

The two-stage RLS lookup is more explicit than a broad pre-tenant query. It also
means connection cleanliness and function privileges are part of authorization and
must remain under real PostgreSQL tests. A compromised identity service can still
misuse the context it is allowed to set; RLS is defense in depth, not a sandbox for
trusted backend code.

## Verification

See [slice 0004](../implementation/0004-session-derived-authorization.md) and
the [PostgreSQL evidence](../evidence/0004-postgresql.json). The complete database
suite passes 63 cases on PostgreSQL 17.11, including invalidation, expiry, recovery
generation, wrong-site denial, effective grants, and transactional migration
failure. This grants no pilot or production authority.
