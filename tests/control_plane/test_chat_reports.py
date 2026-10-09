import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import httpx2
import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.chat_reports import ChatReports, ChatReportSender, configure_chat_reports
from signal_core.shared_egress import ProviderEgressUnavailable
from signal_core.slack_secrets import OpenBaoSlackSecrets
from signal_core.telegram_secrets import OpenBaoTelegramSecrets

from tests.control_plane.test_gsc_binding import owner_and_verified_origin
from tests.control_plane.test_slack_binding import BOT as SLACK_BOT
from tests.control_plane.test_slack_binding import BaoDouble
from tests.control_plane.test_slack_binding import egress as slack_egress
from tests.control_plane.test_telegram_binding import BOT as TELEGRAM_BOT
from tests.control_plane.test_telegram_binding import USER as CHAT
from tests.control_plane.test_telegram_binding import egress as telegram_egress
from tests.control_plane.test_weekly_loop import _cycle, _grant, _open


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def chat_env(request, admin, api, identity, scopes, identity_context):
    channel = getattr(request, "param", "slack_channel")
    scope, context = scopes[0], identity_context
    owner_and_verified_origin(admin, identity, context, scope.site_id)
    provider = "telegram" if channel == "telegram" else "slack"
    with psycopg.connect(os.environ["SIGNAL_TEST_BOOTSTRAP_DSN"], autocommit=True) as bootstrap:
        configure_chat_reports(
            bootstrap, provider=provider, origin="https://dashboard.example.invalid", daily_cap=100
        )
    binding, link = uuid4(), uuid4()
    if provider == "slack":
        admin.execute(
            "INSERT INTO app.slack_bindings(tenant_id,site_id,id,workspace_id,channel_id,"
            "owner_user_id,secret_reference,max_risk,recovery_generation) "
            "VALUES(%s,%s,%s,'T00000133','C00000133',%s,%s,2,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                binding,
                context["user_id"],
                "secret://slack/" + str(binding),
                context["generation"],
            ),
        )
        admin.execute(
            "INSERT INTO control.slack_binding_routes VALUES(%s,%s,%s)",
            (binding, scope.tenant_id, scope.site_id),
        )
        admin.execute(
            "INSERT INTO app.slack_links(tenant_id,site_id,id,binding_id,user_id,slack_user_id,"
            "membership_epoch,recovery_generation) "
            "VALUES(%s,%s,%s,%s,%s,'U00000001',2,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                link,
                binding,
                context["user_id"],
                context["generation"],
            ),
        )
    else:
        admin.execute(
            "INSERT INTO app.telegram_bindings(tenant_id,site_id,id,owner_user_id,secret_reference,"
            "bot_id,bot_username,state,max_risk,recovery_generation) "
            "VALUES(%s,%s,%s,%s,%s,%s,'synthetic_report_bot','active',2,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                binding,
                context["user_id"],
                "secret://telegram/" + str(binding),
                str(uuid4().int % 10**12 + 1),
                context["generation"],
            ),
        )
        admin.execute(
            "INSERT INTO control.telegram_binding_routes VALUES(%s,%s,%s)",
            (binding, scope.tenant_id, scope.site_id),
        )
        admin.execute(
            "INSERT INTO app.telegram_links(tenant_id,site_id,id,binding_id,user_id,"
            "telegram_user_id,chat_id,membership_epoch,recovery_generation) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,2,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                link,
                binding,
                context["user_id"],
                CHAT,
                CHAT,
                context["generation"],
            ),
        )
    bao = BaoDouble()
    bao.documents["bots/" + str(binding)] = (
        {"bot_token": TELEGRAM_BOT, "webhook_secret": "synthetic-webhook-secret-report-0133"}
        if provider == "telegram"
        else {"bot_token": SLACK_BOT}
    )
    options = {"transport": httpx2.MockTransport(bao.request)}
    sender = ChatReportSender(
        identity,
        context["generation"],
        OpenBaoSlackSecrets("https://bao.example.invalid", "synthetic-bao-token-0133"),
        OpenBaoTelegramSecrets("https://bao.example.invalid", "synthetic-bao-token-0133"),
        options,
        options,
    )
    preferences = ChatReports(api, context["generation"])
    assert (
        preferences.preference(
            context["session_token"], scope.site_id, channel=channel, enabled=True
        )
        == "enabled"
    )
    return scope, context, channel, binding, link, sender, preferences, bao


