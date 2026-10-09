# Slice 0021: Purpose-Bound Invitation Identity Proofs

Status: **SUPERSEDED FOR CONSUMPTION BY SLICE 0022 AND BROWSER COMPOSITION BY SLICE 0023**.

## Outcome

The identity boundary can now distinguish ordinary login from a future
invitation-acceptance OIDC transaction and issue a separate short-lived proof from
a fresh provider-verified identity. This proof is not a Signal user session and
grants no tenant, site, connector, or write authority.

| Component | Implemented behavior |
| --- | --- |
| OIDC attempt purpose | Immutable `login` or `invitation_acceptance`, bound before redirect and returned after one-time callback proof consumption |
| Invitation identity proof | Random 256-bit browser credential, hash-only persistence, exact verified identity projection, maximum ten-minute lifetime |
| Runtime authority | Hash-scoped insert/read only; no direct update, delete, truncate, migration, user, membership, or invitation mutation |

Existing attempt callers default to `login`, preserving the deployed internal
contract. An invitation-specific caller must opt in before provider redirection;
callback input cannot change the stored purpose.

## Data And Privacy

Migration `0011` adds `control.invitation_identity_proofs`. Each row stores the
credential hash, exact OIDC issuer and subject, normalized verified email, signed
identity issuance and expiry, proof expiry, creation time, and an internal UUID.
It stores no raw credential, provider token, provider session identifier, PKCE
secret, invitation bearer, tenant, site, role, or permission.

The table forces hash-scoped RLS. `signal_identity` can set the exact proof hash
for one transaction, insert only reviewed fields, and read only the matching row.
It receives no mutation privilege. The shared clean-connection guard now rejects
a pooled connection carrying residual proof scope.

Provider identity must be a previously validated `VerifiedOidcIdentity` with a
restricted normalized email. Missing, differently normalized, control-bearing,
stale, future, expired, reversed, or noninteger identity claims fail before any
database access. Token and UUID collisions retry at most three times and never
return a colliding secret.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Twenty new PostgreSQL cases bring the real PostgreSQL 17.11 suite to 270. The API
suite remains at 106 and the complete non-database Python suite remains at 271.
Reviewed source-hashed evidence is
[0021-postgresql.json](../evidence/0021-postgresql.json); it records the pinned
image, completed cleanup, and no production authority.

## Explicit Limits

- Slice 0022 later adds exact atomic consumption and a bounded cleanup primitive;
  slice 0023 later adds the dedicated browser proof and acceptance contract. The
  default application still configures neither path for customer use.
- No deployed expiry cleanup schedule exists. No production path may enable
  issuance until cleanup is configured and monitored.
- The verified identity projection contains sensitive email and provider
  identifiers. It must never be logged, returned in a response, or exposed to a
  model or connector.
- Invitation delivery, revocation, throttling, account lock policy, Keycloak user
  creation, and production identity credentials remain absent.
- There is no dashboard, Telegram surface, workflow engine, reasoning agent,
  human approval flow, undo operation, customer connector, or production write.

See [ADR-0021](../adr/0021-purpose-bound-invitation-identity-proofs.md) for the
separate-proof, purpose-binding, storage, and atomic-consumption decisions.
The successor [slice 0022](0022-atomic-invitation-proof-acceptance.md) implements
that atomic consumption and narrows the runtime acceptance grant.
