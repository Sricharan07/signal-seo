# ADR-0005: Run Quality Gates in Least-Privilege GitHub CI

Status: Accepted. Date: 2026-09-07.

## Context

Local evidence is necessary while building Signal, but future changes need the
same checks on a clean Linux machine. The repository already has locked JavaScript
tooling, pinned Python versions, a disposable PostgreSQL runner, and tests that
refuse external database input. There is no deployment or production credential
that belongs in CI.

## Decision

Run two jobs on the explicit `ubuntu-24.04` GitHub-hosted image for pushes to
`main` and pull requests. One runs locked Node.js 22 repository/documentation
checks. The other installs the pinned Python 3.12 graph, checks dependencies,
lints and verifies formatting, tests the lab lifecycle, and runs the complete
digest-pinned PostgreSQL suite.

Grant the workflow only `contents: read`. Checkout does not persist credentials.
No secret context is referenced, no provider account is contacted, and no
deployment action exists. Jobs have 10- and 15-minute limits and stale runs are
cancelled per workflow/ref.

Pin every action to a full commit SHA and retain the reviewed release in a comment.
The selected revisions are `actions/checkout` v7.0.1 and v7.0.0 of both setup
actions. GitHub's official checkout guidance recommends `contents: read`, and its
hosted-runner reference lists `ubuntu-24.04` as a supported image. See the
[checkout repository](https://github.com/actions/checkout) and
[hosted-runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Consequences

Pull-request code runs only on a fresh GitHub-hosted runner with a read-only token;
it never runs on a trusted self-hosted production host. Dependency installation
still contacts package registries and the PostgreSQL image registry. Existing
version and image pins reduce drift, but Python wheels are not hash locked and the
workflow is not a complete supply-chain attestation.

The workflow is configuration until this repository is published and a real run
is observed. Local parsing proves its structure and required commands, not GitHub's
remote execution. Branch protection and required-status configuration are also
repository-host settings and are not created by this commit.

## Alternatives

Running only local checks would make regressions dependent on individual machines.
Using mutable action tags would allow reviewed workflow behavior to drift. A
self-hosted runner would expose a durable host to untrusted pull-request code.
Combining deployment secrets with quality checks would enlarge the blast radius.
These alternatives are rejected for the current repository.

## Verification

The repository test parses the workflow and asserts triggers, permissions, action
SHAs, checkout credential handling, timeouts, no secret references, and every
required command. Local execution of the underlying gates remains recorded by the
implementation slices. A remote green run is still required after publication.
