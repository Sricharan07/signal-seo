# ADR-0136: Chat Report Outbox and Unknown Outcomes

Status: Accepted for internal local qualification; live delivery NOT_EXECUTED.
Date: 2026-10-03.

## Context

Revision 4.0 sections 12 and 16 require weekly reports and alerts through the
existing Slack and Telegram bindings. Sending is security-critical even without
new egress. Neither provider guarantees an idempotent send after a lost reply.

## Decision

Use a shared tenant/site-scoped report outbox, not the approval callback outboxes.
Committed cycle closure snapshots the existing 0104/email weekly projection and
0094 measurement projection. Committed restrictions and delivery failures queue
fixed alerts. Unique event/category/channel/binding/destination identity absorbs
replay. Queue constraint failures are recorded without reversing restrictions.

Only the existing shared gateway and `SLACK_BOT` `chat.postMessage` or
`TELEGRAM_BOT` `sendMessage` may send. No profile or provider method changes.
Dispatch atomically rechecks the exact recipient and reserves a UTC site/provider
daily attempt cap. Slack channel and DM attempts share that cap. A claim commits
before I/O; immutable receipts contain closed outcomes and a message digest, not
provider reply text or credentials. No database lock spans network I/O.

Only shared-egress pre-dispatch deferral retries: at most three claims, at least
five minutes apart. Provider rejection, malformed response and timeout are
conservatively unknown and terminal. An abandoned claim becomes unknown after
thirty seconds. Late acceptance cannot overwrite unknown or trigger a resend.
There is no automatic/manual browser resend endpoint.

## Alternatives

Reusing the approval payload outboxes would mix report consent and callback
authority, which have different lifetimes. Blind retry or relying on a provider
request identifier would promise deduplication the providers have not qualified.
Direct HTTP or a new bot profile would bypass existing reviewed egress.

## Consequences

Some messages may remain unknown or unsent. The dashboard shows that explicitly.
The host must supply current recovery authority, existing OpenBao readers and
site/provider-scoped gateway composition, and supervise bounded pumps. Applying
the migration alone neither contacts a provider nor enables a capability.

## Verification

See [0133](../implementation/0133-chat-reports.md) and
[evidence](../evidence/0133-chat-reports.json). Disposable real PostgreSQL tests
cover concurrent claims, replay, caps, safe retries, lost replies, abandoned
claims, denial and immutable history. Provider doubles use the actual shared
gateway. Live Slack, Telegram and deployed pump qualification are NOT_EXECUTED.
