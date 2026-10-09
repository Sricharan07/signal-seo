"""Start one admitted command under a deterministic Temporal identity."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg import Connection
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from signal_core.database import _clean_transaction
from signal_core.workflow_admission import WorkflowAdmission
from signal_core.workflow_contracts import CrawlSiteWorkflowInput


class WorkflowStartOutcomeUnknown(Exception):
    """Signal cannot prove whether Temporal accepted the start request."""


class WorkflowStartRecordRejected(Exception):
    """The admitted workflow projection is no longer available."""


class WorkflowStartRecordConflict(Exception):
    """A different first execution run is already bound to the command."""


class _WorkflowHandle(Protocol):
    @property
    def first_execution_run_id(self) -> str | None: ...


class TemporalClient(Protocol):
    async def start_workflow(
        self,
        workflow: str,
        arg: Any,
        **kwargs: Any,
    ) -> _WorkflowHandle: ...


@dataclass(frozen=True)
class TemporalWorkflowStartConfig:
    task_queue: str = "signal.crawl.v1"
    rpc_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.task_queue, str) or _TASK_QUEUE.fullmatch(self.task_queue) is None:
            raise ValueError("Temporal task queues require 1-64 lowercase ASCII token characters.")
        if isinstance(self.rpc_timeout_seconds, bool) or not isinstance(
            self.rpc_timeout_seconds, (int, float)
        ):
            raise ValueError("Invalid Temporal start timeout.")
        timeout = float(self.rpc_timeout_seconds)
        if not 0.1 <= timeout <= 60.0:
            raise ValueError("Invalid Temporal start timeout.")


@dataclass(frozen=True)
class WorkflowStartReceipt:
    workflow_id: str
    first_run_id: str
    evidence_kind: str


@dataclass(frozen=True)
class WorkflowStarted:
    progress_event_id: UUID
    command_id: UUID
    workflow_id: str
    first_run_id: str
    start_evidence: str
    state: str
    projected_at: datetime
    duplicate: bool


class TemporalWorkflowStarter:
    """Apply Signal's fixed Temporal identity and timeout policy."""

    def __init__(
        self,
        client: TemporalClient,
        *,
        config: TemporalWorkflowStartConfig | None = None,
    ) -> None:
        if not callable(getattr(client, "start_workflow", None)):
            raise ValueError("A Temporal workflow client is required.")
        self._client = client
        self._config = config or TemporalWorkflowStartConfig()

    async def start(self, admission: object) -> WorkflowStartReceipt:
        payload = _workflow_input(admission)
        timeout = float(self._config.rpc_timeout_seconds)
        try:
            async with asyncio.timeout(timeout):
                handle = await self._client.start_workflow(
                    admission.workflow_type,
                    payload,
                    id=admission.workflow_id,
                    task_queue=self._config.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                    rpc_timeout=timedelta(seconds=timeout),
                )
        except WorkflowAlreadyStartedError as error:
            if (
                error.workflow_id != admission.workflow_id
                or error.workflow_type != admission.workflow_type
            ):
                raise WorkflowStartOutcomeUnknown() from None
            first_run_id = _canonical_run_id(error.run_id)
            return WorkflowStartReceipt(
                workflow_id=admission.workflow_id,
                first_run_id=first_run_id,
                evidence_kind="already_started",
            )
        except TimeoutError:
            raise WorkflowStartOutcomeUnknown() from None
        except Exception:
            raise WorkflowStartOutcomeUnknown() from None

        first_run_id = _canonical_run_id(handle.first_execution_run_id)
        return WorkflowStartReceipt(
            workflow_id=admission.workflow_id,
            first_run_id=first_run_id,
            evidence_kind="start_acknowledged",
        )


def record_workflow_started(
    connection: Connection,
    admission: object,
    receipt: object,
) -> WorkflowStarted:
    """Atomically project positive Temporal start evidence into PostgreSQL."""
    _validate_record_input(admission, receipt)
    candidate_event_id = uuid4()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT progress_event_id, command_id, workflow_id, first_run_id, "
            "start_evidence, state_projection, projected_at, duplicate, outcome "
            "FROM control.record_workflow_started(%s, %s, %s, %s, %s, %s, %s)",
            (
                admission.tenant_id,
                admission.site_id,
                admission.command_id,
                admission.workflow_id,
                receipt.first_run_id,
                receipt.evidence_kind,
                candidate_event_id,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Workflow start recording returned no outcome.")
    if row[8] == "workflow_unavailable":
        raise WorkflowStartRecordRejected()
    if row[8] == "run_conflict":
        raise WorkflowStartRecordConflict()
    if row[8] != "recorded":
        raise RuntimeError("Workflow start recording returned an invalid outcome.")

    started = WorkflowStarted(
        progress_event_id=row[0],
        command_id=row[1],
        workflow_id=row[2],
        first_run_id=row[3],
        start_evidence=row[4],
        state=row[5],
        projected_at=row[6],
        duplicate=row[7],
    )
    _validate_started(started, admission, receipt)
    return started


def _workflow_input(admission: object) -> CrawlSiteWorkflowInput:
    if not isinstance(admission, WorkflowAdmission):
        raise ValueError("Temporal start requires a typed workflow admission.")
    if (
        not isinstance(admission.tenant_id, UUID)
        or not isinstance(admission.source_event_id, UUID)
        or not isinstance(admission.command_id, UUID)
        or not isinstance(admission.site_id, UUID)
        or admission.workflow_type != "CrawlSite"
        or _WORKFLOW_ID.fullmatch(admission.workflow_id) is None
        or admission.workflow_id != f"signal:CrawlSite:{admission.tenant_id}:{admission.command_id}"
        or admission.state != "admitted"
    ):
        raise ValueError("Temporal start requires a valid workflow admission.")
    return CrawlSiteWorkflowInput(
        schema_version=1,
        tenant_id=str(admission.tenant_id),
        site_id=str(admission.site_id),
        command_id=str(admission.command_id),
        source_event_id=str(admission.source_event_id),
        scope_version=1,
        crawl_policy_version=1,
    )


def _validate_record_input(admission: object, receipt: object) -> None:
    _workflow_input(admission)
    if (
        not isinstance(receipt, WorkflowStartReceipt)
        or receipt.workflow_id != admission.workflow_id
        or receipt.evidence_kind not in {"start_acknowledged", "already_started"}
        or not _is_canonical_run_id(receipt.first_run_id)
    ):
        raise ValueError("Workflow start evidence does not match its admission.")


def _validate_started(
    started: WorkflowStarted,
    admission: WorkflowAdmission,
    receipt: WorkflowStartReceipt,
) -> None:
    if (
        not isinstance(started.progress_event_id, UUID)
        or started.command_id != admission.command_id
        or started.workflow_id != admission.workflow_id
        or started.first_run_id != receipt.first_run_id
        or started.start_evidence not in {"start_acknowledged", "already_started"}
        or started.state != "running"
        or not isinstance(started.projected_at, datetime)
        or started.projected_at.tzinfo is None
        or started.projected_at.utcoffset() is None
        or not isinstance(started.duplicate, bool)
    ):
        raise RuntimeError("Workflow start recording returned an invalid projection.")


def _canonical_run_id(value: object) -> str:
    if not _is_canonical_run_id(value):
        raise WorkflowStartOutcomeUnknown()
    return value


def _is_canonical_run_id(value: object) -> bool:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return str(parsed) == value


_TASK_QUEUE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_RUN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_WORKFLOW_ID = re.compile(
    r"signal:CrawlSite:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
