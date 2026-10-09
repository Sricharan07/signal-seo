# ADR-0127: Import only documented Bing top-page performance

Status: Accepted for the internal read boundary; live Bing NOT_EXECUTED.
Date: 2026-10-02. Slice: 0129. Classification: security-critical.

## Context

0071 imports site totals through GetRankAndTrafficStats. Those rows do not
identify pages and cannot support per-page measurement. Revision 4.0 section 12
permits read-only Bing performance. Provider content remains untrusted data.

## Official API Findings

Reviewed Microsoft documentation only, on 2026-10-02 and 2026-10-03:

- [GetPageStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getpagestats?view=bing-webmaster-dotnet)
  is a GET with `siteUrl`, returning `List<QueryStats>` for top pages. The XML
  sample identifies `Query` as the page URL (the JSON sample uses the generic
  string `query`). Returned fields are `Query`, `Date`, `Clicks`, `Impressions`,
  `AvgClickPosition`, and `AvgImpressionPosition`; JSON may also carry `__type`.
  The documented JSON path is `/webmaster/api.svc/json/GetPageStats`.
  The reference says updates occur weekly. It does not define the aggregation
  period, day boundary, retention, completeness, pagination, or date filters.
- [QueryStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.querystats?view=bing-webmaster-dotnet)
  enumerates those six properties. Their linked property references specify
  String Query and DateTime Date.
  [Clicks](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.querystats.clicks?view=bing-webmaster-dotnet)
  and [Impressions](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.querystats.impressions?view=bing-webmaster-dotnet)
  are Int64; the two position fields are Int32. In particular,
  [AvgImpressionPosition](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.querystats.avgimpressionposition?view=bing-webmaster-dotnet)
  is an integer, not a floating-point GSC metric. Preserve the separate click
  and impression position fields; do not silently relabel either as a generic
  average position.
- [Date](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.querystats.date?view=bing-webmaster-dotnet)
  specifies DateTime without defining a measurement period. The endpoint samples
  show a date-bearing timestamp, with a JSON milliseconds/offset representation.
  Preserve that exact provider value. Date granularity is **unknown**, not daily
  or weekly inferred from the refresh frequency.
- [GetPageQueryStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getpagequerystats?view=bing-webmaster-dotnet)
  takes `siteUrl` and a specific `page`, returns QueryStats with `Query` holding
  the search query, and also documents weekly updates. It is unnecessary for
  the top-page import and remains unavailable in the egress profile.

## Decision

Add GetPageStats only to the existing Bing GET profile, using the already-bound
OAuth refresh/access-token path and exact site. Do not add query-string API keys,
new origins, date filters, pagination, or query-level imports. The existing
OAuth-only implementation is sufficient under section 12's API-key-or-OAuth
contract. Existing OAuth/token and site-level import behavior is unchanged.

Validate the full bounded response before persistence. Keep both documented
position fields, preserve provider dates, normalize page identity through the
existing URL module, and drop/count valid rows outside the exact verified origin
(including different schemes and subdomains). Reject malformed rows and duplicate
page/date identities. Coverage remains `complete=false`, `missing_data=unknown`.

## Alternatives

Site totals cannot substitute for pages. Summing query rows would introduce
unsupported completeness and attribution assumptions. Daily/weekly resampling
would invent a documented aggregation contract. Opening all Bing paths or adding
API keys to persisted request URLs would enlarge the provider/credential boundary.

## Consequences and Verification

This is a bounded top-page observation, not an exhaustive page census or a
qualified 7/28/90-day dataset. Migration 0079 and the ingest-only read port are
described in [0129](../implementation/0129-bing-pages.md). Positive, negative,
failure and real PostgreSQL tests use synthetic doubles behind shared egress;
live consent, imports, and production composition remain NOT_EXECUTED.
