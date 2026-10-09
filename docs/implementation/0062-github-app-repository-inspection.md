# Slice 0062: GitHub App Repository Inspection

## Objective

Implement the real least-privilege provider boundary needed to inspect one exact
GitHub repository and base commit before durable repository binding or candidate
generation.

## Implemented

- Added RS256 GitHub App JWT creation from a transient private key with a bounded
  issue time and expiration.
- Added repository- and permission-downscoped installation token creation. The
  adapter requests one repository and only `contents: read`, accepts only read-only
  contents/metadata permission evidence, and rejects broader or incoherent tokens.
- Added exact repository metadata and selected-branch reads against the fixed
  `api.github.com` origin and current versioned API contract.
- Added strict owner, repository, Git ref, and relative content-path validation.
  Hidden paths, traversal, invalid refs, archived or disabled repositories, wrong
  repository IDs, and mismatched branch identities fail closed.
- Added bounded response streaming, mandatory TLS, no redirects, no environment
  proxy inheritance, fixed timeout, retryability classes, and credential-safe
  dataclass/error behavior.

## Verification

```sh
PYTHONPATH=services/control_plane/src .venv/bin/pytest -q tests/connectors/test_github_app.py
.venv/bin/ruff check services/control_plane/src/signal_core/github_app.py tests/connectors/test_github_app.py
.venv/bin/ruff format --check services/control_plane/src/signal_core/github_app.py tests/connectors/test_github_app.py
npm test
```

The focused adapter suite passes 34 positive, negative, and failure cases. A live
negative request using a generated non-App key reached GitHub and returned the
expected sanitized authorization-rejected class. No customer or developer
credential was used.

## Limits And Next Work

The adapter is implemented but not yet reachable from the browser and no authorized
GitHub App success has been qualified. The next slice must store the App private key
server-side, accept an owner-attributed exact repository target, commit intent
before provider I/O, and persist the immutable repository/base observation under
tenant and site scope. Only then can the approved revision enter an isolated
candidate builder.
