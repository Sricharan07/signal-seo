# ADR-0084: Owner Brand Documents Stay Untrusted and Encrypted

Status: accepted for slice 0072, 2026-09-29.

## Context

R1 onboarding needs owner-supplied PDF, Markdown, DOCX, and text evidence before
Business Brain can derive facts. Files may be malformed, credential-bearing, or
addressed to an agent. They must not become instructions or ordinary SQL text.

## Decision

The same-origin BFF checks browser origin, selected-site cookie, strict JSON, and
byte bounds; the API requires its existing session-bound CSRF proof. A fresh
current owner/site check precedes extraction and is repeated at registration.
The owner key comes from a dedicated read-only OpenBao KV-v2 path. Files are
screened by content, extracted in a short-lived no-network OS sandbox with CPU,
time, file, expansion, page, and output bounds, then stored through the 0036
AES-GCM no-overwrite/readback boundary. PostgreSQL receives only the encrypted
object's digest/metadata, text digest, and injection/secret signals. No extracted
text enters ordinary tables. Active reads have an explicit untrusted-data type;
secret-bearing documents cannot be read from that port.

Superseding is append-only. Deleting records a tombstone that immediately blocks
future reads while retaining encrypted evidence. The UI says retained evidence
is not erased. Physical release, holds, and unresolved-operation reference
tracking are not yet implemented; there is no purge or production claim.

## Alternatives

Keeping extracted text in PostgreSQL would spread owner secrets into ordinary
backups and queries. Trusting file extensions or running parsers in the API
process would make spoofed or malformed files an ingress risk. Deleting bytes
immediately would misrepresent evidence retention. These options were rejected.

## Consequences

- Missing OpenBao key, parser, sandbox, storage, or owner authority fails closed.
- Signal does not call a model with uploaded documents in this slice.
- The local macOS sandbox denies network and home-directory reads except the
  parser script and Python environment; Linux requires bubblewrap and exposes
  only system runtime paths plus the exact input and parser files.
- Parser and secret screening are defensive filters, not a claim that arbitrary
  attacker-controlled formats are intrinsically safe. Production composition
  awaits host-specific sandbox and key-lifecycle qualification.

## Verification

See [slice 0072](../implementation/0072-brand-documents.md) and its evidence.
