import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from signal_core.outbox_dispatch import OutboxEnvelope, OutboxLeaseLost
from signal_core.outbox_worker import (
    AsyncDeliveryWorker,
    DeliveryCycleReport,
    DeliveryObservation,
    DeliveryWorkerConfig,
    PostgresOutboxStore,
    PublishNotAccepted,
    PublishOutcomeUnknown,
)

from tests.tooling.outbox_worker_support import DeliveryWorker


def envelope(*, tenant_id: UUID | None = None, attempt_count: int = 1) -> OutboxEnvelope:
    now = datetime.now(UTC)
    return OutboxEnvelope(
        tenant_id=tenant_id or uuid4(),
        outbox_id=uuid4(),
        event_id=uuid4(),
        site_id=uuid4(),
        command_id=uuid4(),
        aggregate_kind="command",
        event_type="command.accepted",
        schema_version=1,
        payload=b'{"schema_version":1}',
        available_at=now,
        lease_until=now + timedelta(seconds=30),
        attempt_count=attempt_count,
    )


class FakeStore:
    def __init__(self, tenants=(), batches=None):
        self.tenants = tuple(tenants)
        self.batches = batches or {}
        self.calls = []
        self.claim_errors = set()
        self.ack_error = None
        self.reschedule_error = None
        self.scan_error = None

    def list_tenants(self, *, after_tenant_id, batch_size):
        self.calls.append(("list", after_tenant_id, batch_size))
        if self.scan_error is not None:
            raise self.scan_error
        return tuple(
            tenant for tenant in self.tenants if after_tenant_id is None or tenant > after_tenant_id
        )[:batch_size]

    def claim(self, *, tenant_id, worker_key, batch_size, lease_seconds):
        self.calls.append(("claim", tenant_id, worker_key, batch_size, lease_seconds))
        if tenant_id in self.claim_errors:
            raise RuntimeError("private database detail")
        return tuple(self.batches.get(tenant_id, ()))[:batch_size]

    def acknowledge(self, item, *, worker_key):
        self.calls.append(("ack", item.outbox_id, worker_key, item.attempt_count))
        if self.ack_error is not None:
            raise self.ack_error
        return datetime.now(UTC)

    def reschedule(self, item, *, worker_key, delay_seconds):
        self.calls.append(
            ("reschedule", item.outbox_id, worker_key, item.attempt_count, delay_seconds)
        )
        if self.reschedule_error is not None:
            raise self.reschedule_error
        return datetime.now(UTC) + timedelta(seconds=delay_seconds)


class FakePublisher:
    def __init__(self, failure=None, result=None):
        self.failure = failure
        self.result = result
        self.published = []

    def publish(self, item):
        self.published.append(item)
        if self.failure is not None:
            raise self.failure
        return self.result


class AsyncFakePublisher(FakePublisher):
    async def publish(self, item):
        return super().publish(item)


def worker(store, publisher, observations=None, **overrides):
    return DeliveryWorker(
        store=store,
        publisher=publisher,
        config=DeliveryWorkerConfig(worker_key="publisher.one", **overrides),
        observer=None if observations is None else observations.append,
    )


def async_worker(store, publisher, observations=None, cycle_observer=None, **overrides):
    return AsyncDeliveryWorker(
        store=store,
        publisher=publisher,
        config=DeliveryWorkerConfig(worker_key="publisher.async", **overrides),
        observer=None if observations is None else observations.append,
        cycle_observer=cycle_observer,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"worker_key": "UPPER"},
        {"tenant_page_size": 0},
        {"tenant_page_size": 1001},
        {"outbox_batch_size": True},
        {"outbox_batch_size": 2},
        {"outbox_batch_size": 101},
        {"lease_seconds": 0},
        {"retry_delay_seconds": 3601},
        {"idle_delay_seconds": 0},
        {"active_delay_seconds": 5.1},
    ],
)
def test_configuration_is_strictly_bounded(overrides):
    values = {"worker_key": "publisher.one", **overrides}
    with pytest.raises(ValueError):
        DeliveryWorkerConfig(**values)


def test_happy_cycle_publishes_after_claim_then_acknowledges():
    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})
    publisher = FakePublisher()
    observations = []

    report = worker(store, publisher, observations).run_once()

    assert report == DeliveryCycleReport(active_tenants=1, claimed=1, delivered=1)
    assert publisher.published == [item]
    assert [call[0] for call in store.calls] == ["list", "claim", "ack"]
    assert [event.outcome for event in observations] == ["claimed", "delivered"]


def test_positive_transport_rejection_reschedules_exact_attempt():
    item = envelope(attempt_count=7)
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})
    publisher = FakePublisher(PublishNotAccepted())

    report = worker(store, publisher, retry_delay_seconds=45).run_once()

    assert report == DeliveryCycleReport(active_tenants=1, claimed=1, rescheduled=1)
    assert store.calls[-1] == ("reschedule", item.outbox_id, "publisher.one", 7, 45)


