# Crawl Robots Runbook

This runbook covers Slice 0037's RFC-aware robots retrieval, immutable snapshot,
and cached decision boundary. It is not a production crawler startup procedure.

## Local Qualification

Prerequisites are a locked Python 3.12 environment, responsive Docker Engine, at
least 3 GiB free for PostgreSQL and 2 GiB for the network lab, and a stable source
tree. Run from the repository root:

```sh
.venv/bin/pip check
.venv/bin/pytest -q tests/tooling/test_crawl_http.py tests/tooling/test_crawl_robots.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-database-tests.py
```

The network lab creates one invocation-labeled internal, non-masqueraded Docker
network with no host publication. The database lab uses digest-pinned PostgreSQL
17.11, tmpfs, random credentials, and one ephemeral loopback-only port. Both remove
only invocation-owned resources and record source hashes.

## State Interpretation

| Decision | Meaning | First safe action |
| --- | --- | --- |
| `rules` | A current verified body parsed under the exact release/profile | Apply the exact snapshot to an admitted same-origin URL |
| `allow_missing` | The exact resource returned `404` | Allow only otherwise-authorized public crawling until expiry |
| `deny` | The resource returned `401` or `403` | Send no new origin request; verify ownership/configuration out of band |
| `backoff` | The resource returned `429` | Send no new origin request; wait until the bounded snapshot expiry |
| `suspended` | Retrieval, policy, status, encoding, media, or size failed closed | Send no new origin request; diagnose and retry only after expiry/policy permits |
| Missing/expired | No exact current run/origin/profile snapshot exists | Send no page request; retrieve a fresh snapshot through admitted authority |

Robots permission does not replace site ownership, scope, consent, legal review,
global origin permits, or current crawl authority.

## Incident Checks

- Confirm the snapshot tenant, site, run, origin, fetch-profile hash, parser release,
  retrieval outcome, expiry, and decision status before inspecting lower layers.
- For redirect rejection, verify every recorded target is an immutable allowed
  origin. Never broaden scope just to make the fetch succeed.
- For `transport_error` or `server_error`, keep the origin suspended. Never rewrite
  it to `not_found` or reuse stale rules beyond their expiry.
- For artifact failure, follow the crawl-artifact integrity/restore runbook. Do not
  parse raw filesystem bytes that lack the exact verified SQL record.
- Preserve malformed evidence for bounded inspection. Do not enable Protego debug
  logging because raw website-controlled rules do not belong in ordinary logs.

## Cache And Parser Changes

Normal current selection uses the newest snapshot with `fetched_at <= now <
expires_at`. A later restriction therefore applies to unsent work immediately.
Historical rows are append-only and must not be edited to change current behavior.

Any Protego upgrade or wrapper behavior change requires a new parser release,
dependency/security review, RFC vector suite, migration/compatibility decision,
ADR, implementation record, and fresh provider evidence. Do not silently reinterpret
stored `rules_hash` values with a different parser profile.

## Migration And Production Boundary

Migration `0022` is forward-only. A failed transaction remains on `0021`; do not
run a destructive downgrade. Migration `0024` now binds an exact current snapshot
to an HTML permit and durable page dispatch. Before production use, add controlled
resolver and egress, robots-request composition, frontier/byte settlement, workflow
integration, monitoring/alerts, distributed artifact storage, and key/retention
lifecycle. No command in this runbook grants production authority.
