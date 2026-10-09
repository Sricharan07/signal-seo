# Slice 0011: OpenBao PKCE Secrets

Status: **INTERNAL SECRET BOUNDARY QUALIFIED; COMPLETE CUSTOMER LOGIN IS NOT ENABLED**.

## Outcome

`signal_core.pkce_secrets.OpenBaoPkceClient` stores a PKCE verifier outside the
business database and returns only a versioned `secret://` reference. Consumption
reads exactly version one and releases the verifier only after OpenBao confirms
permanent metadata deletion.

| Control | Implemented behavior |
| --- | --- |
| Network | Exact HTTPS origin, mandatory verification, five-second timeout, no redirects or environment proxy |
| Request | UUIDv4 path, RFC 7636 verifier, KV v2 CAS zero, exact version-one read |
| Response | 16 KiB maximum, strict JSON shape, fixed redacted failures |
| Storage | Required CAS, one version, ten-minute soft-deletion deadline, explicit permanent consume |
| Credentials | Create-only writer; read-data/delete-metadata consumer; no list or broad CRUD |

The client deliberately depends on the atomic PostgreSQL login-attempt consumer
from Slice 0008. OpenBao read plus metadata deletion is not independently atomic
under concurrent callers. A callback must first become the sole durable winner;
any later failure abandons that login and requires a new initiation.

## Real OpenBao Lab

The lab uses this digest-pinned image:

```text
ghcr.io/openbao/openbao:2.6.1@sha256:5b2486ab0fb90bbc788cc345b0a08616dfb375873ee8be5df3a2fd4d378a67e0
```

It starts OpenBao in memory with generated development TLS, a tmpfs certificate
directory, an ephemeral loopback port, 384 MiB memory, one CPU, 128 PIDs, and
`no-new-privileges`. The generated private key remains inside invocation-owned
tmpfs; only the public CA is copied into a private runtime directory for local
verification. A synthetic root token provisions two nonrenewable 15-minute test
tokens with disjoint policies. All container and certificate material is removed
after the run.

The real server passed five scenarios:

1. TLS KV v2 bounded mount configuration.
2. CAS-zero PKCE verifier creation.
3. Separate writer and consumer ACL enforcement.
4. Confirmed permanent consume and sequential replay rejection.
5. Conflicting overwrite rejection with the original verifier preserved.

Reviewed evidence is [0011-openbao.json](../evidence/0011-openbao.json). The
complete 137-case PostgreSQL identity and tenant-isolation regression is recorded
separately in
[0011-postgresql-regression.json](../evidence/0011-postgresql-regression.json).

## Verification

Run from the repository root:

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

This slice adds 48 client cases and 11 runner/policy cases. The full non-database
suite has 164 passing cases; repository checks have 11 passing cases; and 36
Markdown files pass documentation validation. The source-hashed real OpenBao
report contains five passing scenarios and explicitly records that no token or
secret material and no production authority were included. The source-hashed
PostgreSQL report contains 137 passing cases and records completed cleanup.

## Explicit Limits

- The durable login-attempt, OpenBao secret, Keycloak exchange, token validation,
  and session issuer are not yet composed into login/callback API routes.
- No production OpenBao cluster, seal or auto-unseal, workload authentication,
  audit device, replication, backup, restore, monitoring, or upgrade is configured.
- The development server, root token, generated CA, and in-memory storage are lab
  fixtures only and must never be reused in production.
- Automatic expiry is a soft delete. Normal consumption permanently deletes key
  metadata, but production storage-media retention needs a separate policy.
- Process memory cannot be reliably zeroized by Python.
- The API reports only an internal capability; customer authentication is disabled.

See [ADR-0011](../adr/0011-openbao-pkce-secret-boundary.md) for failure semantics,
trust boundaries, reviewed sources, and rejected alternatives.
