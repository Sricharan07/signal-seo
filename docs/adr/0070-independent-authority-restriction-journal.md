# ADR-0070: Independently Operated Authority-Restriction Journal

Status: Accepted for the internal 0102 foundation. Production operation is not
qualified.

## Context

Revision 3.2 section 18.5 and INV-025 require restrictions to survive rollback of
Signal's primary PostgreSQL and identity stores. The existing OpenBao recovery
generation fences restored sessions, but does not preserve which individual
membership, session, approval, or standing grant was revoked after a backup.

## Decision

Use a **separate PostgreSQL cluster**, independently provisioned, backed up, and
operated from Signal's primary and identity databases. It is not another schema,
database, or tablespace on the primary server. A one-stream head assigns a UUID
generation and contiguous positions. A narrowly credentialed journal writer can
only call a serialized append function; a separate reader can inspect the stream.
Rows have immutable-entry triggers and no writer update/delete privilege. The
store enforces stable event-ID deduplication and rejects a different encrypted
body or writer signature for the same ID.

The application signs RFC 8785 canonical restrictive records with Ed25519 and
encrypts them with AES-SIV before append. Separate signing and encryption keys
are provisioned outside the primary backup boundary. Replay verifies every
position, generation, hash-chain link, ciphertext digest, signature, exact typed
body, and authenticated current head before opening a primary transaction.
The dispatcher only persists a receipt after reading the record back and
verifying the stream. OpenBao remains the independent recovery-generation
authority; restore replay requires a freshly rotated generation. Replay can only
reinstate denial and record tombstones.

## Alternatives

- A primary-database audit hash or outbox alone can be rewound with its database;
  it cannot prove a lost revocation.
- OpenBao KV v2 CAS can store an independent anchor but lacks a natural ordered,
  immutable multi-event stream and queryable gap-free replay contract. It remains
  the generation authority, not the journal.
- A filesystem append log can be self-hosted but adds difficult fsync, locking,
  replication, and restore semantics. PostgreSQL supplies transactional append
  and explicit privileges while keeping operations familiar.

## Consequences

Self-hosters must operate and retain a second PostgreSQL cluster and protect its
heads, backups, signer, and encryption key beyond the longest supported primary
restore horizon. A journal or key outage leaves local restrictions effective and
durability pending; it must block recovery release. A privileged journal operator
can still destroy the independent cluster, so off-cluster backup, monitoring, and
operational access separation remain release gates. The current repository has
no production journal deployment, signer provisioning, dispatcher supervision,
or emergency egress integration. The 0102 lab uses two real disposable clusters,
real OpenBao rotation, a real `pg_dump`/`pg_restore`, and intentional corruption.
