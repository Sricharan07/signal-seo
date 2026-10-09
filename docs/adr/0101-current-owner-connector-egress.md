# ADR 0101: Current Owner Connector Egress

Status: **Accepted 2026-10-01; live composition not qualified**.

## Decision

Connector onboarding is a human owner's operation, not a crawl and not autonomous
work. Do not fabricate `CrawlSite`, a Temporal execution, robots evidence or a
standing grant to satisfy the previously crawl-only shared-egress composition.
Permit a distinct `OwnerConnectorContext` through the same provider boundary.

Before each dispatch, PostgreSQL must resolve the current session, selected site,
recovery generation, active Owner/MFA membership and verified public-origin claim.
The owner path admits only the existing read GitHub, read-only Search Console,
Google token/revocation and Slack OAuth/bot profiles. Closed methods and endpoints
apply independently in Python and SQL. It cannot borrow model, crawler, browser,
repository-write or standing-grant authority. Individual connector operations
still apply their existing binding, target, one-use state and external-write gates.

Robots are retrieved using the crawler's screened numeric-peer TLS boundary after
durable admission in the existing global origin bucket. The existing RFC parser
decides the exact target URL; fetched plaintext is encrypted and read back before
recording its immutable artifact handle and rules digest. Only a real HTTP 404 or
an allowed, proved HTTP 200 text/plain response can support provider dispatch.
Both robots and provider requests use the same global concurrency, politeness
and backoff state as actual crawler traffic. No unrestricted outbound path exists.

Use separate forced-RLS, immutable owner-operation records instead of assigning
fake crawl IDs. Dispatch, unknown outcomes and terminal evidence remain durable;
credential/body values never enter PostgreSQL or logs. A repeated unresolved
dispatch is not sent again; a terminal provider body is not synthesized or
replayed. Permits expire within 30 seconds and the owner's session deadline;
robots decisions expire within five minutes. A live test remains unavailable
until the resolver/network, provider credentials, owner browser routes and real
positive/negative/failure qualification are composed.

## Qualification

[Slice 0116](../implementation/0116-owner-connector-egress.md) records local
qualification and the remaining actual-provider gates. Accepted specifications
and existing crawl/standing-authority behavior are unchanged.
