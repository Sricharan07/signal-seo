# Slice 0014: Identity Session Audit

Status: **SUCCESSFUL SESSION ISSUANCE IS AUDITED; COMPLETE LOGIN AUDIT IS NOT IMPLEMENTED**.

## Outcome

Migration `0005` adds the first strict `control.platform_events` contract. Every
successful `issue_identity_session` call now commits exactly one
`identity.session.issued` event in the same transaction as its hash-only session.
The returned `IssuedIdentitySession.audit_event_id` supplies a non-secret
correlation reference for future adapters.

| Field | Contract |
| --- | --- |
| `event_type` | Exactly `identity.session.issued` |
| `actor_user_id` | Existing enabled user selected by the verified issuer/subject identity |
| `object_kind` | Exactly `identity_session` |
| `object_id` | Session row whose ID/user pair is validated at insertion |
| `facts` | Exactly `{"schema_version":1,"authentication_level":"primary|mfa"}` |
| `reason` | Null for issuance |
| `created_at` | Database transaction time; not caller writable |

The event intentionally excludes all raw and hashed bearer credentials, provider
claims, browser proofs, contact details, recovery-generation values, and tenant
records. It is immutable through a trigger as well as missing update/delete
privileges.

## Transaction And Access

The issuer sets the new session's token hash as transaction-local scope, inserts
the session, and then inserts its event. Forced RLS verifies that event object and
actor against the hash-visible session. The identity role has only the seven
reviewed insert columns and cannot select the global event table. A failure at
either insert rolls back both records and no raw token is returned.

At this slice the schema rejected every other platform event type. Slice 0015
later added one proof-gated consumed-login failure contract by forward migration;
application code still cannot turn this table into a generic logging endpoint.

## Tests And Evidence

Run from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Five new PostgreSQL cases verify migration rollback, event immutability, strict
facts, hash-scoped insertion, exact column privileges, and atomic rollback on an
event collision. All 160 PostgreSQL cases pass in the isolated PostgreSQL 17.11
lab: [0014-postgresql.json](../evidence/0014-postgresql.json).

## Explicit Limits

- [Slice 0015](0015-consumed-login-failure-audit.md) now records sanitized failures
  after legitimate attempt consumption; initiation and pre-consumption rejects remain absent.
- In this slice, tenant selection, logout, revocation, invitation, and provisioning
  are unaudited. Later slices add invitation events and audited user logout.
- No audit query API, dashboard view, retention/redaction workflow, hash chain,
  signed off-host checkpoint, or operator dispatcher exists.
- There is no public login/callback route or browser cookie.
- This migration grants no production authority and enables no external write.

See [ADR-0014](../adr/0014-atomic-identity-session-audit.md) for atomicity,
least-privilege, data-minimization, and expansion decisions.
