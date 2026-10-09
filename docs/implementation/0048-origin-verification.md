# Slice 0048: Exact Public-Origin Verification

- Status: Implemented and locally qualified; production composition unavailable
- Date: 2026-09-12
- Milestone: M2 partial
- Specification: Revision 3.2 sections 2 INV-002/INV-003/INV-004, 3.3, 8.5,
  9, 12, 24.2, 25, 31, and 33
- Decision: [ADR-0048](../adr/0048-exact-public-origin-verification.md)
- Runbook: [Origin verification](../runbooks/origin-verification.md)

## Scope

This slice proves control of the selected site's exact configured HTTPS origin
through one expiring well-known plaintext resource. It records issuance and every
attempt, reserves the origin against cross-tenant claims, promotes only exact
success, and exposes strict browser-facing API contracts.

This slice does not add dashboard controls, production identity/resolver wiring,
automatic rechecks, origin changes, crawling, GSC/GitHub/Telegram, agents, approval,
provider writes, or undo.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0028` | Adds forced-RLS challenge, attempt, and verification tables plus a private global origin claim registry |
| Challenge boundary | Owner-only, exact selected site/origin, UUIDv4 idempotency, 30-minute expiry, ten-open-challenge bound |
| Network boundary | Reuses public-address screening and numeric-peer pinning; requests only exact plaintext with no redirect, 1 KiB body, and fixed timeouts |
| Verification boundary | Separates prepare, network observation, and record transactions; rechecks current authority after I/O |
| Exact proof | Requires status 200, `text/plain`, exact final URL, body/digest, peer, and elapsed-time evidence |
| Durable outcomes | Commits immutable success, mismatch, missing, invalid-response, policy, transport, and claim-conflict observations |
| Protected claim | Serializes one canonical origin globally without exposing a conflicting tenant or site |
| Site projection | Exact success sets `active`/`verified`, records one permitted origin and revocation conditions, and schedules recheck at 30 days |
| API | Adds strict tenant-CSRF-protected challenge and verification mutations with closed safe errors |

## Trust And Data Flow

```text
Current owner tenant session + selected unverified site
  -> issue exact 30-minute challenge in PostgreSQL
  -> owner publishes exact plaintext at one fixed HTTPS URL
  -> prepare transaction rechecks live authority, expiry, replay, and limits
  -> transaction closes
  -> pinned public-address HTTP GET, no redirect, bounded plaintext only
  -> record transaction rechecks live authority
  -> immutable sanitized attempt
  -> on exact match: global claim + verification + active/verified site
```

Tenant, actor, role, selected site, session, and recovery generation are derived
from server-side authority. The browser supplies only the expected site route,
exact configured origin, challenge UUID, and UUIDv4 request identities. The
network cannot choose a different accepted origin through DNS rebinding or redirect.

## Verification

Run:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python -m ruff check apps/api services/control_plane \
  database/migrations deploy/crawler-network tests/api tests/control_plane \
  tests/crawler tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane \
  database/migrations deploy/crawler-network tests/api tests/control_plane \
  tests/crawler tests/tooling
npm run test:repo
npm run check:docs
```

Final commands, counts, source hashes, dependency checks, and cleanup state are in
[0048 origin-verification qualification](../evidence/0048-origin-verification.json).
The provider check uses only a disposable Docker internal network and synthetic
proof. No customer origin, data, identity, credential, or external write is used.

## Explicit Limits

- The default FastAPI process does not construct the credential-bearing browser
  gateway or controlled resolver, so these API routes return unavailable until a
  later reviewed deployment composition provides both.
- The dashboard has no issuance, copy, or verify interaction in this backend slice.
- Recheck timestamps and revocation reasons are durable policy inputs; no scheduler
  yet marks an overdue proof `reverification_required` or releases a claim.
- No origin-change endpoint exists. Operators must not edit `app.sites` or the
  protected claim registry to work around a changed origin.
- Verification grants no crawl admission or provider authority. The existing
  crawler remains a separate incomplete M1 path with its own robots, budget,
  workflow, egress, and release gates.
- HTTP is the only implemented proof method. DNS and upstream-provider binding
  remain planned alternatives under the same exact resource policy.
- Production identity, monitoring, abuse controls, retention, restore, and fresh
  browser qualification remain release blockers.

## Next Safe Dependency

Integrate this strict API into the completed dashboard visual system, then add and
real-provider-qualify the least-privilege GSC binding. Production deployment must
also supply a controlled resolver and automatic stale-proof enforcement before
any verified state can authorize later work.
