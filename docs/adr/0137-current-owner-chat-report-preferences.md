# ADR-0137: Current Owner Chat Report Preferences

Status: Accepted for internal local qualification; live delivery NOT_EXECUTED.
Date: 2026-10-03.

## Context

Slack may post only to the binding's chosen channel or linked users. Telegram
may message paired private accounts only and never join groups. The email design
establishes verified recipient identity, token-free summaries and dashboard
handoffs rather than notification-based authority.

## Decision

Default report/alert delivery off independently for Slack channel, Slack DM and
Telegram private chat. A current authenticated site owner sets their own
preference in Settings. Only the binding owner controls its chosen Slack channel;
other owners can opt into their own linked DM or paired chat. The preference pins
the binding/link, membership/site epochs, membership-change history and recovery
generation. A new binding/pair, changed membership or recovery cannot revive old
consent. Opt-out remains available when a provider is unavailable.

Dispatch uses the incumbent member/link authority resolvers, current preference,
exact chosen channel or paired positive private-chat ID and denial tombstones.
Revoked, unlinked, changed or opted-out destinations receive no provider request
and retain suppression receipts. Owner-only same-origin API/BFF commands accept
no recipient, provider payload, raw text or send command. History is bounded to
twenty entries for the current owner/site, with no payloads or secret references.

Rendering shares email's closed vocabulary. Raw customer/model text, evidence
strings, tokens and callback data are withheld, not heuristically redacted.
Slack disables markdown, mention parsing and previews; Telegram uses escaped HTML
and no keyboard. Only bounded numeric provider-reported measurement deltas and
fixed uncertainty language are emitted. Links point to the configured HTTPS
dashboard, except exact committed GitHub PR URLs with no query or fragment.
No approval buttons are present at any risk class; existing step-up rules remain
unchanged. Messages and lists have hard bounds, rejecting oversize projections.

## Alternatives

Default-on DMs would confuse account linking with report consent. Arbitrary
recipient configuration would bypass identity verification. Free-text summaries
with secret-pattern filtering cannot guarantee token-free delivery. Chat approval
buttons are unnecessary for reports and would add authority risk.

## Consequences

Owners must explicitly enable each destination. Missing binding, pair, operator
configuration, composition or credential is visibly unavailable. Provider
acceptance is not evidence that a person received or read a message.

## Verification

[0133](../implementation/0133-chat-reports.md) records rendering, current-role,
tenant/site, group, revocation, unpairing, opt-out and secret-read race tests.
Settings uses existing unframed sections and native checkboxes; synthetic
desktop/mobile captures do not claim live provider qualification.
