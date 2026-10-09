# Slice 0139: Front-Matter Content Adapter (F1)

Classification: **security-critical**. Stacked on 0127 Astro delivery.
Decisions: [ADR-0147](../adr/0147-exact-front-matter-content-adapter.md) and
[ADR-0148](../adr/0148-front-matter-offline-toolchain-availability.md).
Migration **0081**, down revision **0080**; no new tables. Accepted specifications
and existing migrations are unchanged.

## Implemented Scope

One leading-block data parser supplies exact existing top-level title/description
string spans for Markdown, MDX and Eleventy Nunjucks. YAML/TOML/JSON, quoted and
supported multiline values, BOM and CRLF are covered. Replacement changes one
parser-proven scalar, retains its style/line layout and preserves comments, key
order, delimiters, other fields and body bytes. Instruction-like content is data.

Duplicate keys, aliases/anchors/tags, missing/non-string/nested targets, ambiguous
syntax, quoting-style changes and multiline line-layout changes are unavailable.
TOML bare root metadata keys must precede tables/unrelated multiline structures.
The adapter does not insert missing fields or normalize syntax.

Detection observes Eleventy `.md`/`.njk`, Next.js front-matter MDX conventions,
Hugo/Jekyll front matter and existing Astro collection entries. Detection is not
a receipt. Hugo/Jekyll explicitly report **build verification unavailable: no
pinned offline toolchain**; neither can seal. Dynamic/wrapped Next configuration,
server output, unsupported commands and unpinned graphs remain unavailable.

## Build And Authority

Reuse 0126 official-registry shared egress, bounded integrity cache and pinned
credential-free network-none container. Install/build stay offline with lifecycle
scripts disabled. Commands are `astro build`, `eleventy`, or `next build`;
Next requires literal static export to `out`, Eleventy uses `_site`, and Astro
uses its parsed configured output directory. No configuration is edited.

Reuse 0127 encrypted HTML receipts and all-page/artifact verification. Baseline
and candidate require the same base/tree/lockfile and committed crawl agreement.
Each declared changed page differs only in its intended built field. Extra/omitted
pages, changed bodies/assets and failed/missing receipts refuse sealing. More
than one changed built page is A4. Existing scope/source/manifest bounds remain.

Separately reviewed signed F1 releases use existing immutable impact and MFA
records. Strict API/dashboard Inbox contracts display exact impact/samples.
Approval and dispatch require the current Owner via dashboard Inbox and MFA
within five minutes. Standing/weekly/Slack dispatch are ineligible. 0125 base
acceptance must still be current and each PR needs exact Inbox approval. Layouts,
includes, data, configuration, workflows and protected paths are never edited.
Dispatch reconstructs the scalar proof before the existing journaled ref-only
write path. No merge, deploy, default-branch push, secrets or production authority.

## Qualification

The frozen-source full gate passed: **3,142 test/check cases, zero failures**,
plus the GSC boundary guard, lint, formatting, dependency and repository checks.
Database: 915; authority journal: 14; autonomy delivery: 37; candidate sandbox:
23; consumer: 3; crawler network: 14; page attempt: 1; Temporal: 7; consumer
image: 6; OpenBao: 23; API/identity/tooling/connectors: 1,896; npm: 43 repository
and 160 dashboard tests. Formatting checked 443 Python files; documentation
checked 256 Markdown files. Invocation-owned cleanup completed; no owned
containers/processes remain. All lab source hashes still match the qualified
source. Counts, receipt paths and key hashes are in
[evidence](../evidence/0139-front-matter.json).

The earlier exploratory database run was superseded: pytest had collected old
0077-head assertions before their source update to 0093. Its seven stale-head
failures were not counted as qualification. The frozen rerun passed all 915.

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-autonomy-delivery-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q --junitxml=.runtime/0139-gate/python.xml
.venv/bin/ruff check apps services scripts tests database
.venv/bin/ruff format --check apps services scripts tests database
.venv/bin/python -m pip check
npm test
gitleaks protect --staged --config /tmp/signal-0139-main-gitleaks.toml --redact
gitleaks git --config /tmp/signal-0139-main-gitleaks.toml --log-opts 237ccd15c35a1eafd7f4211f29e626d2874fb5c7..HEAD --redact
```

The temporary gitleaks configuration is exactly `origin/main:.gitleaks.toml`;
the allowlist is not changed. Secret scans qualify the staged and committed slice.

Live GitHub/registry, actual generator compatibility, customer build/repository,
deployment/live assertions and production composition are **NOT_EXECUTED**.
Synthetic packages qualify npm/container execution only. No private customer
repository, customer credentials or customer data were accessed.

## First Live Run

The owner/integration thread supplies an exact verified site/repository/App
binding with current permissions/base, private OpenBao credentials, admitted
GitHub/registry egress contexts, pinned sandbox image, public cache, encrypted
artifact backend and private key. Supply committed crawl evidence, a reviewed
signed F1 release and supported scalar mapped to exact built URLs. Qualify the
real generator/graph within bounds and retain both build receipts. Approve the
exact revision with fresh dashboard Owner MFA; accept an unprotected default
base separately under 0125. Existing workflow certification remains mandatory.
A PR is not deployment/live verification. Non-npm support needs a reviewed future
slice, not an owner bypass of the build receipt gate.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0081` follows `0080`; 157 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2459 API/identity/tooling/connector
cases, 1060 PostgreSQL cases (including the runner's repeated Brain checks),
46 repository and 232 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.
The slice-owned candidate-sandbox lab passed all 24 cases on this rebased source.
