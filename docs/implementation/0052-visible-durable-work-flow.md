# Slice 0052: Visible Durable Work Flow

- Status: Implemented and locally qualified; production crawl unavailable
- Date: 2026-09-12
- Milestone: M1/M2/M4 partial
- Specification: Revision 3.2 sections 3, 4, 8, 9, 12, 18, 25, 31, 32, and 33
- Decision: [ADR-0052](../adr/0052-prioritize-a-user-testable-durable-work-flow.md)
- Runbook: [Local pilot](../runbooks/local-pilot.md)

## Scope

This slice turns the existing durable command and workflow foundations into the
first user-testable Work-page operation. It intentionally uses a synthetic local
executor so the product can prove orchestration without claiming a public crawl or
weakening the unfinished crawler gates.

## Implemented Journey

```text
npm run pilot
  -> sign in and select the synthetic organization
  -> create/select one disposable unverified site
  -> open Work and select Start audit
  -> exact same-origin BFF requests tenant CSRF
  -> API accepts current-site snapshot intent
  -> PostgreSQL commits command, event, and outbox atomically
  -> outbox worker admits and starts CrawlSite in real local Temporal
  -> workflow worker executes the no-network local activity
  -> terminal activity commits the bounded manifest projection
  -> Work refreshes from current-user PostgreSQL truth and shows Completed
```

| Boundary | Implemented behavior |
| --- | --- |
| Latest work | Migration `0029` exposes only the newest current user's snapshot command after complete live-authority recheck |
| API | `GET /v1/sites/{site_id}/commands/latest` shares the exact bounded status contract and closed errors |
| Dashboard BFF | Server-only latest read and same-origin POST use one exact tenant cookie, tenant CSRF, no redirects, bounded bodies, and strict schemas |
| Work UI | Selected site, authority, four durable stages, current state, manifest counts, and receipt update from committed records |
| Temporal | The pilot runs the real SDK-managed server, outbox publisher, `CrawlSite` worker, and PostgreSQL terminal activity |
| Local executor | Deterministic one-URL manifest after a visible delay; no DNS, HTTP, customer credential, or configured-origin access |
| Browser state | Per-invocation local cookie namespace prevents discarded pilot sessions from contaminating a fresh run |
| Production | The Work action is disabled unless the explicit local-pilot flag is active; production cookies and write gates are unchanged |

## Verification

Run the focused checks:

```sh
.venv/bin/python -m pytest tests/api/test_human_commands_http.py \
  tests/api/test_authentication.py tests/tooling/test_local_pilot.py -q
npm --workspace @signal/dashboard test
npm --workspace @signal/dashboard run typecheck
.venv/bin/python scripts/run-database-tests.py
```

Run `npm run pilot` and follow the runbook. The final Work screen must show all
four stages complete, a `succeeded` command projection, complete coverage, one
synthetic discovered URL, one terminal URL, and one bounded manifest identity.
The exact aggregate results and source hashes are in
[0052 evidence](../evidence/0052-visible-durable-work-flow.json).

## Failure Behavior

- Missing, malformed, duplicate, cross-origin, or unauthorized browser authority
  is rejected before work starts.
- A wrong site, stale session, reduced membership, or another actor's command is
  not returned by the latest-work boundary.
- Invalid or oversized API data renders an unavailable state and cannot create a
  success receipt.
- Workflow failures and cancellation remain closed terminal outcomes without a
  fabricated manifest.
- Temporal, API, dashboard, or database startup failure keeps the pilot unready
  and cleanup remains invocation-bound.

## Explicit Limits

- The manifest is synthetic local workflow evidence, not a crawl result for the
  configured origin.
- This is not restart or backup durability evidence because pilot state is
  intentionally destroyed on shutdown.
- No GSC data, competitor research, specialist agent, proposal, approval, GitHub
  operation, deployment observation, live verification, recovery, or undo is
  implemented by this slice.
- The production crawler, worker deployment, identity/resolver composition,
  monitoring, customer resources, and all external writes remain unavailable.

## Next User-Visible Slice

Use this working command/workflow/read-model spine to expose a durable evidence
and findings view, then a bounded proposal/review/approval journey. Keep real
provider binding and crawler qualification on their existing safety gates rather
than substituting the synthetic executor.
