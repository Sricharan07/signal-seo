# 0170: Fail-closed Permission And Lab Isolation

Classification: **security-critical**. Base: `b7a084f`. Decision:
[ADR-0177](../adr/0177-fail-closed-permission-and-lab-isolation.md).
This implements Revision 4.0 sections 4, 6, 9 and 19 and the unchanged Revision
3.2 sections 7.9, 8.3, 11.3, 18 and 27.2. It grants no production authority.

## Permission Boundary

Migration `0102_fail_closed_permission` (written as 0103) follows unchanged 0101
at this base and the owner renumbers at merge. No existing migration or protected
specification is edited. There are no new tables or data rewrites.

The flag-bearing predicate is dropped. Three private, total overloads distinguish
an outcome-only check, an authority context with a known role, and an exact
owner/workload requirement. NULL and unknown outcomes deny. NULL or unknown roles
deny on authority-bearing records; NULL or unknown required roles cannot grant.
Role-free scope records and derived permits have no invented role requirement.
All 95 calls across 51 function definitions are forward-replaced. Authorized
contexts with malformed roles are reduced to `authorization_denied`, avoiding a
misleading `authorized` return from an outcome-denial branch. The non-Astro PR
eligibility delegation is checked before calling its legacy implementation.

Distinct owner sessions and worker handles, admission, stage/resource fencing,
standing grants, caps, recovery, RLS, immutable receipts and runtime ACLs remain
unchanged. No external write, merge, deploy, default-branch push, deletion, CI
edit or repository-secret read is added.

## Lab Diagnosis And Isolation

The browser fixture's `is_global` sampler accepts multicast. The production
validator correctly rejects it. This is an entropy defect, not evidence that
concurrent crawler DNS overwrote a browser resolver. A single opt-in multicast
draw reproduces it without changing the egress check. Separately, concurrent
crawler and page-attempt runs collide because their differently named networks
claim the same fixed subnet. A Docker overlap rejection reproduces that collision.

`scripts/lab_network.py` now screens every address through the product validator
and atomically claims an internal /29 with Docker IPAM. Collision retries are
bounded and restricted to overlap errors. Other errors fail visibly. Browser,
crawler and page-attempt resolvers receive only their own allocation. No host
ports or mounts are added; masquerading is off and gateway mode is isolated.
Private worker/proxy network names, images, builders and ephemeral loopback
database ports were already invocation-owned. Candidate builds still have no
network; their final cleanup query now selects only their invocation label.
The gate's fixed-subnet serialization is removed, not used to hide the defect.

The original baseline attempt stalled in the host Docker credential helper
before fixture setup. An invocation-private anonymous Docker config for public
pinned images bypassed that infrastructure stall without changing host
credentials or Docker resources belonging to another track. That interrupted
attempt is not qualification evidence.

## Qualification

Real PostgreSQL focused permission checks: **63 passed, zero failed/skipped**.
They cover a 350-combination truth table, all 48 formerly nullable entry points
across 23 cores/helpers, independent 0101 authorized-decision equivalence,
the existing 0100 divergence oracle, unchanged operation bodies/ACLs, private
execution denials, injected admission failures and transactional migration failure.
Eight migration-head assertions advance to 0102 (written as 0103); no safety assertion is weakened.

Concurrent reproduction on the old fixtures: browser **22 passed, 13 failed**
with public-destination rejection/incomplete execution; crawler **14 passed**;
page-attempt failed before collection on the overlapping subnet; candidate
**34 passed**. This deliberately failing run is not qualification. After the fix,
the same four concurrent labs pass **36 + 14 + 1 + 34 = 85 tests**, zero
failures/skips. The browser receives the identical initial multicast draw and
adds an actual IPAM collision/retry with four disjoint networks held at once.
Twenty-two focused allocator/ownership/runner checks also pass.

