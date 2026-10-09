# ADR-0076: Keep Telegram Private and Bound to Exact Revisions

Status: Accepted for the internal, optional Telegram boundary.

## Context

Revision 4.0 section 12 and EC-140, and Revision 3.2 section 25 require paired
people, current authority, exact revision identity, and dashboard step-up for
high-risk decisions. Telegram embeds its bot credential in the HTTP request
path; ordinary URL evidence or transport diagnostics could expose that secret.

## Decision

Extend [ADR-0075](0075-bind-slack-actions-to-current-person-and-revision.md), not
its decision logic. Telegram uses the same current-member authorizer, canonical
risk calculation, sealed-revision checks, immutable Inbox decision function,
0084 eligibility path, recovery generation, and independent denial journal.
Chat never inherits dashboard MFA or the installation owner's authority.

A current owner of a verified site can install one bot through OpenBao CAS-zero
storage, `getMe`, and `setWebhook`. Group joining must be disabled in BotFather
and explicitly confirmed by `getMe`. Allowed updates are only `message` and
`callback_query`. A bot ID is globally reserved before webhook registration;
failed installation remains revocable. Local revocation immediately denies use,
attempts webhook deletion once, and destroys bot-secret metadata. A confirmed
deletion releases the bot reservation; an ambiguous deletion never does.

The dashboard issues a five-minute, single-use deep-link code for an expected
numeric Telegram user ID. Only that person's private `/start` message can bind
the private chat, Signal member, current membership epoch, and recovery
generation. Unpairing and de-provisioning invalidate authority. Group, supergroup,
and channel updates are audited and ignored; ordinary message text is data.

A closed `TELEGRAM_BOT` shared-egress profile permits only bounded JSON POST to
`getMe`, `setWebhook`, `deleteWebhook`, `sendMessage`, and `answerCallbackQuery`
on `https://api.telegram.org`. It records safe method URLs only. A typed private
credential creates the token-bearing request target at the pinned transport,
with HTTP debugging disabled. Robots evaluates the private target in memory;
durable evidence uses the safe logical URL. Provider response headers and raw
transport exceptions are not retained for this profile. Other profiles are not
widened, and token-bearing public URL input is denied.

Constant-time webhook-secret comparison precedes parsing. Durable update-ID
serialization rejects replay. Callback hashes bind the accepted outbox item,
intended user, chat, message, and exact revision. Messages contain fixed bounded
escaped summaries, the sealed ID/hash, and dashboard evidence. A3+, canonical,
unknown, or above-configured risk creates only an outbox dashboard handoff;
fresh dashboard MFA remains required at decision time. Accepted and ambiguous
outbox writes are never blindly resent.

0104 consumes this same exact Inbox record. Forward migration 0070 extends the
0084 operation channel constraint to Telegram while the existing
`control.bind_github_pr_decision_channel` trigger derives the channel from the
referenced immutable decision. API and dashboard projections preserve it. There
is no additional Telegram write authority and migrations 0001-0062 are unchanged.

## Alternatives

Group bots, message-text commands, owner impersonation, bearer credentials in
recorded URLs, and duplicated approval policy were rejected. Blind webhook or
message retries cannot establish a safe remote outcome and were rejected.

## Consequences

Operators must disable group joining, know their numeric Telegram user ID, and
provide exact HTTPS webhook/dashboard origins. Ambiguous writes or deletions
require operator qualification, not automatic resend or forced unreservation.
Unconfigured composition stays visibly unavailable. No autonomous dispatcher,
production authority, or live-provider readiness is implied.

## Verification

See [0092 implementation](../implementation/0092-telegram-approvals.md) and
[0092 evidence](../evidence/0092-telegram-approvals.json). Provider doubles run
behind real PostgreSQL and shared egress; OpenBao and independent restore replay
are real. Live Telegram remains `NOT_EXECUTED`.
