# ADR-0072: Bind Standing Grants to Reviewed Releases and Recovery

Status: Accepted for the internal 0088 foundation, 2026-09-29.

## Context

Revision 4.0 requires human-granted, bounded autonomy. Revision 3.2 requires
exact reviewed recipe release IDs and an independently anchored recovery
generation. A live compatible range must not silently include a future release.
Revocation must survive a primary database rollback.

## Decision

The current owner grants one immutable site record through a session-derived,
verified-site database function. The service resolves each requested range
through the signed 0103 registry inside the grant transaction and persists only
the resulting exact release UUIDs. The grant also records the current OpenBao
generation, owner membership and site epochs, work types, thresholds, weekly
volume and spend caps, excluded paths, dates, and recovery window. Only A0,
A1, and bounded A2 work types exist in this contract; A4 and A5 cannot be
selected. The sole seeded release is proposal-only and does not permit a PR.
Any future A2 release must also declare the exact signed standing work type;
an A2 recipe cannot borrow a lower threshold from another A2 category.

Revocation inserts an immutable local denial and a typed 0102 journal outbox
event atomically. It is immediately effective but reports
`AUTHORITY_DURABILITY_PENDING` until the independent receipt is verified. A
restore replays a deny-only standing-grant tombstone, even when the restored
grant predates revocation. Weekly reservations serialize a site-wide counter
across operation IDs and grant replacements; exact retries bind the recipe,
revision, resource path, work type, and cost.

## Alternatives

- Re-resolving ranges at dispatch would silently enlarge a human grant.
- A grant-local counter would let replacement grants reset the weekly cap.
- A primary-only revocation would disappear after a backup restore.

## Consequences

This is an internal authorization foundation, not an autonomous dispatcher.
Production journal deployment, current-generation checks at every future
dispatch, budget attribution for whole jobs, and the 0089 decision gate remain
required before any unattended operation.

## Verification

See [0088 implementation](../implementation/0088-standing-authorization.md) and
[0088 evidence](../evidence/0088-standing-authorization.json). The real
PostgreSQL tests exercise forced RLS, immutable records, current authority,
eligibility, and serialized caps. The independent PostgreSQL/OpenBao journal
lab restores a pre-revocation snapshot and checks that replay denies the
restored grant.
