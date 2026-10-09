# ADR-0058: Bind Luna drafts to verified page evidence

## Status

Accepted on 2026-09-20.

## Context

Signal could observe one owner-verified homepage and could separately prove a
bounded Luna proposal flow over synthetic fixture evidence. Leaving those paths
separate meant the visible product still could not turn real customer evidence
into a reviewable optimization. Allowing a model to browse the site, select a
target, or directly prepare a provider operation would combine reasoning,
authority, and external I/O in one unauditable step.

## Decision

Introduce one evidence-bound verified-homepage proposal path:

1. Select only the current open `verified_origin` missing-description finding and
   its exact latest successful page observation.
2. Build a canonical model packet containing identifiers, HTTPS page URL, title,
   first H1, and the explicit absent description. Treat all page values as
   untrusted data rather than instructions.
3. Commit the model-run and call identities before provider I/O. Fix the release
   to `gpt-5.6-luna`, strict structured output, no tools, `store=false`, bounded
   timeout and output, and a hashed prompt release.
4. Record sanitized known failures, block blind retry after unknown outcomes, and
   cap attempts for one evidence identity.
5. After adapter validation, recheck live owner authority and current evidence in
   PostgreSQL. Reconstruct and seal the exact proposal manifest, provider receipt,
   usage, and hashes into an immutable revision and approval request.
6. Keep approval semantically limited to accepting, rejecting, or requesting
   edits to the draft. Do not dispatch repository or provider work.

## Alternatives

- **Keep drafting from the fixed fixture.** Rejected because it cannot propose an
  optimization for the owner's site.
- **Send the full customer HTML to the model.** Rejected because the bounded task
  needs only URL, title, and H1 and should minimize customer data exposure.
- **Let the model browse or call GitHub.** Rejected because evidence acquisition,
  reasoning, deterministic policy, approval, and execution require separate
  identities and controls.
- **Create a pull request immediately after drafting.** Rejected because no
  repository binding, candidate build, independent review, or exact approval for
  an external write exists yet.

## Consequences

An owner can now move from a real verified-page finding to an inspectable Luna
draft and exact human decision. Every model fact and revision remains attributable
and replay-safe. The product still has no outcome until a separately qualified
repository candidate, GitHub PR, delivery observation, and live verification path
are implemented.

## Verification

- Real PostgreSQL tests cover evidence admission, exact replay, durable completion,
  missing page facts, tamper resistance, privilege boundaries, and migration
  behavior.
- Adapter and API tests cover exact packets, prompt release, structured output,
  provider failure mapping, not-ready state, and no-write manifests.
- Dashboard tests cover the verified route, strict HTTPS target validation,
  customer-visible Chat and Approvals state, and removal of fixture controls from
  normal product views.
