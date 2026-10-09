# ADR-0108: Exact Owner Editorial Delivery

Status: accepted for slice 0123, 2026-10-02.
Supersedes only ADR-0086's record-only limitation for this exact path.

## Context

Revision 4.0 sections 6.2 and 14 permit an A2 article or refresh PR. Semantic
grounding is unavailable, so the technical standing-grant set cannot include
articles. Legacy editorial reviews did not bind recovery or fresh MFA.

## Decision

Keep legacy editorial reviews record-only. A distinct dashboard command records
one immutable `content_delivery_decisions` approval bound to candidate, exact
SHA-256, current approving owner, authorization epochs and recovery generation.
Require MFA authenticated within five minutes using the existing dashboard rule.
Every flagged sentence path must be explicitly acknowledged; the candidate digest
binds its exact text and reasons. Neither Slack, Telegram nor a workload can mint
this approval. Rejection or requested changes prevents delivery approval.
Recording either after delivery approval also denies subsequent dispatch effects;
legacy approval can never increase write authority.

Add `owner_editorial` as a third exclusive operation authority kind. Generated
technical/content reference columns retain exact composite foreign keys. The
existing decision-channel trigger binds editorial source, owner, revision and
generation on insertion and makes all authority fields immutable. No synthetic
technical revision, recipe release, session, standing grant or Jev approval is
created. A recovery-generation change requires fresh drafting/sealing and approval.

## Alternatives

Rejected treating legacy reviews as grants, expanding the technical A2 registry,
or copying articles into the recipe table. Each would blur the authority source
or let old approvals become write authority without fresh human consent.

## Consequences

The existing journal, deterministic Git objects, fencing, per-effect checks and
read reconciliation remain the only PR path. Approving is not opening, merging,
deploying or proving delivery. Production composition remains unavailable.
Article autonomy remains excluded even under a standing grant and Jev `ship`.

## Verification

See [0123](../implementation/0123-owner-approved-article-delivery.md), its real
PostgreSQL/journal/shared-egress delivery suite and linked gate evidence.