def queue(admin, env, *, event=None, category="weekly_report", projection=None):
    scope, _, channel, binding, _, _, _, _ = env
    event = event or uuid4()
    if projection is None:
        projection = {
            "site_id": str(scope.site_id),
            "week_start": "2026-09-28",
            "stages": [],
            "delivery": [],
            "waiting_for_owner": [],
            "deferred": [],
        }
    admin.execute(
        "SELECT control.queue_chat_report_event(%s,%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, event, category, Jsonb(projection)),
    )
    return admin.execute(
        "SELECT id FROM app.chat_report_outbox WHERE event_id=%s AND channel=%s AND binding_id=%s",
        (event, channel, binding),
    ).fetchone()[0]


@pytest.mark.anyio
@pytest.mark.parametrize("chat_env", ["slack_channel", "slack_dm", "telegram"], indirect=True)
async def test_chat_report_gateway_send_idempotency_and_secret_isolation(
    chat_env, admin, api, scheduler, workflow, crawl_admission, crawl_ingest, tmp_path
):
    scope, context, channel, _, _, sender, prefs, _ = chat_env
    provider, double = (telegram_egress if channel == "telegram" else slack_egress)(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    event = uuid4()
    identifier = queue(admin, chat_env, event=event)
    assert queue(admin, chat_env, event=event) == identifier
    time.sleep(1.1)
    assert await sender.deliver(identifier, egress=provider) == "accepted"
    assert await sender.deliver(identifier, egress=provider) == "accepted"
    assert len(double.calls) == 1
    assert double.calls[0].url.endswith(
        "sendMessage" if channel == "telegram" else "chat.postMessage"
    )
    payload = json.loads(double.calls[0].body)
    assert "blocks" not in payload and "reply_markup" not in payload
    assert prefs.read(context["session_token"], scope.site_id)["history"][0]["state"] == "accepted"
    for table in ("chat_report_preferences", "chat_report_outbox", "chat_report_receipts"):
        rows = admin.execute(
            f"SELECT to_jsonb(t)::text FROM app.{table} t WHERE tenant_id=%s", (scope.tenant_id,)
        ).fetchall()
        assert all(SLACK_BOT not in row[0] and TELEGRAM_BOT not in row[0] for row in rows)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(f"SELECT * FROM app.{table}")


@pytest.mark.anyio
@pytest.mark.parametrize("chat_env", ["slack_channel", "slack_dm", "telegram"], indirect=True)
@pytest.mark.parametrize(
    "denial",
    [
        "revoked",
        "unlinked",
        "opt_out",
        "role",
        "tenant",
        "recovery",
        "membership",
        "site_epoch",
        "tombstone",
    ],
)
async def test_chat_report_denied_destinations_are_suppressed_and_audited(chat_env, admin, denial):
    scope, context, channel, binding, link, sender, prefs, _ = chat_env
    identifier = queue(admin, chat_env)
    provider = "telegram" if channel == "telegram" else "slack"
    if denial == "revoked":
        admin.execute(
            f"UPDATE app.{provider}_bindings SET revoked_at=now() WHERE id=%s", (binding,)
        )
    elif denial == "unlinked":
        if channel == "slack_channel":
            admin.execute(
                "UPDATE app.memberships SET state='removed' WHERE user_id=%s", (context["user_id"],)
            )
        else:
            admin.execute(f"UPDATE app.{provider}_links SET revoked_at=now() WHERE id=%s", (link,))
    elif denial == "opt_out":
        assert (
            prefs.preference(
                context["session_token"], scope.site_id, channel=channel, enabled=False
            )
            == "disabled"
        )
    elif denial == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='analyst' WHERE user_id=%s", (context["user_id"],)
        )
    elif denial == "tenant":
        admin.execute(
            "UPDATE app.tenants SET lifecycle='suspended' WHERE tenant_id=%s", (scope.tenant_id,)
        )
    elif denial == "recovery":
        sender = replace(sender, recovery_generation="synthetic-new-generation")
    elif denial == "membership":
        admin.execute(
            "UPDATE app.memberships SET state='removed' WHERE user_id=%s", (context["user_id"],)
        )
        admin.execute(
            "UPDATE app.memberships SET state='active' WHERE user_id=%s", (context["user_id"],)
        )
    elif denial == "site_epoch":
        admin.execute(
            "UPDATE app.site_memberships SET authorization_epoch=authorization_epoch+1 "
            "WHERE user_id=%s",
            (context["user_id"],),
        )
    else:
        admin.execute(
            "INSERT INTO control.authority_denial_tombstones(event_id,target_kind,target_id,"
            "restriction_kind,effective_epoch,stream_generation,stream_position,payload_hash) "
            "VALUES(%s,%s,%s,%s,1,%s,1,%s)",
            (
                uuid4(),
                provider + "_binding",
                binding,
                provider + "_binding_revoked",
                uuid4(),
                "a" * 64,
            ),
        )
    assert sender.inspect(identifier) == {"state": "suppressed"}

    class NoNetwork:
        def request_json(self, **kwargs):
            pytest.fail("Denied chat recipient reached provider I/O.")

    assert await sender.deliver(identifier, egress=NoNetwork()) == "suppressed"
    assert admin.execute(
        "SELECT count(*) FROM app.chat_report_receipts WHERE outbox_id=%s AND phase='suppression'",
        (identifier,),
    ).fetchone() == (1,)
    assert sender.inspect(identifier) == {"state": "suppressed"}


