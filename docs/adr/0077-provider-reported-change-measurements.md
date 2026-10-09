# ADR-0077: Preserve Provider-Reported Change Measurements

Status: Accepted for local product implementation.

## Context

Revision 4.0 section 16 and INV-018 require pre-change evidence, observations at
7, 28 and 90 days, and explicit missing data. Existing 0069 GSC and 0071 Bing
imports deliberately do not certify completeness. Bing's current performance
import has neither a page dimension nor position. Those boundaries are unchanged.

## Decision

Capture immutable, exact-page baseline plans on the first 0085 live-verified
receipt. Pin only source generations imported before PR preparation. Use whole
source-calendar days and matching-length before/after windows. Observe GSC page
metrics and separately labelled Bing site context from existing imports only.

States are `not_yet_due`, `awaiting_data`, `measured_as_reported`, `unavailable`
and `failed`. `measured_as_reported` means "as reported by provider; completeness
not guaranteed", never complete. Carry source coverage verbatim; missing values
are null, never zero or interpolated. Subtractions are "observed change", not
causal impact. Record other Signal live-verified changes to the exact page.

Temporal timers identify each operation/horizon uniquely. SQL serializes and
appends only changed evidence; weekly maintenance can append later source
corrections without mutating the baseline or prior observations. Extend the
existing owner report with a versioned projection, not a channel-delivery service.

## Alternatives

- Claim complete data after a date window closes: rejected because provider
  coverage explicitly disclaims completeness.
- Attribute Bing site totals to a changed page: rejected because the dimension
  does not exist in the current import.
- Fetch providers from measurement activities: rejected; imports own provider
  authority, credentials and egress, and this product slice does not enlarge them.
- Fill missing baseline data with later imports: rejected; later evidence cannot
  silently become a pre-change baseline.

## Consequences

Missing pre-change evidence remains missing permanently in that plan. Post-window
provider lag can resolve as imports arrive. GSC determines the page-observation
state; Bing's independent state remains visible. Confounders do not cover unknown
external edits, seasonality or other causal influences. GA4 and Bing page-level
imports need separate follow-up slices. Production/provider qualification is not
implied by local tests.

## Verification

The [0094 implementation record](../implementation/0094-change-measurement.md)
and [evidence](../evidence/0094-change-measurement.json) describe real PostgreSQL,
Temporal timer/replay, worker-kill recovery, owner/tenant negatives and strict
API/dashboard data contracts. Accepted specification revisions remain unchanged.
