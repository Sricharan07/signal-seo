# Slice 0055: Collision-Safe Local Pilot Port

- Status: Implemented and real-stack-qualified locally
- Date: 2026-09-13
- Scope: Disposable local pilot operability only
- Depends on: [Slice 0054](0054-durable-fixture-finding.md)

## Scope

The one-command local pilot now prefers dashboard port `3000` and selects port
`3001` when an unrelated process already owns `3000`. It never stops or modifies
the other process. An optional `SIGNAL_LOCAL_PILOT_DASHBOARD_PORT` override can
require either exact allowlisted port.

The change is deliberately narrower than arbitrary port configuration. The
synthetic Keycloak client registers the two exact callback URIs, and the API's
browser security boundary receives only the origin selected for that invocation.
No wildcard redirect, browser origin, production authority, or external write is
introduced.

## Implemented Behavior

| Condition | Result |
| --- | --- |
| Ports `3000` and `8000` free | Pilot uses `http://localhost:3000` |
| Port `3000` occupied and `3001` free | Pilot uses `http://localhost:3001` and leaves the existing listener untouched |
| Explicit override is `3000` or `3001` | Pilot requires that exact dashboard port |
| Override is absent from the allowlist | Startup fails before provider creation |
| API port `8000` occupied | Startup fails before provider creation |
| Both dashboard ports occupied | Startup fails without stopping either listener |

The selected origin drives the dashboard process, readiness request, printed URL,
OIDC redirect URI, and the API's exact allowed-origin set.

## Verification

Run:

```sh
.venv/bin/pytest -q tests/tooling/test_local_pilot.py
.venv/bin/ruff check scripts/local_pilot.py tests/tooling/test_local_pilot.py
.venv/bin/ruff format --check scripts/local_pilot.py tests/tooling/test_local_pilot.py
npm test
```

Focused tests cover the default, automatic fallback, exact valid overrides,
occupied API port, both dashboard ports occupied, and rejection of empty,
out-of-range, and nonnumeric overrides. Real-stack browser
qualification runs the existing sign-in, onboarding, audit, and fixture-finding
journey on port `3001` while an unrelated application remains on port `3000`.
Exact results and source hashes are recorded in
[0055 evidence](../evidence/0055-collision-safe-local-pilot-port.json).

## Explicit Limits

- This changes only the disposable development pilot. It does not configure a
  deployment, public callback, customer identity provider, or production port.
- Port selection remains vulnerable to the ordinary bind race between preflight
  and server startup; startup still fails closed if another process wins it.
- Only `3000` and `3001` are supported. More ports require an explicit reviewed
  redirect registration rather than a wildcard.
- All customer-origin reads, connectors, approvals, GitHub operations, deployment
  observation, recovery, and production writes retain their existing state.
