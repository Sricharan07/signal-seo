"""Standing workload admission and the existing provider boundaries, on PostgreSQL."""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.ai_visibility_schedule import VisibilityActivities
from signal_core.chat_reports import ChatDelivery
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import EgressProfile
from signal_core.health_monitoring import CHECKS, HealthMonitor, HealthStore
from signal_core.owner_connector_egress import WeeklyPageSpeedContext
from signal_core.pagespeed_credentials import (
    OpenBaoPageSpeedCredentials,
    PageSpeedCredentialUnavailable,
)
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.weekly_delivery_runtime import WeeklyDeliveryRuntime
from signal_core.weekly_loop import WeeklySite
from signal_core.weekly_skill_ports import (
    ChatReportSkillPort,
    PageSpeedSkillPort,
    VisibilitySkillPort,
)
from signal_core.weekly_skills import SKILLS, WeeklySkills

from tests.control_plane.measurement_support import bind_sources, import_fixture
from tests.control_plane.test_chat_reports import chat_env as chat_env
from tests.control_plane.test_chat_reports import slack_egress, telegram_egress
from tests.control_plane.test_gsc_binding import owner_and_verified_origin
from tests.control_plane.test_health_monitoring import record, snapshot
from tests.control_plane.test_pagespeed import send
from tests.control_plane.test_pagespeed import setup as setup
from tests.control_plane.test_recipe_releases import release_manager as release_manager
from tests.control_plane.test_shared_egress import (
    Fetcher,
    authority,
    response,
)
from tests.control_plane.test_shared_egress import (
    request as http_request,
)
from tests.control_plane.test_visibility_schedule import schedule_context as schedule_context
from tests.control_plane.test_visibility_schedule import settings
from tests.control_plane.test_weekly_skills import (
    Recovery,
    admit,
    open_skills,
    report,
    workflow_connection,
)
from tests.control_plane.test_weekly_skills import skill_setup as skill_setup


@pytest.mark.parametrize("table", ["weekly_skill_intents", "weekly_skill_results"])
def test_loop_wiring_constraints_keep_every_registered_stage_and_deny_unknown(admin, table):
    names = {skill.name for skill in SKILLS}
    assert names == {
        "import_gsc",
        "import_bing",
        "import_ga4",
        "pagespeed_refresh",
        "visibility_reobserve",
        "brain_refresh",
        "strategy_rebuild",
        "internal_link_proposals",
        "brief_proposals",
        "report_delivery",
        "chat_report_delivery",
    }
    for stage in sorted(names | {"approve", "delete", "unregistered"}):
        assert admin.execute(
            "SELECT control.sql_dispatch_value_allowed(%s,%s)",
            (table + "_stage_check", stage),
        ).fetchone()[0] is (stage in names)


@pytest.mark.parametrize(
    "key,approval,autonomy,placement,audit,allowed",
    [
        ("links.internal.add", "owner_review", False, False, False, True),
        ("indexnow.key.required", "owner_review", False, False, False, True),
        ("indexnow.key.required", "A4", False, True, False, True),
        ("links.internal.add", "A4", False, True, False, False),
        ("unknown", "owner_review", False, False, False, False),
        ("indexnow.key.required", "A4", True, True, False, False),
        ("indexnow.key.required", "A4", False, False, False, False),
        ("indexnow.key.required", "A2", False, True, False, False),
        ("links.internal.add", "A2", False, True, False, False),
        ("unknown", "A2", True, False, True, True),
    ],
)
def test_loop_wiring_keeps_reviewed_link_evidence_without_widening_a4(
    admin, key, approval, autonomy, placement, audit, allowed
):
    manifest = {
        "evidence": {"finding": {"key": key}},
        "approval_class": approval,
        "autonomy_eligible": autonomy,
    }
    if placement:
        manifest["static_key_placement"] = {"output_path": "dist/synthetic-key.txt"}
    assert (
        admin.execute(
            "SELECT control.sql_dispatch_shape_allowed('candidate_recipe_evidence_kind', "
            "jsonb_build_object('audit_report_id',%s::uuid,'canonical_manifest',%s::bytea))",
            (uuid4() if audit else None, json.dumps(manifest).encode()),
        ).fetchone()[0]
        is allowed
    )


@pytest.fixture
def psi_setup(skill_setup, setup):
    return setup


