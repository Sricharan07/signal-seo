# ADR-0102: Owner-Reviewed IndexNow Key Publication

Status: Accepted for internal qualification; live publication NOT_EXECUTED.
Date: 2026-10-02

## Context

Revision 4.0 section 12 requires a verified-site IndexNow key file delivered by
Signal PR, not a direct repository or deployment write. The existing Inbox seals
the complete exact patch. IndexNow keys are public proof files, not bearer access
to customer repositories. The authoritative key must remain in OpenBao.

## Decision

Generate a 64-character lowercase hexadecimal key once per site/key generation,
using OpenBao KV v2 CAS-zero and version-one reads. Store only generation identity
and SHA-256 in lifecycle tables. The intentional publication exception is the
sealed exact public-file patch, reviewed GitHub publication, and exact public
key-file URL in shared-egress metadata; do not claim that the public key never
appears outside OpenBao.

Add `technical_indexnow_key` to the reviewed release/Inbox/0084 path, not 0104's
closed A2 set. Require owner review and fresh MFA. Add exactly `<key>.txt` at the
repository root, with exactly the key bytes and no newline. Support only the
existing Eleventy HTML extension whose isolated build already copies the file to
`_site/<key>.txt`; never edit build configuration to manufacture support.

Rotation creates a new generation and reviewed PR proposal plus an immutable
old-key retirement record. Retired keys cannot start PR delivery or notification.
Do not delete old repository files or silently enlarge standing authority.

## Alternatives

Direct key deployment bypasses owner review. Adding passthrough configuration
would exceed this one-file recipe. Persisting an independent plaintext credential
copy would create an unnecessary second authoritative secret store.

## Consequences

Key publication is inspectable in Inbox. Unsupported static output stays
unavailable. Retirement at replacement sealing may temporarily stop notification
until the replacement PR is opened and deployed. Customer merge/deploy remains
outside Signal. Production composition and live GitHub publication remain absent.

## Verification

See [0086](../implementation/0086-indexnow.md) and its
[evidence](../evidence/0086-indexnow.json): OpenBao CAS/ACL checks, real PostgreSQL
owner/tenant/role/review/rotation negatives, exact one-file Git reconstruction,
isolated static-output verification, and unchanged A2 eligibility.