def test_chat_report_daily_cap_claim_replay_bounded_retry_and_abandoned_claim(chat_env, admin):
    scope, context, _, _, _, sender, _, _ = chat_env
    digest = hashlib.sha256(b"synthetic-report").digest()
    first = queue(admin, chat_env)
    for attempt in range(3):
        claimed = sender._one("chat_report_item", first, context["generation"], digest)
        assert claimed["state"] == "claimed"
        assert sender._one("chat_report_item", first, context["generation"], digest) == {
            "state": "dispatching"
        }
        assert sender._one("finish_chat_report", first, digest, "deferred") == (
            "retry" if attempt < 2 else "failed"
        )
        admin.execute(
            "UPDATE app.chat_report_outbox SET ready_at=now()-interval '1 second' WHERE id=%s",
            (first,),
        )
    assert sender.inspect(first) == {"state": "failed"}
    # Cap includes safe pre-I/O deferrals and both Slack channel and DM attempts.
    admin.execute("UPDATE control.chat_report_configuration SET daily_cap=3 WHERE provider='slack'")
    second = queue(admin, chat_env)
    assert sender._one("chat_report_item", second, context["generation"], digest) == {
        "state": "suppressed"
    }
    assert admin.execute(
        "SELECT outcome FROM app.chat_report_receipts WHERE outbox_id=%s", (second,)
    ).fetchone() == ("cap_reached",)
    admin.execute(
        "UPDATE control.chat_report_configuration SET daily_cap=100 WHERE provider='slack'"
    )
    third = queue(admin, chat_env)
    assert (
        sender._one("chat_report_item", third, context["generation"], digest)["state"] == "claimed"
    )
    admin.execute(
        "UPDATE app.chat_report_outbox SET ready_at=now()-interval '1 second' WHERE id=%s", (third,)
    )
    assert sender.inspect(third) == {"state": "unknown"}
    assert sender._one("finish_chat_report", third, digest, "accepted") == "unknown"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["lost_reply", "deferred", "missing_secret", "bad_projection"])
