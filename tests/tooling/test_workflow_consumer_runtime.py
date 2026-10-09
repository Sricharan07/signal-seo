import asyncio
import io
import json
import signal
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from signal_core.outbox_dispatch import OutboxEnvelope
from signal_core.outbox_worker import DeliveryCycleReport, DeliveryObservation
from signal_core.workflow_consumer import WorkflowPublishObservation
from signal_core.workflow_consumer_health import WorkflowConsumerHealth
from signal_core.workflow_consumer_runtime import (
    JsonLineConsumerObserver,
    WorkflowConsumerConfigurationError,
    WorkflowConsumerRuntimeConfig,
    install_stop_handlers,
    run_workflow_consumer,
)
from temporalio.client import TLSConfig

RELEASE = "sha256:" + "b" * 64


def environment() -> dict[str, str]:
    return {
        "SIGNAL_SCHEDULER_DSN": (
            "host=127.0.0.1 dbname=signal user=signal_scheduler password=scheduler-secret"
        ),
        "SIGNAL_WORKFLOW_DSN": (
            "host=127.0.0.1 dbname=signal user=signal_workflow password=workflow-secret"
        ),
        "SIGNAL_TEMPORAL_ADDRESS": "127.0.0.1:7233",
        "SIGNAL_TEMPORAL_NAMESPACE": "default",
        "SIGNAL_WORKFLOW_CONSUMER_KEY": "workflow.consumer.one",
        "SIGNAL_WORKFLOW_CONSUMER_RELEASE": RELEASE,
        "SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK": "1",
    }


def envelope() -> OutboxEnvelope:
    now = datetime.now(UTC)
    return OutboxEnvelope(
        tenant_id=uuid4(),
        outbox_id=uuid4(),
        event_id=uuid4(),
        site_id=uuid4(),
        command_id=uuid4(),
        aggregate_kind="command",
        event_type="command.accepted",
        schema_version=1,
        payload=b'{"schema_version":1}',
        available_at=now,
        lease_until=now + timedelta(seconds=45),
        attempt_count=2,
    )


def test_environment_builds_closed_loopback_runtime_without_leaking_dsns():
    config = WorkflowConsumerRuntimeConfig.from_environment(environment())

    assert config.temporal_address == "127.0.0.1:7233"
    assert config.temporal_namespace == "default"
    assert config.delivery_config().outbox_batch_size == 1
    assert config.delivery_config().lease_seconds == 45
    assert config.temporal_config().task_queue == "signal.crawl.v1"
    assert config.temporal_tls() is False
    assert "secret" not in repr(config)
    assert "dsn" not in repr(config).lower()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK", "yes"),
        ("SIGNAL_WORKFLOW_CONSUMER_KEY", "UPPER"),
        ("SIGNAL_TEMPORAL_ADDRESS", "https://127.0.0.1:7233"),
        ("SIGNAL_TEMPORAL_NAMESPACE", "UPPER"),
        ("SIGNAL_WORKFLOW_LEASE_SECONDS", "29"),
        ("SIGNAL_WORKFLOW_LEASE_SECONDS", "not-a-number"),
        ("SIGNAL_TEMPORAL_START_TIMEOUT_SECONDS", "61"),
        ("SIGNAL_WORKFLOW_CONNECT_TIMEOUT_SECONDS", "0"),
        ("SIGNAL_WORKFLOW_HEALTH_PORT", "80"),
        ("SIGNAL_WORKFLOW_HEALTH_STALE_SECONDS", "4"),
        ("SIGNAL_WORKFLOW_CONSUMER_RELEASE", "latest"),
    ],
)
def test_environment_rejects_invalid_or_unbounded_values(key, value):
    values = environment()
    values[key] = value

    with pytest.raises(WorkflowConsumerConfigurationError):
        WorkflowConsumerRuntimeConfig.from_environment(values)


def test_health_staleness_must_cover_configured_idle_scan_interval():
    values = environment()
    values["SIGNAL_WORKFLOW_IDLE_SECONDS"] = "10"
    values["SIGNAL_WORKFLOW_HEALTH_STALE_SECONDS"] = "20"

    with pytest.raises(WorkflowConsumerConfigurationError, match="two idle"):
        WorkflowConsumerRuntimeConfig.from_environment(values)


