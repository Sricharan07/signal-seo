import json
from types import SimpleNamespace

import pytest
import workflow_consumer_image_lab as lab


def test_release_identity_is_stable_and_order_independent():
    first = lab.release_identity({"b": "2", "a": "1"})
    second = lab.release_identity({"a": "1", "b": "2"})
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == 71


def test_cleanup_removes_only_exact_labeled_resources(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("container", "ls"):
            return SimpleNamespace(
                stdout="signal-consumer-image-run-health\nunrelated-name\n",
                returncode=0,
            )
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(stdout="", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(lab, "docker", docker)
    with pytest.raises(lab.LabError, match="cleanup"):
        lab.cleanup("run", "image:run")
    assert (
        "container",
        "rm",
        "--force",
        "--volumes",
        "signal-consumer-image-run-health",
    ) in calls
    assert not any(call[-1] == "unrelated-name" for call in calls)
    assert ("image", "rm", "--force", "image:run") in calls
    assert any(call[:2] == ("image", "ls") for call in calls)


def test_runner_owns_build_test_evidence_and_cleanup(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    hashes = {"source.py": "a" * 64}
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "RUNTIME", runtime)
    monkeypatch.setattr(lab, "DOCKERFILE", tmp_path / "Dockerfile")
    monkeypatch.setattr(lab, "require_free_space", lambda _: None)
    monkeypatch.setattr(lab, "image_source_hashes", lambda: hashes)
    monkeypatch.setattr(lab, "verification_source_hashes", lambda: hashes)
    monkeypatch.setattr(
        lab,
        "summarize_junit",
        lambda _: {"tests": [{"name": "image", "status": "PASS"}], "passed": 1},
    )
    cleaned = []
    monkeypatch.setattr(lab, "cleanup", lambda run_id, image: cleaned.append((run_id, image)))
    calls = []

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("image", "inspect"):
            value = "sha256:" + "b" * 64 + "\n"
        else:
            value = "28.5.1\n" if args[0] in {"info", "version"} else ""
        return SimpleNamespace(stdout=value, returncode=0)

    monkeypatch.setattr(lab, "docker", docker)
    pytest_calls = []

    def run(args, **kwargs):
        pytest_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lab.subprocess, "run", run)

    assert lab.main() == 0
    build = next(call for call in calls if call[0][0] == "build")
    assert "--pull" in build[0]
    assert f"SIGNAL_RELEASE={lab.release_identity(hashes)}" in build[0]
    assert len(cleaned) == 1
    assert pytest_calls[0][0][3] == "tests/container/test_workflow_consumer_image.py"
    assert pytest_calls[0][1]["env"]["SIGNAL_WORKFLOW_CONSUMER_IMAGE_LAB"] == "1"
    report = json.loads((runtime / "latest.json").read_text())
    assert report["docker_server"] == "28.5.1"
    assert report["local_image_id"] == "sha256:" + "b" * 64
    assert report["production_authority"] is False
    assert report["cleanup"] == "completed"
    assert "secret" not in json.dumps(report)


def test_runner_attempts_exact_cleanup_when_build_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(lab, "DOCKERFILE", tmp_path / "Dockerfile")
    monkeypatch.setattr(lab, "require_free_space", lambda _: None)
    monkeypatch.setattr(lab, "image_source_hashes", lambda: {"source.py": "a" * 64})
    monkeypatch.setattr(lab, "verification_source_hashes", lambda: {"source.py": "a" * 64})
    cleaned = []
    monkeypatch.setattr(lab, "cleanup", lambda run_id, image: cleaned.append((run_id, image)))

    def docker(*args, **kwargs):
        if args[0] == "build":
            raise lab.LabError("sanitized build failure")
        return SimpleNamespace(stdout="28.5.1\n", returncode=0)

    monkeypatch.setattr(lab, "docker", docker)

    with pytest.raises(lab.LabError, match="build failure"):
        lab.main()
    assert len(cleaned) == 1