async def test_chat_report_failure_no_blind_reposts(
    chat_env, admin, api, scheduler, workflow, crawl_admission, crawl_ingest, tmp_path, failure
):
    scope, _, _, _, _, sender, _, bao = chat_env
    projection = (
        {
            "site_id": str(scope.site_id),
            "week_start": "2026-09-28",
            "stages": [{"stage": "synthetic-invalid", "outcome": "completed"}],
        }
        if failure == "bad_projection"
        else None
    )
    identifier = queue(admin, chat_env, projection=projection)
    provider, double = slack_egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    if failure == "missing_secret":
        bao.documents.clear()
    elif failure == "lost_reply":
        double.failure = True
    elif failure == "deferred":

        class Deferred:
            def request_json(self, **kwargs):
                raise ProviderEgressUnavailable("EGRESS_DEFERRED", retryable=True)

        provider = Deferred()
    time.sleep(1.1)
    expected = {
        "lost_reply": "unknown",
        "deferred": "retry",
        "missing_secret": "suppressed",
        "bad_projection": "suppressed",
    }[failure]
    assert await sender.deliver(identifier, egress=provider) == expected
    count = len(double.calls)
    assert await sender.deliver(identifier, egress=provider) == expected
    assert len(double.calls) == count


def test_chat_report_owner_site_role_and_configuration_negatives(chat_env, api, admin, scopes):
    scope, context, channel, _, _, _, prefs, _ = chat_env
    for site in (scopes[1].site_id, scopes[2].site_id, uuid4()):
        with pytest.raises(PermissionError):
            prefs.read(context["session_token"], site)
        with pytest.raises(PermissionError):
            prefs.preference(context["session_token"], site, channel=channel, enabled=True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(
            "SELECT control.configure_chat_reports('slack','https://dashboard.example.invalid',10)"
        )
    admin.execute("DELETE FROM control.chat_report_configuration WHERE provider='slack'")
    assert (
        prefs.read(context["session_token"], scope.site_id)["channels"][0]["availability"]
        == "unavailable"
    )
    assert (
        prefs.preference(context["session_token"], scope.site_id, channel=channel, enabled=True)
        == "unavailable"
    )
    assert (
        prefs.preference(context["session_token"], scope.site_id, channel=channel, enabled=False)
        == "disabled"
    )
    admin.execute(
        "UPDATE app.memberships SET role_key='analyst' WHERE user_id=%s", (context["user_id"],)
    )
    with pytest.raises(PermissionError):
        prefs.read(context["session_token"], scope.site_id)


def test_chat_report_committed_weekly_and_pause_alert_queue_and_projection(
    chat_env, admin, api, workflow
):
    scope, context, channel, _, _, sender, _, _ = chat_env
    grant = _grant(admin, api, scope, context)
    cycle = _cycle(scope, grant, admin)
    assert _open(workflow, cycle) == "opened"
    assert (
        workflow.execute(
            "SELECT control.close_weekly_cycle(%s,%s,%s,%s,'completed','CYCLE_REPORTED')",
            (scope.tenant_id, scope.site_id, cycle.week_start, cycle.cycle_id),
        ).fetchone()[0]
        == "closed"
    )
    identifier, projection = admin.execute(
        "SELECT id,projection FROM app.chat_report_outbox WHERE event_id=%s AND channel=%s",
        (cycle.cycle_id, channel),
    ).fetchone()
    shared = admin.execute(
        "SELECT control.weekly_report_projection(%s,%s,%s)",
        (scope.tenant_id, scope.site_id, cycle.week_start),
    ).fetchone()[0]
    assert {key: value for key, value in projection.items() if key != "measurements"} == shared
    assert "measurements" in projection
    assert sender.inspect(identifier)["category"] == "weekly_report"
    admin.execute(
        "INSERT INTO app.site_weekly_control(tenant_id,site_id,paused) VALUES(%s,%s,true)",
        (scope.tenant_id, scope.site_id),
    )
    assert admin.execute(
        "SELECT count(*) FROM app.chat_report_outbox WHERE site_id=%s AND category='pause'",
        (scope.site_id,),
    ).fetchone() == (1,)
    admin.execute(
        "UPDATE app.site_weekly_control SET paused=true WHERE site_id=%s", (scope.site_id,)
    )
    assert admin.execute(
        "SELECT count(*) FROM app.chat_report_outbox WHERE site_id=%s AND category='pause'",
        (scope.site_id,),
    ).fetchone() == (1,)


def test_chat_report_queue_rejection_cannot_rollback_pause(chat_env, admin):
    scope = chat_env[0]
    admin.execute(
        "SELECT control.queue_chat_report_event(%s,%s,%s,'pause',%s)",
        (scope.tenant_id, scope.site_id, uuid4(), Jsonb({"text": "x" * 131073})),
    )
    assert admin.execute(
        "SELECT error_class FROM control.chat_report_queue_failures WHERE site_id=%s",
        (scope.site_id,),
    ).fetchone() == ("bounded_projection_rejected",)


def test_chat_report_concurrent_claims_and_slack_shared_daily_cap(chat_env, admin):
    scope, context, _, _, _, sender, prefs, _ = chat_env
    digest = hashlib.sha256(b"synthetic-concurrent-report").digest()
    first = queue(admin, chat_env)

    def claim(identifier):
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            return ChatReportSender(connection, context["generation"])._one(
                "chat_report_item", identifier, context["generation"], digest
            )["state"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(claim, [first, first]))
    assert sorted(states) == ["claimed", "dispatching"]
    assert sender._one("finish_chat_report", first, digest, "accepted") == "accepted"
    assert claim(first) == "accepted"
    assert (
        prefs.preference(context["session_token"], scope.site_id, channel="slack_dm", enabled=True)
        == "enabled"
    )
    admin.execute("UPDATE control.chat_report_configuration SET daily_cap=1 WHERE provider='slack'")
    event = uuid4()
    queue(admin, chat_env, event=event)
    identifiers = [
        row[0]
        for row in admin.execute(
            "SELECT id FROM app.chat_report_outbox WHERE event_id=%s", (event,)
        ).fetchall()
    ]
    assert len(identifiers) == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(claim, identifiers)) == ["suppressed", "suppressed"]
    assert admin.execute(
        "SELECT count(*) FROM app.chat_report_receipts WHERE site_id=%s AND phase='dispatch'",
        (scope.site_id,),
    ).fetchone() == (1,)


