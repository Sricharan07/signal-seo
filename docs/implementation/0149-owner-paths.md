# Slice 0149: Owner Paths

Classification: **security-critical**, because an owner can now grant the GitHub
PR-permission prerequisite for an external write. Based on main `f611c42`.
Decision: [ADR-0159](../adr/0159-owner-pr-write-grant.md).
Migration **0093**, down revision **0092**; 177 cumulative app tables, no new dependencies.
Accepted specifications and historical migrations are unchanged.

## Implemented

1. **GitHub PR permission:** owner-only GET/POST
   `/v1/sites/{site_id}/github-pr` and `/auth/github-pr` reuse the existing
   connector composition and browser mutation proof. Prepare, independently
   observed finish and revoke require current owner, matching selected site,
   fresh MFA within five minutes, verified origin and an active exact read
   binding. Final observation rechecks authority. Browser assertions, mismatched
   intent/request/binding IDs and completed/revoked finish replays are rejected
   before credential/provider I/O. The GitHub card shows granted/ungranted,
   incomplete/failed/stale and unreadable states, with exact repository, base,
   content and deterministic Signal branch scope in Technical details and a
   separate Revoke. Home includes the optional step from the loaded state;
   unreadable state says Could not check. Existing checklist completion behavior
   is unchanged.
2. **Weekly pause/resume:** `/actions/weekly-loop` obtains the normal API mutation
   proof and calls the existing pause/resume endpoints. Home and Autonomy show
   an ink secondary command, selected from a new current-owner read of actual
   stored pause state, not the latest weekly report. Missing state cannot invent
   an action. Pause still revokes the allowance; resume clears the pause but does
   not resurrect an allowance. Pending durability and unknown responses stay
   explicit; the UI requires refresh after an unknown outcome.
3. **Visibility schedule:** owners now reach both AI visibility and the schedule
   on AI answers. The unreachable owner-only branch in the non-owner fallback is
   removed; role/render regression coverage pins both paths.
4. **Activity:** the empty ledger no longer asserts GitHub is disconnected. It
   states that no pull request evidence is shown; it does not infer permission or
   delivery from a binding.
5. **Inbox:** the Signal Chat proposal prompt is restricted to the local pilot.
   Other empty Inboxes say: "Nothing waits on you. Signal adds decisions here as
   the weekly loop finds work."
6. **Email links:** weekly reports and alerts link to `/changes`, labelled Activity,
   rather than the hidden `/work` placeholder. The shared chat projection follows
   the same correction, with plain-text and HTML regression assertions.

## Authority And Recovery

The provider observer requests only the existing one-repository contents-read
and PR-write permission observation; no browser-supplied scope is trusted. It
opens no PR or branch. Separate approved revision/standing allowance, live scope,
recipe, build and journal preconditions still govern every external write.
Merge, deploy, default-branch push, CI workflow edits and secret reads remain
forbidden; provider/write/protected-path regression tests remain unchanged.

PR revocation is atomic with the independent journal outbox. Its retry uses the
original restriction, yielding `AUTHORITY_DURABILITY_PENDING` until a verified
journal receipt exists. Restore replay is deny-only and idempotent, rejects
conflicting receipts, and restrictive RLS blocks resurrected observed extensions
for all existing consumers. No credentials or tokens enter browser projections,
grant records or restriction payloads. Existing OpenBao private TLS is retained.

## Qualification

Runnable commands and exact final counts are in
[the evidence record](../evidence/0149-owner-paths.json).
All requested checks pass: 2,871 fast Python, 1,239 PostgreSQL, 115 unsharded
delivery, 17 journal, 14 crawler-network, 45 OpenBao, 46 repository and 277
dashboard tests/checks. Ruff passes over 635 formatted Python files; typecheck,
build and 327 Markdown checks pass. Receipt source hashes match frozen source
and invocation-owned cleanup completed. Staged gitleaks with main's unchanged
configuration reports zero leaks.

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
npm test
.venv/bin/python scripts/run-database-tests.py --shards 3
.venv/bin/python scripts/run-autonomy-delivery-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/openbao_lab.py
gitleaks git --staged --config /tmp/signal-0149-main-gitleaks.toml --redact
```

New real-PostgreSQL tests cover the composed owner HTTP flow, exact scope,
non-owner/primary/stale/future MFA, unverified origin, absent/stale binding,
cross-tenant/site, forged/replayed finish, final-authority changes, revoke-before-
finish, pending retry, acknowledged revoke and restored observed-row denial.
Negative and provider-failure coverage from the existing GitHub suites also runs.
Six dashboard tests cover rendered owner paths, strict state projection, unknown
states, role isolation, same-origin BFF proof, rejected payloads and pending results.
The repository dashboard design guards run unchanged.

Native Chrome screenshots checked the loopback-only synthetic SSR fixture at
desktop and 390x844 mobile sizes, including the grant step and Autonomy control.
This is layout evidence only, not a logged-in or hydrated live-provider journey.
Playwright is not installed in this worktree; no dependency was added. Detector
review found no new frontend findings. The temporary fixture contains no customer
data or credentials and is not a product route.

Earlier mutable-source/interrupted lab runs are excluded. One new session fixture
initially violated the existing last-seen constraint; the correction preserves
it and synchronizes identity/tenant MFA timestamps so stale-MFA tests exercise
the freshness gate rather than merely an invalid session. No safety check was
weakened. The full gate is intentionally not run; the merge train owns it.

## Limits

Live GitHub App permission changes, real owner grant/revoke, PR creation, customer
repositories/data, deployed weekly controls and visibility scheduling, and
production composition are **NOT_EXECUTED**. Dedicated connector admission remains
exactly scoped; this slice adds no general production provider runtime. No R1-R4
release or production/external write is enabled. Live qualification needs the
existing authorized dedicated environment, actual narrowly configured App,
current owner MFA and independently operating restriction journal.
