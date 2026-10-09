# Slice 0037: RFC-Aware Robots Retrieval And Snapshots

- Status: Implemented and real-network/PostgreSQL-qualified; production crawl disabled
- Date: 2026-09-09
- Milestone: M1 partial
- Specification: Revision 3.2 sections 7.4, 11.1-11.8, 26.3, Appendix A
  `app.robots_snapshots`, EC-025, EC-026, EC-032, and EC-034
- Decision: [ADR-0037](../adr/0037-persist-robots-before-page-admission.md)

## Scope

This slice retrieves, parses, encrypts, persists, and applies one origin's robots
evidence under an immutable crawl-run and fetch profile. It turns robots policy
from transient process state into an append-only, expiring, auditable decision.

It does not add global origin request permits, claim or settle frontier work,
compose the HTTP boundary into Temporal, provide controlled production DNS/egress,
or enable crawling against customer or public resources.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| HTTP boundary | Fetches exact `/robots.txt`, sends the Signal user agent with identity encoding, pins public peers, re-admits redirects, retains closed headers, and caps successful text at 500 KiB |
| Parser profile | Pins Protego 0.6.2 behind `protego/0.6.2+signal-rfc9309-v1`; uses strict UTF-8 line isolation and bounded records while preserving RFC group and match behavior |
| Status policy | Allows only a cached `404`; denies `401`/`403`; backs off `429`; suspends on all other retrieval, policy, media, size, and transport failures |
| Migration `0022` | Adds the forced-RLS `app.robots_snapshots` table, append-only guard, run/scope/artifact foreign keys, current-cache index, and forward-only rollback behavior |
| Persistence | Encrypts and reads back successful bodies before atomically registering artifact, attestation, and snapshot metadata; bodyless outcomes create no artifact |
| Decisions | Selects only the newest unexpired run/origin/profile snapshot, verifies and reparses the exact artifact, and returns a typed allow/deny reason plus snapshot identity |
| Runtime authority | Reuses function-only `signal_crawl_ingest`; no runtime role owns or directly reads/writes the snapshot table |

The parser wrapper supports case-insensitive product-token selection, repeated exact
groups, `*` fallback, longest octet match, equal-match allow priority, wildcard and
`$` behavior, percent-encoded paths, and implicit retrieval of `/robots.txt`.
Unknown directives are ignored. Malformed UTF-8/control lines, overlong lines, and
excessively wildcarded records are isolated rather than poisoning parseable rules.
Raw content and rule text are absent from normal object representations and logs.

Successful, missing, and forbidden snapshots expire after at most 24 hours.
`Retry-After` is parsed as delta-seconds or an HTTP date and bounded to one second
through 24 hours. Other failures remain suspended evidence for five minutes; when
any snapshot expires, no current decision exists and page admission fails closed.

## Data Contract

`app.robots_snapshots` stores tenant/site/run identity; origin and exact robots/final
URLs; immutable fetch-profile and parser identities; fetch/expiry/recording times;
retrieval and decision states; sanitized headers, redirects, public address, and
media type; parse counts and optional crawl delay; source/rules/network hashes; and
an optional exact artifact reference. It stores no plaintext body, encryption key,
credential, cookie, authorization header, provider exception, or model output.

The encrypted `text/plain` object remains in the local artifact backend. PostgreSQL
stores its hash, version, length, key reference, retention time, and verified
upload-readback attestation. A parser decision is usable only while the snapshot is
current and the artifact remains exactly registered and `verified`.

## Verification

Run:

```sh
.venv/bin/pytest -q tests/tooling/test_crawl_http.py tests/tooling/test_crawl_robots.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-database-tests.py
```

The slice adds 23 focused non-database cases, two real-network cases, and 13 real-
database cases. The complete PostgreSQL 17.11 suite passes 445 cases. The crawler
lab passes seven cases on an internal non-masqueraded public-shaped network and
confirms invocation-owned cleanup. Final cumulative evidence is recorded below:

The surrounding stable tree also passes 585 non-database Python cases, including
171 API cases; three real Temporal cases; one joint PostgreSQL/Temporal consumer
case; six workflow-consumer image cases; five Keycloak cases; seven OpenBao cases;
16 repository cases; and checks across 101 Markdown files.

- [0037 PostgreSQL](../evidence/0037-postgresql.json)
- [0037 crawler network](../evidence/0037-crawler-network.json)
- [0037 Temporal](../evidence/0037-temporal.json)
- [0037 joint consumer](../evidence/0037-consumer.json)
- [0037 workflow-consumer image](../evidence/0037-workflow-consumer-image.json)
- [0037 Keycloak](../evidence/0037-keycloak.json)
- [0037 OpenBao](../evidence/0037-openbao.json)

Every provider report retains final source hashes, completed cleanup, and
`production_authority` false.

## Dependency Record

`Protego==0.6.2` is pinned directly in both requirements files. It requires Python
3.10 or later and is distributed under BSD-3-Clause. The selected release includes
the upstream fix for GHSA-wjmf-p669-5m5p; releases through 0.6.1 are not accepted.
The sources reviewed for this decision are the
[RFC](https://www.rfc-editor.org/rfc/rfc9309.html),
[package release](https://pypi.org/project/Protego/), and
[security advisory](https://github.com/scrapy/protego/security/advisories/GHSA-wjmf-p669-5m5p).

## Explicit Limits

- A robots snapshot is evidence, not legal permission, ownership proof, or network
  authority.
- Global cross-tenant/origin concurrency and delay enforcement do not exist yet.
  `crawl_delay_ms` is recorded but not acted on in this slice.
- No page fetch records a robots snapshot identity yet because the live frontier,
  permit, fetch, settlement, and observation path remains intentionally disconnected.
- The local artifact backend, caller-supplied key, and retention deadline are not a
  distributed production storage/key lifecycle.
- Protego behavior outside the versioned supported directive profile is not part of
  the contract. Debug logging for the parser is not enabled.
- No public site was contacted and no production or external-write authority is enabled.

## Next Safe Dependency

Add transactional, cross-tenant global-origin admission with shared politeness,
backoff, and concurrency before composing robots decisions with frontier claims
and the pinned HTTP fetcher.
