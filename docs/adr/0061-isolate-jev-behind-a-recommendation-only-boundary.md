# ADR-0061: Isolate Jev Behind A Recommendation-Only Boundary

- Status: Accepted
- Date: 2026-09-26
- Owners: Signal decision and policy boundaries
- Related: [ADR-0060](0060-autonomous-seo-employee-direction.md), [ADR-0062](0062-record-shared-egress-before-network-io.md), Revision 4.0 sections 6 and 7, INV-026

## Context

Signal needs calibrated routine decisions for triage, classification, browser action
selection, and the later autonomy gate. A model response cannot be authority: policy
and a human standing authorization decide what is eligible before Jev is consulted.
The provider is also optional in self-hosted deployments, so an absent, rate-limited,
or invalid response must remain an explicit operating state rather than silently
disabling work or granting permission.

TypeSafe documents one HTTP endpoint, `POST /v1/systemone`, with typed Choice,
Noul, and Score questions. The request uses `jev-latest`; the response reports a
pinned release. The published API contract provides the shapes and option limits,
but leaves probability rounding tolerance and the exact response legend for
structured Score criteria unspecified.

## Decision

1. Keep domain contracts, provider I/O, credentials, persistence, and future policy
   composition separate. `DecisionService` returns a `DecisionRecommendation`; it
   accepts no executor, authorizer, connector, or mutable authority capability.
2. Use a thin fixed-origin provider adapter instead of the TypeSafe SDK. The adapter
   fixes the endpoint and model alias, delegates all public network I/O to the
   shared-egress boundary from ADR-0062, and gives Signal direct control of strict
   validation without adding an unused dependency.
3. Support typed Choice, Noul, and Score questions. Choice is limited to 255
   options and Score to 2–10 levels. Requests accept only text, JSON objects, or
   JSON arrays. A 64 KiB canonical UTF-8 request limit is a conservative preflight
   below the approximately 64k shared-token limit; provider-reported input usage is
   also rejected above 65,536 tokens. Each question is capped at 32 KiB.
4. Require exact answer ids and types. Choice and Score probabilities must contain
   exactly the submitted keys, be finite in `[0,1]`, and sum to one within
   `0.000001`; Choice must name a highest-probability known option. Confidence must
   be finite in `[0,1]`. Score must return the exact submitted legend and weighted
   score. Any mismatch makes Jev unavailable under EC-125.
5. Represent deterministic policy as a recommendation ceiling ordered
   `reject < ask_owner < ship`. Every primary or fallback result is reduced to that
   ceiling. This proves INV-026 locally and still requires the future authorizer to
   establish actual standing authority.
6. On unconfigured, unavailable, rate-limited, rejected, timed-out, or invalid Jev,
   use a labelled fallback. The bounded Luna structured classifier can only return
   `ask_owner` or `reject`; if it is unavailable, deterministic rules return
   `reject` under a reject ceiling and otherwise `ask_owner`. A fallback can never
   recommend `ship`.
7. Read the Jev key from a single-version, read-only OpenBao KV capability. Store
   every returned recommendation through one function-only workflow role into an
   immutable forced-RLS PostgreSQL record containing input and question-schema
   digests, exact answers, recommendation probabilities, confidence, threshold,
   deterministic ceiling, outcome, and fallback label.

## Isolated Assumptions

- TypeSafe's response `legend` is required to echo submitted Score criteria exactly,
  including structured JSON criteria. The request documentation permits structured
  criteria, while the response schema describes legend values as strings. Until a
  live key qualifies this behavior, a mismatch fails closed.
- JSON floating-point probability sums are accepted only within an absolute
  tolerance of `0.000001`. The official contract says they sum to one but does not
  publish a serialization tolerance.
- The canonical-byte preflight is intentionally more conservative than the token
  budget because TypeSafe does not publish a tokenizer package or exact local token
  counting contract. The provider-reported usage check is the second bound.
- The adapter does not automatically retry 429, 529, or 5xx responses. Those states
  enter the explicit fallback; retry scheduling and budgets belong to a later
  workflow slice.

## Alternatives

- **Install the TypeSafe SDK.** Rejected for this slice because the fixed endpoint
  requires one operation and Signal must own validation, response bounds, and
  fallback evidence. Reconsider only if the SDK supplies a qualified tokenizer or
  materially different signed protocol.
- **Let Jev return an authorization decision.** Rejected by INV-026. A model has no
  authority source and cannot widen deterministic policy.
- **Fail all work when Jev is absent.** Rejected by EC-124 and EC-145. Explicit
  owner escalation preserves useful operation without pretending Jev is present.
- **Allow the frontier fallback to ship.** Rejected. The fallback lacks Jev's
  calibrated decision contract and is deliberately more cautious.

## Consequences

The provider boundary and durable recommendation evidence are available for later
R1 consumers, but no standing authorization, autonomous execution, browser action,
or external write is created. Live TypeSafe behavior remains unqualified until the
owner runs the silent-key command. Any live mismatch with the isolated assumptions
requires an adapter revision and new evidence, never relaxed validation in place.

## Verification

- Positive, negative, and failure tests cover every typed question, documented
  limits, strict response validation, timeout, 429, 5xx, rejection, and unconfigured
  states.
- INV-026 tests enumerate every provider recommendation and deterministic ceiling
  and assert the returned rank never widens; the service exposes no authorize,
  execute, dispatch, merge, or deploy operation.
- A disposable PostgreSQL 17.11 lab covers migration rollback, forced RLS,
  function-only writes, scope mismatch, immutability, exact replay, conflict, and
  fallback evidence.
- A disposable TLS OpenBao 2.6.1 lab proves a single-version Jev secret and a
  read-only token that cannot mutate it.
- [Live Jev evidence](../evidence/0064-jev-live-qualification.json) remains
  `NOT_EXECUTED` without a key.
