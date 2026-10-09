import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx2
import psycopg
import pytest
import rfc8785
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.shared_egress import SharedEgressProvider
from signal_core.slack_binding import SlackService
from signal_core.slack_protocol import SlackRejected
from signal_core.slack_secrets import OpenBaoSlackSecrets

from tests.control_plane.test_gsc_binding import owner_and_verified_origin
from tests.control_plane.test_shared_egress import authority

WORKSPACE = "T00000001"
CHANNEL = "C00000001"
USER = "U00000001"
DM = "D00000001"
SIGNING = "synthetic-slack-signing-secret-0091"
BOT = "synthetic-slack-bot-token-0091"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class BaoDouble:
    def __init__(self):
        self.documents = {
            "client": {
                "client_id": "1234567890.1234567890",
                "client_secret": "synthetic-slack-client-secret-0091",
                "signing_secret": SIGNING,
            }
        }

    def request(self, request):
        key = request.url.path.split("/data/")[-1]
        if request.method == "DELETE":
            self.documents.pop("bots/" + request.url.path.rsplit("/", 1)[-1], None)
            return httpx2.Response(204)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["options"] == {"cas": 0} and key not in self.documents
            self.documents[key] = payload["data"]
            return httpx2.Response(200, json={"data": {"version": 1}})
        if key not in self.documents:
            return httpx2.Response(404, json={"errors": ["synthetic unavailable"]})
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": self.documents[key],
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )


class SlackDouble:
    def __init__(self):
        self.calls = []
        self.scope = "chat:write"
        self.workspace = WORKSPACE
        self.failure = False

    def request(self, outbound, *, policy):
        self.calls.append(outbound)
        if self.failure:
            return EgressHttpResult(
                1,
                outbound.url,
                outbound.url,
                "POST",
                "timeout",
                None,
                None,
                (),
                "8.8.8.8",
                b"",
                None,
                0,
                0,
            )
        if outbound.url.endswith("oauth.v2.access"):
            assert dict(outbound.headers)["content-type"] == "application/x-www-form-urlencoded"
            data = {key: values[0] for key, values in parse_qs(outbound.body.decode()).items()}
            assert "authorization" not in dict(outbound.headers)
            assert data["client_secret"].startswith("synthetic-")
            response = {
                "ok": True,
                "scope": self.scope,
                "token_type": "bot",
                "team": {"id": self.workspace},
                "is_enterprise_install": False,
                "enterprise": None,
                "authed_user": {"id": USER},
                "access_token": BOT,
            }
        elif outbound.url.endswith("chat.postMessage"):
            assert dict(outbound.headers)["content-type"] == "application/json"
            data = json.loads(outbound.body)
            assert dict(outbound.headers)["authorization"] == "Bearer " + BOT
            response = {
                "ok": True,
                "channel": DM if data["channel"] == USER else data["channel"],
                "ts": f"1700000000.{len(self.calls):06d}",
            }
        else:
            assert outbound.url.endswith("auth.revoke")
            assert dict(outbound.headers)["content-type"] == "application/json"
            assert dict(outbound.headers)["authorization"] == "Bearer " + BOT
            assert json.loads(outbound.body) == {}
            response = {"ok": True, "revoked": True}
        body = json.dumps(response).encode()
        return EgressHttpResult(
            1,
            outbound.url,
            outbound.url,
            "POST",
            "fetched",
            200,
            "application/json",
            (("content-type", "application/json"),),
            "8.8.8.8",
            body,
            hashlib.sha256(body).hexdigest(),
            len(body),
            0,
        )


def service(identity, context):
    bao = BaoDouble()
    return SlackService(
        identity,
        OpenBaoSlackSecrets("https://bao.example.invalid", "synthetic-bao-token-0091"),
        "https://dashboard.example.invalid",
        context["generation"],
        {"transport": httpx2.MockTransport(bao.request)},
    ), bao


def egress(api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path):
    store = EncryptedLocalArtifactStore(tmp_path / uuid4().hex)
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "slack-" + uuid4().hex,
        store,
        origin_override="https://slack.com",
        github_profile=True,
    )
    double = SlackDouble()
    return SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        double,
        "slack.test",
        OriginAdmissionPolicy(),
        None,
        "connector",
    ), double


