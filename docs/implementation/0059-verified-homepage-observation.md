# Slice 0059: Verified Homepage Observation

## Objective

Replace the fixture-only product boundary with one owner-authorized customer-page
read that produces durable, inspectable SEO evidence and a deterministic finding.

## Implemented

- Added an owner-visible **Analyze verified homepage** action to Pages after the
  selected origin is currently verified and a completed audit manifest exists.
- Added a durable observation intent before network I/O. The intent binds the
  current actor, selected site, exact origin verification, completed command and
  manifest, recovery generation, and idempotency key.
- Reused the pinned HTTP boundary with bounded DNS, public-address admission,
  exact-origin redirects, TLS hostname validation, timeouts, and a 512 KiB body
  limit to read only the verified homepage.
- Added a bounded HTML parser for title, first H1, and meta description. The body
  is not stored; PostgreSQL receives the digest and bounded extracted facts.
- Added immutable forced-RLS observation results and verified-origin evidence.
  Known fetch failures commit without evidence or a finding. A missing meta
  description produces one deterministic current finding; a present description
  produces no invented issue.
- Added authenticated API read/mutation contracts, a same-origin dashboard BFF,
  strict server-side response validation, and a Pages projection showing URL,
  status, title, H1, description state, evidence identity, digest, and time.

## Safety Properties

- The database commits intent before HTTP, and rechecks current session, selected
  site, ownership verification, global claim, and authorization epochs before
  recording the result.
- The runtime role has function-only access. Intents, results, evidence, and
  finding links are immutable under forced tenant/site RLS.
- Private or ambiguous destinations, cross-origin redirects, unsupported content,
  oversized bodies, invalid HTML, and unknown transport outcomes fail closed.
- This slice performs one authorized GET. It grants no repository, CMS, GitHub,
  merge, deployment, Telegram, or production-write authority.

## Verification

Run from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/pytest -q tests/api/test_audit_findings_http.py tests/tooling/test_page_observations.py
npm --workspace apps/dashboard test
npm test
.venv/bin/ruff check apps services scripts tests
.venv/bin/ruff format --check apps services scripts tests
.venv/bin/pip check
```

## Limits And Next Work

The observation is one homepage metadata read, not a site crawl, GSC import,
competitor study, or performance audit. The existing proposal path still accepts
only the synthetic fixture finding. The next slice must allow the bounded Luna
content responsibility to draft from this verified-origin evidence while keeping
the exact owner decision and all external writes disabled.
