# Slice 0088: Standing Authorization

Status: **INTERNAL AUTHORITY FOUNDATION; NO AUTONOMOUS DISPATCH OR PRODUCTION WRITE**.

Security-critical. Migration 0049 adds immutable, forced-RLS standing grants and
revocations. The current owner alone can grant or revoke for the selected,
verified site through the API and same-origin dashboard BFF. A grant records
owner and site epochs, the OpenBao recovery generation, exact signed and
reviewed 0103 release IDs resolved once at grant time, work types, thresholds,
weekly caps, excluded paths, dates, and recovery window. A4/A5 are absent from
the allowlist; the dashboard exposes only the seeded proposal-only draft
release. Pull requests and production writes remain unavailable.
Future A2 releases must carry an exact signed standing work type, preventing a
content recipe from borrowing a metadata threshold.

The 0102 restriction journal receives a stable `standing_grant_revoked` event
from the same local transaction as the revocation. Local eligibility stops
immediately. Until the independent journal receipt is verified, the API and
dashboard show `AUTHORITY_DURABILITY_PENDING`. Restore replay writes a
deny-only tombstone; it never re-enables a restored grant. Eligibility also
checks current membership, site epoch, recipe status, exact release ID, grant
window, excluded paths, and the supplied external recovery generation.

One site-wide UTC-week counter serializes volume and spend reservations across
operation IDs and replacement grants. Exact retries bind the recipe release,
revision digest, path, work type, and cost. This is a reservation primitive,
not an execution permit: the 0089 gate and a qualified external-write path are
still required.

## Verification

Run every command recorded in [0088 evidence](../evidence/0088-standing-authorization.json).
The PostgreSQL lab uses disposable real PostgreSQL 17.11 with non-owner roles.
The authority-journal lab uses a second independent PostgreSQL server and real
OpenBao, takes a pre-revocation `pg_dump`, revokes and acknowledges, restores
the old primary, rotates the external generation, replays the journal, and
verifies the restored grant cannot pass eligibility. API and dashboard tests
cover owner-only routing, malformed requests, CSRF, and visible pending state.

## Limits

There is no autonomous job or production-composed authorizer yet. The gate must
obtain the current recovery generation independently at use, and no caller may
treat a reservation as permission to execute. Whole-job impact and spend
attribution need the future sealed-work contract; this slice cannot certify
unattended PRs. Production journal dispatcher, custody, monitoring, and release
qualification are absent.
