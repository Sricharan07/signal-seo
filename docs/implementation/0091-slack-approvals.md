# Slice 0091: Slack Linking and Exact-Revision Approvals

Security-critical. Internal optional connector; live Slack and production
composition remain **NOT_EXECUTED / unavailable**.

Rebased onto `b7cda0f` with Business Brain behavior preserved. Migration `0058`
follows `0057`; migrations `0001`-`0057` are unchanged. Six forced-RLS tables hold installation attempts,
bindings, per-member links, one-use link codes, message outbox, and immutable
ingress audit. Runtime roles have only bounded security-definer entry points,
not table write access. The existing current-member authorizer is shared by
dashboard sessions and Slack links; the existing exact Inbox decision function
is shared by both channels. The 0084 path consumes that same immutable record.
Accepted specification revisions are unchanged.

## Implemented Boundary

- Current owner and verified site initiate ten-minute OAuth state, exact workspace
  and one channel, and a configured A0-A2 ceiling. Only `chat:write` is requested;
  unexpected scopes, workspace, enterprise installs, or rotating tokens fail closed.
- OpenBao-only OAuth client/signing secrets and CAS-zero bot references; revocation
  destroys bot metadata. No credentials enter ordinary tables, provider artifacts,
  or error bodies. A failed confirm destroys its newly stored bot secret.
- Dashboard-initiated expected-user link, five-minute expiry, single consumption,
  current membership epoch, revocation, and de-provisioning denial.
- Exact Slack egress profiles retain robots, public-address/pinning, origin
  admission, immutable operation evidence, response bounds, and timeout controls.
  OAuth uses form POST to `/api/oauth.v2.access` with exact client ID/secret,
  code and redirect fields and JSON response. Bot posting and revocation retain
  JSON POST with the bound bearer token. JSON OAuth bodies and query strings fail closed.
- Outbox-backed direct-user and chosen-channel messages contain exact candidate
  ID/hash, bounded fixed summary, escaped text, evidence link, and approve/reject
  callbacks. Text and `response_url` are never executed or followed.
- Raw-body HMAC v0, constant-time verification, five-minute time window, durable
  replay serialization, workspace/channel/message/user/callback matching, current
  person/site authorization, and exact current-revision checks. Each known binding
  rejection is audited without retaining request text.
- A3+, A4 always, above-configured, and unknown risk hand off without a decision.
  Dashboard high-risk review requires MFA within five minutes. Slack records
  primary authentication, never inherited dashboard MFA.
- Binding/link revocations enter the independent 0102 journal; restore replay
  adds denial tombstones. `AUTHORITY_DURABILITY_PENDING` is truthful until receipt.
- Optional API composition and same-origin CSRF-protected dashboard installation,
  OAuth callback, per-person link/unlink, owner disconnect, and exact Inbox request.
  Unconfigured or unreadable secrets produce visible unavailability, not fake controls.

## Delivery Semantics

`queued -> dispatching -> accepted|unknown` is durable. Only an admission refusal
before an egress operation exists can return to queued. Accepted, unknown, or
abandoned dispatching messages are never blindly reposted. Retry of an exact
revision reuses its outbox ID. This is at-most-once dispatch, not a promise that
Slack deduplicates ambiguous writes. Link requests expire rather than auto-reissue.

An OAuth state is consumed before the exchange. Deferral or ambiguous failure
requires restarting consent. Rejected provider scope/workspace results do not
create a binding; an operator must remove an unexpected upstream installation.
The chosen channel must already admit the bot; no channel-discovery/join scopes
are requested.

## Verification

Run the commands and inspect counts in [0091 evidence](../evidence/0091-slack-approvals.json).
Real PostgreSQL tests cover installation, expiry/reuse/wrong person, secret
isolation, immutable audit, signatures/time/replay, EC-140 current-membership
variants, wrong workspace/channel, revision mismatch/stale base, A3/A4 handoff,
and acknowledged/ambiguous outbox retry. The qualified A2 fixture proceeds from
Inbox through a linked-owner Slack approval to the same dashboard-readable
decision and the existing 0084 operation. Admin-seeded signed A3/A4 fixtures test
callback policy only, not qualification of a high-risk build or write pipeline.
The database lab timeout accommodates the added full continuation regression;
no authority, assertions, provider boundaries, or failure gates are relaxed.

Real TLS OpenBao checks qualify dedicated read-only client/signing ACL, CAS-zero
bot creation, overwrite rejection, and permanent secret deletion. The separate
PostgreSQL journal/restore lab verifies signed Slack denial replay. UI screenshots
cover actual unavailable state and explicitly synthetic owner-form layout at
1440 and 390 pixels; synthetic captures do not claim provider readiness.
Captures: [unavailable desktop](../evidence/0091-slack-unavailable-desktop.png),
[unavailable mobile](../evidence/0091-slack-unavailable-mobile.png),
[synthetic owner desktop](../evidence/0091-slack-owner-desktop.png), and
[synthetic owner mobile](../evidence/0091-slack-owner-mobile.png).

Provider protocol references: Slack's
[OAuth v2 access method](https://docs.slack.dev/reference/methods/oauth.v2.access/)
is used with form encoding; the
[auth.revoke method](https://docs.slack.dev/reference/methods/auth.revoke/)
lists JSON as an accepted content type, and
[chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)
documents `chat:write` and user-ID destinations. These references do not replace
the required live qualification.

## First Live Run Inputs

The owner must provide a disposable Slack workspace, exact channel and linked
user IDs, a Slack app with only `chat:write`, bot membership in the chosen channel,
client ID, client secret, signing secret, exact HTTPS dashboard OAuth redirect,
and the binding-specific signed interactivity URL
`/v1/slack/<binding-id>/interactivity`. Store client configuration at
`signal-slack/data/client` with exactly `client_id`, `client_secret`, and
`signing_secret`; permit connector read only, bot-path create/read, and bot
metadata delete. Provide verified site/current owner, fresh external recovery
authority, independent journal custody, and a correctly composed shared egress
gateway with live network/robots evidence. No credentials belong in git or PR text.

Before production: qualify live OAuth, direct-user posting, chosen-channel action
identity and retries, revocation, provider latency/limits, and a deployment-bound
supervisor that pumps deferred messages within their expiry. Automatic weekly
workflow notifications, high-risk step-up initiation UX, Slack app distribution,
and production monitoring are not qualified by this slice. No merge, deploy,
default-branch push, content deletion, CI edit, or secret read is authorized.
