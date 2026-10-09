# Slice 0012: OIDC Login Composition

Status: **INTERNAL FLOW IMPLEMENTED; CUSTOMER AUTHENTICATION REMAINS DISABLED**.

Slice 0013 replaces this slice's caller-supplied recovery-generation placeholder
with a qualified external OpenBao read and performs it before callback consumption.

## Outcome

`signal_core.login_flow` composes the four identity foundations into one internal
initiation/callback contract:

1. Validate registration, local return path, TTL, UUIDv4 identity, and independent
   state, nonce, browser-binding, and verifier entropy.
2. Validate exact Keycloak metadata and build an S256 authorization request.
3. Store the verifier at the matching OpenBao UUID path with CAS zero.
4. Commit only state, nonce, and browser-binding hashes plus the secret reference
   in PostgreSQL.
5. On callback, atomically consume the two browser proofs before external work.
6. Validate exact provider metadata and public JWKS.
7. Permanently consume the verifier, exchange the code, and validate the signed
   ID token, issuer, audience, nonce, times, authorized party, access-token hash,
   and configured ACR.
8. Issue a hash-only global session only for an existing enabled issuer/subject
   identity under the current external recovery generation.

The PostgreSQL calls use AnyIO worker threads rather than blocking the async event
loop. Result and exception representations contain no authorization URL, browser
binding, PKCE verifier, provider token, or raw session token.

## Durable Identity

`create_oidc_login_attempt` now accepts an optional validated UUIDv4 identifier.
The coordinator uses that identifier for both the PostgreSQL attempt and the
versioned `secret://oidc-login/<uuid>/1` OpenBao reference. Existing callers may
still omit it and receive a generated UUIDv4 value.

The return-path and TTL validators and authorization-code validator are public
internal helpers so the coordinator can reject unsafe input before creating or
consuming durable state. Recovery-generation validation is similarly shared with
the session issuer.

## Tests And Evidence

The new flow adds 17 real PostgreSQL cases. They include the complete successful
path through mocked network boundaries, strict operation ordering, invalid local
policy and entropy, discovery and secret failures, state collision, wrong browser
binding, invalid external recovery generation, missing verifier, invalid signed
token, unknown identity, and sequential callback replay.

Run the verification gates from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

The 154-case PostgreSQL report is
[0012-postgresql.json](../evidence/0012-postgresql.json). The refreshed real
Keycloak protocol report is
[0012-keycloak-regression.json](../evidence/0012-keycloak-regression.json).
Both reports bind their results to source hashes and record no production
authority with completed cleanup.

## Explicit Limits

- No login or callback HTTP route exists and no browser cookie is issued.
- The browser binding has a tested internal value contract but is not yet attached
  to a host-only cookie by an HTTP adapter.
- No invitation, user provisioning, membership provisioning, or tenant-selection
  route exists; token claims never create authority.
- At completion of this historical slice, the external recovery reader was not
  implemented; Slice 0013 now provides the internal read boundary, while rotation
  and full disaster-recovery orchestration remain unimplemented.
- Login success/failure does not yet append an immutable audit event.
- Keycloak and OpenBao have real isolated labs, but this slice does not claim a
  combined real three-service environment or production deployment.
- A process failure after session commit but before response delivery can leave an
  inaccessible session that must expire; it cannot authorize without its raw token.

See [ADR-0012](../adr/0012-fail-closed-oidc-login-composition.md) for ordering,
failure handling, and rejected alternatives.
