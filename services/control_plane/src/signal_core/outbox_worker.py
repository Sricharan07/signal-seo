"""Bounded outbox delivery loops with explicit publish outcome semantics."""

import asyncio
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from psycopg import Connection

from signal_core.outbox_dispatch import (
    OutboxEnvelope,
    OutboxLeaseLost,
    claim_outbox_batch,
    list_active_dispatch_tenants,
    mark_outbox_delivered,
    reschedule_outbox,
)


class PublishNotAccepted(Exception):
    """The transport positively rejected the envelope without accepting it."""


class PublishOutcomeUnknown(Exception):
    """The caller cannot prove whether the transport accepted the envelope."""


class EventPublisher(Protocol):
    def publish(self, envelope: OutboxEnvelope) -> None:
        """Return only after positive acceptance; raise a classified outcome otherwise."""


class AsyncEventPublisher(Protocol):
    async def publish(self, envelope: OutboxEnvelope) -> None:
        """Return only after positive acceptance; raise a classified outcome otherwise."""


class StopSignal(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class OutboxStore(Protocol):
    def list_tenants(
        self, *, after_tenant_id: UUID | None, batch_size: int
    ) -> tuple[UUID, ...]: ...

    def claim(
        self,
        *,
        tenant_id: UUID,
        worker_key: str,
        batch_size: int,
        lease_seconds: int,
    ) -> tuple[OutboxEnvelope, ...]: ...

    def acknowledge(self, envelope: OutboxEnvelope, *, worker_key: str) -> datetime: ...

    def reschedule(
        self,
        envelope: OutboxEnvelope,
        *,
        worker_key: str,
        delay_seconds: int,
    ) -> datetime: ...


@dataclass(frozen=True)
class DeliveryWorkerConfig:
    worker_key: str
    tenant_page_size: int = 100
    outbox_batch_size: int = 1
    lease_seconds: int = 30
    retry_delay_seconds: int = 30
    idle_delay_seconds: float = 1.0
    active_delay_seconds: float = 0.01

    def __post_init__(self) -> None:
        if not isinstance(self.worker_key, str) or _WORKER.fullmatch(self.worker_key) is None:
            raise ValueError("Worker keys require 1-64 lowercase ASCII token characters.")
        _integer(self.tenant_page_size, 1, 1000, "tenant page size")
        _integer(self.outbox_batch_size, 1, 1, "synchronous outbox batch size")
        _integer(self.lease_seconds, 1, 300, "outbox lease")
        _integer(self.retry_delay_seconds, 1, 3600, "retry delay")
        _delay(self.idle_delay_seconds, 0.05, 60.0, "idle delay")
        _delay(self.active_delay_seconds, 0.01, 5.0, "active delay")


@dataclass(frozen=True)
class DeliveryObservation:
    outcome: str
    tenant_id: UUID | None = None
    outbox_id: UUID | None = None
    event_id: UUID | None = None
    attempt_count: int | None = None


@dataclass(frozen=True)
class DeliveryCycleReport:
    active_tenants: int = 0
    claimed: int = 0
    delivered: int = 0
    rescheduled: int = 0
    ambiguous: int = 0
    lease_lost: int = 0
    storage_failures: int = 0


class PostgresOutboxStore:
    """Use one short-lived scheduler connection for each database operation."""

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Connection]],
    ) -> None:
        self._connection_factory = connection_factory

    def list_tenants(
        self,
        *,
        after_tenant_id: UUID | None,
        batch_size: int,
    ) -> tuple[UUID, ...]:
        with self._connection_factory() as connection:
            return list_active_dispatch_tenants(
                connection,
                after_tenant_id=after_tenant_id,
                batch_size=batch_size,
            )

    def claim(
        self,
        *,
        tenant_id: UUID,
        worker_key: str,
        batch_size: int,
        lease_seconds: int,
    ) -> tuple[OutboxEnvelope, ...]:
        with self._connection_factory() as connection:
            return claim_outbox_batch(
                connection,
                tenant_id=tenant_id,
                worker_key=worker_key,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )

    def acknowledge(self, envelope: OutboxEnvelope, *, worker_key: str) -> datetime:
        with self._connection_factory() as connection:
            return mark_outbox_delivered(
                connection,
                tenant_id=envelope.tenant_id,
                outbox_id=envelope.outbox_id,
                worker_key=worker_key,
                attempt_count=envelope.attempt_count,
            )

    def reschedule(
        self,
        envelope: OutboxEnvelope,
        *,
        worker_key: str,
        delay_seconds: int,
    ) -> datetime:
        with self._connection_factory() as connection:
            return reschedule_outbox(
                connection,
                tenant_id=envelope.tenant_id,
                outbox_id=envelope.outbox_id,
                worker_key=worker_key,
                attempt_count=envelope.attempt_count,
                delay_seconds=delay_seconds,
            )


