# Slice 0137: Observed Learning and Page Decay

## Merge Train Integration

The workload-port integration is security-critical composition of existing
authority. The measurement projection follows the recorded technical/editorial
foreign-key paths. Owner-editorial article/refresh measurements retain their
exact work type and the recorded authority kind as a cohort key, not a fabricated
recipe release. Refreshes retain their real baseline. New pages expose their
explicit null pre-change window and remain excluded from comparative effectiveness
and priority bonuses; post-launch observations are not a before/after effect.

The weekly strategy source port wraps its already guarded base through the same
new learning projection, rather than retaining the pre-learning packet copied
by 0135. Stage, current standing authority, generation and tenant/site checks are
retained; its old base is not directly executable by workflow callers. No owner
session, new grant, publishing port, cross-site learning or causal claim is added.

Classification: **Product**. Base `7754746`. This reads existing owner-authorized
site-local strategy/0094 records, computes deterministic observations, and uses
the existing unaccepted brief-proposal boundary. No new authority, role, provider,
credential, network call, external write, dispatch or service is added. Revision
4.0 sections 13, 16, 19 and Revision 3.2 sections 12, 13, 22 apply; INV-018 is
preserved. The accepted specifications are unchanged.

Current state: **implemented; full local qualification passed.** Live
providers, deployed runtime and customer efficacy are **NOT_EXECUTED**. No
production readiness or write authority is claimed.

## Boundary

Migration `0088_observed_learning` follows `0087`. No tables are added;
the cumulative app-table count remains 173. It wraps the existing source function, revoking
API access to the preserved private base function. The existing current-owner
check, forced-RLS site scope, exact-source recording recheck, immutable snapshots,
historical reads and proposal decisions remain unchanged. Measurement evidence
IDs deterministically identify tenant/site/operation/horizon/sequence; both
horizons remain independently inspectable without evidence-key collisions.

The extended source packet pins latest measurement observations and current-bound
daily GSC page generations, exact coverage and a source-calendar as-of date.
Generations ending within the last 14 months are eligible. Old or revoked GSC
bindings cannot contribute decay evidence. Historical measured records remain
inspectable by their site's current owner. Source changes during a refresh reject
the stale snapshot through the existing 0077 boundary. Size limits remain closed:
an oversized packet is unavailable, not truncated into an estimated result.

## Observed Effectiveness

[ADR-0144](../adr/0144-site-local-observed-effectiveness.md) defines the fixed
sample, weighting, shrinkage and bounded-factor math. Weighted click, impression,
CTR and position deltas are separate at 28 and 90 days, by work type and exact
recipe on this site only. Latest non-measured states exclude the operation/horizon
rather than reusing an older measured observation. Small effective n (<3) has
neutral priority factor 1. Five neutral prior samples shrink larger groups.
Recorded confounders reduce each affected measurement's weight.

Every priority input includes an explained observed-effectiveness factor and
measurement evidence. Multiplying existing impact/confidence/inverse effort by
0.8-1.2 cannot bypass policy, acceptance, grants, caps or the autonomy gate. No
causal, revenue, conversion or predicted ranking claim is made.

## Declining Pages

[ADR-0145](../adr/0145-evidence-only-seasonal-page-decay.md) defines windows,
minimum volume and the robust fixed screening heuristic. A recent 28-day window
compares prior 28 days and, with sufficient usable history, year-over-year.
Year-over-year is preferred; prior-period-only results show "seasonality possible".
Festival/calendar shifts remain an alternative explanation even with year-over-year.
All 28 explicit page-days must exist per window; provider lag, missing rows,
unavailable generations and verbatim incomplete coverage stay visible.

Pages still in overlapping 90-day Signal measurement windows are excluded. A
decline creates a 0077 Content Writer refresh brief proposal action, not an
accepted brief. It requires existing crawled source and current approved facts.
Absent grounding is visibly unavailable. Same-page existing technical findings
are linked in the rationale, not fabricated from traffic. The owner chooses
whether to create the proposal; Content Writer still requires its own acceptance.

Overview and Analytics expose sample sizes, weighted n, signed observed deltas,
factor and evidence; the declining-pages list exposes comparison windows/counts,
seasonality, full coverage, partial comparisons and excluded pages. Original
strategy item IDs and decisions are preserved. Historical snapshots without the
optional versioned learning extension remain readable and show unavailable.

