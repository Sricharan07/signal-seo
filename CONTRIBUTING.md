# Contributing

Signal is being implemented in small, documented, tested commits. Start with the
[agent working agreement](AGENTS.md),
[product requirements](docs/product/prd.md),
[Revision 4.0](Signal_Production_Engineering_Specification_Revision_4_0.md),
[R1–R4 roadmap](docs/implementation/roadmap.md),
[current status](docs/implementation/status.md), and
[documentation index](docs/README.md).

## Start Here

Thanks for helping. A few ground rules first:

- **Be respectful.** Everyone follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Security issues are private.** Never report a vulnerability in a public issue;
  follow [SECURITY.md](SECURITY.md).
- **Sign the CLA.** Your first pull request asks you to sign the
  [Contributor License Agreement](CLA.md) once. Pull requests cannot be merged
  without it.
- **License.** Signal is source-available under the
  [Elastic License 2.0](LICENSE). Contributions are accepted under the CLA.

### How to contribute

1. **Find or open an issue.** For anything beyond a typo, open an issue (or comment
   on one) before writing code, so the approach can be agreed first. Issues labelled
   `good first issue` are a good starting point.
2. **Fork and branch.** Use one branch per change, named after it (for example
   `fix/inbox-empty-state`).
3. **Keep changes small.** One problem per pull request. Do not mix refactors with
   behavior changes.
4. **Follow the safety rules.** Read the "Product Safety Rules" in
   [AGENTS.md](AGENTS.md). Changes that let Signal do more on its own, write
   somewhere new or read new data are security-critical and need the extra records
   described below.
5. **Test it.** Add positive, negative and failure tests, and run the checks below.
6. **Open the pull request.** Fill in the template. CI runs the Quality workflow:
   repository and dashboard checks, Python tests, linting, and the Docker labs. The
   maintainer reviews every pull request.

### Development setup

