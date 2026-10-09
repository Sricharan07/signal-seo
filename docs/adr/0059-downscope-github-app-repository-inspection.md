# ADR-0059: Downscope GitHub App inspection tokens

## Status

Accepted on 2026-09-20.

## Context

The approved verified-homepage revision needs an exact repository, base branch,
base commit, and content path before Signal can build a candidate. A developer
personal access token would blur product authority with a human account and often
carry access to unrelated repositories. An installation token created with every
permission granted to the App would also give a read-only inspection operation
unnecessary standing capability.

GitHub supports App-signed JWT authentication and short-lived installation access
tokens scoped to selected repositories and requested permissions. Its current API
also uses a stateless installation-token format, so token validation must be
bounded without assuming the historical fixed length.

## Decision

1. Authenticate the provider boundary only as a GitHub App with an RS256 JWT that
   lasts less than ten minutes. Do not accept OAuth tokens or personal access
   tokens for this operation.
2. Mint a fresh installation token for one repository name and only
   `contents: read`. Accept only the implicit `metadata: read` addition, reject
   write/admin or unrelated permission grants, and require the response to name
   exactly the selected repository.
3. Use that token only in memory to read repository metadata and the exact selected
   base branch. Return repository ID, canonical full name, visibility, default and
   selected branches, exact 40-character base SHA, protected flag, approved content
   path, and credential expiry. Never return or log either credential.
4. Fix the provider origin, GitHub API version, media type, TLS verification,
   timeout, redirect policy, environment-proxy policy, response size, and sanitized
   failure classes.
5. Treat archived, disabled, wrong-repository, wrong-branch, malformed, broadened,
   and ambiguous provider state as blockers. This read operation grants no branch,
   commit, pull-request, merge, workflow, or deployment authority.

The provider contract follows GitHub's official
[installation-token endpoint](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app)
and [GitHub App permission guidance](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app).

## Alternatives

- **Use the developer's `gh` login or PAT.** Rejected because it is not a product
  connector, cannot express one installation boundary, and may silently carry
  unrelated user authority.
- **Mint a token with the App's full permission set.** Rejected because inspection
  needs only metadata and repository contents reads.
- **Clone immediately.** Rejected because durable owner/site binding and exact
  provider identity must exist before repository content enters a candidate
  workspace.
- **Assume a 40-character installation token.** Rejected because GitHub's current
  stateless format is longer and JWT-shaped.

## Consequences

Signal now has a real least-privilege provider protocol for inspecting one exact
repository and base commit without using a PAT or retaining the token. A dedicated
GitHub App installation and server-side private-key secret are still required for
authorized provider success. Durable site binding, candidate checkout, branch and
ruleset qualification, PR writes, webhook reconciliation, and delivery verification
remain separate future slices.

## Verification

- Thirty-four adapter tests cover signed JWT claims, exact request scope, current
  token format, canonical repository and branch identity, state rejection,
  broadened permissions, malformed/oversized responses, transport failure, target
  validation, TLS enforcement, and credential non-disclosure.
- A live call to `api.github.com` with a generated non-App key reached the fixed
  provider boundary and returned the sanitized nonretryable
  `GITHUB_AUTHORIZATION_REJECTED` class. This is negative protocol evidence, not an
  authorized installation qualification.
