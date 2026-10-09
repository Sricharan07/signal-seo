# ADR-0153: Private Health Observations and Existing-Channel Alerts

Status: Accepted for local security-critical implementation; live composition unqualified.
Date: 2026-10-03.

## Context

H1 requires honest scheduled operational states and owner escalation without
creating publishing authority, new outbound destinations or public operational
data. Binding presence is not evidence of usable credentials. Missing probes,
budget ledgers and optional providers must stay unknown. Email 0093 already
enforces same-person verified opt-in and dispatch-time revalidation; chat-report
delivery 0133 is a separate pending integration.

## Decision

Host a bounded read-only monitor in the existing weekly runtime. Record complete
closed-vocabulary snapshots in immutable, function-only, forced-RLS site tables.
Serialize episode deduplication and rate limits with the scoped site lock. Queue
fixed owner notifications transactionally through the existing email outbox,
requiring current verified preference, membership/site authority and recovery
generation. Preserve the existing sender's independent dispatch checks and caps.

Expose an identity-worker-only durable alert-event registration point for existing
verified-recipient chat outboxes, not another provider sender. Operator-level
failures are logged without exception text or credentials, with eligible-owner
email when configured and storage is available. Monitoring never changes a cap,
authority, quarantine or write outcome.

The authenticated owner dashboard receives only check/state/reason/time and
fixed remediation. Stale evidence becomes unknown. The schema-hidden private
`/health` admits literal loopback without forwarded headers and returns success
only for a complete, fresh, persisted all-ok snapshot. Public Caddy denial remains
unchanged. No egress profile or dependency is added.

## Alternatives

- Probe providers or create new alert transports: rejected because they enlarge
  outbound behavior, spend and recipient authority instead of reusing controls.
- Treat configured bindings or absent measurements as healthy: rejected because
  this simulates readiness and hides missing evidence.
- Store only latest state or process-local dedup: rejected because restarts lose
  episodes and rate limits, and suppressed transitions can be lost permanently.
- Publish unauthenticated operational health: rejected because the private-only
  boundary is explicit and public routing must remain 404.

## Consequences

Unavailable ledgers/probes remain visible unknown; no new service fills them with
synthetic readiness. Alerts can be delayed by bounded caps, but deferred episodes
remain eligible. Database failure prevents durable email and is logged, not
reported delivered. H1 requires explicit host composition and 0133 registration
before live monitoring/chat claims. Existing safety and production gates remain.

## Verification

See [0142 implementation](../implementation/0142-health-monitoring.md) and its
[evidence](../evidence/0142-health-monitoring.json) for real PostgreSQL/Temporal,
probe failures, dedup/caps, verified-recipient and tenant/role negatives, stale
projections, unchanged egress/public routing, full regression gates and live
NOT_EXECUTED exclusions. Migration 0089 follows 0088; no existing migration or
accepted specification is modified.
