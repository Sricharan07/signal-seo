# Slice 0005: Least-Privilege Quality CI

Status: **CONFIGURED AND REMOTELY EXERCISED; NO DEPLOYMENT AUTHORITY**.

## Outcome

`.github/workflows/quality.yml` now applies the repository's existing quality gates
to pushes on `main` and pull requests. The workflow has no production authority,
deployment step, provider credential, or write permission.

The repository job uses Node.js 22, installs the committed lock with lifecycle
scripts disabled, and runs all repository/documentation checks. The Python job uses
Python 3.12, verifies the pinned dependency graph, runs Ruff, tests the disposable
lab lifecycle, and executes the complete PostgreSQL contract suite through the
same source-hashing runner used locally.

## Security and Reliability

- Workflow permissions are limited to `contents: read`.
- Checkout credentials are not persisted.
- Official GitHub actions are pinned to full 40-character commit SHAs.
- No `${{ secrets.* }}` context, deployment environment, artifact containing
  credentials, external database URL, or self-hosted runner is used.
- Both jobs use the explicit supported `ubuntu-24.04` image and bounded timeouts.
- Concurrency cancels superseded work for the same workflow and Git reference.
- Pull requests never use the elevated `pull_request_target` trigger.

## Verification

The added repository test parses the workflow as YAML and checks all of the above,
plus the exact commands for Node, dependency consistency, Ruff, lab safety, and
real PostgreSQL tests. The local repository suite now has 9 passing cases. The
Python checks, 8 tooling tests, and 63 PostgreSQL cases also pass locally.

The workflow now runs on the private GitHub repository. The Slice 0039 push and
one failed-job rerun exercised the read-only hosted-runner path through dependency,
lint, formatting, Python, OpenBao, and Keycloak startup; both attempts exposed the
same fixed 90-second Keycloak cold-start limit rather than a protocol assertion.
A subsequent 180-second correction run established that elapsed time was not the
root cause: Linux preserved the private fixture owner's UID while the image ran as
UID 1000. The POSIX bind-ownership correction and its local real-provider evidence
are recorded in [Slice 0039](0039-durable-crawl-page-attempts.md). A correction is
not remotely verified until its own pushed workflow is green. Branch protection,
required-check configuration, and release/deployment workflows remain explicit
follow-up gates. See [ADR-0005](../adr/0005-least-privilege-ci.md).
