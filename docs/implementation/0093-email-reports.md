# Slice 0093: Email Reports and Alerts

Security-critical optional outbound boundary, based on `404eeb9`. Migration
`0068` follows `0067` on the accepted merge train; the review correction adds
forward migration `0069` after `0068`. Migrations 0001-0062 remain unchanged.
The implementation uses no Signal-operated service and adds no package
dependency. Accepted specification revisions remain byte-for-byte unchanged.

## Implemented

- Operator-configured SMTP host/port, STARTTLS or implicit verified TLS, sender,
  exact HTTPS dashboard origin, and per-site UTC daily cap (1-100 attempts).
  Credentials remain in OpenBao. Unconfigured composition or unreadable
  credentials is visibly unavailable in the API and Settings.
- The closed `SMTP_SUBMIT` shared-egress path reuses public-address screening,
  numeric-peer pinning and global origin buckets; HTTP profiles cannot borrow it.
  Replies, DNS, submission duration, message size and batch size are bounded.
- Existing OIDC session issuance records the exact authenticated verified email,
  including absence of verification. A dashboard self opt-in requires fresh
  proof and authentication (ten minutes), exact normalized address, same user and
  membership, and current recovery generation. There is no verification email.
  Immutable opt-in provenance, claim epochs and membership-change history prevent
  address/de-provision changes, including changing back, from reviving opt-in.
- Weekly cycle closure queues the same owner-visible 0104 report projection used
  by the dashboard. Committed pauses, revocations, delivery failure/ambiguity,
  failed connector bindings, and live verification regressions queue fixed alerts.
  Each event/recipient/category has one immutable outbox intent. Oversized or
  constraint-rejected notification queues produce an immutable error-class audit
  without rolling back an authority reduction.
- Messages have bounded plain text and escaped minimal HTML: what ran, PR numbers
  and observed live status, Inbox count, and known deferral reasons. Free-form
  customer/model strings are withheld. Every link is a plain dashboard URL;
  Changes exposes the actual PR links. No secret, credential, session, approval,
  verification or unsubscribe token is rendered. Email grants no approval authority.
- Current opt-in, claim epoch, membership history, owner/site authority, active
  tenancy, external recovery generation and bounce history are rechecked at
  dispatch. Rejected recipients get no SMTP commands and have suppression receipts.
  Immutable receipts record recipient/message digests and provider response class,
  never provider response text or credentials.
- Definite transient rejection permits at most three attempts, five minutes
  apart. The site cap counts attempts including retries. Accepted or unknown DATA
  outcomes are terminal; abandoned dispatches reconcile to unknown after thirty
  seconds. Concurrent/replayed dispatch cannot double-send. Settings exposes the
  dashboard notification preference and explicitly unknown delivery state.

## Boundaries

[ADR-0106](../adr/0106-closed-tls-smtp-submission.md) specifies the narrow non-HTTP
egress extension; [ADR-0107](../adr/0107-identity-verified-email-opt-in.md) records
identity-provider verification and token-free rendering. Runtime roles receive
only scoped security-definer functions, not table privileges. Preferences and
receipts have forced tenant RLS and immutable guards. The global routing table is
private to the trusted sender; it does not expose customer data to the API.

Verification freshness applies when opting in, not to every future weekly send.
Logout is not an unsubscribe. A changed/missing next-login claim, membership
change, bounce or external recovery generation blocks delivery until the
applicable prerequisite is restored and the member opts in again. A bounced
address remains suppressed even if opted in again; use a newly IdP-verified
address. A site grant removed and later restored does not itself recreate a
removed tenant membership or opt-in.

### Sign-In Availability Review Correction

Optional email proof freshness cannot reject an otherwise authenticated sign-in.
An out-of-order claim or a claim outside the database's eleven-minute past /
thirty-second future window clears the current verified digest and advances the
claim epoch. The timestamp watermark never moves backwards or accepts the
rejected future timestamp. Earlier opt-ins cannot revive, including from a
previously verified session. A fresh verified login and explicit opt-in are needed.

