# Slice 0070: Durable GitHub Read Binding

Status: **INTERNAL SERVICE IMPLEMENTED AND LOCALLY QUALIFIED; OWNER UI AND LIVE APP NOT QUALIFIED; R1 PARTIAL**.

## Objective And Tier

Bind one owner-selected GitHub App installation, repository, protected base
branch, and content path to one verified Signal site before any repository read.
This is **security-critical** because it adds a provider credential, durable
resource association, and connector egress. [ADR-0066](../adr/0066-bind-github-read-authority-to-owner-site.md)
records the decision. The GitHub-first beta reorder remains an internal milestone,
not a Revision 4.0 release admission.

## Implemented

- Migration `0039` stores a request digest, exact target, owner identity,
  authority epochs, recovery generation, observed repository ID/base SHA,
  sanitized failure, and revocable status. Lifecycle events are immutable.
  Both tables force RLS; only narrow identity functions can prepare, finish,
  read, or revoke. Each step rechecks current session and site authority, and
  preparation and completion require current exact-origin verification.
- The GitHub App key is read from `signal-github/data/github/app` in OpenBao KV
  v2 with one read-only credential. PostgreSQL, events, error messages, and
  reprs do not contain the PEM or installation token. The real OpenBao lab
  provisions a synthetic key and proves the reader cannot mutate it.
- The 0062 provider adapter requests one repository and `contents: read`, then
  checks the repository ID, name, selected branch, and base SHA. Binding also
  requires a protected base. A current read repeats the provider check; a
  revoked local binding, rejected installation, changed repository identity,
  or lost protection cannot authorize downstream work.
- `GitHubSharedEgressTransport` routes the three fixed GitHub inspection
  endpoints through the durable connector gateway. It rejects other hosts,
  routes, methods, queries, credentials in URLs, wrong API version, and
  unapproved user agent. A missing transport refuses live GitHub I/O.
- The live qualification command uses only an exact pre-created
  `api.github.com` shared-egress context and prompts for the owner session and
  OpenBao read token. It does not create a PR or repository write.

## Qualification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-crawler-network-tests.py
PYTHONPATH=services/control_plane/src .venv/bin/pytest -q tests/connectors/test_github_app.py tests/connectors/test_github_read_binding.py tests/connectors/test_github_shared_egress_transport.py tests/tooling/test_github_binding_qualification.py tests/tooling/test_crawl_http.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/ruff check apps services scripts tests database/migrations
.venv/bin/ruff format --check apps services scripts tests database/migrations
.venv/bin/python -m pip check
npm test
```

Exact outcomes are in [the evidence record](../evidence/0070-github-read-binding.json).
The PostgreSQL suite checks owner and verified-site gating, exact target and
idempotency conflicts, authorization reduction, local revocation during an
in-flight read, immutable events, function-only access, failure receipts, and
migration rollback. Fakes exercise provider permission loss and wrong binding;
they do not count as live GitHub success.

## Live Qualification

Provision a **dedicated test** GitHub App with `contents: read` on only the
selected test repository, a protected base branch, and an OpenBao KV v2 secret
at `signal-github/data/github/app` containing exactly `app_id` and
`private_key_pem`. Grant the runtime token only read on that path. Prepare a
running, site-scoped connector-egress context for only `https://api.github.com`
with current robots evidence, public-address pinning, and admission/ingest
roles. The JSON context has the same `schema_version`, `artifact_root`, `run`,
and `policy` shape as the [Jev qualification context](0065-shared-egress-proxy.md#live-qualification-context),
but policy allows only GitHub, uses `SignalBot/1.0`, denies redirects, and caps
the request at five seconds. The command validates that context before I/O.

```sh
export SIGNAL_GITHUB_EGRESS_CONTEXT=/absolute/path/to/github-egress-context.json
export SIGNAL_GITHUB_EGRESS_ADMISSION_DSN='...'
export SIGNAL_GITHUB_EGRESS_INGEST_DSN='...'
export SIGNAL_GITHUB_IDENTITY_DSN='...'
export SIGNAL_GITHUB_OPENBAO_URL='https://bao.example.invalid'
.venv/bin/python scripts/qualify_github_read_binding.py \
  --site-id <verified-site-uuid> --idempotency-key <stable-uuid> \
  --installation-id <test-installation-id> --owner <test-owner> \
  --repository <test-repository> --base-branch <protected-branch> \
  --content-path <relative-content-file> --recovery-generation <current-generation>
```

The command prompts for the owner session and OpenBao read token. Do not put
either in arguments, environment variables, the context file, or shell history.
This live run is **NOT_EXECUTED** until the dedicated App, repository, protected
branch, OpenBao key, and egress context exist.

## Limits And Next Work

The binding is an internal owner-session service, not yet a browser connector
screen or production service composition. The local fake GitHub tests do not
verify actual GitHub App permissions, installation revocation behavior, or
branch protection. No repository content is cloned, no candidate is built,
and no write authority exists. Slice 0079 extends this exact binding with
separately gated PR authority and framework/content-format detection.