@pytest.fixture
def weekly_chat_delivery(
    chat_env,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    tmp_path,
):
    scope, _, channel, _, _, sender, _, _ = chat_env
    gateway, double = (telegram_egress if channel == "telegram" else slack_egress)(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )

    class SlackCredentials:
        async def bot(self, reference):
            return await sender.slack_secrets.bot(reference, **sender.slack_options)

    class TelegramCredentials:
        async def bot(self, reference):
            return await sender.telegram_secrets.bot(reference, **sender.telegram_options)

    delivery = ChatDelivery(
        lambda: psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True),
        Recovery(),
        scope,
        lambda site, selected: gateway,
        SlackCredentials(),
        TelegramCredentials(),
    )
    return delivery, double


@pytest.mark.parametrize("chat_env", ["slack_channel", "slack_dm", "telegram"], indirect=True)
@pytest.mark.parametrize(
    "negative",
    [None, "binding_revoked", "unverified", "role", "cap", "configuration", "provider_error"],
)
def test_weekly_chat_stage_delivers_existing_verified_channels_once(
    chat_env,
    weekly_chat_delivery,
    skill_setup,
    workflow,
    api,
    identity_context,
    admin,
    negative,
):
    scope, context, channel, binding, link, _, _, _ = chat_env
    delivery, double = weekly_chat_delivery
    cycle = skill_setup(cap=1 if negative == "cap" else 25)
    open_skills(workflow, cycle)
    if negative == "cap":
        assert admit(workflow, cycle, "strategy_rebuild")[0]["state"] == "started"
    assert (
        workflow.execute(
            "SELECT control.close_weekly_cycle(%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                "completed",
                "CYCLE_REPORTED",
            ),
        ).fetchone()[0]
        == "closed"
    )
    time.sleep(1.1)
    provider = "telegram" if channel == "telegram" else "slack"
    if negative == "binding_revoked":
        admin.execute(
            f"UPDATE app.{provider}_bindings SET revoked_at=now() WHERE id=%s", (binding,)
        )
    elif negative == "unverified":
        if channel == "slack_channel":
            admin.execute("UPDATE app.slack_bindings SET revoked_at=now() WHERE id=%s", (binding,))
        else:
            admin.execute(f"UPDATE app.{provider}_links SET revoked_at=now() WHERE id=%s", (link,))
    elif negative == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='viewer' WHERE tenant_id=%s", (scope.tenant_id,)
        )
    elif negative == "configuration":
        admin.execute(
            "DELETE FROM control.chat_report_configuration WHERE provider=%s", (provider,)
        )
    elif negative == "provider_error":
        double.failure = True
    engine = WeeklySkills(
        workflow_connection,
        Recovery(),
        ports={"chat_report_delivery": ChatReportSkillPort(workflow_connection, delivery)},
    )
    asyncio.run(engine.run(cycle, "delivery"))
    if negative == "role":
        assert report(api, cycle, identity_context) is None
        projection = admin.execute(
            "SELECT control.weekly_report_projection(%s,%s,%s)",
            (scope.tenant_id, scope.site_id, cycle.week_start),
        ).fetchone()[0]
    else:
        projection = report(api, cycle, identity_context)
    result = next(s for s in projection["skill_stages"] if s["stage"] == "chat_report_delivery")
    expected = {
        None: ("completed", "CHAT_REPORT_ACCEPTED"),
        "binding_revoked": ("unavailable", "NO_VERIFIED_CHAT_RECIPIENT"),
        "unverified": ("unavailable", "NO_VERIFIED_CHAT_RECIPIENT"),
        "role": ("unavailable", "AUTHORITY_CHANGED"),
        "cap": ("unavailable", "WEEKLY_CAP_REACHED"),
        "configuration": ("unavailable", "NO_VERIFIED_CHAT_RECIPIENT"),
        "provider_error": ("failed", "CHAT_REPORT_OUTCOME_UNKNOWN"),
    }[negative]
    assert (result["outcome"], result["detail_code"]) == expected
    calls = 1 if negative in {None, "provider_error"} else 0
    assert len(double.calls) == calls
    asyncio.run(engine.run(cycle, "delivery"))
    assert len(double.calls) == calls