Every claim records an immutable closed outcome: `recorded`, `unverified`,
`stale`, or `outside_window`; rejected claims store no address or address digest.
False/missing IdP verification also records an unverified claim and permits login.
The existing application OIDC token-validation policy is unchanged. Tests exercise
accepted identities against a skewed application/database clock, not invalid OIDC
tokens. Wrong database role, session/hash, issuer/subject, or missing timestamp
remains a programming/authority error. No exception is swallowed: session, email
claim/head, and session-issued audit remain one transaction, and no opaque session
is returned before commit. Authority errors and later audit failures leave no
partially issued session or claim.

## Verification

Exact commands, counts, source evidence and exclusions are in
[0093 evidence](../evidence/0093-email-reports.json). Tests use disposable real
PostgreSQL with non-owner runtime roles and real loopback TLS SMTP servers behind
the actual shared gateway. Synthetic credentials are labelled `synthetic-...`.
The loopback exception is test-local monkeypatching; production has no private-IP
bypass. The existing TLS OpenBao lab covers dedicated SMTP credential read-only
ACL and denied writes. No public SMTP test service or customer account is used.

Tests cover false/missing verified claims, normalization, mismatched address,
stale authentication, tenant/role isolation, membership/address changes and
restoration, recovery, de-provisioning, opt-out, bounce, caps, definite retries,
lost acceptance, crash reconciliation, immutable audit, queue failures, projection
equivalence, plaintext/certificate rejection, injected secrets/token-shaped
values, and strict same-origin API/BFF commands. The dashboard extension retains
the incumbent settings layout; desktop/mobile captures use the actual component
and CSS with synthetic preference state, not a live provider.

Captures: [synthetic owner desktop](../evidence/0093-email-owner-desktop.png),
[synthetic owner mobile](../evidence/0093-email-owner-mobile.png),
[actual unavailable desktop](../evidence/0093-email-unavailable-desktop.png), and
[actual unavailable mobile](../evidence/0093-email-unavailable-mobile.png).
The bounded extension review disposition is `ship`; the documentation comparison
preserves the incumbent design files. Existing design-schema/narrative drift was
reported, not repaired. Actual unavailable captures are from the development
server, including its non-shipping development indicator.

## First Live Run

The owner must privately supply a public submission hostname and port, required
TLS mode and trusted certificate chain, sender mailbox, exact HTTPS dashboard
origin, UTC daily cap, and SMTP username/password. Write exactly `username` and
`password` to OpenBao KV-v2 `signal-email/data/smtp/default`. A dedicated reader
needs only `read` on that path; use separate operator custody for secret rotation.
Never put credentials or personal identifiers in git, logs, or PR text.

Using `signal_bootstrap`, call `configure_smtp(connection, SmtpConfiguration(...))`
with placeholders replaced by private configuration. Compose the API's optional
`ComposedEmailGateway` with the existing current recovery authority, scoped API
connection factory and OpenBao SMTP reader. In the existing trusted host,
compose `SharedSmtpEgress` with a dedicated `signal_identity` connection, exact
same configuration, reader and `PinnedSmtpSubmitter`. Pump `deliver_due` in
bounded batches using a fresh external recovery generation; no HTTP send route
is provided. This reuses host composition, not a new dormant service.

The recipient must sign in through the existing verified OIDC path, select the
site, and personally enable notifications in Settings while authentication is
fresh. Qualify provider acceptance, inbox placement, bounce handling, certificate
rotation, restart/ambiguous outcome behavior, and deployed pump/monitoring before
customer enablement. Live SMTP send, real customer inbox placement, production
composition and supervision remain **NOT_EXECUTED**. Quiet hours, personalized
free-text report summaries, and tokenized unsubscribe are not implemented.
No merge, deploy, default-branch push, content deletion, CI edit, repository secret
read, new autonomy, or release certification is authorized by this slice.

## Merge-Train Verification

PR #15 is rebased onto the preceding accepted train head. Migration 0068, 0069
follows unchanged 0067, 0068; the cumulative app-table assertion is 124.
All five requested fast checks passed. The original qualification above is
historical; current counts and commands are in the `merge_train` entry of
[the evidence](../evidence/0093-email-reports.json). No production authority changed.
