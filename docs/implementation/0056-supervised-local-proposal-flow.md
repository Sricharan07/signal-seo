# Slice 0056: Supervised Local Proposal Flow

## Objective

Turn the existing durable fixture finding into the first user-testable supervised
agentic loop: issue a bounded command in Signal Chat, inspect an evidence-backed
exact proposal, and record an owner decision without performing an external write.

## Implemented

- Added forward-only database revision `0031` with tenant/site-scoped immutable
  proposals, RFC 8785 canonical revisions, expiring approval requests, and exact
  decisions. All four tables use forced RLS and reject updates or deletion.
- Added narrow authenticated PostgreSQL functions for prepare, read, and decide.
  They recheck current session, recovery generation, owner role, selected site,
  membership, and supporting finding. Runtime roles receive no direct table
  privileges.
- Added a deterministic four-responsibility local pipeline for technical evidence,
  content drafting, independent scope review, and coordination. The database
  reconstructs the only accepted fixture manifest before committing it.
- Added strict FastAPI prepare, list, and decision routes. The exact approval
  request is bound to the revision SHA-256 and expires after 24 hours.
- Added server-only dashboard proposal reads plus same-origin tenant-CSRF route
  handlers. Signal Chat prepares the proposal; Approvals shows evidence, exact
  before/after state, checks, authority, cost, recovery, expiry, and the full
  revision digest; the owner can approve, reject, or request edits.
- Added explicit local-pilot composition and capability inventory entries.
- Hardened pilot port selection to reject an already-connectable `localhost`
  listener even on platforms where a second loopback bind can appear available.

## Safety Properties

- Only a current owner can prepare or decide, and owner authority is checked before
  proposal readiness is revealed.
- A decision cannot be replayed against a different digest or replaced by a
  conflicting decision.
- Exact retries converge; concurrent preparation creates one proposal, revision,
  and approval request.
- Approval means only that the synthetic local draft is accepted. No outbox event,
  GitHub request, provider operation, customer-origin read, merge, deployment, or
  production authority is created.
- The recovery plan is truthful: discard the local draft. There is no external
  state to undo in this slice.

## Verification

Run from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api -q
.venv/bin/python -m pytest tests/api tests/connectors tests/identity tests/tooling -q
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm test
.venv/bin/ruff check apps services scripts tests
.venv/bin/ruff format --check apps services scripts tests
npm audit --omit=dev
.venv/bin/pip check
```

The browser proof uses `npm run pilot`, then completes sign-in, site creation,
audit, fixture analysis, chat proposal preparation, exact approval inspection, and
one immutable decision. The proof also runs beside an unrelated listener on port
`3000`, selects port `3001`, reloads the recorded decision, checks the browser
console, and verifies no horizontal overflow at 390px and 1440px widths.

Evidence is recorded in
[0056-supervised-local-proposal-flow.json](../evidence/0056-supervised-local-proposal-flow.json).

## Limits And Next Work

This is a deterministic synthetic walkthrough, not the full agent workforce. It
does not call a language model, run competitor research, accept arbitrary chat,
read a customer page, connect Telegram, build a repository patch, or write to
GitHub. Next, replace fixture evidence with one authorized immutable customer page
observation and qualify the smallest real role handoff and repository candidate
builder. Keep GitHub disabled until installation binding, protected-path policy,
credential-free build checks, independent review, exact approval, and recovery
tests all pass.
