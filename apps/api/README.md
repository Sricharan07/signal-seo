# Signal API

This FastAPI process exposes truthful service status plus bounded OIDC login,
invitation acceptance, tenant selection, session lifecycle, harmless durable
snapshot commands, owner-controlled unverified site creation, and exact public-
origin challenge/verification contracts. The explicit local-pilot composition also
exposes one verified-homepage observation, one fixed internal fixture analysis,
their durable current-finding projection, and a supervised exact
verified-homepage proposal/decision flow with an optional bounded Luna drafting
adapter.
The default process has no credential-bearing gateway
or browser-security key, so those routes fail closed. It does not expose command
execution, broad customer-site crawling, provider credentials, or production writes.

Run from the repository root after installing `requirements.txt`:

```sh
PYTHONPATH=apps/api/src:services/control_plane/src \
  .venv/bin/uvicorn signal_api.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

To run the real API together with disposable PostgreSQL, OpenBao, Keycloak, and
the dashboard for the qualified local owner journey, use `npm run pilot` instead.
See the [local pilot runbook](../../docs/runbooks/local-pilot.md). That composition
uses only synthetic state and grants no production or external write authority.
It also starts a real local Temporal server, outbox publisher, workflow worker,
and no-network synthetic crawl activity so the browser can exercise one terminal
Work flow.

Available routes:

| Route | Purpose |
| --- | --- |
| `GET /health/live` | Process liveness without dependency claims |
| `GET /health/ready` | Dependency readiness; returns 503 until a real probe is configured |
| `GET /v1/capabilities` | Machine-readable enabled, internal-only, and disabled capabilities |
| `GET /v1/session/login` | Start existing-user OIDC login when a reviewed gateway is injected; otherwise 503 |
| `GET /v1/session/callback` | Consume exact callback/browser proofs and set the credential selected by immutable OIDC purpose |
| `GET /v1/invitations/verify` | Start invitation-purpose OIDC without accepting an invitation ID or bearer |
| `GET /v1/invitations/csrf` | Derive a CSRF proof from the exact short-lived invitation identity cookie |
| `POST /v1/invitations/accept` | Atomically accept a body-only invitation bearer under proof, Origin, and CSRF checks |
| `GET /v1/organizations` | List current active memberships for the exact pre-tenant identity cookie |
| `GET /v1/session/csrf` | Derive a CSRF proof bound to the pre-tenant identity cookie |
| `GET /v1/session/logout-csrf` | Derive a CSRF proof for the strongest browser session that logout will revoke |
| `GET /v1/session/tenant-csrf` | Derive a CSRF proof from the exact tenant session for site-context changes |
| `POST /v1/session/switch-tenant` | Recheck one selected membership and issue an opaque tenant session |
| `GET /v1/session` | Inspect current tenant context, version, and selected site from live server-side authority |
| `PUT /v1/session/site` | Optimistically select one currently authorized site in the exact tenant session |
| `GET /v1/sites` | List at most 100 current non-archived sites authorized for the exact tenant session |
| `POST /v1/sites` | Atomically create, grant, and select one owner-controlled unverified site |
| `POST /v1/sites/{site_id}/origin-challenges` | Issue one owner-gated 30-minute plaintext proof for the selected exact origin |
| `POST /v1/sites/{site_id}/verify-origin` | Fetch, record, and verify the exact current proof without redirects |
| `POST /v1/sites/{site_id}/commands/snapshot` | Accept harmless durable snapshot intent under exact browser proof and idempotency |
| `GET /v1/sites/{site_id}/commands/latest` | Reauthorize and inspect the current actor's newest accepted snapshot projection |
| `GET /v1/sites/{site_id}/commands/{command_id}` | Reauthorize and inspect accepted, admitted, processing, or terminal progress |
| `POST /v1/sites/{site_id}/analysis/local-fixture` | After a completed audit, persist one deterministic finding from the fixed internal HTML fixture |
| `POST /v1/sites/{site_id}/analysis/verified-homepage` | Owner-only: observe the exact verified homepage through the bounded reader and persist immutable metadata evidence |
| `GET /v1/sites/{site_id}/observations/homepage/latest` | Reauthorize and return the latest successful verified-homepage observation |
| `GET /v1/sites/{site_id}/findings` | Reauthorize and list at most 50 current findings with latest immutable evidence provenance |
| `POST /v1/sites/{site_id}/proposals/local-fixture` | Owner-only: seal the current fixture finding into one RFC 8785 revision and request exact local approval |
| `POST /v1/sites/{site_id}/proposals/model-fixture` | Owner-only local pilot: durably invoke the fixed Luna metadata role, seal its exact model provenance, and request approval without external dispatch |
| `POST /v1/sites/{site_id}/proposals/verified-homepage` | Owner-only local pilot: draft from the exact verified-homepage observation, seal model/evidence provenance, and request exact approval without external dispatch |
| `GET /v1/sites/{site_id}/proposals` | Reauthorize and list at most 50 exact current-site local proposal projections |
| `POST /v1/sites/{site_id}/approval-requests/{approval_request_id}/decision` | Owner-only: record one approve, reject, or request-edits decision bound to the exact revision SHA-256 |
| `POST /v1/session/logout` | Revoke the parent identity session, audit once, and clear all auth cookies |

Interactive documentation is off by default and cannot be enabled in production.
Set `SIGNAL_EXPOSE_API_DOCS=true` only in a nonproduction environment. All
responses receive a bounded correlation ID, no-store and content security headers,
and stable safe errors. No CORS policy is enabled.

The application factory accepts a readiness probe for composition, but the default
fails closed. A later deployment slice must connect an authoritative dependency
probe without placing database credentials in browser-visible configuration.

The application contains browser-mutation dependencies that validate an exact
trusted Origin, reject ambiguous duplicate security inputs, and require a
credential-bound HMAC CSRF proof carried with the pre-tenant identity, tenant, or
invitation-only cookie. Tenant selection uses the identity-bound form and then
rechecks server-side membership. Invitation acceptance uses only its dedicated
short-lived proof and rechecks the invitation atomically in PostgreSQL. A browser
proof alone is not identity or site authorization.

The control plane also has a durable hash-only, purpose-bound OIDC login-attempt
store and hash-only global/tenant session issuance. PKCE verifiers have a separately
qualified OpenBao boundary, login reads the current recovery generation through a
separate exact-path OpenBao credential, and those components have a fail-closed
internal composition. Successful identity-session issuance now commits a narrow
immutable platform event in the same database transaction, and failures after a
legitimate attempt is consumed append a sanitized proof-gated event. All are reported as
internal-only by `/v1/capabilities`. The login start and callback now have an HTTP
adapter, but the default process does not construct its credential-bearing gateway.
When injected, a successful existing-user callback first receives a pre-tenant
identity cookie. The membership routes can then exchange it for one tenant cookie
without treating a browser-supplied tenant ID as authority. Current-session
inspection and logout recheck or revoke server-side state; logout also supports a
pre-tenant identity proof and does not depend on recovery-authority availability.
A dedicated logout-CSRF read selects the tenant cookie before the pre-tenant
identity cookie so the subsequent mutation proves possession of the same session
level it will revoke.
A separate site-directory route revalidates the tenant session and current site
memberships through a hash-bound PostgreSQL function. Site selection is a distinct
tenant-CSRF-protected operation: it accepts a site UUID and expected session
version, never browser-owned tenant/user scope, and commits one server-owned
context transition with audit evidence. Site-scoped snapshot operations require
that selected context and still repeat current authority checks.
Site creation uses the same tenant-bound browser proof but additionally requires a
current owner role. It accepts one canonical HTTPS DNS origin, bounded name, IANA
timezone, reporting currency, expected session version, and UUIDv4 idempotency
key. One PostgreSQL transaction creates the explicitly unverified site and narrow
owner grant, selects it, extends the active-site event chain, and appends a
request-hashed onboarding event. Exact replay returns the original receipt;
conflicting input, stale context, duplicate current origin, and the 100-site bound
fail closed. This is configuration intake, not origin ownership verification or
crawl/provider authority.
Origin verification is a separate owner-only boundary. It prepares under current
session/site authority, closes the database transaction, performs one bounded
public-address-pinned plaintext fetch through an injected controlled resolver, and
rechecks authority before recording the outcome. Exact success promotes only the
configured origin, records a 30-day recheck and closed revocation conditions, and
claims that origin against cross-tenant conflicts. Failed observations remain
durable. The default API does not inject the credential-bearing browser gateway or
resolver, so both routes fail unavailable and grant no crawl/provider authority.
A complete immutable login audit, recovery rotation, restore reconciliation, and
pre-consumption rejection audit remain absent, so the API does not claim
production customer authentication.

Invitation-purpose attempts produce a separate short-lived opaque identity proof.
Only the proof hash and minimum provider-verified identity projection persist;
the proof grants no user, tenant, site, connector, or general write authority.
The browser adapter stores it in a separate host-only cookie, derives a bound CSRF
proof, and accepts the invitation UUID and bearer only in a strict JSON body. The
database consumes both credentials with authority and audit in one transaction.
The default process does not compose the required gateway or key, and no deployed
cleanup schedule exists, so this contract remains customer-disabled.

The snapshot mutation accepts only a strict version-one empty command body and
exactly one bounded idempotency header. A 202 response proves durable acceptance,
not execution. It returns the stable status path in both the versioned response
and `Location`; an exact retry returns the original command with `reused=true`.
The exact and latest status routes repeat current session, recovery, membership,
site, and actor authorization in PostgreSQL. The latest route is limited to the
current actor's newest `api.site.snapshot` command and grants no direct table
access. The routes can expose complete deterministic workflow
admission, Temporal first-run progress, or a terminal workflow projection. Success
contains only bounded crawl-manifest metadata; failure and cancellation contain one
closed reason. Accepted and nonterminal commands omit terminal fields. Neither
route accepts browser-provided tenant or actor identity. They share the
unconfigured gateway boundary and remain internal-only. The disposable local pilot
now composes the consumer and workflow state machine with a synthetic no-network
activity. There is still no deployed worker, complete network crawler, distributed
artifact service, provider operation, or production authority.

The fixture-analysis mutation accepts only `{"schema_version":1}` under the same
tenant-cookie, Origin, Fetch Metadata, and tenant-CSRF boundary. It requires the
current actor's completed selected-site audit, records exact command/manifest and
fixture-digest provenance, and is idempotent for that audit. The finding read
repeats live authority and returns a bounded exact schema. Both routes return 503
unless a reviewed gateway is explicitly injected; only `npm run pilot` currently
does so. The source is always `synthetic_fixture`, the configured origin is never
read, and the result cannot support a customer SEO claim.

The verified-homepage mutation additionally requires a current unexpired exact-
origin verification, matching global claim, and completed selected-site audit. It
commits a durable intent before performing one pinned, public-address-screened,
same-origin bounded GET. The result stores extracted title, first H1, description
state, final URL, status, and body digest as immutable verified-owner evidence.
Known fetch failures commit without evidence or a finding. A missing description
creates one deterministic finding; a present description creates none. This is
one customer-origin page read, not a crawl or external-write capability.

The verified-homepage proposal mutation accepts only the closed schema-version
body after current owner authority, a latest successful observation, and its open
verified-origin finding exist. Luna receives only the URL, title, first H1, and
immutable identifiers through a fixed prompt release with no tools and
`store=false`. PostgreSQL commits intent before provider I/O, blocks blind retry
after ambiguous outcomes, rechecks current evidence, and reconstructs the exact
model manifest before sealing the revision and approval request. Approval still
creates no outbox work and grants no repository or provider authority.

The local proposal mutation accepts the same closed schema-version body and only
after current owner authority and the exact fixture finding exist. Its manifest
has one fixed target, recipe, cost, authority, recovery plan, four deterministic
role contributions, and four checks. RFC 8785 canonical bytes and SHA-256 identify
the revision; PostgreSQL reconstructs the expected manifest from current evidence
before commit. Approval decisions require the exact request UUID, revision digest,
new UUIDv4 decision identity, closed decision value, and fresh browser mutation
proof. They append no outbox work and authorize no GitHub or provider operation.
The default API leaves this gateway unconfigured; only the disposable local pilot
composes it.

Callback middleware removes its authorization code and state from the ASGI query
scope before the application server can write an access-log line. The example
command also disables Uvicorn access logs. A production reverse proxy observes the
request first and must independently prove query redaction before login is enabled.

Internal one-site invitation issuance and verified acceptance are also available
to future adapters. Issuance stores only a bearer-token hash, rechecks and locks
current owner/admin authority, and commits strict tenant audit evidence.
Acceptance binds the opaque proof to a fresh signed provider identity with verified
email and atomically provisions exact issuer/subject authority while consuming the
proof and invitation. The identity role can no longer invoke acceptance with a raw
identity projection. The capability inventory reports issuance, acceptance,
proof, and HTTP ingress as internal only. There is no invitation delivery, UI,
abuse control, deployed retention operation, or production credential.

Run the API contract tests from the repository root:

```sh
.venv/bin/python -m pytest tests/api -q
```

See the [API slice](../../docs/implementation/0006-read-only-api.md),
[browser-security slice](../../docs/implementation/0007-browser-mutation-security.md),
[OIDC protocol slice](../../docs/implementation/0009-keycloak-oidc-protocol.md),
[session-issuance slice](../../docs/implementation/0010-scoped-session-issuance.md),
[OpenBao PKCE slice](../../docs/implementation/0011-openbao-pkce-secrets.md),
[OIDC composition slice](../../docs/implementation/0012-oidc-login-composition.md),
[OpenBao recovery-authority slice](../../docs/implementation/0013-openbao-recovery-authority.md),
[identity-session audit slice](../../docs/implementation/0014-identity-session-audit.md),
[consumed-login failure audit slice](../../docs/implementation/0015-consumed-login-failure-audit.md),
[site-invitation issuance slice](../../docs/implementation/0016-site-invitation-issuance.md),
[verified invitation-acceptance slice](../../docs/implementation/0017-invitation-acceptance.md),
[OIDC HTTP-ingress slice](../../docs/implementation/0018-oidc-http-ingress.md),
[tenant-selection slice](../../docs/implementation/0019-tenant-selection.md),
[session-lifecycle slice](../../docs/implementation/0020-session-lifecycle.md),
[authenticated site-context slice](../../docs/implementation/0042-authenticated-site-context.md),
[active-site context slice](../../docs/implementation/0044-active-site-context.md),
[site-onboarding slice](../../docs/implementation/0045-site-onboarding.md),
[origin-verification slice](../../docs/implementation/0048-origin-verification.md),
[invitation-identity-proof slice](../../docs/implementation/0021-invitation-identity-proofs.md),
[atomic-invitation-acceptance slice](../../docs/implementation/0022-atomic-invitation-proof-acceptance.md),
[invitation-browser-acceptance slice](../../docs/implementation/0023-invitation-browser-acceptance.md),
[human-command authority slice](../../docs/implementation/0024-human-command-authority.md),
[human-command HTTP-ingress slice](../../docs/implementation/0025-human-command-http-ingress.md),
[Temporal workflow-start slice](../../docs/implementation/0029-temporal-workflow-start.md),
[crawl workflow-state slice](../../docs/implementation/0032-crawl-workflow-state.md),
[durable fixture-finding slice](../../docs/implementation/0054-durable-fixture-finding.md),
[supervised local proposal slice](../../docs/implementation/0056-supervised-local-proposal-flow.md),
[ADR-0006](../../docs/adr/0006-truthful-read-only-api-boundary.md),
[ADR-0007](../../docs/adr/0007-session-bound-browser-mutation-proof.md),
[ADR-0009](../../docs/adr/0009-keycloak-oidc-protocol.md),
[ADR-0010](../../docs/adr/0010-scoped-hash-only-session-issuance.md),
[ADR-0011](../../docs/adr/0011-openbao-pkce-secret-boundary.md),
[ADR-0012](../../docs/adr/0012-fail-closed-oidc-login-composition.md),
[ADR-0013](../../docs/adr/0013-external-recovery-generation-authority.md),
[ADR-0014](../../docs/adr/0014-atomic-identity-session-audit.md),
[ADR-0015](../../docs/adr/0015-proof-gated-consumed-login-failure-audit.md),
[ADR-0016](../../docs/adr/0016-authority-locked-site-invitations.md),
[ADR-0017](../../docs/adr/0017-verified-invitation-acceptance.md),
[ADR-0018](../../docs/adr/0018-bounded-oidc-http-ingress.md),
[ADR-0019](../../docs/adr/0019-hash-scoped-membership-directory.md),
[ADR-0020](../../docs/adr/0020-audited-browser-session-revocation.md),
[ADR-0021](../../docs/adr/0021-purpose-bound-invitation-identity-proofs.md),
[ADR-0022](../../docs/adr/0022-atomic-invitation-proof-acceptance.md), and
[ADR-0023](../../docs/adr/0023-dedicated-invitation-browser-proof.md),
[ADR-0024](../../docs/adr/0024-atomic-human-command-authority.md),
[ADR-0025](../../docs/adr/0025-bounded-human-command-http-ingress.md), and
[ADR-0029](../../docs/adr/0029-deterministic-temporal-workflow-start.md), and
[ADR-0032](../../docs/adr/0032-durable-crawl-terminal-projection.md), and
[ADR-0045](../../docs/adr/0045-owner-controlled-site-onboarding.md), and
[ADR-0048](../../docs/adr/0048-exact-public-origin-verification.md), and
[ADR-0054](../../docs/adr/0054-prove-the-finding-pipeline-with-a-fixed-fixture.md), and
[ADR-0055](../../docs/adr/0055-supervise-local-proposals-with-exact-immutable-decisions.md) for verified
behavior, dependency decisions, and deferred controls.
