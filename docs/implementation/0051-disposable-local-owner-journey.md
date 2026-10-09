# Slice 0051: Disposable Local Owner Journey

- Status: Implemented and locally qualified; production deployment unavailable
- Date: 2026-09-12
- Milestone: M1/M2 partial
- Specification: Revision 3.2 sections 3, 4, 8, 9, 12, 16, 25, 31, 32, and 33
- Decision: [ADR-0051](../adr/0051-compose-a-disposable-local-owner-journey.md)
- Runbook: [Local pilot](../runbooks/local-pilot.md)

## Scope

This slice composes the already implemented existing-user identity and owner site
onboarding boundaries into one runnable browser journey on real disposable
dependencies. It makes the first meaningful product path directly inspectable
without claiming a production deployment or inventing customer SEO data.

## Implemented Journey

```text
npm run pilot
  -> invocation-owned PostgreSQL + migrations
  -> invocation-owned OpenBao + scoped login secrets
  -> invocation-owned Keycloak + fixed synthetic owner
  -> real FastAPI and Next.js processes on loopback
  -> OIDC sign-in
  -> active organization selection
  -> owner creates and selects one unverified HTTPS site
  -> dashboard renders the current server-owned site state
```

| Boundary | Implemented behavior |
| --- | --- |
| Composition | One command starts, checks, and stops the real local service graph |
| State | Only one synthetic owner, tenant, and membership are seeded; sites are created through the product route |
| Identity | Public PKCE client, exact callback, no registration, direct grant, implicit grant, or service account |
| Session | Fresh `auth_time`, hash-only server sessions, real organization switch, and real PostgreSQL authority |
| Browser | Exact loopback-only HTTP exception with separate local cookie names; production remains `__Host-*; Secure` |
| Onboarding | Real owner-only API/BFF/database transaction creates and selects an `onboarding`/`unverified` site |
| Shutdown | SIGINT/SIGTERM stop both app processes, release ports, and remove invocation-owned containers and data |

## Integration Corrections

The first real browser run found five cross-layer defects that isolated suites had
not exposed:

1. Loopback HTTP could not retain production `Secure` cookies.
2. The dashboard relay rejected the disposable loopback Keycloak origin.
3. Content Security Policy blocked the exact Keycloak authorization form action.
4. generated cookie deletion dates did not match the dashboard's strict contract.
5. the session reader still used a parallel hard-coded production cookie name.

The journey also confirmed that current Keycloak requires its `basic` client scope
to emit the signed `auth_time` needed by Signal session issuance. The protocol now
requests `max_age=0`, and the real-provider lab asserts a session-ready token.

## Verification

Run the focused checks:

```sh
.venv/bin/python -m pytest tests/tooling/test_local_pilot.py tests/tooling/test_keycloak_lab.py tests/api/test_browser_security.py tests/identity/test_oidc_protocol.py -q
npm --workspace @signal/dashboard test
npm --workspace @signal/dashboard run typecheck
.venv/bin/python scripts/keycloak_lab.py
```

Run `npm run pilot`, complete the steps in the runbook, then stop it with Ctrl-C.
The command must exit zero, release ports 3000 and 8000, and leave no
invocation-owned PostgreSQL, OpenBao, or Keycloak containers. Full repository,
dashboard build, Python regression, real PostgreSQL, documentation, formatting,
and staged secret checks are recorded in
[0051 evidence](../evidence/0051-disposable-local-owner-journey.json).

## Explicit Limits

- This is a local qualification composition, not production deployment evidence.
- The identity, organization, and any entered site are disposable synthetic data.
- The pilot has no customer credentials, GSC property, GitHub repository, Telegram
  pairing, analytics, specialist agents, approval, PR, deployment, or undo flow.
- A site remains unverified because the pilot cannot publish a proof on its public
  origin. Do not claim or bypass ownership.
- Production cookies, HTTPS, identity hosting, controlled DNS resolution,
  supervision, monitoring, backups, and restore qualification remain separate.
- Production and external writes remain disabled.

## Next Safe Dependency

Use this runnable owner path as the browser qualification harness while adding the
first real read-only resource binding: GSC OAuth attempt state, OpenBao-backed token
reference, exact owner property selection, revocation, and truthful connector
health. Production deployment remains gated separately.
