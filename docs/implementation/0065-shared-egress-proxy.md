# Slice 0065: Shared Egress Proxy

Status: **IMPLEMENTED AND LOCALLY QUALIFIED; PRODUCTION COMPOSITION UNAVAILABLE**.

## Objective

Put crawler, browser, connector, and model-provider HTTP behind one reviewed
outbound boundary. Every request must use public-address screening, numeric-peer
pinning, current robots evidence, and the existing global origin admission before
network I/O. Dispatch and terminal outcomes must be durable and auditable without
storing credentials or request bodies.

This is a security-critical slice under Revision 4.0 INV-029 and EC-133.

## Implemented

- Added a strict shared request contract for GET, HEAD, and POST. Requests are
  bounded to 1 MiB, responses to at most 5 MiB, and time to 0.25–60 seconds. Only
  `accept`, `authorization`, and `content-type` can be supplied. Cookie headers,
  control characters, unsupported methods, GET/HEAD bodies, duplicate headers,
  compression, and unlisted media types fail closed.
- Extended the pinned HTTP boundary with bounded POST and HEAD support. It resolves
  once, rejects the whole answer set if any address is not public, connects to a
  numeric peer, verifies the connected peer, preserves TLS SNI, sends identity
  encoding, enforces an absolute response deadline, and never follows a redirect.
  POST uses exactly one screened peer and is never repeated after an ambiguous
  transport failure. A redirect retains the status but not its potentially
  sensitive destination header, and causes no second resolution or connection.
- Added a durable gateway that requires a current running crawl/workflow authority,
  exact admitted origin, and current robots snapshot before using the same global
  origin bucket as crawler traffic. Browser and crawler purposes cannot carry
  credentials or POST bodies.
- Added migration `0036` with immutable forced-RLS `app.egress_operations` records
  and function-only admission/completion operations. Admission stores the exact
  request and body digests, credential-presence flag, robots snapshot and decision,
  origin permit, and dispatch time before I/O. Completion stores sanitized response
  evidence and atomically releases capacity or applies provider backoff.
- Exact terminal replay never performs network I/O again. An unresolved dispatch
  returns an explicit uncertain state and is never retried blindly. Conflicting
  operation identity, expired permits, stale authority, inconsistent completion,
  or direct mutation fail closed.
- Added a provider JSON capability on top of the gateway. It returns a bounded body
  only after durable completion and exposes sanitized typed failure codes for
  denial, deferral, uncertainty, state outage, response rejection, and conflict.
- Removed direct public HTTP from both the Jev adapter and its labelled fallback.
  Both now require injected shared egress, preserve fixed endpoints and response
  validation, and use stable distinct UUIDv4-shaped operation identities. Missing
  egress is visibly unavailable and enters the existing conservative fallback.
- Updated the live Jev qualification command to require an exact TypeSafe-only
  shared-egress context plus the least-privilege admission and ingest roles. The API
  key is still read silently through standard input and is never accepted through
  an argument, environment variable, context file, record, or log.

## Durable Data And Authority

`app.egress_operations` stores tenant, site, running crawl, worker, purpose, method,
canonical URL and origin, request and body SHA-256 digests, byte limits,
credential-presence only, exact robots snapshot/reason, permit identity, dispatch
time, terminal network class, HTTP status, sanitized headers, response digest and
size, media type, public peer address, latency, retry-after, and completion class.

The operation row and global permit are created atomically by
`control.begin_shared_egress_operation`. The terminal row and permit release are
committed atomically by `control.finish_shared_egress_operation`. Runtime roles have
no direct table access. Forced RLS and the mutation trigger keep identity and
terminal facts immutable. Credentials, request bodies, and response bodies are not
stored in PostgreSQL.

## Failure Semantics

- Private, loopback, link-local, reserved, multicast, unspecified, or mixed unsafe
  DNS answers are rejected before connection.
- Redirects are never followed by shared egress. The exact status and sanitized
  location header may be recorded, but no redirected authority is exercised.
- Robots denial or unavailable current evidence prevents dispatch. Provider
  backoff, in-flight capacity, and politeness return explicit deferral.
- A lost acknowledgement after dispatch remains `dispatch_unknown`; neither the
  provider adapter nor gateway repeats the call.
- Transport and deterministic policy failures become terminal failed records and
  release capacity. HTTP 429 and 503 update the shared origin backoff.
- Unknown internal consistency failures are not converted into successful provider
  responses. Inconsistent completion evidence changes neither operation nor permit.

## Verification

```sh
.venv/bin/pytest -q \
  tests/tooling/test_crawl_http.py \
  tests/tooling/test_jev_decisions.py \
  tests/tooling/test_jev_qualification.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/python scripts/openbao_lab.py
.venv/bin/ruff check apps services scripts tests
.venv/bin/ruff format --check apps services scripts tests
.venv/bin/pip check
npm test
npm audit --omit=dev
```

The focused boundary suite passes 87 cases. The isolated Docker network lab passes
10 real-network cases, including a credentialed POST and a private redirect with no
second connection. The PostgreSQL 17.11 result and complete repository counts are
recorded in [the slice evidence](../evidence/0065-shared-egress-proxy.json).

## Live Qualification Context

The Jev command accepts no network bypass. Before running it, the application must
prepare a current running crawl authority whose only allowed origin is
`https://api.typesafe.ai`, persist current robots evidence for that origin, and
write a secret-free context file with exactly this shape:

```json
{
  "schema_version": 1,
  "artifact_root": "/absolute/private/artifact/root",
  "run": {
    "tenant_id": "uuid",
    "site_id": "uuid",
    "command_id": "uuid",
    "run_id": "uuid",
    "root_frontier_id": "uuid",
    "root_url_id": "uuid",
    "first_run_id": "opaque-run-id",
    "started_at": "RFC-3339 timestamp with offset",
    "status": "running"
  },
  "policy": {
    "schema_version": 1,
    "allowed_origins": ["https://api.typesafe.ai"],
    "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
    "max_redirects": 0,
    "max_body_bytes": 131072,
    "request_timeout_seconds": 20,
    "total_timeout_seconds": 20
  }
}
```

Set `SIGNAL_JEV_EGRESS_CONTEXT` to that file and set the admission and ingest DSNs
through `SIGNAL_JEV_EGRESS_ADMISSION_DSN` and `SIGNAL_JEV_EGRESS_INGEST_DSN`.
Those DSNs are runtime secrets and do not belong in the context file or repository.
The provider key is still entered only at the silent prompt.

## Limits And Next Work

No production egress service, resolver deployment, browser container, connector
binding, provider credential, or autonomous authority is enabled. The live Jev call
remains `NOT_EXECUTED`; the qualification command additionally requires a prepared
running authority and current TypeSafe robots evidence. Fetched robots evidence
that requires an artifact decryption key is not yet supported by that standalone
command, so it fails closed unless the current snapshot is an allowed missing-file
decision.

The gateway is currently an application-level library boundary, not a separately
deployed network choke point. A process with unrelated direct-network code is not
made safe merely by this module; each future connector and the browser worker must
be composed so the shared capability is their only public HTTP path. Slice 0066
next composes the full-site crawl and settles frontier coverage and budgets.
