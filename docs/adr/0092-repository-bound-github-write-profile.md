# ADR-0092: Bind GitHub writes to a separate repository profile

Status: Accepted 2026-09-29 for the internal beta path; live qualification remains NOT_EXECUTED.

## Decision

Add the closed `github_repository_write` profile without widening existing
profiles. Its sole origin is `https://api.github.com`; methods are GET and POST.
POST creates Git trees, blobs, commits, operation-specific refs, or pull requests
under one bound `/repos/{owner}/{repository}`. GET looks up exact Git SHAs, the
deterministic Signal branch, or a bounded exact-head/base pull-request list.
No PUT, PATCH, DELETE, merge, workflow, Actions, administration, secrets,
default-branch, or other-repository route exists.

The frozen scope carries the selected repository and installation token. The
adapter creates it only after the existing token-response validator confirms one
selected repository and contents/write, pull_requests/write, plus optional
metadata/read. App JWT token exchange stays on GitHub REST and cannot be used as
the scoped write token. REST is narrowed to GET plus installation-token POST;
other profiles cannot borrow repository mutations. Headers come from the profile:
JSON only, 128 KiB request, 256 KiB JSON response, five-second timeout, no redirects
or cookies. Repository identity enters the redacted request digest, never the token.

Migration 0054 binds the separate profile in PostgreSQL before I/O and checks the
repository prefix, closed route/method and size. Existing immutable egress and
assistant guards remain intact. Exact sealed-body validation, current authority,
independent write journal, and last-moment lease fencing remain mandatory; the
profile alone grants no authority. 0085 uses GitHub REST reads and crawl GET,
never the repository-write scope.
Pre-dispatch spacing deferral waits at most five seconds on the same egress
identity, with final fence validation. Ambiguous responses never retry.

## Alternatives And Evidence

Expanding REST or accepting caller headers would let callers borrow write shapes;
a second network stack would bypass shared controls. Both were rejected.

Run `tests/tooling/test_github_write_profile.py`, connector adapter tests, real
PostgreSQL shared-egress/recipe tests, and the crawler-network lab. They cover
positive shapes, wrong repository/token/origin, absent scope, forbidden routes,
non-JSON/media, size/time bounds and cross-profile denial. The 0084 rebase evidence
records the complete gate. Live customer writes remain unqualified and disabled.
