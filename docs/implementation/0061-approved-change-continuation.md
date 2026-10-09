# Slice 0061: Approved Change Continuation

## Objective

Make the accepted verified-homepage proposal visibly continue into delivery
without representing an approved draft as a repository candidate, pull request,
deployment, or SEO outcome.

## Implemented

- Added a dedicated Changes projection for the verified-homepage model proposal.
  It displays the exact revision, evidence, target resource, before/after field
  value, human decision, and current delivery boundary.
- Added an explicit seven-stage delivery path: verified evidence, sealed Luna
  draft, human decision, repository binding, candidate, pull request, and live
  check. Only states backed by the existing committed proposal are complete.
- An accepted decision now redirects from Approvals to Changes. Pending revisions
  return to approval, and rejected or edit-requested revisions remain closed.
- Added an approved-state action to continue to Connectors. The interface states
  that no branch, commit, pull request, deployment, or live result exists until a
  later capability commits its own evidence.
- Corrected the proposal rail to describe the verified-page evidence packet rather
  than the retired customer-visible fixture path.

## Safety Properties

- The page is a read-only projection over the existing exact proposal and human
  decision. It creates no new authority and performs no provider I/O.
- A repository, base commit, and content path are never inferred from the page URL
  or the approved copy.
- Closed revisions cannot appear to advance to repository delivery, and pending
  revisions continue to require the existing exact approval.
- GitHub and production writes remain disabled.

## Verification

Run from the repository root:

```sh
npm test --workspace apps/dashboard
npm run typecheck --workspace apps/dashboard
npm run build --workspace apps/dashboard
node --test tests/repository/dashboard.test.mjs
npm test
npm audit --omit=dev
```

## Limits And Next Work

This slice closes a visible workflow gap but does not build the repository
candidate. The next slice must bind one least-privilege GitHub App installation,
exact repository, base branch, and approved content path, then read and persist the
exact base identity. Only after that binding is current may an isolated,
credential-free candidate builder consume this approved revision.
