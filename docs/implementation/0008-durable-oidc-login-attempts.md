# Slice 0008: Durable OIDC Login Attempts

Status: **INTERNAL FOUNDATION IMPLEMENTED; LOGIN IS NOT IMPLEMENTED**.

## Outcome

Forward-only migration `0003` adds one global pre-tenant table:

| Table | Stored data | Excluded data |
| --- | --- | --- |
| `control.oidc_login_attempts` | State, nonce, and browser-binding hashes; fixed OIDC registration; PKCE secret reference; local return path; expiry and consumption time | Raw browser values, PKCE verifier, authorization code, provider token, user password |

`create_oidc_login_attempt` validates all public inputs, sets transaction-local
state and browser-binding scopes, and inserts a short-lived immutable row through
the non-owner `signal_identity` role. `consume_oidc_login_attempt` uses both raw
proofs to set those scopes and atomically marks one unexpired row consumed.
`ConsumedOidcLoginAttempt.validate_nonce` compares the validated ID token's nonce
to the stored hash without putting the expected hash or secret reference in the
object representation.

## Database Enforcement

- Forced RLS requires both state and browser-binding hashes.
- The identity role cannot list attempts when either scope is absent.
- The identity role may select and insert matching rows and update only the
  `consumed_at` column.
- A trigger rejects mutation of every other field and a second consumption.
- State is unique, expiry must follow creation, and consumption must fall within
  the attempt lifetime.
- The migration is atomic; an injected collision leaves revision `0002` intact.
- All OIDC scope is transaction-local and pooled-connection contamination is
  rejected before work starts.

## Runtime Boundary

The PKCE verifier must eventually be generated cryptographically and written to
OpenBao before this row is created. Only the resulting `secret://` reference is
accepted here. The future callback retrieves and deletes that secret after the
one-time database consume, exchanges the code using a maintained OIDC client,
validates signature/issuer/audience/time/nonce, and only then creates identity and
application sessions.

No such provider or secret-manager integration exists in this slice. The
capability inventory reports `identity.oidc_login_attempts` as `internal_only` and
continues to report `customer.authentication` as `disabled`.

## Verification

Observed locally on 2026-09-07:

- 95 PostgreSQL 17.11 cases passed under non-owner runtime roles.
- 32 cases are new for OIDC persistence, boundaries, concurrency, and migration safety.
- 36 API cases continue to pass after the truthful capability inventory update.
- The disposable database project was removed after the successful run.

The complete report is preserved as
[`0008-postgresql.json`](../evidence/0008-postgresql.json). It includes exact
source hashes, `production_authority: false`, and `cleanup: completed`.

Run the relevant checks with:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations tests/api tests/control_plane
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations tests/api tests/control_plane
```

## Explicit Limits

- Keycloak and OpenBao are not connected.
- There is no authorization redirect or callback route.
- No code exchange, JWKS/signature validation, user provisioning, application
  session issuance, logout, MFA, account recovery, or transactional email exists.
- A secret-manager write failure/orphan cleanup procedure and expired-row cleanup
  task remain to be implemented.
- This slice has no customer authentication or production authority.

See [ADR-0008](../adr/0008-durable-pre-tenant-oidc-attempts.md) for the persistence and isolation decision.
