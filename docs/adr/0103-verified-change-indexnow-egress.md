# ADR-0103: Verified-Change IndexNow Egress

Status: Accepted for internal qualification; live IndexNow NOT_EXECUTED.
Date: 2026-10-02

## Context

Revision 4.0 section 12 permits notification only for verified changes to the
verified site. EC-142 requires skipped, reported submissions when the key is not
deployed. IndexNow does not provide a documented client idempotency key or exact
write reconciliation route.

## Decision

An atomic trigger on committed 0085 `verified` receipts derives changed URLs from
the sealed revision. Deduplicate by `(change, URL)`, excluding key publications.
The existing verified-homepage subset produces one URL; do not crawl or bulk
resubmit unchanged pages.

Before each POST, read the authoritative key from OpenBao and GET its exact HTTPS
root file through the shared crawl egress, robots, leased frontier, global origin
admission, and zero-redirect policy. Require status 200, `text/plain`, and exact
bytes. Missing, mismatch, redirect, unreachable, or unverified states produce an
immutable skip receipt and no POST.

Use a typed `INDEXNOW_SUBMIT` profile: only JSON POST to
`https://api.indexnow.org/indexnow`, 65,536 request bytes, 4,096 response bytes,
five-second request timeout. Payload fields and every URL must equal the typed
verified-origin scope before I/O. No credential header or other route is allowed.

Append an exact encrypted intent to the independent write journal before the
primary dispatch fence. Retain immutable submitted URL/status/time receipts.
Definite 429 or 5xx responses back off through the outbox, at most four attempts.
Other failures stop. Lost/ambiguous POST outcomes and dispatch lease expiry remain
unknown without blind retry. A surviving independent intent without its matching
primary definite-outcome receipt blocks restored-primary replay.

## Alternatives

Provider-side idempotency cannot be assumed. Blind retries after timeout risk
duplicates. A generic connector POST permits unwanted hosts and payloads.
Notifying before live verification or scanning all URLs violates the contract.

## Consequences

Accepted means notification accepted, not indexed or improved ranking. An unknown
result needs owner review; it is not converted to success or an automatic retry.
Internal composition requires fresh site/provider egress contexts, current owner
session/recovery authority, and the independent journal. No production poller or
live-provider readiness is simulated.

## Verification

See [0086](../implementation/0086-indexnow.md), its
[evidence](../evidence/0086-indexnow.json), and the official
[IndexNow protocol](https://www.indexnow.org/documentation).
