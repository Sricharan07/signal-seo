import asyncio
import os
from contextlib import contextmanager
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.ai_visibility_schedule import (
    VisibilityActivities,
    VisibilityRun,
    VisibilityScheduleUnavailable,
    VisibilitySite,
    read_visibility_schedule,
    set_visibility_schedule,
)
from signal_core.recovery_authority import RecoveryGeneration

from tests.control_plane.test_full_site_crawl import _command, _executor, _verify_origin
from tests.control_plane.test_technical_recipes import TimedFetcher


@pytest.fixture
def schedule_context(
    admin, identity, api, scheduler, workflow, crawl_ingest, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run = _command(api, scheduler, workflow, scope, "visibility-schedule-crawl")
    manifest = _executor(tmp_path, TimedFetcher(origin)).run(command, first_run_id=first_run)
    question_set, question = uuid4(), uuid4()
    payload = [
        {
            "id": str(question),
            "question": "What does this site offer?",
            "source_kind": "owner",
            "source_evidence_id": None,
        }
    ]
    assert (
        crawl_ingest.execute(
            "SELECT control.record_ai_visibility_question_set(%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                question_set,
                UUID(manifest.manifest_id),
                Jsonb(payload),
            ),
        ).fetchone()[0]
        == "recorded"
    )
    return scope, identity_context, question_set, question


def settings(api, scope, context, **extra):
    return set_visibility_schedule(
        api,
        session_token=context["session_token"],
        site_id=scope.site_id,
        generation=context["generation"],
        request_id=uuid4(),
        enabled=True,
        **extra,
    )


def projection(api, scope, context):
    return read_visibility_schedule(
        api,
        session_token=context["session_token"],
        site_id=scope.site_id,
        generation=context["generation"],
    )


def open_run(workflow, scope, context, run=None):
    run = run or uuid4()
    return run, workflow.execute(
        "SELECT control.open_ai_visibility_run(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, run, context["generation"]),
    ).fetchone()[0]


def reserve(
    workflow, scope, context, run, question, *, provider="openai", configured=True, ceiling=25_000
):
    return workflow.execute(
        "SELECT * FROM control.reserve_ai_visibility_call(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            run,
            question,
            provider,
            context["generation"],
            configured,
            ceiling,
        ),
    ).fetchone()


def test_owner_defaults_idempotency_roles_tenants_and_missing_config(
    api, identity, admin, scopes, identity_context
):
    scope = scopes[0]
    with pytest.raises(VisibilityScheduleUnavailable):
        projection(api, scope, identity_context)
    _verify_origin(admin, identity, identity_context, scope)
    value = projection(api, scope, identity_context)
    assert (
        value["cadence_days"],
        value["monthly_cap_micros"],
        value["held_micros"],
        value["enabled"],
        value["spent_micros"],
    ) == (7, 1_000_000, 0, False, None)
    request = uuid4()
    kwargs = dict(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        generation=identity_context["generation"],
        request_id=request,
    )
    assert set_visibility_schedule(api, **kwargs) == "updated"
    assert set_visibility_schedule(api, **kwargs) == "replayed"
    with pytest.raises(VisibilityScheduleUnavailable, match="conflict"):
        set_visibility_schedule(api, **kwargs, cadence_days=1)
    for wrong in scopes[1:]:
        with pytest.raises(VisibilityScheduleUnavailable):
            projection(api, wrong, identity_context)
        with pytest.raises(VisibilityScheduleUnavailable):
            settings(api, wrong, identity_context)
    with pytest.raises(VisibilityScheduleUnavailable):
        read_visibility_schedule(
            api,
            session_token=identity_context["session_token"],
            site_id=scope.site_id,
            generation="test-other-generation",
        )
    admin.execute(
        "UPDATE app.memberships SET role_key='analyst', authorization_epoch=authorization_epoch+1 "
        "WHERE id=%s",
        (identity_context["membership_id"],),
    )
    with pytest.raises(VisibilityScheduleUnavailable, match="owner_access_denied"):
        projection(api, scope, identity_context)
    with pytest.raises(VisibilityScheduleUnavailable, match="owner_access_denied"):
        settings(api, scope, identity_context)