class AsyncDeliveryWorker:
    """Run the same delivery contract while awaiting a bounded async publisher."""

    def __init__(
        self,
        *,
        store: OutboxStore,
        publisher: AsyncEventPublisher,
        config: DeliveryWorkerConfig,
        observer: Callable[[DeliveryObservation], None] | None = None,
        cycle_observer: Callable[[DeliveryCycleReport], None] | None = None,
    ) -> None:
        if not callable(getattr(publisher, "publish", None)):
            raise ValueError("Delivery worker requires an event publisher.")
        if cycle_observer is not None and not callable(cycle_observer):
            raise ValueError("Delivery cycle observer must be callable.")
        self._store = store
        self._publisher = publisher
        self._config = config
        self._observer = observer
        self._cycle_observer = cycle_observer
        self._tenant_cursor: UUID | None = None

    async def run_once(self, *, stop: asyncio.Event | None = None) -> DeliveryCycleReport:
        if stop is not None and not isinstance(stop, asyncio.Event):
            raise ValueError("Async delivery requires an asyncio stop event.")
        counts = _CycleCounts()
        try:
            tenants = await asyncio.to_thread(
                self._store.list_tenants,
                after_tenant_id=self._tenant_cursor,
                batch_size=self._config.tenant_page_size,
            )
        except Exception:
            counts.storage_failures += 1
            self._emit(DeliveryObservation("tenant_scan_failed"))
            return counts.report()

        if not tenants:
            self._tenant_cursor = None
            return counts.report()
        self._tenant_cursor = tenants[-1]
        counts.active_tenants = len(tenants)

        for tenant_id in tenants:
            if stop is not None and stop.is_set():
                break
            try:
                envelopes = await asyncio.to_thread(
                    self._store.claim,
                    tenant_id=tenant_id,
                    worker_key=self._config.worker_key,
                    batch_size=self._config.outbox_batch_size,
                    lease_seconds=self._config.lease_seconds,
                )
            except Exception:
                counts.storage_failures += 1
                self._emit(DeliveryObservation("claim_failed", tenant_id=tenant_id))
                continue
            for envelope in envelopes:
                counts.claimed += 1
                self._emit(_observation("claimed", envelope))
                await self._publish_one(envelope, counts)
        return counts.report()

    async def run_forever(self, stop: asyncio.Event) -> int:
        """Drain a current attempt and stop before claiming more work."""
        if not isinstance(stop, asyncio.Event):
            raise ValueError("Async delivery requires an asyncio stop event.")
        cycles = 0
        while not stop.is_set():
            report = await self.run_once(stop=stop)
            cycles += 1
            self._emit_cycle(report)
            delay = (
                self._config.active_delay_seconds
                if report.claimed > 0
                else self._config.idle_delay_seconds
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                continue
        return cycles

    async def _publish_one(self, envelope: OutboxEnvelope, counts: "_CycleCounts") -> None:
        try:
            result = await self._publisher.publish(envelope)
            if result is not None:
                raise PublishOutcomeUnknown()
        except PublishNotAccepted:
            await self._reschedule(envelope, counts)
            return
        except Exception:
            counts.ambiguous += 1
            self._emit(_observation("publish_outcome_unknown", envelope))
            return

        try:
            await asyncio.to_thread(
                self._store.acknowledge,
                envelope,
                worker_key=self._config.worker_key,
            )
        except OutboxLeaseLost:
            counts.lease_lost += 1
            self._emit(_observation("ack_lease_lost", envelope))
        except Exception:
            counts.storage_failures += 1
            self._emit(_observation("ack_failed", envelope))
        else:
            counts.delivered += 1
            self._emit(_observation("delivered", envelope))

    async def _reschedule(self, envelope: OutboxEnvelope, counts: "_CycleCounts") -> None:
        try:
            await asyncio.to_thread(
                self._store.reschedule,
                envelope,
                worker_key=self._config.worker_key,
                delay_seconds=self._config.retry_delay_seconds,
            )
        except OutboxLeaseLost:
            counts.lease_lost += 1
            self._emit(_observation("reschedule_lease_lost", envelope))
        except Exception:
            counts.storage_failures += 1
            self._emit(_observation("reschedule_failed", envelope))
        else:
            counts.rescheduled += 1
            self._emit(_observation("rescheduled", envelope))

    def _emit(self, observation: DeliveryObservation) -> None:
        if self._observer is None:
            return
        try:
            self._observer(observation)
        except Exception:
            return

    def _emit_cycle(self, report: DeliveryCycleReport) -> None:
        if self._cycle_observer is None:
            return
        try:
            self._cycle_observer(report)
        except Exception:
            return


@dataclass
class _CycleCounts:
    active_tenants: int = 0
    claimed: int = 0
    delivered: int = 0
    rescheduled: int = 0
    ambiguous: int = 0
    lease_lost: int = 0
    storage_failures: int = 0

    def report(self) -> DeliveryCycleReport:
        return DeliveryCycleReport(
            active_tenants=self.active_tenants,
            claimed=self.claimed,
            delivered=self.delivered,
            rescheduled=self.rescheduled,
            ambiguous=self.ambiguous,
            lease_lost=self.lease_lost,
            storage_failures=self.storage_failures,
        )


def _observation(outcome: str, envelope: OutboxEnvelope) -> DeliveryObservation:
    return DeliveryObservation(
        outcome=outcome,
        tenant_id=envelope.tenant_id,
        outbox_id=envelope.outbox_id,
        event_id=envelope.event_id,
        attempt_count=envelope.attempt_count,
    )


def _integer(value: object, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {name}.")
    return value


def _delay(value: object, minimum: float, maximum: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {name}.")
    converted = float(value)
    if not minimum <= converted <= maximum:
        raise ValueError(f"Invalid {name}.")
    return converted


_WORKER = re.compile(r"[a-z][a-z0-9_.:-]{0,63}")
