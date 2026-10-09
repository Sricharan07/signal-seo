# ADR-0002: Separate Provider Feasibility from Execution Authority

Status: Accepted. Date: 2026-09-07.

## Context

The corrected specification requires real WordPress feasibility before platform
breadth. Core updates read/merge post state and invoke hooks. Transactions can
coordinate InnoDB rows, not reverse a plugin's external effects or prevent a later
editor save. See the [WordPress update implementation](https://developer.wordpress.org/reference/functions/wp_update_post/)
and specification sections 19.4 and 20.

## Decision

Build a CLI-only experiment against digest-pinned WordPress and MariaDB. Exercise
exact content patches, synthetic metadata, concurrency, receipts, crashes, hooks,
public rendering, and limited inverse recovery. Keep production capabilities
disabled regardless of the experiment's pass count.

Do not present synthetic metadata as Yoast support or SQL rollback as undo for
emails, search-index history, or other irreversible effects. Do not expose an
unauthenticated prototype REST write endpoint for convenience.

WordPress remains **draft/manual-only**. Continue only with provider-independent
trusted data contracts and read-only work until the actual pilot plugin/cache
configuration, complete preconditions, identity, authority, side effects, and
recovery protocol are qualified. This is not Milestone 0 or pilot admission
approval and does not skip their remaining gates.

## Alternatives and Consequences

- A fake CMS is faster but cannot establish real WordPress locking/hook behavior.
- A deployable Bridge now mixes feasibility with unbuilt security infrastructure.
- Building many CMSs now multiplies unverified assumptions. Other CMS capabilities
  remain unsupported rather than nominally implemented through generic MCP calls.
- This prototype must not become a production connector by simply moving files.

## Verification and Follow-Up

See [slice 0002](../implementation/0002-wordpress-feasibility.md) for reproducible
tests. Qualify the named pilot SEO plugin and rendered metadata, persistent caches,
CDN, actual hook inventory, network-level response loss, and recovery interference
on the intended deployment. Establish manifest/permit checks and an independent
verifier. Preserve the demonstrated editor and external-effect limitations in
product behavior and documentation.
