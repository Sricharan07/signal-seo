# ADR-0089: Bounded GA4 Evidence, Never Complete Coverage

Status: accepted for the local R4 boundary, 2026-10-02.

## Context

GA4 supplies page traffic, engagement, and reported conversions for later
measurement (Revision 4.0 sections 11, 12, 16). Paginated results can be sampled,
thresholded, aggregated into `(other)`, or limited by collection and consent setup.

## Decision

Use the Data API's exact property `runReport` endpoint with `pagePath`, `sessions`,
`engagedSessions`, `engagementRate`, and `keyEvents`. Absolute date ranges have at
most a 92-day difference. Requests use a fixed dimension order, 1,000-row pages,
five-page/5,000-row maximum, and property quota metadata. Page paths need not be
additive session buckets; these metrics are not summed into site-wide totals.

An immutable import generation contains the requested dates, rows, property,
binding, observed operation IDs and response digests, and each page's metadata and
quota verbatim. Preserve sampling, thresholding, and `(other)` indicators. Always
record `complete=false` and `missing_data=unknown_not_zero`, even for an empty or
fully paginated response. Conversion instrumentation is not validated and consent
setup is not assessed. Provider metadata is evidence, never an instruction.

Add closed typed profiles for Admin GET account summaries/property web streams and
Data POST property reports. They admit only the exact Google origin/path/method,
bound bearer token, fixed media types, ten-second timeouts, 128 KiB responses, and
bounded request/query values. Google token/revoke traffic reuses the closed OAuth
profiles. Every call passes the existing durable admission, robots, public-address
pinning, politeness, and response gateway. No additional Google API is admitted.

## Alternatives

- Unbounded pagination would defeat local resource limits.
- Zero-filling unavailable dates would invent measurements.
- Treating a page count as complete coverage would erase Google's own limitations.
- New provider workers or dependencies are unnecessary for an owner-triggered read.

## Consequences

Imports fail closed on changing pagination, malformed metrics, scope reduction,
unpersistable refresh rotation, revoked bindings, or changed recovery authority.
Observed provider denial enters the restriction journal. A failed import creates no
successful generation. Later effect measurement and automatic reimports are outside
this slice. Unconfigured API composition stays visibly unavailable in Connectors.

## Verification

See [0097](../implementation/0097-ga4-binding.md). Official
[runReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport)
and [response metadata](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData)
define the read and coverage surfaces; live provider qualification remains
`NOT_EXECUTED`.