@pytest.mark.parametrize(
    "missing",
    [
        "SIGNAL_SCHEDULER_DSN",
        "SIGNAL_WORKFLOW_DSN",
        "SIGNAL_TEMPORAL_ADDRESS",
        "SIGNAL_TEMPORAL_NAMESPACE",
        "SIGNAL_WORKFLOW_CONSUMER_KEY",
        "SIGNAL_WORKFLOW_CONSUMER_RELEASE",
    ],
)
def test_environment_requires_every_authority_boundary(missing):
    values = environment()
    del values[missing]

    with pytest.raises(WorkflowConsumerConfigurationError, match=missing):
        WorkflowConsumerRuntimeConfig.from_environment(values)


def test_database_roles_cannot_be_interchanged():
    values = environment()
    values["SIGNAL_WORKFLOW_DSN"] = values["SIGNAL_SCHEDULER_DSN"]

    with pytest.raises(WorkflowConsumerConfigurationError, match="role"):
        WorkflowConsumerRuntimeConfig.from_environment(values)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SIGNAL_TEMPORAL_ADDRESS", "temporal.internal:7233"),
        (
            "SIGNAL_SCHEDULER_DSN",
            "host=db.internal dbname=signal user=signal_scheduler password=secret",
        ),
    ],
)
def test_insecure_mode_is_literal_loopback_only(key, value):
    values = environment()
    values[key] = value

    with pytest.raises(WorkflowConsumerConfigurationError, match="loopback"):
        WorkflowConsumerRuntimeConfig.from_environment(values)


def _production_environment(tmp_path) -> dict[str, str]:
    values = environment()
    values["SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK"] = "0"
    values["SIGNAL_TEMPORAL_ADDRESS"] = "temporal.internal:7233"
    for name, role in [
        ("SIGNAL_SCHEDULER_DSN", "signal_scheduler"),
        ("SIGNAL_WORKFLOW_DSN", "signal_workflow"),
    ]:
        path = tmp_path / name.lower()
        path.write_text(
            f"host=db.internal dbname=signal user={role} password=secret sslmode=verify-full\n"
        )
        path.chmod(0o600)
        del values[name]
        values[f"{name}_FILE"] = str(path)
    return values


def test_non_loopback_runtime_requires_secret_files_and_verified_database_tls(tmp_path):
    values = environment()
    values["SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK"] = "0"
    values["SIGNAL_TEMPORAL_ADDRESS"] = "temporal.internal:7233"

    with pytest.raises(WorkflowConsumerConfigurationError, match="DSN_FILE"):
        WorkflowConsumerRuntimeConfig.from_environment(values)

    values = _production_environment(tmp_path)
    scheduler_path = Path(values["SIGNAL_SCHEDULER_DSN_FILE"])
    scheduler_path.write_text(
        "host=db.internal dbname=signal user=signal_scheduler password=secret\n"
    )

    with pytest.raises(WorkflowConsumerConfigurationError, match="verify-full"):
        WorkflowConsumerRuntimeConfig.from_environment(values)


def test_tls_material_is_bounded_and_private_key_is_not_group_readable(tmp_path):
    root = tmp_path / "root.pem"
    cert = tmp_path / "client.pem"
    key = tmp_path / "private.pem"
    root.write_bytes(b"root")
    cert.write_bytes(b"cert")
    key.write_bytes(b"key")
    key.chmod(0o600)
    config = WorkflowConsumerRuntimeConfig(
        scheduler_dsn=(
            "host=db.internal dbname=signal user=signal_scheduler "
            "password=secret sslmode=verify-full"
        ),
        workflow_dsn=(
            "host=db.internal dbname=signal user=signal_workflow "
            "password=secret sslmode=verify-full"
        ),
        temporal_address="temporal.internal:7233",
        temporal_namespace="signal",
        worker_key="workflow.consumer.one",
        release=RELEASE,
        temporal_root_ca_file=root,
        temporal_client_cert_file=cert,
        temporal_client_key_file=key,
        temporal_server_name="temporal.internal",
    )

    tls = config.temporal_tls()

    assert isinstance(tls, TLSConfig)
    assert tls.server_root_ca_cert == b"root"
    assert tls.client_cert == b"cert"
    assert tls.client_private_key == b"key"
    key.chmod(0o644)
    with pytest.raises(WorkflowConsumerConfigurationError, match="permissions"):
        replace(config)