@pytest.mark.parametrize("cap", [0, 25_000])
def test_cap_serialized_unknown_hold_no_replay_and_history(
    api, workflow, admin, schedule_context, cap
):
    scope, context, question_set, question = schedule_context
    settings(api, scope, context, monthly_cap_micros=cap)
    run, packet = open_run(workflow, scope, context)
    assert packet["reason"] is None and packet["questions"][0]["id"] == str(question)
    assert open_run(workflow, scope, context, run)[1] == packet
    operation, state = reserve(workflow, scope, context, run, question)
    assert state == ("reserved" if cap else "COST_CAP_REACHED")
    assert reserve(workflow, scope, context, run, question)[1] == (
        "outcome_unknown" if cap else "unavailable"
    )
    assert (
        reserve(workflow, scope, context, run, question, provider="gemini")[1] == "COST_CAP_REACHED"
    )
    next_run, not_due = open_run(workflow, scope, context)
    assert next_run != run and not_due["reason"] == "CADENCE_NOT_DUE"
    value = projection(api, scope, context)
    assert value["held_micros"] == cap and value["cap_reached"]
    assert value["runs"][-1]["question_set_id"] == str(question_set)
    if cap:
        assert (
            workflow.execute(
                "SELECT control.finish_ai_visibility_call(%s,%s,%s,"
                "'outcome_unknown','PROVIDER_OUTCOME_UNKNOWN',NULL)",
                (scope.tenant_id, scope.site_id, operation),
            ).fetchone()[0]
            == "recorded"
        )
        assert projection(api, scope, context)["held_micros"] == cap
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.ai_visibility_call_reservations SET reserved_micros=0 WHERE id=%s",
            (operation,),
        )
    for connection in (api, workflow):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM app.ai_visibility_call_reservations")


def test_unconfigured_is_visible_and_zero_hold_and_permit_rechecks(
    api, workflow, admin, schedule_context
):
    scope, context, _, question = schedule_context
    settings(api, scope, context)
    run, _ = open_run(workflow, scope, context)
    assert (
        reserve(workflow, scope, context, run, question, configured=False)[1]
        == "PROVIDER_UNCONFIGURED"
    )
    operation, state = reserve(workflow, scope, context, run, question, provider="gemini")
    assert state == "reserved"
    permit = (scope.tenant_id, scope.site_id, operation, context["generation"])
    assert (
        workflow.execute(
            "SELECT control.ai_visibility_call_permit(%s,%s,%s,%s)", permit
        ).fetchone()[0]
        == "permitted"
    )
    settings(api, scope, context, monthly_cap_micros=0)
    assert (
        workflow.execute(
            "SELECT control.ai_visibility_call_permit(%s,%s,%s,%s)", permit
        ).fetchone()[0]
        == "unavailable"
    )
    data = projection(api, scope, context)
    assert data["held_micros"] == 25_000
    assert {call["reason"] for call in data["runs"][0]["calls"]} == {None, "PROVIDER_UNCONFIGURED"}


def test_concurrent_providers_cannot_split_the_site_cap(api, workflow, schedule_context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    scope, context, _, question = schedule_context
    settings(api, scope, context, monthly_cap_micros=25_000)
    run, _ = open_run(workflow, scope, context)
    ready = Barrier(2)

    def attempt(provider):
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as connection:
            ready.wait(timeout=10)
            return reserve(connection, scope, context, run, question, provider=provider)[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("openai", "gemini")))
    assert sorted(results) == ["COST_CAP_REACHED", "reserved"]
    assert projection(api, scope, context)["held_micros"] == 25_000


