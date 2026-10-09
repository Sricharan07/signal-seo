# ADR-0010: Issue Scoped Hash-Only Application Sessions

Status: Accepted. Date: 2026-09-07.

## Context

Slices 0004, 0008, and 0009 established current-state authorization, durable
one-time login attempts, and a validated Keycloak identity projection. They did
not turn that projection into a Signal credential. A callback process must not be
able to provision itself into an organization, select an organization without a
live membership, persist bearer credentials, or make a restored database valid
without checking independent recovery authority.

Signal needs two scopes. A global identity session permits only account-level
selection, while a separate tenant session can enter one organization. The later
site authorizer must still select an exact current site grant. These boundaries
need to remain useful after connection pooling without allowing transaction-local
scope to leak between requests.

## Decision

Accept only the `VerifiedOidcIdentity` projection returned by the reviewed OIDC
protocol boundary. Match an already provisioned, enabled user by the exact issuer
and subject pair; never create a user, membership, role, or site grant during
session issuance. Unknown and disabled identities have the same empty failure.
The caller is responsible for composing the protocol validator and issuer in the
same trusted service boundary; constructing the Python projection by hand is not
proof of authentication.

Map the provider `acr` through a fixed, disjoint allowlist. Unknown or absent ACR
values fail closed. Require a recent `auth_time`, a currently valid and recently
issued provider assertion, and an externally validated recovery generation. The
default global session lifetime is eight hours and cannot extend beyond twelve
hours from authentication. The tenant session defaults to eight hours and cannot
outlive its parent global session.

Generate independent 256-bit opaque credentials for the global and tenant layers.
Return each credential once in a `repr`-hidden field and store only its SHA-256
hash. Retry an exact hash collision at most three times; unrelated integrity
failures do not receive broad retries.

Give `signal_identity` only the columns needed to insert the two session records.
It receives no session update, delete, truncate, schema, or migration authority.
Force row-level security on global sessions. An identity lookup can see only the
row matching the transaction-local global hash, while the established site
authorizer can see the parent row linked to its exact transaction-local tenant
session hash. The identity role can read a tenant session only by that exact hash;
setting tenant context alone does not make other sessions visible. Tenant-session
insertion remains constrained by tenant RLS.

Before issuing a tenant session, recheck the parent credential, recovery
generation, enabled user, active membership, active tenant, and parent expiry in
one transaction. A requested tenant UUID is only a selector; it creates no
authority. Site access remains a separate current-state check.

## Consequences

A verified provider response can now become a revocable, hash-only global session
for an existing user and then a tenant session for one live membership. The
identity role cannot enumerate global or tenant sessions without a matching
credential context. Existing site authorization still works through the newly
forced global-session policy.

Sessions can be issued concurrently with a later administrative restriction. This
does not preserve authority: tenant issuance and every site authorization recheck
current user, membership, tenant, session, and recovery state. Expiry and
revocation management are still internal database state, not user-facing flows.

There is no callback route, browser cookie response, OpenBao PKCE retrieval,
identity invitation service, logout, back-channel logout, session rotation,
step-up flow, rate limiting, or production Keycloak deployment. Provider session
IDs are not yet persisted for logout correlation. The default ACR mapping is a
code-level pilot policy and must become reviewed deployment configuration before
customer authentication is enabled.

## Alternatives

Just-in-time user or membership provisioning was rejected because authentication
must not grant tenant authority. Reusing the provider token as an application
credential was rejected because it would spread provider bearer material and
couple application lifetime to token representation. One global session for all
tenant data was rejected because selecting an organization must be an explicit,
currently authorized transition. Storing raw session values for convenience was
rejected because a business-database read should not directly yield reusable
credentials.

## Verification

The disposable PostgreSQL 17.11 suite has 137 passing cases. Forty-two new cases
cover ACR and time policy, exact identity matching, disabled identities, hash-only
storage, bounded collision handling, forced RLS, exact insert columns, residual
scope rejection, parent and authority invalidation, tenant isolation, expiry and
recovery generation, no authority creation, migration rollback, and composition
with site authorization. The source-hashed report records completed cleanup and
no production authority.
