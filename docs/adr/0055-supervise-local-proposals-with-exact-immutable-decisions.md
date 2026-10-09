# ADR-0055: Supervise local proposals with exact immutable decisions

## Status

Accepted on 2026-09-13.

## Context

The local pilot could prove identity, durable audit execution, and one immutable
fixture finding, but a user still could not direct an agent-like operation or
exercise the authority boundary that separates analysis from action. Jumping
straight to GitHub would combine proposal quality, human authorization, and an
external write in one unsafe step. A free-form model response would also make the
first approval contract nondeterministic and difficult to audit.

## Decision

Implement the first supervised planning loop as a fixture-only vertical slice:

1. Signal Chat presents one typed command after a committed fixture finding.
2. Four deterministic local responsibilities contribute evidence, draft, review,
   and coordination results under one fixed release identifier.
3. The control plane constructs a closed proposal manifest, canonicalizes it with
   RFC 8785, and binds an approval request to its SHA-256 revision identity.
4. PostgreSQL stores proposals, revisions, requests, and decisions as forced-RLS,
   append-only records reachable by the identity role only through narrow
   security-definer functions.
5. Only the current owner may prepare or decide. Approval, rejection, and edit
   requests are exact immutable decisions with a 24-hour expiry and fresh live
   session, membership, site, and recovery-generation checks.
6. A decision authorizes only acceptance or closure of the local draft. It emits
   no outbox event and grants no GitHub, provider, merge, deployment, or customer
   site authority.

The database independently reconstructs the expected fixture manifest and rejects
caller content, canonical bytes, or digest that do not match current evidence.
The API and dashboard use strict versioned schemas and same-origin tenant-CSRF
mutations.

## Alternatives

- **Call a model and approve its prose.** Rejected because the first authority
  boundary needs an exact reproducible candidate and model evaluation does not yet
  exist.
- **Create a GitHub pull request after approval.** Deferred because GitHub App
  binding, repository qualification, build evidence, independent review, and
  recovery are separate gates.
- **Store only mutable proposal status.** Rejected because it would erase which
  exact revision a person saw and decided.
- **Treat the fixture as customer SEO evidence.** Rejected. The configured origin
  is never read and the UI states that limitation.

## Consequences

Users can now test the central supervision shape from chat command to inspectable
proposal to exact human decision. The records remain useful when a future
repository candidate is added because revision identity and authority are already
separate from provider I/O.

This does not implement a model-backed specialist workforce, long-running
planning workflow, arbitrary chat, customer evidence, Telegram, GitHub delivery,
deployment observation, live verification, or undo of external state. The local
recovery action is simply discarding a draft because no external state changed.

## Verification

- Real PostgreSQL tests cover current evidence, owner gating, RFC 8785 identity,
  idempotency, concurrency, stale revision denial, conflicting decisions, direct
  privilege denial, and immutable records.
- API tests cover exact prepare/read/decision contracts, all closed error classes,
  malformed bodies, same-origin proof, and unconfigured failure.
- Dashboard tests cover strict server-only reads, tenant-CSRF mutations, malformed
  projections, chat preparation, exact revision display, and all three decision
  controls.
- The disposable browser journey proves the full visible flow with synthetic data
  and confirms no GitHub or customer-origin operation occurs.