@pytest.mark.parametrize("chat_env", ["slack_dm", "telegram"], indirect=True)
@pytest.mark.parametrize("failure", [False, True])
def test_runtime_registers_health_chat_and_preserves_unknown_without_reposting(
    chat_env,
    weekly_chat_delivery,
    admin,
    failure,
):
    scope, context, _, _, _, _, _, _ = chat_env
    delivery, double = weekly_chat_delivery
    double.failure = failure
    probes = {check: lambda: asyncio.sleep(0, result={"condition": "healthy"}) for check in CHECKS}
    probes["binding_gsc"] = lambda: asyncio.sleep(0, result={"condition": "revoked"})
    monitor = HealthMonitor(
        HealthStore(
            lambda: psycopg.connect(os.environ["SIGNAL_TEST_SCHEDULER_DSN"], autocommit=True)
        ),
        scope,
        Recovery(),
        probes,
    )
    WeeklyDeliveryRuntime(
        None,
        WeeklySite(str(scope.tenant_id), str(scope.site_id), str(uuid4())),
        None,
        None,
        health_monitor=monitor,
        chat_delivery=delivery,
    )
    assert monitor.chat_alerts == delivery.queue_health
    asyncio.run(monitor.run_once())
    assert monitor.persisted
    time.sleep(1.1)
    assert asyncio.run(delivery.pump()) == ["unknown" if failure else "accepted"]
    assert len(double.calls) == 1
    asyncio.run(monitor.run_once())
    assert asyncio.run(delivery.pump()) == []
    assert len(double.calls) == 1
    assert (
        admin.execute(
            "SELECT count(*) FROM app.chat_report_outbox "
            "WHERE tenant_id=%s AND category='health_alert'",
            (scope.tenant_id,),
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(PermissionError):
        asyncio.run(delivery.queue_health(scope, "synthetic-wrong-generation"))


@pytest.mark.parametrize("negative", ["profile", "url", "operation"])
def test_psi_grant_cannot_authorize_another_profile_url_or_operation(
    psi_setup, workflow, skill_setup, negative, request
):
    provider, _, origin = psi_setup
    cycle = skill_setup()
    open_skills(workflow, cycle)
    admitted, handle = admit(workflow, cycle, "pagespeed_refresh")
    assert admitted["state"] == "started"
    ingest = workflow_connection()
    request.addfinalizer(ingest.close)
    gateway = replace(
        provider,
        admission_connection=workflow,
        ingest_connection=ingest,
        run=WeeklyPageSpeedContext(
            provider.run.tenant_id, provider.run.site_id, handle, "test-generation-1"
        ),
    )
    sample = admitted["plan"][0]
    if negative == "profile":
        with pytest.raises((ProviderEgressUnavailable, ValueError)):
            gateway.request_json(
                method="GET",
                url="https://www.googleapis.com/webmasters/v3/sites",
                profile=EgressProfile.GSC_API,
                authorization=None,
                operation_id=uuid4(),
                max_response_bytes=512 * 1024,
                timeout_seconds=25,
            )
    else:
        if negative == "url":
            sample = {**sample, "url": origin + "/not-admitted"}
        elif negative == "operation":
            sample = {**sample, "sample_id": str(uuid4())}
        with pytest.raises(ProviderEgressUnavailable):
            send(gateway, sample, key=None)
    assert provider.fetcher.calls == [] or all(
        call.url.endswith("/robots.txt") for call in provider.fetcher.calls
    )


def test_psi_standing_collector_does_not_create_or_borrow_sessions(
    psi_setup, skill_setup, workflow, admin, api, identity_context
):
    provider, _, _ = psi_setup
    cycle = skill_setup()
    open_skills(workflow, cycle)
    sessions = admin.execute("SELECT count(*) FROM app.sessions").fetchone()[0]
    port = PageSpeedSkillPort(
        workflow_connection,
        provider,
        OpenBaoPageSpeedCredentials("https://bao.example.invalid", "synthetic-reader-token"),
    )
    engine = WeeklySkills(workflow_connection, Recovery(), ports={"pagespeed_refresh": port})
    asyncio.run(engine.run(cycle, "research"))
    first = report(api, cycle, identity_context)
    result = next(s for s in first["skill_stages"] if s["stage"] == "pagespeed_refresh")
    assert result["outcome"] == "completed" and result["detail_code"] == "PSI_OBSERVED_KEYLESS", (
        result["detail_code"]
    )
    assert result["units"] == 4 and len(provider.fetcher.calls) == 8
    assert admin.execute("SELECT count(*) FROM app.sessions").fetchone()[0] == sessions
    rows = admin.execute(
        "SELECT weekly_skill_handle_hash,session_hash "
        "FROM app.owner_connector_egress_operations WHERE site_id=%s",
        (provider.run.site_id,),
    ).fetchall()
    assert len(rows) == 8 and all(handle is not None and handle == token for handle, token in rows)
    asyncio.run(engine.run(cycle, "research"))
    assert len(provider.fetcher.calls) == 8
    assert report(api, cycle, identity_context) == first


@pytest.mark.parametrize("status", [0, 503])
def test_psi_provider_failure_is_visible_and_not_retried(
    psi_setup, skill_setup, workflow, api, identity_context, status
):
    provider, _, _ = psi_setup
    provider.fetcher.status = status
    cycle = skill_setup()
    open_skills(workflow, cycle)
    port = PageSpeedSkillPort(
        workflow_connection,
        provider,
        OpenBaoPageSpeedCredentials("https://bao.example.invalid", "synthetic-reader-token"),
    )
    engine = WeeklySkills(workflow_connection, Recovery(), ports={"pagespeed_refresh": port})
    asyncio.run(engine.run(cycle, "research"))
    stages = report(api, cycle, identity_context)["skill_stages"]
    result = next(s for s in stages if s["stage"] == "pagespeed_refresh")
    assert result["outcome"] == "unavailable" and result["detail_code"] == (
        "PSI_RESPONSE_REJECTED" if status == 0 else "PSI_PROVIDER_UNAVAILABLE"
    )
    calls = len(provider.fetcher.calls)
    assert calls >= 2
    asyncio.run(engine.run(cycle, "research"))
    assert len(provider.fetcher.calls) == calls


def test_configured_psi_key_unavailable_is_visible_without_keyless_fallback(
    psi_setup, skill_setup, workflow, api, identity_context
):
    provider, _, _ = psi_setup

    class UnavailableCredentials:
        async def api_key(self):
            raise PageSpeedCredentialUnavailable()

    cycle = skill_setup()
    open_skills(workflow, cycle)
    engine = WeeklySkills(
        workflow_connection,
        Recovery(),
        ports={
            "pagespeed_refresh": PageSpeedSkillPort(
                workflow_connection, provider, UnavailableCredentials()
            )
        },
    )
    asyncio.run(engine.run(cycle, "research"))
    result = next(
        s
        for s in report(api, cycle, identity_context)["skill_stages"]
        if s["stage"] == "pagespeed_refresh"
    )
    assert (result["outcome"], result["detail_code"]) == (
        "unavailable",
        "PSI_CREDENTIAL_UNAVAILABLE",
    )
    assert provider.fetcher.calls == []
    asyncio.run(engine.run(cycle, "research"))
    assert provider.fetcher.calls == []


@pytest.mark.parametrize(
    "negative", ["no_grant", "cap", "expired", "revoked", "role", "site", "tenant", "generation"]
)
def test_psi_workload_admission_and_dispatch_refuse_changed_authority(
    psi_setup, skill_setup, workflow, admin, scopes, negative, api, identity_context
):
    cycle = skill_setup(
        research=negative != "no_grant",
        cap=1 if negative == "cap" else 25,
        lifetime=5 if negative == "expired" else 604800,
    )
    open_skills(workflow, cycle)
    admitted, handle = admit(workflow, cycle, "pagespeed_refresh")
    if negative in {"no_grant", "cap"}:
        assert admitted["state"] == "unavailable"
        assert admitted["reason"] == (
            "WORK_TYPE_NOT_GRANTED" if negative == "no_grant" else "WEEKLY_CAP_REACHED"
        )
        return
    assert admitted["state"] == "started"
    site, generation = UUID(cycle.site.site_id), "test-generation-1"
    if negative == "expired":
        import time

        time.sleep(5.1)
    elif negative == "revoked":
        from signal_core.standing_authorization import revoke_standing_authorization

        revoke_standing_authorization(
            api,
            session_token=identity_context["session_token"],
            current_recovery_generation=identity_context["generation"],
            site_id=UUID(cycle.site.site_id),
            grant_id=UUID(cycle.site.grant_id),
        )
    elif negative == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='viewer' WHERE tenant_id=%s",
            (cycle.site.tenant_id,),
        )
    elif negative in {"site", "tenant"}:
        site = scopes[1 if negative == "site" else 2].site_id
    elif negative == "generation":
        generation = "synthetic-other-generation"
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _clean_transaction(workflow):
        workflow.execute(
            "SELECT * FROM control.weekly_skill_pagespeed_context(%s,%s,%s)",
            (hash_session_token(handle), generation, site),
        )


def test_new_ports_unconfigured_are_visible_and_not_reserved(
    workflow, skill_setup, api, identity_context, admin
):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    asyncio.run(WeeklySkills(workflow_connection, Recovery(), ports={}).run(cycle, "research"))
    stages = {s["stage"]: s for s in report(api, cycle, identity_context)["skill_stages"]}
    for stage in ("pagespeed_refresh", "visibility_reobserve"):
        assert stages[stage]["outcome"] == "unavailable"
        assert stages[stage]["detail_code"] == "PORT_UNCONFIGURED"
    assert (
        workflow.execute(
            "SELECT control.close_weekly_cycle(%s,%s,%s,%s,%s,%s)",
            (
                cycle.site.tenant_id,
                cycle.site.site_id,
                cycle.week_start,
                cycle.cycle_id,
                "completed",
                "CYCLE_REPORTED",
            ),
        ).fetchone()[0]
        == "closed"
    )
    asyncio.run(WeeklySkills(workflow_connection, Recovery(), ports={}).run(cycle, "delivery"))
    chat = next(
        s
        for s in report(api, cycle, identity_context)["skill_stages"]
        if s["stage"] == "chat_report_delivery"
    )
    assert (chat["outcome"], chat["detail_code"]) == ("unavailable", "PORT_UNCONFIGURED")
    assert not admin.execute(
        "SELECT 1 FROM app.weekly_skill_intents WHERE tenant_id=%s", (cycle.site.tenant_id,)
    ).fetchall()


@pytest.mark.parametrize(
    "negative", ["no_grant", "cap", "spend", "revoked", "role", "site", "tenant", "generation"]
)
def test_visibility_workload_refuses_missing_or_changed_standing_authority(
    schedule_context, skill_setup, workflow, api, admin, scopes, identity_context, negative
):
    scope, context, _, _ = schedule_context
    assert settings(api, scope, context) == "updated"
    cycle = skill_setup(
        research=negative != "no_grant",
        cap=1 if negative == "cap" else 25,
        spend=8 if negative == "spend" else 100,
    )
    open_skills(workflow, cycle)
    result, handle = admit(workflow, cycle, "visibility_reobserve", cost=3)
    if negative in {"no_grant", "cap", "spend"}:
        assert result["state"] == "unavailable"
        assert result["reason"] == (
            "WORK_TYPE_NOT_GRANTED" if negative == "no_grant" else "WEEKLY_CAP_REACHED"
        )
        return
    assert result["state"] == "started"
    if negative == "revoked":
        from signal_core.standing_authorization import revoke_standing_authorization

        revoke_standing_authorization(
            api,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            grant_id=UUID(cycle.site.grant_id),
        )
    elif negative == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='viewer' WHERE tenant_id=%s", (scope.tenant_id,)
        )
    tenant = scopes[2].tenant_id if negative == "tenant" else scope.tenant_id
    site = scopes[1].site_id if negative == "site" else scope.site_id
    generation = "synthetic-wrong-generation" if negative == "generation" else context["generation"]
    run = admin.execute(
        "SELECT control.weekly_skill_identity(%s,%s)", (cycle.cycle_id, "visibility_reobserve")
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _clean_transaction(workflow):
        workflow.execute(
            "SELECT control.weekly_skill_open_ai_visibility_run(%s,%s,%s,%s,%s,%s)",
            (hash_session_token(handle), generation, tenant, site, run, generation),
        )


def test_visibility_admission_holds_weekly_spend_and_cannot_exceed_reviewed_ceiling(
    schedule_context, skill_setup, workflow, api, admin
):
    scope, context, _, question = schedule_context
    assert settings(api, scope, context) == "updated"
    cycle = skill_setup(spend=100)
    open_skills(workflow, cycle)
    admitted, handle = admit(workflow, cycle, "visibility_reobserve", cost=3)
    assert admitted["state"] == "started" and len(admitted["plan"]) == 3
    digest = hash_session_token(handle)
    run = admin.execute(
        "SELECT control.weekly_skill_identity(%s,%s)", (cycle.cycle_id, "visibility_reobserve")
    ).fetchone()[0]
    args = (
        digest,
        context["generation"],
        scope.tenant_id,
        scope.site_id,
        run,
        context["generation"],
    )
    packet = workflow.execute(
        "SELECT control.weekly_skill_open_ai_visibility_run(%s,%s,%s,%s,%s,%s)", args
    ).fetchone()[0]
    assert packet["questions"][0]["id"] == str(question)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _clean_transaction(workflow):
        workflow.execute(
            "SELECT control.weekly_skill_reserve_ai_visibility_call(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*args[:5], question, "openai", context["generation"], True, 30_001),
        )
    assert (
        admin.execute(
            "SELECT reserved_cents FROM app.weekly_skill_intents WHERE handle_hash=%s", (digest,)
        ).fetchone()[0]
        == 9
    )


@pytest.mark.parametrize("failure", [False, True])
def test_visibility_weekly_port_uses_existing_assistant_budgets_and_gateway(
    schedule_context,
    skill_setup,
    workflow,
    api,
    scheduler,
    crawl_admission,
    crawl_ingest,
    tmp_path,
    admin,
    identity_context,
    failure,
):
    scope, context, _, _ = schedule_context
    assert settings(api, scope, context) == "updated"
    gateways = {}

    class AssistantFetcher(Fetcher):
        def request(self, outbound, *, policy):
            value = super().request(outbound, policy=policy)
            return replace(
                value, request_url=outbound.url, final_url=outbound.url, method=outbound.method
            )

    for name, origin, profile in (
        ("openai", "https://api.openai.com", EgressProfile.OPENAI_ASSISTANT),
        ("perplexity", "https://api.perplexity.ai", EgressProfile.PERPLEXITY_ASSISTANT),
        ("gemini", "https://generativelanguage.googleapis.com", EgressProfile.GEMINI_ASSISTANT),
    ):
        store = EncryptedLocalArtifactStore(tmp_path / name)
        run, policy, _ = authority(
            api,
            scheduler,
            workflow,
            crawl_admission,
            crawl_ingest,
            scope,
            "synthetic-weekly-" + name,
            store,
            origin_override=origin,
        )
        doc = (
            {
                "responseId": "synthetic-gemini-response",
                "modelVersion": "gemini-2.5-flash",
                "candidates": [
                    {"content": {"parts": [{"text": "Synthetic response"}]}, "finishReason": "STOP"}
                ],
            }
            if name == "gemini"
            else {
                "id": "resp_synthetic_weekly",
                "model": "perplexity/fast" if name == "perplexity" else "gpt-4.1-mini",
                "status": "completed",
                "output": [],
            }
        )
        body = json.dumps(doc).encode()
        result = replace(
            response(http_request(origin)),
            http_status=503 if failure and name == "openai" else 200,
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
        )
        gateways[profile] = SharedEgressProvider(
            crawl_admission,
            crawl_ingest,
            store,
            run,
            policy,
            AssistantFetcher(result),
            "worker.visibility-weekly",
            OriginAdmissionPolicy(),
            None,
        )

    class Credentials:
        async def api_key(self, provider):
            return "synthetic-assistant-key-0144"

    async def egress(site, operation):
        return gateways

    source = VisibilityActivities(
        workflow_connection,
        lambda: psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True),
        Recovery(),
        credentials=Credentials(),
        egress_factory=egress,
        ceilings={"openai": 25_000, "perplexity": 25_000, "gemini": 25_000},
    )
    cycle = skill_setup()
    open_skills(workflow, cycle)

    engine = WeeklySkills(
        workflow_connection, Recovery(), ports={"visibility_reobserve": VisibilitySkillPort(source)}
    )
    asyncio.run(engine.run(cycle, "research"))
    result = next(
        s
        for s in report(api, cycle, identity_context)["skill_stages"]
        if s["stage"] == "visibility_reobserve"
    )
    assert result["outcome"] == ("unavailable" if failure else "completed"), result["detail_code"]
    calls = admin.execute(
        "SELECT status FROM app.ai_visibility_call_results WHERE tenant_id=%s", (scope.tenant_id,)
    ).fetchall()
    assert len(calls) == 3
    assert sum(row[0] == "complete" for row in calls) == (2 if failure else 3)
    counts = [len(g.fetcher.calls) for g in gateways.values()]
    asyncio.run(engine.run(cycle, "research"))
    assert [len(g.fetcher.calls) for g in gateways.values()] == counts


