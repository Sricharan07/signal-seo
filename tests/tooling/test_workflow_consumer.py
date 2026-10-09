import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from signal_core.outbox_dispatch import OutboxEnvelope
from signal_core.outbox_worker import PublishNotAccepted, PublishOutcomeUnknown
from signal_core.workflow_admission import WorkflowAdmission, WorkflowAdmissionRejected
from signal_core.workflow_consumer import (
    PostgresWorkflowStore,
    WorkflowEventPublisher,
    WorkflowPublishObservation,
)
from signal_core.workflow_start import (
    WorkflowStarted,
    WorkflowStartOutcomeUnknown,
    WorkflowStartReceipt,
)


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
        attempt_count=3,
    )


def admission(item: OutboxEnvelope) -> WorkflowAdmission:
    return WorkflowAdmission(
        tenant_id=item.tenant_id,
        source_event_id=item.event_id,
        progress_event_id=uuid4(),
        command_id=item.command_id,
        site_id=item.site_id,
        workflow_id=f"signal:CrawlSite:{item.tenant_id}:{item.command_id}",
        workflow_type="CrawlSite",
        state="admitted",
        processed_at=datetime.now(UTC),
        duplicate=False,
    )


def receipt(admitted: WorkflowAdmission) -> WorkflowStartReceipt:
    return WorkflowStartReceipt(
        workflow_id=admitted.workflow_id,
        first_run_id=str(uuid4()),
        evidence_kind="start_acknowledged",
    )


def started(admitted: WorkflowAdmission, accepted: WorkflowStartReceipt) -> WorkflowStarted:
    return WorkflowStarted(
        progress_event_id=uuid4(),
        command_id=admitted.command_id,
        workflow_id=admitted.workflow_id,
        first_run_id=accepted.first_run_id,
        start_evidence=accepted.evidence_kind,
        state="running",
        projected_at=datetime.now(UTC),
        duplicate=False,
    )


class FakeWorkflowStore:
    def __init__(self, item, *, admit_failure=None, record_failure=None, record_result=None):
        self.admitted = admission(item)
        self.accepted = receipt(self.admitted)
        self.admit_failure = admit_failure
        self.record_failure = record_failure
        self.record_result = record_result
        self.calls = []

    def admit(self, item):
        self.calls.append(("admit", item))
        if self.admit_failure is not None:
            raise self.admit_failure
        return self.admitted

    def record(self, admitted, accepted):
        self.calls.append(("record", admitted, accepted))
        if self.record_failure is not None:
            raise self.record_failure
        if self.record_result is not None:
            return self.record_result
        return started(admitted, accepted)


class FakeStarter:
    def __init__(self, accepted, failure=None):
        self.accepted = accepted
        self.failure = failure
        self.calls = []

    async def start(self, admitted):
        self.calls.append(admitted)
        if self.failure is not None:
            raise self.failure
        return self.accepted


def test_publisher_admits_starts_and_records_before_confirming_acceptance():
    item = envelope()
    store = FakeWorkflowStore(item)
    starter = FakeStarter(store.accepted)
    observations = []
    publisher = WorkflowEventPublisher(
        store=store,
        starter=starter,
        observer=observations.append,
    )

    assert asyncio.run(publisher.publish(item)) is None

    assert [call[0] for call in store.calls] == ["admit", "record"]
    assert starter.calls == [store.admitted]
    assert [observation.outcome for observation in observations] == [
        "workflow_admitted",
        "temporal_start_confirmed",
        "workflow_start_recorded",
    ]
    assert all(
        observation
        == WorkflowPublishObservation(
            outcome=observation.outcome,
            tenant_id=item.tenant_id,
            outbox_id=item.outbox_id,
            event_id=item.event_id,
            command_id=item.command_id,
            attempt_count=item.attempt_count,
        )
        for observation in observations
    )


def test_definite_admission_rejection_is_the_only_early_reschedule_outcome():
    item = envelope()
    store = FakeWorkflowStore(item, admit_failure=WorkflowAdmissionRejected("private detail"))
    starter = FakeStarter(store.accepted)
    observations = []
    publisher = WorkflowEventPublisher(
        store=store,
        starter=starter,
        observer=observations.append,
    )

    with pytest.raises(PublishNotAccepted) as captured:
        asyncio.run(publisher.publish(item))

    assert str(captured.value) == ""
    assert starter.calls == []
    assert [event.outcome for event in observations] == ["workflow_admission_rejected"]


