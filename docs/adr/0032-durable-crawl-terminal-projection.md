# ADR-0032: Project Crawl Completion Before Closing The Workflow

- Status: Accepted
- Date: 2026-09-09
- Owners: Workflow orchestration, command service, and crawl execution

## Context

The command consumer can durably admit a snapshot command, start exactly one
Temporal execution, record its first run, and acknowledge outbox delivery. Until
now, the registered `CrawlSite` implementation existed only in tests and command
status stopped at `processing`. A real workflow state machine needs deterministic
retry and cancellation behavior, but it must not claim a crawl succeeded unless a
bounded result reference and terminal business event are committed atomically.

Temporal history is durable orchestration state, not the user-facing source of
truth. PostgreSQL is the current command projection, while future artifact storage
will own crawl manifests and observations. The workflow also races the consumer:
an activity can finish before first-run evidence reaches PostgreSQL. Completion
therefore needs retryable projection rather than a direct table update or an
assumption that `processing` already exists.

At-least-once outbox delivery creates a second race. If acknowledgement is lost
after a fast workflow finishes, redelivery repeats admission and start recording.
Both earlier-stage receipts must remain reproducible after the current projection
has advanced to a terminal state.

## Decision

Implement one deterministic `CrawlSite` workflow with serialization-safe,
versioned dataclasses. Bind each input to the deterministic workflow identity and
pin `scope_version` and `crawl_policy_version`. Keep all provider and database I/O
in activities behind narrow protocols.

Run crawl execution as one heartbeat-capable activity with fixed timeouts and a
closed retry policy. Retry only errors classified as `crawl_retryable`, up to three
attempts. Treat rejected requests, unexpected executor failures, and invalid or
version-mismatched results as non-retryable. Store only fixed sanitized Temporal
failure messages and types; executor exception text never enters history.

Represent a successful activity result as a bounded crawl-manifest reference:
UUID, SHA-256, complete/partial coverage, discovered and terminal counts, and the
two input versions. This reference does not assert that an artifact service or
network crawler exists. Those remain separate qualification work.

Project `succeeded`, `failed`, or `cancelled` through one function-only
`signal_workflow` PostgreSQL operation. In one transaction it locks the exact
running workflow and command, advances both projections, and appends command event
four. Success requires the manifest reference; failure and cancellation require
their exact closed reason and no result. Exact retries return the original event;
different terminal evidence is a conflict. Running work may record its outcome
after tenant or site suspension, because suspension blocks new authority rather
than erasing an already accepted outcome.

Retry the terminal projection independently for up to eight attempts. A workflow
does not return success, surface its original activity failure, or complete as
cancelled until the terminal projection activity succeeds. Cooperative workflow
cancellation waits for activity cancellation, records `crawl_cancelled`, then
rethrows cancellation. Temporal's Python SDK reports the cancelled activity as an
`ActivityError`, so classification also uses the deterministic workflow
cancellation marker.

Migration 0019 also makes the original workflow-start receipt stable after the
projection reaches any terminal state. It returns event three's immutable
`running` receipt while leaving event four and the terminal workflow reference
unchanged. This mirrors the admission-receipt correction in migration 0018 and
allows acknowledgement-loss redelivery to converge.

Expose terminal command state through the existing authorized status route. The
HTTP contract returns bounded manifest metadata on success or one closed reason on
failure/cancellation. It never returns crawl content, provider failures, Temporal
history, or database internals.

## Alternatives

- Return from Temporal and update PostgreSQL asynchronously. Rejected because a
  completed workflow could remain indefinitely `processing` with no durable repair
  obligation.
- Put crawl observations in Temporal history. Rejected because history is not the
  evidence store and large or sensitive payloads would make replay and retention
  unsafe.
- Let the workflow write PostgreSQL directly. Rejected because deterministic
  workflow code cannot perform provider I/O and must remain replayable.
- Retry every crawl failure. Rejected because policy rejection, malformed output,
  and unexpected implementation errors are not proven transient and may amplify
  unsafe behavior.
- Recheck active tenant/site state at completion. Rejected because an accepted
  in-flight operation still needs a truthful terminal outcome after authority is
  reduced.
- Return the latest projection when start recording is redelivered. Rejected
  because a later state cannot mutate the immutable receipt contract required by
  the earlier delivery stage.
- Implement the HTTP crawler in the same slice. Deferred because redirect scope,
  DNS rebinding, private-network exclusion, normalization, artifact persistence,
  and resource limits require their own real-network qualification.

## Consequences

- Accepted snapshot commands can now reach a durable terminal state through the
  production workflow definition and terminal activity boundary.
- Temporal history and PostgreSQL command history agree on success, failure, and
  cancellation in the qualified scenarios.
- Projection lag is retried and visible as an open workflow rather than reported
  as success.
- Earlier admission and start receipts remain stable across fast completion and
  outbox acknowledgement loss.
- The command API can report terminal metadata without exposing artifact bodies or
  untrusted failure text.
- A production worker process, worker build-ID rollout, HTTP crawler, artifact
  persistence, crawl scope enforcement, deployment, health, and alerting are still
  absent. The manifest reference is a contract, not proof those dependencies exist.

## Verification

Unit tests cover closed contracts, count and version bounds, heartbeat forwarding,
retryable/non-retryable error classification, sanitization, invalid adapter output,
terminal-store failures, and API projection coherence. A real Temporal development
server verifies retry, failure, cancellation, and replay of all resulting histories.

Real PostgreSQL tests cover success, failure, cancellation, exact and conflicting
retry, concurrent projection, post-suspension completion, stable start receipt
after completion, strict SQL input, function-only privileges, immutable state,
event-insert rollback, migration rollback, and authorized status reads. The joint
PostgreSQL/Temporal lab runs the production workflow with a synthetic injected
executor and proves fast completion plus outbox acknowledgement-loss recovery
converge on one execution and four events.
