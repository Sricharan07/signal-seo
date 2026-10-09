# Slice 0103: Reviewed Recipe-Release Registry

Status: **INTERNAL FOUNDATION QUALIFIED; NO EXECUTABLE RECIPE OR PRODUCTION DISPATCH**.

Security-critical. Migration 0042 adds immutable platform-owned release bytes,
SHA-256 hashes, Ed25519 signatures, signing keys, and append-only lifecycle
events in the control schema. `signal_release_manager` alone can register a key
or release and advance status. Tenants and workflow roles cannot write registry
tables or transitions. The first signed REVIEWED release records the existing
verified-homepage metadata proposal contract. Its `proposal_only` delivery mode
is not external-write authority.

## Resolution Contract

`signal_core.recipe_releases.resolve_reviewed_recipe_range` accepts a bounded
compatible `[minimum_inclusive, maximum_exclusive)` semver range and returns a
tuple of exact reviewed release UUIDs in version order. It verifies canonical
manifest bytes, hash, signature, recipe family, ID, and version on every read.
Future standing-grant code must call it inside grant creation and persist that
exact tuple. It must never resolve the range again during dispatch. The current
registry has no grant table, runner, or GitHub write path.

`recipe_release_dispatch_eligible` is only the recipe-status portion of future
dispatch policy; it cannot substitute for standing authorization, deterministic
policy, Jev's restrict-only gate, or provider permissions. A release must be
currently REVIEWED, signed and intact, not tombstoned, and
`pull_request` delivery mode. The seeded proposal-only release is false.

## Revocation and Recovery

An operator REVOKED transition adds a platform event and local 0102 journal
intent atomically. The current status denies dispatch immediately and
`AUTHORITY_DURABILITY_PENDING` remains until the independent encrypted,
signed journal acknowledges it. Replay after a real primary restore writes a
typed `recipe_release_revoked` tombstone and append-only REVOKED event. An
absent release ID remains tombstoned and cannot be registered later. Replay
never changes a release to a permissive state.

## Verification

Run from the repository root:

```sh
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/temporal tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/temporal tests/tooling
.venv/bin/python -m pip check
npm test
```

The PostgreSQL lab uses a disposable real PostgreSQL 17.11 server and separate
non-owner roles. The authority lab uses a second independent PostgreSQL 17.11
server and real OpenBao 2.6.1. It takes a real pre-revocation `pg_dump`, revokes
the release and journals it, restores the dump into a fresh database, rotates
the external recovery generation, and applies only the denial. It also checks
missing journal segments, bad signatures, and unavailable heads. Evidence:
[0103](../evidence/0103-recipe-release-registry.json).

## Limits

The manager role and signing keys are lab-provisioned; production custody,
rotation, an independently operated journal, monitored dispatcher, backup
watermark, and recovery release gate are absent. No technical-SEO recipe or
standing grant exists yet. Recipe revocation is the first new restriction kind;
future standing-grant revocation, membership removal, and site pause need their
own typed journal producers and replay handlers.
