"""Configuration and process composition for the workflow command consumer."""

import asyncio
import json
import os
import re
import signal
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from stat import S_IMODE, S_ISREG
from typing import TextIO
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from temporalio.client import Client, TLSConfig

from signal_core.outbox_worker import (
    AsyncDeliveryWorker,
    DeliveryObservation,
    DeliveryWorkerConfig,
    PostgresOutboxStore,
)
from signal_core.workflow_consumer import WorkflowPublishObservation, temporal_workflow_publisher
from signal_core.workflow_consumer_health import (
    WorkflowConsumerHealth,
    WorkflowConsumerHealthError,
    validate_workflow_consumer_release,
)
from signal_core.workflow_start import TemporalWorkflowStartConfig, TemporalWorkflowStarter


class WorkflowConsumerConfigurationError(ValueError):
    """Runtime configuration is incomplete or violates a closed safety boundary."""


@dataclass(frozen=True, repr=False)
class WorkflowConsumerRuntimeConfig:
    scheduler_dsn: str = field(repr=False)
    workflow_dsn: str = field(repr=False)
    temporal_address: str
    temporal_namespace: str
    worker_key: str
    release: str
    temporal_root_ca_file: Path | None = None
    temporal_client_cert_file: Path | None = None
    temporal_client_key_file: Path | None = field(default=None, repr=False)
    temporal_server_name: str | None = None
    allow_insecure_loopback: bool = False
    tenant_page_size: int = 25
    lease_seconds: int = 45
    retry_delay_seconds: int = 30
    idle_delay_seconds: float = 1.0
    active_delay_seconds: float = 0.05
    temporal_rpc_timeout_seconds: float = 10.0
    connect_timeout_seconds: float = 10.0
    health_host: str = "127.0.0.1"
    health_port: int = 8081
    health_stale_seconds: float = 15.0

    def __post_init__(self) -> None:
        scheduler = _database_identity(self.scheduler_dsn, "signal_scheduler")
        workflow = _database_identity(self.workflow_dsn, "signal_workflow")
        host = _temporal_host(self.temporal_address)
        if _NAMESPACE.fullmatch(self.temporal_namespace) is None:
            raise WorkflowConsumerConfigurationError("Invalid Temporal namespace.")
        try:
            validate_workflow_consumer_release(self.release)
        except WorkflowConsumerHealthError as error:
            raise WorkflowConsumerConfigurationError(str(error)) from None
        if self.health_host not in {"127.0.0.1", "::1"}:
            raise WorkflowConsumerConfigurationError(
                "Workflow consumer health must bind to literal loopback."
            )
        if (
            isinstance(self.health_port, bool)
            or not isinstance(self.health_port, int)
            or not 1024 <= self.health_port <= 65535
        ):
            raise WorkflowConsumerConfigurationError("Invalid workflow consumer health port.")
        _bounded_number(self.health_stale_seconds, 5.0, 300.0, "health staleness interval")

        delivery = self.delivery_config()
        temporal = self.temporal_config()
        if delivery.lease_seconds < temporal.rpc_timeout_seconds + 20:
            raise WorkflowConsumerConfigurationError(
                "The outbox lease must cover the bounded workflow-start sequence."
            )
        _bounded_number(self.connect_timeout_seconds, 1.0, 30.0, "connect timeout")
        if self.health_stale_seconds < self.idle_delay_seconds * 2 + 1:
            raise WorkflowConsumerConfigurationError(
                "Health staleness must cover at least two idle scan intervals."
            )

        certificate_paths = (
            self.temporal_root_ca_file,
            self.temporal_client_cert_file,
            self.temporal_client_key_file,
        )
        for path in (item for item in certificate_paths if item is not None):
            _validate_secret_file(
                path,
                private_key=path == self.temporal_client_key_file,
            )
        if (self.temporal_client_cert_file is None) != (self.temporal_client_key_file is None):
            raise WorkflowConsumerConfigurationError(
                "Temporal client certificate and key must be configured together."
            )
        if (
            self.temporal_server_name is not None
            and _SERVER_NAME.fullmatch(self.temporal_server_name) is None
        ):
            raise WorkflowConsumerConfigurationError("Invalid Temporal TLS server name.")

        if self.allow_insecure_loopback:
            if host not in {"127.0.0.1", "::1"}:
                raise WorkflowConsumerConfigurationError(
                    "Insecure Temporal transport is restricted to literal loopback."
                )
            if any(path is not None for path in certificate_paths):
                raise WorkflowConsumerConfigurationError(
                    "TLS files cannot accompany insecure loopback mode."
                )
            for database in (scheduler, workflow):
                if database.get("host") not in {"127.0.0.1", "::1"}:
                    raise WorkflowConsumerConfigurationError(
                        "Insecure database transport is restricted to literal loopback."
                    )
        else:
            for database in (scheduler, workflow):
                if database.get("sslmode") != "verify-full":
                    raise WorkflowConsumerConfigurationError(
                        "Workflow consumer databases require sslmode=verify-full."
                    )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "WorkflowConsumerRuntimeConfig":
        values = os.environ if environ is None else environ
        insecure = _environment_bool(values, "SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK")
        return cls(
            scheduler_dsn=_required_dsn(values, "SIGNAL_SCHEDULER_DSN", insecure=insecure),
            workflow_dsn=_required_dsn(values, "SIGNAL_WORKFLOW_DSN", insecure=insecure),
            temporal_address=_required(values, "SIGNAL_TEMPORAL_ADDRESS"),
            temporal_namespace=_required(values, "SIGNAL_TEMPORAL_NAMESPACE"),
            worker_key=_required(values, "SIGNAL_WORKFLOW_CONSUMER_KEY"),
            release=_required(values, "SIGNAL_WORKFLOW_CONSUMER_RELEASE"),
            temporal_root_ca_file=_optional_path(values, "SIGNAL_TEMPORAL_ROOT_CA_FILE"),
            temporal_client_cert_file=_optional_path(values, "SIGNAL_TEMPORAL_CLIENT_CERT_FILE"),
            temporal_client_key_file=_optional_path(values, "SIGNAL_TEMPORAL_CLIENT_KEY_FILE"),
            temporal_server_name=_optional(values, "SIGNAL_TEMPORAL_SERVER_NAME"),
            allow_insecure_loopback=insecure,
            tenant_page_size=_environment_int(values, "SIGNAL_WORKFLOW_TENANT_PAGE_SIZE", 25),
            lease_seconds=_environment_int(values, "SIGNAL_WORKFLOW_LEASE_SECONDS", 45),
            retry_delay_seconds=_environment_int(values, "SIGNAL_WORKFLOW_RETRY_SECONDS", 30),
            idle_delay_seconds=_environment_float(values, "SIGNAL_WORKFLOW_IDLE_SECONDS", 1.0),
            active_delay_seconds=_environment_float(values, "SIGNAL_WORKFLOW_ACTIVE_SECONDS", 0.05),
            temporal_rpc_timeout_seconds=_environment_float(
                values, "SIGNAL_TEMPORAL_START_TIMEOUT_SECONDS", 10.0
            ),
            connect_timeout_seconds=_environment_float(
                values, "SIGNAL_WORKFLOW_CONNECT_TIMEOUT_SECONDS", 10.0
            ),
            health_port=_environment_int(values, "SIGNAL_WORKFLOW_HEALTH_PORT", 8081),
            health_stale_seconds=_environment_float(
                values, "SIGNAL_WORKFLOW_HEALTH_STALE_SECONDS", 15.0
            ),
        )

    def delivery_config(self) -> DeliveryWorkerConfig:
        try:
            return DeliveryWorkerConfig(
                worker_key=self.worker_key,
                tenant_page_size=self.tenant_page_size,
                outbox_batch_size=1,
                lease_seconds=self.lease_seconds,
                retry_delay_seconds=self.retry_delay_seconds,
                idle_delay_seconds=self.idle_delay_seconds,
                active_delay_seconds=self.active_delay_seconds,
            )
        except ValueError as error:
            raise WorkflowConsumerConfigurationError(str(error)) from None

    def temporal_config(self) -> TemporalWorkflowStartConfig:
        try:
            return TemporalWorkflowStartConfig(
                task_queue="signal.crawl.v1",
                rpc_timeout_seconds=self.temporal_rpc_timeout_seconds,
            )
        except ValueError as error:
            raise WorkflowConsumerConfigurationError(str(error)) from None

    def temporal_tls(self) -> bool | TLSConfig:
        if self.allow_insecure_loopback:
            return False
        return TLSConfig(
            server_root_ca_cert=_read_bounded(
                self.temporal_root_ca_file,
                private_key=False,
            ),
            client_cert=_read_bounded(
                self.temporal_client_cert_file,
                private_key=False,
            ),
            client_private_key=_read_bounded(
                self.temporal_client_key_file,
                private_key=True,
            ),
            verification_server_name=self.temporal_server_name,
        )


