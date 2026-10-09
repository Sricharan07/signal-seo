# ADR-0107: Identity-Verified Email Opt-In Without Email Tokens

Status: Accepted for the internal optional email boundary

## Context

Revision 4.0 section 12 says never send to an unverified address and never carry
secrets or tokens. An email verification link would violate both requirements.
Email must not mint identity, session, publishing, or approval authority.

## Decision

Verification comes exclusively from the existing authenticated OIDC path. Its
validated identity must assert boolean `email_verified=true` and an ASCII
mailbox. Opt-in from the dashboard requires the same user's current tenant
membership, exact normalized address, identity proof and authentication time both
within ten minutes, current selected-site authority, and the external recovery
generation. The immutable preference records user, membership, address digest,
identity session reference, membership epoch/change count, identity claim epoch,
and recovery generation. No member can opt another person in or supply an IdP
verification assertion through the email API.

The next authenticated login records even a missing/unverified email claim.
Out-of-order or database-clock out-of-window claims invalidate verification and
record an immutable rejection outcome without rejecting authenticated sign-in.
The claim timestamp high-water mark cannot regress or accept a rejected future
timestamp. Genuine authority/programming errors still roll back the entire
session-issuance transaction; no partially issued session is returned.
A different claim invalidates old opt-ins, including changing away and back.
Every membership change is append-only history, so de-provisioning and restoring
the same membership cannot revive verification. Address verification is fresh
when granted; notification preferences persist without requiring a login every
ten minutes. Dispatch independently rechecks the current claim, membership,
owner role, site grant, active tenant/site/user, latest preference, recovery
generation, and bounce history. Ineligible attempts record suppression receipts
and perform no SMTP submission. Reverification requires a fresh opt-in.

Messages contain no login, verification, unsubscribe, approval, or other secret
tokens. Preferences and approvals use ordinary dashboard URLs with normal login
and step-up. The report is the 0104 committed dashboard projection. Rendering
selects a bounded fixed vocabulary of stages/outcomes, PR numbers with live
verification state, Inbox count, and known deferral reasons. Arbitrary customer,
model, evidence, identifier, digest, and URL strings are withheld, rather than
relying on probabilistic secret redaction. Minimal HTML escapes the entire text.
PR references lead to Changes in the dashboard, not external or one-click links.

## Alternatives

- Verification email or tokenized approval URLs contradict section 12.
- Treating editable contact email as verified does not establish identity proof.
- An owner attestation for another member does not satisfy same-person proof.
- Rendering raw report summaries could leak tokens or customer content.

## Consequences

Members with false/missing IdP verification, stale authentication, or mismatched
claims cannot enable delivery. A new verified address, changed membership, or
recovery generation requires opt-in again. Only authorized owners receive these
owner-visible reports; other members may maintain their own preferences but gain
no report read or delivery authority. Unsubscribe is a dashboard preference, not
an emailed token. Existing Slack and dashboard decision paths are unchanged.

## Verification

See [0093](../implementation/0093-email-reports.md): expiry/freshness, missing or
false verification, address mismatch, wrong person/tenant/role, address changes,
de-provisioning, opt-out, bounce and recovery negatives; token-shaped injected
values never appear in outgoing messages. Protected specifications are unchanged.
