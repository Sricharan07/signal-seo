# ADR-0159: Current Owner PR Permission Grant

Status: Accepted for local security-critical implementation; live qualification pending.
Date: 2026-10-04.

## Context

The 0079 PR-permission observer is required by candidate preparation and delivery
but has no owner browser entrypoint. Exposing it is security-critical under
Revision 4.0 section 19. An observed GitHub permission is not a standing grant,
an approved revision, a qualified write runtime, or production readiness.

## Decision

Expose prepare, finish and revoke through the existing owner connector API and
same-origin dashboard BFF. Reuse the exact active read binding, verified site,
current selected-site owner and MFA session. Require the existing five-minute
MFA window, excluding future timestamps, before every mutation and again when
recording the provider observation. The browser supplies intent identifiers,
never a provider snapshot or permission assertion. Finish independently observes
the installation, repository, branch and format through existing shared egress
and OpenBao credential readers. It rejects another intent, request key, owner,
site, tenant and completed/revoked intent before provider I/O. No OAuth callback
or new provider permission acquisition path is introduced.

Show the loaded permission state, exact repository/base/content path and the
existing `signal/<32-character operation ID>` write-branch convention. Keep
identifiers in Technical details and separate this optional permission from the
read binding and standing allowance. An unreadable state is not granted.

Extend the independent authority-restriction journal with a deny-only
`github_pr_extension` target. Local revocation atomically enqueues its restriction;
only an independently verified receipt returns `ACKNOWLEDGED`. Otherwise return
`AUTHORITY_DURABILITY_PENDING`, including an idempotent retry after an unknown
response. Restore replay hides an old observed extension through restrictive RLS
from all existing preparation/build/delivery consumers, not just the owner read.
Revoking PR permission does not remove the GitHub read binding.

The grant never permits merge, deploy, default-branch push, CI workflow edits or
secret reads. Existing exact-revision/standing authority, protected-path,
unprotected-base acceptance, live permission rechecks and write-journal guards
remain mandatory. This slice changes no recipe eligibility or write dispatcher.

## Alternatives

- Accept a browser callback's permission fields: rejected; untrusted browser data
  cannot establish provider authority.
- Treat the read binding or observed PR permission as standing authorization:
  rejected; these are independent, narrower prerequisites.
- Revoke only in primary storage: rejected; an older restored primary could
  resurrect the extension without an independent deny record.
- Add a separate provider flow or service: rejected; existing bounded observation,
  credentials and egress already implement the relevant contract.

## Consequences

The owner must configure the actual GitHub App's narrow permissions separately;
an unchanged read-only installation fails confirmation rather than simulating
success. Missing composition stays unavailable. Journal failure leaves local
revocation explicit and recovery durability pending. No live GitHub permission,
PR creation, deployment or production readiness is claimed by local tests.

## Verification

See [0149 implementation](../implementation/0149-owner-paths.md) and
[evidence](../evidence/0149-owner-paths.json) for owner/MFA/site/binding negatives,
forged/replayed finish, browser-proof failures, pending/repeated revocation,
independent PostgreSQL/OpenBao journal and restored-primary denial. Migration
0093 follows unchanged 0092. Accepted specifications are preserved.
