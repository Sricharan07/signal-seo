# Slice 0015: Consumed Login Failure Audit

Status: **POST-CONSUMPTION LOGIN FAILURES ARE AUDITED; PUBLIC LOGIN IS NOT ENABLED**.

## Outcome

Migration `0006` expands the strict append-only platform-event contract with
`identity.login.failed`. The internal callback coordinator records this event
only after a stored OIDC attempt has been selected with both original proofs and
atomically consumed.

| Field | Contract |
| --- | --- |
| `event_type` | Exactly `identity.login.failed` |
| `actor_user_id` | Null; no identity is assumed before a verified assertion and authorized account |
| `object_kind` | Exactly `oidc_login_attempt` |
| `object_id` | Existing consumed attempt selected under original two-proof scope |
| `facts` | Exactly `{"schema_version":1}` |
| `reason` | One of six fixed internal failure categories |
| `created_at` | Database transaction time; not caller writable |

The event stores no callback state, browser binding, nonce, authorization code,
PKCE reference or verifier, provider token or body, exception text, contact data,
or guessed actor. Existing `identity.session.issued` shape and atomic issuance
behavior remain unchanged.

## Failure Boundary

The callback reads recovery authority before consuming the login attempt. A
malformed callback, wrong browser binding, replay, or recovery-authority outage
therefore creates no durable event. This prevents anonymous requests from using
the audit table as an unbounded write endpoint.

Once the attempt is consumed, provider configuration, missing or unconfirmed
PKCE consumption, invalid provider assertion, unknown identity, and session
persistence failures map to closed reason values. The append uses a separate
clean transaction because external provider and OpenBao operations cannot be
atomic with PostgreSQL. If the append fails, the coordinator returns only
`LOGIN_CALLBACK_AUDIT_FAILED`; it never claims the original failure was audited.

Forced RLS requires the original state and browser-binding hashes, an insert
trigger requires the referenced attempt to be consumed, and the existing unique
object/type key limits the path to one failure event per attempt. The identity
role receives no additional table or column privileges and still cannot read the
global event stream.

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

Eleven new cases verify migration rollback, strict and sanitized event shape,
closed reason validation before database access, consumed-object integrity,
original-proof scope, one event per attempt, each composed stage mapping, and
explicit audit failure. All 171 PostgreSQL cases pass in the isolated PostgreSQL
17.11 lab: [0015-postgresql.json](../evidence/0015-postgresql.json).

Regression verification also passes 197 non-database Python cases, 11 Node
repository cases, 44 Markdown files, seven real OpenBao scenarios, five real
Keycloak scenarios, Ruff lint/format checks, and dependency consistency. The real
service regressions use only synthetic invocation-owned credentials and confirm
cleanup; they grant no production authority.

## Explicit Limits

- Login initiation and pre-consumption callback rejects do not append durable events.
- A database outage after consumption can burn an attempt before its event commits;
  the fixed audit-failure response exposes but cannot atomically repair that gap.
- In this slice, tenant selection, logout, revocation, invitation, and provisioning
  are unaudited. Later slices add invitation events and audited user logout.
- No audit query API, dashboard view, export, retention/redaction workflow, hash
  chain, signed off-host checkpoint, reconciliation worker, or dispatcher exists.
- There is no public login/callback route or browser cookie.
- This migration grants no production authority and enables no external write.

See [ADR-0015](../adr/0015-proof-gated-consumed-login-failure-audit.md) for the
proof gate, data-minimization, transaction-boundary, and least-privilege decisions.
