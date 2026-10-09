import json
from contextlib import nullcontext
from types import SimpleNamespace

import consumer_lab as lab
import pytest


def test_junit_summary_distinguishes_failure_skip_and_pass(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text(
        '<testsuites><testsuite><testcase name="pass"/>'
        '<testcase name="fail"><failure/></testcase>'
        '<testcase name="error"><error/></testcase>'
        '<testcase name="skip"><skipped/></testcase></testsuite></testsuites>'
    )
    result = lab.summarize_junit(path)
    assert result["passed"] == 1
    assert [test["status"] for test in result["tests"]] == ["PASS", "FAIL", "FAIL", "SKIP"]
    path.write_text("<testsuites/>")
    with pytest.raises(lab.LabError, match="Empty"):
        lab.summarize_junit(path)


def test_joint_runner_owns_both_provider_boundaries_and_sanitized_evidence(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "RUNTIME", runtime)
    monkeypatch.setattr(lab, "DOWNLOAD", runtime / "sdk-cache")
    monkeypatch.setattr(lab, "source_hashes", lambda: {"source.py": "a" * 64})
    monkeypatch.setattr(lab, "isolated_postgres", lambda: nullcontext(("admin-secret", {})))
    monkeypatch.setattr(
        lab,
        "provision",
        lambda admin, common: (
            {
                "SIGNAL_TEST_SCHEDULER_DSN": "scheduler-secret",
                "SIGNAL_TEST_WORKFLOW_DSN": "workflow-secret",
            },
            "17.11",
        ),
    )
    monkeypatch.setattr(lab, "temporal_versions", lambda: ("1.8.3", "1.31.2", "2.50.1"))
    monkeypatch.setattr(
        lab,
        "summarize_junit",
        lambda _: {"tests": [{"name": "joint", "status": "PASS"}], "passed": 1},
    )
    monkeypatch.setenv("SIGNAL_TEMPORAL_ADDRESS", "secret.external.invalid:7233")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lab.subprocess, "run", run)

    assert lab.main() == 0

    assert len(calls) == 3
    assert calls[0][0][-2:] == ["upgrade", "head"]
    assert calls[1][0][-2:] == ["upgrade", "head"]
    pytest_args, pytest_options = calls[2]
    assert "tests/consumer" in pytest_args
    assert pytest_options["env"]["SIGNAL_CONSUMER_LAB"] == "1"
    assert pytest_options["env"]["SIGNAL_TEMPORAL_DOWNLOAD_DIR"] == str(runtime / "sdk-cache")
    assert "SIGNAL_TEMPORAL_ADDRESS" not in pytest_options["env"]
    report = json.loads((runtime / "latest.json").read_text())
    assert report["database"] == "17.11"
    assert report["temporal_server"] == "1.31.2"
    assert report["bind_address"] == "127.0.0.1"
    assert report["production_authority"] is False
    assert report["cleanup"] == "completed"
    assert "secret" not in json.dumps(report)
