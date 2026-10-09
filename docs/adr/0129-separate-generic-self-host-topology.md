# ADR-0129: Separate Generic Self-Host Topology

Status: Accepted for the unqualified deployment candidate. Date: 2026-10-03.

## Context

Revision 4.0 section 18/INV-032 requires owner-operated open-source installation
without a Signal service. The dedicated integration topology has qualified exact
owner/environment guards that are not a generic deployment contract.

## Decision

Keep `deploy/self-host` separate with no environment-specific identifiers, public
HTTPS only, internal administrative networks, immutable image references,
non-root read-only containers and tmpfs secret hydration. Reuse existing dashboard,
ingress and worker Dockerfiles and identity/session/OpenBao/database primitives.
Parameterize only workload credential directory/policy prefix with identical
integration defaults. Store optional keys without admitting provider execution.

## Alternatives

Generalizing integration scope would invalidate its exact qualification. Copying
runtime domain logic would fork safety fixes. Exposing private ports or connecting
an unqualified provider gateway would enlarge exposure/egress authority.

## Consequences And Verification

The candidate cannot claim integration or production certification. Owners must
supply reviewed registry digests and DNS/TLS and complete a dedicated live run.
Compose normalization and topology tests prove configuration boundaries; real
PostgreSQL/OpenBao labs prove narrower bootstrap/storage mechanics. See
[0130](../implementation/0130-self-host.md) and [runbook](../self-host.md).
