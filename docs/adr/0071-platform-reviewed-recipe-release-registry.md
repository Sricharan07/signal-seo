# ADR-0071: Keep Recipe Releases Platform-Owned and Immutable

Status: Accepted for an internal foundation. Date: 2026-09-29.

## Context

Standing authorization must reference reviewed executable policy, not a mutable
tenant-authored recipe name. Revision 4.0 requires immutable compatible release
IDs at grant time and immediate invalidation when a release is revoked. The
existing verified-homepage metadata draft is proposal-only and must not acquire
GitHub authority by being entered into a registry.

## Decision

Use `control.recipe_releases` for immutable canonical RFC 8785 manifests, a
generated SHA-256 content hash, an Ed25519 writer signature, and a platform
signing-key registry. Use append-only `control.recipe_release_events` for
`DRAFT -> TESTED -> REVIEWED` and later status transitions. Only the dedicated
platform `signal_release_manager` role can call registration and transition
functions. Tenants, the API, and workflow roles cannot mutate releases or
history. Read resolution verifies the signed bytes, exact identity, content
hash, and compatible version range before returning an immutable tuple of IDs
for a future standing grant to persist.

Revocation is a local status change in the same transaction as an independent
authority-journal intent. It makes dispatch ineligible immediately, remains
pending until a verified journal receipt, and is replayed only as a typed denial
after restore. The seeded verified-homepage release is REVIEWED solely for
proposal generation and explicitly cannot dispatch an external write.

## Alternatives

A tenant-editable recipe catalog would violate INV-020. A mutable version alias
would allow grants to silently expand. Treating a database-only revocation as
durable would lose the restriction across a backup restore. Each is rejected.

## Consequences

Recipe operators need controlled signing-key and manager-role provisioning.
The registry is not a package deployment system and has no executable recipe
runner. Future grant code must persist the resolved IDs, not the range, and
future dispatch code must recheck each ID's current signed status alongside all
other authority gates. A separate production journal deployment, key lifecycle,
and recovery release gate remain prerequisites for use beyond the lab.

## Verification

See [slice 0103](../implementation/0103-recipe-release-registry.md) and its
[evidence](../evidence/0103-recipe-release-registry.json). The real PostgreSQL
lab checks role denial, immutable records, signature rejection, range freezing,
status transitions, and revocation. The separate-cluster journal lab restores a
pre-revocation primary dump, rotates the real OpenBao generation, and replays the
recipe denial.