@pytest.mark.parametrize("failure", [PublishOutcomeUnknown(), TimeoutError(), RuntimeError()])
def test_ambiguous_publish_leaves_lease_for_safe_redelivery(failure):
    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})
    observations = []

    report = worker(store, FakePublisher(failure), observations).run_once()

    assert report == DeliveryCycleReport(active_tenants=1, claimed=1, ambiguous=1)
    assert [call[0] for call in store.calls] == ["list", "claim"]
    assert observations[-1] == DeliveryObservation(
        "publish_outcome_unknown",
        tenant_id=item.tenant_id,
        outbox_id=item.outbox_id,
        event_id=item.event_id,
        attempt_count=item.attempt_count,
    )


def test_non_none_publish_result_is_treated_as_ambiguous_contract_violation():
    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})
    report = worker(store, FakePublisher(result="unexpected detail")).run_once()
    assert report.ambiguous == 1
    assert not any(call[0] == "ack" for call in store.calls)


@pytest.mark.parametrize(
    ("operation", "error", "expected"),
    [
        ("ack", OutboxLeaseLost(), DeliveryCycleReport(active_tenants=1, claimed=1, lease_lost=1)),
        (
            "ack",
            RuntimeError(),
            DeliveryCycleReport(active_tenants=1, claimed=1, storage_failures=1),
        ),
        (
            "reschedule",
            OutboxLeaseLost(),
            DeliveryCycleReport(active_tenants=1, claimed=1, lease_lost=1),
        ),
        (
            "reschedule",
            RuntimeError(),
            DeliveryCycleReport(active_tenants=1, claimed=1, storage_failures=1),
        ),
    ],
)
def test_bookkeeping_failures_never_claim_delivery(operation, error, expected):
    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})
    publisher = FakePublisher(PublishNotAccepted() if operation == "reschedule" else None)
    setattr(store, f"{operation}_error", error)
    assert worker(store, publisher).run_once() == expected


def test_claim_failure_isolated_to_one_tenant():
    tenants = tuple(sorted((uuid4(), uuid4())))
    item = envelope(tenant_id=tenants[1])
    store = FakeStore(tenants, {tenants[1]: (item,)})
    store.claim_errors.add(tenants[0])

    report = worker(store, FakePublisher()).run_once()

    assert report == DeliveryCycleReport(
        active_tenants=2,
        claimed=1,
        delivered=1,
        storage_failures=1,
    )


def test_tenant_scan_failure_is_bounded_and_sanitized():
    store = FakeStore()
    store.scan_error = RuntimeError("dsn=password")
    observations = []

    report = worker(store, FakePublisher(), observations).run_once()

    assert report == DeliveryCycleReport(storage_failures=1)
    assert observations == [DeliveryObservation("tenant_scan_failed")]
    assert "password" not in repr(observations)


def test_cursor_pages_tenants_and_resets_only_after_end():
    tenants = tuple(sorted((uuid4(), uuid4(), uuid4())))
    store = FakeStore(tenants)
    delivery = worker(store, FakePublisher(), tenant_page_size=2)

    assert delivery.run_once().active_tenants == 2
    assert delivery.run_once().active_tenants == 1
    assert delivery.run_once().active_tenants == 0
    assert delivery.run_once().active_tenants == 2
    assert [call[1] for call in store.calls if call[0] == "list"] == [
        None,
        tenants[1],
        tenants[2],
        None,
    ]


def test_observer_failure_cannot_interrupt_delivery():
    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})

    def broken_observer(observation):
        raise RuntimeError("telemetry unavailable")

    delivery = DeliveryWorker(
        store=store,
        publisher=FakePublisher(),
        config=DeliveryWorkerConfig(worker_key="publisher.one"),
        observer=broken_observer,
    )
    assert delivery.run_once().delivered == 1


def test_worker_requires_a_publisher():
    with pytest.raises(ValueError, match="publisher"):
        DeliveryWorker(
            store=FakeStore(),
            publisher=object(),
            config=DeliveryWorkerConfig(worker_key="publisher.one"),
        )


def test_run_forever_uses_bounded_delays_and_cooperative_shutdown():
    class StopAfterTwoWaits:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return False

        def wait(self, timeout):
            self.waits.append(timeout)
            return len(self.waits) == 2

    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})
    stop = StopAfterTwoWaits()
    cycles = worker(
        store,
        FakePublisher(),
        tenant_page_size=1,
        active_delay_seconds=0.25,
        idle_delay_seconds=2.0,
    ).run_forever(stop)

    assert cycles == 2
    assert stop.waits == [0.25, 2.0]


