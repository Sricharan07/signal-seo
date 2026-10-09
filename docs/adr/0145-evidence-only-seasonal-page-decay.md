# ADR-0145: Evidence-Only Seasonal Page Decay

Status: Accepted for local product implementation; no release authority.

## Context

Calendar/festival sites can fall month-to-month for entirely expected reasons.
GSC returned rows are incomplete cohorts. Missing page-days cannot be treated as
zero, and recent interventions should not trigger a second refresh while measured.

## Decision

Use the existing current-binding, web, exactly page/date GSC generations, including
their verbatim coverage and lag metadata. Pin the latest covering generation for
each whole comparison window; never splice generations within a window, sum
query/device cohorts, interpolate missing days, or substitute site totals.

The recent window contains 28 source-calendar days ending three days before the
snapshot's `America/Los_Angeles` as-of date. Compare the preceding 28 days. When
history reaches 13 calendar months back and the required page-days exist, prefer
the 28-day window ending one calendar year before the recent end (Feb 29 clamps
to Feb 28). Both comparisons remain inspectable. A usable year-over-year result
overrides month-to-month even when the latter declined. Otherwise flag
"seasonality possible" and explicitly state why year-over-year is unavailable or
partial. Lunar festivals can move dates even in year-over-year comparisons.

For each metric require at least 100 baseline clicks or 1,000 baseline
impressions, a drop >=25%, and a drop >3 times a conservative noise scale:

```text
noise^2 = max(baseline_total + recent_total,
              28 * (daily_sample_variance_before + daily_sample_variance_after),
               4 * (weekly_total_sample_variance_before + weekly_total_sample_variance_after),
              28 * (daily_MAD_scale_before^2 + daily_MAD_scale_after^2))
MAD_scale = 1.4826 * median(abs(value - median(values)))
```

Weekly blocks guard against clustered days; the Poisson floor protects steady
counts; sample variance and robust median absolute deviation conservatively
screen noisy cohorts. This is a fixed screening heuristic, not a calibrated
p-value, causal diagnosis, or ranking prediction. Both metrics are reported, and
either may motivate diagnosis. Each window needs all 28 explicit page-day rows.
Missing/lagged data yields partial comparisons with null statistics, not estimates.
Overall coverage is partial because absent pages remain unknown (INV-018).

Exclude exact page URLs whose existing 90-day Signal measurement post window
overlaps either recent/prior window. Refresh candidates use the existing 0077
Content Writer proposal action only when current approved facts and crawled
source exist. Existing same-page technical findings remain independent evidence;
traffic alone does not invent a technical fault. Planning acceptance creates an
`evidence_proposal`, never brief acceptance, publishing authority or a dispatch.

## Alternatives

Month-to-month alone is seasonally misleading. Missing-as-zero fabricates decline.
Automatically refreshing a detected page bypasses the existing editorial boundary.
A model-selected threshold is neither reproducible nor justified by these records.

## Consequences

Sparse sites may have no eligible comparison. A newest incomplete generation is
not replaced by older favorable evidence. No provider reads, egress, secrets,
external writes or new worker are added. An owner refresh recomputes the evidence
snapshot; no new unattended cadence is claimed.

## Verification

Domain tests cover minimum volume, robust thresholds, seasonal preference,
missing/lagged/invalid data, exclusion and proposal-only outputs. Real PostgreSQL
tests cover current binding/revocation, owner/site/tenant denials, exact evidence,
and unaccepted Content Writer briefs. See [0137](../implementation/0137-learning-and-decay.md).
