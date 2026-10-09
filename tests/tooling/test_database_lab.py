import json
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import database_lab as lab
import pytest


def test_disk_preflight_fails_before_docker_is_called(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=3 * 1024**3 - 1))
    monkeypatch.setattr(lab, "docker", lambda *a, **k: pytest.fail("Docker must not be invoked"))
    with pytest.raises(lab.LabError, match="3 GiB"):
        with lab.isolated_postgres():
            pytest.fail("Low-space lab cannot start")
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=3 * 1024**3))
    lab.require_free_space(tmp_path)


def test_docker_error_does_not_expose_command_arguments_or_output(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["secret-argument"], stderr="secret-output")

    monkeypatch.setattr(lab.subprocess, "run", fail)
    with pytest.raises(lab.LabError) as result:
        lab.docker("info")
    assert "secret" not in str(result.value)
    assert "CalledProcessError" in str(result.value)


def test_cleanup_filters_by_run_label_and_removes_only_exact_name(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return "unrelated\nrun-owned" if args[1] == "ls" else ""

    monkeypatch.setattr(lab, "docker", docker)
    lab.cleanup("run-owned")
    assert ("container", "rm", "--force", "--volumes", "run-owned") in calls
    assert ("network", "rm", "run-owned") in calls
    assert all(f"label={lab.LABEL}=run-owned" in call for call in calls if call[1] == "ls")
    assert all(call[-1] == "run-owned" for call in calls if call[1] == "rm")


def test_cleanup_does_not_remove_unmatched_resources(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, "docker", lambda *a, **k: calls.append(a) or "unrelated")
    lab.cleanup("run-owned")
    assert all(call[1] == "ls" for call in calls)


def test_cleanup_attempts_network_after_container_cleanup_failure(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("container", "rm"):
            raise lab.LabError("unavailable")
        return "run-owned" if args[1] == "ls" else ""

    monkeypatch.setattr(lab, "docker", docker)
    with pytest.raises(lab.LabError, match="Cleanup unconfirmed"):
        lab.cleanup("run-owned")
    assert ("network", "rm", "run-owned") in calls


def test_network_creation_response_loss_still_attempts_cleanup(monkeypatch):
    created = set()

    def docker(*args, **kwargs):
        if args[0] == "info":
            return "28"
        if args[:2] == ("network", "create"):
            created.add(args[-1])
            raise lab.LabError("response lost")
        if args[:2] == ("network", "ls"):
            return "\n".join(created)
        if args[:2] == ("network", "rm"):
            created.remove(args[-1])
        return ""

    monkeypatch.setattr(lab, "require_free_space", lambda *args, **kwargs: None)
    monkeypatch.setattr(lab, "docker", docker)
    with pytest.raises(lab.LabError, match="response lost"):
        with lab.isolated_postgres():
            pytest.fail("Startup did not acknowledge success")
    assert not created


def test_body_failure_cleans_resources_and_password_is_not_in_argv(monkeypatch):
    created = {"container": set(), "network": set()}

    def docker(*args, **kwargs):
        if args[0] == "info":
            return "28"
        if args[0] == "inspect":
            return json.dumps(
                [
                    {
                        "NetworkSettings": {
                            "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54321"}]}
                        }
                    }
                ]
            )
        kind, action = args[:2]
        if action == "create":
            name = args[-1] if kind == "network" else args[args.index("--name") + 1]
            created[kind].add(name)
            if kind == "container":
                password = kwargs["env"]["POSTGRES_PASSWORD"]
                assert password not in " ".join(args)
                assert args[args.index("--publish") + 1] == "127.0.0.1::5432"
        if action == "ls":
            return "\n".join(created[kind])
        if action == "rm":
            created[kind].remove(args[-1])
        return ""

    monkeypatch.setattr(lab, "require_free_space", lambda *args, **kwargs: None)
    monkeypatch.setattr(lab, "docker", docker)
    monkeypatch.setattr(
        lab.psycopg, "connect", lambda *a, **k: nullcontext(SimpleNamespace(execute=lambda _: None))
    )
    with pytest.raises(RuntimeError, match="body failed"):
        with lab.isolated_postgres():
            raise RuntimeError("body failed")
    assert not any(created.values())


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
