# ADR-0177: Fail-closed permission and invocation-owned lab isolation

Status: Accepted for security-critical local implementation; no production authority.

## Context

Slice 0165 deliberately retained three-valued SQL comparisons in the optional
non-fail-closed branch of `control.port_permission`. `IF NOT NULL` does not enter
a denial branch. This record supersedes only that nullable compatibility choice
in [ADR-0172](0172-shared-permission-check.md), not its distinct credential ports,
private operation cores, immutable records or ACLs.

Crawler and page-attempt fixtures reused one subnet across independently named
Docker projects. Browser fixture entropy used `is_global` alone, which accepts
multicast addresses that the unchanged production public-destination check denies.
Candidate cleanup inspected a global label shared by independent runs.

## Decision

Migration 0102 follows 0101 (written as 0103; renumbered at merge so 0169 composes on top).
Remove the boolean flag and replace every caller with explicit outcome-only,
known-role or exact-required-role overloads. Every predicate returns a boolean,
never NULL. An authorized context with a NULL or unknown role becomes a denial
before domain work. The legacy non-Astro eligibility delegation also receives
that check before delegation. Outcome-only checks on role-free scope or derived
permit records remain explicit; they are not nullable role bypasses.

Keep all supported roles, actor admission, ACLs, operation bodies and valid
authorized decisions unchanged. New overloads are migrator-owned, private,
SECURITY DEFINER functions with `search_path=pg_catalog`. Use literal forward
replacements, not migration-time function-definition copying. No records change.

Browser, crawler and page-attempt labs use one lab-only allocator that validates
every address with the existing `validate_public_addresses`, then atomically
claims an internal /29 through Docker IPAM. Retry only pool-overlap errors, with
a bounded limit. Do not publish ports; disable masquerading and bridge gateway
routing. Pass that invocation's addresses directly to its fixture resolver.
Docker-assigned private worker/proxy networks and loopback ephemeral PostgreSQL
ports remain invocation-owned. Candidate builds retain `--network none`; a
test-only invocation label scopes their cleanup verification. No product sandbox
or egress implementation changes.

Browser fixture cleanup queries the exact invocation label and resource name,
including after a lost creation acknowledgement. Ownership-query or removal
failures remain explicit; absent or foreign resources are never removed.

## Alternatives

- Keeping a nullable compatibility flag leaves the fail-open branch available.
- Serializing one gate does not isolate other worktrees or independent lab runs.
- Accepting multicast or documentation addresses in product egress would weaken
  the security boundary and is rejected.
- Random subnets checked only with `is_global` retain the browser fixture defect.

## Consequences And Verification

The static SQL forward migration is intentionally verbose to preserve reviewed
domain bodies. Failures roll back transactionally; destructive downgrade stays
disabled. Legitimate authorization is not enlarged. Lab retries never turn a
failed test into a pass and never prune or stop another invocation's resources.

The [implementation record](../implementation/0170-fail-closed-permission-and-lab-isolation.md)
and [evidence](../evidence/0170-fail-closed-permission-and-lab-isolation.json)
record real PostgreSQL equivalence, malformed-admission denial, ACL and failure
checks, deterministic fixture reproduction, concurrent labs and the final gate.
Live providers and deployed composition remain NOT_EXECUTED.
