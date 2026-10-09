# ADR-0068: Isolate Candidate Builds Before Repository Writes

- Status: Accepted
- Date: 2026-09-29
- Owners: Signal repository and build boundaries
- Related: [ADR-0067](0067-observe-github-pr-permission-separately.md), Revision 4.0 section 4, Revision 3.2 section 21

## Context

An owner-selected GitHub repository and observed PR permission do not make its
source or build scripts trusted. A candidate must be validated without giving
repository code Signal credentials, a writable host checkout, network reachability,
or repository write authority. A lost build receipt must not cause a blind rerun.

## Decision

Read the exact protected base commit/tree and its ordinary blobs through the
fixed-route shared connector egress, verify each Git blob digest, and keep the
bytes in a bounded in-memory checkout. Validate every patch path against the
recipe's exact allowlist and protected-path policy before creating a container.
This slice builds only an empty-patch baseline; later recipes supply sealed
patches through the same policy.

Run the source only in a disposable, digest-pinned, non-root, read-only Docker
container with a small writable tmpfs, no host mounts, no credentials, no
network, dropped capabilities, and bounded time, CPU, memory, processes, disk,
and output. Since the build needs no egress, no exception to the shared-egress
rule is granted. Read back a bounded artifact tree, reject changed source or
unexpected files/symlinks, and retain only digests and sizes. The database
records an owner/site/extension-bound intent before container dispatch and one
immutable receipt after it. A dispatched intent without a receipt is unknown
and cannot be blindly replayed.

## Consequences

No repository file is modified in GitHub, no branch or PR is created, and no
production build authority is admitted. Offline Node builds that need missing
dependencies and Hugo builds remain explicitly unavailable; this slice does
not run package installation or admit package-registry egress. The disposable
Docker lab is local qualification, not a production isolation certification;
a production runner needs separate hardening and a dedicated real-repository
qualification. Existing workflow, secret, default-branch, merge, deploy, and
autonomy prohibitions are unchanged.

## Alternatives

A host checkout or a mounted working tree would expose host state to untrusted
build scripts, so neither is admitted. Allowing direct registry access from the
build would bypass shared egress and make the output depend on unsealed external
state. A separately qualified dependency snapshot may be considered later, but
this slice stays offline and fails visibly when dependencies are missing.

## Verification

See [slice 0080](../implementation/0080-isolated-candidate-build.md) and its
[evidence record](../evidence/0080-isolated-candidate-build.json).