async def install(svc, egress, context, site_id):
    time.sleep(1.1)  # The shared origin limiter also spans separate synthetic installations.
    begun = await svc.begin_install(
        session_token=context["session_token"],
        site_id=site_id,
        workspace_id=WORKSPACE,
        channel_id=CHANNEL,
    )
    query = parse_qs(urlsplit(begun["authorization_url"]).query)
    assert query["scope"] == ["chat:write"]
    binding = await svc.complete_install(
        session_token=context["session_token"],
        site_id=site_id,
        attempt_id=begun["attempt_id"],
        state=query["state"][0],
        code="synthetic-slack-oauth-code",
        egress=egress,
    )
    return binding


def signed_action(
    callback,
    action,
    ts,
    *,
    workspace=WORKSPACE,
    channel=DM,
    user=USER,
    now=None,
    text="untrusted data",
):
    body = urlencode(
        {
            "payload": json.dumps(
                {
                    "type": "block_actions",
                    "team": {"id": workspace},
                    "channel": {"id": channel},
                    "user": {"id": user},
                    "message": {"ts": ts, "text": text},
                    "response_url": "https://private.invalid/never-follow",
                    "actions": [{"action_id": action, "value": callback}],
                }
            )
        }
    ).encode()
    timestamp = str(int(time.time()) if now is None else now)
    signature = (
        "v0="
        + hmac.new(
            SIGNING.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    return {"body": body, "timestamp": timestamp, "signature": signature}


async def deliver(svc, provider, binding, outbox):
    for _ in range(10):
        outcome = await svc.deliver(binding_id=binding, outbox_id=outbox, egress=provider)
        if outcome != "queued":
            assert outcome == "accepted"
            return
        time.sleep(0.3)
    pytest.fail("Synthetic Slack outbox was not admitted.")


def message(admin, outbox):
    return admin.execute(
        "SELECT payload,channel_id,message_ts FROM app.slack_outbox WHERE id=%s", (outbox,)
    ).fetchone()


async def link(svc, provider, admin, context, site, binding):
    outbox = svc.begin_link(
        session_token=context["session_token"], site_id=site, binding_id=binding, slack_user_id=USER
    )
    await deliver(svc, provider, binding, outbox)
    payload, channel, ts = message(admin, outbox)
    callback = payload["blocks"][0]["elements"][0]["value"]
    signed = signed_action(callback, "signal_link", ts, channel=channel)
    assert (await svc.interact(binding_id=binding, **signed))["outcome"] == "linked"
    return callback, ts, signed


@pytest.mark.anyio
async def test_install_link_replay_revoke_and_secret_isolation(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    svc, bao = service(identity, identity_context)
    provider, double = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    binding = await install(svc, provider, identity_context, scope.site_id)
    callback, ts, signed = await link(
        svc, provider, admin, identity_context, scope.site_id, binding
    )
    assert (await svc.interact(binding_id=binding, **signed))["outcome"] == "replay"
    assert (
        await svc.interact(
            binding_id=binding, **signed_action(callback, "signal_link", ts, text="new delivery")
        )
    )["outcome"] == "unlinked"
    link_id = admin.execute(
        "SELECT id FROM app.slack_links WHERE binding_id=%s", (binding,)
    ).fetchone()[0]
    result = await svc.revoke(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        link_id=link_id,
    )
    assert result["outcome"] == "AUTHORITY_DURABILITY_PENDING"
    assert admin.execute(
        "SELECT target_kind FROM control.authority_restriction_outbox WHERE event_id=%s",
        (result["restriction_event_id"],),
    ).fetchone() == ("slack_link",)
    result = await svc.revoke(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        egress=provider,
    )
    assert result["outcome"] == "AUTHORITY_DURABILITY_PENDING"
    assert "bots/" + str(binding) not in bao.documents
    for table in (
        "slack_bindings",
        "slack_outbox",
        "slack_ingress_audit",
        "slack_links",
        "slack_link_codes",
    ):
        rows = admin.execute(f"SELECT to_jsonb(t)::text FROM app.{table} t").fetchall()
        assert all(
            BOT not in row[0]
            and SIGNING not in row[0]
            and "synthetic-slack-client-secret" not in row[0]
            for row in rows
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            identity.execute(f"DELETE FROM app.{table}")
    assert any(call.url.endswith("oauth.v2.access") for call in double.calls)


@pytest.mark.anyio
async def test_link_expiry_wrong_user_bad_signature_and_old_timestamp(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    svc, _ = service(identity, identity_context)
    provider, _ = egress(api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path)
    binding = await install(svc, provider, identity_context, scope.site_id)
    outbox = svc.begin_link(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        slack_user_id=USER,
    )
    await deliver(svc, provider, binding, outbox)
    payload, channel, ts = message(admin, outbox)
    callback = payload["blocks"][0]["elements"][0]["value"]
    bad = signed_action(callback, "signal_link", ts)
    with pytest.raises(SlackRejected):
        await svc.interact(binding_id=binding, **{**bad, "signature": "v0=" + "0" * 64})
    with pytest.raises(SlackRejected):
        await svc.interact(
            binding_id=binding,
            **signed_action(callback, "signal_link", ts, now=int(time.time()) - 301),
        )
    assert (
        await svc.interact(
            binding_id=binding, **signed_action(callback, "signal_link", ts, user="U00000002")
        )
    )["outcome"] != "linked"
    assert (
        await svc.interact(
            binding_id=binding, **signed_action(callback, "signal_link", ts, workspace="T00000002")
        )
    )["outcome"] == "wrong_binding"
    assert (
        await svc.interact(
            binding_id=binding, **signed_action(callback, "signal_link", ts, channel="D00000002")
        )
    )["outcome"] == "wrong_binding"
    admin.execute(
        "UPDATE app.slack_link_codes SET expires_at=now()-interval '1 second' WHERE id=%s",
        (outbox,),
    )
    assert (await svc.interact(binding_id=binding, **bad))["outcome"] == "unlinked"
    assert admin.execute(
        "SELECT count(*) FROM app.slack_links WHERE binding_id=%s", (binding,)
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_oauth_consumption_scope_expiry_and_ambiguous_outbox(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    svc, bao = service(identity, identity_context)
    provider, double = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    begun = await svc.begin_install(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        workspace_id=WORKSPACE,
        channel_id=CHANNEL,
    )
    state = parse_qs(urlsplit(begun["authorization_url"]).query)["state"][0]
    args = {
        "session_token": identity_context["session_token"],
        "site_id": scope.site_id,
        "attempt_id": begun["attempt_id"],
        "state": state,
        "code": "synthetic-slack-code",
        "egress": provider,
    }
    with pytest.raises(SlackRejected):
        await svc.complete_install(**{**args, "session_token": "synthetic-" + "x" * 33})
    with pytest.raises(SlackRejected):
        await svc.complete_install(**{**args, "state": "x" * 43})
    admin.execute(
        "UPDATE app.slack_oauth_attempts SET expires_at=now()-interval '1 second' WHERE id=%s",
        (begun["attempt_id"],),
    )
    with pytest.raises(SlackRejected):
        await svc.complete_install(**args)
    for response in ("extra_scope", "wrong_workspace"):
        time.sleep(1.1)
        begun = await svc.begin_install(
            session_token=identity_context["session_token"],
            site_id=scope.site_id,
            workspace_id=WORKSPACE,
            channel_id=CHANNEL,
        )
        args.update(
            attempt_id=begun["attempt_id"],
            state=parse_qs(urlsplit(begun["authorization_url"]).query)["state"][0],
        )
        double.scope = "chat:write,channels:history" if response == "extra_scope" else "chat:write"
        double.workspace = "T00000002" if response == "wrong_workspace" else WORKSPACE
        with pytest.raises(SlackRejected):
            await svc.complete_install(**args)
        before = len(double.calls)
        with pytest.raises(SlackRejected):
            await svc.complete_install(**args)
        assert len(double.calls) == before
    double.scope, double.workspace = "chat:write", WORKSPACE
    binding = await install(svc, provider, identity_context, scope.site_id)
    outbox = svc.begin_link(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        slack_user_id=USER,
    )
    double.failure = True
    time.sleep(1.1)
    assert await svc.deliver(binding_id=binding, outbox_id=outbox, egress=provider) == "unknown"
    before = len(double.calls)
    assert await svc.deliver(binding_id=binding, outbox_id=outbox, egress=provider) == "unknown"
    assert len(double.calls) == before
    assert len(bao.documents) == 2
    assert admin.execute(
        "SELECT in_flight_count FROM control.origin_buckets WHERE origin='https://slack.com'"
    ).fetchone() == (0,)
    # Honor the unchanged shared gateway's global failure backoff before the next case.
    remaining = admin.execute(
        "SELECT EXTRACT(EPOCH FROM next_allowed_at-now()) "
        "FROM control.origin_buckets WHERE origin='https://slack.com'"
    ).fetchone()[0]
    time.sleep(max(0, float(remaining)) + 0.1)


@pytest.mark.anyio
async def test_slack_restore_tombstones_are_restrictive_and_runtime_cannot_forge_them(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    svc, _ = service(identity, identity_context)
    provider, _ = egress(api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path)
    binding = await install(svc, provider, identity_context, scope.site_id)
    await link(svc, provider, admin, identity_context, scope.site_id, binding)
    link_id = admin.execute(
        "SELECT id FROM app.slack_links WHERE binding_id=%s", (binding,)
    ).fetchone()[0]
    for target, kind in ((link_id, "slack_link"), (binding, "slack_binding")):
        args = (uuid4(), target, 1, uuid4(), 1, "a" * 64, kind)
        query = "SELECT control.apply_slack_authority_denial(%s,%s,%s,%s,%s,%s,%s)"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            identity.execute(query, args)
        with psycopg.connect(
            os.environ["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], autocommit=True
        ) as dispatcher:
            dispatcher.execute(query, args)
            dispatcher.execute(query, args)
            with pytest.raises(psycopg.errors.UniqueViolation):
                dispatcher.execute(query, (*args[:5], "b" * 64, kind))
        row = svc._one(
            "read_slack_binding",
            hashlib.sha256(identity_context["session_token"].encode()).digest(),
            scope.site_id,
            identity_context["generation"],
        )
        assert row is None if kind == "slack_binding" else row[4] is None
        with pytest.raises(SlackRejected):
            svc.queue_approval(
                session_token=identity_context["session_token"],
                site_id=scope.site_id,
                binding_id=binding,
                revision_id=uuid4(),
                revision_sha256="a" * 64,
                channel_id=CHANNEL,
            )


async def exercise_slack_approval(
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scope,
    context,
    revision_id,
    revision_hash,
    tmp_path,
    release_manager,
):
    svc, _ = service(identity, context)
    provider, double = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    binding = await install(svc, provider, context, scope.site_id)
    await link(svc, provider, admin, context, scope.site_id, binding)
    outbox = svc.queue_approval(
        session_token=context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        revision_id=revision_id,
        revision_sha256=revision_hash,
        channel_id=CHANNEL,
    )
    await deliver(svc, provider, binding, outbox)
    payload, channel, ts = message(admin, outbox)
    callback = payload["blocks"][1]["elements"][1]["value"]
    assert str(revision_id) in payload["text"] and revision_hash in payload["text"]
    before = len(double.calls)
    assert await svc.deliver(binding_id=binding, outbox_id=outbox, egress=provider) == "accepted"
    assert len(double.calls) == before
    with pytest.raises(SlackRejected):
        svc.queue_approval(
            session_token=context["session_token"],
            site_id=scope.site_id,
            binding_id=binding,
            revision_id=revision_id,
            revision_sha256="0" * 64,
            channel_id=CHANNEL,
        )
    # Admin-seeded risk fixtures qualify callback policy, not a high-risk build pipeline.
    from tests.control_plane.test_technical_recipes import _register_release

    with admin.cursor(row_factory=dict_row) as cursor:
        source_revision = cursor.execute(
            "SELECT * FROM app.candidate_recipe_revisions WHERE id=%s", (revision_id,)
        ).fetchone()
        source_build = cursor.execute(
            "SELECT * FROM app.candidate_build_receipts WHERE build_id=%s",
            (source_revision["build_id"],),
        ).fetchone()
        source_intent = cursor.execute(
            "SELECT * FROM app.candidate_build_intents WHERE id=%s",
            (source_revision["build_id"],),
        ).fetchone()
    for risk in (3, 4):
        release, _ = _register_release(
            admin,
            release_manager,
            "technical_alt",
            reviewed=True,
            version=f"1.0.{100 + risk}",
            approval_class=f"A{risk}",
        )
        release_hash = admin.execute(
            "SELECT content_hash FROM control.recipe_releases WHERE id=%s", (release,)
        ).fetchone()[0]
        build = {**source_build, "build_id": uuid4()}
        intent = {**source_intent, "id": build["build_id"], "idempotency_key": uuid4()}
        revision = {
            **source_revision,
            "id": uuid4(),
            "build_id": build["build_id"],
            "finding_id": uuid4(),
            "recipe_release_id": release,
            "release_content_hash": release_hash,
            "idempotency_key": uuid4(),
        }
        manifest = json.loads(revision["canonical_manifest"])
        manifest.update(
            recipe_release_id=str(release),
            release_content_hash=release_hash.hex(),
            build_id=str(build["build_id"]),
            finding_id=str(revision["finding_id"]),
        )
        revision["canonical_manifest"] = rfc8785.dumps(manifest)
        del revision["revision_sha256"]
        for table, document in (
            ("candidate_build_intents", intent),
            ("candidate_build_receipts", build),
            ("candidate_recipe_revisions", revision),
        ):
            columns = list(document)
            admin.execute(
                sql.SQL("INSERT INTO app.{} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns),
                ),
                tuple(
                    Jsonb(document[key])
                    if isinstance(document[key], (list, dict))
                    else document[key]
                    for key in columns
                ),
            )
        digest = hashlib.sha256(revision["canonical_manifest"]).hexdigest()
        risk_outbox = svc.queue_approval(
            session_token=context["session_token"],
            site_id=scope.site_id,
            binding_id=binding,
            revision_id=revision["id"],
            revision_sha256=digest,
            channel_id=CHANNEL,
        )
        await deliver(svc, provider, binding, risk_outbox)
        risk_message, risk_channel, risk_ts = message(admin, risk_outbox)
        for action, index in (("signal_approve", 1), ("signal_reject", 2)):
            risk_code = risk_message["blocks"][1]["elements"][index]["value"]
            result = await svc.interact(
                binding_id=binding,
                **signed_action(risk_code, action, risk_ts, channel=risk_channel),
            )
            assert result["outcome"] == "step_up" and "/approvals" in result["text"]
        dashboard = identity.execute(
            "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
            "%s,%s,%s,%s,%s,%s,%s)",
            (
                hashlib.sha256(context["session_token"].encode()).digest(),
                scope.site_id,
                context["generation"],
                revision["id"],
                bytes.fromhex(digest),
                uuid4(),
                "approved",
            ),
        ).fetchone()
        assert dashboard[15] == "approval_permission_denied"
        # The dashboard cannot act with either primary authentication or stale MFA.
        session_times = admin.execute(
            "SELECT auth_time FROM control.identity_sessions WHERE id=%s",
            (context["identity_session_id"],),
        ).fetchone()[0]
        for fresh in (False, True):
            auth_time = (
                session_times
                if fresh
                else admin.execute("SELECT now()-interval '6 minutes'").fetchone()[0]
            )
            admin.execute(
                "UPDATE control.identity_sessions SET authentication_level='mfa',auth_time=%s "
                "WHERE id=%s",
                (auth_time, context["identity_session_id"]),
            )
            admin.execute(
                "UPDATE app.sessions SET mfa_level='mfa',auth_time=%s WHERE id=%s",
                (auth_time, context["tenant_session_id"]),
            )
            decided = identity.execute(
                "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
                "%s,%s,%s,%s,%s,%s,%s)",
                (
                    hashlib.sha256(context["session_token"].encode()).digest(),
                    scope.site_id,
                    context["generation"],
                    revision["id"],
                    bytes.fromhex(digest),
                    uuid4(),
                    "approved",
                ),
            ).fetchone()
            assert decided[15] == ("decided" if fresh else "approval_permission_denied")
            if fresh:
                assert decided[12] == "dashboard"
        admin.execute(
            "UPDATE control.identity_sessions SET authentication_level='primary',auth_time=%s "
            "WHERE id=%s",
            (session_times, context["identity_session_id"]),
        )
        admin.execute(
            "UPDATE app.sessions SET mfa_level='primary',auth_time=%s WHERE id=%s",
            (session_times, context["tenant_session_id"]),
        )
    extension = admin.execute(
        "SELECT extension_id FROM app.candidate_recipe_revisions WHERE id=%s", (revision_id,)
    ).fetchone()[0]
    old_base = admin.execute(
        "SELECT base_sha FROM app.github_pr_extensions WHERE id=%s", (extension,)
    ).fetchone()[0]
    admin.execute(
        "UPDATE app.github_pr_extensions SET base_sha=%s WHERE id=%s", ("e" * 40, extension)
    )
    assert (
        await svc.interact(
            binding_id=binding,
            **signed_action(callback, "signal_approve", ts, channel=channel, text="stale base"),
        )
    )["outcome"] == "stale_revision"
    admin.execute(
        "UPDATE app.github_pr_extensions SET base_sha=%s WHERE id=%s", (old_base, extension)
    )
    for user in ("U00000002",):
        assert (
            await svc.interact(
                binding_id=binding,
                **signed_action(callback, "signal_approve", ts, channel=channel, user=user),
            )
        )["outcome"] == "unlinked"
    for table, column, value, restore in (
        ("app.memberships", "state", "removed", "active"),
        ("app.memberships", "role_key", "analyst", "owner"),
        ("app.site_memberships", "state", "removed", "active"),
    ):
        admin.execute(
            f"UPDATE {table} SET {column}=%s WHERE user_id=%s", (value, context["user_id"])
        )
        assert (
            await svc.interact(
                binding_id=binding,
                **signed_action(
                    callback, "signal_approve", ts, channel=channel, text=table + column
                ),
            )
        )["outcome"] == "authority_denied"
        admin.execute(
            f"UPDATE {table} SET {column}=%s WHERE user_id=%s", (restore, context["user_id"])
        )
    membership_epoch = admin.execute(
        "SELECT authorization_epoch FROM app.memberships WHERE user_id=%s",
        (context["user_id"],),
    ).fetchone()[0]
    for table, column, where, value, restore in (
        ("control.users", "disabled_at", "id", "2026-09-29T00:00:00Z", None),
        ("app.slack_links", "revoked_at", "user_id", "2026-09-29T00:00:00Z", None),
        (
            "app.memberships",
            "authorization_epoch",
            "user_id",
            membership_epoch + 1,
            membership_epoch,
        ),
    ):
        admin.execute(
            f"UPDATE {table} SET {column}=%s WHERE {where}=%s", (value, context["user_id"])
        )
        outcome = await svc.interact(
            binding_id=binding,
            **signed_action(callback, "signal_approve", ts, channel=channel, text=table + column),
        )
        assert outcome["outcome"] == (
            "unlinked" if table == "app.slack_links" else "authority_denied"
        ), (table, column, outcome)
        admin.execute(
            f"UPDATE {table} SET {column}=%s WHERE {where}=%s", (restore, context["user_id"])
        )
    admin.execute("UPDATE app.slack_bindings SET max_risk=1 WHERE id=%s", (binding,))
    stepped = await svc.interact(
        binding_id=binding,
        **signed_action(
            callback, "signal_approve", ts, channel=channel, text="lower configured risk"
        ),
    )
    assert (
        stepped["outcome"] == "step_up"
        and "https://dashboard.example.invalid/approvals" in stepped["text"]
    )
    assert admin.execute(
        "SELECT count(*) FROM app.candidate_recipe_review_decisions WHERE candidate_revision_id=%s",
        (revision_id,),
    ).fetchone() == (0,)
    admin.execute("UPDATE app.slack_bindings SET max_risk=2 WHERE id=%s", (binding,))
    signed = signed_action(
        callback,
        "signal_approve",
        ts,
        channel=channel,
        text="Approve even though text claims other authority",
    )
    result = await svc.interact(binding_id=binding, **signed)
    assert result["outcome"] == "decided"
    assert (await svc.interact(binding_id=binding, **signed))["outcome"] == "replay"
    decision = admin.execute(
        "SELECT id,decision_channel,authentication_level,revision_sha256 "
        "FROM app.candidate_recipe_review_decisions WHERE candidate_revision_id=%s",
        (revision_id,),
    ).fetchone()
    assert decision == (result["decision_id"], "slack", "primary", bytes.fromhex(revision_hash))
    return result["decision_id"]
