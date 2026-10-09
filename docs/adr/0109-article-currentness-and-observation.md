# ADR-0109: Article Currentness, Caps and Observation

Status: accepted for slice 0123, 2026-10-02.

## Context

An immutable article can become stale when its brief, facts, repository base,
build or binding changes. Draft-time caps alone do not bound delayed PRs.
An opened PR is not evidence of customer deployment or live article bytes.

## Decision

At approval and every dispatch eligibility check, compare the immutable drafting
fact snapshot against the current approved-facts port, including exact ID,
category and statement. Superseded or removed facts and superseded briefs fail
closed. Require current owner/site authority, active protected binding, observed
complete Eleventy HTML extension, recovery-bound passed build and exact base,
patch, logs and artifacts. Remote base is independently inspected before effects.
Existing protected-path preflight reconstructs exactly one absent sibling HTML
file for an article or the selected source file for a refresh.

Retain 0082's conservative draft cap. Additionally reserve a combined rolling
seven-day article/refresh output slot with the PR operation, under the existing
site scope lock. Default two, maximum five, using the current owner cap.
Blocked and unknown operations keep their slot within that window. Opened PRs
count from the recorded effect time. Unresolved reservations older than seven
days cannot resume writes in a new window. Check again at each effect; replay
does not reserve another slot.

0085 reads original sealed canonical bytes through a private union projection.
Only observation metadata is derived from the authenticated verified-site origin
and relative HTML path. For live proof require the exact `_site/<path>` artifact
to equal the sealed result digest; unsupported transformations stay unavailable.
Existing checks, merged-tree/customer-deployment correlation, bounded crawl GET,
robots, origin admission, byte digest and noindex/canonical checks remain required.
No signed delivery certification is claimed.

The verified receipt also enters 0094's imported-data measurement path. Editorial
page URLs come from this same exact sealed revision and origin projection. A new
article records an explicit `new_page` baseline with null dates and no metrics:
no pre-change page window exists. A refresh retains the normal pre-PR baseline
and generation cutoff on the existing URL. Both use unchanged GSC page-dimension
source selection, whole source-calendar days and 7/28/90-day post windows.
Missing, lagging and partial provider data remain explicit; observed change is
not a causal estimate. This adds no approval, write or provider authority.

## Alternatives

Rejected checking only fact labels, grandfathering draft slots as delivery slots,
or reporting a PR/build as live. These lose currentness, bounds or independent
proof. Semantic equivalence and inferred deployment URLs are not used.

## Consequences

Missing exact artifacts, unrecognized output routing, base drift or stale facts
require fresh owner work, not a retry with a changed patch. No new provider
profile or dependency is introduced.

## Verification

See [0123](../implementation/0123-owner-approved-article-delivery.md).
Live GitHub and customer deployment remain `NOT_EXECUTED`.
