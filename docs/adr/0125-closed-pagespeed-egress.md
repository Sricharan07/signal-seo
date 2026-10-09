# ADR 0125: Closed PageSpeed Insights Egress

Status: Accepted. Date: 2026-10-03.

## Context

Revision 4.0 section 11 permits PageSpeed Insights (PSI) for performance data.
Its optional API key is a query credential, unlike the existing header credentials.
Neither a provider URL nor a verified website may broaden network authority.

## Decision

Add a typed `PAGESPEED` connector profile to the existing shared egress gateway:
GET only, exact `https://www.googleapis.com/pagespeedonline/v5/runPagespeed`,
`url`, `strategy`, `category=performance`, and `key` only when configured.
Mobile and desktop are the only strategies. The normalized HTTPS page origin
must equal the current verified site origin. Maximum response is 512 KiB and
maximum request time is 25 seconds. Existing DNS screening, pinned TLS,
redirect rejection, robots admission, global origin buckets and backoff apply.

The existing current MFA-owner connector authority is revalidated in SQL before
network dispatch. A stored weekly sample and remaining daily cap are additional
conditions, not authority. No crawl, session, workflow or standing grant is minted.

Keys are operator- or owner-provisioned in one exact OpenBao KV v2 path. The
runtime reader has read-only access. No configured path selects keyless quota;
a configured but unreadable path fails closed, never silently falling back.
Only the transport receives the key. Canonical evidence URLs remove it before
request hashing and database calls; evidence records credential presence but
neither the key nor a key fingerprint. Fixed error codes replace provider errors.

## Consequences

Provider receipts remain immutable, tenant-scoped and replay-safe. Unknown
dispatch is not retried; observed replay does not manufacture a response body.
The collector rejects a response containing the configured key and stores only
a response digest and a closed numeric projection, not raw PSI JSON.
OpenBao, PostgreSQL and gateway doubles qualify the local boundary. Live PSI
qualification remains `NOT_EXECUTED`; no production network configuration is added.

See [implementation](../implementation/0128-page-speed.md) and
[field evidence decision](0126-source-separated-core-web-vitals.md).
