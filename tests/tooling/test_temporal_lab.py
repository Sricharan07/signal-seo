import json
import subprocess
from types import SimpleNamespace

import pytest
import temporal_lab as lab


def test_disk_preflight_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=512 * 1024**2 - 1))
    with pytest.raises(lab.LabError, match="512 MiB"):
        lab.require_free_space(tmp_path)
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=512 * 1024**2))
    lab.require_free_space(tmp_path)


def test_temporal_version_parser_accepts_only_closed_output():
    assert lab.parse_temporal_versions("temporal version 1.8.3 (Server 1.31.2, UI 2.50.1)\n") == (
        "1.8.3",
        "1.31.2",
        "2.50.1",
    )
    for value in (
        "temporal version latest",
        "temporal version 1.8.3 (Server 1.31.2, UI 2.50.1) extra",
        "secret-token",
    ):
        with pytest.raises(lab.LabError, match="unexpected version"):
            lab.parse_temporal_versions(value)


def test_version_command_failure_does_not_expose_process_details(monkeypatch, tmp_path):
    binary = tmp_path / "temporal-sdk-python-1.32.0"
    binary.write_bytes(b"synthetic")
    monkeypatch.setattr(lab, "DOWNLOAD", tmp_path)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["secret-argument"], stderr="secret-output")

    monkeypatch.setattr(lab.subprocess, "run", fail)
    with pytest.raises(lab.LabError) as captured:
        lab.temporal_versions()
    assert "secret" not in str(captured.value)


def test_version_command_requires_exactly_one_sdk_managed_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(lab, "DOWNLOAD", tmp_path)
    with pytest.raises(lab.LabError, match="unavailable"):
        lab.temporal_versions()
    (tmp_path / "temporal-sdk-python-one").write_bytes(b"one")
    (tmp_path / "temporal-sdk-python-two").write_bytes(b"two")
    with pytest.raises(lab.LabError, match="unavailable"):
        lab.temporal_versions()


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


def test_runner_removes_external_address_and_writes_sanitized_evidence(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "RUNTIME", runtime)
    monkeypatch.setattr(lab, "DOWNLOAD", runtime / "sdk-cache")
    monkeypatch.setattr(lab, "require_free_space", lambda _: None)
    monkeypatch.setattr(lab, "source_hashes", lambda: {"source.py": "a" * 64})
    monkeypatch.setattr(lab, "temporal_versions", lambda: ("1.8.3", "1.31.2", "2.50.1"))
    monkeypatch.setattr(
        lab,
        "summarize_junit",
        lambda _: {"tests": [{"name": "real", "status": "PASS"}], "passed": 1},
    )
    monkeypatch.setenv("SIGNAL_TEMPORAL_ADDRESS", "secret.external.invalid:7233")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lab.subprocess, "run", run)

    assert lab.main() == 0
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert "SIGNAL_TEMPORAL_ADDRESS" not in kwargs["env"]
    assert kwargs["env"]["SIGNAL_TEMPORAL_LAB"] == "1"
    assert kwargs["env"]["SIGNAL_TEMPORAL_DOWNLOAD_DIR"] == str(runtime / "sdk-cache")
    report = json.loads((runtime / "latest.json").read_text())
    assert report["bind_address"] == "127.0.0.1"
    assert report["persistence"] == "ephemeral_in_memory_sqlite"
    assert report["production_authority"] is False
    assert "secret" not in json.dumps(report)
