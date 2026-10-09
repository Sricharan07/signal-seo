import json
import os
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SIGNAL_WORKFLOW_CONSUMER_IMAGE_LAB") != "1",
    reason="Run through scripts/run-workflow-consumer-image-tests.py.",
)

IMAGE = os.environ.get("SIGNAL_WORKFLOW_CONSUMER_IMAGE", "")
RUN_ID = os.environ.get("SIGNAL_WORKFLOW_CONSUMER_IMAGE_RUN_ID", "")
RELEASE = os.environ.get("SIGNAL_WORKFLOW_CONSUMER_RELEASE", "")
LABEL = "dev.signal.image-lab"
ROOT = Path(__file__).resolve().parents[2]


def docker(*args, check=True, timeout=60):
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def hardened_run_arguments(name=None):
    arguments = [
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=1m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--cpus",
        "0.5",
        "--label",
        f"{LABEL}={RUN_ID}",
    ]
    if name is not None:
        arguments.extend(["--name", name])
    return arguments


def test_built_image_has_exact_nonroot_release_and_probe_contract():
    image = json.loads(docker("image", "inspect", IMAGE).stdout)[0]
    config = image["Config"]

    assert config["User"] == "10001:10001"
    assert config["Entrypoint"] == ["python", "/opt/signal/run-workflow-consumer.py"]
    assert config["StopSignal"] == "SIGTERM"
    assert config["Healthcheck"]["Test"] == [
        "CMD",
        "python",
        "/opt/signal/check-workflow-consumer-health.py",
    ]
    assert config["Labels"]["org.opencontainers.image.revision"] == RELEASE
    environment = dict(item.split("=", 1) for item in config["Env"])
    assert environment["SIGNAL_WORKFLOW_CONSUMER_RELEASE"] == RELEASE
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert not any(
        marker in key.upper()
        for key in environment
        for marker in ["PASSWORD", "TOKEN", "SECRET", "DSN", "PRIVATE_KEY"]
    )


def test_image_imports_runtime_without_network_or_writable_root():
    program = (
        "import json,os,pathlib; writable=True; "
        "exec(\"try:\\n pathlib.Path('/probe').write_text('x')\\n"
        'except OSError:\\n writable=False"); '
        "import signal_core.workflow_consumer_runtime as runtime; "
        "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),"
        "'writable_root':writable,'config':runtime.WorkflowConsumerRuntimeConfig.__name__}))"
    )
    result = docker(
        "run",
        "--rm",
        *hardened_run_arguments(),
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        program,
    )
    record = json.loads(result.stdout)
    assert record == {
        "uid": 10001,
        "gid": 10001,
        "writable_root": False,
        "config": "WorkflowConsumerRuntimeConfig",
    }
    assert result.stderr == ""


