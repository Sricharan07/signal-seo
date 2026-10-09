# Disposable Keycloak Protocol Lab

This lab qualifies Signal's OIDC authorization-code boundary against a real,
synthetic Keycloak instance. It is not a production identity deployment and does
not create a Signal user or application session.

## What It Proves

- exact Keycloak discovery endpoints are accepted and provider-selected network
  destinations are rejected;
- a public client completes authorization code with S256 PKCE;
- ID tokens are verified with a public RS256 JWKS and checked for issuer,
  audience, time, nonce, authorized party, and access-token hash;
- authorization-code replay, a wrong verifier, and a wrong nonce are rejected;
- reports and errors contain no authorization code, verifier, password, or token.

The committed realm, client, user, email address, and password are deliberately
synthetic. The password secures nothing outside the invocation-owned container.

## Run

From the repository root with Python 3.12, the pinned dependencies, Docker, and
at least 3 GiB free:

```sh
.venv/bin/python scripts/keycloak_lab.py
```

The runner pulls a digest-pinned Keycloak 26.7.3 image, generates a one-day local
certificate and key with mode `0600`, binds HTTPS to an ephemeral loopback port,
and starts Keycloak with `start-dev --import-realm`. It trusts only that generated
certificate for the test. The container and certificate directory are removed on
normal and catchable failure paths.

Keycloak explicitly documents `start-dev` as a development mode that must not be
used in production. A real deployment needs independently managed certificates,
database state, hostname and proxy configuration, backups, monitoring, hardening,
and an upgrade procedure. See [ADR-0009](../../docs/adr/0009-keycloak-oidc-protocol.md)
and [slice 0009](../../docs/implementation/0009-keycloak-oidc-protocol.md).

The sanitized runtime report is written to `.runtime/keycloak-tests/latest.json`.
Reviewed evidence is copied to `docs/evidence/`; token material is never evidence.
