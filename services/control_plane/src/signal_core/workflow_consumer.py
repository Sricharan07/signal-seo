"""Compose one accepted command event into a durably recorded Temporal start."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from psycopg import Connection

from signal_core.outbox_dispatch import OutboxEnvelope
from signal_core.outbox_worker import PublishNotAccepted, PublishOutcomeUnknown
from signal_core.workflow_admission import (
    WorkflowAdmission,
    WorkflowAdmissionRejected,
    admit_command_event,
)
from signal_core.workflow_start import (
    TemporalWorkflowStarter,
    WorkflowStarted,
    WorkflowStartReceipt,
    record_workflow_started,
)


class WorkflowStore(Protocol):
    def admit(self, envelope: OutboxEnvelope) -> WorkflowAdmission: ...

    def record(
        self,
        admission: WorkflowAdmission,
        receipt: WorkflowStartReceipt,
    ) -> WorkflowStarted: ...


class WorkflowStarter(Protocol):
    async def start(self, admission: object) -> WorkflowStartReceipt: ...


@dataclass(frozen=True)
class WorkflowPublishObservation:
    outcome: str
    tenant_id: UUID
    outbox_id: UUID
    event_id: UUID
    command_id: UUID
    attempt_count: int

    def __post_init__(self) -> None:
        if (
            self.outcome not in _OBSERVATION_OUTCOMES
            or any(
                not isinstance(value, UUID)
                for value in (
                    self.tenant_id,
                    self.outbox_id,
                    self.event_id,
                    self.command_id,
                )
            )
            or isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or not 1 <= self.attempt_count <= 2147483647
        ):
            raise ValueError("Invalid workflow publish observation.")


class PostgresWorkflowStore:
    """Open a fresh workflow-role connection for each durable transition."""

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Connection]],
    ) -> None:
        if not callable(connection_factory):
            raise ValueError("A workflow connection factory is required.")
        self._connection_factory = connection_factory

    def admit(self, envelope: OutboxEnvelope) -> WorkflowAdmission:
        with self._connection_factory() as connection:
            return admit_command_event(connection, envelope)

    def record(
        self,
        admission: WorkflowAdmission,
        receipt: WorkflowStartReceipt,
    ) -> WorkflowStarted:
        with self._connection_factory() as connection:
            return record_workflow_started(connection, admission, receipt)


class WorkflowEventPublisher:
    """Cross database and Temporal boundaries without holding a transaction open."""

    def __init__(
        self,
        *,
        store: WorkflowStore,
        starter: WorkflowStarter,
        observer: Callable[[WorkflowPublishObservation], None] | None = None,
    ) -> None:
        if not callable(getattr(store, "admit", None)) or not callable(
            getattr(store, "record", None)
        ):
            raise ValueError("Workflow publisher requires a durable workflow store.")
        if not callable(getattr(starter, "start", None)):
            raise ValueError("Workflow publisher requires a Temporal starter.")
        if observer is not None and not callable(observer):
            raise ValueError("Workflow publisher observer must be callable.")
        self._store = store
        self._starter = starter
        self._observer = observer

    async def publish(self, envelope: OutboxEnvelope) -> None:
        if not isinstance(envelope, OutboxEnvelope):
            raise PublishNotAccepted() from None
        if (
            any(
                not isinstance(value, UUID)
                for value in (
                    envelope.tenant_id,
                    envelope.outbox_id,
                    envelope.event_id,
                    envelope.command_id,
                )
            )
            or isinstance(envelope.attempt_count, bool)
            or not isinstance(envelope.attempt_count, int)
            or not 1 <= envelope.attempt_count <= 2147483647
        ):
            raise PublishOutcomeUnknown() from None

        try:
            admission = await asyncio.to_thread(self._store.admit, envelope)
            if (
                not isinstance(admission, WorkflowAdmission)
                or admission.tenant_id != envelope.tenant_id
                or admission.source_event_id != envelope.event_id
                or admission.site_id != envelope.site_id
                or admission.command_id != envelope.command_id
                or admission.state != "admitted"
            ):
                raise RuntimeError("Invalid workflow admission boundary result.")
        except WorkflowAdmissionRejected:
            self._emit("workflow_admission_rejected", envelope)
            raise PublishNotAccepted() from None
        except Exception:
            self._emit("workflow_admission_outcome_unknown", envelope)
            raise PublishOutcomeUnknown() from None
        self._emit("workflow_admitted", envelope)

        try:
            receipt = await self._starter.start(admission)
            if (
                not isinstance(receipt, WorkflowStartReceipt)
                or receipt.workflow_id != admission.workflow_id
            ):
                raise RuntimeError("Invalid Temporal start boundary result.")
        except Exception:
            self._emit("temporal_start_outcome_unknown", envelope)
            raise PublishOutcomeUnknown() from None
        self._emit("temporal_start_confirmed", envelope)

        try:
            started = await asyncio.to_thread(self._store.record, admission, receipt)
            if (
                not isinstance(started, WorkflowStarted)
                or started.command_id != admission.command_id
                or started.workflow_id != admission.workflow_id
                or started.first_run_id != receipt.first_run_id
                or started.state != "running"
            ):
                raise RuntimeError("Invalid workflow projection boundary result.")
        except Exception:
            self._emit("workflow_record_outcome_unknown", envelope)
            raise PublishOutcomeUnknown() from None
        self._emit("workflow_start_recorded", envelope)

    def _emit(self, outcome: str, envelope: OutboxEnvelope) -> None:
        if self._observer is None:
            return
        observation = WorkflowPublishObservation(
            outcome=outcome,
            tenant_id=envelope.tenant_id,
            outbox_id=envelope.outbox_id,
            event_id=envelope.event_id,
            command_id=envelope.command_id,
            attempt_count=envelope.attempt_count,
        )
        try:
            self._observer(observation)
        except Exception:
            return


def temporal_workflow_publisher(
    *,
    connection_factory: Callable[[], AbstractContextManager[Connection]],
    starter: TemporalWorkflowStarter,
    observer: Callable[[WorkflowPublishObservation], None] | None = None,
) -> WorkflowEventPublisher:
    """Build the runtime composition from its narrow provider adapters."""
    return WorkflowEventPublisher(
        store=PostgresWorkflowStore(connection_factory),
        starter=starter,
        observer=observer,
    )


_OBSERVATION_OUTCOMES = {
    "workflow_admission_rejected",
    "workflow_admission_outcome_unknown",
    "workflow_admitted",
    "temporal_start_outcome_unknown",
    "temporal_start_confirmed",
    "workflow_record_outcome_unknown",
    "workflow_start_recorded",
}
