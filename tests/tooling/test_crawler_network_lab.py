import json
import subprocess
from types import SimpleNamespace

import crawler_network_lab as lab
import pytest
from lab_network import candidate_network


def test_disk_preflight_and_docker_errors_are_sanitized(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=2 * 1024**3 - 1))
    with pytest.raises(lab.LabError, match="2 GiB"):
        lab.require_free_space(tmp_path)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["private-argument"], stderr="private-output")

    monkeypatch.setattr(lab.subprocess, "run", fail)
    with pytest.raises(lab.LabError) as captured:
        lab.docker("info", "private-argument")
    assert "private" not in str(captured.value)
    assert "CalledProcessError" in str(captured.value)


def test_cleanup_removes_only_exact_labeled_resources_and_attempts_every_kind(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("container", "ls"):
            return SimpleNamespace(
                stdout="signal-crawler-network-run-origin\nunrelated\n",
                returncode=0,
            )
        if args[:2] == ("network", "ls"):
            return SimpleNamespace(stdout="signal-crawler-network-run\nother\n", returncode=0)
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(stdout="", returncode=0 if len(calls) < 7 else 1)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(lab, "docker", docker)
    with pytest.raises(lab.LabError, match="cleanup"):
        lab.cleanup("run", "signal-crawler-network-run", "image:run")

    assert (
        "container",
        "rm",
        "--force",
        "--volumes",
        "signal-crawler-network-run-origin",
    ) in calls
    assert not any(call[-1] == "unrelated" for call in calls)
    assert ("network", "rm", "signal-crawler-network-run") in calls
    assert not any(call[-1] == "other" for call in calls)
    assert ("image", "rm", "--force", "image:run") in calls


def test_runner_builds_internal_network_executes_tests_records_evidence_and_cleans(
    monkeypatch, tmp_path
):
    runtime = tmp_path / "runtime"
    hashes = {"source.py": "a" * 64}
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "RUNTIME", runtime)
    monkeypatch.setattr(lab, "DOCKERFILE", tmp_path / "deploy/crawler-network/Dockerfile")
    monkeypatch.setattr(lab, "require_free_space", lambda _: None)
    monkeypatch.setattr(lab, "source_hashes", lambda: hashes)
    monkeypatch.setattr(lab, "wait_for_server", lambda _: None)
    monkeypatch.setattr(
        lab,
        "summarize_junit",
        lambda _: {"tests": [{"name": "network", "status": "PASS"}], "passed": 1},
    )
    cleaned = []
    monkeypatch.setattr(
        lab,
        "cleanup",
        lambda run_id, network, image: cleaned.append((run_id, network, image)),
    )
    calls = []

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("image", "inspect"):
            output = "sha256:" + "b" * 64 + "\n"
        elif args[0] in {"info", "version"}:
            output = "28.5.1\n"
        else:
            output = ""
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(lab, "docker", docker)
    fixture = candidate_network()
    network_calls = []

    def create_public_network(name, label):
        network_calls.append((name, label))
        return fixture

    monkeypatch.setattr(lab, "create_public_network", create_public_network)
    pytest_calls = []

    def run(args, **kwargs):
        pytest_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lab.subprocess, "run", run)

    assert lab.main() == 0
    build = next(call for call in calls if call[0][0] == "build")
    server = next(call for call in calls if call[0][:2] == ("container", "create"))
    assert "--pull" in build[0]
    assert network_calls == [(cleaned[0][1], f"{lab.LABEL}={cleaned[0][0]}")]
    assert "--read-only" in server[0]
    assert "no-new-privileges:true" in server[0]
    assert len(cleaned) == 1
    assert pytest_calls[0][0][3] == "tests/crawler"
    assert pytest_calls[0][1]["env"]["SIGNAL_CRAWLER_NETWORK_LAB"] == "1"

    report = json.loads((runtime / "latest.json").read_text())
    assert report["docker_server"] == "28.5.1"
    assert report["local_image_id"] == "sha256:" + "b" * 64
    assert report["network"]["internal"] is True
    assert report["network"]["ip_masquerade"] is False
    assert report["network"]["subnet"] == fixture.subnet
    assert report["network"]["address"] == fixture.address
    assert report["production_authority"] is False
    assert report["cleanup"] == "completed"


def test_runner_attempts_exact_cleanup_after_build_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(lab, "DOCKERFILE", tmp_path / "Dockerfile")
    monkeypatch.setattr(lab, "require_free_space", lambda _: None)
    monkeypatch.setattr(lab, "source_hashes", lambda: {"source.py": "a" * 64})
    cleaned = []
    monkeypatch.setattr(
        lab,
        "cleanup",
        lambda run_id, network, image: cleaned.append((run_id, network, image)),
    )

    def docker(*args, **kwargs):
        if args[0] == "build":
            raise lab.LabError("sanitized build failure")
        return SimpleNamespace(stdout="28.5.1\n", returncode=0)

    monkeypatch.setattr(lab, "docker", docker)
    with pytest.raises(lab.LabError, match="build failure"):
        lab.main()
    assert len(cleaned) == 1
