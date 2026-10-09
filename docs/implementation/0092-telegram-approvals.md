# Slice 0092: Telegram Pairing and Exact-Revision Approvals

Security-critical. Internal optional connector; live Telegram and production
composition remain **NOT_EXECUTED / unavailable**.

Rebased after the accepted email reports slice in the merge train. Migration `0070` follows `0069` and
adds five forced-RLS application tables: bot bindings, member links, one-use
pairing codes, outbox, and immutable ingress audit. The application now has 129
tables. Runtime roles have bounded security-definer functions, not direct table
access. Migrations `0001`-`0062` and accepted specification revisions are unchanged.
See [ADR-0076](../adr/0076-private-telegram-exact-revision-approvals.md).

## Implemented Boundary

- Current owner/verified-site installation stores the bot token and random
  webhook secret only in a dedicated OpenBao KV v2 mount. `getMe` confirms a bot
  with group joining disabled; `setWebhook` registers the binding-specific HTTPS
  route and only `message`/`callback_query` updates. Failed setup stays revocable.
- A bot ID is reserved before webhook registration. Revocation takes effect
  locally before I/O, journals denial, attempts deletion once, and permanently
  destroys secret metadata. Confirmed deletion releases the reservation;
  ambiguous deletion keeps it reserved so an old revoke cannot delete a new
  binding's webhook. An unverified duplicate never deletes another webhook.
- Dashboard-issued five-minute one-use deep links bind the expected numeric
  Telegram person, private chat, current Signal membership epoch, and recovery
  generation. Unpairing, disabled users, removed memberships/site grants, role
  changes, epoch changes, and deny-journal tombstones fail closed at use.
- The typed `TELEGRAM_BOT` profile permits only JSON POST on
  `https://api.telegram.org`, exact `getMe`, `setWebhook`, `deleteWebhook`,
  `sendMessage`, and `answerCallbackQuery`; requests/responses are at most 16 KiB,
  with the existing bounded timeout and no redirects. Other origins/methods,
  query strings, token-bearing input URLs, non-JSON, and GET are denied.
- Only a private typed credential adds `/bot<token>/<method>` at the pinned
  HTTP transport. Records and errors use safe logical method URLs, never the
  wire URL. Robots checks the private path only in memory. HTTP debug is forced
  off; Telegram response headers and raw transport exceptions are suppressed.
  No provider credential is written to PostgreSQL, artifacts, or captured logs.
- Webhook-secret verification is constant-time; missing/wrong secrets are
  audited before update parsing. Durable update IDs reject replay. Groups,
  supergroups, channels, and ordinary text are audited and ignored. Only private
  `/start <code>` pairing and exact opaque callback codes are recognized.
- Approval messages contain exact revision ID/sealed hash, a bounded fixed
  HTML-escaped summary, evidence link, and opaque 43-byte approve/reject buttons.
  Hashes bind callbacks to the accepted outbox item, intended user/chat/message,
  expiry, current linked person, and exact revision. No customer content is sent.
- Telegram extends the existing immutable Inbox decision function with the
  `telegram` channel. It shares current-member authorization and deterministic
  risk/revision checks with Slack and dashboard, not a parallel implementation.
  A3+, A4, canonical, unknown, or above-configured risk creates only a dashboard
  handoff through the outbox. High-risk dashboard decisions require fresh MFA.
- Strict optional API and same-origin CSRF-protected dashboard installation,
  pairing/unpairing, owner disconnect, and exact Inbox request are implemented.
  The bot token input is cleared on submission; error/response schemas never
  echo credentials. Unconfigured Telegram stays unavailable by default.

## Delivery and Failure Semantics

`queued -> dispatching -> accepted|unknown` is durable. Only pre-dispatch
admission deferral with no existing egress operation can return an item to
queued. Retrying the same revision reuses its outbox ID; accepted, unknown, or
abandoned dispatching items never double-send. This is at-most-once dispatch,
not a claim of Telegram remote exactly-once delivery. Callback acknowledgements
and step-up replies also use the outbox. Setup retries only safe pre-dispatch
`EGRESS_DEFERRED`, never an ambiguous external write.

Failed installation retains its OpenBao secret for explicit revocation of a
possibly registered webhook. Upstream delete ambiguity requires operator
resolution; the bot is not automatically made reusable. Independent journal
acknowledgement can remain `AUTHORITY_DURABILITY_PENDING`; local denial is
already effective. Restore replay adds only denial, never resurrected authority.

## 0104 Integration

The existing `control.bind_github_pr_decision_channel` trigger binds Telegram's
channel from the exact immutable Inbox decision. Migration 0070 extends the
0084 operation constraint; 0104, API and dashboard projections accept and display
that channel without a separate write authority. Telegram's risk refusals and
fresh-MFA dashboard step-up remain unchanged. Delivery tests cover one exact PR
dispatch despite retries, stale callbacks and a revision superseded after approval.

## Verification

Historical slice qualification commands and source-bound reports are in
[0092 evidence](../evidence/0092-telegram-approvals.json): 2,243 final passing
cases, zero final failures. PostgreSQL covers positive, negative, failure,
EC-140, replay, stale/mismatched revision, A3/A4/canonical/unknown handoff,
configured lower ceiling, acknowledged/ambiguous retry, and revocation fences.
The full Inbox -> paired owner -> Telegram decision -> dashboard-readable same
immutable record -> 0084/0085 continuation is exercised using provider doubles
behind shared egress. Admin-seeded high-risk fixtures test policy only, not a
qualified high-risk build/write pipeline. Token-redaction checks scan every
persisted app/control row and DEBUG log capture, including malicious reflected
response headers and a failing transport. Unit tests inspect the actual pinned
HTTP request target without recording the credential.

