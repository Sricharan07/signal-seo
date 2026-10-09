# ADR-0063: Settle Every Crawl Frontier With Evidence

- Status: Accepted
- Date: 2026-09-29
- Owners: Signal crawl, workflow, and evidence boundaries
- Related: [ADR-0035](0035-bind-crawl-leases-to-immutable-run-snapshots.md), [ADR-0039](0039-record-dispatch-before-composing-crawl-page-io.md), [ADR-0062](0062-record-shared-egress-before-network-io.md), Revision 3.2 section 6.2, Revision 4.0 INV-029

## Context

Signal had reviewed URL, robots, admission, artifact, page-attempt, and shared-egress
primitives, but no honest site-wide completion contract. A workflow result could
otherwise be mistaken for complete coverage while frontier leases, lost responses,
or byte-limited pages remained unresolved. Temporal may retry an activity after a
worker crash, including after a network dispatch whose response was lost.

## Decision

1. Admit a full crawl only for one currently verified exact primary origin and one
   already-started `CrawlSite` command. The local pilot's real-crawl mode is
   explicit and separate from its default synthetic walkthrough.
2. Reuse the shared global-origin bucket for both robots bootstrap and page GETs.
   A robots dispatch is recorded before the request; page traffic also requires a
   current robots snapshot, public-address screening, and a pinned peer.
3. Store parsed page evidence and one immutable terminal settlement per frontier
   URL under forced RLS and narrow functions. Settlements bind the lease, URL,
   robots snapshot, egress operation, fetch observation, page record, and bytes.
4. On restart, recover exact observations when possible. An unresolved robots or
   page dispatch becomes `dispatch_unknown` and is never blindly retried. A
   completed robots request is not itself an unknown page dispatch.
5. Serialize byte accounting and finalize only after every discovered frontier
   URL has a terminal classification. Coverage is partial when a budget, unknown
   dispatch, failure, parser error, or robots denial prevented observation.
6. Keep a bounded one-hour crawl deadline with cooperative activity cancellation
   and a longer heartbeat-aware Temporal activity window. Cancellation stops new
   dispatches and waits for the crawl thread to exit.

## Alternatives

- **Retry all expired leases.** Rejected because a response may have been lost
  after network dispatch and a duplicate request would be unaccounted for.
- **Mark a workflow complete when the queue is empty.** Rejected because active
  leases and unclassified budget-limited URLs would disappear from coverage.
- **Use the synthetic pilot result for verified sites.** Rejected because it would
  appear to describe real site coverage. The default pilot remains synthetic;
  verified crawling requires an explicit local mode and proof before the button
  becomes available.
- **Enable production crawling now.** Rejected pending production resolver,
  artifact-key lifecycle, process isolation, deployment, and release qualification.

## Consequences

An ambiguous dispatch can make a crawl partial even if the remote site may have
served the page. This conservative classification preserves evidence integrity.
The local artifact encryption key and object directory are invocation-owned; they
are not a production key or distributed storage design. The Pages view exposes
manifest coverage but not a full per-page crawl browser yet.

## Verification

Real PostgreSQL tests cover verified and unverified sites, completed replay,
robots denial, revocation after robots, byte-budget finalization, unknown robots
and page dispatches, forged settlement rejection, forced RLS, and migration
rollback. Separate isolated-network tests cover the pinned fetch boundary;
Temporal tests cover activity retry/cancellation and workflow replay. The exact
commands and results are in [slice 0066](../implementation/0066-full-site-crawl.md).
