# ADR-0088: Exact Read-Only GA4 Owner Binding

Status: accepted for the local R4 boundary, 2026-10-02.

## Context

Revision 4.0 sections 11-12 require optional GA4 reads, not property or tracking
changes. OAuth, secrets, egress, and recovery make this a security-critical slice.
GSC already owns the Google PKCE and OpenBao refresh-token lifecycle.

## Decision

Parameterize the existing Google OAuth helpers with a closed two-scope vocabulary;
their default remains `webmasters.readonly`. GA4 requests and accepts exactly
`https://www.googleapis.com/auth/analytics.readonly`, with offline consent, S256,
non-incremental scope, ten-minute hashed one-use state, and an exact HTTPS callback.
Reuse the OpenBao lifecycle with a separate `signal-ga4` mount and `secret://ga4/`
references. Only OpenBao holds client secrets, PKCE verifiers, or refresh tokens.

The current site owner with MFA chooses one discovered `properties/<number>` from
Admin API account summaries. A web stream's normalized origin must equal the site's
current verified origin. Recheck identity, property selection, origin, attempt
expiry, and recovery generation before committing the immutable binding. A stream
response digest must match an observed shared-egress receipt.

Local disconnect or provider reauthorization restricts use before any upstream
revocation. Restrictions enter the existing independent journal outbox and replay
as deny-only GA4 tombstones after restore. No model makes the binding decision.

## Alternatives

- Forking GSC's PKCE/CAS implementation would duplicate a security boundary.
- Automatically selecting the first property would remove the owner's choice.
- Matching a display name, account, or parent domain would not establish exact site
  identity. Only the web stream's origin qualifies.
- Direct Google SDK traffic would bypass the crawler's egress controls.

## Consequences

GA4's least-privilege OpenBao credential cannot access the GSC namespace. Existing
GSC defaults and tests stay unchanged. Access tokens are short-lived memory only;
PostgreSQL rows, projections, and errors contain no token material. Revocation
durability can remain pending without restoring local access. Upstream revocation
and secret cleanup can fail independently; such outcomes are not claimed complete.
An expired staged attempt can retain an inaccessible OpenBao token until operator
cleanup; automated orphan cleanup is not implemented.

## Verification

See [0097](../implementation/0097-ga4-binding.md) and its evidence. Live Google
consent, authorized GA4 responses, and production composition are `NOT_EXECUTED`.
Google documents the read-only discovery surface in
[account summaries](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list)
and [web streams](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/properties.dataStreams/list).
