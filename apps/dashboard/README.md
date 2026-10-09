# Signal Dashboard

Signal's Next.js owner dashboard follows the approved Direction C design.
Home reads committed activity and decisions; the Inbox holds exact revisions,
articles and business-fact reviews; Activity distinguishes review, PR, deployment,
live verification and measurement. Content includes articles, Strategy, keyword
ideas and Business facts. Results includes search evidence, AI answers and Pages.
Connections and Autonomy expose current owner-authorized controls. Readiness-only
destinations stay reachable by URL. See [DESIGN.md](../../DESIGN.md).

Pages load current identity, organization, site and domain projections from the
Signal API. Missing gateways and malformed or unavailable evidence remain explicit;
no fixture metrics, connection status or shipping state is inferred. The public
capability inventory describes composed internal gateways, not provider health,
a particular site's authorization, or production readiness.

Same-origin route handlers mediate owner decisions, connector commands, site
proof, team invitations, weekly pause, visibility schedules and Ask Signal.
The IndexNow key action requires browser proof and current-owner fresh MFA,
then the existing reviewed recipe and exact build seal the key-file revision
into the Candidate Inbox. It never directly writes or publishes the key file.
Absent key/build composition stays unavailable. WordPress initial binding remains
operator-provisioned; owner selections use current bindings and articles.
Notion remains explicitly unavailable.

## Development

Use Node.js 22 and the repository lockfile:

```sh
npm ci --ignore-scripts
npm run pilot
```

The disposable [local pilot](../../docs/runbooks/local-pilot.md) serves
`http://localhost:3000`. Its synthetic audit does not contact a customer site.
For the unconfigured shell, run the API and dashboard separately:

```sh
PYTHONPATH=apps/api/src:services/control_plane/src:scripts \
  .venv/bin/uvicorn signal_api.main:app \
  --host 127.0.0.1 --port 8000 --no-access-log
npm --workspace @signal/dashboard run dev -- --hostname 127.0.0.1 --port 3000
```

The default API has no credential-bearing gateway and reports dependencies not
ready. Dashboard environment settings alone cannot compose providers or identity.
`SIGNAL_API_BASE_URL` is a trusted server-only origin with no credentials, path,
query or fragment. `SIGNAL_DASHBOARD_ORIGIN` is the exact accepted mutation
origin; production requires HTTPS. `SIGNAL_IDENTITY_PROVIDER_ORIGIN` is an
explicit HTTPS redirect allowlist. Never use customer identity or credentials for
local experiments.

## Verification

```sh
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm test
```

Domain tests cover positive, rejected, malformed, oversized, cross-site, unavailable
and failed responses; browser proof and exact cookie handling; strict redirects;
honest states and evidence provenance. Repository design guards remain unchanged.
Provider and real PostgreSQL/Temporal/container qualification commands are in
[CONTRIBUTING.md](../../CONTRIBUTING.md) and the slice implementation records.

## Boundaries

- `lib/relay-json.ts` validates API origins, rejects unexpected cookies and
  redirects, streams with byte bounds, and cancels overflow. Every caller retains
  its domain's closed schema, content-type, tenant/site and identity checks.
  Limits and timeouts remain specific to each endpoint.
- Reads forward only the exact host-only cookie for their identity or tenant
  scope. Duplicate/malformed cookies are rejected before I/O. Server session and
  site projections reconcile the active site and monotonic session version.
- Identity login, callback and revocation are the deliberate cookie-mutating
  exception: their separately validated cookie names, attributes, lifetimes and
  provider redirects are not accepted by the JSON relay.
- Mutations require exact Origin and fetch-site proof, a current tenant cookie,
  server-fetched tenant CSRF and closed, byte-bounded commands. Owner controls
  are not authorization; the API and PostgreSQL independently resolve authority.
- Opaque credentials, authorization codes and private cookies are transient and
  never rendered or logged. Technical evidence retains exact hashes and manifests.
- Production CSP denies framing, objects, cross-origin connections and referrer
  disclosure. Next.js needs inline runtime scripts; development-only eval is not
  permitted in production.
- Missing optional runtime composition stays unavailable. Local tests do not
  certify deployment or live provider behavior. Signal never merges, deploys,
  pushes to a default branch, deletes content or edits CI. Production writes stay
  disabled; live limits belong in [current status](../../docs/implementation/status.md).
