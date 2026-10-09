# ADR-0052: Prioritize A User-Testable Durable Work Flow

- Status: Accepted
- Date: 2026-09-12
- Scope: Core V1 implementation order and disposable local workflow composition

## Context

Signal had durable command acceptance, outbox delivery, Temporal admission and
start, a deterministic `CrawlSite` workflow, terminal PostgreSQL projection, and a
browser owner journey. These capabilities were qualified separately. A user could
not start work from the dashboard and watch it reach a durable terminal state, so
the product still felt static even though much of the execution path existed.

The owner asked to complete visible, testable core flows before returning to
broader hardening. That changes implementation order, not the Revision 3.2 safety
or release gates. Connecting the unfinished production crawler merely to make a
demo would bypass origin, frontier, byte, egress, artifact, and deployment gates.

## Decision

Make the first Work-page operation an explicitly local, disposable vertical flow:

```text
current owner/site -> same-origin dashboard action -> authenticated command
  -> PostgreSQL command + outbox -> Temporal admission and workflow
  -> synthetic no-network executor -> PostgreSQL terminal projection
  -> current-user latest-work read -> visible completed receipt
```

The local pilot starts the real Temporal development server, real workflow worker,
real outbox publisher, and the existing real PostgreSQL, OpenBao, Keycloak, API,
and dashboard graph. The executor returns one deterministic synthetic manifest
after a short delay and cannot contact the configured site. The Work control is
enabled only when `SIGNAL_LOCAL_PILOT=1`; every other composition states that the
production executor is unavailable.

Add one narrow latest-snapshot read function and API route. They recheck the same
live session, recovery generation, tenant, selected site, membership, and actor as
the existing exact-command status route, then return only that actor's newest
`api.site.snapshot` projection. Keep direct table reads unavailable to the
identity role.

Namespace local pilot cookies with a new random generation on every run so a
discarded pilot's browser state cannot be confused with the next invocation.
Production cookie names and attributes remain unchanged.

## Consequences

- A user can now exercise and inspect one complete durable operation through the
  product UI instead of reading logs or tests.
- The browser proves real cross-process orchestration and terminal persistence,
  while the page and manifest clearly identify synthetic local evidence.
- Restart durability, public-site crawling, provider data, agents, approvals,
  repository changes, deployment, verification, and undo remain unproved.
- The implementation sequence now favors coherent user-visible vertical slices,
  but no production authority or Core V1 release gate is relaxed.

## Alternatives Rejected

- **Continue hardening only isolated foundations:** preserves quality but leaves
  users unable to evaluate a working product flow.
- **Register the incomplete network crawler:** would turn a demonstration goal
  into an unsafe shortcut around unresolved production gates.
- **Return fabricated analytics or crawl rows:** would make the dashboard look
  active without evidence and violate the product truth boundary.
- **Read the latest command directly in the dashboard:** would bypass current
  server-side authority and expose tenant records to the presentation layer.
- **Reuse fixed local cookie names:** lets expired disposable sessions shadow a
  fresh run and produces misleading browser failures.

## Verification

Positive, negative, and failure tests cover the latest current-user projection,
wrong-site and absent results, exact function privileges, API authorization,
strict dashboard response parsing, same-origin mutation, generated cookie names,
local-only control enablement, and migration rollback. Real PostgreSQL 17.11
qualification and a browser walkthrough prove the full local path reaches a
terminal manifest through the real Temporal server and worker with no browser
warnings or errors.
