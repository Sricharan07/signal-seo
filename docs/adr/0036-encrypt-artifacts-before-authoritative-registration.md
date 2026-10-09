# ADR-0036: Encrypt Artifacts Before Authoritative Registration

- Status: Accepted
- Date: 2026-09-09
- Owners: Crawler evidence, data integrity, and security

## Context

Slice 0035 can issue one exact durable frontier lease, and Slice 0034 can return a
bounded HTML response from one admitted numeric peer. Neither boundary can retain
the response as evidence. Storing raw HTML in PostgreSQL would mix large private
objects with transactional records. Writing an object and then independently
inserting database rows also creates an unavoidable cross-store failure window.

The active specification requires encrypted immutable artifacts, append-only fetch
observations, exact hashes and references in PostgreSQL, integrity monitoring, and
grace-period cleanup when object publication succeeds but SQL registration fails.
It also requires an unreadable or damaged object to be blocked rather than treated
as valid evidence. This slice has no production object service, robots decision,
global origin admission, workflow composition, or retention authority.

## Decision

Add a private local filesystem artifact driver as the first qualified backend. It
publishes an authenticated AES-256-GCM envelope to a deterministic tenant/site/
artifact/plaintext-hash key. Each encryption uses a random 96-bit nonce. The
canonical envelope header is authenticated associated data and records identity,
hash, length, media type, key reference, and creation time. Key material is supplied
by the caller and is never persisted or represented in ordinary diagnostics.

Require a canonical absolute owner-only directory without symbolic-link components.
Create owner-only directories and files, reject non-regular or non-private objects,
write and fsync a temporary file, and publish by no-overwrite hard link while holding
an owner-only per-artifact file lock. Exact retries authenticate and compare the
existing object; conflicting content or metadata never replaces it.

Add forced-RLS `app.artifacts`, `app.artifact_attestations`, and
`app.fetch_observations` tables. Register one fetched body, its upload-readback
attestation, and its fetch observation in one PostgreSQL transaction. Bind every
observation to the exact tenant-unique frontier lease receipt, run, URL, worker,
timestamps, canonical redirect chain, public numeric address, sanitized headers,
network profile hash, and optional artifact. A fetch that started during a valid
lease may record its completed evidence after lease expiry or authority reduction;
that convergence does not grant new fetch authority.

Place lookup, commit, and integrity transitions behind three `SECURITY DEFINER`
functions granted only to a new function-only `signal_crawl_ingest` role. Runtime
roles receive no direct access to the three tables. Artifact and observation
identities are deterministic; exact retries return the committed evidence and a
different retry conflicts.

Treat durability as an attested projection. Upload readback creates the initial
`verified` attestation. Scheduled and restore checks append a new immutable
attestation before atomically changing `durability_state`. Ordinary reads require
the current state to be `verified`; the attestation path alone may inspect a
non-verified object to prove a restore. Authenticated-decryption failure is recorded
as `corrupt`, while absent and inaccessible objects remain distinct states.

If object publication succeeds and SQL fails, leave a non-authoritative orphan.
A bounded reconciler scans only parseable `.sig` envelopes, preserves all objects
inside the grace period, locks each eligible artifact, queries the exact database
identity, preserves exact registrations, and deletes only old unregistered objects.
Unreadable, malformed, and symbolic-link candidates are reported but not deleted.

## Alternatives

- Store raw HTML in PostgreSQL. Rejected because large evidence belongs in private
  artifact storage while PostgreSQL retains authoritative references and hashes.
- Insert the database row before writing the object. Rejected because committed
  authority could point at an object that never existed; upload-readback must occur
  before registration.
- Overwrite a deterministic path on retry. Rejected because a conflicting retry
  could silently replace evidence already referenced by an observation.
- Delete every unregistered object immediately. Rejected because a concurrent or
  acknowledgement-lost registration may still be converging.
- Allow reads whenever ciphertext decrypts. Rejected because it bypasses the
  durability projection and restore-verification requirement.
- Add S3, retention deletion, key rotation, robots, frontier settlement, and
  workflow execution now. Rejected because each adds a separate authority or
  provider failure contract that needs its own bounded slice.

## Consequences

- A fetched HTML body can now be retained privately with an exact SQL identity,
  immutable observation, and integrity history.
- PostgreSQL and filesystem publication are not one transaction. The database is
  authoritative, and the explicit orphan reconciler owns the safe failure case.
- The local driver is suitable for qualification and a single-host runtime only.
  Shared/distributed object storage requires a separately tested backend with the
  same immutability and reconciliation contract.
- Artifact key references are durable, but key provisioning, rotation, escrow, and
  loss recovery remain external and unimplemented.
- The new direct `cryptography` dependency is pinned at `50.0.1`; its upstream
  license expression is Apache-2.0 OR BSD-3-Clause. This is dependency metadata,
  not a project license decision or a vulnerability audit.
- No production crawler, retention deletion, legal-hold mutation, public egress,
  workflow registration, or release authority is created.

## Verification

Unit and real PostgreSQL tests cover encryption without plaintext leakage, exact and
concurrent retry, content/key/metadata conflicts, tampering, private permissions,
symbolic-link rejection, bounded writes, fetched and body-free observations,
sanitized headers, public-address and timestamp validation, wrong scope/worker,
post-expiry recording, append-only state, integrity failure, restore verification,
grace-period orphan preservation/deletion, function-only privileges, connection
hygiene, and transactional migration rollback.
