# Slice 0060: Verified Homepage Model Proposal

## Objective

Continue the real owner journey from an immutable verified-homepage finding to a
bounded Luna metadata draft, an exact proposal revision, and a human decision.

## Implemented

- Added a dedicated `gpt-5.6-luna` release for verified-homepage metadata. The
  model receives only the exact page URL, bounded title, first H1, and immutable
  evidence identities. It receives no customer HTML, credentials, tools, or
  external authority.
- Added durable model-run admission before provider I/O. PostgreSQL rechecks the
  current owner, selected site, recovery generation, latest successful page
  observation, open verified-origin finding, and exact input and prompt hashes.
- Added strict structured output for one 70-160 character meta description and a
  bounded rationale. Requests use `store=false`, no tools, fixed model identity,
  bounded response size and timeout, and sanitized failure classes.
- Added migration `0034`, which seals the exact provider receipt, requested and
  reported model, token usage, prompt/input/output identities, role releases,
  deterministic checks, cost ceiling, and recovery statement into one immutable
  RFC 8785 proposal revision and 24-hour approval request.
- Added the authenticated API mutation and strict dashboard contract. Signal Chat
  prepares the verified-homepage proposal and Approvals records approve, reject,
  or request-edits against the exact revision SHA-256.
- Removed fixture analysis and fixture findings from normal Pages and Chat views.
  The fixed fixture remains internal qualification coverage only.

## Safety Properties

- The model cannot choose the site, page, field, evidence, prompt, recipe,
  authority, cost, or recovery behavior. Browser input is limited to the current
  site and the closed prepare command.
- Known provider failures are durable and bounded. An ambiguous transport outcome
  is marked unknown and blocks blind retry; a completed call cannot be rebound to
  different evidence.
- PostgreSQL reconstructs the expected verified proposal from current immutable
  evidence and durable model facts before accepting the canonical manifest.
- Approval accepts only the draft. It emits no outbox event and grants no
  repository, GitHub, CMS, merge, deployment, or production-write authority.

## Verification

Run from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/pytest -q tests/api tests/tooling
npm test
.venv/bin/ruff check apps services scripts tests
.venv/bin/ruff format --check apps services scripts tests
.venv/bin/pip check
npm audit --omit=dev
```

## Limits And Next Work

The accepted revision is a supervised content draft, not a repository patch or
deployed SEO change. It does not inspect the customer's framework, modify files,
create a branch or pull request, observe CI, verify live delivery, or measure an
outcome. The next outcome-bearing slice must bind a selected repository and build
one isolated, tested candidate from this approved revision before any GitHub write
is enabled.