@pytest.mark.parametrize("negative", ["revoked", "unverified", "role", "configuration"])
@pytest.mark.parametrize("chat_env", ["slack_dm"], indirect=True)
def test_health_chat_never_queues_an_invalid_destination(
    chat_env, scheduler, identity, admin, negative
):
    scope, context, _, binding, link, _, _, _ = chat_env
    assert record(scheduler, scope, snapshot(binding_gsc="revoked"))["alerts"] == 1
    if negative == "revoked":
        admin.execute(
            "UPDATE app.slack_bindings SET revoked_at=clock_timestamp() WHERE id=%s", (binding,)
        )
    elif negative == "unverified":
        admin.execute(
            "UPDATE app.slack_links SET revoked_at=clock_timestamp() WHERE id=%s", (link,)
        )
    elif negative == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='viewer' WHERE tenant_id=%s", (scope.tenant_id,)
        )
    else:
        admin.execute("DELETE FROM control.chat_report_configuration WHERE provider='slack'")
    assert (
        identity.execute(
            "SELECT control.queue_health_chat_alerts(%s,%s,%s)",
            (scope.tenant_id, scope.site_id, context["generation"]),
        ).fetchone()[0]
        == []
    )


def test_health_chat_preserves_upstream_dedup_and_rate_limit(
    chat_env, scheduler, identity, admin, api
):
    scope, context, _, _, _, _, _, _ = chat_env
    args = (scope.tenant_id, scope.site_id, context["generation"])
    assert record(scheduler, scope, snapshot(binding_gsc="revoked"))["alerts"] == 1
    refs = identity.execute("SELECT control.queue_health_chat_alerts(%s,%s,%s)", args).fetchone()[0]
    assert len(refs) == 1
    assert record(scheduler, scope, snapshot(binding_gsc="revoked"))["alerts"] == 0
    assert (
        identity.execute("SELECT control.queue_health_chat_alerts(%s,%s,%s)", args).fetchone()[0]
        == refs
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT control.queue_health_chat_alerts(%s,%s,%s)", args)
    assert (
        admin.execute(
            "SELECT count(*) FROM app.chat_report_outbox "
            "WHERE tenant_id=%s AND category='health_alert'",
            (scope.tenant_id,),
        ).fetchone()[0]
        == 1
    )


def test_bing_page_windows_preserve_reported_dates_site_context_and_missing_reason(
    admin, identity, scopes, identity_context
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    bindings, page = bind_sources(admin, identity, scope, identity_context)
    start, end = date(2026, 8, 1), date(2026, 8, 7)
    import_fixture(admin, scope, bindings, page, start, end)
    coverage = {
        "complete": False,
        "source": "bing_webmaster",
        "coverage": "provider_returned_top_pages",
        "date_granularity": "unknown",
        "provider_update_frequency": "weekly",
    }
    rows = [
        {
            "page_url": page,
            "date": f"/Date({int(datetime(2026, 8, 2, tzinfo=UTC).timestamp() * 1000)}+0000)/",
            "clicks": 5,
            "impressions": 50,
            "avg_click_position": 2,
            "avg_impression_position": 3,
        }
    ]
    admin.execute(
        "INSERT INTO app.bing_import_generations(tenant_id,site_id,id,binding_id,"
        "property_resource_name,kind,rows,coverage,response_sha256,egress_operation_id) "
        "VALUES(%s,%s,%s,%s,%s,'page_performance',%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            uuid4(),
            bindings["bing"],
            page,
            Jsonb(rows),
            Jsonb(coverage),
            hashlib.sha256(b"synthetic-page-row").digest(),
            uuid4(),
        ),
    )

    def window(target, first, last):
        return admin.execute(
            "SELECT control.measurement_source_window(%s,%s,%s,%s,%s,%s)",
            (scope.tenant_id, scope.site_id, target, first, last, datetime.now(UTC)),
        ).fetchone()[0]

    before = window(page, start, end)
    assert before["bing_page"]["state"] == "measured_as_reported"
    assert before["bing_page"]["coverage"] == coverage
    assert before["bing_page"]["metrics"] == {
        "clicks": 5,
        "impressions": 50,
        "ctr": None,
        "position": None,
    }
    assert before["bing_site_context"]["state"] == "measured_as_reported"
    assert (
        window(page, date(2026, 8, 8), date(2026, 8, 14))["bing_page"]["reason"]
        == "PAGE_ROWS_NOT_REPORTED_IN_WINDOW"
    )
    assert window(page + "not-measured", start, end)["bing_page"]["metrics"] is None
