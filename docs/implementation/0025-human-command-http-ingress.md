# Slice 0025: Human Command HTTP Ingress

Status: **IMPLEMENTED AS AN UNCONFIGURED API CONTRACT; NO EXECUTION**.

## Outcome

Signal now exposes the Slice 0024 harmless snapshot intent through authenticated
browser routes. Acceptance remains asynchronous and durable: a successful HTTP
response means the command, acceptance event, and outbox record committed. It does
not mean a crawl ran, a workflow started, or a provider changed.

| Boundary | Implemented behavior |
| --- | --- |
| Mutation proof | Exact tenant cookie, trusted Origin, same-origin fetch metadata, and session-bound HMAC CSRF |
| Request | 256-byte strict JSON containing only `schema_version: 1` |
| Retry | Exactly one bounded `Idempotency-Key`; exact retry returns the original command with `reused=true` |
| Authority | Gateway reads current external recovery generation, then the database derives actor and exact site scope atomically |
| Acceptance | Returns 202, a relative status URL in body and `Location`, acceptance time, and correlation ID |
| Status | Exact tenant cookie plus repeated live database authorization; returns only the actor's accepted snapshot intent |
| Deployment | Capability is `internal_only`; the default process injects no gateway and returns 503 |

## API Contract

`POST /v1/sites/{site_id}/commands/snapshot` accepts only:

```json
{"schema_version": 1}
```

The body parser rejects duplicate keys, extra properties, malformed UTF-8,
non-object JSON, content encoding, non-JSON media types, and bodies over 256 bytes.
The endpoint also rejects missing, duplicate, malformed, or overlong idempotency
headers before invoking the command gateway.

On success the response is a versioned projection containing `command_id`,
`site_id`, `status`, `status_url`, `reused`, `accepted_at`, and `correlation_id`.
Session failures clear the stale tenant cookie. Authorization denial, command
absence, and idempotency collision use stable non-disclosing errors.

`GET /v1/sites/{site_id}/commands/{command_id}` is intentionally read-only and
does not require CSRF. It does require one exact tenant cookie, and the underlying
database function rechecks the current session, recovery generation, user,
membership, site grant, tenant, site, and actor ownership.

## Composition

`BrowserCommandGateway` is the narrow API dependency. `ComposedBrowserLogin`
implements it using the same connection lifecycle as the identity routes: fetch
the independent OpenBao recovery generation, acquire one validated autocommit
connection, run one synchronous database operation in a worker thread, and close
or discard the connection on every path.

The API never accepts browser-provided tenant or actor IDs and never receives a
raw command-table capability. The default module-level application remains
unconfigured so importing or starting it cannot grant customer command authority.

## Verification

```sh
.venv/bin/python -m pytest tests/api -q
.venv/bin/python scripts/run-database-tests.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

The new HTTP and composition coverage includes accepted and reused responses,
bounded status, every browser proof dimension, strict parser failures, exact
idempotency headers, safe error mappings, output revalidation, recovery-before-DB
ordering, and closed default configuration. All 158 API and 323 non-database
Python cases pass.

All 314 real PostgreSQL 17.11 cases also pass, retaining the Slice 0024 atomicity,
RLS, privilege, authority-reduction, and concurrency coverage. Reviewed
source-hashed evidence is [0025-postgresql.json](../evidence/0025-postgresql.json);
cleanup completed and `production_authority` is false. The evidence contains the
same two known generic-key secret-scan false positives as Slice 0024: source-path
keys ending in `pkce_secrets.py` and `session_tokens.py` whose values are SHA-256
hashes, not credentials.

The repository suite passes all 11 cases and validates 64 Markdown files. Ruff
checks 73 Python files, format verification passes, and the pinned environment has
no broken Python requirements.

## Explicit Limits

- No dispatcher, inbox consumer, Temporal workflow, crawl, progress transition,
  result artifact, cancellation, or event stream exists.
- Snapshot acceptance is harmless intent, not an approval and not provider write
  authority.
- The gateway, browser-security key, database credential, and recovery credential
  are not composed into the default process or a deployment.
- No dashboard, Telegram command path, rate limit, customer enablement, or
  production operations exist.
- Invitation delivery, abuse controls, deployed cleanup, recovery rotation,
  restriction replay, and restore reconciliation remain required.

See [ADR-0025](../adr/0025-bounded-human-command-http-ingress.md) for the ingress,
retry, authorization, and status decisions.
