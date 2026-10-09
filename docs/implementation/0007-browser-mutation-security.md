# Slice 0007: Browser Mutation Security

Status: **IMPLEMENTED PRIMITIVE; NO CUSTOMER MUTATION ROUTE**.

## Outcome

The API now has an explicit dependency for proving that a state-changing browser
request possesses the opaque session cookie and its matching CSRF proof and came
from an exact trusted origin. The application has no state-changing product route,
so this slice adds protection before authority rather than retrofitting it later.

The browser contract provides:

- `__Host-signal_session` issue and clear helpers with Secure, HttpOnly,
  SameSite=Lax, root path, and no Domain attribute;
- HMAC-SHA-256 CSRF tokens bound to the opaque session token and a secret key;
- constant-time proof comparison;
- strict Origin and optional Fetch Metadata validation;
- rejection of repeated security headers and repeated session-cookie values; and
- stable non-disclosing `403` and fail-closed `503` API errors.

Opaque session validation and hashing now live in
`signal_core.session_tokens`. The database authorizer uses the same validator and
continues to persist only a SHA-256 token hash.

## Integration Contract

A future cookie-authenticated mutation route must include
`require_browser_mutation` and then pass the returned raw opaque token directly to
the trusted server-side authorization service. The proof object hides the token
from its representation. It must never be logged, serialized, put in a URL, sent
to a model, or treated as tenant/site permission.

Trusted origins are exact ASCII HTTP(S) origins. Non-loopback HTTP, wildcards,
userinfo, paths, query strings, fragments, surrounding whitespace, and invalid
ports are rejected. HTTPS remains mandatory outside `localhost`, `127.0.0.1`, and
`::1`.

The HMAC key must be at least 32 bytes and supplied through a later secret-backed
composition layer. There is intentionally no hard-coded development or production
key. Without configured browser security, a route using the dependency returns
`503 BROWSER_SECURITY_NOT_READY`.

## Verification

Observed locally on 2026-09-07:

- 36 API tests passed, including 26 browser-security cases.
- 63 PostgreSQL 17.11 integration cases passed after token validation was shared.
- The disposable PostgreSQL project was removed after the run.

The regression report is preserved as
[`0007-postgresql-regression.json`](../evidence/0007-postgresql-regression.json).
It hashes the updated control-plane source and records
`production_authority: false` and `cleanup: completed`.

Run the relevant checks with:

```sh
.venv/bin/python -m pytest tests/api -q
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m ruff check apps/api services/control_plane tests/api tests/control_plane
.venv/bin/python -m ruff format --check apps/api services/control_plane tests/api tests/control_plane
```

## Explicit Limits

- This is CSRF/request-possession proof, not authentication or authorization.
- Keycloak OIDC, PKCE, state/nonce binding, login transactions, and session
  issuance are not implemented.
- No state-changing application route is exposed.
- There is no CORS policy, production key provisioning, TLS/proxy deployment,
  request rate limit, or external security assessment.
- The original customer authentication and production-write capabilities remain
  disabled in `/v1/capabilities`.

See [ADR-0007](../adr/0007-session-bound-browser-mutation-proof.md) for rationale and rejected alternatives.
