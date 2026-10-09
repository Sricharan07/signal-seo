# Slice 0039: Durable Crawl Page Attempts

- Status: Implemented and jointly PostgreSQL/network-qualified; production crawl disabled
- Date: 2026-09-09
- Milestone: M1 partial
- Specification: Revision 3.2 sections 7.3-7.5, 8.5, 11.1-11.8, 19.1-19.4,
  Appendix A crawl lease/fetch tables, EC-022, EC-023, and G-02/G-03
- Decision: [ADR-0039](../adr/0039-record-dispatch-before-composing-crawl-page-io.md)

## Scope

This slice composes one current frontier lease, exact robots decision, global HTML
permit, pinned HTTP fetch, encrypted fetch observation, and permit completion into
an authority-free page-attempt operation. It closes the blind HTTP retry gap with
a durable pre-dispatch receipt and recovers an observation committed before final
settlement without another network call.

It does not settle frontier status or run byte budgets, parse/index HTML, discover
links, finalize coverage, register a production Temporal activity, provide a
production resolver/egress path, distribute artifact storage, or enable customer
crawling.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0024` | Adds forced-RLS `app.crawl_page_attempts`, exact foreign keys, one-way terminal transition, an observation race guard, and four function-only operations |
| Dispatch admission | Rechecks current run/frontier/workflow/lifecycle, exact unexpired robots snapshot, exact active HTML permit, origin/fingerprint, and robots-derived delay |
| Coordinator | Uses separate admission/ingest connections, commits dispatch before HTTP, and holds no database transaction over network or artifact I/O |
| Retry handling | Returns terminal evidence, finalizes an existing exact observation, or reports unknown dispatch without refetching |
| Observation lookup | Rehydrates only exact historical lease/worker evidence and validates URLs, redirects, headers, address, timings, profile hash, and artifact metadata |
| Atomic finalization | Completes the exact global permit and changes the page attempt to `observed` or `failed` in one transaction |
| Backoff evidence | Maps `429`/`503`, bounds Retry-After to 1 second-24 hours, and records provider/fallback/cap basis |
| Qualification | Adds a joint disposable PostgreSQL 17.11 and isolated non-masqueraded crawler-network lab plus CI gate |

The attempt and origin permit share one UUID. Terminal failure reasons are closed
to `transport_error`, `policy_rejected`, and
`observation_persistence_failed`. An unexpected worker exception after dispatch is
not converted into a false failure receipt. Neither is an uncertain database
commit: retry recovers exact evidence when present, otherwise it sees the durable
unknown state.

## Data Contract

`app.crawl_page_attempts` stores tenant/site identity, run/frontier/URL and exact
frontier lease/worker, robots snapshot/reason, origin permit, state, dispatch and
finish times, optional observation, terminal reason, completion, measured latency,
bounded Retry-After, and its interpretation basis. It stores no response body,
credential, cookie, authorization header, DNS answer set, or model output.

Bodies remain encrypted in private artifact storage. `app.fetch_observations`
stores bounded response metadata and the exact artifact reference/hash. Global
origin tables remain content-free. Runtime roles receive function execution only;
they cannot select or mutate the attempt table directly.

## Verification

Run:

```sh
.venv/bin/python -m pytest tests/tooling/test_crawl_page.py -q
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-database-tests.py
```

The focused tests cover Retry-After parsing, invalid/opaque values, robots denial,
origin deferral, success and exact retry, known failures, unknown dispatch,
post-observation recovery after authority reduction, contradictory failure
rejection, preexisting/late observation races, artifact failure, database
uncertainty, provider backoff, concurrent exact calls, privileges, immutability,
and failed migration.

The stable tree passes 624 non-database Python cases, including 171 API cases;
477 real PostgreSQL cases; one joint page-attempt case; seven isolated
crawler-network cases; three real Temporal cases; one joint PostgreSQL/Temporal
consumer case; six workflow-consumer image cases; five Keycloak cases; seven
OpenBao cases; 16 repository cases; and checks across 107 Markdown files.

- [0039 PostgreSQL](../evidence/0039-postgresql.json)
- [0039 page-attempt composition](../evidence/0039-page-attempt.json)
- [0039 crawler network](../evidence/0039-crawler-network.json)
- [0039 Temporal before CI correction](../evidence/0039-temporal.json)
- [0039 Temporal after readiness-test provenance refresh](../evidence/0039-temporal-readiness.json)
- [0039 Temporal after POSIX-bind correction](../evidence/0039-temporal-posix-bind.json)
- [0039 joint consumer](../evidence/0039-consumer.json)
- [0039 workflow-consumer image](../evidence/0039-workflow-consumer-image.json)
- [0039 Keycloak before CI correction](../evidence/0039-keycloak.json)
- [0039 Keycloak readiness correction](../evidence/0039-keycloak-readiness.json)
- [0039 Keycloak POSIX-bind correction](../evidence/0039-keycloak-posix-bind.json)
- [0039 OpenBao](../evidence/0039-openbao.json)

Every provider report retains final source hashes, completed invocation-owned
cleanup, and `production_authority` false.

### Post-push CI reliability correction

The first private-repository Quality run and its failed-job rerun both passed all
earlier gates, then exhausted the lab's fixed 90-second Keycloak startup window on
the hosted runner. The lab now allows at most 180 seconds for cold startup while
retaining two-second requests, 250-millisecond-or-shorter polling, loopback-only
TLS, pinned provider identity, exact cleanup, and fail-closed timeout behavior.

The readiness loop is independently testable: transient non-200 responses recover,
transport failure at the deadline is rejected without extending it, and the
configured allowance remains explicitly bounded. Fourteen focused lab tests and
all five real Keycloak protocol scenarios pass locally after the correction. The
three real Temporal workflow/replay cases were also rerun because their evidence
manifest intentionally hashes the complete tooling-test directory. This changes
qualification timing only; it does not change OIDC behavior, workflow authority,
customer access, or production configuration. The pushed correction must still
pass its own remote workflow before the CI repair is considered remotely verified.

The first 180-second correction run also timed out, disproving the timeout-only
hypothesis. Image metadata identifies Keycloak as UID 1000 and GID 0, while a
GitHub Linux runner preserves its different host UID on the owner-only `0600` key
bind. Docker Desktop did not expose that ownership mismatch. The lab now runs the
container as the invoking non-root host UID with the image's existing GID 0. This
keeps the private bind owner-readable, preserves Keycloak's group-writable runtime
directories, and never grants UID 0. Root-host and non-POSIX invocation fail before
container creation. Two focused identity tests, the repository lab contract, all
five real Keycloak scenarios, and all three cross-hashed Temporal cases pass
locally after this correction.

The correction's private-repository Quality run `34393013320` did not execute any
step: GitHub rejected both jobs before checkout because the account's payments or
Actions spending limit require attention. The Linux-bind correction therefore
remains locally qualified and remotely unverified; the run is not evidence of a
code failure or a passing remote gate.

## Explicit Limits

- `dispatched` with no observation is unresolved by design. No automatic
  classification, timeout settlement, or new frontier lease is implemented here.
- Permit expiry before page finalization remains an explicit operator/reconciliation
  case; this slice never overwrites a terminal permit receipt.
- Robots retrieval itself is not yet composed through this page-attempt operation.
- The local artifact store has no distributed durability, managed key rotation,
  retention execution, backup, or production restore qualification.
- The joint lab uses a synthetic Docker origin and injected numeric resolver. No
  public site or customer resource is contacted.
- No frontier/byte settlement, fair scheduling, parser, coverage manifest,
  deployed worker, production egress, monitoring backend, or crawl authority exists.

## Next Safe Dependency

Add exact frontier and total-byte settlement for terminal page-attempt receipts,
including explicit treatment of unresolved dispatch and expired completion, before
link discovery, coverage finalization, or Temporal workflow registration.