def test_activity_unconfigured_and_failure_reserves_before_factory_never_retries(
    api, workflow, schedule_context
):
    scope, context, _, _ = schedule_context
    settings(api, scope, context, monthly_cap_micros=25_000)
    calls = []

    @contextmanager
    def connections():
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as connection:
            yield connection

    class Recovery:
        async def current_generation(self):
            return RecoveryGeneration(context["generation"], 1)

    async def egress(site, operation):
        assert projection(api, scope, context)["held_micros"] == 25_000
        calls.append(operation)
        raise RuntimeError("synthetic-secret-material-must-not-enter-history")

    activities = VisibilityActivities(
        connections,
        None,
        Recovery(),
        credentials=object(),
        egress_factory=egress,
        ceilings={"openai": 25_000},
    )
    request = VisibilityRun(VisibilitySite(str(scope.tenant_id), str(scope.site_id)), str(uuid4()))
    first = asyncio.run(activities.run(request))
    assert first["state"] == "unavailable" and len(calls) == 1
    second = asyncio.run(activities.run(request))
    assert second["state"] == "unavailable" and len(calls) == 1
    data = projection(api, scope, context)
    assert data["held_micros"] == 25_000
    assert {call["status"] for call in data["runs"][0]["calls"]} == {
        "outcome_unknown",
        "unavailable",
    }
    assert {call["reason"] for call in data["runs"][0]["calls"]} == {
        "PROVIDER_OUTCOME_UNKNOWN",
        "PROVIDER_UNCONFIGURED",
    }


@pytest.mark.parametrize("value", [True, -1, 31, 1.5])
def test_invalid_cadence_rejected_before_database(value):
    with pytest.raises(ValueError):
        set_visibility_schedule(
            None,
            session_token="synthetic-token",
            site_id=uuid4(),
            generation="test",
            request_id=uuid4(),
            cadence_days=value,
        )


def test_scheduled_success_uses_existing_shared_egress_and_exact_observation(
    api, workflow, scheduler, crawl_admission, crawl_ingest, admin, schedule_context, tmp_path
):
    import hashlib
    import json
    from contextlib import nullcontext

    from signal_core.crawl_admission import OriginAdmissionPolicy
    from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
    from signal_core.crawl_http import EgressHttpResult
    from signal_core.shared_egress import SharedEgressProvider

    from tests.control_plane.test_shared_egress import Fetcher, authority, request, response

    scope, context, _, _ = schedule_context
    settings(api, scope, context)
    store = EncryptedLocalArtifactStore(tmp_path / "assistant-provider")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "visibility-paid-call",
        store,
        origin_override="https://api.openai.com",
    )
    document = {
        "id": "resp_synthetic_schedule",
        "model": "gpt-4.1-mini",
        "status": "completed",
        "output": [],
    }
    body = json.dumps(document).encode()
    result = EgressHttpResult(
        schema_version=1,
        request_url="https://api.openai.com/v1/responses",
        final_url="https://api.openai.com/v1/responses",
        method="POST",
        outcome="fetched",
        http_status=200,
        media_type="application/json",
        response_headers=(("content-type", "application/json"),),
        resolved_address=response(request("https://api.openai.com")).resolved_address,
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=12,
    )
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        Fetcher(result),
        "worker.visibility-provider",
        OriginAdmissionPolicy(),
        None,
    )

    @contextmanager
    def connections():
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as c:
            yield c

    class Recovery:
        async def current_generation(self):
            return RecoveryGeneration(context["generation"], 1)

    class Credentials:
        async def api_key(self, provider):
            assert projection(api, scope, context)["held_micros"] == 25_000
            return "synthetic-openai-key-00000000"

    async def factory(site, operation):
        assert projection(api, scope, context)["held_micros"] == 25_000
        return provider

    activities = VisibilityActivities(
        connections,
        lambda: nullcontext(crawl_ingest),
        Recovery(),
        credentials=Credentials(),
        egress_factory=factory,
        ceilings={"openai": 25_000},
    )
    request = VisibilityRun(VisibilitySite(str(scope.tenant_id), str(scope.site_id)), str(uuid4()))
    asyncio.run(activities.run(request))
    data = projection(api, scope, context)
    paid = next(c for c in data["runs"][0]["calls"] if c["provider"] == "openai")
    assert paid["status"] == "complete" and paid["observation_id"] is not None
    assert admin.execute(
        "SELECT purpose,egress_profile FROM app.egress_operations WHERE id=%s",
        (paid["operation_id"],),
    ).fetchone() == ("model", "openai_assistant")
    assert data["held_micros"] == 25_000 and data["spent_micros"] is None