class JsonLineConsumerObserver:
    """Write a closed, sanitized process event schema without payloads or errors."""

    def __init__(self, stream: TextIO) -> None:
        if not callable(getattr(stream, "write", None)) or not callable(
            getattr(stream, "flush", None)
        ):
            raise ValueError("A writable observation stream is required.")
        self._stream = stream

    def __call__(self, observation: object) -> None:
        if isinstance(observation, DeliveryObservation):
            if observation.outcome not in _DELIVERY_OUTCOMES:
                raise ValueError("Unsupported workflow consumer observation.")
            if (
                any(
                    value is not None and not isinstance(value, UUID)
                    for value in (
                        observation.tenant_id,
                        observation.outbox_id,
                        observation.event_id,
                    )
                )
                or observation.attempt_count is not None
                and (
                    isinstance(observation.attempt_count, bool)
                    or not isinstance(observation.attempt_count, int)
                    or not 1 <= observation.attempt_count <= 2147483647
                )
            ):
                raise ValueError("Unsupported workflow consumer observation.")
            identifiers = {
                "tenant_id": observation.tenant_id,
                "outbox_id": observation.outbox_id,
                "event_id": observation.event_id,
                "attempt_count": observation.attempt_count,
            }
            outcome = observation.outcome
            kind = "delivery"
        elif isinstance(observation, WorkflowPublishObservation):
            identifiers = {
                "tenant_id": observation.tenant_id,
                "outbox_id": observation.outbox_id,
                "event_id": observation.event_id,
                "command_id": observation.command_id,
                "attempt_count": observation.attempt_count,
            }
            outcome = observation.outcome
            kind = "workflow_publish"
        else:
            raise ValueError("Unsupported workflow consumer observation.")
        record = {
            "schema_version": 1,
            "service": "workflow_command_consumer",
            "kind": kind,
            "outcome": outcome,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        record.update(
            {
                key: str(value) if isinstance(value, UUID) else value
                for key, value in identifiers.items()
                if value is not None
            }
        )
        self._write(record)

    def lifecycle(self, outcome: str, *, cycles: int | None = None) -> None:
        if outcome not in {
            "consumer_started",
            "consumer_draining",
            "consumer_stopped",
            "consumer_failed",
        }:
            raise ValueError("Unsupported workflow consumer lifecycle outcome.")
        record: dict[str, object] = {
            "schema_version": 1,
            "service": "workflow_command_consumer",
            "kind": "lifecycle",
            "outcome": outcome,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        if cycles is not None:
            record["cycles"] = cycles
        self._write(record)

    def _write(self, record: dict[str, object]) -> None:
        self._stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self._stream.flush()


async def run_workflow_consumer(
    config: WorkflowConsumerRuntimeConfig,
    *,
    stop: asyncio.Event,
    observer: JsonLineConsumerObserver,
    health: WorkflowConsumerHealth | None = None,
) -> int:
    """Connect once, process until signalled, and drain the current envelope."""
    if not isinstance(config, WorkflowConsumerRuntimeConfig):
        raise ValueError("A validated workflow consumer configuration is required.")
    if not isinstance(stop, asyncio.Event):
        raise ValueError("A workflow consumer stop event is required.")
    if not isinstance(observer, JsonLineConsumerObserver):
        raise ValueError("A workflow consumer observer is required.")
    if health is not None and (
        not isinstance(health, WorkflowConsumerHealth) or health.release != config.release
    ):
        raise ValueError("A release-matched workflow consumer health state is required.")
    if stop.is_set():
        if health is not None:
            health.mark_draining()
            health.mark_stopped()
        return 0

    try:
        async with asyncio.timeout(float(config.connect_timeout_seconds)):
            client = await Client.connect(
                config.temporal_address,
                namespace=config.temporal_namespace,
                tls=config.temporal_tls(),
                identity=config.worker_key,
            )

        scheduler_factory = _connection_factory(
            config.scheduler_dsn,
            application_name=f"signal-workflow-dispatch-{config.worker_key}",
        )
        workflow_factory = _connection_factory(
            config.workflow_dsn,
            application_name=f"signal-workflow-project-{config.worker_key}",
        )
        publisher = temporal_workflow_publisher(
            connection_factory=workflow_factory,
            starter=TemporalWorkflowStarter(client, config=config.temporal_config()),
            observer=observer,
        )
        worker = AsyncDeliveryWorker(
            store=PostgresOutboxStore(scheduler_factory),
            publisher=publisher,
            config=config.delivery_config(),
            observer=observer,
            cycle_observer=None if health is None else health.record_cycle,
        )
        if health is not None:
            health.mark_running()
        _safe_lifecycle(observer, "consumer_started")
        cycles = await worker.run_forever(stop)
        if health is not None and health.mark_draining():
            _safe_lifecycle(observer, "consumer_draining")
        _safe_lifecycle(observer, "consumer_stopped", cycles=cycles)
        return cycles
    except Exception:
        _safe_lifecycle(observer, "consumer_failed")
        raise
    finally:
        if health is not None:
            health.mark_stopped()


def install_stop_handlers(
    stop: asyncio.Event,
    *,
    on_stop: Callable[[], None] | None = None,
) -> None:
    """Translate process termination into cooperative draining."""
    if not isinstance(stop, asyncio.Event):
        raise ValueError("A workflow consumer stop event is required.")
    if on_stop is not None and not callable(on_stop):
        raise ValueError("A workflow consumer stop callback must be callable.")
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        if stop.is_set():
            return
        try:
            if on_stop is not None:
                on_stop()
        finally:
            stop.set()

    for process_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(process_signal, request_stop)


def _connection_factory(dsn: str, *, application_name: str):
    @contextmanager
    def connect():
        with psycopg.connect(
            dsn,
            autocommit=True,
            connect_timeout=3,
            application_name=application_name,
        ) as connection:
            yield connection

    return connect


def _database_identity(dsn: object, expected_user: str) -> dict[str, str]:
    if not isinstance(dsn, str) or not dsn or len(dsn) > 4096:
        raise WorkflowConsumerConfigurationError("Invalid workflow consumer database DSN.")
    try:
        values = conninfo_to_dict(dsn)
    except psycopg.Error:
        raise WorkflowConsumerConfigurationError(
            "Invalid workflow consumer database DSN."
        ) from None
    if values.get("user") != expected_user or not values.get("dbname"):
        raise WorkflowConsumerConfigurationError(
            "Workflow consumer database role or database is invalid."
        )
    return values


def _temporal_host(address: object) -> str:
    if not isinstance(address, str) or len(address) > 300:
        raise WorkflowConsumerConfigurationError("Invalid Temporal address.")
    match = _TEMPORAL_ADDRESS.fullmatch(address)
    if match is None or int(match.group("port")) > 65535:
        raise WorkflowConsumerConfigurationError("Invalid Temporal address.")
    host = match.group("host")
    return host[1:-1] if host.startswith("[") else host


def _required(values: Mapping[str, str], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise WorkflowConsumerConfigurationError(f"Missing required setting {key}.")
    return value


def _required_dsn(values: Mapping[str, str], key: str, *, insecure: bool) -> str:
    direct = values.get(key)
    file_value = values.get(f"{key}_FILE")
    if direct is not None and file_value is not None:
        raise WorkflowConsumerConfigurationError(f"Conflicting settings for {key}.")
    if direct is not None:
        if not insecure:
            raise WorkflowConsumerConfigurationError(
                f"Production database credentials require {key}_FILE."
            )
        return _required(values, key)
    if file_value is None:
        missing = key if insecure else f"{key}_FILE"
        raise WorkflowConsumerConfigurationError(f"Missing required setting {missing}.")
    return _read_secret_text(Path(file_value), key=f"{key}_FILE")


def _optional(values: Mapping[str, str], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    if not value:
        raise WorkflowConsumerConfigurationError(f"Invalid setting {key}.")
    return value


def _optional_path(values: Mapping[str, str], key: str) -> Path | None:
    value = _optional(values, key)
    return None if value is None else Path(value)


def _read_secret_text(path: Path, *, key: str) -> str:
    if not path.is_absolute():
        raise WorkflowConsumerConfigurationError(f"Invalid setting {key}.")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > 4096
            or S_IMODE(metadata.st_mode) & 0o077
        ):
            raise WorkflowConsumerConfigurationError(f"Secret file for {key} is invalid.")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            raw = stream.read(4097)
    except OSError:
        raise WorkflowConsumerConfigurationError(f"Secret file for {key} is unavailable.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        value = raw.decode("utf-8")
    except UnicodeError:
        raise WorkflowConsumerConfigurationError(f"Secret file for {key} is unavailable.") from None
    if value.endswith("\n"):
        value = value[:-1]
    if not value or any(character in value for character in "\r\n\x00"):
        raise WorkflowConsumerConfigurationError(f"Secret file for {key} is invalid.")
    return value


def _safe_lifecycle(
    observer: JsonLineConsumerObserver,
    outcome: str,
    *,
    cycles: int | None = None,
) -> None:
    try:
        observer.lifecycle(outcome, cycles=cycles)
    except Exception:
        return


def _environment_bool(values: Mapping[str, str], key: str) -> bool:
    value = values.get(key, "0")
    if value not in {"0", "1"}:
        raise WorkflowConsumerConfigurationError(f"Invalid setting {key}.")
    return value == "1"


def _environment_int(values: Mapping[str, str], key: str, default: int) -> int:
    value = values.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise WorkflowConsumerConfigurationError(f"Invalid setting {key}.") from None


def _environment_float(values: Mapping[str, str], key: str, default: float) -> float:
    value = values.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise WorkflowConsumerConfigurationError(f"Invalid setting {key}.") from None


def _validate_secret_file(path: object, *, private_key: bool) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise WorkflowConsumerConfigurationError("Temporal TLS files require absolute paths.")
    try:
        metadata = path.stat()
    except OSError:
        raise WorkflowConsumerConfigurationError("Temporal TLS material is unavailable.") from None
    if (
        path.is_symlink()
        or not path.is_file()
        or metadata.st_size <= 0
        or metadata.st_size > 1024 * 1024
    ):
        raise WorkflowConsumerConfigurationError("Temporal TLS material is invalid.")
    if private_key and S_IMODE(metadata.st_mode) & 0o077:
        raise WorkflowConsumerConfigurationError("Temporal client key permissions are too broad.")


def _read_bounded(path: Path | None, *, private_key: bool) -> bytes | None:
    if path is None:
        return None
    _validate_secret_file(path, private_key=private_key)
    try:
        value = path.read_bytes()
    except OSError:
        raise WorkflowConsumerConfigurationError("Temporal TLS material is unavailable.") from None
    if not value or len(value) > 1024 * 1024:
        raise WorkflowConsumerConfigurationError("Temporal TLS material is invalid.")
    return value


def _bounded_number(value: object, minimum: float, maximum: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkflowConsumerConfigurationError(f"Invalid {name}.")
    converted = float(value)
    if not minimum <= converted <= maximum:
        raise WorkflowConsumerConfigurationError(f"Invalid {name}.")
    return converted


_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
_SERVER_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_TEMPORAL_ADDRESS = re.compile(
    r"(?P<host>127\.0\.0\.1|\[::1\]|[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?):"
    r"(?P<port>[1-9][0-9]{0,4})"
)
_DELIVERY_OUTCOMES = {
    "tenant_scan_failed",
    "claim_failed",
    "claimed",
    "publish_outcome_unknown",
    "ack_lease_lost",
    "ack_failed",
    "delivered",
    "reschedule_lease_lost",
    "reschedule_failed",
    "rescheduled",
}
