# Slice 0050: Read-Only GSC Property Discovery

- Status: Protocol implemented and locally qualified; authorized provider success blocked
- Date: 2026-09-12
- Milestone: M2 partial
- Specification: Revision 3.2 sections 10, 12.1, 24.1-24.5, 25, 31, and 33
- Decision: [ADR-0050](../adr/0050-fixed-read-only-gsc-property-discovery.md)
- Runbook: [GSC property discovery](../runbooks/gsc-property-discovery.md)

## Scope

This slice adds the first provider-specific Google Search Console boundary needed
by the owner connection journey. It discovers the exact properties visible to an
already authorized Google credential and determines which read-only resource can
represent one already verified Signal origin.

It does not implement OAuth, persist credentials or bindings, expose a dashboard
route, import Search Analytics data, or grant provider authority.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Provider boundary | Fixed Google `sites.list` HTTPS endpoint, `GET` only, no redirects, no environment proxies, verified TLS, and five-second timeout |
| Privilege contract | One pinned Search Console read-only scope constant for the later OAuth flow |
| Credential handling | Strict bounded bearer validation; token stays in call-local memory and never appears in returned state or fixed errors |
| Response bounds | Exact JSON media type, at most 128 KiB and 1,000 unique entries, with a closed top-level and entry schema |
| Resource identity | Preserves Google's exact `siteUrl` and `permissionLevel`; distinguishes domain from URL-prefix properties |
| Eligibility | Requires a readable permission plus exact canonical-origin matching; unverified and unrelated resources remain ineligible |
| Failure model | Maps authorization, provider availability, other provider responses, local authority, and TLS configuration to fixed codes |
| Real-provider lane | Calls the fixed public Google endpoint with a synthetic bearer and requires the expected authorization rejection |

## Data And Trust Flow

```text
future secret-store lease -> call-local bearer
  -> fixed Search Console sites.list request
  -> bounded exact provider response
  -> immutable property candidates with exact resource names
  -> later owner selection and durable binding (not in this slice)
```

No customer credential, account, property, or response is stored by this slice.
The future connector flow must fetch a token from the secret boundary, call this
module, and persist only the selected resource identity and secret reference.

## Verification

Run:

```sh
.venv/bin/python -m pytest tests/connectors/test_gsc_properties.py -q
.venv/bin/python -m pytest tests/api tests/connectors tests/identity tests/tooling -q
.venv/bin/python -m ruff check services/control_plane/src/signal_core/gsc_properties.py tests/connectors/test_gsc_properties.py scripts/check-gsc-provider-boundary.py
.venv/bin/python -m ruff format --check services/control_plane/src/signal_core/gsc_properties.py tests/connectors/test_gsc_properties.py scripts/check-gsc-provider-boundary.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
npm run test:repo
npm run check:docs
git diff --check
```

Exact counts, source hashes, and the secret scan are recorded in
[0050 GSC property discovery](../evidence/0050-gsc-property-discovery.json).

## Explicit Limits

- The repository has no Google OAuth client configuration or owner authorization
  route, so successful real account/property discovery is not yet qualified.
- No access or refresh token is persisted, refreshed, revoked, logged, or exposed.
- No `app.integrations`, binding, capability snapshot, analytics import, health, or
  dashboard state is created.
- The constant documents the required least-privilege scope but is not yet wired
  into an authorization request.
- Discovery is read-only and grants no change, approval, crawl, or production-write
  authority.

## Next Safe Dependency

Compose the deployed owner identity and origin-proof journey, then add a durable
GSC OAuth attempt, OpenBao-backed secret reference, exact owner property selection,
revocation, and connector health before importing Search Analytics evidence.
