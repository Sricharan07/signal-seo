# ADR-0037: Persist Robots Evidence Before Page Admission

- Status: Accepted
- Date: 2026-09-09
- Decision owners: crawl ingestion and policy
- Specification: Revision 3.2 sections 7.4, 11.1-11.8, 26.3, Appendix A
  `app.robots_snapshots`, EC-025, EC-026, EC-032, and EC-034

## Context

Signal has a pinned HTTP boundary, durable crawl frontier, and encrypted artifact
store, but no page may be sent merely because those parts exist. Robots rules are
origin-specific, user-agent-specific, time-sensitive evidence. Treating a live
parser object, substring check, or transient fetch result as authority would make
retries and audits non-reproducible. Treating every failure as an absent file would
also turn origin outages and hostile responses into accidental crawl permission.

The standard is [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html). Python's
standard-library parser does not provide the complete profile needed here. Protego
is a maintained RFC-oriented parser, but releases through 0.6.1 had a high-severity
wildcard ReDoS issue. The project fixed it in 0.6.2.

## Decision

Signal retrieves the exact `/robots.txt` resource through the existing resolver-
injected, public-address-screened, numeric-peer-pinned boundary. Redirects remain
inside immutable allowed origins, every hop is re-admitted, and no more than five
hops are followed. Only identity-encoded `text/plain` successful bodies up to
500 KiB are retained.

Signal pins [Protego 0.6.2](https://pypi.org/project/Protego/) and wraps it in the
versioned `protego/0.6.2+signal-rfc9309-v1` profile. The wrapper parses strict UTF-8
line by line, preserves parseable supported records, ignores unknown records,
bounds line count, line size, and wildcard count, and never logs raw rules. It
tests case-insensitive product-token groups, repeated groups, wildcard/end anchors,
percent encoding, longest match, and allow on an equal match. The `/robots.txt`
resource itself is implicitly fetchable by the retrieval boundary.

The status policy is deliberately conservative:

- `404` permits an otherwise authorized public request until the snapshot expires;
- `401` and `403` deny;
- `429` denies and records bounded retry timing;
- `5xx`, transport failure, unsafe response/redirect, unsupported encoding/media,
  oversized content, and other `4xx` responses suspend new origin requests.

This is stricter than RFC 9309's generic unavailable handling for other `4xx`
responses because Revision 3.2 explicitly grants the allow exception only to
`404`. Every result becomes an immutable, run/profile-bound snapshot. Successful
raw content is encrypted before its metadata and upload-readback attestation are
atomically registered. Ordinary decisions use only the newest unexpired snapshot;
an absent, expired, unreadable, mismatched, or non-verified artifact fails closed.

The ingest role receives execute permission on two security-definer functions and
no direct table access. Snapshot identity retries serialize and converge; conflicts
cannot replace evidence. This slice records policy evidence but grants no network,
frontier, workflow, or production authority.

## Alternatives

- `urllib.robotparser`: rejected because the required RFC behavior and release-
  bound security profile are not complete enough for this trust boundary.
- A custom robots parser: rejected because reproducing URL octet matching, group
  selection, wildcard behavior, and interoperability would add avoidable risk.
- Live fetch and parse before every page: rejected because it is inefficient,
  difficult to audit, and cannot reproduce the exact decision after restart.
- Allow on all `4xx` or network failure: rejected because it weakens the approved
  conservative Signal policy.
- Add global origin permits in the same change: deferred to keep one independently
  testable slice and avoid confusing evidence with distributed request authority.

## Consequences

Every future page admission can cite an exact robots snapshot and parser release.
Newer restrictions supersede older cached rules for unsent work, while historical
snapshots remain auditable. The local encrypted backend and caller-supplied key are
still development boundaries. Protego upgrades require a reviewed parser-profile
revision, security review, RFC regression vectors, and compatibility decision.

The [Protego advisory](https://github.com/scrapy/protego/security/advisories/GHSA-wjmf-p669-5m5p)
is part of the dependency decision. Production rollout must additionally add
global origin admission, shared retry/backoff state, controlled resolver/egress,
frontier settlement, workflow composition, monitoring, and distributed artifact
and key lifecycle.

## Verification

Unit tests cover parser and HTTP policy vectors, malformed input, bounds, bodyless
status evidence, and opaque representations. A real internal Docker network covers
the exact resource, admitted cross-origin redirect, public peer pinning, status
matrix, and private redirect closure. Disposable PostgreSQL covers forced RLS,
function-only privileges, append-only rows, encrypted registration, current-cache
selection, exact/concurrent retry, conflicts, cross-site rejection, expiry, and
migration rollback.
