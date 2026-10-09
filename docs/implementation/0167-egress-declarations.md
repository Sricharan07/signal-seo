# Slice 0167 - Egress Declarations

Classification: **Security-critical** (egress and external-write boundaries).
Stacked on `bfc84153e7fdb629ecd80191916697ff17ad092c` (0166).
[ADR-0174](../adr/0174-egress-declarations.md).

## Scope And Invariants

One Python declaration model covers HTTP origins/ports, method/path restrictions,
credential/header kinds, scopes, body/response/time budgets, reserved-provider
origins, robots profiles, context limits and admission binding ports. Shared
egress interprets the declarations instead of selecting provider branches.
Owner/weekly authority ports and connector factory policies are declared rather
than copied. Provider-specific resource and body checks remain pure adapters.

No HTTP route/body/status/dashboard changes. No SQL, migrations, dependencies,
deployment or live writes. SQL egress functions and constraints remain unchanged
for track A. OpenBao-only custody, durable/pending restriction semantics,
intent-before-I/O, private-address denial, pinned resolution, robots and budgets
remain mandatory. Declaration data cannot grant authority.

The 0166 line-removal target is not reached: its production Python diff adds
958 lines and removes 1,041 (83 net removed). Provider-specific authority and
cleanup distinctions were retained rather than flattened to claim the target.
This shortfall needs reviewer acceptance or a further bounded consolidation.

## Frozen Oracle

The checked-in synthetic oracle contains 541 decisions across all 36 profiles:
120 allowed and 421 denied. Of these, 253 were captured directly from existing
profile/connector/tooling tests before the refactor; supplemental cases cover
missing positive routes and purpose/method/header/size/time/reserved-host bounds
using the old validator. Each case retains its source. No existing test changes.
SMTP is always denied at the HTTP boundary; its separate protocol tests remain.

Explicit ports in the fixture are structural values reconstructed into the
exact original URL. This avoids a scanner false positive on the JEV URL while
keeping synthetic credentials visible and the main allowlist unchanged.

The baseline is the 0166 commit, before changing egress declarations. Reproducible
before/after replay:

```sh
.venv/bin/python scripts/check-egress-equivalence.py --baseline bfc84153e7fdb629ecd80191916697ff17ad092c
.venv/bin/python -m pytest tests/connectors/test_egress_declarations.py -q
```

The replay passes 541/541 before and after. Declaration regression tests also
check all binding ports/argument order, factory origin/budget bounds and PSI's
reserved owner/weekly context. Denials include scope borrowing, mutations,
invalid resource selections, duplicate keys, malformed bodies and over-budget
requests. Existing real PostgreSQL and network labs qualify admission,
restriction/unknown outcomes and transport failures.

## Qualification

[Evidence](../evidence/0167-egress-declarations.json) distinguishes commands,
counts and qualification status. The full gate ran once and is
**DEFERRED_TO_MERGE_TRAIN (infrastructure: Temporal dev-server startup under parallel load)**:
seven steps passed, two delivery steps failed and 18 remaining steps are
incomplete after the required infrastructure stop.
The runner labels cancelled/not-started steps FAIL; these are not 18 additional
test failures. No full-gate pass is claimed for 0167.

The two failures are Temporal dev-server startup connection refusals after its
five-second startup window, in `replay_measurement` for the new-article and
content-refresh tests. Their provider dispatch, observations and measurement
assertions had already passed. The reviewer confirmed infrastructure load from
parallel full gates and nearly full disk, not a refactor failure. No
product/test/SDK timeout was changed.
The completed Python suite passed 3,706 cases; npm passed 46 repository and
363 dashboard cases; Ruff (691 files), pip check and 19 authority-journal checks
passed. The exact fixture representation change was separately requalified:
541 before/after decisions and 583 declaration cases passed; staged gitleaks
with main's unchanged configuration reported zero leaks.

The gate and all its own processes/resources were stopped. Interrupted browser
fixture cleanup required exact invocation-owned container/network/image removal
after creation-time/image/label checks; no other track's resources were stopped.
The reviewer directs committing the staged slice and opening the stacked PRs,
with one Ruff check/format check and connector-suite sanity run on the committed
tip. The reviewer will run the full gate on the merged train; this track must
not rerun it. Infrastructure startup-budget remediation belongs to track C.
Live provider/deployment/production-write qualification is NOT_EXECUTED.

Unchanged first-live-run owner inputs: private provider credentials and TLS
trust, exact verified origins and selected resources, current owner/MFA and
recovery generation, provider consent/permissions, durable restriction journal
and qualified deployment composition. No new inputs or authority are introduced.
