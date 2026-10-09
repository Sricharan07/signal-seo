"""Lease-safe delivery of immutable command events; no broker or workflow client."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection

from signal_core.database import _clean_transaction


class OutboxLeaseLost(Exception):
    """The delivery attempt no longer owns the exact live lease fence."""


@dataclass(frozen=True)
class OutboxEnvelope:
    tenant_id: UUID
    outbox_id: UUID
    event_id: UUID
    site_id: UUID
    command_id: UUID
    aggregate_kind: str
    event_type: str
    schema_version: int
    payload: bytes
    available_at: datetime
    lease_until: datetime
    attempt_count: int


def list_active_dispatch_tenants(
    connection: Connection,
    *,
    after_tenant_id: object = None,
    batch_size: object = 100,
) -> tuple[UUID, ...]:
    """Page through the minimal active tenant directory without touching tenant data."""
    if after_tenant_id is not None and not isinstance(after_tenant_id, UUID):
        raise ValueError("Dispatch pagination requires a UUID cursor.")
    _bounded_integer(batch_size, minimum=1, maximum=1000, name="tenant batch size")

    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT tenant_id FROM control.tenant_directory "
            "WHERE lifecycle = 'active' AND (%s::uuid IS NULL OR tenant_id > %s::uuid) "
            "ORDER BY tenant_id LIMIT %s",
            (after_tenant_id, after_tenant_id, batch_size),
        ).fetchall()
    tenant_ids = tuple(row[0] for row in rows)
    if (
        any(not isinstance(tenant_id, UUID) for tenant_id in tenant_ids)
        or tuple(sorted(tenant_ids)) != tenant_ids
    ):
        raise RuntimeError("Active dispatch tenant query returned an invalid projection.")
    return tenant_ids


def claim_outbox_batch(
    connection: Connection,
    *,
    tenant_id: object,
    worker_key: object,
    batch_size: object = 25,
    lease_seconds: object = 30,
) -> tuple[OutboxEnvelope, ...]:
    """Claim one bounded tenant batch; publish only after this transaction commits."""
    _validate_tenant_and_worker(tenant_id, worker_key)
    _bounded_integer(batch_size, minimum=1, maximum=100, name="outbox batch size")
    _bounded_integer(lease_seconds, minimum=1, maximum=300, name="outbox lease")

    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT outbox_id, event_id, site_id, command_id, aggregate_kind, "
            "event_type, schema_version, payload, available_at, lease_until, attempt_count "
            "FROM control.claim_outbox_batch(%s, %s, %s, %s)",
            (tenant_id, worker_key, batch_size, lease_seconds),
        ).fetchall()
    envelopes = tuple(_outbox_envelope(tenant_id, row) for row in rows)
    if (
        len(envelopes) > batch_size
        or len({item.outbox_id for item in envelopes}) != len(envelopes)
        or tuple(sorted(envelopes, key=lambda item: (item.available_at, item.outbox_id)))
        != envelopes
    ):
        raise RuntimeError("Outbox claim returned an invalid batch.")
    return envelopes


def mark_outbox_delivered(
    connection: Connection,
    *,
    tenant_id: object,
    outbox_id: object,
    worker_key: object,
    attempt_count: object,
) -> datetime:
    """Acknowledge publication only while the exact claim fence remains current."""
    _validate_delivery_identity(tenant_id, outbox_id, worker_key, attempt_count)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT delivery_time, outcome FROM control.mark_outbox_delivered(%s, %s, %s, %s)",
            (tenant_id, outbox_id, worker_key, attempt_count),
        ).fetchone()
    if row is None:
        raise RuntimeError("Outbox delivery acknowledgement returned no outcome.")
    if row[1] == "lease_lost":
        raise OutboxLeaseLost()
    if row[1] != "delivered" or not _aware(row[0]):
        raise RuntimeError("Outbox delivery acknowledgement returned an invalid outcome.")
    return row[0]


def reschedule_outbox(
    connection: Connection,
    *,
    tenant_id: object,
    outbox_id: object,
    worker_key: object,
    attempt_count: object,
    delay_seconds: object,
) -> datetime:
    """Release a failed live claim with a bounded database-clock retry delay."""
    _validate_delivery_identity(tenant_id, outbox_id, worker_key, attempt_count)
    _bounded_integer(delay_seconds, minimum=1, maximum=3600, name="retry delay")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT next_available_at, outcome FROM control.reschedule_outbox(%s, %s, %s, %s, %s)",
            (tenant_id, outbox_id, worker_key, attempt_count, delay_seconds),
        ).fetchone()
    if row is None:
        raise RuntimeError("Outbox reschedule returned no outcome.")
    if row[1] == "lease_lost":
        raise OutboxLeaseLost()
    if row[1] != "rescheduled" or not _aware(row[0]):
        raise RuntimeError("Outbox reschedule returned an invalid outcome.")
    return row[0]


def _outbox_envelope(tenant_id: UUID, row: object) -> OutboxEnvelope:
    try:
        values = tuple(row)
        payload = json.dumps(values[7], sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (IndexError, TypeError, ValueError):
        raise RuntimeError("Outbox claim returned an invalid projection.") from None
    if len(values) != 11:
        raise RuntimeError("Outbox claim returned an invalid projection.")
    envelope = OutboxEnvelope(
        tenant_id=tenant_id,
        outbox_id=values[0],
        event_id=values[1],
        site_id=values[2],
        command_id=values[3],
        aggregate_kind=values[4],
        event_type=values[5],
        schema_version=values[6],
        payload=payload,
        available_at=values[8],
        lease_until=values[9],
        attempt_count=values[10],
    )
    if (
        any(
            not isinstance(value, UUID)
            for value in (
                envelope.tenant_id,
                envelope.outbox_id,
                envelope.event_id,
                envelope.site_id,
                envelope.command_id,
            )
        )
        or envelope.aggregate_kind != "command"
        or envelope.event_type != "command.accepted"
        or envelope.schema_version != 1
        or envelope.payload != b'{"schema_version":1}'
        or not _aware(envelope.available_at)
        or not _aware(envelope.lease_until)
        or envelope.lease_until <= envelope.available_at
        or isinstance(envelope.attempt_count, bool)
        or not isinstance(envelope.attempt_count, int)
        or envelope.attempt_count < 1
    ):
        raise RuntimeError("Outbox claim returned an invalid projection.")
    return envelope


def _validate_delivery_identity(
    tenant_id: object,
    outbox_id: object,
    worker_key: object,
    attempt_count: object,
) -> None:
    _validate_tenant_and_worker(tenant_id, worker_key)
    if not isinstance(outbox_id, UUID):
        raise ValueError("Outbox delivery requires a UUID identity.")
    _bounded_integer(attempt_count, minimum=1, maximum=2147483647, name="attempt fence")


def _validate_tenant_and_worker(tenant_id: object, worker_key: object) -> None:
    if not isinstance(tenant_id, UUID):
        raise ValueError("Outbox dispatch requires a UUID tenant identity.")
    if not isinstance(worker_key, str) or _WORKER.fullmatch(worker_key) is None:
        raise ValueError("Worker keys require 1-64 lowercase ASCII token characters.")


def _bounded_integer(value: object, *, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {name}.")
    return value


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


_WORKER = re.compile(r"[a-z][a-z0-9_.:-]{0,63}")
