# ADR-0025: Expose Harmless Human Commands Through Bounded HTTP Ingress

- Status: Accepted
- Date: 2026-09-08
- Owners: API ingress, identity authorization, and command service

## Context

Slice 0024 can atomically authorize and persist one human-attributed
`site.snapshot` command, but it intentionally exposes no browser route. A browser
adapter must not treat a site UUID, user field, cookie contents, or prior command
acceptance as authority. It also must not turn retries into duplicate durable
intent or leak whether another actor or site owns a command ID.

The snapshot command has no request options yet. Accepting arbitrary JSON would
create an undocumented input surface and make future idempotency semantics
ambiguous. Returning only a command ID would also leave clients without a stable,
bounded way to recognize exact retries or inspect the accepted intent.

## Decision

Expose `POST /v1/sites/{site_id}/commands/snapshot` as a 202 Accepted operation.
It requires the exact host-only tenant-session cookie, trusted Origin, same-origin
fetch metadata, a session-bound HMAC CSRF proof, and exactly one
`Idempotency-Key` header. The key is an ASCII token of 1-128 characters. The JSON
body is limited to 256 bytes and must be exactly `{"schema_version":1}`; duplicate
keys, additional fields, encoded bodies, malformed UTF-8, and non-JSON content are
rejected before command acceptance.

The API passes only the opaque session, path site UUID, and idempotency key to the
composed gateway. The gateway reads the independent current recovery generation
before opening a clean autocommit database connection and invokes the existing
atomic database contract. The response is a versioned projection with command and
site IDs, `accepted` status, acceptance time, exact-retry indicator, correlation
ID, and a relative status URL. The same URL is returned in `Location`.

Expose `GET /v1/sites/{site_id}/commands/{command_id}` for the bounded accepted
status. It requires one exact tenant-session cookie but not CSRF because it is
read-only. The database repeats current session and site authorization and returns
only a human snapshot command owned by the current actor. Missing, inaccessible,
wrong-site, and wrong-actor records are not enumerated through direct table reads.

Both routes remain `internal_only` and fail closed with 503 unless a reviewed
gateway is injected. Stable errors distinguish invalid session, authorization
denial, idempotency conflict, missing command, invalid input, and malformed trusted
gateway output without exposing credentials or internal exception details.

## Alternatives

- Accept the command from query parameters or a generic command envelope.
  Rejected because credentials and future options need explicit, separately
  reviewed contracts rather than a broad polymorphic surface.
- Let the browser provide tenant or actor identity. Rejected because opaque
  session possession must be resolved and reauthorized server-side.
- Return 200 and perform the snapshot inline. Rejected because durable acceptance
  is not execution or completion.
- Make status readable with only a command ID. Rejected because identifiers are
  not authorization and current authority can be reduced after acceptance.
- Retry without an idempotency key. Rejected because network retries could create
  duplicate durable work.

## Consequences

- A browser can now submit and inspect harmless durable intent through a narrow,
  versioned API without gaining direct database or provider access.
- An exact retry is observable as `reused=true`; a changed target under the same
  key returns a generic conflict.
- Status is deliberately limited to `accepted`. No running, completion, result,
  cancellation, approval, or provider behavior is implied.
- The API process still does not construct a customer gateway by default, so this
  route is not production-enabled.
- Future command kinds and request options require new contracts and idempotency
  review rather than silent expansion of this body.

## Verification

The API suite covers successful acceptance, exact retry, status projection,
Origin/CSRF/fetch/cookie proof failures, strict body and header bounds, duplicate
inputs, stable authorization and conflict mappings, credential non-reflection,
gateway output validation, fail-closed default composition, recovery-authority
ordering, clean connection lifecycle, and the exact OpenAPI mutation inventory.
All 158 API and 323 non-database Python cases pass. All 314 real PostgreSQL 17.11
cases continue to exercise the atomic authority and durability boundary under
non-owner roles; cleanup completed with no production authority.
