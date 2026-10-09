# ADR-0009: Qualify a Narrow Keycloak OIDC Protocol Boundary

Status: Accepted. Date: 2026-09-07.

## Context

Slice 0008 made login attempts durable but deliberately stopped before contacting
an identity provider. The callback path will receive attacker-controlled URLs,
provider documents, codes, keys, and tokens. Successful code exchange alone is
not authentication: Signal must bind the result to its exact registration and
one-time attempt, reject algorithm/key confusion, and avoid leaking ephemeral
credentials through logs or evidence.

Keycloak 26.7.3 is the current reviewed lab target. Its official container guide
supports realm import from `/opt/keycloak/data/import` and explicitly says
development mode must not be used in production. Authlib 1.8.0 is the maintained
OAuth client selected for code exchange and its current documentation recommends
HTTPX2. The legacy `authlib.jose` interface is deprecated, so signed JWT handling
uses the separately maintained `joserfc` package with an explicit algorithm list.

Sources reviewed on 2026-09-07:

- [Keycloak downloads](https://www.keycloak.org/downloads)
- [Keycloak container guidance](https://www.keycloak.org/server/containers)
- [Keycloak realm import and export](https://www.keycloak.org/server/importExport)
- [Authlib FastAPI client guidance](https://docs.authlib.org/en/latest/oauth2/client/web/fastapi.html)
- [Authlib HTTPX2 client guidance](https://docs.authlib.org/en/latest/oauth2/client/http/httpx.html)
- [Authlib JWT guidance](https://docs.authlib.org/en/latest/jose/jwt.html)
- [HTTPX2 package release](https://pypi.org/project/httpx2/)

## Decision

Implement a Keycloak-specific protocol adapter rather than a generic dynamic OIDC
client. Discovery is fetched from the fixed registration issuer with a bounded
timeout, response size, content type, no redirects, no environment proxy, and TLS
verification that cannot be disabled. The advertised issuer, authorization,
token, and JWKS URLs must exactly equal Keycloak's endpoints derived from that
issuer. Discovery must advertise authorization code, S256, and RS256 support.

Use Authlib's asynchronous OAuth client over HTTPX2 to construct the authorization
request and exchange the code as a public client. Require exact state, nonce,
redirect URI, `openid email` scope, and S256 challenge. Keep codes, verifiers, and token
values in non-represented ephemeral objects; ignore rather than return any refresh
token. Use fixed safe failure codes without provider response text.

Validate ID tokens with `joserfc` and an explicit `RS256` allowlist. Accept only
bounded public RSA signature keys, ignore unrelated provider encryption keys, and
reject private key material, duplicate signing key IDs, invalid operations, or an
empty compatible set. Require signature, exact issuer, client audience, subject,
expiry, issued-at, and the nonce from the consumed durable attempt. Enforce a
30-second clock skew and 10-minute maximum token age/lifetime. Require `azp` for a
multi-audience token and match it whenever present. Validate `at_hash` whenever
the provider supplies it.

Qualify the adapter against a digest-pinned Keycloak 26.7.3 container with a
synthetic imported realm. Generate a per-run one-day certificate, trust it through
a dedicated SSL context, expose only an ephemeral loopback HTTPS port, bound CPU,
memory, and PIDs, and remove the container and certificate on completion. The lab
uses `start-dev` only as a disposable provider protocol fixture.

ADR-0017 extends the verified identity projection with provider-verified email for
invitation acceptance; exact issuer/subject remains the identity key.

## Consequences

Provider metadata cannot redirect Signal's backend to arbitrary authorization,
token, or key hosts. Symmetric/asymmetric key confusion and broad default
algorithm acceptance are excluded. A real Keycloak exchange and independently
verified ID token now work, including SSO redirects after the first browser login.

This adapter is intentionally not generic. Adding another OIDC provider requires
a separately reviewed endpoint and claims profile rather than weakening these
checks. The lab's generated CA, synthetic password, in-memory Keycloak database,
and development server are not reusable production configuration.

There is still no OpenBao integration, callback route, application session issue,
identity provisioning, logout, MFA, recovery, production Keycloak topology, or
customer authentication claim. The token object is ephemeral but process-memory
zeroization is not guaranteed by Python.

## Alternatives

Trusting every URL in discovery was rejected because an issuer or metadata
compromise could turn the backend into a network client for attacker-selected
destinations. A fully generic provider adapter was rejected because provider
profiles differ and no second provider is in scope. Hand-written token endpoint
requests were rejected in favor of a maintained OAuth client. Authlib's legacy
JOSE namespace was rejected because its current documentation marks it deprecated
and warns that decode without a restricted algorithm list is broad. An HTTP lab
was rejected after the real run showed Keycloak's `Secure` browser cookies; the
lab now uses ephemeral trusted TLS rather than forcing cookies manually.

## Verification

The source-hashed real-provider report records five passing Keycloak scenarios:
exact discovery/JWKS, complete code+PKCE+signed-token validation, code replay
rejection, wrong-verifier rejection, and wrong-nonce rejection. Unit and contract
tests cover endpoint substitution, redirects, oversized/non-JSON documents, TLS
disablement, malformed browser values, token response bounds, signature and claim
failures, public-key restrictions, secret-safe errors, and lab cleanup/configuration.
