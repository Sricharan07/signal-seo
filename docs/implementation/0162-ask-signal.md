# Slice 0162 - Ask Signal backend

Classification: **Security-critical** (Revision 4.0 section 19): private tenant
data, conversational interpretation, model spend and persistent memory. Based on
merge-train-4 tip `1923beb`. Backend only; no files under `apps/dashboard` changed.
See [ADR-0170](../adr/0170-ask-signal-conversations.md).

## Implemented

Seven cookie-session routes implement the shared Ask Signal v1 contract:

| Method | Path under `/v1/sites/{site_id}` |
| --- | --- |
| GET | `/assistant` |
| POST | `/assistant/conversations` |
| GET | `/assistant/conversations/{conversation_id}` |
| POST | `/assistant/conversations/{conversation_id}/messages` |
| GET | `/assistant/memory` |
| POST | `/assistant/memory` |
| POST | `/assistant/memory/{memory_id}/forget` |

All POSTs require the existing tenant CSRF token and same-origin browser mutation
proof. JSON is strict, schema version is 1, input is bounded to 12 KiB, timestamps
are UTC and responses are bounded to 256 KiB with `Cache-Control: no-store`.
Invalid or inaccessible resources return the existing error envelope, with no
resource enumeration. The normal composed browser login supplies the connection,
current recovery generation and existing configured reasoner. There is no
parallel identity implementation or new provider I/O.

Migration 0099 follows 0098 (merge train 5; written after 0092). It adds four app tables
(183 cumulative) and a private retention-scope directory. All app tables have
forced RLS on tenant/site/user, revoke-all and function-only runtime access.
All new privileged functions have a fixed qualified search path and explicit
grants. Frozen migrations and accepted specifications are unchanged.

Conversations persist, list newest first and resume per user/site. Request IDs
are idempotent; conflicting reuse is denied. A provisional unknown reply commits
before model I/O. Final replies are immutable, including unknown outcomes. A
replay cannot call the gateway or reserve budget again. Bounds are 100 active
conversations per user/site, 200 messages per conversation, 2,000 owner characters,
1,200 reply characters, 16 KiB per stored message, 180 KB total stored message
payload, four citations and three link actions per reply.
An in-flight request holds a connection-scoped advisory lock. Concurrent duplicate
POSTs receive 409; they never return a provisional reply that can subsequently
change. After connection loss, replay seals the stored unknown outcome. New
preference/context memories commit atomically with their visible `remembered`
reply; failed reply persistence rolls them back.

The context projection contains the latest weekly cycle/stage outcomes, pending
and decided exact candidate revisions/articles, PR operations, delivery receipts,
change measurements, latest strategy items/topics, visibility observations,
health observations and only current approved business facts. Allowlisted fields,
ten records per kind, 4 KiB per record and a 60 KB record packet prevent unbounded
source ingestion; no credentials or unrestricted source blobs are projected.
All 12 recent turns enter as bounded quoted snippets; full messages remain in
the conversation store. The combined packet accounts for JSON's second escaping
pass and is capped at 110 KB of gateway input, leaving room for the existing
128 KB request envelope. Suggestions are derived from real records, never sample
state. Maximum-size Unicode and quote-heavy packets have explicit regression tests.

The existing budgeted reasoner uses `owner_answers` at medium effort on the
operator-configured product model. The monthly account is shared with other
roles; member-scoped budget ports can neither manage caps nor spend through any
other model role. Reservations require an unfinalized private signal message.
Gateway uncertainty or malformed output retains the existing hold. Unconfigured
models and exhausted budgets return plain unavailable messages, not answers.

Domain code validates a closed model schema. Unknown citations are dropped;
answers must be exact, complete server-built excerpts from cited packet records,
otherwise the reply says `I don't have a record of that`. Record/site/history/
memory text is explicitly quoted untrusted data. Models have no tools, browsing
or authority. Explain/inspect are read-only; approval, pause, resume, revoke and
research interpretations return confirmation links only. Reprioritization and
unsupported operations explain the limitation without simulated success.
Only existing same-origin paths are permitted. Inbox links carry exact IDs;
existing owner flows still enforce MFA, browser proof and revision checks.

## Persistent Memory

Every answer packet retrieves the last 12 turns, its conversation's summary and
up to ten private, non-forgotten memories ranked by PostgreSQL full-text search
plus recency. Preference/context memory is neither a business fact nor authority.
After a grounded owner answer, at most two eligible exact substrings of that
owner's current message are recorded with source conversation/message IDs and
shown in `remembered`. Explicit add and forget routes are owner-only. Public
memory listing is newest first, capped at 200 and the response-size bound.

