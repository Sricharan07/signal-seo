# Slice 0038: Global Origin Admission

- Status: Implemented and real-PostgreSQL-qualified; production crawl disabled
- Date: 2026-09-09
- Milestone: M1 partial
- Specification: Revision 3.2 sections 7.3-7.5, 11.1, 11.3, 11.5, 26.3,
  Appendix A `control.origin_buckets` and `control.admission_leases`, EC-022,
  and G-02/G-03
- Decision: [ADR-0038](../adr/0038-admit-every-crawl-request-through-a-global-origin-bucket.md)

## Scope

This slice adds one transactional request-admission gate shared by every tenant
using the same canonical origin. It owns concurrency, minimum request spacing,
provider/latency backoff, exact retry receipts, and failed-worker permit expiry.

It does not call the network, choose frontier work fairly, fetch or settle a page,
compose a robots decision, run a Temporal crawl activity, provide production DNS/
egress, or enable crawling against customer or public resources.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0023` | Adds global canonical-origin buckets and guarded expiring admission leases without tenant/site content |
| Profile V1 | Enforces one in-flight request, one token, at least 1,000 ms spacing, request-specific stricter delay up to 60 seconds, and permits no longer than 120 seconds |
| New authority | Requires an exact live frontier receipt/worker/origin, running workflow, active directory/tenant, and non-archived site under forced RLS |
| Retry identity | Binds one permit UUID and origin/workload/kind tuple to an opaque authority fingerprint; exact active retry rechecks authority and terminal retry cannot authorize I/O |
| Completion | Releases in a separate transaction and shares `429`, `503`, transport, and slow-response delay across all callers of the origin |
| Crash recovery | Reconciles expired permits during acquisition and through a bounded global sweeper with bucket-before-lease lock order |
| Runtime privilege | Reuses function-only `signal_crawl_admission`; no runtime role can query or mutate either global table directly |

Permit kinds are closed to `robots` and `html_navigation`. Completion kinds are
closed to `success`, `rate_limited`, `service_unavailable`, `transport_error`, and
`cancelled`; reconciliation records `lease_expired`. A completed or expired permit
is a receipt, not reusable authority. A different completion for the same permit
is a conflict rather than a rewritten outcome.

The qualification fixture now derives session timestamps from PostgreSQL rather
than the host clock. This prevents host/container skew from making expiration
tests nondeterministic and does not change production identity behavior.

## Data Contract

`control.origin_buckets` stores only canonical origin, profile version, one-token
state, in-flight count, refill/readiness times, global degradation, and maintenance
timestamps. `control.admission_leases` stores permit/bucket UUIDs, an opaque
run-and-frontier workload key, permit kind, a SHA-256 authority fingerprint,
requested delay/lifetime, issue/expiry/release times, and a closed completion.

The global tables intentionally have no `tenant_id`, `site_id`, URL path, query,
customer payload, body, credentials, cookies, or model output. The acquisition
function temporarily sets exact tenant/site context only to validate the private
frontier and current lifecycle rows under forced RLS.

## Verification

Run:

```sh
.venv/bin/pytest -q tests/tooling/test_crawl_admission.py
.venv/bin/python scripts/run-database-tests.py
```

The slice adds 22 pure-Python cases and 17 real-database cases. The complete
PostgreSQL 17.11 suite passes 462 cases. It covers positive, negative, concurrent,
authority-reduction, provider-backoff, crash-expiry, least-privilege, immutable-
identity, and failed-migration paths.

The surrounding stable tree also passes 607 non-database Python cases, including
171 API cases; seven isolated crawler-network cases; three real Temporal cases;
one joint PostgreSQL/Temporal consumer case; six workflow-consumer image cases;
five Keycloak cases; seven OpenBao cases; 16 repository cases; and checks across
104 Markdown files.

- [0038 PostgreSQL](../evidence/0038-postgresql.json)
- [0038 crawler network](../evidence/0038-crawler-network.json)
- [0038 Temporal](../evidence/0038-temporal.json)
- [0038 joint consumer](../evidence/0038-consumer.json)
- [0038 workflow-consumer image](../evidence/0038-workflow-consumer-image.json)
- [0038 Keycloak](../evidence/0038-keycloak.json)
- [0038 OpenBao](../evidence/0038-openbao.json)

Every provider report retains final source hashes, completed cleanup, and
`production_authority` false.

## Explicit Limits

- The profile is a conservative connected-site default, not a qualified competitor
  research rate or a customer override mechanism.
- Admission is first-come and safety-preserving, but it does not yet implement fair
  queue selection or starvation bounds across tenants/runs.
- Origin-bucket/cardinality retention, aggregate metrics/alerts, load/capacity
  qualification, and deployed reconciliation scheduling remain absent.
- A caller must supply any stricter current robots delay. No live composition yet
  proves that every request carries the exact robots snapshot and permit together.
- Controlled production resolver/egress, page settlement, total-byte accounting,
  parsing, coverage, and Temporal integration are still missing.
- No public site was contacted and no production or external-write authority is enabled.

## Next Safe Dependency

Compose frontier lease, current robots snapshot, global permit, pinned HTTP fetch,
encrypted observation persistence, and exact permit completion into one
authority-free page-attempt service before adding frontier/byte settlement.