@pytest.mark.parametrize(
    ("stage", "failure", "expected_observation"),
    [
        ("admit", RuntimeError("dsn=password"), "workflow_admission_outcome_unknown"),
        (
            "start",
            WorkflowStartOutcomeUnknown("provider secret"),
            "temporal_start_outcome_unknown",
        ),
        ("start", RuntimeError("provider secret"), "temporal_start_outcome_unknown"),
        ("record", RuntimeError("dsn=password"), "workflow_record_outcome_unknown"),
    ],
)
def test_unprovable_stage_outcomes_are_sanitized_and_never_confirm_delivery(
    stage, failure, expected_observation
):
    item = envelope()
    store = FakeWorkflowStore(
        item,
        admit_failure=failure if stage == "admit" else None,
        record_failure=failure if stage == "record" else None,
    )
    starter = FakeStarter(store.accepted, failure if stage == "start" else None)
    observations = []
    publisher = WorkflowEventPublisher(
        store=store,
        starter=starter,
        observer=observations.append,
    )

    with pytest.raises(PublishOutcomeUnknown) as captured:
        asyncio.run(publisher.publish(item))

    assert str(captured.value) == ""
    assert observations[-1].outcome == expected_observation
    assert "password" not in repr(observations)
    assert "secret" not in repr(observations)


def test_publisher_does_not_swallow_cooperative_cancellation():
    item = envelope()
    store = FakeWorkflowStore(item)
    publisher = WorkflowEventPublisher(
        store=store,
        starter=FakeStarter(store.accepted, asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(publisher.publish(item))

    assert [call[0] for call in store.calls] == ["admit"]


@pytest.mark.parametrize("stage", ["admit", "start", "record"])
def test_invalid_adapter_results_cannot_confirm_delivery(stage):
    item = envelope()
    store = FakeWorkflowStore(item, record_result="invalid" if stage == "record" else None)
    if stage == "admit":
        store.admitted = "invalid"
    starter = FakeStarter("invalid" if stage == "start" else store.accepted)
    publisher = WorkflowEventPublisher(store=store, starter=starter)

    with pytest.raises(PublishOutcomeUnknown):
        asyncio.run(publisher.publish(item))


def test_observer_failure_cannot_change_publish_result():
    item = envelope()
    store = FakeWorkflowStore(item)

    def fail(observation):
        raise RuntimeError("telemetry unavailable")

    publisher = WorkflowEventPublisher(
        store=store,
        starter=FakeStarter(store.accepted),
        observer=fail,
    )

    assert asyncio.run(publisher.publish(item)) is None


def test_publisher_requires_store_and_starter_ports():
    with pytest.raises(ValueError, match="store"):
        WorkflowEventPublisher(store=object(), starter=object())
    item = envelope()
    store = FakeWorkflowStore(item)
    with pytest.raises(ValueError, match="starter"):
        WorkflowEventPublisher(store=store, starter=object())
    with pytest.raises(ValueError, match="observer"):
        WorkflowEventPublisher(
            store=store,
            starter=FakeStarter(store.accepted),
            observer=object(),
        )
    with pytest.raises(ValueError, match="factory"):
        PostgresWorkflowStore(object())


def test_postgres_store_uses_a_fresh_context_for_each_transition(monkeypatch):
    item = envelope()
    admitted = admission(item)
    accepted = receipt(admitted)
    calls = []

    @contextmanager
    def connection_factory():
        connection = object()
        calls.append(("open", connection))
        yield connection
        calls.append(("close", connection))

    monkeypatch.setattr(
        "signal_core.workflow_consumer.admit_command_event",
        lambda connection, envelope: admitted,
    )
    monkeypatch.setattr(
        "signal_core.workflow_consumer.record_workflow_started",
        lambda connection, admission, receipt: started(admission, receipt),
    )
    store = PostgresWorkflowStore(connection_factory)

    assert store.admit(item) == admitted
    assert store.record(admitted, accepted).first_run_id == accepted.first_run_id
    assert [call[0] for call in calls] == ["open", "close", "open", "close"]


@pytest.mark.parametrize("bad_attempt", [0, -1, True, 2**31])
def test_observation_builder_rejects_invalid_envelope_attempt_fence(bad_attempt):
    item = envelope()
    object.__setattr__(item, "attempt_count", bad_attempt)
    store = FakeWorkflowStore(item)
    publisher = WorkflowEventPublisher(store=store, starter=FakeStarter(store.accepted))

    with pytest.raises(PublishOutcomeUnknown):
        asyncio.run(publisher.publish(item))


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "provider-secret"},
        {"tenant_id": "not-a-uuid"},
        {"attempt_count": True},
        {"attempt_count": 0},
    ],
)
def test_workflow_observation_schema_is_closed(change):
    item = envelope()
    values = {
        "outcome": "workflow_admitted",
        "tenant_id": item.tenant_id,
        "outbox_id": item.outbox_id,
        "event_id": item.event_id,
        "command_id": item.command_id,
        "attempt_count": item.attempt_count,
        **change,
    }
    with pytest.raises(ValueError, match="observation"):
        WorkflowPublishObservation(**values)