@pytest.mark.parametrize("chat_env", ["slack_dm", "telegram"], indirect=True)
def test_chat_report_unpaired_destinations_and_groups_cannot_enable(chat_env, admin):
    scope, context, channel, _, link, _, prefs, _ = chat_env
    provider = "telegram" if channel == "telegram" else "slack"
    admin.execute(f"UPDATE app.{provider}_links SET revoked_at=now() WHERE id=%s", (link,))
    assert (
        prefs.preference(context["session_token"], scope.site_id, channel=channel, enabled=True)
        == "destination_unavailable"
    )
    if provider == "telegram":
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute("UPDATE app.telegram_links SET chat_id='-9000133' WHERE id=%s", (link,))


@pytest.mark.anyio
async def test_chat_report_revalidates_opt_out_after_secret_read(
    chat_env, admin, api, scheduler, workflow, crawl_admission, crawl_ingest, tmp_path
):
    scope, context, channel, _, _, sender, prefs, _ = chat_env
    identifier = queue(admin, chat_env)
    provider, double = slack_egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )

    class RevokingSecrets:
        async def bot(self, *args, **kwargs):
            prefs.preference(
                context["session_token"], scope.site_id, channel=channel, enabled=False
            )
            return SLACK_BOT

    assert (
        await replace(sender, slack_secrets=RevokingSecrets()).deliver(identifier, egress=provider)
        == "suppressed"
    )
    assert not double.calls