def test_tls_client_identity_must_be_complete(tmp_path):
    cert = tmp_path / "client.pem"
    cert.write_bytes(b"cert")
    values = _production_environment(tmp_path)
    values["SIGNAL_TEMPORAL_CLIENT_CERT_FILE"] = str(cert)

    with pytest.raises(WorkflowConsumerConfigurationError, match="together"):
        WorkflowConsumerRuntimeConfig.from_environment(values)


def test_database_secret_files_are_private_bounded_and_unambiguous(tmp_path):
    values = _production_environment(tmp_path)
    config = WorkflowConsumerRuntimeConfig.from_environment(values)
    assert "password=secret" in config.scheduler_dsn
    assert "secret" not in repr(config)

    values["SIGNAL_SCHEDULER_DSN"] = "forbidden-environment-value"
    with pytest.raises(WorkflowConsumerConfigurationError, match="Conflicting"):
        WorkflowConsumerRuntimeConfig.from_environment(values)
    del values["SIGNAL_SCHEDULER_DSN"]

    scheduler = Path(values["SIGNAL_SCHEDULER_DSN_FILE"])
    scheduler.chmod(0o644)
    with pytest.raises(WorkflowConsumerConfigurationError, match="invalid"):
        WorkflowConsumerRuntimeConfig.from_environment(values)
    scheduler.chmod(0o600)
    scheduler.write_text("first\nsecond\n")
    with pytest.raises(WorkflowConsumerConfigurationError, match="invalid"):
        WorkflowConsumerRuntimeConfig.from_environment(values)

    scheduler.unlink()
    target = tmp_path / "actual-secret"
    target.write_text("not followed")
    target.chmod(0o600)
    scheduler.symlink_to(target)
    with pytest.raises(WorkflowConsumerConfigurationError, match="unavailable"):
        WorkflowConsumerRuntimeConfig.from_environment(values)


def test_json_observer_emits_only_closed_identifiers_and_state():
    item = envelope()
    stream = io.StringIO()
    observer = JsonLineConsumerObserver(stream)
    observer(
        DeliveryObservation(
            "claimed",
            tenant_id=item.tenant_id,
            outbox_id=item.outbox_id,
            event_id=item.event_id,
            attempt_count=item.attempt_count,
        )
    )
    observer(
        WorkflowPublishObservation(
            outcome="workflow_admitted",
            tenant_id=item.tenant_id,
            outbox_id=item.outbox_id,
            event_id=item.event_id,
            command_id=item.command_id,
            attempt_count=item.attempt_count,
        )
    )
    observer.lifecycle("consumer_started")
    observer.lifecycle("consumer_draining")
    observer.lifecycle("consumer_stopped", cycles=4)

    records = [json.loads(line) for line in stream.getvalue().splitlines()]

    assert [record["kind"] for record in records] == [
        "delivery",
        "workflow_publish",
        "lifecycle",
        "lifecycle",
        "lifecycle",
    ]
    assert records[1]["command_id"] == str(item.command_id)
    assert records[-1]["cycles"] == 4
    assert all(record["schema_version"] == 1 for record in records)
    assert "payload" not in stream.getvalue()
    assert "secret" not in stream.getvalue()


def test_runtime_skips_connections_when_shutdown_precedes_start():
    async def exercise():
        stop = asyncio.Event()
        stop.set()
        return await run_workflow_consumer(
            WorkflowConsumerRuntimeConfig.from_environment(environment()),
            stop=stop,
            observer=JsonLineConsumerObserver(io.StringIO()),
        )

    assert asyncio.run(exercise()) == 0


