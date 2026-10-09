# Slice 0009: Keycloak OIDC Protocol Qualification

Status: **INTERNAL PROTOCOL IMPLEMENTED; CUSTOMER LOGIN IS NOT IMPLEMENTED**.

## Outcome

`signal_core.oidc_protocol` now provides a narrow Keycloak protocol boundary:

| Operation | Enforced behavior |
| --- | --- |
| Discovery | Fixed issuer URL, exact derived endpoints, no redirects/proxy, bounded JSON, mandatory code/S256/RS256 support |
| Authorization request | Exact public client, redirect, state, nonce, `openid email` scope, and S256 challenge |
| Code exchange | Authlib 1.8.0 over HTTPX2 2.12.0, five-second timeout, no redirects/proxy, bounded bearer response |
| JWKS | Public RSA signature keys only, RS256 only, bounded count and key IDs, no private parameters |
| ID token | Signature, issuer, audience/`azp`, subject, expiry, issued-at, nonce, and optional `at_hash` |

Token values use frozen fields excluded from `repr`; no provider error body is
included in the fixed protocol exceptions. The validator returns only a bounded
verified identity projection. It does not return access, refresh, or ID tokens.

## Real Provider Lab

The lab imports one synthetic realm, public client, and user into this image:

```text
quay.io/keycloak/keycloak:26.7.3@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
```

It generates per-run TLS material, publishes only `127.0.0.1::<ephemeral>` for
container port 8443, limits the container to 768 MiB, one CPU and 256 PIDs, and
uses `no-new-privileges`. Cleanup selects the invocation by both label and exact
name. The evidence report contains source hashes and scenario names, never the
synthetic password, authorization code, PKCE verifier, or provider token.

The real Keycloak 26.7.3 run passed five scenarios:

1. Exact discovery and public RS256 JWKS.
2. Authorization code with S256 PKCE and a completely validated signed ID token.
3. Authorization-code replay rejection.
4. Wrong PKCE verifier rejection.
5. Wrong ID-token nonce rejection.

Reviewed evidence is [0009-keycloak.json](../evidence/0009-keycloak.json).
The unchanged persistence boundary also passed all 95 PostgreSQL 17.11 cases;
that regression is [0009-postgresql-regression.json](../evidence/0009-postgresql-regression.json).

## Verification

Run from the repository root:

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

The local API/identity/tooling suite has 105 passing cases: 36 existing API cases,
50 OIDC protocol cases, and 19 disposable-runner cases. The real PostgreSQL
regression has 95 passing cases under non-owner roles. CI runs all of these gates
without repository write or deployment authority.

## Explicit Limits

- The protocol is internal code and has no login or callback API route.
- The PKCE verifier is not yet written to or deleted from OpenBao.
- The validated identity is not provisioned and no server-side session is issued.
- Keycloak production deployment, persistent database, TLS, hostname/proxy
  configuration, backups, MFA, logout, recovery, and upgrades are not configured.
- JWKS caching and bounded refresh-on-unknown-key are not implemented.
- Python does not guarantee process-memory zeroization of ephemeral strings.
- This slice has no customer authentication, production credential, or write authority.

See [ADR-0009](../adr/0009-keycloak-oidc-protocol.md) for the provider-specific and
cryptographic decisions.
