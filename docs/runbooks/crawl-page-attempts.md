# Crawl Page Attempt Runbook

This runbook covers Slice 0039's authority-free page-attempt coordinator. It is a
local qualification and incident guide, not a production crawler startup procedure.

## Local Qualification

Use the locked Python 3.12 environment, a responsive Docker Engine, and at least
2 GiB free for the joint lab:

```sh
.venv/bin/python -m pytest tests/tooling/test_crawl_page.py -q
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-database-tests.py
```

The joint command creates an invocation-owned PostgreSQL container, random
non-owner role credentials, one loopback-only database port, a hardened non-root
crawler image, and an internal non-masqueraded synthetic-origin network. It applies
migrations twice, runs the real pinned HTTP composition, records source hashes and
`production_authority: false`, and removes only its labeled resources.

## Outcome Interpretation

| Outcome | Meaning | First safe action |
| --- | --- | --- |
| `observed` | Exact response metadata and any body artifact are durable; permit completion is atomic | Pass this receipt to future frontier settlement exactly once |
| `failed / transport_error` | No valid response observation exists; global transport feedback is durable | Preserve the receipt and wait for future bounded settlement policy |
| `failed / policy_rejected` | Pinned fetch policy rejected request/response behavior | Investigate scope, redirect, address, framing, or adapter evidence; do not broaden policy |
| `failed / observation_persistence_failed` | HTTP returned and evidence definitively did not commit | Preserve any orphan object, honor conservative backoff, and investigate storage or contract health |
| `dispatch_outcome_unknown` | Dispatch was committed but no exact observation is available | Do not refetch, release by hand, or mark failed; escalate for reconciliation |
| `permit_finished_without_attempt` | A terminal permit exists without a page dispatch receipt | Do not contact the origin; inspect admission/caller interruption evidence |
| Robots blocked | Current exact snapshot denied or suspended the URL | Do not acquire a page permit; refresh robots only through its separate safe path |
| Origin deferred | Shared in-flight, politeness, or provider backoff is active | Schedule at `retry_at` or later; do not bypass the global bucket |

## Runtime Invariants

- Use separate idle autocommit connections for `signal_crawl_admission` and
  `signal_crawl_ingest`; roles and connections are not interchangeable.
- Use the exact typed run and frontier lease, leased policy, stable permit UUID,
  current robots evidence, encryption key reference/material, retention deadline,
  and network profile hash.
- Never call the fetcher before `begin_crawl_page_attempt` commits a newly inserted
  dispatch receipt. A duplicate begin is not network authority.
- Hold no PostgreSQL transaction while resolving, connecting, reading a response,
  or writing/reading an artifact.
- Never infer that absence of an observation means the request was not sent.
- Never bypass the frontier-lease/attempt ordering: preexisting evidence blocks a
  new dispatch, and a failed attempt rejects late observation insertion.
- Never log body bytes, key material, credentials, cookies, authorization headers,
  or raw provider errors. Public result objects are intentionally opaque in repr.
- Do not mutate attempt, observation, artifact, bucket, or permit rows manually.

## Unknown Dispatch

Confirm the attempt UUID, frontier lease receipt, worker, permit, robots snapshot,
and absence of exact fetch observation through an approved operator path. Preserve
all rows and any unregistered object until the artifact orphan grace period has
elapsed. Do not mint a replacement permit or requeue the URL in this slice.

An exact observation appearing later can be finalized without current crawl
authority and without refetch. Absence after permit expiry remains unknown, not a
transport failure. Future frontier settlement must make this state explicit.

## Persistence Failure

`observation_persistence_failed` releases the permit atomically with conservative
`service_unavailable` feedback and a `local_persistence_failure` basis only after a
definitive non-commit. Check private storage ownership/mode, key reference
availability, free space, input-contract failures, and orphan reconciliation. Do
not invent an observation or attach a nearby artifact to the attempt.

A PostgreSQL error during observation persistence has uncertain commit status. It
must propagate while the page attempt remains `dispatched`; retry then recovers the
exact observation if present or returns `dispatch_outcome_unknown`. Never convert
that uncertainty into a failed receipt or a second request.

## Migration And Production Boundary

Migration `0024` is forward-only. A failed transaction remains at `0023`; never
use destructive downgrade. Production rollout still requires catalog-lock and
capacity review, distributed artifacts/key lifecycle, metrics and alerts,
controlled resolver/egress, frontier/byte settlement, workflow integration,
failure/restart qualification, and explicit pilot authority.

The current coordinator is called only by tests. The joint lab contacts one
synthetic internal origin and grants no production or external-write authority.