You need Node.js 22, Python 3.12, Docker Desktop (or Docker Engine) and
[gitleaks](https://github.com/gitleaks/gitleaks).

```sh
npm ci
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm test
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
```

To see the whole product running locally on disposable dependencies, use the local
pilot: `npm run pilot` (see [Try The Local Pilot](README.md#try-the-local-pilot)).

### Commits

- Write a short imperative subject: `Fix empty Inbox copy`, or `0172: ...` for
  roadmap slices.
- Never commit secrets, real customer data, personal data or environment-specific
  identifiers. Test credentials start with `synthetic-`.
- Never modify an existing database migration or an accepted specification revision.

## Local Checks

Use Node.js 22 and install the locked development dependencies:

```sh
npm ci
npm test
```

These are repository/documentation checks, not proof of production readiness.
Capability-specific test commands belong in their implementation records. Docker
integration tests must use unique disposable project names and synthetic data.
The current Python and PostgreSQL commands, role model, and cleanup behavior are
documented in the [database guide](database/README.md).

Run the current non-database Python contract tests and static checks with:

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/python -m pip check
```

The GitHub workflow runs the same checks on a clean Linux host. Keep actions pinned
to reviewed full commit SHAs, preserve read-only permissions for quality jobs, and
do not add deployment credentials to pull-request workflows. A configured workflow
is not verified until a real remote run is observed.

The [browser worker](docs/implementation/0067-sandboxed-browser-worker.md) has a
dedicated real Docker/PostgreSQL lab:

```sh
.venv/bin/python scripts/run-browser-worker-tests.py
```

It requires isolated Docker bridge gateways and owns a private BuildKit builder,
cache and image, all removed after the run. It never prunes shared Docker state.
Live Jev, owner-site qualification and production browser composition are separate
gates, not results of this lab.

## Full Local Gate

With the existing Python environment, Node dependencies, Docker Desktop and
`gitleaks` installed, run the complete local qualification from the repository root:

```sh
.venv/bin/python scripts/run-full-gate.py --range main..HEAD
```

This runs `npm test`, API/identity/tooling/connector pytest, Ruff lint and format
checks over all Python source and tests (including delivery and page attempts),
`pip check`, every `scripts/run-*-tests.py` lab, OpenBao, Keycloak, the GSC boundary
check, and gitleaks with a fresh copy of **main's** `.gitleaks.toml`. Supply the
actual commit range being reviewed. Before committing, also scan the staged diff;
an empty commit range does not scan uncommitted work. No allowlist is changed.

Defaults are four steps at a time, at most two Docker labs at a time, and three
isolated PostgreSQL database shards. Delivery uses its existing two complementary
shards. Browser, crawler and page-attempt fixtures atomically claim distinct
internal subnets per invocation, including across worktrees; candidate containers
have no network and cleanup checks use an invocation label. Bounds are `--jobs 1..8`, `--docker-jobs 1..3`, and
`--database-shards 1..6`. These limits apply to this invocation, not other tracks
running independently on the same Docker engine. Lower concurrency when the host
is already busy. The standalone database lab also defaults to three shards:

```sh
.venv/bin/python scripts/run-database-tests.py --shards 3
SIGNAL_DATABASE_TEST_SHARDS=1 .venv/bin/python scripts/run-database-tests.py
```

Admission refuses below four GiB free by default, both before the gate and again
before each step. `--minimum-free-gib` accepts 3..64; individual lab guards remain
unchanged. The runner creates an invocation-private artifact root and a private
system temporary directory outside the repository for each step, then removes
both after completion, including failed steps, bulk artifacts and SDK caches.
It never removes an old `.runtime`
directory, prunes Docker, or discovers/stops somebody else's containers. Existing
lab helpers retain exact invocation-owned container/network/image cleanup.

Every step has a deadline: 120 seconds for static/scanner/boundary checks,
600 seconds for npm, non-database pytest and identity/secrets labs, and 3,600
seconds for integration labs (their tighter internal deadlines still apply).
Timeout or Ctrl-C interrupts only owned process groups, allowing up to 120 seconds
for cleanup before forced termination. Unconfirmed process or artifact cleanup is
a failure, never readiness; inspect the retained log and that invocation only.

The printed `.runtime/full-gate/run-*/summary.json` records wall-clock duration,
each exact command, pass/fail/skip counts, count kind (test cases or checks), exit
code, failure reason, and cleanup status. Step duration includes admission wait;
its execution timeout starts only when its subprocess starts.
Small JUnit/provider reports and private
logs are retained alongside it; bulk per-step work directories are deleted. Any
failure, timeout, missing/empty report, or unexpected skipped test exits nonzero.
The database step verifies complete exact-once shard collection and repeats all
four Business Brain extraction/replay variants five additional times in the same
disposable database. No failed test is retried into a passing result.

Allow roughly 20-30 minutes for the default bounded runner on a comparable warm
local setup. The measured full gate fell from **45:36 serial to 19:50 bounded**
(20 passing steps, no failures). Detailed before/after results are recorded in
[slice 0138](docs/implementation/0138-fast-gate.md); cache warmth,
available CPU, Docker load and other tracks affect elapsed time. This is local
synthetic qualification, not live-provider or production release evidence.

## Completing a Slice

Classify the slice first (Revision 4.0 section 19). **Security-critical** slices
(identity, tenancy, secrets, egress, browser sandbox, autonomy gate, standing
authorization, external writes, publishing authority, recovery) follow every step
below. **Product** slices, which use existing authority without changing it, need
steps 1–3, a status line, a changelog entry, and step 7; they skip the
implementation record, ADR, and evidence record unless a reviewer asks for them.

1. State its scope and map it to specification requirements and edge cases.
2. Implement the behavior and its denial, boundary, and failure paths.
3. Add or update tests. Use actual database/provider behavior where the contract depends on it.
4. Document interfaces, data ownership, operational commands, known limitations, and actual test results in `docs/implementation/`.
5. Add an architectural decision record for a consequential choice. State alternatives, consequences, and evidence; do not turn every variable name into an ADR.
6. Update `docs/implementation/status.md` and `CHANGELOG.md` without overstating completion.
7. Run all applicable checks, inspect `git diff` and the staged diff, and commit the small completed slice with a descriptive message.

Normative scope changes require a new specification revision and a superseding
ADR. Preserve every accepted revision, update the PRD, roadmap, status, indexes,
implementation record, changelog, and repository assertions together, and never
use a documentation change to claim runtime evidence.

Suggested commit prefixes are `docs:`, `chore:`, `feat:`, `fix:`, and `test:`.
Publishing a repository, deploying a service, or changing customer resources is
separate from making local commits.

## Sensitive Data

Never commit actual tokens, application passwords, cookies, customer captures,
database dumps, or unredacted provider payloads. Test fixtures must be synthetic.
`.gitignore` is only housekeeping. Dependencies and infrastructure must be pinned
and reviewed, and any vulnerability-scan limitations must be stated honestly.

Scan every change with `gitleaks git --log-opts="<base>..HEAD" .`. The repository
`.gitleaks.toml` keeps all default rules and allows only three reviewed shapes:
SHA-256 digests keyed by file path in `docs/evidence/*.json`, values starting with
`synthetic-` or `synthetic_` in `tests/` and `scripts/`, and bare lowercase
identifiers or Ed25519 key type names. Label test credentials `synthetic-…` so
they are recognisable. Never widen an allowlist to silence a finding; fix the
finding or remove the value.

## Decisions and Baselines

Use sequential ADR identifiers in `docs/adr/`. Keep accepted decisions immutable
in meaning: supersede them with a new record when the choice changes. The supplied
specifications are preserved baselines, not evidence of executed product tests.
