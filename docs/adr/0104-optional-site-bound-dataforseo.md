# ADR-0104: Optional Site-Bound DataForSEO

Status: Accepted for the local internal boundary; live qualification pending.
Date: 2026-10-02.

## Decision

Use only DataForSEO's official API for paid ranking-competitor, keyword-volume,
and competitor-backlink data. Add one closed connector-purpose `DATAFORSEO`
profile with three exact live POST endpoints. Do not scrape search engines,
follow returned URLs, submit asynchronous tasks, or add a generic HTTP tool.
Only one task is permitted per request; SERPs are limited to depth 10 and one
page, keywords to one, and backlinks to one domain summary.

Store login/password in site- and generation-scoped OpenBao KV-v2 paths, not
business rows, configuration files, logs, exceptions, or model context. Reuse
`openbao_http`, current session/recovery authority, and `shared_egress`. A typed
credential scope binds the Basic header and nonsecret generation to dispatch.
Dashboard changes use the existing cookie, CSRF, and same-origin BFF contract.
No new operator identity or privilege is invented: dashboard setup requires the
current site owner; deployment operators provision the narrowly scoped OpenBao
mount and runtime composition through their existing infrastructure authority.

Disable the old generation before secret I/O; permanently remove its KV metadata
before activating a replacement. Activation rechecks current owner authority and
the exact generation. A removal error is explicit, leaves new dispatch disabled,
and requires operator cleanup of the generation recorded in settings events.
Already accepted upstream calls cannot be recalled. Restoration must keep provider
egress disabled until OpenBao generations and call receipts are reconciled.

## Alternatives

Search-engine scraping violates INV-031. A global shared credential would obscure
site cost ownership and expand tenant exposure. Secrets in SQL or browser storage
would violate the connector boundary. A separate provider service adds a dormant
deployment rather than reusing the gateway and its admission evidence.

## Consequences

An unconfigured self-hosted installation remains complete for its independent
Search Console workflows; all three paid features visibly report unavailable.
The default application does not fabricate a composed or live-qualified provider.
Official response metadata not consumed by the adapter is ignored; every consumed
identity, number, URL and collection is validated, bounded, and treated as data.
Only sanitized typed results and digests enter business records. Production and
live provider qualification remain `NOT_EXECUTED`.

Evidence: [0076 implementation](../implementation/0076-dataforseo.md) and
[0076 checks](../evidence/0076-dataforseo.json).
