"""Local verification orchestration only; no container discovery or global cleanup."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_PATHS = (
    "apps/api",
    "services/control_plane",
    "database/migrations",
    "deploy/crawler-network",
    "scripts",
    "tests",
)


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    timeout: float = 3600
    docker: bool = False
    report: str | None = None
    exclusive: str | None = None


def plan(root: Path, python: str, revision_range: str, config: Path, shards: int) -> list[Step]:
    steps = [
        Step("npm", ("npm", "test"), 600),
        Step(
            "pytest",
            (
                python,
                "-m",
                "pytest",
                "tests/api",
                "tests/identity",
                "tests/tooling",
                "tests/connectors",
                "-q",
                "--junitxml={work}/pytest.xml",
            ),
            600,
            report="pytest.xml",
        ),
        Step("ruff-check", (python, "-m", "ruff", "check", *STATIC_PATHS), 120),
        Step("ruff-format", (python, "-m", "ruff", "format", "--check", *STATIC_PATHS), 120),
        Step("pip-check", (python, "-m", "pip", "check"), 120),
    ]
    # Longest jobs first. Delivery uses its existing complementary CI shards.
    labs = sorted(root.glob("scripts/run-*-tests.py"))
    labs.sort(key=lambda p: (p.name != "run-autonomy-delivery-tests.py", p.name))
    reports = {
        "authority-journal": None,
        "autonomy-delivery": "autonomy-delivery/latest.xml",
        "browser-worker": "browser-worker/latest.xml",
        "candidate-sandbox": "candidate-sandbox/latest.xml",
        "consumer": "consumer-tests/latest.xml",
        "crawler-network": "crawler-network/latest.xml",
        "database": "database-tests/latest.xml",
        "indexnow": "indexnow-tests/latest.xml",
        "page-attempt": "page-attempt-tests/latest.xml",
        "self-host": "self-host-tests/latest.xml",
        "temporal": "temporal-tests/latest.xml",
        "workflow-consumer-image": "workflow-consumer-image/latest.xml",
        "webflow": "webflow/latest.xml",
    }
    for lab in labs:
        name = lab.name.removeprefix("run-").removesuffix("-tests.py")
        command = (python, "scripts/gate_lab_worker.py", str(lab.relative_to(root)))
        if name == "autonomy-delivery":
            steps.extend(
                Step(
                    f"{name}-{index}", (*command, "--shard", str(index)), 3600, True, reports[name]
                )
                for index in range(2)
            )
        else:
            if name == "database":
                command += ("--shards", str(shards), "--repeat-brain", "5")
            steps.append(
                Step(
                    name,
                    command,
                    3600,
                    name != "temporal",
                    reports.get(name),
                    None,
                )
            )
    steps.extend(
        Step(
            name,
            (python, "scripts/gate_lab_worker.py", f"scripts/{name}_lab.py"),
            600,
            True,
            f"{name}-tests/latest.json",
        )
        for name in ("openbao", "keycloak")
    )
    steps.extend(
        [
            Step("gsc-boundary", (python, "scripts/check-gsc-provider-boundary.py"), 120),
            Step(
                "gitleaks",
                ("gitleaks", "git", f"--config={config}", f"--log-opts={revision_range}", "."),
                120,
            ),
        ]
    )
    return steps


def counts(report: Path | None, output: str, code: int) -> tuple[int, int, int, str]:
    if report is not None:
        if report.suffix == ".xml":
            cases = ET.parse(report).getroot().findall(".//testcase")
            if not cases:
                raise ValueError("Empty JUnit report")
            failed = sum(
                any(c.find(tag) is not None for tag in ("failure", "error")) for c in cases
            )
            skipped = sum(c.find("skipped") is not None for c in cases)
            return len(cases) - failed - skipped, failed, skipped, "test_cases"
        document = json.loads(report.read_text())
        if (
            not isinstance(document.get("tests"), list)
            or type(document.get("passed")) is not int
            or not 0 <= document["passed"] <= len(document["tests"])
            or not document["tests"]
        ):
            raise ValueError("Invalid provider report counts")
        return document["passed"], len(document["tests"]) - document["passed"], 0, "test_cases"
    if "PASS " in output:
        passed = len(re.findall(r"^PASS .+", output, re.MULTILINE))
        return passed, int(code != 0), 0, "checks"
    if "# pass " in output:
        passed = sum(map(int, re.findall(r"^# pass (\d+)", output, re.MULTILINE)))
        failed = sum(map(int, re.findall(r"^# fail (\d+)", output, re.MULTILINE)))
        # Vitest's test total (not its file count), alongside Node repository tests.
        passed += sum(map(int, re.findall(r"Tests\s+(\d+) passed", output)))
        failed += sum(map(int, re.findall(r"Tests\s+(\d+) failed", output)))
        return passed, failed, 0, "test_cases"
    return int(code == 0), int(code != 0), 0, "checks"


def execute(command, *, cwd, env, log, timeout, cancel):
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
    )
    deadline = time.monotonic() + timeout
    reason = None
    while process.poll() is None:
        if cancel.is_set() or time.monotonic() >= deadline:
            reason = "cancelled" if cancel.is_set() else "timeout"
            break
        time.sleep(0.05)
    if reason:
        # Interrupt only this process group, not other tracks or Docker resources.
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return process.returncode, reason, "unconfirmed"
    return process.wait(), reason, "attempted" if reason else "completed"


def run_step(step, directory, root, minimum_free, docker_slots, cancel, exclusive_slots=None):
    started = time.monotonic()
    result = {
        "name": step.name,
        "command": list(step.command),
        "status": "FAIL",
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "count_kind": "checks",
        "exit_code": None,
        "error": None,
        "process_cleanup": "not_started",
    }
    work = directory / "work" / step.name
    owned = False
    temporary = None
    try:
        exclusive_slots = exclusive_slots or {}
        with (
            exclusive_slots[step.exclusive] if step.exclusive else nullcontext(),
            docker_slots if step.docker else nullcontext(),
        ):
            if cancel.is_set():
                result["error"] = "cancelled"
                return result
            if shutil.disk_usage(root).free < minimum_free:
                result["error"] = "disk_guard"
                return result
            work.mkdir(parents=True, exist_ok=False)
            owned = True
            temporary = Path(tempfile.mkdtemp(prefix=f"signal-gate-{step.name}-"))
            if temporary.resolve().is_relative_to(root.resolve()):
                raise ValueError("Gate temporary data must be outside the repository")
            env = dict(
                os.environ,
                SIGNAL_LAB_RUNTIME_ROOT=str(work),
                TMPDIR=str(temporary),
                NO_COLOR="1",
                FORCE_COLOR="0",
                PYTHONDONTWRITEBYTECODE="1",
            )
            env["PYTEST_ADDOPTS"] = f"--basetemp={temporary / 'pytest-tmp'}"
            for key in list(env):
                if key.startswith(
                    ("SIGNAL_TEST_", "SIGNAL_DATABASE_TEST_", "SIGNAL_DELIVERY_TEST_")
                ):
                    env.pop(key)
            command = [arg.replace("{work}", str(work)) for arg in step.command]
            result["command"] = command
            print(f"START {step.name}", flush=True)
            log_path = directory / f"{step.name}.log"
            with log_path.open("x") as log:
                log_path.chmod(0o600)
                code, reason, cleanup = execute(
                    command, cwd=root, env=env, log=log, timeout=step.timeout, cancel=cancel
                )
            result.update(exit_code=code, error=reason, process_cleanup=cleanup)
            result["passed"], result["failed"], result["skipped"], result["count_kind"] = counts(
                work / step.report if step.report else None, log_path.read_text(), code
            )
            if step.report:
                retained = directory / f"{step.name}{Path(step.report).suffix}"
                shutil.copyfile(work / step.report, retained)
                result["report"] = retained.name
            if code == 0 and reason is None and result["failed"] == result["skipped"] == 0:
                result["status"] = "PASS"
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
        result["report_error"] = type(error).__name__
        result["error"] = result["error"] or type(error).__name__
    finally:
        # No glob cleanup, shared cache deletion, or global Docker prune. Only our root.
        result["artifact_cleanup"] = "completed"
        for path in (work if owned else None, temporary):
            if path is None:
                continue
            try:
                shutil.rmtree(path)
            except OSError:
                result.update(artifact_cleanup="unconfirmed", status="FAIL")
        result["seconds"] = round(time.monotonic() - started, 3)
        if result["status"] != "PASS":
            result["failed"] = max(1, result["failed"])
        print(
            f"{result['status']} {step.name}: {result['passed']} passed, "
            f"{result['failed']} failed ({result['seconds']}s)",
            flush=True,
        )
    return result


def run_gate(
    steps, directory, root, *, jobs=4, docker_jobs=2, minimum_free=4 * 1024**3, cancel=None
):
    started = time.monotonic()
    cancel = cancel or threading.Event()
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "jobs": jobs,
        "docker_jobs": docker_jobs,
        "minimum_free_bytes": minimum_free,
        "steps": [],
        "production_authority": False,
    }
    slots = threading.BoundedSemaphore(docker_jobs)
    exclusive_slots = {s.exclusive: threading.Lock() for s in steps if s.exclusive}
    names = [s.name for s in steps]
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[a-z0-9-]+", n) for n in names):
        raise ValueError("Gate steps require unique safe artifact names")
    if shutil.disk_usage(root).free < minimum_free:
        report["steps"] = [
            {
                "name": "preflight",
                "status": "FAIL",
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "error": "disk_guard",
            }
        ]
        report["steps"].extend(
            {
                "name": s.name,
                "status": "NOT_EXECUTED",
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "error": "disk_guard",
                "command": list(s.command),
            }
            for s in steps
        )
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(
                    run_step, s, directory, root, minimum_free, slots, cancel, exclusive_slots
                )
                for s in steps
            ]
            report["steps"] = [future.result() for future in futures]
    report.update(
        seconds=round(time.monotonic() - started, 3),
        passed=sum(s["status"] == "PASS" for s in report["steps"]),
        failed=sum(s["status"] == "FAIL" for s in report["steps"]),
        not_executed=sum(s["status"] == "NOT_EXECUTED" for s in report["steps"]),
    )
    (directory / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return int(report["failed"] != 0), report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", required=True, dest="revision_range")
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--docker-jobs", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--database-shards", type=int, choices=range(1, 7), default=3)
    parser.add_argument("--minimum-free-gib", type=int, choices=range(3, 65), default=4)
    args = parser.parse_args()
    parent = ROOT / ".runtime/full-gate"
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    signal.signal(signal.SIGTERM, lambda *_: cancel.set())
    try:
        config = directory / "main-gitleaks.toml"
        config.write_bytes(
            subprocess.check_output(["git", "show", "main:.gitleaks.toml"], cwd=ROOT, timeout=30)
        )
        steps = plan(ROOT, sys.executable, args.revision_range, config, args.database_shards)
        code, report = run_gate(
            steps,
            directory,
            ROOT,
            jobs=args.jobs,
            docker_jobs=args.docker_jobs,
            minimum_free=args.minimum_free_gib * 1024**3,
            cancel=cancel,
        )
        print(f"Summary: {directory / 'summary.json'} ({report['seconds']}s)", flush=True)
        return code
    except (OSError, subprocess.SubprocessError):
        (directory / "summary.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "passed": 0,
                    "failed": 1,
                    "steps": [
                        {
                            "name": "configuration",
                            "status": "FAIL",
                            "passed": 0,
                            "failed": 1,
                            "error": "main_gitleaks_unavailable",
                        }
                    ],
                }
            )
            + "\n"
        )
        return 1
