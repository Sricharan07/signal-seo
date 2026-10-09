# ADR-0157: Standing-Scoped PageSpeed Weekly Execution

Status: Accepted for slice 0144; local qualification recorded separately.
Classification: security-critical.

## Decision

The owner's 0144 clarification permits read-only PSI research on the granted
site's own public pages under existing `research_audit` standing grants. This
does not create a work type, owner session, identity, egress profile or publishing
permission. The existing current-owner PSI path remains unchanged.

0135 admits the bounded sample plan and reserves weekly volume before I/O.
Its opaque handle is valid only for `pagespeed_refresh`, that site, cycle and
recovery generation. Exact sample URL, strategy and operation checks supplement
the unchanged 0128 profile, verified-origin rules, five-page weekly selection,
four-request daily cap, robots admission and one-shot outcome records. A `psi:`
lease label distinguishes workloads without impersonating an owner; lease
identity shape, duration and traffic limits are unchanged.

Revocation, expiry, pause, scope, owner epochs, excluded paths and release status
are rechecked before credentials and dispatch. Completion can preserve an
already attempted outcome, but cannot authorize another request. No-grant and
exhausted-cap cases refuse. Replay never redispatches an unknown intent or
releases its reservation. Keyless observation remains explicitly labelled;
failure to read a configured key never falls back to keyless mode.

Read-only A0 admission does not add a Jev shipping decision. Existing Jev and
journaled external-write gates remain unchanged and can only reduce authority.
No model can create or enlarge the grant or workload handle.

## Consequences

Migration 0092 adds narrow workflow-only ports using 0135's existing
migration-time copy mechanism. Identity, API and scheduler roles cannot exercise
these ports. No consistency refactor of historical copies is included.
See [0144](../implementation/0144-loop-wiring.md) for evidence and live-run inputs.
