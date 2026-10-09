# Slice 0081: Technical SEO Recipes

Status: **INTERNAL EVIDENCE-BOUND PATCH SEALING IMPLEMENTED AND LOCALLY QUALIFIED; LIVE CUSTOMER REPOSITORY NOT QUALIFIED; R2 PARTIAL**.

## Objective And Tier

Turn one committed 0068 technical finding into one exact, sandbox-built,
reviewable candidate revision without writing to a customer repository. This
is **security-critical** because the candidate is intended for a later
external write. [ADR-0069](../adr/0069-seal-one-evidence-bound-recipe-patch.md)
records the conservative format and authority boundary.

## Implemented

- The crawler commits bounded missing-image-alt evidence atomically with the
  page record. A new detector release adds missing-alt and duplicate-description
  findings without changing earlier immutable reports. The recipe loader reads
  only current-detector findings joined to a completed crawl manifest and exact
  immutable page or broken-link settlement evidence under fresh owner/site and
  origin-proof checks.
- Six fixed recipe families cover missing/duplicate titles, missing/duplicate
  descriptions, missing image alt, missing canonical, invalid single JSON-LD,
  and one observed broken internal link. They accept only an owner-selected
  Eleventy root `index.html` whose source bytes match the observed homepage
  body digest. Each changes one fragment. Ambiguous tags, missing evidence,
  truncated parses, changed base/source, template syntax, unsupported format,
  protected paths, and larger-than-sealable edits fail closed.
- Each family has an exact registry manifest. Platform release registration
  uses the 0103 signing/review API; dispatch verifies the signature, current
  reviewed status, exact manifest, and denial gate. No unreviewed or revoked
  release runs. Natural-language fields use the bounded no-tools/no-store model
  adapter on page title, H1, URL, and image source only. New claims outside
  that evidence are rejected for owner handling; every accepted candidate
  still requires exact owner review in 0083.
- The existing 0080 protected-path planner runs before any build. The
  credential-free Docker sandbox must return a passed immutable receipt. One
  forced-RLS, function-only RFC 8785 revision seals the exact base, release
  hash, finding, before/after fragment, source/result hashes, build receipt,
  expected impact, and revert plan. A missing receipt, failed build, revoked
  release, or reduced authority cannot seal. Retry is idempotent. No GitHub
  write endpoint, branch, or PR is called.

## Qualification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/ruff check apps services scripts tests database/migrations
.venv/bin/ruff format --check apps services scripts tests database/migrations
.venv/bin/python -m pip check
npm test
gitleaks git --log-opts="8a2ebc5..HEAD" .
```

The [evidence record](../evidence/0081-technical-seo-recipes.json) records
exact counts. Positive tests cover every recipe and real PostgreSQL release
registration, page evidence, sealing, and replay. Negative tests cover
unsupported format/path, missing evidence, changed source, unreviewed or
revoked release, and unsupported text. Failure tests cover model/provider
loss, failed build, unknown receipt, and migration rollback. The existing
candidate-sandbox lab exercises timeout, OOM, crash, oversized output,
forbidden network, and hostile scripts in a real Docker container.

## Live Qualification And Limits

A dedicated test repository and installation, a reviewed platform signer,
an exact static Eleventy homepage matching a verified crawl, and the existing
OpenBao, shared-egress, and isolated-runner resources are needed for a live
candidate. Live repository build and production runner isolation remain
**NOT_EXECUTED**. Synthetic local signing keys do not qualify production
releases. No production write or autonomous authority is enabled.

The static-root restriction is deliberate: framework/source mapping for
Next.js, Astro, and templated content remains unavailable under EC-146.
Generated content is not certified as an approved business fact merely because
it appears on a page. Owner review remains mandatory, and the later Business
Brain must supply durable approved facts before broader autonomous drafting.
This slice does not replace the full formatting, type, integration, visual,
performance, and delivery-contract checks required for release admission by
Revision 3.2 section 21.
