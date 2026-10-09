# ADR-0034: Pin Crawl Connections To Admitted Public Addresses

- Status: Accepted
- Date: 2026-09-09
- Owners: Crawler, security, and workflow runtime

## Context

The implemented `CrawlSite` workflow deliberately used an injected synthetic
executor. It could prove workflow retry, cancellation, replay, and terminal
projection, but it could not safely contact a website. String-checking an input URL
is not enough: a hostname can resolve to a private address, return mixed public and
private answers, change between validation and connection, or redirect to a new
target after the first request.

URL identity also cannot be reduced to a lowercased string. Paths are case
sensitive on some servers, trailing slashes and empty query markers can matter,
repeated query parameters preserve order, and fragments are not part of an HTTP
fetch. Original evidence, safe display, transport identity, and deduplication need
separate fields even when some currently share the same canonical ASCII value.

The crawler eventually needs a controlled resolver/egress service, robots policy,
frontier, parser, artifact service, and origin-wide scheduler. This slice must not
create a system-DNS shortcut that can later be mistaken for that production
boundary.

## Decision

Introduce an immutable crawl-scope policy with an exact set of canonical HTTP or
HTTPS origins and bounded user agent, redirect count, body size, request timeout,
and total timeout. Only default ports 80 and 443 are admitted in this release.
Every URL is parsed into:

- the unchanged original string;
- a fragment-free ASCII fetch URL;
- a conservative ASCII display URL;
- a fetch-identity deduplication key;
- an exact canonical origin; and
- an ASCII request target that preserves path case, trailing slash, percent
  encoding, empty query markers, repeated query parameters, and query order.

Reject unsupported schemes, user information, non-default ports, backslashes,
malformed escapes, raw or percent-encoded control characters, invalid host labels,
oversized values, and cross-origin redirects. IDNs use an ASCII host for both
transport and display so a Unicode homograph is not made visually friendlier than
the authority actually contacted.

Require the HTTP fetcher to receive a controlled resolver port. The resolver gets
the bounded remaining timeout. Every answer must parse as a public IPv4 or IPv6
address; one loopback, private, link-local, carrier-grade NAT, multicast, reserved,
unspecified, metadata, documentation, or mapped-private answer rejects the entire
set. Answers are canonicalized and sorted for deterministic selection.

For each hop, resolve once, validate the complete answer set, connect to one of
those exact numeric addresses, and compare the socket peer with the selected
address. Fallback attempts across the validated set share one exchange deadline;
they cannot multiply the configured timeout. HTTPS wraps that already-connected
socket using the admitted hostname for SNI and certificate verification. Redirects
are not followed by the HTTP library; the next location repeats exact-origin
admission and resolution. Requests send
only GET, Host, Signal user agent, a bounded Accept header, identity encoding, and
connection-close. They never forward cookies or authorization.

Retain only an allowlist of bounded response headers. Reject ambiguous framing,
duplicate singleton headers, invalid control characters, truncated declared
bodies, and a `304` without a retained prior body. Keep at most 5 MiB of HTML or
XHTML and record its SHA-256. Return explicit empty-body outcomes for unsupported
media, unsupported content encoding, or a body over the configured limit. URL and
fetch result representations are opaque so routine diagnostics cannot dump query
values or page bodies.

Qualify the real socket path in a locally built non-root image. Put the client and
synthetic HTTP origin on an internal Docker bridge using a public-shaped address,
disable IP masquerading, publish no host port, mount no host path, and remove only
invocation-labeled resources. This is a dedicated test environment, not a way to
permit private production destinations.

## Alternatives

- Validate the hostname and let the HTTP client resolve it again. Rejected because
  it leaves a DNS-rebinding gap between authorization and connection.
- Accept any address when at least one resolver answer is public. Rejected because
  address selection could still reach a private or metadata endpoint.
- Follow redirects automatically. Rejected because each location and resolved
  destination needs a fresh scope decision.
- Treat `www`, HTTP, HTTPS, or subdomains as interchangeable. Rejected because
  those are different origins until the admitted site binding explicitly includes
  them.
- Exercise only loopback fixtures with a test bypass. Rejected because a bypass in
  the production policy would weaken the SSRF invariant. The isolated lab instead
  gives its private bridge a public-shaped subnet while disabling egress.
- Add system DNS as a default resolver. Rejected because DNS timeout, provenance,
  rebinding, and egress ownership must be selected with the production deployment.

## Consequences

- URL and destination admission are deterministic domain decisions, while socket
  and TLS behavior remain in a separate provider-I/O module.
- A mixed resolver answer fails closed even when that can reduce availability.
- The current boundary intentionally refuses compression and conditional `304`
  reuse; later support must retain decompression and prior-artifact safety.
- The low-level fetcher is not registered with `CrawlSite`. There is no production
  resolver/egress proxy, robots implementation, scheduler, parser, artifact store,
  subresource policy, TLS provider fixture, or production crawl authority yet.

## Verification

Generated tests cover normalization idempotence and non-collapse across paths,
queries, schemes, fragments, IDNs, malformed encodings, scope limits, redirect
targets, and IPv4/IPv6 classes. Fetch tests cover resolver failure and mixed-answer
denial, deterministic address fallback, timeout propagation, response sanitizing,
body hashes and limits, unsupported media/encoding, framing ambiguity, redirect
loops, truncated bodies, and total-time accounting.

The real Docker network cases inspect the non-root image/internal bridge, execute a
same-origin redirect over an address-pinned socket, prove no cookie or authorization
is sent, reject a metadata redirect before another resolution/connection, enforce
streaming and encoding limits, and prove the non-masqueraded network cannot reach an
unserved address. Evidence records exact source hashes and completed cleanup with
production authority false.
