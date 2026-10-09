import json
import os
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import database_shards as shards
import pytest


def test_partition_evidence_requires_identical_collection_and_exact_once_assignment():
    cases = [f"file::case[{i}]" for i in range(11)]
    manifests = [{"all": cases, "selected": cases[i::3]} for i in range(3)]
    assert shards.validate_partitions(manifests, 3) == 11
    for bad in [
        manifests[:2],
        [{"all": [], "selected": []}] * 3,
        [*manifests[:2], {"all": cases, "selected": cases[1::3]}],
        [*manifests[:2], {"all": cases[:-1], "selected": cases[2::3]}],
    ]:
        with pytest.raises(shards.LabError):
            shards.validate_partitions(bad, 3)


def test_combined_junit_preserves_failure_error_skip_and_every_case(tmp_path):
    files = []
    for index, body in enumerate(
        [
            "<testcase name='pass'/>",
            "<testcase name='fail'><failure>exact failure</failure></testcase>",
            "<testcase name='error'><error/></testcase><testcase name='skip'><skipped/></testcase>",
        ]
    ):
        path = tmp_path / f"{index}.xml"
        path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>")
        files.append(path)
    out = tmp_path / "combined.xml"
    shards.combine_junit(files, out)
    report = shards.summarize_junit(out)
    assert report["passed"] == 1 and len(report["tests"]) == 4
    assert "exact failure" in out.read_text()
    assert [t["status"] for t in report["tests"]] == ["PASS", "FAIL", "FAIL", "SKIP"]


def test_empty_junit_fails_closed(tmp_path):
    path = tmp_path / "empty.xml"
    path.write_text("<testsuites/>")
    with pytest.raises(shards.LabError, match="Empty"):
        shards.combine_junit([path], tmp_path / "combined.xml")


def test_worker_provisions_own_database_twice_migrates_and_repeats_without_sharding(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(shards, "isolated_postgres", lambda: nullcontext(("synthetic-dsn", {})))
    monkeypatch.setattr(shards, "provision", lambda *a: ({}, "17.11"))

    def run(command, **kwargs):
        calls.append((command, dict(kwargs["env"])))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(shards.subprocess, "run", run)
    assert shards.worker(0, 3, tmp_path / "worker", 5) == 0
    assert len(calls) == 8
    assert calls[0][0] == calls[1][0]
    assert calls[2][1]["SIGNAL_DATABASE_TEST_SHARD"] == "0"
    assert calls[2][1]["SIGNAL_DATABASE_TEST_SHARDS"] == "3"
    assert all("SIGNAL_DATABASE_TEST_SHARD" not in env for _, env in calls[3:])
    assert all(shards.BRAIN_TEST in cmd for cmd, _ in calls[3:])
    assert (tmp_path / "worker/version.txt").read_text() == "17.11"


def test_worker_test_failure_is_not_hidden_by_successful_repetition(monkeypatch, tmp_path):
    monkeypatch.setattr(shards, "isolated_postgres", lambda: nullcontext(("synthetic-dsn", {})))
    monkeypatch.setattr(shards, "provision", lambda *a: ({}, "17.11"))
    codes = iter([0, 0, 1, 0])
    monkeypatch.setattr(
        shards.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=next(codes))
    )
    assert shards.worker(0, 1, tmp_path / "worker", 1) == 1


def test_parent_starts_exactly_n_separate_workers_and_combines_complete_evidence(
    monkeypatch, tmp_path
):
    calls = []
    cases = ["file::a", "file::b", "file::c"]
    monkeypatch.setattr(shards, "source_hashes", lambda: {})
    monkeypatch.setattr(shards, "runtime_root", lambda _: tmp_path)
    monkeypatch.setattr(shards.sys, "argv", ["runner", "--shards", "3"])

    def start(cmd, **kwargs):
        calls.append((cmd, kwargs))
        index = int(cmd[cmd.index("--worker") + 1])
        directory = shards.Path(cmd[cmd.index("--artifact-directory") + 1])
        directory.mkdir()
        (directory / "collection.json").write_text(
            json.dumps({"all": cases, "selected": cases[index::3]})
        )
        (directory / "suite.xml").write_text(
            f"<testsuites><testsuite><testcase name='{cases[index]}'/></testsuite></testsuites>"
        )
        (directory / "version.txt").write_text("17.11")
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    monkeypatch.setattr(shards.subprocess, "Popen", start)
    assert shards.main() == 0
    assert len(calls) == 3
    assert all(kwargs["start_new_session"] for _, kwargs in calls)
    assert len({cmd[cmd.index("--artifact-directory") + 1] for cmd, _ in calls}) == 3
    report = json.loads((tmp_path / "database-tests/latest.json").read_text())
    assert report["shards"] == 3 and report["collected"] == report["passed"] == 3
    assert not list((tmp_path / "database-tests").glob("run-*"))


def test_stop_workers_signals_only_own_active_groups(monkeypatch):
    calls = []
    monkeypatch.setattr(shards.os, "killpg", lambda *a: calls.append(a))
    processes = [
        SimpleNamespace(pid=10, poll=lambda: None, wait=lambda **k: 130),
        SimpleNamespace(pid=20, poll=lambda: 0),
    ]
    shards.stop_workers(processes)
    assert calls == [(10, shards.signal.SIGINT)]


def test_worker_that_wont_exit_has_explicit_unconfirmed_cleanup(monkeypatch):
    calls = []
    monkeypatch.setattr(shards.os, "killpg", lambda *a: calls.append(a))

    def wait(**kwargs):
        if kwargs:
            raise subprocess.TimeoutExpired("synthetic-worker", 60)
        return -9

    process = SimpleNamespace(pid=10, poll=lambda: None, wait=wait)
    with pytest.raises(shards.LabError, match="unconfirmed"):
        shards.stop_workers([process])
    assert calls == [(10, shards.signal.SIGINT), (10, shards.signal.SIGKILL)]


@pytest.mark.parametrize("value", ["0", "7", "-1", "not-an-integer"])
def test_environment_cannot_bypass_shard_concurrency_bound(monkeypatch, value):
    monkeypatch.setenv("SIGNAL_DATABASE_TEST_SHARDS", value)
    monkeypatch.setattr(shards.sys, "argv", ["runner"])
    monkeypatch.setattr(shards.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as result:
        shards.main()
    assert result.value.code == 2


def test_actual_pytest_collection_is_complete_across_shards(tmp_path):
    manifests = []
    for index in range(3):
        report = tmp_path / f"collection-{index}.json"
        env = dict(
            os.environ,
            SIGNAL_DATABASE_LAB="1",
            SIGNAL_DATABASE_TEST_SHARD=str(index),
            SIGNAL_DATABASE_TEST_SHARDS="3",
            SIGNAL_DATABASE_COLLECTION_REPORT=str(report),
        )
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/control_plane", "--collect-only", "-q"],
            env=env,
            cwd=shards.ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        manifests.append(json.loads(report.read_text()))
    total = shards.validate_partitions(manifests, 3)
    assert total > 900
    assert sum(len(m["selected"]) for m in manifests) == total
