# ADR-0075: Bind Slack Actions to the Current Person and Exact Revision

Status: Accepted for the internal, optional Slack boundary.

## Context

Revision 4.0 sections 5.1 and 12, EC-140/141, and Revision 3.2 sections 18.3
and 25 require chat approvals to use the linked person's current authority,
never the installation owner's authority or message text. An old button is
not approval for a new revision. Provider success is not production readiness.

## Decision

Use one owner-selected workspace/channel per verified site. OAuth v2 requests
only `chat:write`; bot, client, and signing secrets remain in a dedicated OpenBao
KV v2 mount. The dashboard initiates a five-minute single-use link sent directly
to the expected Slack user; both dashboard membership and the signed Slack
identity must match. Current member/site state, role, epochs, recovery generation,
revocations, and independently replayed denial tombstones are checked at use.

All provider I/O uses the existing shared egress gateway. A closed form-POST
OAuth profile permits only `/api/oauth.v2.access`, with exact client ID/secret,
code and redirect fields, no query string, and a JSON response, following the
Google token-exchange pattern in [ADR-0083](0083-scope-shared-egress-by-profile.md).
A separate closed JSON POST bot profile
permits only `chat.postMessage` and `auth.revoke` with the bound bearer token
on `https://slack.com`. No history, discovery, admin, response-URL, or read path
exists. Bounded messages carry immutable revision ID/hash, dashboard evidence,
and opaque per-message callbacks. Customer text is not sent.

Durable outbox claims precede posting. An acknowledged message is never posted
again. Pre-dispatch admission deferral can return to queued only when no egress
operation exists. Any dispatched/ambiguous outcome stays unknown or dispatching;
no claim of remote exactly-once delivery is made.

Verify raw-body v0 HMAC and five-minute timestamp with constant-time comparison;
persist signature hashes under an atomic replay lock. Match workspace, actual
posted channel/message, intended user, action, and callback. The existing Inbox
decision function is shared, preserving the same immutable decision record and
0084 eligibility checks. Slack never substitutes browser MFA. Deterministic
recipe minima cannot be lowered by a model or manifest; A3+ and unknown classes
hand off to the dashboard. High-risk dashboard decisions require fresh MFA.

Binding/link revocations take effect locally in their transaction and enter the
0102 deny-only journal. Pending independent acknowledgement is explicit.

## Alternatives

Workspace-wide owner authority, approval-by-message-text, history-reading bots,
and dashboard-session impersonation were rejected. Blind retries were rejected
because a lost `chat.postMessage` response does not establish non-delivery.

## Consequences

Channel IDs are explicit rather than discovered with additional scopes. A failed
or deferred OAuth exchange requires a new installation attempt, never reuse of
a consumed code. Unknown delivery requires operator review. Low-confidence
work may request review through the outbox; no authority is minted. The optional
gateway is unavailable by default and has no production supervisor yet.

## Verification

See [0091 implementation](../implementation/0091-slack-approvals.md) and
[0091 evidence](../evidence/0091-slack-approvals.json). Live Slack remains
`NOT_EXECUTED`; local doubles run behind real shared egress and PostgreSQL.
