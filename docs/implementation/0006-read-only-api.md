# Slice 0006: Truthful Read-Only API Boundary

Status: **IMPLEMENTED AND LOCALLY VERIFIED; NOT A CUSTOMER API**.

> Slice 0018 extends this original read-only baseline with unconfigured OIDC login
> start/callback routes. Customer data and production write APIs remain absent.

## Outcome

`apps/api` now contains the first runnable FastAPI service. It exposes three
read-only endpoints and deliberately has no route that accepts commands, changes
settings, accesses customer data, or contacts a provider.

| Route | Current contract |
| --- | --- |
| `GET /health/live` | Reports only that the API process is responding |
| `GET /health/ready` | Runs an injected dependency probe and returns `503` by default |
| `GET /v1/capabilities` | Reports implemented internal foundations and disabled customer/write capabilities |

The service returns a stable error envelope with a safe message, retry guidance,
and correlation ID. Correlation IDs are accepted only when they match a bounded
safe-token grammar; otherwise the service generates a UUID. Every response is
marked `no-store` and receives restrictive CSP, referrer, content-type, and frame
headers.

Unhandled failures return a generic `500`. The catch-all log records only the
event name, correlation ID, and method; the exception, path, request content, and
stack are not included. This is a conservative first logging contract, not a
complete observability implementation.

## Configuration

`SIGNAL_ENVIRONMENT` accepts only `development`, `test`, or `production`.
`SIGNAL_EXPOSE_API_DOCS` accepts only `true` or `false`. Documentation and the
OpenAPI document are disabled by default, and configuration fails closed if they
are requested in production.

Run the intentionally unready local process from the repository root:

```sh
PYTHONPATH=apps/api/src:services/control_plane/src \
  .venv/bin/uvicorn signal_api.main:app --host 127.0.0.1 --port 8000
```

`/health/ready` returns `503` in this mode by design. Application composition must
inject a real probe before a deployment can use readiness for traffic admission.

## Dependencies

The direct Python input now pins FastAPI 0.141.1, Uvicorn 0.52.4, and HTTPX 0.28.1.
The committed runtime lock pins the resolved transitive graph. HTTPX is currently
used for direct ASGI contract tests; Uvicorn is the local process runner.

The versions are exact but artifact hashes are not yet locked. A future
supply-chain slice must add vulnerability and license reporting rather than
treating version pins as complete dependency assurance.

## Verification

Run the API and lightweight repository gates with:

```sh
.venv/bin/python -m pytest tests/api tests/tooling -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/tooling
.venv/bin/python -m pip check
npm test
```

Observed locally on 2026-09-07:

- 10 API tests passed through the asynchronous ASGI transport with no warnings.
- 8 disposable database-tooling tests passed.
- 9 repository and CI-policy tests passed; documentation checks passed.
- Ruff lint and format checks passed, and `pip check` reported no broken requirements.
- 63 PostgreSQL integration cases passed on PostgreSQL 17.11 after the lock change.

The PostgreSQL runner report is preserved as
[`0006-postgresql-regression.json`](../evidence/0006-postgresql-regression.json).
It hashes the database, control-plane, runner, test, lock, and configuration inputs;
it records `production_authority: false` and `cleanup: completed`.

## Explicit Limits

- There is no Keycloak integration, login, cookie, session issuance, or CSRF boundary.
- There is no public command, approval, recovery, analytics, dashboard, Telegram,
  workflow, model, MCP, GitHub, or CMS endpoint.
- The readiness probe is not connected to PostgreSQL or any other dependency.
- There is no TLS/reverse-proxy policy, rate limiter, body-size middleware, CORS
  policy, telemetry exporter, release build, deployment, or production authority.
- A process response and passing local tests do not establish pilot or GA1 readiness.

See [ADR-0006](../adr/0006-truthful-read-only-api-boundary.md) for the boundary decision.
