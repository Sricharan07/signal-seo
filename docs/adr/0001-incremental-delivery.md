# ADR-0001: Incremental Delivery With Evidence

- Date: 2026-09-07
- Status: Accepted; future pilot sequence superseded by ADR-0030
- Scope: Repository and implementation workflow

## Context

The production specification is much larger than a safe initial delivery. The user
requires maintained documentation, tests, and a commit for every completed small slice.

## Decision

Preserve the accepted specification baselines. Track actual implementation
independently in the status page, implementation records, and changelog. Begin
with the then-active corrected specification's WordPress feasibility experiment
before broad application work. Commit each verified slice locally; no automatic
remote publication or deployment.

Use Node.js 22's built-in test runner for repository tooling. Parse Markdown with
markdown-it, GitHub headings with github-slugger, and YAML with the yaml package.
These pinned development dependencies avoid inventing a Markdown parser and do not
dictate the Python API or eventual Next.js application architecture.

## Alternatives

- Implement the entire catalog first: delays validation of provider assumptions.
- Treat the specification as the implementation tracker: blurs planned and executed evidence.
- Write a custom Markdown parser: unnecessary parsing edge cases and maintenance.

## Consequences

Each code change needs documentation and tests in the same commit. An experimental
pass is not a production certification. Documentation checks are reproducible with
`npm ci` followed by `npm test`. Application dependencies will be introduced only
when their corresponding code is implemented.

## Verification

The [repository implementation record](../implementation/0001-repository.md)
defines positive and negative checks. All production release gates remain unearned.

## Supersession

[ADR-0030](0030-github-first-core-v1.md) supersedes this record's future pilot
sequence after the preserved WordPress feasibility experiment. It establishes the
owner-controlled GSC, GitHub, public-site, dashboard, and Telegram flow with all
nine non-CMS roles as Core V1. This record's incremental, evidence-backed delivery
method and baseline-preservation decision remain active.