def test_default_entrypoint_fails_closed_without_mounted_authority():
    result = docker(
        "run",
        "--rm",
        *hardened_run_arguments(),
        IMAGE,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Missing required setting SIGNAL_SCHEDULER_DSN_FILE.\n"
    assert "Traceback" not in result.stderr


def test_compose_manifest_renders_with_synthetic_file_backed_secrets(tmp_path):
    secret_names = [
        "scheduler_dsn",
        "workflow_dsn",
        "temporal_root_ca",
        "temporal_client_cert",
        "temporal_client_key",
        "database_root_ca",
    ]
    secret_paths = {}
    for name in secret_names:
        path = tmp_path / name
        path.write_text("synthetic-image-lab-only\n")
        path.chmod(0o600)
        secret_paths[name] = str(path)
    env = {
        **os.environ,
        "SIGNAL_WORKFLOW_CONSUMER_REPOSITORY": "registry.example.invalid/signal/consumer",
        "SIGNAL_WORKFLOW_CONSUMER_DIGEST": "d" * 64,
        "SIGNAL_TEMPORAL_ADDRESS": "temporal.internal:7233",
        "SIGNAL_TEMPORAL_NAMESPACE": "signal",
        "SIGNAL_TEMPORAL_SERVER_NAME": "temporal.internal",
        "SIGNAL_WORKFLOW_CONSUMER_KEY": "consumer.image.lab",
        "SIGNAL_CONTROL_NETWORK": "signal-control-image-lab",
        "SIGNAL_SCHEDULER_DSN_FILE": secret_paths["scheduler_dsn"],
        "SIGNAL_WORKFLOW_DSN_FILE": secret_paths["workflow_dsn"],
        "SIGNAL_TEMPORAL_ROOT_CA_FILE": secret_paths["temporal_root_ca"],
        "SIGNAL_TEMPORAL_CLIENT_CERT_FILE": secret_paths["temporal_client_cert"],
        "SIGNAL_TEMPORAL_CLIENT_KEY_FILE": secret_paths["temporal_client_key"],
        "SIGNAL_DATABASE_ROOT_CA_FILE": secret_paths["database_root_ca"],
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            "deploy/workflow-consumer/compose.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rendered = json.loads(result.stdout)
    service = rendered["services"]["workflow-consumer"]
    assert service["image"] == "registry.example.invalid/signal/consumer@sha256:" + "d" * 64
    assert "ports" not in service
    assert "volumes" not in service
    assert service["read_only"] is True
    assert service["restart"] == "on-failure:5"


def test_container_health_moves_unready_to_ready_and_sigterm_exits_zero():
    name = f"signal-consumer-image-{RUN_ID}-health"
    program = textwrap.dedent(
        f"""
        import asyncio
        import signal
        from signal_core.outbox_worker import DeliveryCycleReport
        from signal_core.workflow_consumer_health import (
            WorkflowConsumerHealth,
            WorkflowConsumerHealthServer,
        )

        async def main():
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, stop.set)
            health = WorkflowConsumerHealth(release={RELEASE!r}, stale_after_seconds=30)
            async with WorkflowConsumerHealthServer(health, host="127.0.0.1", port=8081):
                health.mark_running()
                await asyncio.sleep(5)
                health.record_cycle(DeliveryCycleReport())
                await stop.wait()
                health.mark_draining()
            health.mark_stopped()

        asyncio.run(main())
        """
    )
    docker(
        "run",
        "--detach",
        "--init",
        *hardened_run_arguments(name),
        "--health-interval",
        "1s",
        "--health-timeout",
        "2s",
        "--health-start-period",
        "0s",
        "--health-retries",
        "1",
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        program,
    )

    probe = _wait_for_probe(name, expected=1, timeout=4)
    assert probe is not None
    assert probe.returncode == 1
    assert probe.stderr == "Workflow consumer is not ready.\n"
    assert _wait_for_health(name, "healthy", timeout=12)
    assert (
        docker("exec", name, "python", "/opt/signal/check-workflow-consumer-health.py").stdout == ""
    )

    docker("container", "stop", "--time", "8", name, timeout=15)
    state = json.loads(docker("container", "inspect", name).stdout)[0]["State"]
    assert state["Running"] is False
    assert state["ExitCode"] == 0


def test_external_supervisor_bounds_repeated_failed_process_attempts():
    name = f"signal-consumer-image-{RUN_ID}-restart"
    program = (
        "import pathlib,sys; marker=pathlib.Path('/tmp/restarted'); "
        "exists=marker.exists(); marker.write_text('1'); sys.exit(0 if exists else 1)"
    )
    docker(
        "run",
        "--detach",
        "--restart",
        "on-failure:2",
        *hardened_run_arguments(name),
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        program,
    )

    deadline = time.monotonic() + 8
    state = None
    while time.monotonic() < deadline:
        state = json.loads(docker("container", "inspect", name).stdout)[0]
        if state["State"]["Running"] is False and state["RestartCount"] >= 1:
            break
        time.sleep(0.2)

    assert state is not None
    assert state["RestartCount"] == 2
    assert state["State"]["ExitCode"] == 1


def _wait_for_health(name: str, expected: str, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = docker(
            "container",
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
            name,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == expected:
            return True
        time.sleep(0.2)
    return False


def _wait_for_probe(name: str, *, expected: int, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = docker(
            "exec",
            name,
            "python",
            "/opt/signal/check-workflow-consumer-health.py",
            check=False,
        )
        if result.returncode == expected:
            return result
        time.sleep(0.2)
    return None
