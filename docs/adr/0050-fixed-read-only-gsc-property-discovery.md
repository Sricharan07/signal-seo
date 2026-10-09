# ADR-0050: Fix Search Console Discovery To One Read-Only Boundary

- Status: Accepted
- Date: 2026-09-12
- Scope: Google Search Console property discovery and origin matching

## Context

Core V1 requires an owner to select the Google Search Console property that
represents the already verified public origin. This is a credential-bearing
provider call, so accepting a caller-controlled endpoint, following redirects,
using a write-capable scope, or treating a similarly named property as authority
would widen the connector boundary before OAuth and durable binding are ready.

Google's `sites.list` contract returns the properties visible to the authenticated
account, including each exact resource name and permission level. Domain
properties and URL-prefix properties use different identities and therefore
cannot be normalized into one ambiguous string.

## Decision

Use only `GET https://www.googleapis.com/webmasters/v3/sites` and require the
`https://www.googleapis.com/auth/webmasters.readonly` OAuth scope in the future
authorization flow. The discovery client verifies TLS, follows no redirects,
ignores environment proxies, bounds request time and response bytes, and maps
provider and transport failures to fixed nonsecret codes.

Keep the bearer token in call-local memory and never return it. Parse an exact,
bounded property-list schema. Preserve every provider resource name and permission
level. Classify `sc-domain:` and URL-prefix resources separately, and mark a
property eligible only when it is readable and matches the selected site's exact
canonical HTTPS origin. A domain property may match that host or its DNS suffix;
an eligible URL-prefix property must equal the exact origin root.

This module performs discovery only. OAuth attempts, secret storage, durable
bindings, revocation, health, and analytics imports remain separate later slices.

## Consequences

- Provider location and privilege are not browser- or model-controlled.
- Similar domains, path prefixes, unverified permissions, duplicate entries,
  malformed responses, redirects, oversized responses, and ambiguous failures
  grant no eligibility.
- Exact Google resource identity remains available for a later immutable binding.
- A real successful account/property call is still required before this boundary
  can support an owner pilot.
- The module grants no crawl, analytics-import, repository, or write authority.

## Alternatives Rejected

- **Accept a provider URL from configuration or the browser:** creates an SSRF and
  credential-forwarding boundary that discovery does not need.
- **Use the broad Search Console scope:** asks for write authority before any
  product workflow needs it.
- **Match by substring or normalized display name:** can bind the wrong property.
- **Persist the access token in the connector module:** couples provider protocol
  to secret lifecycle and risks reusable credentials entering ordinary records.
- **Return partial data after a malformed entry:** makes an untrusted response look
  authoritative instead of failing closed.

## Verification

Focused tests cover successful domain and URL-prefix matches, empty real-state
projection, unreadable properties, wrong-domain and malformed entries, duplicate
and unbounded lists, provider status classes, transport failure, response media
type and byte bounds, rejected tokens/origins, and mandatory TLS verification. A
live negative check calls Google's fixed endpoint with a synthetic credential and
confirms authorization rejection without customer access or data.

Provider references:

- [Search Console sites.list](https://developers.google.com/webmaster-tools/v1/sites/list)
- [Google OAuth scopes](https://developers.google.com/identity/protocols/oauth2/scopes)
