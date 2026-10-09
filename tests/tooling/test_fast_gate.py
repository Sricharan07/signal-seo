import json
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import full_gate as gate
import pytest
from lab_runtime import runtime_root
from test_shards import partition_cases


@pytest.mark.parametrize("count", range(1, 7))
def test_shards_are_complete_disjoint_balanced_and_collection_order_independent(count):
    items = [SimpleNamespace(nodeid=f"test_file.py::test_case[{i:04d}]") for i in range(961)]
    assignments = []
    sizes = []
    for index in range(count):
        selected, rejected = partition_cases(items, index, count)
        reverse, _ = partition_cases(list(reversed(items)), index, count)
        assert selected == reverse
        assert Counter(i.nodeid for i in selected + rejected) == Counter(i.nodeid for i in items)
        assignments.extend(i.nodeid for i in selected)
        sizes.append(len(selected))
    assert Counter(assignments) == Counter(i.nodeid for i in items)
    assert max(sizes) - min(sizes) <= 1


@pytest.mark.parametrize("shard,count", [(0, 0), (0, 7), (-1, 3), (3, 3), (True, 3), (0, True)])
def test_invalid_shards_fail_closed(shard, count):
    with pytest.raises(ValueError):
        partition_cases([], shard, count)


def test_private_runtime_is_opt_in_and_does_not_change_standalone_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("SIGNAL_LAB_RUNTIME_ROOT", raising=False)
    assert runtime_root(tmp_path) == tmp_path / ".runtime"
    monkeypatch.setenv("SIGNAL_LAB_RUNTIME_ROOT", str(tmp_path / "private"))
    assert runtime_root(tmp_path) == tmp_path / "private"


def command(code):
    return (sys.executable, "-c", code)


@pytest.mark.parametrize("relative", [True, False])
def test_lab_worker_preserves_absolute_file_arguments_and_exit_status(tmp_path, relative):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys; from pathlib import Path\n"
        "assert Path(__file__).is_absolute()\n"
        "assert Path(__file__).relative_to(Path.cwd()) == Path('probe.py')\n"
        "assert sys.argv[1:] == ['--shard', '0']\n"
        "raise SystemExit(7)\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(gate.ROOT / "scripts/gate_lab_worker.py"),
            str(probe.relative_to(tmp_path) if relative else probe),
            "--shard",
            "0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7, result.stderr


