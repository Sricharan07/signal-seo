# ADR-0035: Bind Crawl Leases To Immutable Run Snapshots

- Status: Accepted
- Date: 2026-09-09
- Owners: Crawler workflow, crawler admission, and data integrity

## Context

The URL and address-pinned HTTP boundary from Slice 0034 can safely describe and
contact one admitted destination, but it has no durable answer to which workflow
authorized a crawl, which policy and budgets governed it, whether a discovered URL
was already seen, or which worker currently owns a fetch attempt. Wiring that
boundary directly into Temporal would make retries and process restarts depend on
ephemeral memory.

The active specification requires stable URL identity, immutable run scope,
discovery provenance, bounded attempts, and lease-safe scheduling. Those decisions
are contested database transitions. Application-side check-then-insert logic would
permit duplicate runs, URL-budget oversubscription, or simultaneous claims under
concurrency. At the same time, this slice has no robots, origin-wide admission,
artifact, fetch-observation, or completion contract and must not invent their
terminal evidence.

## Decision

Add four forced-RLS site tables:

- `app.crawl_runs` binds one accepted command and exact running Temporal first run
  to immutable, schema-validated scope and limit snapshots plus a fetch-profile
  SHA-256;
- `app.urls` stores one stable original/fetch/key/origin identity per site and
  normalization version; and
- `app.crawl_frontier` stores one URL per run with its source URL, depth, discovery
  reason, priority, not-before time, attempt count, and current lease; and
- `app.crawl_frontier_leases` stores one immutable receipt per issued lease and
  attempt.

Generate run, URL, and frontier UUIDs deterministically from their durable business
identities. Exact retries therefore reproduce the same identifiers without random
values becoming workflow-history inputs. A conflicting second configuration for
the same command is rejected rather than replacing the accepted run.

Place all mutations behind three `SECURITY DEFINER` functions granted only to a
new non-owner, non-login, non-`BYPASSRLS` `signal_crawl_admission` role. The role
has no direct table privileges. Private validator and trigger functions remain
unexecutable by public and runtime roles.

`app.crawl_frontier_leases` is a physical implementation split of the canonical
frontier's lease history, not a new product domain. Its append-only lifecycle and
historical uniqueness differ from the mutable current frontier row, which is the
catalog's documented reason to split a logical table.

Serialize first-run opening on the exact workflow/command rows. Serialize URL
admission on the run row so concurrent discoveries cannot exceed the URL budget.
Claims use database time, short caller-supplied lease UUIDs, bounded durations,
attempt ceilings, row locks, and `SKIP LOCKED`. Only one live lease per origin is
issued within a run, and its expiry cannot exceed the immutable run deadline. An
exact live lease retry returns its original evidence;
an expired lease identity is unavailable. The receipt's tenant/lease primary key
and per-frontier attempt uniqueness prevent a delayed stale or concurrent claim
from acquiring different work. A later fresh claim may reassign the item until the
immutable attempt ceiling is reached.

Return already-accepted run, enqueue, and live-lease retries before applying
current authority reductions so acknowledgement loss can converge. New discovery
and new leases require the workflow to remain running, the tenant and site to
remain active, and the run duration to remain open. New discovery also enforces
exact allowed origin, source provenance, depth, and URL-count budgets.

Keep the only run status `running` and frontier terminal state in this slice to
`permanently_failed` after lease exhaustion. Do not fabricate fetched, robots,
artifact, parser, byte-budget, coverage, or final-manifest state before those
boundaries can commit their own evidence.

## Alternatives

- Keep the frontier in Temporal history. Rejected because URL sets and mutable
  leases are large operational state, while policy identity and workflow progress
  should remain compact deterministic history.
- Let each crawler read and write the tables directly. Rejected because callers
  could bypass current authority, budgets, provenance, and transition guards.
- Use random IDs on every retry. Rejected because an ambiguous response could
  create duplicate logical runs or frontier items.
- Check URL budgets in Python before inserting. Rejected because concurrent
  workers could both observe capacity and oversubscribe it.
- Add fetch observations, artifacts, robots, and finalization in the same slice.
  Rejected because cross-store artifact durability and fetch reconciliation need
  their own complete failure semantics and provider evidence.
- Claim origin-wide politeness from a per-run lease. Rejected because two tenants
  or two runs can share an origin; the required global bucket/admission boundary
  is still absent.

## Consequences

- A restarted activity can reopen or continue one exact crawl without rebuilding
  scope from mutable configuration.
- Run-level locking favors correctness over maximum claim throughput. It is a
  conservative first implementation and must be measured before changing lock
  granularity.
- URL-count, depth, duration, and attempt ceilings are enforced now. Total-byte
  settlement waits for append-only fetch observations and cannot yet be claimed.
- Existing active lease evidence survives an authority reduction, but no new
  discovery or lease is granted. This does not authorize a network request by
  itself; future origin admission and robots decisions remain mandatory.
- No production crawler, public egress, artifact retention, complete coverage, or
  Core V1 release authority is created.

## Verification

Real PostgreSQL tests cover exact persistence, malformed limits, workflow binding,
idempotent and conflicting retries, concurrent opening, normalized URL deduplication,
provenance, scope/depth/count/duration ceilings, concurrent budget admission,
live and expired lease identity, concurrent origin claims, reassignment, attempt
exhaustion, authority reduction, immutable transitions, cross-site foreign keys,
function-only privileges, transaction cleanup, and transactional migration failure.