The rolling summary refreshes at turn 13, then every 12 turns, including model
unavailability and deterministic business-fact proposals. It retains eligible
whole personal owner quotes, an earlier anchor, recent context and opaque cited
record references, in at most 500 characters. It never copies raw business
statements, excludes business-fact proposal turns and creates a new `summary`
memory linked to the conversation, auditing prior summaries as superseded.
Eligible personal summary quotes can be cited as conversation memory. References
alone cannot ground a factual answer or authorize an action.
No unbudgeted summarization call or model-generated business assertion is added.

Forget creates an immutable audit row. Retrieval excludes the item, and suppresses
history/summary text containing the forgotten content. A mid-call forget discards
the answer; final reply commit also checks owner-forgetting under memory locks.
An injected memory cannot approve, change policy/grants/caps, allow merges or
enlarge authority. Explicit business statements and exact model-detected owner
business statements use existing Business Brain proposal and Inbox review;
they are not approved and are not stored as preference/context memory.

Retention is 365 days from conversation creation, cascading to its messages and
linked memories. Independent memories expire 365 days after creation. Read paths
exclude expired data and prune due private scopes on access. The scheduler-only
`SELECT control.assistant_purge()` cleans at most 100 expired private scopes per
transaction with skip-locked enumeration. Deployment must call it regularly
(for example hourly, repeating while 100 scopes were processed) using the
private scheduler role. No deployed timer is claimed by this backend slice.

## Contract Deviations And Limits

- The explicit-memory route additionally requires conservatively recognizable
  personal preferences/future context. Ambiguous, business-like or authority-like
  text receives 409 instead of being blindly stored despite valid kind/length.
- Answers are extractive, not arbitrary synthesized prose. Summaries are bounded
  quoted-owner rolling records, not an additional model operation. Neither changes
  the contract's fields or route shapes.
- Dashboard BFF/UI, live model/deployed owner journey and inactive-scope cleanup
  timer are NOT_EXECUTED here. They are not represented as available deployment
  capabilities. No model, chat or memory introduces external-write authority.

## Verification

Final counts and commands are recorded in
[0162 evidence](../evidence/0162-ask-signal.json). Overlapping focused and full
runs are not additive totals. Real disposable PostgreSQL is used; provider
doubles pass through the existing shared-egress and durable budget ledger.

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q` | 2,997 passed |
| `.venv/bin/python -m pytest tests/api/test_ask_signal.py tests/connectors/test_ask_signal_domain.py -q` | 39 passed |
| `.venv/bin/python scripts/run-ask-signal-tests.py` | 26 passed |
| `.venv/bin/python scripts/run-database-tests.py --shards 3` | 1,348 passed; final stable-source receipt `.runtime/database-tests/latest.json` |
| `.venv/bin/ruff check apps/api/src services/control_plane/src tests scripts database` | Passed |
| `.venv/bin/ruff format --check apps/api/src services/control_plane/src tests scripts database` | 654 files passed |
| `npm test` | 46 repository and 278 dashboard cases passed; 336 Markdown files, typecheck and build passed |
| `gitleaks protect --staged --config=.runtime/0162-main-gitleaks.toml --redact --no-banner` | Passed, zero leaks; unchanged main configuration also matches fetched origin/main |

Positive cases: cited grounded answer, exact replay, remembered source provenance,
cross-conversation retrieval, full-text ranking, forgetting and audit, repeated
summary refresh, scoped resume/list/read and physical retention cleanup.

Negative cases: tenant/site/user/viewer isolation, current membership/recovery
denials, runtime direct table access, injected records/memory, model href or
execution fields, oversized messages, exact action links, budget role restrictions
and conflicting request IDs. Failure cases: model unconfigured, exhausted budget,
unknown and malformed outcomes with retained holds/no second call, database
unavailability, and forgetting during model I/O or before final commit.
Real separate connections additionally qualify concurrent replay, connection
loss before dispatch and terminal unknown replay. A deliberately failing database
trigger verifies that failed reply commit rolls back newly remembered memory.

The first fast suite exposed the required OpenAPI mutation allowlist addition.
The first full database run exposed a pre-existing global Business Brain audit
count assumption: earlier Ask Signal tests legitimately created audited facts.
The assertion now counts the fixture's tenant, still requiring its exact four
audit records. Development failures are not passing qualification evidence.
Two invocation-owned final-lab attempts were cancelled for further review edits
and cleaned by their runner; they are not counted as passed. A historical full
database run passed 1,343 cases before the additional concurrency/atomic-memory
and provider-independent-summary regressions. Final-tip qualification is below.

The full gate and Temporal were not run, as directed. Labs clean only their own
randomly named disposable resources; no shared containers or images were stopped
or pruned. No dependencies, credentials or production enablement are added.

An additional broad `ruff format --check .` experiment flagged three immutable
specification Markdown files' embedded code examples. They were not modified;
the normal scoped Python Ruff check/format commands pass.
