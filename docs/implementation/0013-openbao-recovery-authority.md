# Slice 0013: OpenBao Recovery Authority

Status: **INTERNAL READ BOUNDARY QUALIFIED; RECOVERY ROTATION IS NOT IMPLEMENTED**.

## Outcome

`signal_core.recovery_authority.OpenBaoRecoveryAuthority` now obtains the current
recovery generation from a storage boundary outside the Signal business database.
The OIDC callback coordinator requires this client and no longer accepts a
caller-supplied generation string.

| Control | Implemented behavior |
| --- | --- |
| Location | Separate `signal-authority` OpenBao KV v2 mount and exact `recovery/current` key |
| Credential | Read only on the exact data path; no wildcard, list, write, delete, or metadata capability |
| Transport | Exact HTTPS origin, mandatory verification, five-second timeout, no redirects or environment proxy |
| Response | 16 KiB maximum; fixed 200 JSON contract; live non-deleted positive KV version |
| Value | One ASCII generation identifier, 1-128 characters; no credential or provider body in errors |
| Login ordering | Authority read succeeds before PostgreSQL callback consumption; outage leaves the attempt retryable |

The shared `signal_core.openbao_http` module now owns the bounded HTTPS and JSON
mechanics used by both the recovery reader and the existing PKCE client. The
refactor does not combine credentials or provider operations.

## Data Boundary

The current generation is read from OpenBao and copied into a successfully issued
hash-only `control.identity_sessions` row. OpenBao's KV metadata version is
validated and returned by the client but is not persisted as authorization truth.
Session authorization continues comparing its stored generation with a current
value supplied by a trusted service boundary.

No recovery generation, OpenBao token, response body, PKCE verifier, provider
token, or raw session token is included in qualification evidence. The generation
is fencing metadata rather than a login secret, but omitting its actual value keeps
the report limited to facts needed for audit.

## Failure Ordering

The callback first validates its local time and authorization-code shape. It then
reads and validates the external generation. Any TLS, network, status, size, JSON,
deletion-state, version, or value failure becomes
`LOGIN_RECOVERY_AUTHORITY_FAILED`. The PostgreSQL attempt is still live, and no
Keycloak, PKCE, or session operation occurs.

After a successful authority read, ADR-0012's one-time PostgreSQL winner and
proof-burning flow applies. A later provider or secret failure still requires a
fresh login.

## Tests And Evidence

Run the gates from the repository root:

```sh
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

The focused authority suite has 33 passing cases. The real OpenBao report records
seven passing TLS-backed scenarios, including an observed read and rejected
reader mutation: [0013-openbao.json](../evidence/0013-openbao.json). The cumulative
155-case PostgreSQL report includes the composed callback behavior:
[0013-postgresql.json](../evidence/0013-postgresql.json).

## Explicit Limits

- No public login, callback, cookie, logout, invitation, or provisioning route exists.
- Generation creation and rotation remain operator and disaster-recovery work;
  this runtime client cannot perform either action.
- No independent authority-restriction journal or end-to-end database restore and
  reconciliation drill is implemented.
- The lab's in-memory OpenBao, generated development TLS, synthetic root token,
  and short-lived scoped tokens are qualification fixtures, not production setup.
- Production OpenBao workload identity, seal/HA/storage design, audit device,
  backup, restore, replication, monitoring, and upgrades remain unimplemented.
- Login success and failure still do not append immutable audit events.

See [ADR-0013](../adr/0013-external-recovery-generation-authority.md) for the
recovery-failure boundary, ordering change, and rejected alternatives.
