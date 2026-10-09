# Slice 0125: Owner-Accepted Unprotected Default Branch

Classification: **security-critical**. Contract: [Revision 4.1](../../Signal_Production_Engineering_Specification_Revision_4_1.md),
[ADR-0120](../adr/0120-owner-accepted-unprotected-base.md).

## Scope

One narrow exception to Revision 3.2's protected-base requirement. Normal binding
still durably rejects an unprotected branch. A separate dashboard command requires
current Owner authority, explicit unchecked consent, and MFA within five minutes
before any provider access and again in the activation transaction. It independently
observes the exact repository, default branch and actual installation permissions
through the existing shared-egress gateway. Acceptance records immutable owner,
authentication/acceptance time, repository identity, branch, observed protection,
installation/permission fingerprint, authorization epochs and recovery generation.
An unprotected observation is recorded as `none`; the provider cannot establish
plan availability, so the implementation never invents `unavailable_on_plan`.

Migration **0076**, following 0075, adds two forced-RLS/function-only
immutable tables and monotonically increasing binding risk generations. No
existing migration is edited. Provider identity/default-branch/installation or
permission drift, unavailable inspection and recovery invalidate the exception.
Matching old state cannot revive it. Later protection is preferred, and returning
to an unprotected state requires fresh acceptance. The dashboard persists
`Unprotected default branch (owner-accepted)` while the exception is current.

Standing authorization never dispatches on this base. Exact owner Inbox approval
must be from the dashboard for every PR; all original epoch, recovery, recipe,
receipt, scope and fencing checks remain. The ref-restricted write profile, no
PATCH/PUT/merge/default-branch push/secrets, protected-path preflight and workflow
certification remain unchanged. A repository containing workflows can still be
blocked by existing certification. Astro builds and delivery are not enabled.

## Qualification

The final frozen-source gate passed: 885 PostgreSQL, 37 PostgreSQL/Temporal
delivery, 1,668 API/identity/tooling/connector, 43 repository and 155 dashboard
cases, plus all other required labs, formatting, dependency, type and build
checks. Exact commands, per-gate counts and source-hashed invocation-owned cleanup
are in [the evidence record](../evidence/0125-unprotected-base-acceptance.json).
The default main-branch gitleaks configuration remains unchanged.

Positive, negative and failure coverage uses real PostgreSQL and Temporal, with
synthetic provider doubles behind shared egress. Live GitHub, private repository
inspection, owner risk acceptance on a live binding, and production writes are
**NOT_EXECUTED**. No production release or external delivery authority is claimed.

## First Live Run

The integration thread needs protected exact site/repository/App configuration,
private OpenBao credentials and admitted shared-egress contexts; a verified site;
an actual owner dashboard session with MFA within five minutes; and explicit risk
consent after observing the default branch. Reinspect installation permissions,
exercise default denial, acceptance, invalidation and later protection, and retain
redacted evidence. Each PR additionally needs exact dashboard Inbox approval and
the existing workflow certification and qualified build/delivery prerequisites.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0076` follows `0075`; 150 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2082 API/identity/tooling/connectors, 1005 PostgreSQL,
43 repository and 222 dashboard cases; Ruff check and format passed.
The technical eligibility update targets `github_pr_eligible_before_indexnow`,
preserving both the IndexNow retired-key guard and the exact article-authority
wrapper. The first migration attempt detected the renamed wrapper signature;
after correcting that integration conflict, the complete fast gate passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
