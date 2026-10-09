"""Legacy qualification ports, not composed product capabilities."""

from collections.abc import Callable
from uuid import UUID

from signal_core.outbox_dispatch import OutboxEnvelope, OutboxLeaseLost
from signal_core.outbox_worker import (
    DeliveryCycleReport,
    DeliveryObservation,
    DeliveryWorkerConfig,
    EventPublisher,
    OutboxStore,
    PublishNotAccepted,
    PublishOutcomeUnknown,
    StopSignal,
    _CycleCounts,
    _observation,
)


class DeliveryWorker:
    def __init__(
        self,
        *,
        store: OutboxStore,
        publisher: EventPublisher,
        config: DeliveryWorkerConfig,
        observer: Callable[[DeliveryObservation], None] | None = None,
    ) -> None:
        if not callable(getattr(publisher, "publish", None)):
            raise ValueError("Delivery worker requires an event publisher.")
        self._store = store
        self._publisher = publisher
        self._config = config
        self._observer = observer
        self._tenant_cursor: UUID | None = None

    def run_once(self) -> DeliveryCycleReport:
        counts = _CycleCounts()
        try:
            tenants = self._store.list_tenants(
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
            try:
                envelopes = self._store.claim(
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
                self._publish_one(envelope, counts)
        return counts.report()

    def run_forever(self, stop: StopSignal) -> int:
        """Run until cooperative shutdown and return the number of completed cycles."""
        cycles = 0
        while not stop.is_set():
            report = self.run_once()
            cycles += 1
            delay = (
                self._config.active_delay_seconds
                if report.claimed > 0
                else self._config.idle_delay_seconds
            )
            if stop.wait(delay):
                break
        return cycles

    def _publish_one(self, envelope: OutboxEnvelope, counts: "_CycleCounts") -> None:
        try:
            result = self._publisher.publish(envelope)
            if result is not None:
                raise PublishOutcomeUnknown()
        except PublishNotAccepted:
            self._reschedule(envelope, counts)
            return
        except Exception:
            counts.ambiguous += 1
            self._emit(_observation("publish_outcome_unknown", envelope))
            return

        try:
            self._store.acknowledge(envelope, worker_key=self._config.worker_key)
        except OutboxLeaseLost:
            counts.lease_lost += 1
            self._emit(_observation("ack_lease_lost", envelope))
        except Exception:
            counts.storage_failures += 1
            self._emit(_observation("ack_failed", envelope))
        else:
            counts.delivered += 1
            self._emit(_observation("delivered", envelope))

    def _reschedule(self, envelope: OutboxEnvelope, counts: "_CycleCounts") -> None:
        try:
            self._store.reschedule(
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
