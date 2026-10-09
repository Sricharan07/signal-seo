# ADR-0067: Observe PR Permission Separately From Repository Writes

- Status: Accepted
- Date: 2026-09-29
- Owners: Signal connector and identity boundaries
- Related: [ADR-0066](0066-bind-github-read-authority-to-owner-site.md), Revision 4.0 sections 4 and 12, Revision 3.2 section 21.1

## Context

The 0070 owner-selected GitHub binding has `contents: read` but no PR
permission. A detected framework does not grant authority, and an installation
may later lose permission, repository selection, or base protection. The
GitHub-first beta needs candidate format information before an isolated build,
without treating a permission observation as an external-write approval.

## Decision

Keep the 0070 binding as the only repository selection. A current site owner
prepares a separate, revocable PR-capability extension bound to that exact
binding. A short-lived installation token requests only `contents: read` and
`pull_requests: write` for the one selected repository. The adapter inspects
repository identity and protected base, then reads the base commit/tree with a
separate contents-read token. Only validated tree metadata informs deterministic
framework and content-format detection. Truncated, missing, symlink, or
ambiguous evidence never becomes candidate-compatible.

The permission and format observation is durable, not an authorization to
write. Every downstream use must recheck current local/session/site authority,
binding status, permission, repository identity, and base SHA. No PR endpoint,
branch creation, checkout, or repository mutation is admitted here. Slice 0084
must add a separate write-intent journal and reconcile uncertain outcomes.

## Consequences

The extension can be locally qualified without a live App; real GitHub success
remains `NOT_EXECUTED` until a dedicated App and test repository exist. The
existing prohibition on merge, deploy, default-branch push, workflow edit,
secret read, and self-granted autonomy is unchanged. This is not a release or
beta admission.

## Verification

See [slice 0079](../implementation/0079-github-pr-authority-framework-detection.md)
and its [evidence record](../evidence/0079-github-pr-authority-framework-detection.json).
