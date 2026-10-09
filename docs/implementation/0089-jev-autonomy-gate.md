# Slice 0089: Deterministic-First Jev Autonomy Gate

Status: **INTERNAL GATE FOUNDATION; NO AUTONOMOUS DISPATCH OR PRODUCTION WRITE**.

Security-critical. Migration 0050 adds immutable forced-RLS gate outcomes,
site-wide budget preflight, and an atomic finalizer. `AutonomyGate` fetches the
current recovery generation independently. A0/A1/A2 work types must match their
approval class; A4/A5 never pass. Sensitive subjects, ungrounded claims, and
an always-ask restriction go straight to an exact owner-review outcome without
calling Jev. Current grant, exact signed recipe release, excluded path, weekly
volume, and spend are checked before the decision call.

For a deterministically eligible candidate, the existing 0064 service records
a typed Jev recommendation and risk class, or a labelled fallback. The finalizer then rechecks
current grant and recipe authority and the stored recommendation hash,
confidence, and grant threshold. It serializes an 0088 budget reservation and
an immutable final gate record. Only a current primary Jev `ship` above the
threshold with low or moderate typed risk can produce `ship`; every fallback or
high-risk classification is owner review or rejection.
Concurrent revocation or cap consumption after preflight closes the gate.
Retries return historical evidence only, never a fresh authorization.

## Verification

Run the commands in [0089 evidence](../evidence/0089-jev-autonomy-gate.json).
The PostgreSQL suite uses real PostgreSQL 17.11 and non-owner roles for
positive, negative, race, failure, immutability, and forced-RLS cases. The
existing real OpenBao, journal restore, network, Temporal, and container labs
remain the regression gates. Jev's provider parser and typed client were
qualified in 0064; live TypeSafe success is `NOT_EXECUTED` here.

## Limits

This internal gate takes a validated candidate, but no production sealed-work
adapter yet proves that every supplied candidate field came from the same
authoritative proposal revision. No external writer consumes the `ship` result.
The future dispatch path must enforce whole-job impact and publication windows,
bind exact revision and resource identities, obtain fresh recovery authority,
and use a durable external-write intent before any I/O. A budget reservation is
not an execution permit. Production Jev/OpenBao/journal credentials and live
success are unqualified; no unattended PR or publishing capability is claimed.
