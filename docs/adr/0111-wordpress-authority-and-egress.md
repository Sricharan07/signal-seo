# ADR-0111: Bind WordPress Draft Dispatch To Current Authority And The Journal

Status: Accepted for slice 0099's internal optional boundary.
Date: 2026-10-02.

## Context

An external CMS write combines a provider credential, tenant authority, network
admission and an irreversible ambiguity window. ADR-0083 shared egress, the 0102
restriction journal and 0084 write-intent journal remain the common boundaries.
A restored primary must not repeat an independently acknowledged dispatch.

## Decision

An owner binds one verified HTTPS origin and one dedicated application-password
user. A subdomain must independently pass the existing exact-origin proof path;
there is no arbitrary alternate-origin exemption. Provision credentials only in
OpenBao KV v2 at `signal-wordpress/bindings/<binding UUID>`, as exact `username`,
`password`, `tenant_id`, `site_id` and `origin` fields. The owner session derives
the trusted tenant/site/origin; the operator's assignment must match before the
credential can authorize even the first current-user GET. A client-supplied binding
UUID is never sufficient authority to read and transmit another site's credential.
No credential form or database credential column exists.
Use a least-privilege read policy, TLS-verified OpenBao and private connector egress.

Read `/wp-json/wp/v2/users/me?context=edit` at binding and before each operation.
Accept Author or a narrowly enumerated custom role. Refuse Administrator, Editor,
unknown enabled capabilities, and plugin, theme, user, option, other-author
edit/delete or management capabilities. Standard Author own-post delete
capabilities are observed but cannot be exercised by the profile. Any change to
recorded roles, capabilities or user requires a new binding. Revocation denies
locally before independent acknowledgement; outbox and restore replay are deny-only.

Add a separate closed `WORDPRESS_REST` profile without widening other profiles:
HTTPS on the bound origin, Basic auth, JSON, GET/POST, 64 KiB request, 128 KiB
response, ten-second timeout and no redirects. GET routes are only exact current
user, own-author slug reconciliation and the recorded id in edit context. POST is
only exact new draft creation. Method overrides, encoded routes, existing id
writes, pages, media, comments, settings, plugins, themes and users writes are
denied. Python and database admission both check scope; robots and shared origin
admission still precede dispatch.

Seal the 0082 result into canonical draft title/content/excerpt. Immutable owner
review captures membership and site epochs. Queue and claim recheck current owner
session, tenant/site, recovery and origin-proof generations, binding, approved
facts, unsuperseded brief, review and weekly Content Writer cap. POST admission
again checks dispatch session, owner epochs, pause, current binding, marker and
exact approved message digest. No standing grant or model can substitute for review.

The independent encrypted, signed, hash-chained journal receives a tagged record
for each permitted dispatch attempt. Its deterministic operation UUID binds
intent, attempt 1 or 2, site, binding, sealed revision, exact message digest,
marker and recovery generation; it carries no content or credential. Replaying
an acknowledged attempt while the primary says queued/retry quarantines it
without POST. A second attempt is independently detectable after primary rollback.
A crash after acknowledgement but before dispatch is conservatively unknown.
Concurrent claims cannot produce two POSTs. Immutable primary receipts preserve
recorded outcomes and observations.

## Alternatives

- A new provider service or generic HTTP escape hatch is unnecessary and rejected.
- Credentials, Basic values and provider bodies in rows/errors/journals are rejected.
- One journal entry for both attempts is rejected: rollback could repeat attempt 2.
- Publishing with an owner grant is excluded by ADR-0110, not emulated.

## Consequences And Verification

Unconfigured ports are visibly unavailable. Production composition explicitly
injects existing API/identity connections, current OpenBao recovery authority,
credential reader, independent journal and shared-egress factory. The optional
public live-verifier port is credential-free. No always-running service is added.

Migration 0073 follows unchanged 0072, adds five forced-RLS tables and narrow
security-definer RPCs, and grants no runtime table rights. Tests cover real
primary/journal PostgreSQL, dispatch replay, idempotent claims, epoch/pause/recovery/
tenant/role reductions, revocation replay, provider capabilities, OpenBao ACLs and
credential redaction and cross-tenant/site/origin credential assignment before
provider I/O. See [0099](../implementation/0099-wordpress-drafts.md).