def test_postgres_store_opens_a_fresh_context_for_each_operation(monkeypatch):
    item = envelope()
    calls = []

    @contextmanager
    def connection_factory():
        connection = object()
        calls.append(("open", connection))
        yield connection
        calls.append(("close", connection))

    monkeypatch.setattr(
        "signal_core.outbox_worker.list_active_dispatch_tenants",
        lambda connection, **kwargs: (item.tenant_id,),
    )
    monkeypatch.setattr(
        "signal_core.outbox_worker.claim_outbox_batch",
        lambda connection, **kwargs: (item,),
    )
    monkeypatch.setattr(
        "signal_core.outbox_worker.mark_outbox_delivered",
        lambda connection, **kwargs: datetime.now(UTC),
    )
    monkeypatch.setattr(
        "signal_core.outbox_worker.reschedule_outbox",
        lambda connection, **kwargs: datetime.now(UTC),
    )
    store = PostgresOutboxStore(connection_factory)

    assert store.list_tenants(after_tenant_id=None, batch_size=1) == (item.tenant_id,)
    assert store.claim(
        tenant_id=item.tenant_id,
        worker_key="publisher.one",
        batch_size=1,
        lease_seconds=30,
    ) == (item,)
    store.acknowledge(item, worker_key="publisher.one")
    store.reschedule(item, worker_key="publisher.one", delay_seconds=30)
    assert [call[0] for call in calls] == ["open", "close"] * 4


def test_async_worker_awaits_publication_then_acknowledges():
    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})
    observations = []

    report = asyncio.run(async_worker(store, AsyncFakePublisher(), observations).run_once())

    assert report == DeliveryCycleReport(active_tenants=1, claimed=1, delivered=1)
    assert [call[0] for call in store.calls] == ["list", "claim", "ack"]
    assert [event.outcome for event in observations] == ["claimed", "delivered"]


def test_async_worker_preserves_rejection_and_ambiguous_outcomes():
    rejected = envelope()
    rejected_store = FakeStore((rejected.tenant_id,), {rejected.tenant_id: (rejected,)})
    rejected_report = asyncio.run(
        async_worker(rejected_store, AsyncFakePublisher(PublishNotAccepted())).run_once()
    )
    assert rejected_report == DeliveryCycleReport(
        active_tenants=1,
        claimed=1,
        rescheduled=1,
    )

    unknown = envelope()
    unknown_store = FakeStore((unknown.tenant_id,), {unknown.tenant_id: (unknown,)})
    unknown_report = asyncio.run(
        async_worker(unknown_store, AsyncFakePublisher(PublishOutcomeUnknown())).run_once()
    )
    assert unknown_report == DeliveryCycleReport(
        active_tenants=1,
        claimed=1,
        ambiguous=1,
    )
    assert [call[0] for call in unknown_store.calls] == ["list", "claim"]


def test_async_worker_propagates_cancellation_without_acknowledging():
    class CancelledPublisher:
        async def publish(self, item):
            raise asyncio.CancelledError()

    item = envelope()
    store = FakeStore((item.tenant_id,), {item.tenant_id: (item,)})

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(async_worker(store, CancelledPublisher()).run_once())

    assert [call[0] for call in store.calls] == ["list", "claim"]


def test_async_worker_drains_claimed_envelope_then_stops_before_next_tenant():
    tenants = tuple(sorted((uuid4(), uuid4())))
    first = envelope(tenant_id=tenants[0])
    second = envelope(tenant_id=tenants[1])
    store = FakeStore(tenants, {tenants[0]: (first,), tenants[1]: (second,)})

    class StoppingPublisher:
        def __init__(self, stop):
            self.stop = stop

        async def publish(self, item):
            self.stop.set()

    async def exercise():
        stop = asyncio.Event()
        delivery = async_worker(store, StoppingPublisher(stop))
        return await delivery.run_forever(stop)

    assert asyncio.run(exercise()) == 1
    assert [call[0] for call in store.calls] == ["list", "claim", "ack"]


def test_async_worker_reports_each_completed_cycle_without_trusting_observer():
    reports = []

    async def exercise(observer):
        stop = asyncio.Event()

        def record(report):
            observer(report)
            stop.set()

        delivery = async_worker(
            FakeStore(),
            AsyncFakePublisher(),
            cycle_observer=record,
        )
        return await delivery.run_forever(stop)

    assert asyncio.run(exercise(reports.append)) == 1
    assert reports == [DeliveryCycleReport()]

    def broken_observer(report):
        raise RuntimeError("private telemetry failure")

    async def stop_after_cycle():
        stop = asyncio.Event()

        def broken_then_stop(report):
            stop.set()
            broken_observer(report)

        return await async_worker(
            FakeStore(),
            AsyncFakePublisher(),
            cycle_observer=broken_then_stop,
        ).run_forever(stop)

    assert asyncio.run(stop_after_cycle()) == 1


def test_async_worker_rejects_noncallable_cycle_observer():
    with pytest.raises(ValueError, match="cycle observer"):
        async_worker(FakeStore(), AsyncFakePublisher(), cycle_observer=object())


def test_async_worker_requires_typed_stop_event():
    with pytest.raises(ValueError, match="stop event"):
        asyncio.run(async_worker(FakeStore(), AsyncFakePublisher()).run_once(stop=object()))
