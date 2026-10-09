# ADR-0066: Bind GitHub Read Authority to an Owner and Site

- Status: Accepted
- Date: 2026-09-29
- Owners: Signal connector and identity boundaries
- Related: [ADR-0059](0059-downscope-github-app-repository-inspection.md), [ADR-0062](0062-record-shared-egress-before-network-io.md), Revision 4.0 sections 4 and 12

## Context

The read-only GitHub App protocol from slice 0062 checks a repository and branch
but cannot prove that an owner selected them for a Signal site. A transient
inspection result must not become durable authority by itself. GitHub credentials
must stay out of PostgreSQL, and connector calls must use the shared egress
boundary rather than silently using the adapter's direct HTTP default.

## Decision

Commit one owner/session/site-attributed selection before provider I/O. Bind its
installation, repository, protected base branch, content path, request digest,
current authority epochs, and recovery generation. A successful provider
observation activates only that exact selection; a sanitized failure closes it.
Keep separate immutable lifecycle events and a revocable state projection under
forced RLS and function-only identity access. A current owner may revoke even a
prepared selection; completion after revocation fails. New binding requests for
a site require revocation or failure of the previous one.

Read the App ID and private key only through an exact, read-only OpenBao KV v2
path. Mint only the 0062 one-repository `contents: read` token. Reinspect the
repository through an explicitly supplied shared-egress transport before each
use, comparing immutable repository identity and current branch protection.
The binding service refuses to make a live GitHub call without that transport.

## Alternatives

- Store a PAT, key, or installation token in PostgreSQL. Rejected as broader and
  harder to revoke than a narrow App and OpenBao credential.
- Treat the 0062 direct HTTP transport as a production connector. Rejected
  because it would bypass the shared-egress admission and evidence controls.
- Consider a stored successful inspection permanent access. Rejected because
  installation permissions, repository selection, and branch protection change.
- Let repository text or a model select or expand the binding. Rejected because
  only the current human owner can grant this resource association.

## Consequences

The internal service can hold a durable read binding locally. It has no PR,
branch-write, merge, deployment, or secret-reading permission. The browser
connector screen and owner-controlled live GitHub qualification are not yet
complete; absence of a configured provider and shared-egress context remains
visibly unavailable. This does not admit R1 or the internal beta.

## Verification

See [slice 0070](../implementation/0070-github-read-binding.md) and its
[evidence record](../evidence/0070-github-read-binding.json).
