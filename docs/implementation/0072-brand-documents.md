# Slice 0072: Owner Brand Documents

Status: **LOCAL BOUNDARY IMPLEMENTED; PRODUCTION COMPOSITION AND PHYSICAL RETENTION
RELEASE NOT QUALIFIED**. Security-critical R1 slice under Revision 4.0 INV-033,
EC-135, and the carried-forward Revision 3.2 identity, tenancy, secrets, and
artifact controls. Decision: [ADR-0084](../adr/0084-owner-brand-document-boundary.md).

## Implemented

- Same-origin dashboard upload/list/view/supersede/delete controls and BFF relay;
  API uses existing exact-cookie CSRF and strict-schema patterns. Browser files
  are limited to 2 MiB and one of `.pdf`, `.docx`, `.md`, or `.txt`; active count
  is 20 per site. Extension and content signatures must agree.
- PDF active objects, executable/archive spoofing, DOCX macros/embedded objects,
  encrypted ZIP members, traversal, expansion over 8 MiB, PDFs over 50 pages,
  malformed XML, invalid UTF-8, and output over 512 KiB fail closed.
- Parser process runs with no network, a wall deadline, CPU/file limits, a
  sanitized environment, and OS file-read restrictions. A missing required
  sandbox or PDF parser reports unavailable rather than running unsandboxed.
- Uploads use the 0036 encrypted immutable object store. The dedicated AES key
  is read from OpenBao `signal-artifacts/data/brand/default` by a read-only
  capability. Migration 0052, after autonomy migrations 0049-0051, binds artifact,
  attestation, document, and event rows by tenant/site with forced RLS and owner-only
  function access. Owner/count
  preflight avoids known-rejected orphan objects; registration rechecks both
  under a site lock after encrypted readback.
- Text digests, injection signals, and credential signals are recorded. Secret
  content is blocked from the typed read port and owner text view; ordinary
  tables contain no extracted text. Even clean content has the type label
  `owner_upload_untrusted_data`, never approved facts or instructions. The
  disposable local pilot composes a fresh per-run OpenBao key and private store
  for synthetic owner files.
- Superseded and deleted documents stop serving text. Delete is append-only and
  explicitly reports `deleted_retained`; ciphertext remains for evidence.

## Limits

No model reads these documents in 0072. The typed owner read port is for 0073 to
adapt under its own provenance and fact-approval boundary. Current detection is
conservative pattern screening, not a comprehensive DLP or prompt-injection
proof. No physical purge, hold release, key rotation, distributed object store,
or unresolved-operation reference release exists yet. No production key or
customer document was used, and the default API composition remains unconfigured.
The local pilot is disposable and must not be used for customer documents.

## Verification

Run the full gates requested for this slice:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling tests/connectors
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/pip check
npm test
```

Results, including failures and unexecuted checks, are in
[0072 evidence](../evidence/0072-brand-documents.json).
