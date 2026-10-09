# ADR-0132: Reserved AI-Visibility Re-Observation

- Status: Accepted
- Date: 2026-10-03
- Owners: Signal assistant observation boundary
- Related: [slice 0131](../implementation/0131-structured-data-and-visibility-schedule.md), ADR-0105 reservation pattern (reviewed DataForSEO reference)

## Context

Scheduled assistant observations are paid external reads. Workflow retry,
worker loss and provider ambiguity must not turn an owner budget into repeated
paid calls. Optional provider absence must stay visible. The branch retains its
assigned base; the reviewed DataForSEO pattern was consulted, not imported from
another branch.

## Decision

Record owner-only versioned settings under current MFA/session, selected-site,
membership, verified-origin and external recovery-generation checks. Default to
disabled, seven days and USD 1.00. Cadence is bounded to 1-30 days; the cap is
0-100,000,000 integer USD millionths. Site pause, proof expiry, owner removal or
recovery change disables admission.

Reconcile one stable Temporal schedule per tenant/site, UTC interval,
skip-overlap, one-minute catch-up and pause-on-failure. Each run snapshots the
latest immutable target-question version. Verified shipped delivery operations
not previously associated with an admitted run appear at the next run as
`observed change`; this does not assert causation or improved visibility.

Serialize admission on the site row. Before an egress factory, credential read
or provider request, commit an immutable per-question/provider reservation.
Amounts use integer currency units and explicitly configured, reviewed provider
ceilings, never token-to-currency guesses. Recheck authority and budget before
credentials and immediately before the existing 0074 request through shared
egress. No new destination, profile, credential boundary or consumer scraping
is introduced.

Unique run/question/provider keys prevent redispatch. Replay of a reservation
without a result returns `outcome_unknown`, never permission to call again.
Process loss, timeout, malformed response or uncertain external outcome retains
the hold. Worker retry can continue unreserved calls but cannot blindly repeat
an already reserved paid call. Cap exhaustion and provider-unconfigured cases
record explicit unavailable results and zero new holds.

The existing provider responses do not contain qualified billing currency.
Therefore actual spend remains null/unpriced. All dispatched holds, including
known successful but unpriced calls, remain charged against admission, across
UTC month boundaries. There is no automatic release, monthly reset or invoice
settlement in this slice. Unknown outcomes cannot gain budget at month rollover.
This conservative retained-hold accounting is stricter than a renewing monthly
allowance; it does not certify an actual provider bill. Live activation requires
qualification of each configured monetary ceiling against the actual provider
contract. Defaults do not supply a production-safe price estimate.

## Consequences

Four forced-RLS append-only, function-only tables store settings, run intents,
reservations and results. The owner API/dashboard exposes cadence, cap, held
usage, unpriced actual spend, run history and unavailable reasons. No provider
secret enters API/dashboard projections. The private worker is usable through
explicit existing database, Temporal, recovery, OpenBao and shared-egress ports;
deployed composition is not supplied or implied. Live providers stay
NOT_EXECUTED. A future qualified billing/settlement contract is needed before
releasing any held budget.

## Verification

Real PostgreSQL tests cover serialized cap admission, immutable replay,
unconfigured outcomes, tenant/role/recovery negatives and reservation before
factory/credential/egress. Real Temporal tests cancel a worker after reservation,
restart it and replay history without a second paid dispatch. A subprocess
SIGKILL probe additionally exercises loss without a graceful worker notification,
using bounded test deadlines and the same production activity. See the linked
implementation and [evidence](../evidence/0131-structured-data-and-visibility-schedule.json).