def test_real_runner_summary_failure_exit_and_cleanup_are_invocation_scoped(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir()
    unrelated = tmp_path / "other-track" / "keep"
    unrelated.parent.mkdir()
    unrelated.write_text("keep")
    steps = [
        gate.Step(
            "good",
            command(
                "import os; from pathlib import Path; "
                "Path(os.environ['TMPDIR'], 'large').write_bytes(b'x'*10000)"
            ),
        ),
        gate.Step("bad", command("raise SystemExit(7)")),
    ]
    code, report = gate.run_gate(steps, directory, tmp_path, minimum_free=0)
    assert code == 1
    assert report["passed"] == report["failed"] == 1
    assert [s["exit_code"] for s in report["steps"]] == [0, 7]
    assert [(s["passed"], s["failed"]) for s in report["steps"]] == [(1, 0), (0, 1)]
    assert all(s["artifact_cleanup"] == "completed" for s in report["steps"])
    assert not list((directory / "work").iterdir())
    assert unrelated.read_text() == "keep"
    assert json.loads((directory / "summary.json").read_text()) == report


def test_all_passing_gate_returns_zero(tmp_path):
    code, report = gate.run_gate(
        [gate.Step("ok", command("pass"))], tmp_path, tmp_path, minimum_free=0
    )
    assert code == 0
    assert report["passed"] == 1 and report["failed"] == 0


def test_global_disk_guard_starts_no_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr(gate.shutil, "disk_usage", lambda _: SimpleNamespace(free=99))
    monkeypatch.setattr(gate.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    code, report = gate.run_gate(
        [gate.Step("no", command("pass"))], tmp_path, tmp_path, minimum_free=100
    )
    assert code == 1
    assert report["steps"][0]["error"] == "disk_guard"
    assert report["failed"] == 1
    assert report["not_executed"] == 1
    assert report["steps"][1]["name"] == "no"
    assert report["steps"][1]["status"] == "NOT_EXECUTED"
    assert (tmp_path / "summary.json").exists()


def test_each_step_rechecks_space_after_admission(monkeypatch, tmp_path):
    calls = iter([100, 99])
    monkeypatch.setattr(gate.shutil, "disk_usage", lambda _: SimpleNamespace(free=next(calls)))
    monkeypatch.setattr(gate.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    code, report = gate.run_gate(
        [gate.Step("no", command("pass"))], tmp_path, tmp_path, minimum_free=100
    )
    assert code == 1
    assert report["steps"][0]["error"] == "disk_guard"


def test_timeout_interrupts_only_owned_process_and_runs_finally(tmp_path):
    marker = tmp_path / "finally"
    step = gate.Step(
        "slow",
        command(
            "import time; from pathlib import Path\n"
            f"try: time.sleep(30)\nfinally: Path({str(marker)!r}).write_text('cleaned')"
        ),
        timeout=0.5,
    )
    code, report = gate.run_gate([step], tmp_path, tmp_path, minimum_free=0)
    assert code == 1
    assert report["steps"][0]["error"] == "timeout"
    assert report["steps"][0]["process_cleanup"] == "attempted"
    assert marker.read_text() == "cleaned"
    assert not (tmp_path / "work/slow").exists()


def test_spawn_failure_is_reported_and_cleans_owned_artifacts(tmp_path):
    step = gate.Step("missing", (str(tmp_path / "not-executable"),))
    code, report = gate.run_gate([step], tmp_path, tmp_path, minimum_free=0)
    assert code == 1
    assert report["steps"][0]["error"] == "FileNotFoundError"
    assert report["steps"][0]["artifact_cleanup"] == "completed"


def test_cleanup_failure_cannot_pass(monkeypatch, tmp_path):
    cleanup = gate.shutil.rmtree

    def fail(path):
        if path == tmp_path / "work/ok":
            raise OSError("synthetic cleanup failure")
        cleanup(path)

    monkeypatch.setattr(gate.shutil, "rmtree", fail)
    code, report = gate.run_gate(
        [gate.Step("ok", command("pass"))], tmp_path, tmp_path, minimum_free=0
    )
    assert code == 1
    assert report["steps"][0]["artifact_cleanup"] == "unconfirmed"


def test_temporary_data_is_private_outside_repository_and_removed(tmp_path):
    marker = tmp_path / "temporary-path"
    step = gate.Step(
        "temporary",
        command(
            "import os,stat; from pathlib import Path; "
            "p=Path(os.environ['TMPDIR']); "
            "assert not p.resolve().is_relative_to(Path.cwd().resolve()); "
            "assert stat.S_IMODE(p.stat().st_mode)==0o700; "
            "assert os.environ['PYTEST_ADDOPTS'].startswith('--basetemp='+str(p)); "
            f"Path({str(marker)!r}).write_text(str(p))"
        ),
    )
    code, report = gate.run_gate([step], tmp_path, tmp_path, minimum_free=0)
    assert code == 0
    assert not Path(marker.read_text()).exists()
    assert report["steps"][0]["artifact_cleanup"] == "completed"


def test_repository_local_temporary_root_fails_closed_and_is_cleaned(monkeypatch, tmp_path):
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setattr(gate.tempfile, "mkdtemp", lambda **_: str(temporary))
    monkeypatch.setattr(gate.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    code, report = gate.run_gate(
        [gate.Step("local", command("pass"))], tmp_path, tmp_path, minimum_free=0
    )
    assert code == 1 and report["steps"][0]["error"] == "ValueError"
    assert not temporary.exists()


def test_docker_and_overall_parallelism_are_bounded(monkeypatch, tmp_path):
    lock = threading.Lock()
    active = {"all": 0, "docker": 0}
    peak = dict(active)
    import time

    def execute(command, **kwargs):
        docker = command[0] == "docker-double"
        with lock:
            active["all"] += 1
            active["docker"] += docker
            for name in peak:
                peak[name] = max(peak[name], active[name])
        time.sleep(0.04)
        with lock:
            active["all"] -= 1
            active["docker"] -= docker
        return 0, None, "completed"

    monkeypatch.setattr(gate, "execute", execute)
    steps = [gate.Step(str(i), ("docker-double",), docker=True) for i in range(6)]
    steps += [gate.Step("plain", ("plain",))]
    code, _ = gate.run_gate(steps, tmp_path, tmp_path, jobs=4, docker_jobs=2, minimum_free=0)
    assert code == 0
    assert peak["all"] <= 4 and peak["docker"] == 2


def test_report_counts_are_cases_not_successful_processes(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        "<testsuites><testsuite><testcase/><testcase><failure/></testcase>"
        "<testcase><error/></testcase><testcase><skipped/></testcase>"
        "</testsuite></testsuites>"
    )
    assert gate.counts(report, "", 1) == (1, 2, 1, "test_cases")
    report.write_text("<testsuites/>")
    with pytest.raises(ValueError):
        gate.counts(report, "", 0)
    assert gate.counts(None, "# pass 44\n# fail 0\nTests 153 passed (153)", 0) == (
        197,
        0,
        0,
        "test_cases",
    )


def test_missing_or_skipped_report_cannot_pass(tmp_path):
    steps = [
        gate.Step("missing", command("pass"), report="missing.xml"),
        gate.Step(
            "skipped",
            command(
                "import os; from pathlib import Path; "
                "Path(os.environ['SIGNAL_LAB_RUNTIME_ROOT'], 'skip.xml').write_text("
                "'<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>')"
            ),
            report="skip.xml",
        ),
    ]
    code, report = gate.run_gate(steps, tmp_path, tmp_path, minimum_free=0)
    assert code == 1 and report["failed"] == 2
    assert report["steps"][1]["skipped"] == 1


def test_plan_discovers_every_lab_and_preserves_main_scanner_configuration(tmp_path):
    root = Path(gate.ROOT)
    steps = gate.plan(root, "python", "main..HEAD", tmp_path / "main.toml", 3)
    commands = [s.command for s in steps]
    for lab in root.glob("scripts/run-*-tests.py"):
        assert any(str(lab.relative_to(root)) in cmd for cmd in commands)
    assert len([s for s in steps if s.name.startswith("autonomy-delivery-")]) == 2
    db = next(s for s in steps if s.name == "database")
    assert db.command[-4:] == ("--shards", "3", "--repeat-brain", "5")
    assert all("tests" in s.command for s in steps if s.name.startswith("ruff"))
    assert next(s for s in steps if s.name == "gitleaks").command == (
        "gitleaks",
        "git",
        f"--config={tmp_path / 'main.toml'}",
        "--log-opts=main..HEAD",
        ".",
    )


def test_cancellation_records_failure_without_starting_work(tmp_path):
    cancel = threading.Event()
    cancel.set()
    code, report = gate.run_gate(
        [gate.Step("no", command("pass"))], tmp_path, tmp_path, minimum_free=0, cancel=cancel
    )
    assert code == 1 and report["steps"][0]["error"] == "cancelled"


def test_train_two_labs_have_exact_reports_in_the_private_runtime(tmp_path, monkeypatch):
    import runpy

    private = tmp_path / "invocation"
    monkeypatch.setenv("SIGNAL_LAB_RUNTIME_ROOT", str(private))
    browser = runpy.run_path(str(gate.ROOT / "scripts/run-browser-worker-tests.py"))
    assert browser["REPORT"] == private / "browser-worker"
    steps = {
        s.name: s for s in gate.plan(gate.ROOT, "python", "main..HEAD", tmp_path / "main.toml", 3)
    }
    assert steps["browser-worker"].report == "browser-worker/latest.xml"
    assert steps["self-host"].report == "self-host-tests/latest.xml"
    assert steps["webflow"].report == "webflow/latest.xml"


def test_lab_wrapper_cleans_up_on_group_interrupt(tmp_path):
    source = tmp_path / "lab.py"
    marker = tmp_path / "cleanup"
    source.write_text(
        "import time; from pathlib import Path\n"
        f"try: time.sleep(30)\nfinally: Path({str(marker)!r}).write_text('owned')"
    )
    step = gate.Step(
        "wrapper",
        (sys.executable, str(gate.ROOT / "scripts/gate_lab_worker.py"), str(source)),
        timeout=0.5,
    )
    code, _ = gate.run_gate([step], tmp_path, tmp_path, minimum_free=0)
    assert code == 1 and marker.read_text() == "owned"


def test_environment_does_not_inherit_external_test_infrastructure(monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNAL_TEST_ADMIN_DSN", "synthetic-do-not-inherit")
    monkeypatch.setenv("SIGNAL_DATABASE_TEST_SHARD", "5")
    step = gate.Step(
        "env",
        command(
            "import os; assert 'SIGNAL_TEST_ADMIN_DSN' not in os.environ; "
            "assert 'SIGNAL_DATABASE_TEST_SHARD' not in os.environ"
        ),
    )
    code, _ = gate.run_gate([step], tmp_path, tmp_path, minimum_free=0)
    assert code == 0


def test_existing_work_directory_is_never_claimed_or_deleted(tmp_path):
    work = tmp_path / "work/ok"
    work.mkdir(parents=True)
    marker = work / "other-run"
    marker.write_text("keep")
    code, _ = gate.run_gate([gate.Step("ok", command("pass"))], tmp_path, tmp_path, minimum_free=0)
    assert code == 1 and marker.read_text() == "keep"


def test_step_namespace_cannot_collide_or_escape(tmp_path):
    for steps in [
        [gate.Step("../escape", command("pass"))],
        [gate.Step("same", command("pass"))] * 2,
    ]:
        with pytest.raises(ValueError, match="unique safe"):
            gate.run_gate(steps, tmp_path, tmp_path, minimum_free=0)


def test_invalid_provider_counts_do_not_fabricate_success(tmp_path):
    path = tmp_path / "latest.json"
    for document in [
        {"passed": 0, "tests": []},
        {"passed": 2, "tests": ["one"]},
        {"passed": -1, "tests": ["one"]},
        {"passed": "1", "tests": ["one"]},
    ]:
        path.write_text(json.dumps(document))
        with pytest.raises(ValueError):
            gate.counts(path, "", 0)


def test_summary_retains_small_combined_junit_after_bulk_artifact_cleanup(tmp_path):
    step = gate.Step(
        "xml",
        command(
            "import os; from pathlib import Path; "
            "Path(os.environ['SIGNAL_LAB_RUNTIME_ROOT'], 'test.xml').write_text("
            "'<testsuites><testsuite><testcase name=\"one\"/></testsuite></testsuites>')"
        ),
        report="test.xml",
    )
    code, report = gate.run_gate([step], tmp_path, tmp_path, minimum_free=0)
    assert code == 0
    assert report["steps"][0]["report"] == "xml.xml"
    assert (tmp_path / "xml.xml").exists()
    assert not (tmp_path / "work/xml").exists()


def test_timeout_reason_is_preserved_when_junit_is_missing(tmp_path):
    step = gate.Step(
        "slow", command("import time; time.sleep(30)"), timeout=0.5, report="absent.xml"
    )
    code, report = gate.run_gate([step], tmp_path, tmp_path, minimum_free=0)
    assert code == 1
    assert report["steps"][0]["error"] == "timeout"
    assert report["steps"][0]["report_error"] == "FileNotFoundError"


def test_forced_timeout_is_unconfirmed_and_signals_only_created_group(monkeypatch):
    import subprocess

    calls = []
    clock = iter([0, 2])

    def wait(**kwargs):
        if kwargs:
            raise subprocess.TimeoutExpired("synthetic-step", 120)
        return -9

    process = SimpleNamespace(pid=5678, poll=lambda: None, wait=wait, returncode=-9)
    monkeypatch.setattr(gate.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(gate.os, "killpg", lambda *a: calls.append(a))
    assert gate.execute(
        ("synthetic",), cwd=None, env={}, log=None, timeout=1, cancel=threading.Event()
    ) == (-9, "timeout", "unconfirmed")
    assert calls == [(5678, gate.signal.SIGINT), (5678, gate.signal.SIGKILL)]


def test_invocation_private_network_labs_need_no_global_serialization(tmp_path):
    steps = gate.plan(gate.ROOT, "python", "main..HEAD", tmp_path / "main.toml", 3)
    assert next(s for s in steps if s.name == "crawler-network").exclusive is None
    assert next(s for s in steps if s.name == "page-attempt").exclusive is None
