# Signal
## Production Engineering Specification and Build Contract

**Revision:** 4.1, owner-accepted unprotected default branch amendment
**Prepared:** October 2, 2026
**Amends:** [Revision 4.0](Signal_Production_Engineering_Specification_Revision_4_0.md)
and [Revision 3.2](Signal_Production_Engineering_Specification_Revision_3_2.md).
**Document status:** Owner-approved narrow contract amendment, not a release
certificate, provider qualification, or production authority.

## 1. Precedence And Scope

Revision 4.1 changes only Revision 3.2 section 21.4's requirement for branch or
ruleset protection, for the PR-only exception below. Revision 4.0 and every
Revision 3.2 requirement not explicitly amended here remain normative. The
protected-base default, workflow trust inspection, required checks, certified
delivery flow, and all identity, tenancy, evidence, recovery, and egress controls
remain in force. Revisions 3.0, 3.1, 3.2, and 4.0 stay byte-for-byte preserved.

## 2. Narrow Owner Risk Acceptance

A repository binding may use an unprotected **default branch** only after a
current site owner explicitly accepts that risk in the authenticated dashboard,
with MFA authenticated within the preceding five minutes. A chat statement,
model recommendation, standing grant, installation, or generic approval is not
this acceptance. The normal binding path continues to reject an unprotected base.

Record the acceptance immutably, including the owner, acceptance time,
authentication time, exact repository ID and name, installation ID, default
branch, observed protection state (`none` or a provider-established
`unavailable_on_plan`), observed installation-permission fingerprint, membership
and site epochs, and externally anchored recovery generation. An unknown provider
state is not an unprotected-state observation and cannot be accepted. Do not infer
a plan limitation merely from an absent protection flag.

The binding displays the persistent warning:
**Unprotected default branch (owner-accepted)**.

## 3. Authority And Invalidation

For an accepted unprotected base, **every PR requires exact owner Inbox approval**.
This exception uses dashboard approval only. Standing grants can never authorize
dispatch on that base, regardless of recipe eligibility or model confidence.
Acceptance itself does not approve a candidate or create a repository operation.

Current provider inspection automatically invalidates acceptance when repository
identity, default branch, installation, or installation permissions change. Record
invalidation append-only; reverting provider state does not revive acceptance.
Owner removal, authorization-epoch changes, binding revocation, and recovery
generation changes also deny use. Provider failure blocks dispatch and invalidates
the observed exception rather than guessing its state. Re-acceptance requires a
fresh human action and fresh MFA; identity or binding-scope drift requires a new
binding. If protection becomes available, prefer it, and do not require or consume
the old exception. Returning to an unprotected base requires re-acceptance.

All Signal write guards are unchanged: repository-bound
`GITHUB_REPOSITORY_WRITE`, only new `refs/heads/signal/<hex>` branches, no PATCH/PUT,
no merge/default-branch push/deploy, no workflow or secrets endpoints, no workflow
files in patches, and protected-path preflight. Repository workflow certification
remains a separate prerequisite; owner risk acceptance cannot waive it.

## 4. Evidence And Release

Positive, negative, and failure tests must cover owner/fresh-MFA acceptance,
immutability and tenant isolation, state drift and nonrevival, recovery generation,
protected-base preference, default rejection without acceptance, and final
standing-dispatch denial with exact owner-approval success. Live provider
qualification is separate and starts `NOT_EXECUTED`. No production write or release
admission is created by this amendment. [ADR-0120](docs/adr/0120-owner-accepted-unprotected-base.md)
records the implementation decision; [status](docs/implementation/status.md) is
the source of truth for implemented and tested behavior.
