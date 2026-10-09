# ADR 0126: Source-Separated Core Web Vitals

Status: Accepted. Date: 2026-10-03.

## Context

Revision 4.0 sections 9.2 and 11 and INV-018 require performance measurements
without disguising partial data as readiness. Lighthouse is lab evidence;
CrUX is field evidence. Origin fallback is not page-level field evidence.

## Decision

Persist an immutable, evidence-linked observation for each sampled page and
strategy. Preserve fetch time, Lighthouse version/run time, lab metrics and
score, and separate URL and origin CrUX p75 LCP, INP and CLS. Record the
reported collection period, or an explicit unavailable reason if absent.
Missing metrics, insufficient samples and URL origin fallback are unavailable,
not zero, estimates, lab substitutions or inferred collection dates.

Poor field metrics produce observation-only findings in the existing 0068
`CrawlAuditFinding` contract, sourced to the PSI observation and the actual
page or origin locator. No fix recipe, proposal, external write or publishing
authority is added. At p75, good thresholds are LCP <= 2,500 ms, INP <= 200 ms
and CLS <= 0.1; poor is LCP > 4,000 ms, INP > 500 ms and CLS > 0.25.
Intermediate values are needs-improvement. Lab results have no INP substitute.

A weekly UTC sample selects at most five successfully crawled same-origin pages,
ranked by clicks from a fresh current GSC web generation when available, otherwise
by crawl order. Both strategies are scheduled. SQL serializes per-site admission
and limits PSI provider operations to four per UTC day, including failed and
unknown dispatches. Immutable sample IDs prevent duplicate dispatch. The internal
daily collector requires a fresh current-owner context; it does not persist an
owner session or install an unattended production timer.

The owner read API and Pages dashboard expose source-separated measurements,
unavailable reasons, scheduled/failure/unknown states and receipt provenance.
Local Lighthouse inside the browser sandbox remains explicitly unavailable.

## Consequences

Some PSI observations legitimately have no field metrics or collection period.
The UI and API keep these gaps visible. Historical observations are append-only;
the owner projection presents the latest sampled week, not a fabricated live score.
Autonomous schedule composition and sandbox Lighthouse qualification remain later
work, not simulated capabilities.

Definitions: [Google PSI categories](https://developers.google.com/speed/docs/insights/v5/about)
and [Core Web Vitals thresholds](https://web.dev/articles/defining-core-web-vitals-thresholds).
See [implementation](../implementation/0128-page-speed.md).
