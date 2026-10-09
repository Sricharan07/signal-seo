# Slice 0002: Real WordPress Core Feasibility

## Scope and Traceability

Specification sections 19.3-19.6, 20, and 33 Milestone 0; INV-010; EC-073.
This is a runnable provider experiment, not pilot eligibility.

Implementation details live in the [lab README](../../experiments/wordpress-feasibility/README.md).
The authority boundary is recorded in [ADR-0002](../adr/0002-wordpress-lab-boundary.md).

## Capability Matrix

| Surface | Experimental coverage | Customer authority |
| --- | --- | --- |
| WordPress 6.9.1, PHP 8.3, MariaDB 11.4.10, InnoDB | Digest-pinned real stack | None |
| Existing published page, exact content snippet | Conditional update and protected-field checks | Draft/manual only |
| Operation identity/receipt | Concurrent duplicates, identity reuse, rollback, discarded response | None |
| `_signal_lab_description` | Existing single row; stale/missing/duplicate rejection | Synthetic only |
| Inverse content patch | Unrelated edits preserved; overlap/stale preparation rejected | Not a general Undo API |
| Bundled theme, default per-process cache | Authoritative state and HTTP-rendered content | No CDN/persistent-cache claim |
| Native editor races | Earlier saves conflict; later stale saves can overwrite | Independent verification required |
| External hooks | Nontransactional event survives SQL rollback | Automatic writes blocked |
| Yoast, other SEO plugins, persistent cache, CDN | NOT_EXECUTED | Unsupported |
| Auth, permits, journals, production Bridge routes | Not implemented here | Disabled |

## Reproduce

```sh
npm ci
npm test
npm run test:wordpress
```

The runner returns nonzero on failed assertions, malformed reports, startup
failure, interruption, or failed cleanup. Generated reports include source hashes
so evidence can be tied to tested source rather than later edits.

## Evidence

Local execution on 2026-09-07: **21/21 real-stack scenarios passed** on WordPress
6.9.1, PHP 8.3.30, MariaDB 11.4.10, and bundled Twenty Twenty-Five 1.4. The reviewed
[JSON report](../evidence/0002-wordpress-feasibility.json) contains individual
results and source hashes. Containers, network, and volume were removed afterward.
Eight repository tests and documentation checks also passed.

A passing test that demonstrates a provider limitation is not a passing production
capability certification. Full Milestone 0 remains incomplete for the explicitly
untested plugin/cache/deployment combinations above.

## Maintenance Notes

Both specification baselines remain unchanged. Their eight original Markdown
hard-break lines intentionally contain trailing spaces; the initial commit
preserved those rather than silently normalizing supplied documents.

The first direct-bind run could not read a PHP dependency inside Docker and was
discarded. Source snapshotting avoids live-source changes during a run as well as
that dependency-read problem. Initial harness assertions also exposed nullable
cache flags and boolean DDL return values; the checks now verify their actual
semantics, and engine restoration is protected by `finally` even on assertion
failure. Failed runs are not qualification evidence.
