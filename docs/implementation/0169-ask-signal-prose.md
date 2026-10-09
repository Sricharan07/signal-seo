# 0169 - Ask Signal grounded prose

Classification: **Security-critical**, Revision 4.0 section 19 (claim grounding,
private records and model spend). Owner decision: 2026-10-06. Based on main
`b7a084f`; [ADR-0176](../adr/0176-ask-signal-grounded-prose.md).

## Boundary

The internal model answer is a bounded list of sentences, each with text of at
most 400 characters and packet reference strings. The writer's shared anti-slop
prefix requests concrete, plain owner-addressed prose. Records, question, history,
summary and memory remain quoted untrusted data. Actions, memory proposals and
business-fact proposals retain their existing closed contracts.

Each candidate uses Content Writer `sentence` and `grounding_report`, not a second
editorial grounding engine. Ask Signal adds record-specific factual-token checks:
numbers, percentages, money, dates, URLs/paths, quoted strings and record-derived
proper names must appear in the union of the cited excerpts and structured data.
Unknown references are removed; unknown-only sentences are discarded. Uncited
connectives use a conservative discourse-only allowlist. Business claims require
cited current approved facts. The writer's sensitive exactness remains intact;
customer-named approved statements are treated as sensitive, not freely paraphrased.

One batched second call checks all deterministic survivors. It uses the Content
Writer entailment-question approach, with each sentence's cited records only and
approved facts separately identified. Work records support work/status only;
memory can support quoted personal context, never business facts or authority.
The response schema requires exactly one boolean for each sentence. False,
uncertain or missing entailment never exposes generated prose. The shared Noul
diagnostic asks about unsupported content: its false criterion means complete
coverage. Instructions and the response schema explicitly map that inverse
polarity to the positive `sentences[index]` entailment boolean. Both calls use
`owner_answers` and the operator-configured model, existing OpenBao credentials,
shared egress and the real monthly reservation/dispatch/settlement ports.

When no prose survives, the answer uses complete existing server-built excerpts
from known cited references. It never guesses an unrelated record. With no usable
excerpt it says `I don't have a record of that`. Joining retains whole sentences,
at most four actually-used citations in first-use order and at most 1,200
characters. Verification failure uses extractive fallback plus an explicit
unavailable/unknown status; unresolved holds remain charged to the available
budget and replay makes no further call. No retry is added for unknown outcomes.

The API remains v1: `message.text` is joined text and `citations` has the existing
kind/id/label/href objects. No dashboard change, provider, dependency, external
write, publishing authority or standing authorization is added. Existing private
RLS, idempotency, memory source/atomic persistence, mid-call forget checks,
365-day retention and recovery semantics remain unchanged.

## Migration 0103

0103 (written as 0102) follows 0170's 0102 and adds no table or backfill (183 app tables unchanged).
The old ports require the pending reply's ID, so a second independently budgeted
call is impossible without this amendment. A private migrator-owned helper admits
only the original pending private reply ID or its one deterministic verification
ID, after the original call has a confirmed usage receipt. Forced private RLS
and current-user checks bind it to the admitted tenant/site/user. Finalization
denies both IDs. All existing permission, role, generation, cap, digest, dispatch
deduplication and settlement checks remain in the shared SQL bodies.

The deterministic ID has the UUIDv4 shape required by shared egress. This is a
replay identity, not permission. The migration asserts that the old guards exist
before replacement, runs transactionally, and grants no public helper execution.
Failure rolls back to 0101; destructive downgrade remains disabled. Apply the
migration before deploying the changed service. Frozen migrations/specs are untouched.

## Examples

Test: `test_grounded_prose_budgeted_entailment_fallback_and_replay`.

- Before: `Synthetic startups are the approved audience.`
- After: `Your audience is Synthetic startups.`
- Invented: `Your audience includes 999 startups.` is discarded, with the exact
  approved audience excerpt used instead.

Test: `test_natural_grounded_prose_and_ordered_used_citations`.

- Before: `The recorded article "Home update" is pending. Recorded summary: "Recorded on 2026-10-06 with 12 clicks from Acme at https://example.invalid/plans". Recorded page: "/plans".`
- After: `Here is what I found. Your "Home update" article is pending.`

## Verification

Commands/counts are in [evidence](../evidence/0169-ask-signal-prose.json).
Development runs are not qualification. Provider doubles run behind shared egress;
PostgreSQL checks use invocation-owned disposable real PostgreSQL. No other track's
resource is stopped.

The full gate was invoked **once**, with 26 passed steps and one failed step
(Ask Signal: 20 passed, 15 failed). It exposed an incorrect field lookup in the
shared serialized Noul question. The lookup was corrected, a direct question-shape
regression added, and the affected checks rerun. The gate's later database step
ran with that correction and passed 1,518 cases. Final review also made the shared
diagnostic's inverse boolean polarity explicit; affected checks were rerun again.
The original gate result remains failed, not rewritten as green. Integrated
final-tip qualification remains for the merge train; there is no release authority.

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/connectors/test_ask_signal_domain.py tests/api/test_ask_signal.py -q` | 60 passed, 0 failed |
| `.venv/bin/python scripts/run-ask-signal-tests.py` (corrected rerun) | 35 passed, 0 failed |
| `.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q` (corrected rerun) | 3,843 passed, 0 failed |
| `.venv/bin/python scripts/run-full-gate.py --range main..HEAD --jobs 3 --docker-jobs 1 --database-shards 3` | 26 passed steps, 1 failed step; all 27 executed |
| Gate database (`--shards 3 --repeat-brain 5`) | 1,518 passed, 0 failed, including 20 repeated Brain checks |
| Gate `npm test` | 412 passed, 0 failed; typecheck/build passed |
| Final Ruff check and format check across API, control plane, migrations, crawler, scripts and all tests | Passed; 707 files already formatted |
| Gate `.venv/bin/python -m pip check` and GSC boundary | Passed |

Every `scripts/run-*-tests.py` lab, OpenBao and Keycloak ran in the gate. The evidence
lists individual commands and counts, preserving checks versus test-case units.
The earlier standalone database development run had seven `RobotsSnapshotUnavailable`
failures; migration-0101/head isolation passed 10 cases each, and the final gate
database run passed all cases without weakening fixtures or gates. The gate's
secret scan preceded commits (empty range); explicit staged-patch scans and an
actual `main..HEAD` scan with main's unchanged configuration supplement it before
PR publication.

Positive coverage: prose, record-union support, discourse-only connective, ordered
used citations, configured product model, independent settled calls and exact replay.
Negative coverage: invented tokens, wrong/unknown references, business assertions
without approved facts, sensitive paraphrases, injected records/memory, arbitrary
or premature verification identities and finalized-message spending.
Failure coverage: entailment no/unavailable/timeout/malformed, exhausted second-call
budget, retained unresolved holds, forget during I/O and failed atomic reply commit.

Live product model/provider quality and dedicated deployed owner journey are
**NOT_EXECUTED**. First live qualification needs operator-provisioned model
credentials and price snapshot, a private shared-egress composition for the exact
site, a human-approved monthly cap, and a current authorized owner session. No
customer credential or new provider authority is supplied by this slice.