## Verification

Focused domain: `.venv/bin/python -m pytest tests/connectors/test_observed_learning.py -q`
(23 passed). Focused PostgreSQL learning/strategy: 13 passed using the existing
`database_lab.isolated_postgres`/`provision` harness and `alembic upgrade head`.
Full runnable gate (3,280 checks passed, zero failed):

| Command | Result |
| --- | --- |
| `.venv/bin/python scripts/run-database-tests.py` | 968 passed |
| `.venv/bin/python scripts/run-autonomy-delivery-tests.py` | 40 passed; unsharded |
| `.venv/bin/python scripts/run-authority-journal-tests.py` | 15 passed |
| `.venv/bin/python scripts/run-candidate-sandbox-tests.py` | 11 passed |
| `.venv/bin/python scripts/run-consumer-tests.py` | 3 passed |
| `.venv/bin/python scripts/run-crawler-network-tests.py` | 14 passed |
| `.venv/bin/python scripts/run-indexnow-tests.py` | 17 passed |
| `.venv/bin/python scripts/run-page-attempt-tests.py` | 1 passed |
| `.venv/bin/python scripts/run-temporal-tests.py` | 10 passed |
| `.venv/bin/python scripts/run-workflow-consumer-image-tests.py` | 6 passed |
| `.venv/bin/python scripts/openbao_lab.py` | 34 passed |
| `.venv/bin/python scripts/check-gsc-provider-boundary.py` | Exit 0 |
| `.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q` | 1,915 passed |
| `.venv/bin/ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests` | Passed |
| `.venv/bin/ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests` | 480 files unchanged |
| `.venv/bin/python -m pip check` | No broken requirements |
| `npm test` | 43 repository and 203 dashboard passed; 267 Markdown files, typecheck/build passed |
| `gitleaks git --config .gitleaks.toml --log-opts="main..HEAD" --redact .` | Zero leaks; configuration byte-identical to main |

See [evidence](../evidence/0137-learning-and-decay.json) for source digests,
commands and qualification scope. Real PostgreSQL and Temporal are disposable;
provider behavior uses existing gateway test doubles. All owned lab resources
were cleaned up. The disk-full interruption required infrastructure repair; the
resumed full gate supersedes the earlier failed run. Two slice-test fixture
isolation/clock errors were fixed, and the earlier pre-existing Temporal startup
failure passed on rerun. No test, threshold, safety gate or allowlist was weakened.
Changed sources, JSON, migration and PNG endings were checked for truncation;
all were intact. Accepted specification hashes passed the repository check.

Synthetic visual captures render the actual component and existing CSS at desktop
1440x980 and mobile 390x844. Tables scroll within their containers; document width
matches viewport. These are not authenticated customer or hydrated-flow evidence.
The stale generated cache from the waiting branch was preserved under `.runtime`
before Next typecheck/build; that branch and its commits were not changed.

## First Live Run

No new credentials are required. The owner needs an existing verified site,
current read-only GSC binding and imported web page/date generations covering
both comparison windows. Thirteen months of usable history enables seasonal
comparison; incomplete page-days remain partial. Existing 0094 records at 28/90
days need their real delivery/live-verification and pre-change baseline evidence.
Refresh the site's baseline in the existing owner dashboard. Current approved
Brain facts and crawled source are required to create a Content Writer proposal.
All existing delivery/grant/qualification requirements still apply separately.

No live GSC/Bing/GitHub/Jev calls, deployed worker changes, new schedule, auto-
acceptance, cross-site learning, causal experiment, or production write is qualified.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0088` follows `0087`; 173 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2757 API/identity/tooling/connector
cases, 1215 PostgreSQL suite cases,
46 repository and 258 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.

The owned real PostgreSQL/Temporal delivery lab passed three focused cases:
new-article and refresh measurement/learning projections, plus the unchanged
technical baseline/horizon/report path. Explicit `new_page` baselines have no
fabricated pre-change window and remain excluded from comparative learning.
Receipt: `/tmp/signal-merge-train/train3-37-measurement-lab/00.log`.
