"""Deduplicate accepted-command delivery before any workflow client call."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.database import _clean_transaction
from signal_core.outbox_dispatch import OutboxEnvelope


class WorkflowAdmissionRejected(Exception):
    """The event or its current execution scope is unavailable."""


@dataclass(frozen=True)
class WorkflowAdmission:
    tenant_id: UUID
    source_event_id: UUID
    progress_event_id: UUID
    command_id: UUID
    site_id: UUID
    workflow_id: str
    workflow_type: str
    state: str
    processed_at: datetime
    duplicate: bool


def admit_command_event(
    connection: Connection,
    envelope: object,
    *,
    consumer_key: object = "workflow.command-start.v1",
) -> WorkflowAdmission:
    """Commit one durable workflow admission or return its original receipt."""
    if not isinstance(envelope, OutboxEnvelope):
        raise ValueError("Workflow admission requires a typed outbox envelope.")
    if consumer_key != "workflow.command-start.v1":
        raise ValueError("Unsupported workflow consumer identity.")
    payload = _validated_payload(envelope)

    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT source_event_id, progress_event_id, command_id, site_id, "
            "workflow_id, workflow_type, state_projection, processed_at, duplicate, outcome "
            "FROM control.admit_command_event(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                envelope.tenant_id,
                envelope.outbox_id,
                envelope.event_id,
                envelope.site_id,
                envelope.command_id,
                envelope.aggregate_kind,
                envelope.event_type,
                envelope.schema_version,
                Jsonb(payload),
                consumer_key,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Workflow admission returned no outcome.")
    if row[9] in {"event_unavailable", "scope_unavailable"}:
        raise WorkflowAdmissionRejected()
    if row[9] != "admitted":
        raise RuntimeError("Workflow admission returned an invalid outcome.")

    admission = WorkflowAdmission(
        tenant_id=envelope.tenant_id,
        source_event_id=row[0],
        progress_event_id=row[1],
        command_id=row[2],
        site_id=row[3],
        workflow_id=row[4],
        workflow_type=row[5],
        state=row[6],
        processed_at=row[7],
        duplicate=row[8],
    )
    _validate_admission(admission, envelope)
    return admission


def _validated_payload(envelope: OutboxEnvelope) -> dict[str, int]:
    if (
        not isinstance(envelope.tenant_id, UUID)
        or not isinstance(envelope.outbox_id, UUID)
        or not isinstance(envelope.event_id, UUID)
        or not isinstance(envelope.site_id, UUID)
        or not isinstance(envelope.command_id, UUID)
        or envelope.aggregate_kind != "command"
        or envelope.event_type != "command.accepted"
        or envelope.schema_version != 1
        or envelope.payload != b'{"schema_version":1}'
    ):
        raise ValueError("Unsupported command event envelope.")
    try:
        payload = json.loads(envelope.payload)
    except (TypeError, ValueError):
        raise ValueError("Unsupported command event envelope.") from None
    if payload != {"schema_version": 1}:
        raise ValueError("Unsupported command event envelope.")
    return payload


def _validate_admission(admission: WorkflowAdmission, envelope: OutboxEnvelope) -> None:
    if (
        admission.source_event_id != envelope.event_id
        or admission.tenant_id != envelope.tenant_id
        or not isinstance(admission.progress_event_id, UUID)
        or admission.command_id != envelope.command_id
        or admission.site_id != envelope.site_id
        or not isinstance(admission.workflow_id, str)
        or _WORKFLOW_ID.fullmatch(admission.workflow_id) is None
        or admission.workflow_id != f"signal:CrawlSite:{envelope.tenant_id}:{envelope.command_id}"
        or admission.workflow_type != "CrawlSite"
        or admission.state != "admitted"
        or not isinstance(admission.processed_at, datetime)
        or admission.processed_at.tzinfo is None
        or admission.processed_at.utcoffset() is None
        or not isinstance(admission.duplicate, bool)
    ):
        raise RuntimeError("Workflow admission returned an invalid projection.")


_WORKFLOW_ID = re.compile(
    r"signal:CrawlSite:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
