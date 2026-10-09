# ADR-0105: Reserve Paid Research Before Egress

Status: Accepted for the local internal boundary.
Date: 2026-10-02.

## Decision

Commit one immutable DataForSEO call reservation before credential reads and
shared egress. Serialize reservations and settings changes on the site row in
PostgreSQL. Account in integer USD millionths against an owner-set UTC calendar
month cap (default USD 5, allowed USD 0-1000). Use the documented one-task cost
snapshot or a caller-configured conservative estimate no lower than that snapshot.

The exact operation UUID, credential generation, endpoint, request digest, typed
query, month and reserved amount cannot change. Replays return a recorded result
or `unknown`, never another network call. An intent without a receipt retains
its hold. Successful validated costs replace the estimate, including overspend;
invalid, failed and unknown results retain at least the estimate and any larger
validated reported cost. Admission denies cap exhaustion explicitly.

Dispatch binds the committed reservation to the shared-egress body digest and
current credential generation. Removal, a lower cap, a different site, a receipt,
or a month rollover cannot turn an old reservation into fresh call authority.
The gateway still owns public-address screening, peer pinning, robots evidence,
global origin admission, response limits and timeouts.

## Alternatives

In-memory subtraction races and loses holds on restart. Post-call accounting can
overspend before detecting exhaustion. Blind retry after timeout can charge twice.
Automatically releasing an unknown hold pretends that remote non-execution is
known. None meets the budget or ambiguity contract.

## Consequences

Forced-RLS, function-only reservations and receipts are immutable and expose
endpoint, request/response identity, provider cost, result and coverage to later
consumers. `0077` is not changed. Unknown costs need a reviewed reconciliation
procedure; this slice intentionally provides no automatic hold release or replay
dispatch. A current price snapshot is not a guarantee against provider price
changes or unexpected upstream charges. A first live run must reconfirm prices
and use a dedicated disposable provider account.

Evidence: [0076 implementation](../implementation/0076-dataforseo.md) and
[0076 checks](../evidence/0076-dataforseo.json).
