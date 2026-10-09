# Slice 0034: Crawl URL And Network Boundary

- Status: Implemented and isolated-network-qualified; production crawl disabled
- Date: 2026-09-09
- Milestone: M1 partial
- Specification: Revision 3.2 sections 2, 6.2, 6.4, 11.1-11.3, 11.6,
  27.2-27.4, 30, 32 G-02/G-03, and 33 Milestone 1
- Decision: [ADR-0034](../adr/0034-pin-crawl-connections-to-admitted-public-addresses.md)

## Scope

This slice replaces no synthetic workflow executor and enables no customer crawl.
It implements and qualifies the first real provider-I/O boundary needed by that
executor: exact URL identity, exact-origin admission, complete resolved-address
screening, address-pinned GET requests, manual redirect revalidation, bounded HTML
bodies, and sanitized fetch evidence.

Keeping it disconnected is a safety decision, not unfinished wiring hidden as a
capability. `CrawlSite` cannot use this boundary until a durable scope source,
controlled production resolver/egress path, robots/frontier policy, and immutable
artifact manifest exist.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| `crawl_urls.py` | Separate original/fetch/display/key/origin identities; canonical ASCII hosts; preserved path/query semantics; strict malformed URL and exact-origin denial |
| Address policy | Complete-set public IPv4/IPv6 validation including mapped addresses and explicit private, metadata, link-local, multicast, reserved, documentation, and carrier-grade denial |
| `crawl_http.py` | Resolver timeout propagation, shared bounded address-fallback time, one-resolution-per-hop numeric socket pinning, peer comparison, HTTPS SNI/certificate hostname, manual redirects, GET-only authority-free headers |
| Response boundary | Header allowlist, framing checks, HTML/XHTML-only body retention, 5 MiB hard maximum, SHA-256, explicit unsupported/oversized outcomes, incomplete-body and standalone-304 denial |
| Network lab | Digest-pinned Python 3.12.14 image, UID/GID 10001, internal non-masqueraded bridge, public-shaped static address, no host ports or mounts, exact labeled cleanup |
| CI | Unit/generated, repository, and five real-network scenarios on the locked Linux quality runner |

The fetcher takes a resolver callable rather than resolving with system DNS. It
passes the remaining bounded request timeout and accepts only a complete validated
address set. All connection attempts for that set share one exchange deadline, so
address fallback cannot multiply the timeout. The transport then connects to a
numeric address, so DNS cannot run a second time inside socket connection setup. A
TLS connection still verifies the admitted hostname rather than the numeric
address.

Only retained, bounded response metadata is returned. `Set-Cookie`,
`Authorization`, and arbitrary response fields do not enter the result. Request
headers carry no reusable authority. Provider and resolver exception text is
collapsed into closed rejected or unavailable failure classes. URL and fetch
objects use opaque representations so diagnostics do not implicitly print query
values or page bodies.

## Real-Network Qualification

Run:

```sh
.venv/bin/python scripts/run-crawler-network-tests.py
```

The lab builds from a digest-pinned base, creates an invocation-specific internal
bridge at `93.184.216.0/24`, disables IP masquerading, and starts a non-root
synthetic origin at `93.184.216.34`. That address looks public to the production
policy while remaining trapped inside the lab. The client has a read-only root,
all capabilities dropped, no-new-privileges, bounded CPU/memory/processes, no host
mount, and no production credentials.

Five cases prove image/network shape, real address-pinned redirect and header
behavior, metadata redirect denial before a second connection, streaming/encoding
limits, and absent egress to an unserved destination. The image, containers, and
network are removed by exact invocation identity even after failure.

## Verification

The slice-specific checks are:

```sh
.venv/bin/python -m pytest tests/tooling/test_crawl_urls.py tests/tooling/test_crawl_http.py tests/tooling/test_crawler_network_lab.py -q
.venv/bin/python scripts/run-crawler-network-tests.py
npm test
```

The focused suite has 60 positive, negative, generated, and failure cases. The
real-network suite has five cases. All 553 non-database Python cases, 171 API cases,
390 real PostgreSQL cases, three real Temporal cases, one joint
PostgreSQL/Temporal consumer case, six real consumer-image cases, five real
Keycloak scenarios, and seven real OpenBao scenarios pass. All 16 repository cases
and documentation checks across 92 Markdown files pass; Ruff lint/format cover 127
Python files, and dependency consistency passes.

Exact provider evidence and source hashes are stored in:

- [0034-crawler-network.json](../evidence/0034-crawler-network.json)
- [0034-postgresql.json](../evidence/0034-postgresql.json)
- [0034-temporal.json](../evidence/0034-temporal.json)
- [0034-consumer.json](../evidence/0034-consumer.json)
- [0034-workflow-consumer-image.json](../evidence/0034-workflow-consumer-image.json)
- [0034-keycloak.json](../evidence/0034-keycloak.json)
- [0034-openbao.json](../evidence/0034-openbao.json)

Every real-provider record reports completed cleanup and production authority
false.

## Explicit Limits

- The boundary is not connected to `CrawlSite`, scheduled, deployed, or granted
  customer/public-internet authority.
- There is no production controlled resolver, authenticated egress proxy, origin
  rate coordinator, robots parser/cache, sitemap/frontier, trap detection,
  extraction/parser, browser/subresource worker, or artifact service.
- Compression is explicitly unsupported rather than decompressed. A `304` is
  rejected because no retained prior artifact can currently justify it.
- The positive provider fixture is HTTP on an isolated synthetic origin. HTTPS
  socket wrapping uses the platform trust store and admitted SNI hostname, but a
  dedicated certificate/provider qualification remains required.
- Bodies are returned in bounded memory for the future artifact writer; this slice
  does not persist raw content or claim durable evidence/manifest coverage.
- G-02 and G-03 remain incomplete. Passing this component does not release a
  crawler, satisfy the golden-site/robots/politeness gates, or make M1 complete.

## Next Safe Dependency

Add durable crawl scope, URL/frontier, fetch observation, and immutable artifact
manifest persistence under real PostgreSQL and a private artifact boundary. Only
then compose this fetcher into a workflow executor with a controlled resolver and
origin-wide scheduling.
