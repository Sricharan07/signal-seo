# Slice 0036: Encrypted Artifact And Fetch Observation Durability

- Status: Implemented and real-PostgreSQL-qualified; production crawl disabled
- Date: 2026-09-09
- Milestone: M1 partial
- Specification: Revision 3.2 sections 2, 3, 6, 7, 11, 13.1, 19.1, 27,
  29, 30, 32 G-01/G-03/G-04, 33 Milestones 1-2, Appendix A artifact and
  fetch-observation records, and EC-089/EC-090
- Decision: [ADR-0036](../adr/0036-encrypt-artifacts-before-authoritative-registration.md)

## Scope

This slice retains the qualified fetch boundary's HTML/XHTML evidence as an
encrypted immutable object and atomically registers its authoritative metadata,
initial readback attestation, and append-only fetch observation in PostgreSQL. It
also provides integrity/restore attestations and bounded orphan reconciliation for
the object-first, SQL-second failure window.

It does not connect the HTTP boundary or frontier to Temporal, perform a public
fetch, settle run byte/frontier state, enforce robots or global origin politeness,
parse page facts, delete retained artifacts, rotate keys, or enable customer use.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0021` | Adds forced-RLS artifact, attestation, and fetch-observation tables; exact scoped foreign keys; append-only guards; validation functions; query indexes; and transactional rollback |
| Artifact backend | Uses owner-only canonical filesystem paths, AES-256-GCM authenticated envelopes, random nonces, deterministic object keys, atomic no-overwrite publication, fsync, and per-artifact locks |
| Observation commit | Registers an optional fetched-body artifact, upload-readback attestation, and exact lease-bound observation in one short SQL transaction |
| Evidence contract | Retains only a closed header set, canonical redirects, one screened public address, wall/elapsed time, response outcome, profile hash, and exact artifact reference |
| Integrity | Appends scheduled/restore attestations and changes durability only through an exact transaction-local attestation; ordinary reads fail closed unless state is `verified` |
| Reconciliation | Preserves recent or exactly registered objects and deletes only old parseable unregistered objects under the same artifact lock |
| Runtime role | Adds function-only `signal_crawl_ingest`; it owns no table, has no direct table grant, cannot migrate, and does not bypass RLS |

`persist_fetch_observation` validates all application evidence before publishing an
object, then holds the artifact lock through authenticated readback and the short
database commit. Fetched HTML/XHTML requires a 256-bit key, key reference, body
hash, byte length, and future retention deadline. Explicit body-free outcomes do
not fabricate artifact rows.

The database independently revalidates the lease identity, worker, scope, allowed
URLs, redirect set, public address, header JSON types, elapsed/wall-clock agreement,
body ceiling, and network profile hash. An exact tenant-wide fetch-attempt retry
returns the original record. Different evidence for that attempt returns a closed
conflict without replacing it.

## Data Contract

`app.artifacts` stores the tenant/site artifact UUID, deterministic object key and
version, plaintext SHA-256, byte length, media type, encryption-key reference,
creation/retention times, legal-hold flag, and current durability projection. It
does not store plaintext, ciphertext, or key material.

`app.artifact_attestations` is the append-only source for upload readback,
scheduled integrity, and restore-verification results. A `verified` result carries
the exact registered plaintext hash; `missing`, `corrupt`, and `unreadable` do not.

`app.fetch_observations` stores one immutable result per tenant-unique fetch attempt,
bound by composite foreign keys to the exact run, frontier, URL, and lease receipt.
It keeps sanitized response metadata and an optional exact raw-artifact reference.
It stores no cookies, authorization headers, reusable credentials, body bytes,
provider exception text, or model-generated interpretation.

The encrypted `.sig` envelope is not authoritative by itself. An object becomes
usable evidence only when its exact SQL record is present and currently `verified`.

## Verification

Run:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/pytest -q tests/tooling/test_crawl_artifacts.py
```

The slice adds 20 real-database cases and nine filesystem/crypto cases. The
complete disposable PostgreSQL 17.11 suite passes 432
cases, including migration failure and cleanup. The runner provisions random
temporary role credentials, publishes PostgreSQL only on an ephemeral loopback
port, uses tmpfs, records source hashes, and confirms invocation-owned cleanup.

The surrounding boundaries also pass 562 non-database Python cases, 171 API cases,
three real Temporal cases, one joint PostgreSQL/Temporal consumer case, six
workflow-consumer image cases, five isolated crawler-network cases, five Keycloak
cases, seven OpenBao cases, 16 repository cases, and checks across 98 Markdown
files.

- [0036 PostgreSQL](../evidence/0036-postgresql.json)
- [0036 Temporal](../evidence/0036-temporal.json)
- [0036 joint consumer](../evidence/0036-consumer.json)
- [0036 workflow-consumer image](../evidence/0036-workflow-consumer-image.json)
- [0036 crawler network](../evidence/0036-crawler-network.json)
- [0036 Keycloak](../evidence/0036-keycloak.json)
- [0036 OpenBao](../evidence/0036-openbao.json)

All checked-in evidence source hashes match the final implementation inputs. Every
provider record reports completed cleanup and production authority false.

## Explicit Limits

- The filesystem backend is not a distributed artifact service and is not deployed.
- Encryption keys are caller-supplied; no OpenBao artifact-key path, rotation,
  escrow, or disaster-recovery procedure exists yet.
- Retention and legal-hold values are immutable in this slice. Expiry selection,
  deletion authorization, export, holds, and provider deletion receipts are due.
- Orphan cleanup is an internal bounded function, not a deployed schedule. It never
  deletes malformed/unreadable candidates automatically.
- The database does not advance frontier terminal state or settle total bytes, and
  the observation is not yet included in a final coverage manifest.
- Transport failures, retry policy, robots, global cross-tenant origin admission,
  controlled production resolver/egress, parser facts, and workflow composition
  remain absent.
- Passing this slice does not release crawling, prove restore readiness, or satisfy
  G-03/G-04.

## Next Safe Dependency

Add robots evidence and global origin admission before composing frontier claim,
the pinned HTTP fetcher, encrypted observation persistence, frontier/byte
settlement, and final coverage in the Temporal crawl executor.