def test_runtime_marks_ready_only_after_dependency_backed_cycle(monkeypatch):
    observations = io.StringIO()
    health = WorkflowConsumerHealth(release=RELEASE, stale_after_seconds=15)
    ready_during_cycle = []

    class Client:
        async def start_workflow(self, *args, **kwargs):
            raise AssertionError("No envelope should be published in this test.")

    async def connect(*args, **kwargs):
        return Client()

    class Worker:
        def __init__(self, **kwargs):
            self.cycle_observer = kwargs["cycle_observer"]

        async def run_forever(self, stop):
            assert health.snapshot("ready").reason == "awaiting_database"
            self.cycle_observer(DeliveryCycleReport())
            ready_during_cycle.append(health.snapshot("ready").status)
            stop.set()
            return 1

    monkeypatch.setattr("signal_core.workflow_consumer_runtime.Client.connect", connect)
    monkeypatch.setattr("signal_core.workflow_consumer_runtime.AsyncDeliveryWorker", Worker)

    async def exercise():
        return await run_workflow_consumer(
            WorkflowConsumerRuntimeConfig.from_environment(environment()),
            stop=asyncio.Event(),
            observer=JsonLineConsumerObserver(observations),
            health=health,
        )

    assert asyncio.run(exercise()) == 1
    assert ready_during_cycle == ["ready"]
    assert health.phase == "stopped"
    records = [json.loads(line) for line in observations.getvalue().splitlines()]
    assert [record["outcome"] for record in records] == [
        "consumer_started",
        "consumer_draining",
        "consumer_stopped",
    ]


def test_runtime_failure_is_sanitized_in_lifecycle_and_closes_health(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("private Temporal endpoint detail")

    monkeypatch.setattr("signal_core.workflow_consumer_runtime.Client.connect", fail)
    stream = io.StringIO()
    health = WorkflowConsumerHealth(release=RELEASE, stale_after_seconds=15)

    async def exercise():
        with pytest.raises(RuntimeError, match="private"):
            await run_workflow_consumer(
                WorkflowConsumerRuntimeConfig.from_environment(environment()),
                stop=asyncio.Event(),
                observer=JsonLineConsumerObserver(stream),
                health=health,
            )

    asyncio.run(exercise())
    assert health.phase == "stopped"
    assert "private" not in stream.getvalue()
    assert json.loads(stream.getvalue())["outcome"] == "consumer_failed"


def test_runtime_rejects_health_from_another_release():
    health = WorkflowConsumerHealth(release="sha256:" + "c" * 64, stale_after_seconds=15)

    async def exercise():
        with pytest.raises(ValueError, match="release-matched"):
            await run_workflow_consumer(
                WorkflowConsumerRuntimeConfig.from_environment(environment()),
                stop=asyncio.Event(),
                observer=JsonLineConsumerObserver(io.StringIO()),
                health=health,
            )

    asyncio.run(exercise())


def test_stop_handlers_translate_int_and_term_to_one_event(monkeypatch):
    registrations = []

    class Loop:
        def add_signal_handler(self, process_signal, callback):
            registrations.append((process_signal, callback))

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: Loop())
    stop = asyncio.Event()

    callbacks = []
    install_stop_handlers(stop, on_stop=lambda: callbacks.append("draining"))

    assert [item[0] for item in registrations] == [signal.SIGINT, signal.SIGTERM]
    registrations[0][1]()
    assert stop.is_set()
    registrations[1][1]()
    assert callbacks == ["draining"]


def test_stop_handler_sets_event_even_when_drain_observer_fails(monkeypatch):
    registrations = []

    class Loop:
        def add_signal_handler(self, process_signal, callback):
            registrations.append(callback)

    def fail():
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: Loop())
    stop = asyncio.Event()
    install_stop_handlers(stop, on_stop=fail)

    with pytest.raises(RuntimeError, match="telemetry"):
        registrations[0]()
    assert stop.is_set()


def test_process_entrypoint_rejects_missing_configuration_without_traceback():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/run-workflow-consumer.py"],
        cwd=root,
        env={"PYTHONPATH": str(root / "services/control_plane/src")},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Missing required setting SIGNAL_SCHEDULER_DSN_FILE.\n"
    assert "Traceback" not in result.stderr
    assert "secret" not in result.stderr


@pytest.mark.parametrize(
    "value",
    [object(), "claimed", DeliveryObservation("unrecognized")],
)
def test_json_observer_rejects_unknown_shapes(value):
    with pytest.raises(ValueError, match="observation"):
        JsonLineConsumerObserver(io.StringIO())(value)
