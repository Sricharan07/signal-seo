# ADR-0090: Journal Exact Git Objects Before Opening a PR

Status: Accepted for the internal GitHub-first beta path, 2026-09-29.

## Context

Slice 0084 is the first operation that can change a selected customer repository.
The 0079 observation deliberately requests only `contents:read` and
`pull_requests:write`; Git object and branch creation additionally needs
`contents:write`. GitHub can lose a response after applying a mutation, and a
branch or PR can trigger workflows before any merge.

## Decision

- Keep the read transport unchanged. A distinct write adapter requests a
  one-repository installation token with `contents:write` and
  `pull_requests:write` only after a stable primary operation and an encrypted,
  signed intent are durably acknowledged on the separate write-journal stream.
  Its shared-egress route accepts only the exact tree, commit, deterministic
  `signal/<operation UUID>` ref, and PR bodies. It has no update-ref, merge,
  workflow, secret, or default-branch route.
- Materialize one content-addressed tree and one deterministic commit, then
  create the ref atomically at that commit and open one PR. The commit parent
  is the sealed base; the entire resulting tree must equal the sealed patch
  applied to the verified checkout. An object, ref, or PR response loss becomes
  `OUTCOME_UNKNOWN`. Only provider reads of those exact identities can advance
  reconciliation. Absence is not permission to repeat the mutation.
- A short resource lease and increasing fence are checked at the fetcher just
  before each outbound mutation. Every step also rechecks owner decision,
  membership and site epochs, recovery generation, binding, reviewed recipe
  release and denial tombstones, protected base identity, and journal receipt.
- Until a repository workflow contract is qualified, any checked-out
  `.github/workflows/` file blocks outbound writes. This initial beta subset
  does not silently certify arbitrary CI. A structured inverse-patch,
  three-way recovery plan is sealed by the intent: later overlapping edits
  require owner review, never a forced overwrite.
- PR creation is recorded as `opened`, not delivered. Checks, deployment, and
  an independent live result are separate work in 0085. No standing
  authorization or autonomous execution is inferred from Inbox approval.

## Consequences

The owner must enable `contents:write` on the selected test App and provide a
protected, workflow-qualified repository before live qualification. The wider
write permission is constrained by the credential-bearing adapter and
per-dispatch gateway, not by the installation permission alone. A lost ref or
PR response may remain unresolved and require operator review. Production
writes stay disabled until real-provider and recovery qualifications pass.

## Alternatives

The contents API would require serial branch updates and add another ambiguous
write. Retrying a timed-out create call could duplicate or change customer
work. Both are rejected in favor of content-addressed objects and exact
read-only reconciliation. A broad installation token is rejected in favor of
one selected repository and the separate write adapter.

## Verification

The [slice record](../implementation/0084-idempotent-pr-creation.md) and its
linked evidence cover real PostgreSQL state transitions, the independent
journal, shared-egress admission, fake-provider failure matrices, and the
repository gate. Live GitHub and primary-restore replay are explicitly not
qualified.
