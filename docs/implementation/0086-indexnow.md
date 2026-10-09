# Slice 0086: IndexNow Key File and Verified-Change Notification

Status: **INTERNAL PATH IMPLEMENTED; LOCAL LABS PASS; LIVE INDEXNOW AND KEY PR NOT_EXECUTED; R2 PARTIAL**.

## Classification And Scope

Security-critical: new outbound provider, OpenBao key lifecycle, reviewed
repository addition, durable write intent, and site/tenant authority. Contract:
Revision 4.0 section 12, EC-142, and section 17 R2; retained Revision 3.2 safety,
egress, tenant, secret, and delivery contracts. Decisions:
[ADR-0102](../adr/0102-owner-reviewed-indexnow-key-publication.md) and
[ADR-0103](../adr/0103-verified-change-indexnow-egress.md).

Migration `0065` follows unchanged `0064` on the accepted merge train.

## Implemented Boundary

- `OpenBaoIndexNowKeys` generates a format-valid 64-character hexadecimal key
  with KV-v2 CAS-zero, exact version-one reads, scoped tenant/site/generation
  paths, and no raw provider errors. PostgreSQL stores identities and digests,
  not an independent key secret. The sealed public key-file patch and exact
  public key-file URL in shared-egress metadata intentionally include the key
  for review, publication, and verification; this is the authoritative
  OpenBao-storage/publication distinction, not a claim of exclusive byte custody.
- `seal_indexnow_key_recipe` resolves an exact platform-reviewed release and
  current verified-site owner authority, inspects the bound GitHub extension,
  isolates the candidate build, and seals one root `<key>.txt` addition whose
  entire output is the key. Existing Eleventy HTML static-output passthrough is
  required; no CI, build configuration, default-branch, merge, deploy, or deletion
  route is added. Inbox review precedes 0084 eligibility; fresh MFA is required.
  The five-family technical A2 allowlist remains unchanged. Rotation seals a
  replacement proposal and immutable retirement of the old generation.
  This was an internal port with no owner creation route at the original
  checkpoint. [0168](0168-cleanup.md) adds the fresh-MFA owner API and dashboard
  action; [0144](0144-loop-wiring.md) adds exact Astro `publicDir` placement.
  Both stop at a sealed Inbox revision, not a published file. Optional deployment
  composition and live key publication remain `NOT_EXECUTED`.
- A trigger on committed 0085 `verified` receipts queues the sealed changed URL
  atomically, once per `(change, URL)`. Current 0085 supports the verified static
  homepage, so this slice never invents multi-page coverage or bulk URL discovery.
  Key-file candidates are excluded from change notification.
- `IndexNowService.dispatch_one` rechecks owner/site/recovery/epoch authority,
  obtains the OpenBao key, rejects any nonexact verified-origin URL before I/O,
  and GETs its key file through leased shared crawl egress and robots admission.
  Status 200, exact URL, `text/plain`, and exact bytes are required. Unverified,
  missing, mismatched, redirected, unreachable, or unavailable keys produce
  EC-142 skip reasons with no POST.
- A closed typed provider scope permits only bounded JSON POST to the single
  documented IndexNow endpoint. Every body field and URL is bound before I/O.
  An encrypted independent write intent precedes the primary dispatch fence.
  Immutable receipts retain exact URLs, provider status, and time. Definite 429
  and 5xx retry through the outbox at most four attempts with bounded backoff;
  4xx stops. Ambiguous writes stay unknown and restored-primary replay requires
  the surviving journal intent's matching definite-outcome primary receipt.
- All lifecycle/outbox/receipt tables force tenant/site RLS; runtime roles have
  no direct table privileges. Narrow identity functions re-resolve authority.
  Owner-only GET `/v1/sites/{site_id}/indexnow` and Connectors show key status,
  recent receipts, and skip reasons. Inbox understands exact key additions.
  Missing composition returns unavailable, not synthetic provider readiness.

## Qualification

Exact commands and counts belong in [the evidence](../evidence/0086-indexnow.json).
The complete local gate passed: 16 focused IndexNow tests, 838 database tests,
1,287 API/identity/tooling/connector tests, all remaining container/Temporal labs,
24 real OpenBao checks, and 151 dashboard tests. Static checks, documentation,
dependency validation, dashboard typecheck, and production build also passed.
`scripts/run-indexnow-tests.py` provisions disposable primary and independent
journal PostgreSQL. Provider doubles remain behind real shared egress. The real
OpenBao TLS lab checks key CAS, replay, overwrite denial, and tenant/site ACLs.
The candidate sandbox checks exact static output without network or host mounts.
The full gate includes every `scripts/run-*-tests.py` lab, OpenBao, GSC provider
boundary, API/identity/tooling/connectors, Ruff, pip, npm, and unchanged-main
gitleaks configuration. Each invocation cleans up only its own resources.

## Operational Limits And First Live Run

`dispatch_one` and key recipe sealing are explicit internal composition
entrypoints. A deployed production poller, public key-create action, certified
delivery, and live IndexNow/GitHub qualification remain unavailable. The generic
0085 HTML observer is not claimed to verify plain-text key candidates; the fresh
dedicated key GET independently establishes deployment immediately before a
notification. Accepted receipts do not establish indexing or SEO improvement.
Terminal skips are reported, not silently resubmitted unchanged URLs after setup.

For the first explicitly enabled dedicated integration run the owner supplies a
verified HTTPS test site and protected bound Eleventy HTML repository with
existing root-text passthrough; a narrowly installed GitHub App and its OpenBao
credential; a reviewed key recipe release; current MFA owner Inbox approval;
customer-controlled merge/deploy; a shipped change with an 0085 verified receipt;
robots policies permitting the exact key and provider routes; private shared
egress and encrypted artifacts; current external recovery authority; and an
independent journal with configured signing/encryption material. No customer
credentials, public lab service, or real IndexNow call is used in local evidence.

On mismatch, skip, rejected, exhausted, or unknown result preserve evidence and
escalate. Never merge, deploy, remove old files, or force a retry. Migration
downgrade is disabled; use reviewed forward recovery without deleting receipts.

## Merge-Train Verification

PR #19 is rebased onto the preceding accepted train head. Migration 0065
follows unchanged 0064; the cumulative app-table assertion is 116.
All five requested fast checks passed. The original qualification above is
historical; current counts and commands are in the `merge_train` entry of
[the evidence](../evidence/0086-indexnow.json). No production authority changed.
