# Slice 0100 - Google Docs Sync for the Business Brain

Classification: Security-critical (OAuth, secrets, egress, tenancy, revocation).
Contract: Revision 4.0 sections 10/12 and EC-135; Revision 3.2 connector lifecycle;
the 0072 encrypted/screened document boundary and 0073 proposed-fact boundary.
ADR-0112 records the Google Docs boundary and the authorized Notion deferral.

## Implemented Boundary

Migration `0074_docs_sync` follows unchanged `0073` in the accepted merge train.
Forced-RLS, function-only attempts, bindings,
immutable selected sources, and immutable source-version/withdrawal events retain
tenant/site scope. No existing migration or protected specification changes.

An owner with current MFA and a verified site starts redirect-based Google Picker
consent. The existing Google OAuth/PKCE and OpenBao protocols are parameterized,
not forked: GSC defaults remain unchanged and the accepted GA4 scope is retained.
Consent is offline, non-incremental, S256, and `drive.file` only. The code is
exchanged server-side; its returned scope must match exactly. Each refresh must
also explicitly return that exact scope. Missing, broad, or ambiguous scope is
refused. No access or refresh token is sent to browser JavaScript or stored in
PostgreSQL. Client credentials, one-use verifiers and refresh tokens reside in
the isolated `signal-google-docs` OpenBao KV-v2 mount, with CAS rotation and
permanent metadata destruction. Recovery generation is rechecked across async
boundaries; current owner, MFA, origin and attempt/binding authority are rechecked.

Google's [redirect-based Picker](https://developers.google.com/workspace/drive/picker/guides/desktop-mobile-picker)
returns `picked_file_ids` in the code callback. Only 1-20 unique, path-safe Doc IDs
explicitly confirmed by that owner become the immutable manifest. Drive metadata
must identify a Google document authorized to this app. Signal creates no files,
lists no folder contents, reads no unselected file, and writes nothing to Google.
`drive.file` itself is not a provider-enforced read-only scope: local closed GET
profiles enforce the contract's no-write boundary.

The only connector endpoints invoked are bounded POST
`https://oauth2.googleapis.com/token`, GET
`https://www.googleapis.com/drive/v3/files/{picked-id}` with the exact six metadata
fields, and GET that path's `/export?mimeType=text/plain`. Metadata is at most
16 KiB; export is at most 512 KiB; each call is at most ten seconds, with no
redirect, alternate Google API, extra query, write method, or borrowed token.
All requests use the existing durable shared gateway, public-address/pinning,
robots and origin admission. Disconnect records a local restriction/outbox event
before destroying the OpenBao token. It makes no remote revocation request:
POST is reserved for token exchange. Independent denial-journal replay prevents
a restored old binding from reading. Pending journal acknowledgement is explicit.

## Versions and Facts

Sync is owner/MFA initiated and serialized per binding. Metadata before and after
the export must agree, preventing mixed-revision imports. A changed provider
version or modified time atomically registers a new encrypted 0072 document and
its source-version event; failed registration rolls both back. Prior document
versions and facts retain provenance. Metadata-only unchanged sync is idempotent.
All text runs through the existing extraction, secret and injection screening;
secret-bearing text cannot be read or supplied to the Business Brain model.

When the existing `BusinessBrainExtractor` is composed, sync proposes facts using
exact screened document IDs and bounded 24,000-character ranges. Its existing
durable extraction receipts make retries/replays idempotent; unknown or failed
model outcomes are not blindly retried. No fact is auto-approved. Without that
model composition, document sync still works and fact extraction is visibly
unavailable. The owner can inspect extraction receipts and review facts through
the existing Business Brain API/dashboard.

Explicit removal/unsharing appends a withdrawal event; quota/ambiguous failures
do not fabricate withdrawals. A withdrawn source cannot be read or extracted
again. All derived facts remain in history with `source_review_required`; they
are excluded from both approved read models and cannot be newly approved from
withdrawn evidence. Nothing is silently deleted. Owner correction/removal still
uses the existing explicit, audited fact commands.

## Notion Follow-up

Notion has only an unavailable read projection, with this exact reason:
"Notion read-only capability cannot be verified from provider documentation; pending live qualification".
There is no Notion OAuth, bind, page read, sync, secret mount or egress profile.
No qualification helper is shipped in this slice.

