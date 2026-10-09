# Slice 0133: Weekly Chat Reports and Alerts

Security-critical outbound-message slice rebased above main `168510b`. Migration `0085`
follows `0084` in one Alembic chain. It adds three forced-RLS app tables (167 cumulative)
and private configuration, route and queue-failure records. Existing migrations,
accepted specifications, egress profiles, scopes and provider methods are unchanged.
No dependencies, dormant services or production authority are added.

## Implemented

- Immutable owner/site/channel preferences, off by default, for the binding's
  chosen Slack channel, optional linked-owner DMs and paired Telegram private chats.
  Current owner, membership/site epochs, membership-change history, recovery and
  exact binding/link are checked. New bindings or pairings do not revive consent.
- Committed weekly closure snapshots the same weekly projection that email uses,
  enriched with existing 0094 measurement evidence. Rendering states what ran,
  opened PRs and observed live verification, bounded provider-reported GSC deltas,
  uncertainty/confounders, deferrals, Inbox waiting count and dashboard links.
  At most four direct committed GitHub PR links and three measurement summaries
  are emitted; further detail stays in the dashboard. Raw report text and other
  customer/model strings are withheld. Messages have no secrets, tokens, callbacks
  or approval controls at any risk class.
- Fixed pause, standing/binding/link revocation, failed/unknown delivery and stale
  binding alerts from committed state. Alert delivery cannot recursively generate
  more failure alerts. Rejected queues record closed errors without undoing a stop.
- A unique event/category/destination outbox intent; atomic revalidation and UTC
  site/provider attempt cap (1-100). Slack channel/DM attempts share one cap.
  Accepted, suppressed, failed and unknown states retain immutable receipts.
  Only pre-I/O egress deferral retries, at most three attempts, five minutes apart.
  Timeouts, malformed replies and provider rejections remain terminal unknown.
  Abandoned claims reconcile to unknown after thirty seconds; late completions
  cannot revive them. Concurrent/replayed claims cannot double-post.
- Existing OpenBao readers and shared-egress adapters only: `SLACK_BOT` /
  `chat.postMessage` and `TELEGRAM_BOT` / `sendMessage`. Credential reads precede
  a fresh authority claim. Revoked/unlinked/opted-out destinations are suppressed
  and audited without provider I/O. Positive paired IDs exclude group destinations;
  provider reply validation also requires a private Telegram chat.
- Owner Settings exposes each channel's availability/preference and twenty recent
  delivery entries, including explicit unknown outcomes. Strict same-origin BFF
  and CSRF-bound API accept only a channel and boolean preference; there is no
  browser queue, send, approval or resend endpoint. Missing optional composition,
  configuration, binding/pair or credentials is unavailable, never simulated.

## Verification

Exact commands, pass/fail counts and exclusions are in
[0133 evidence](../evidence/0133-chat-reports.json). Focused real PostgreSQL tests
pass under non-owner roles, and shared-gateway Slack/Telegram doubles qualify
bounded sends without contacting live providers. Synthetic credentials are
labelled `synthetic-...`. Full-gate results are recorded only after execution.

Tests cover fixed-vocabulary injection withholding, escaped token-free rendering,
exact PR links, message bounds, no buttons, replay, concurrent claims, safe retry,
daily caps, revoked binding and denial tombstones, removed/relinked membership,
unlinked/unpaired accounts, group IDs, recovery, opt-out after credential read,
tenant/site/role denial, unavailable providers, failed rendering and unknown sends.
Existing provider-profile negatives and step-up tests remain unchanged.

The owner/preferences and deployment-unavailable states were rendered from the
shipped component and stylesheet at 1440x900 and 390x844. The four synthetic
component captures linked in evidence have no horizontal overflow or overlapping
rows. The scoped review required no material fixes; incumbent design records and
pre-existing drift were left unchanged. This is not whole-app or live-provider
qualification.

Architectural decisions: [ADR-0136](../adr/0136-chat-report-outbox-and-unknown-outcomes.md)
and [ADR-0137](../adr/0137-current-owner-chat-report-preferences.md).

## First Live Run

The owner must privately provide the existing Slack app/bot configuration and
choose its one channel, link each optional DM account, and provide a Telegram bot
with group joining disabled and a paired private owner account. Use the existing
0091/0092 installation/pairing flows and dedicated OpenBao read-only workloads;
do not supply credentials, IDs or account details in git or PR text.

Under `signal_bootstrap`, call `configure_chat_reports(connection, provider=...,
origin="https://dashboard.example.invalid", daily_cap=...)` for each privately
configured provider. Replace the documented origin placeholder in protected
configuration. Under `signal_api`, compose the optional
`ComposedChatReportsGateway` with current external recovery authority, scoped
connections and existing OpenBao readers. The owner enables each desired channel
in Settings. Existing connector linking is not report consent.

In the trusted host, compose `ChatReportSender` with a dedicated `signal_identity`
connection, current external recovery generation and existing credential readers.
Pump `deliver_due(egress_factory, limit=20)` with gateways bound to the item's exact
site and provider, using existing shared-egress/owner-connector composition. The
host owns connection/gateway lifecycle and supervision; no new service is created.
Refresh recovery authority for each pump; never use a stale cached generation.

Qualify real provider acceptance, recipient visibility, private-only Telegram,
Slack linking, opt-out, revocation, cap exhaustion, restart/unknown behavior and
deployed pump/monitoring before customer enablement. Live Slack and Telegram,
customer receipt and deployed report pump remain **NOT_EXECUTED**. No merge,
deploy, default-branch push, deletion, CI edit, repository-secret access, expanded
autonomy, release certification or production write authority is granted.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0085` follows `0084`; 167 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2664 API/identity/tooling/connector
cases, 1137 PostgreSQL suite cases,
46 repository and 251 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.
