# ADR-0083: Scope shared egress by request profile

Status: Accepted 2026-09-29 for the local internal boundary; production composition is unavailable.

## Context

The shared gateway originally carried bounded JSON model requests. GitHub read
inspection added its REST media type and version header. Google OAuth needs a
form-encoded token POST, while crawl pages need HTML. A union of those shapes at
the shared request interface would give every caller authority it did not have.
Revision 4.0 INV-029 and ADR-0062 require one egress path, not one permissive
request shape.

## Decision

Every shared request names a typed member of a closed profile set. A profile
fixes purpose, admitted origin (named provider origins are exact; the pre-existing
generic model JSON and crawl/browser origins remain constrained by the run's
exact admitted scope), methods, request Content-Type,
fixed Accept and additional headers, whether bearer authorization is required,
response media types, body sizes, and timeout. Caller input can supply only the
bearer value where required, a Gemini API key only on its own profile, and the
bounded request body. Credential values are redacted from the request digest.
No profile permits cookies. The HTTP boundary retains public-address screening, pinning, redirect
denial, robots evidence, and global origin admission for all profiles.

The profile name enters the canonical request digest. Admission binds its name to
the immutable operation in the same transaction as dispatch, before network I/O;
completion preserves it. The separate robots-dispatch record has a constrained
`crawl_robots` identity. An old SQL-only operation is explicitly marked
`legacy_unqualified` and cannot be mistaken for a profiled gateway dispatch.
Google OAuth token and revocation profiles are separate; only revocation may
accept Google's non-JSON response. GitHub's REST profile alone accepts its vendor
JSON response and API-version header. Bing OAuth has its own form profile, while
the Bing API remains JSON-only. Crawl/browser profiles never authorize.

## Alternatives

- A global union of headers, media types, and methods was rejected because it
  silently widens every caller.
- Caller-defined profile objects were rejected because they reproduce the same
  authority problem under another name.
- Separate network stacks were rejected because they bypass shared admission and
  durable egress evidence.

## Consequences and verification

New provider capabilities need a reviewed profile and negative cross-profile
tests before use. Profile identity is visible in immutable operation evidence,
while credential values remain absent. Unit and PostgreSQL tests cover forbidden
method, media, header, origin, credential, size, and replay shapes; the 0069
implementation record contains the local commands and qualification limits.