Real TLS OpenBao checks cover Telegram-isolated ACL, CAS-zero creation,
overwrite denial, and permanent deletion. The independent two-PostgreSQL and
OpenBao restore lab exercises Telegram binding/link deny-only replay. All eight
lab runners passed. Initial journal and page-attempt invocations failed before
tests due to transient Docker operations; unchanged reruns passed, not weakened
gates. Earlier implementation test failures were fixed before final qualification.

Screenshots at 1440 and 390 pixels cover
[actual unavailable desktop](../evidence/0092-telegram-unavailable-desktop.png),
[mobile](../evidence/0092-telegram-unavailable-mobile.png),
[synthetic owner desktop](../evidence/0092-telegram-owner-desktop.png),
[mobile](../evidence/0092-telegram-owner-mobile.png),
[synthetic paired desktop](../evidence/0092-telegram-paired-desktop.png),
[mobile](../evidence/0092-telegram-paired-mobile.png),
[synthetic failed setup desktop](../evidence/0092-telegram-failed-desktop.png),
and [mobile](../evidence/0092-telegram-failed-mobile.png). Synthetic states render
the real component with an explicit synthetic label; they do not claim hydrated
provider interaction or readiness. No horizontal overflow was measured.
Finish review found and cleared one contradictory static pairing-status row;
the full dashboard pairing regression now excludes that duplicate. The final
verdict is ship at that fix-list scope. Documentation comparison preserves the
incumbent visual authority files; existing design-document drift is not repaired
by this narrow extension.

Protocol references: Telegram's [Bot API requests](https://core.telegram.org/bots/api#making-requests),
[setWebhook](https://core.telegram.org/bots/api#setwebhook), and
[deep linking](https://core.telegram.org/bots/features#deep-linking).

## First Live Run Inputs

Provide a dedicated BotFather bot token, disabled group joining, the owner's
numeric Telegram user ID/private account, exact public HTTPS API and dashboard
origins with TLS, a verified site/current owner, fresh independent recovery
authority, journal custody, and qualified shared egress/DNS/robots composition.
Configure a dedicated `signal-telegram` KV v2 mount with `max_versions=1` and
`cas_required=true`; permit bot-path create/read and metadata delete only, not
other providers' secrets. Store no token or webhook secret in git or PR text.

Live `getMe`, `setWebhook`, private pairing, posting, callback identity/retries,
webhook deletion, and provider limits/latency remain `NOT_EXECUTED`. Automatic
weekly notifications, production composition, deferred-outbox supervision,
high-risk step-up initiation UX, and deployment monitoring are not qualified.
No merge, deploy, default-branch push, content deletion, CI edit, or repository
secret-read authority is created.

## Final-Tip Publication Gate

The train's final tip must pass every `scripts/run-*-tests.py` runner,
`scripts/openbao_lab.py`, `scripts/keycloak_lab.py`,
`scripts/check-gsc-provider-boundary.py`, the fast checks, `pip check`, and
`gitleaks git --config <main-config> --log-opts=main..HEAD --redact` before any
PR branch is published. The unchanged main configuration is used for the range
scan. Exact final-tip heads and gate counts are recorded in the PR publication
comments and the run-owned lab receipts; historical slice counts are not reused
as final-tip qualification. Lab cleanup is invocation-scoped, not global.

## Merge-Train Checkpoint

PR #11 is rebased after #15. Migration 0070 follows 0069, with 129 cumulative
app tables; migrations 0001-0062 remain unchanged. API/identity/tooling/connectors
pass 1,892 tests, repository 43 and dashboard 200; lint and format pass (477
files), and the full unsharded delivery lab passes all 40 cases with no skips.
The source-bound [delivery receipt](../evidence/0092-autonomy-delivery.json)
includes the exact-once and both stale Telegram regressions.

**Verification is incomplete; no branch publication or full-gate pass is claimed.**
Three full database invocations hit the unchanged 600-second subprocess budget
during the Telegram path, with no assertion failure reported before termination.
Its isolated end-to-end case passes in approximately 113 seconds. One preflight
retry refused startup below 3 GiB free; the train's generated dashboard cache was
removed, without touching other tracks. The lab budget has not been increased.
All required tests and product limits remain intact. The remaining full gate,
PR publication, retargeting and comments are held for database qualification.
Current counts and failures are recorded under `merge_train` in the
[Telegram evidence](../evidence/0092-telegram-approvals.json).

## Owner-Approved Database Lab Budget

On 2026-10-02 the owner approved a 1,200-second overall pytest subprocess budget
in `scripts/database_lab.py`. The aggregate suite now contains 962 cases, and six
parallel tracks share the machine; the former 600-second budget lacked headroom
for the additional rate-limited Telegram delivery path. This supersedes the
historical 600-second checkpoint above, not any qualification requirement.
Only the runner's overall subprocess timeout changes. No test, per-test timeout,
migration timeout, product limit, egress rule, source-integrity check, no-skip
requirement or invocation-owned cleanup guard changes. The budget adjustment is
a separate tip commit after the accepted Telegram commit and applies to the
whole stack. The full final-tip gate is still required before publication; its
exact heads and counts are recorded in the PR comments and run-owned receipts.
