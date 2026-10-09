# Slice 0079: GitHub PR Permission and Repository Format

Status: **INTERNAL SERVICE IMPLEMENTED AND LOCALLY QUALIFIED; LIVE APP AND CANDIDATE BUILD NOT QUALIFIED; R2 PARTIAL**.

## Objective And Tier

Extend the exact owner/site/repository read binding with observed PR permission
and a deterministic framework/content-format assessment. This is
**security-critical** because it changes provider permission and durable
publishing-adjacent authority. [ADR-0067](../adr/0067-observe-github-pr-permission-separately.md)
keeps this observation separate from external-write authority.

## Implemented

- Migration `0040` adds owner-attributed, revocable PR-extension intent and
  append-only lifecycle events. Both tables force RLS; identity has only narrow
  functions. Preparation, completion, and read recheck selected-site/session
  authority, current origin proof, and the active 0070 binding. Completion
  checks the same repository ID and protected base. It stores no credential.
- The GitHub adapter requests an exact one-repository token with only
  `contents: read` and `pull_requests: write`, validates the granted scope,
  repository identity and base protection, and never calls a PR or other write
  endpoint. A separate contents-read token fetches the base commit and
  recursive tree metadata. Fixed-route shared egress rejects unrelated routes,
  queries, hosts, methods, or missing gateway composition.
- Deterministic detection recognizes Next.js, Astro, Hugo, and Eleventy from
  root configuration markers. The owner's exact content path is matched to an
  ordinary blob and a supported extension. Truncated trees, absent content,
  symlinks, unknown and ambiguous frameworks are explicit non-compatible
  outcomes. No repository text is executed or treated as instructions.
- A current use rechecks live PR permission, repository identity, protected
  base and SHA, plus local extension/binding status. This does not grant
  branch-write permission, create a candidate, or open a PR.

## Qualification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-crawler-network-tests.py
PYTHONPATH=services/control_plane/src .venv/bin/pytest -q tests/connectors/test_github_app.py tests/connectors/test_github_read_binding.py tests/connectors/test_github_shared_egress_transport.py tests/tooling/test_github_format.py tests/tooling/test_github_binding_qualification.py tests/tooling/test_github_pr_qualification.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/ruff check apps services scripts tests database/migrations
.venv/bin/ruff format --check apps services scripts tests database/migrations
.venv/bin/python -m pip check
npm test
```

Exact outcomes are in [the evidence record](../evidence/0079-github-pr-authority-framework-detection.json).
Real PostgreSQL covers owner/site authority, one-binding idempotency, current
origin proof, immutable events, permission failure, partial inventory, local
revocation, parent revocation, direct privilege denial, and migration rollback.
Provider fakes cover exact permission and tree identity, current permission
loss and base change. Fakes do not qualify live GitHub behavior.

## Live Qualification

First qualify the 0070 read binding on a dedicated test installation. Extend
that **same** installation with `pull_requests: write` and `contents: read` on
only the selected test repository. Keep its protected base branch and exact
content file. Place the App ID/private key in the existing read-only OpenBao
KV v2 path `signal-github/data/github/app`. Supply the same private, site-scoped
shared-egress context and least-privilege database roles described in
[0070](0070-github-read-binding.md#live-qualification). This command reads
permission and tree metadata only; it does not create a PR.

```sh
export SIGNAL_GITHUB_EGRESS_CONTEXT=/absolute/path/to/github-egress-context.json
export SIGNAL_GITHUB_EGRESS_ADMISSION_DSN='...'
export SIGNAL_GITHUB_EGRESS_INGEST_DSN='...'
export SIGNAL_GITHUB_IDENTITY_DSN='...'
export SIGNAL_GITHUB_OPENBAO_URL='https://bao.example.invalid'
.venv/bin/python scripts/qualify_github_pr_extension.py \
  --site-id <verified-site-uuid> --binding-id <qualified-0070-binding-uuid> \
  --idempotency-key <stable-uuid> --recovery-generation <current-generation>
```

The command prompts for the owner session and OpenBao read token. Do not put
them in arguments, environment, context, or shell history. Live App permission,
inventory, installation revocation, and repository state checks are
**NOT_EXECUTED** until those dedicated resources exist.

## Limits And Next Work

This is an internal owner-session service, not yet an Inbox or browser
connector control. It observes PR permission but cannot create branches or
PRs. It does not inspect branch rulesets, CI workflow risk, or check/deployment
configuration; 0084 must qualify those before any external write. A large
GitHub tree beyond the response bound fails closed rather than claiming
coverage. Slice 0080 adds an isolated credential-free checkout and candidate
build. No production write or R2 release is admitted.
