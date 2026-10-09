# Slice 0080: Isolated Candidate Build

Status: **INTERNAL BASELINE BUILD IMPLEMENTED AND LOCALLY QUALIFIED; LIVE CUSTOMER REPOSITORY AND PRODUCTION RUNNER NOT QUALIFIED; R2 PARTIAL**.

## Objective And Tier

Build one exact, owner-selected GitHub base commit without executing repository
code on the host or granting it credentials, network, or repository writes. This
is **security-critical** because repository content crosses an execution
boundary. [ADR-0068](../adr/0068-isolate-candidate-builds-before-repository-writes.md)
records the boundary and its limits.

## Implemented

- The 0079 extension and 0070 binding are rechecked for current owner/session,
  verified site, installation permission, protected base, repository identity,
  and base SHA. The fixed-route shared connector egress reads the exact commit,
  tree, and at most 256 regular blobs/8 MiB. Each blob's Git SHA-1 is verified;
  symlinks, submodules, executable entries, Git LFS pointers, oversized or
  truncated trees fail closed. No Git clone, customer host checkout, or write
  endpoint is used.
- The deterministic planner validates source paths and blobs, the observed
  framework, root `package.json` build script, and a patch's exact recipe
  allowlist. CI/workflows, secrets, configuration/credentials, lockfiles and
  lockfile-integrity fields, and out-of-scope paths are rejected before the
  sandbox starts. The baseline patch digest is SHA-256 of empty bytes. Current
  offline build profiles are Next.js, Astro, and Eleventy via fixed
  `npm run build`; Hugo and unavailable dependencies are explicit failures.
- The digest-pinned Node image runs as UID 10001 in a disposable read-only
  Docker container with only bounded tmpfs for workspace and temporary files,
  no mounts, no socket, no credentials, no network, dropped capabilities, and
  CPU, memory, process, file, time, archive, and log bounds. Source bytes are
  streamed as a trusted tar through stdin. On successful execution, the
  bounded returned tree must retain every source byte unchanged and contain
  only regular artifact files under the expected root. No raw logs or artifacts
  enter the receipt.
- Migration `0043` adds forced-RLS, function-only owner/site intents and
  immutable receipts. Preparation commits before sandbox dispatch; a missing
  receipt after dispatch remains unknown and cannot trigger a blind replay.
  The receipt binds base SHA, patch digest, pinned toolchain, command, exit
  class, bounded log digest/byte count, and artifact digests/sizes. No
  repository operation follows the build.

## Qualification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
PYTHONPATH=services/control_plane/src .venv/bin/pytest -q tests/connectors/test_github_checkout.py tests/connectors/test_github_app.py tests/connectors/test_github_read_binding.py tests/connectors/test_github_shared_egress_transport.py tests/tooling/test_candidate_build.py tests/tooling/test_candidate_build_qualification.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/ruff check apps services scripts tests database/migrations
.venv/bin/ruff format --check apps services scripts tests database/migrations
.venv/bin/python -m pip check
npm test
```

The [evidence record](../evidence/0080-isolated-candidate-build.json) contains
exact results. Real PostgreSQL verifies owner/site authority, replay and lost
receipt, immutable receipts, failure classes, role reduction, and migration
rollback. The real Docker lab runs positive and hostile scripts, forbidden
network, symlink/modified-source rejection, oversized output, timeout, OOM,
and crash; it checks cleanup. Fakes cover exact GitHub blob identity and
protected-path denial. The workflow image lab is scoped to its own tests; the
candidate sandbox has a separate real-container lab, so neither gate skips
the other's cases.

## Live Qualification

Use a dedicated installation and test repository that already passed 0070 and
0079 live qualification. Its protected base must contain an ordinary source
file and a dependency-free Next.js, Astro, or Eleventy build fixture fitting
the size bounds. The App secret stays at the existing OpenBao path, and the
owner session/OpenBao read token are prompted, never put in arguments or
environment. Supply the same private, site-scoped shared-egress context and
least-privilege database roles described in
[0070](0070-github-read-binding.md#live-qualification). Pull the pinned Node
image on the isolated runner before invoking the command; the untrusted build
itself has no network.

```sh
export SIGNAL_GITHUB_EGRESS_CONTEXT=/absolute/path/to/github-egress-context.json
export SIGNAL_GITHUB_EGRESS_ADMISSION_DSN='...'
export SIGNAL_GITHUB_EGRESS_INGEST_DSN='...'
export SIGNAL_GITHUB_IDENTITY_DSN='...'
export SIGNAL_GITHUB_OPENBAO_URL='https://bao.example.invalid'
.venv/bin/python scripts/qualify_candidate_build.py \
  --site-id <verified-site-uuid> --extension-id <qualified-0079-extension-uuid> \
  --idempotency-key <stable-uuid> --recovery-generation <current-generation>
```

Dedicated real-GitHub success, a live customer build, revocation during a
build, and production isolation are **NOT_EXECUTED** pending those resources.
The command never writes to the repository.

## Limits And Next Work

Only the empty-patch baseline is integrated here. The planner accepts
allowlisted bytes, but sealed recipe patches and review belong to 0081/0083.
This does not persist artifact bytes, install dependencies, or support a
networked build; those needs require a separately reviewed dependency and
egress design. A local Docker sandbox is not a production multi-tenant runner
qualification. No branch, PR, merge, deploy, default-branch push, workflow
edit, secret read, or autonomous authority is enabled.
