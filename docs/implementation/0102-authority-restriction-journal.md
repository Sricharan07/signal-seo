# Slice 0102: Authority-Restriction Journal

Status: **INTERNAL FOUNDATION QUALIFIED; PRODUCTION COMPOSITION NOT IMPLEMENTED**.

Security-critical. This implements the Revision 3.2 section 18.5 acceptance and
deny-only replay protocol for the existing `identity.session.revoked` producer.
It does not confer autonomy or production write authority.

## Contract and Ownership

`database/authority_journal.sql` belongs on a separately operated PostgreSQL
cluster, never the business or identity cluster. It contains one encrypted,
signed, hash-chained stream, a generation/head, append-only entries, and a
writer-only deduplicating append function. Runtime keys are supplied separately
from the primary backup. `signal_core.authority_journal` validates canonical
typed records, verifies the whole stream and receipt, retries exact event IDs,
and applies only denial during restore. No tenant can call its primary-database
dispatcher functions or modify journal entries.

Migration 0041 makes the existing browser-session revocation transaction also
create an immutable platform journal intent. The local session is revoked before
external I/O. `control.pending_authority_restrictions(100)` gives the dedicated
`signal_authority_dispatcher` only unreceipted originals. The dispatcher appends
and verifies one record, then persists an immutable
`authority.restriction.acknowledged` platform event. Receipt events are not
re-enqueued. `control.authority_durability_status` reports
`AUTHORITY_DURABILITY_PENDING` until that event exists; the logout HTTP response
is `202` with the same explicit state and clears browser cookies. On timeout or
journal outage the session stays revoked, the original intent remains pending,
and the same ID is retried. If the journal accepted but the receipt was lost,
deduplication recovers the original acknowledgement.

Restore verifies the entire independent stream and a freshly rotated OpenBao
recovery generation. It re-revokes present sessions and writes immutable typed
denial tombstones for every record, including absent targets. Tombstoned session
IDs cannot be inserted or unrevoked. A checkpoint captures the verified head and
new generation only after complete replay in one primary transaction. A missing
position, bad signature, absent head, stale generation, or database error blocks
replay; none can grant authority.

The record schema is intentionally closed to `identity_session/session_revoked`.
Membership removal and site pause have no runtime producers on this branch;
standing-grant revocation, other restriction kinds, and tenant-scoped
`app.audit_events` require explicit typed migrations, authorization checks,
dispatcher routing, and restore handlers rather than interpreting an unknown
record as a grant. The 0103 recipe revocation history is not yet journaled by
this slice. These omissions block recovery qualification for those capabilities.

## Verification

Run from the repository root:

```sh
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/identity/test_authority_journal.py tests/api/test_session_lifecycle.py -q
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/temporal tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/temporal tests/tooling
.venv/bin/python -m pip check
npm test
```

The dedicated lab uses distinct disposable PostgreSQL 17.11 containers and a
TLS-enabled OpenBao 2.6.1 container. It dumps the primary before logout, writes
the revocation to the independent cluster, restores the actual dump into a fresh
primary database, rotates the real OpenBao generation, and replays restrictions.
It also exercises outage, lost receipt, duplicate/conflicting IDs, absent-target
tombstones, immutable writer privileges, missing segments, corrupt signatures,
and unavailable heads. The lab prints its scenario count and fails on any
assertion. It uses only synthetic identities and loopback-bound disposable
providers. Evidence: [0102](../evidence/0102-authority-journal.json).

## Release Limits

There is no supervised production dispatcher, second-cluster deployment,
external key provisioning/rotation, independent journal backup or retention
monitoring, emergency egress control, full backup-manifest watermark, or recovery
release gate. The full stream is read on each append/replay, so bounded chunked
verification is needed before high-volume operation. Logout's `202` is a local
stop plus pending durability, not a claim of independent completion. No standing
authorization, permission reduction command, or production write is enabled.