The single final full gate passes **27/27 steps**, zero failed/unexecuted steps,
in **2,820.77 seconds**. Receipts show 3,831 API/identity/tooling/connector cases,
412 npm cases, 1,557 database cases (three shards and five brain repetitions),
125 delivery, 19 authority-journal and 26 Ask Signal cases. Other lab counts are:
AI answers 61, browser 36, candidate 34, consumer 3, crawler 14, IndexNow 21,
page-attempt 1, self-host 16, team invitation 150, Temporal 11, Webflow 60,
WordPress draft 35, workflow-consumer image 6, OpenBao 45 and Keycloak 5.
There are zero failed/skipped cases. Log footers supply the case counts where
the gate metadata counts one successful script check rather than pytest cases.
Ruff lint, format (712 files), pip consistency and GSC boundary pass. No
invocation-owned lab resources or processes remain; foreign resources are untouched.
The retained gate artifact is `.runtime/full-gate/run-0t94a7q_/summary.json`;
its SHA-256 and concurrent-run hashes are in the evidence record.

Final review after that gate tightened browser cleanup for lost Docker creation
acknowledgements. Exact label/name queries recover owned resources without
touching a foreign run; query/removal failures remain explicit. Eight new
focused cleanup cases pass within the 22-case tooling run. Only the affected
browser/crawler pair was rerun concurrently: **36 + 14 = 50 passed**, zero
failed/skipped, with the same multicast draw. Ruff lint/format were rechecked.
The full gate was not repeated. This follow-up changes lab cleanup only, not
the permission migration, address allocation or product controls. Its retained
artifact is `.runtime/0170-cleanup-after/summary.json`. The same runnable
orchestrator below, restricted to those two names with two jobs/Docker slots,
reproduces the follow-up invocation.

Individual commands are:

```sh
PYTHONPATH="$PWD" PYTEST_PLUGINS=tests.tooling.browser_fixture_reproduction \
  .venv/bin/python scripts/run-browser-worker-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
env DOCKER_CONFIG="$PWD/.runtime/0170-docker" \
  .venv/bin/python scripts/run-full-gate.py --range main..HEAD
```

The concurrent run uses the existing bounded gate orchestrator, four jobs and
three Docker slots, with invocation-private work/report roots. This is a runnable
equivalent of the qualification invocation (its original report is
`.runtime/0170-after/summary.json`):

```sh
.venv/bin/python - <<'PY'
import signal, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, "scripts")
from full_gate import Step, run_gate
root = Path.cwd()
base = root / ".runtime/lab-isolation"
base.mkdir(parents=True, exist_ok=True)
directory = Path(tempfile.mkdtemp(prefix="run-", dir=base))
cancel = threading.Event()
signal.signal(signal.SIGINT, lambda *_: cancel.set())
signal.signal(signal.SIGTERM, lambda *_: cancel.set())
steps = []
for name in ("browser-worker", "crawler-network", "page-attempt", "candidate-sandbox"):
    prefix = ("env", f"PYTHONPATH={root}",
              "PYTEST_PLUGINS=tests.tooling.browser_fixture_reproduction") if name == "browser-worker" else ()
    report = "page-attempt-tests/latest.xml" if name == "page-attempt" else f"{name}/latest.xml"
    steps.append(Step(name, prefix + (sys.executable, "scripts/gate_lab_worker.py",
                 f"scripts/run-{name}-tests.py"), docker=True, report=report))
code, report = run_gate(steps, directory, root, jobs=4, docker_jobs=3, cancel=cancel)
print(directory / "summary.json")
sys.exit(code)
PY
```

The full gate includes normal database sharding, both delivery shards,
authority-journal, Ask Signal, every other run-*-tests lab, OpenBao, Keycloak,
GSC boundary, API/identity/tooling/connector pytest, Ruff lint and format over
all tests including delivery/page_attempt, pip consistency, npm and main-config
gitleaks. Docker-backed qualification used the invocation-private anonymous
configuration described above. The gate's `main..HEAD` scan ran at the unchanged
base; the staged snapshot scan covers the new files. The actual commit range is
scanned after commit before push, with its receipt in the PR. Main's configuration
is byte-identical and its allowlist is not widened.

Evidence: [0170](../evidence/0170-fail-closed-permission-and-lab-isolation.json).
Live providers and deployed production composition: **NOT_EXECUTED**. A first
live run requires separate owner authorization, private deployment, dedicated
test identities and provider credentials, exact owner-selected site/repository
bindings and fresh grants; this slice enables none of them.
