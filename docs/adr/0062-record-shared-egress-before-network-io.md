# ADR-0062: Record Shared Egress Before Network I/O

- Status: Accepted
- Date: 2026-09-26
- Owners: Signal network, connector, and evidence boundaries
- Related: [ADR-0034](0034-pin-crawl-connections-to-admitted-public-addresses.md), [ADR-0037](0037-persist-robots-before-page-admission.md), [ADR-0038](0038-admit-every-crawl-request-through-a-global-origin-bucket.md), [ADR-0039](0039-record-dispatch-before-composing-crawl-page-io.md), [ADR-0061](0061-isolate-jev-behind-a-recommendation-only-boundary.md), Revision 4.0 INV-029 and EC-133

## Context

Signal already has reviewed public-address screening, numeric-peer pinning, robots
evidence, and global origin admission for crawl traffic. Connector, browser, and
model-provider clients could otherwise reimplement HTTP independently and bypass
one or more of those controls. Ordinary forward HTTP proxies also cannot reliably
persist the exact application operation, robots snapshot, authority, and terminal
evidence in the same business contract.

Outbound calls may have irreversible provider-side effects even when Signal only
intends a read or a model inference. Retrying after an ambiguous dispatch can
duplicate work or spend. Redirects can also cross from an admitted public origin to
a private or metadata address unless every hop is re-screened.

## Decision

1. Use one application-level shared-egress gateway rather than allowing provider
   adapters to own sockets or an opaque CONNECT tunnel. The gateway accepts a
   validated GET, HEAD, or POST request and exposes no general-purpose client API.
2. Admit an exact canonical origin only under a current running workflow and crawl
   authority, a current robots snapshot, and the existing global origin bucket.
   Crawler, browser, connector, and model traffic therefore share concurrency,
   politeness, and provider-backoff state for the same origin.
3. Persist an immutable operation and active permit in PostgreSQL before opening a
   socket. A replay of a terminal operation never refetches; a replay of an
   unresolved dispatch remains explicitly uncertain and is never retried blindly.
4. Reuse the pinned HTTP boundary: validate every DNS answer as public, connect to
   one numeric peer, verify the connected peer, preserve TLS SNI for the admitted
   hostname, disable content compression, and reject every redirect without a
   second connection. Responses, headers, media types, bodies, and time are bounded.
5. Allow only `accept`, `authorization`, and `content-type` request headers. Browser
   and crawler requests cannot carry authorization or POST bodies. Cookie headers
   cannot enter this boundary. Authorization values and bodies are not stored;
   immutable records retain presence, byte counts, and cryptographic digests.
6. Complete the operation and release its global permit in one transaction. Exact
   status, sanitized headers, response digest and size, resolved public address,
   latency, completion class, and retry-after evidence are retained. Inconsistent
   completion evidence is rejected before state changes.
7. Remove direct public HTTP from the Jev and labelled fallback adapters. They
   receive only a shared-egress capability. An unconfigured, denied, deferred,
   uncertain, or invalid gateway outcome enters the existing conservative fallback;
   it cannot create authority or execution capability.

## Alternatives

- **Keep one HTTP client per connector.** Rejected because policy and evidence
  would drift and a newly added provider could silently miss SSRF, robots, or
  global admission controls.
- **Use an unrestricted environment proxy.** Rejected because CONNECT hides the
  destination and response semantics needed for exact application evidence and
  does not prove intent-before-I/O.
- **Retry uncertain requests.** Rejected because an accepted provider request may
  have completed even when its response was lost.
- **Store provider request bodies or credentials for replay.** Rejected because it
  unnecessarily expands secret and customer-data retention. A terminal replay is
  evidence-only; an uncertain replay asks the caller to reconcile.
- **Let provider adapters fall back to direct HTTP.** Rejected because optional
  egress configuration must be visibly unavailable, not a safety bypass.

## Consequences

Provider calls need a pre-created, running authority context and current robots
evidence. This is deliberate but means the standalone Jev live qualification now
requires an explicit shared-egress context and least-privilege database roles.
Response bodies are available only to the original in-process completion; durable
records retain their digest, not replayable content. Browser-container routing and
full crawler orchestration remain later slices, but neither may add a direct public
network path.

## Verification

- Unit tests cover unsafe methods, headers, bodies, media types, sizes, timeouts,
  redirects, private resolution, peer mismatch, and sanitized opaque values.
- The isolated Docker network lab performs a real POST to a pinned public-shaped
  address, proves credentials reach only the admitted origin, proves cookies are
  absent, and proves a private redirect causes no second resolution or connection.
- The PostgreSQL 17.11 lab covers intent-before-I/O, exact replay, unresolved
  dispatch, shared global capacity, network failure, provider completion,
  inconsistent completion rejection, forced RLS, function-only access,
  immutability, and migration rollback.
- Jev and fallback tests prove both adapters require the injected egress capability
  and preserve strict response validation and INV-026 recommendation-only behavior.
