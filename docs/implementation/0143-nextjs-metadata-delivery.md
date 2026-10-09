# Slice 0143: Next.js Metadata Delivery (F2)

Classification: **security-critical**. Stacked on 0139 front-matter delivery.
Decisions: [ADR-0155](../adr/0155-exact-nextjs-metadata-delivery.md) and
[ADR-0156](../adr/0156-nextjs-offline-static-export-verification.md).
Migration **0082**, down revision **0081**; no new tables or dependencies.
Accepted specifications and historical migrations are unchanged.

## Implemented Scope

Next App/Pages Router detection retains unavailable static-export/network reasons.
Tree-sitter proves exact existing title/description literal spans in static
`export const metadata` or one unshadowed `next/head`. Replacement retains quotes
and all surrounding source bytes, reparses and cannot expand into another code
path. Instruction-like comments/string content are data. Protected paths,
configuration, workflows, Pages special files and unsupported mappings are denied.

Generated metadata, templates, imported/data values, mutations, spreads and
non-static objects are unavailable. Dynamic App routes need literal-enumerated
static parameters; Pages dynamic routes are unavailable. All layouts/dynamic
routes and measured multi-page edits are A4. A segment layout must affect exactly
one static page; root layouts require a bounded catalog and complete measured
scope. Only the existing metadata literal is edited, never the layout body.
Unsupported route groups/parallel routes, CRLF and invalid/oversized source remain
unavailable. No metadata insertion or whole-source normalization is supplied.

## Build And Authority

Reuse 0126 pinned official-registry cache/egress and credential-free network-none
container. Only conservative literal static export to `out` and the exact
`next build` script are supported. npm install/build are offline with lifecycle
scripts disabled; telemetry is disabled. Font imports and failed network builds
remain visibly unavailable with no network fallback. Durable closed failure
reasons cannot be interpreted as successful receipts.

Reuse 0127/0139 baseline/candidate receipts, encrypted all-built-HTML storage,
committed crawl agreement, same base/tree/lockfile and exact all-page/artifact
assertions. Target fields alone may differ; bodies, RSC payloads, assets and every
other HTML byte must remain unchanged. Flat static-export routes map without
`.html`. Missing/failed receipts or extra/omitted/unexpected output refuse sealing.

Separately signed reviewed F2 releases are mandatory. Owner dashboard Inbox
approval and five-minute MFA apply to A2 and A4 at approval and dispatch.
Standing/weekly/autonomous/Slack dispatch is ineligible. Current 0125 base
acceptance and all journaled ref-only write/tenant/recovery/protected-path guards
remain unchanged. No merge, deploy, default-branch write or production authority.
Strict existing Inbox contracts display the same exact impact and samples.

## Qualification

The frozen-source full gate passed once: **3,226 test/check cases, zero failures
and zero skips**, plus the GSC boundary, lint, formatting, dependency and
repository checks. PostgreSQL: 919; authority journal: 14; autonomy delivery: 37;
candidate sandbox: 30; consumer: 3; crawler network: 14; page attempt: 1;
Temporal: 7; consumer image: 6; OpenBao: 23; API/identity/tooling/connectors: 1,967;
npm: 43 repository and 162 dashboard tests. Formatting checked 449 Python files;
documentation checked 259 Markdown files. All lab source hashes match the frozen
source. Invocation-owned cleanup completed; no owned containers/processes remain.
Counts, receipt paths and hashes are in
[evidence](../evidence/0143-nextjs-metadata.json).

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
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q --junitxml=.runtime/0143-gate/python.xml
.venv/bin/ruff check apps services scripts tests database
.venv/bin/ruff format --check apps services scripts tests database
.venv/bin/python -m pip check
npm test
gitleaks protect --staged --config /tmp/signal-0143-main-gitleaks.toml --redact
gitleaks git --config /tmp/signal-0143-main-gitleaks.toml --log-opts ad3a1c07c85632cd0a5d046d5c5c884068a8ccc8..HEAD --redact
```

The temporary gitleaks configuration is exactly `origin/main:.gitleaks.toml`;
the allowlist is not changed. Scans qualify both staged and committed slice.

Live builds of actual Next repositories, actual engine compatibility, live
GitHub/registry providers, customer credentials/data, deployment/live assertions
and production composition remain **NOT_EXECUTED**. Synthetic Next CLI packages
qualify the offline build/proof boundary, not real Next behavior. No private
customer repository or customer data was accessed.

## First Live Run

The owner supplies an exact verified site/repository/App binding and current
permissions/base, private OpenBao credentials and admitted GitHub/registry
egress contexts. Supply the pinned image, public cache, encrypted artifact backend
and private key, committed crawl evidence, a signed reviewed F2 release and a
supported literal mapped to exact built URLs. Qualify the actual Next graph and
paired build receipts without changing isolation or byte-scope assertions.
Approve the exact revision with fresh Owner dashboard MFA; accept any unprotected
default base separately under 0125. Existing workflow certification is mandatory.
A PR does not establish deployment or live verification.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0082` follows `0081`; 157 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2530 API/identity/tooling/connector
cases, 1064 PostgreSQL cases (including the runner's repeated Brain checks),
46 repository and 234 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.
The slice-owned candidate-sandbox lab passed all 31 cases on this rebased source.