The owner must create a dedicated integration with only **Read content** enabled
and share one synthetic test page. An operator, outside Signal's binding path,
calls exactly POST `https://api.notion.com/v1/oauth/introspect` once using
client-credential Basic auth and JSON containing only that test token, with
bounded sizes/time. Retain only sanitized `active` and the verbatim `scope`
string, never the token, client secret, headers, page text or raw response.
Compare it to the configured read-only integration and verify it distinguishes
write-enabled integrations. The [provider introspection reference](https://developers.notion.com/reference/introspect-token)
and [official SDK types](https://github.com/makenotion/notion-sdk-js/blob/main/src/api-endpoints/oauth.ts)
currently provide no documented read/write scope mapping. Do not invent one.
Once qualified sanitized evidence exists, a small follow-up slice encodes that
observed read-only scope value as the sole accepted value, verifies it at bind
and before every sync, and enables Notion using the same document/fact pipeline.
Unavailable or ambiguous introspection, inactive tokens, or capability changes
must still fail closed. Live Notion and that qualification are `NOT_EXECUTED`.

## Owner Inputs and Limits

First live Google qualification needs a dedicated web OAuth client and exact
HTTPS dashboard callback, configured Picker access, current owner/MFA/verified
site, one synthetic picked Google Doc plus an unpicked negative, OpenBao KV-v2
client/connector ACLs, external recovery authority and denial journal, a
qualified per-origin shared-egress composition, and the existing encrypted
artifact store/key. Optional automatic fact proposals also need the existing
Business Brain extractor/model/decision-recorder composition. Inject
`ComposedDocsGateway` through `create_app(browser_docs=...)`; absent composition
returns unavailable, not simulated readiness. Production composition, live
Google consent/export/unsharing, live model calls and Notion remain
`NOT_EXECUTED`; local provider doubles are not live qualification.

## Verification

Full commands, pass/fail counts and immutable local evidence are in
[the evidence](../evidence/0100-google-docs-sync.json). Existing GSC tests are
unchanged. Accepted PR17 GA4 OAuth/protocol tests run unchanged from a read-only
archive with the shared Google helpers overlaid; the accepted branch is untouched.
All lab-created resources are invocation-owned and cleaned up.

The full gate passes 850 PostgreSQL, 1,302 API/identity/tooling/connector,
119 other real-infrastructure lab, and 183 repository/dashboard test cases:
2,454 passed, zero failed. The unchanged GA4 compatibility archive adds 32
passed, zero failed. Formatting, lint, dependency, build, protected-document,
provider-boundary and staged secret checks also pass. The PR records the final
commit-range secret scan, executed after the commit and before push using the
unchanged main configuration. Live provider/model and production qualification
remain `NOT_EXECUTED`.

The compatibility archive is generated without checking out or modifying PR17:

```sh
mkdir -p .runtime/0100-ga4-regression
git archive slice/0097-ga4 services/control_plane/src/signal_core tests/connectors/test_ga4_protocol.py pyproject.toml | tar -x -C .runtime/0100-ga4-regression
cp services/control_plane/src/signal_core/gsc_oauth.py services/control_plane/src/signal_core/gsc_secrets.py .runtime/0100-ga4-regression/services/control_plane/src/signal_core/
env PYTHONPATH=.runtime/0100-ga4-regression/services/control_plane/src .venv/bin/pytest .runtime/0100-ga4-regression/tests/connectors/test_ga4_protocol.py -q
```

## UI Direction Contract

Mode: Operate; an ordinary extension of the incumbent Operations Ledger.
Thesis: current selected-document state and owner actions, not a marketing page.
Own-world: existing cool ground, neutral ink, hairlines and icon/text commands.
Story: connect through Picker, inspect immutable imported versions, sync, review
withdrawn evidence, and disconnect; Notion remains honestly unavailable.
First viewport: compact Google Docs heading/state, adjacent owner commands,
unframed source rows and a separate unavailable Notion band. Mobile wraps IDs
and commands without changing authority or hiding source status.
Form: existing connector grammar; no new visual world or raster assets.
Finish: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0074` follows `0073`; 142 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2067 API/identity/tooling/connectors, 994 PostgreSQL,
43 repository and 218 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
