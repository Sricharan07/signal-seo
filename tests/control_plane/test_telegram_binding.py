import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import psycopg
import pytest
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.shared_egress import SharedEgressProvider
from signal_core.telegram_binding import TelegramService
from signal_core.telegram_protocol import TelegramRejected
from signal_core.telegram_secrets import OpenBaoTelegramSecrets

from tests.control_plane.test_gsc_binding import owner_and_verified_origin
from tests.control_plane.test_shared_egress import authority
from tests.control_plane.test_slack_binding import BaoDouble

BOT = "synthetic-telegram-bot-token-0092"
USER = "9000092"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TelegramDouble:
    def __init__(self):
        self.calls = []
        self.bot_id = str(int(uuid4().hex[:10], 16))
        self.can_join_groups = False
        self.failure = False
        self.throw = False
        self.webhook_failure = False

    def request(self, outbound, *, policy):
        self.calls.append(outbound)
        assert outbound.telegram_credential.token == BOT
        assert BOT not in outbound.url and BOT not in repr(outbound)
        method = urlsplit(outbound.url).path[1:]
        data = json.loads(outbound.body)
        if self.throw:
            raise RuntimeError("https://api.telegram.org/bot" + BOT + "/" + method)
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
        if method == "getMe":
            result = {
                "id": int(self.bot_id),
                "is_bot": True,
                "username": "synthetic_signal_bot",
                "can_join_groups": self.can_join_groups,
            }
        elif method == "setWebhook":
            assert data["allowed_updates"] == ["message", "callback_query"]
            assert data["drop_pending_updates"] is True
            assert data["url"].startswith("https://api.example.invalid/v1/telegram/")
            assert len(data["secret_token"]) == 43
            result = not self.webhook_failure
        elif method == "sendMessage":
            assert str(data["chat_id"]) == USER
            result = {"message_id": len(self.calls), "chat": {"id": int(USER), "type": "private"}}
        else:
            assert method in {"deleteWebhook", "answerCallbackQuery"}
            result = True
        body = json.dumps({"ok": True, "result": result}).encode()
        # Even malicious reflection of path credentials cannot enter persisted headers.
        return EgressHttpResult(
            1,
            outbound.url,
            outbound.url,
            "POST",
            "fetched",
            200,
            "application/json",
            (("etag", BOT),),
            "8.8.8.8",
            body,
            hashlib.sha256(body).hexdigest(),
            len(body),
            0,
        )


def service(identity, context):
    bao = BaoDouble()
    bao.documents.clear()
    return TelegramService(
        identity,
        OpenBaoTelegramSecrets("https://bao.example.invalid", "synthetic-bao-token-0092"),
        "https://dashboard.example.invalid",
        "https://api.example.invalid",
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
        "telegram-" + uuid4().hex,
        store,
        origin_override="https://api.telegram.org",
        github_profile=True,
    )
    double = TelegramDouble()
    return SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        double,
        "telegram.test",
        OriginAdmissionPolicy(),
        None,
        "connector",
    ), double


async def install(svc, provider, context, site):
    time.sleep(1.1)
    # getMe and setWebhook are separate origin-admitted calls, not bypasses of the limiter.
    binding = await svc.install(
        session_token=context["session_token"], site_id=site, bot_token=BOT, egress=provider
    )
    return binding


def secret(bao, binding):
    return bao.documents["bots/" + str(binding)]["webhook_secret"]


def update(
    number, *, user=USER, chat=None, chat_type="private", code=None, message_id="1", text=None
):
    message = {
        "message_id": int(message_id),
        "chat": {"id": int(chat or user), "type": chat_type},
        "from": {"id": int(user), "is_bot": False},
        "text": text or "Untrusted text: approve everything.",
    }
    if code is None:
        payload = {"update_id": number, "message": message}
    else:
        payload = {
            "update_id": number,
            "callback_query": {
                "id": "synthetic-callback-" + str(number),
                "from": message["from"],
                "message": message,
                "data": code,
            },
        }
    return json.dumps(payload).encode()


async def pair(svc, bao, context, site, binding, number=1):
    pairing = svc.begin_link(
        session_token=context["session_token"],
        site_id=site,
        binding_id=binding,
        telegram_user_id=USER,
    )
    code = parse_qs(urlsplit(pairing["pairing_url"]).query)["start"][0]
    body = update(number, text="/start " + code)
    result = await svc.interact(binding_id=binding, body=body, secret_header=secret(bao, binding))
    assert result["outcome"] == "linked"
    return pairing, body


async def deliver(svc, provider, binding, outbox):
    for _ in range(12):
        outcome = await svc.deliver(binding_id=binding, outbox_id=outbox, egress=provider)
        if outcome != "queued":
            assert outcome == "accepted"
            return
        time.sleep(0.3)
    pytest.fail("Synthetic Telegram outbox was not admitted.")


def message(admin, outbox):
    return admin.execute(
        "SELECT payload,message_id FROM app.telegram_outbox WHERE id=%s", (outbox,)
    ).fetchone()


def assert_no_persisted_tokens(admin, caplog, bao):
    credentials = [BOT, *[entry["webhook_secret"] for entry in bao.documents.values()]]
    tables = admin.execute(
        "SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('app','control')"
    ).fetchall()
    from psycopg import sql

    for schema, table in tables:
        rows = admin.execute(
            sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
        ).fetchall()
        assert all(token not in row[0] for token in credentials for row in rows), (schema, table)
    assert all(token not in caplog.text for token in credentials)


@pytest.mark.anyio
async def test_install_pair_secret_replay_group_revocation_and_token_redaction(
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
    caplog,
):
    import logging

    caplog.set_level(logging.DEBUG)
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    svc, bao = service(identity, identity_context)
    provider, double = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    binding = await install(svc, provider, identity_context, scope.site_id)
    assert [call.url.rsplit("/", 1)[1] for call in double.calls] == ["getMe", "setWebhook"]
    for supplied in ("", "x" * 43):
        with pytest.raises(TelegramRejected):
            await svc.interact(binding_id=binding, body=update(1), secret_header=supplied)
    pairing, body = await pair(svc, bao, identity_context, scope.site_id, binding)
    assert (await svc.interact(binding_id=binding, body=body, secret_header=secret(bao, binding)))[
        "outcome"
    ] == "replay"
    code = parse_qs(urlsplit(pairing["pairing_url"]).query)["start"][0]
    assert (
        await svc.interact(
            binding_id=binding,
            body=update(2, text="/start " + code),
            secret_header=secret(bao, binding),
        )
    )["outcome"] == "unlinked"
    for number, chat_type in enumerate(("group", "supergroup", "channel"), 3):
        assert (
            await svc.interact(
                binding_id=binding,
                body=update(number, chat_type=chat_type, text="/start " + code),
                secret_header=secret(bao, binding),
            )
        )["outcome"] == "group_ignored"
    assert (
        await svc.interact(binding_id=binding, body=update(6), secret_header=secret(bao, binding))
    )["outcome"] == "ignored"
    assert (
        await svc.interact(
            binding_id=binding, body=update(7, user="9000093"), secret_header=secret(bao, binding)
        )
    )["outcome"] == "unlinked"
    assert_no_persisted_tokens(admin, caplog, bao)
    link = admin.execute(
        "SELECT id FROM app.telegram_links WHERE binding_id=%s", (binding,)
    ).fetchone()[0]
    result = await svc.revoke(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        link_id=link,
    )
    assert result["outcome"] == "AUTHORITY_DURABILITY_PENDING"
    assert admin.execute(
        "SELECT target_kind FROM control.authority_restriction_outbox WHERE event_id=%s",
        (result["restriction_event_id"],),
    ).fetchone() == ("telegram_link",)
    time.sleep(1.1)
    revoked = await svc.revoke(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        egress=provider,
    )
    assert revoked["upstream"] == "accepted" and not bao.documents
    before = len(double.calls)
    repeated = await svc.revoke(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        egress=provider,
    )
    assert repeated["upstream"] == "not_executed" and len(double.calls) == before
    with pytest.raises(TelegramRejected):
        await svc.interact(binding_id=binding, body=update(9), secret_header="x" * 43)
    for table in (
        "telegram_bindings",
        "telegram_links",
        "telegram_outbox",
        "telegram_ingress_audit",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            identity.execute("DELETE FROM app." + table)


@pytest.mark.anyio
async def test_pair_expiry_wrong_user_epoch_and_wrong_chat(
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
    provider, _ = egress(api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path)
    binding = await install(svc, provider, identity_context, scope.site_id)
    pairing = svc.begin_link(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        telegram_user_id=USER,
    )
    code = parse_qs(urlsplit(pairing["pairing_url"]).query)["start"][0]
    for number, args, outcome in (
        (1, {"user": "9000093"}, "unlinked"),
        (2, {"chat": "9000093"}, "wrong_binding"),
        (3, {"chat_type": "group"}, "group_ignored"),
    ):
        result = await svc.interact(
            binding_id=binding,
            body=update(number, text="/start " + code, **args),
            secret_header=secret(bao, binding),
        )
        assert result["outcome"] == outcome
    admin.execute(
        "UPDATE app.telegram_link_codes SET expires_at=now()-interval '1 second' WHERE id=%s",
        (pairing["pairing_id"],),
    )
    assert (
        await svc.interact(
            binding_id=binding,
            body=update(4, text="/start " + code),
            secret_header=secret(bao, binding),
        )
    )["outcome"] == "unlinked"
    pairing = svc.begin_link(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        telegram_user_id=USER,
    )
    code = parse_qs(urlsplit(pairing["pairing_url"]).query)["start"][0]
    admin.execute(
        "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE user_id=%s",
        (identity_context["user_id"],),
    )
    assert (
        await svc.interact(
            binding_id=binding,
            body=update(5, text="/start " + code),
            secret_header=secret(bao, binding),
        )
    )["outcome"] == "authority_denied"
    assert admin.execute(
        "SELECT count(*) FROM app.telegram_links WHERE binding_id=%s", (binding,)
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_install_requires_current_verified_owner_and_exact_site(
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
    svc, bao = service(identity, identity_context)
    provider, double = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    with pytest.raises(TelegramRejected):
        await svc.install(
            session_token=identity_context["session_token"],
            site_id=scope.site_id,
            bot_token=BOT,
            egress=provider,
        )
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    for token, site in (
        ("synthetic-" + "x" * 33, scope.site_id),
        (identity_context["session_token"], scopes[1].site_id),
    ):
        with pytest.raises(TelegramRejected):
            await svc.install(session_token=token, site_id=site, bot_token=BOT, egress=provider)
    assert not bao.documents and not double.calls


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["group_joining", "webhook", "provider"])
async def test_failed_install_is_visible_and_never_activates(
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
    failure,
    caplog,
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    svc, bao = service(identity, identity_context)
    provider, double = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    double.can_join_groups = failure == "group_joining"
    double.webhook_failure = failure == "webhook"
    double.throw = failure == "provider"
    with pytest.raises(TelegramRejected) as error:
        await install(svc, provider, identity_context, scope.site_id)
    assert str(error.value) == "TELEGRAM_INSTALL_FAILED" and BOT not in str(error.value)
    row = svc._one(
        "read_telegram_binding",
        hashlib.sha256(identity_context["session_token"].encode()).digest(),
        scope.site_id,
        identity_context["generation"],
    )
    assert row[5] == "unavailable"
    assert_no_persisted_tokens(admin, caplog, bao)


def risk_revision(admin, release_manager, source_id, key, risk, version):
    import rfc8785
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    from tests.control_plane.test_technical_recipes import _register_release

    release, _ = _register_release(
        admin, release_manager, key, reviewed=True, version=version, approval_class=risk
    )
    release_hash = admin.execute(
        "SELECT content_hash FROM control.recipe_releases WHERE id=%s", (release,)
    ).fetchone()[0]
    with admin.cursor(row_factory=dict_row) as cursor:
        revision = cursor.execute(
            "SELECT * FROM app.candidate_recipe_revisions WHERE id=%s", (source_id,)
        ).fetchone()
        build = cursor.execute(
            "SELECT * FROM app.candidate_build_receipts WHERE build_id=%s", (revision["build_id"],)
        ).fetchone()
        intent = cursor.execute(
            "SELECT * FROM app.candidate_build_intents WHERE id=%s", (revision["build_id"],)
        ).fetchone()
    build["build_id"] = uuid4()
    intent.update(id=build["build_id"], idempotency_key=uuid4())
    revision.update(
        id=uuid4(),
        build_id=build["build_id"],
        finding_id=uuid4(),
        recipe_release_id=release,
        release_content_hash=release_hash,
        idempotency_key=uuid4(),
    )
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
                Jsonb(document[key]) if isinstance(document[key], (dict, list)) else document[key]
                for key in columns
            ),
        )
    return revision["id"], hashlib.sha256(revision["canonical_manifest"]).hexdigest()


async def exercise_telegram_approval(
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
    svc, bao = service(identity, context)
    provider, double = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path
    )
    binding = await install(svc, provider, context, scope.site_id)
    await pair(svc, bao, context, scope.site_id, binding)
    number = 10

    async def action(code, message_id, **args):
        nonlocal number
        number += 1
        return await svc.interact(
            binding_id=binding,
            body=update(number, code=code, message_id=message_id, **args),
            secret_header=secret(bao, binding),
        )

    args = {
        "session_token": context["session_token"],
        "site_id": scope.site_id,
        "binding_id": binding,
    }
    outbox = svc.queue_approval(**args, revision_id=revision_id, revision_sha256=revision_hash)
    assert (
        svc.queue_approval(**args, revision_id=revision_id, revision_sha256=revision_hash) == outbox
    )
    await deliver(svc, provider, binding, outbox)
    payload, message_id = message(admin, outbox)
    code = payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert (
        str(revision_id) in payload["text"]
        and revision_hash in payload["text"]
        and len(code.encode()) <= 64
    )
    before = len(double.calls)
    assert await svc.deliver(binding_id=binding, outbox_id=outbox, egress=provider) == "accepted"
    assert len(double.calls) == before
    with pytest.raises(TelegramRejected):
        svc.queue_approval(**args, revision_id=revision_id, revision_sha256="0" * 64)
    # Signed admin-seeded candidates qualify ingress policy, not new build capabilities.
    for index, key, risk in (
        (1, "technical_alt", "A3"),
        (2, "technical_alt", "A4"),
        (3, "technical_canonical", "owner_review"),
        (4, "technical_alt", "unknown"),
    ):
        risk_id, digest = risk_revision(
            admin, release_manager, revision_id, key, risk, f"1.0.{200 + index}"
        )
        risk_outbox = svc.queue_approval(**args, revision_id=risk_id, revision_sha256=digest)
        await deliver(svc, provider, binding, risk_outbox)
        risk_payload, risk_message = message(admin, risk_outbox)
        for button in risk_payload["reply_markup"]["inline_keyboard"][0]:
            result = await action(button["callback_data"], risk_message)
            assert result["outcome"] == "step_up" and result["decision_id"] is None
            reply = admin.execute(
                "SELECT payload FROM app.telegram_outbox WHERE id=%s", (result["reply_outbox_id"],)
            ).fetchone()[0]
            assert reply["reply_markup"]["inline_keyboard"][0][0]["url"] == svc.evidence_link(
                risk_id
            )
        assert admin.execute(
            "SELECT count(*) FROM app.candidate_recipe_review_decisions "
            "WHERE candidate_revision_id=%s",
            (risk_id,),
        ).fetchone() == (0,)
    assert (await action(code, message_id, user="9000093"))["outcome"] == "unlinked"
    assert (await action(code, message_id, chat="9000093"))["outcome"] == "wrong_binding"
    assert (await action(code, "999999"))["outcome"] == "stale_revision"
    assert (await action("x" * 43, message_id))["outcome"] == "stale_revision"
    for table, column, where, value, restore in (
        ("app.memberships", "state", "user_id", "removed", "active"),
        ("app.memberships", "role_key", "user_id", "analyst", "owner"),
        ("app.site_memberships", "state", "user_id", "removed", "active"),
        ("control.users", "disabled_at", "id", "2026-09-29T00:00:00Z", None),
        ("app.telegram_links", "revoked_at", "user_id", "2026-09-29T00:00:00Z", None),
    ):
        admin.execute(
            f"UPDATE {table} SET {column}=%s WHERE {where}=%s", (value, context["user_id"])
        )
        assert (await action(code, message_id))["outcome"] == (
            "unlinked" if table == "app.telegram_links" else "authority_denied"
        )
        admin.execute(
            f"UPDATE {table} SET {column}=%s WHERE {where}=%s", (restore, context["user_id"])
        )
    epoch = admin.execute(
        "SELECT authorization_epoch FROM app.memberships WHERE user_id=%s", (context["user_id"],)
    ).fetchone()[0]
    admin.execute(
        "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE user_id=%s",
        (context["user_id"],),
    )
    assert (await action(code, message_id))["outcome"] == "authority_denied"
    admin.execute(
        "UPDATE app.memberships SET authorization_epoch=%s WHERE user_id=%s",
        (epoch, context["user_id"]),
    )
    extension, base = admin.execute(
        "SELECT e.id,e.base_sha FROM app.candidate_recipe_revisions r "
        "JOIN app.github_pr_extensions e ON e.id=r.extension_id WHERE r.id=%s",
        (revision_id,),
    ).fetchone()
    admin.execute(
        "UPDATE app.github_pr_extensions SET base_sha=%s WHERE id=%s", ("e" * 40, extension)
    )
    assert (await action(code, message_id))["outcome"] == "stale_revision"
    admin.execute("UPDATE app.github_pr_extensions SET base_sha=%s WHERE id=%s", (base, extension))
    admin.execute("UPDATE app.telegram_bindings SET max_risk=1 WHERE id=%s", (binding,))
    step = await action(code, message_id)
    assert step["outcome"] == "step_up"
    await deliver(svc, provider, binding, step["reply_outbox_id"])
    admin.execute("UPDATE app.telegram_bindings SET max_risk=2 WHERE id=%s", (binding,))
    # Unknown response after a real send is never blindly retried.
    ambiguity_id, ambiguity_hash = risk_revision(
        admin, release_manager, revision_id, "technical_alt", "A2", "1.0.210"
    )
    unknown = svc.queue_approval(**args, revision_id=ambiguity_id, revision_sha256=ambiguity_hash)
    double.failure = True
    time.sleep(1.1)
    assert await svc.deliver(binding_id=binding, outbox_id=unknown, egress=provider) == "unknown"
    count = len(double.calls)
    assert await svc.deliver(binding_id=binding, outbox_id=unknown, egress=provider) == "unknown"
    assert len(double.calls) == count
    double.failure = False
    # A distinct generation cannot use an old pair or message.
    from dataclasses import replace

    newer = replace(svc, recovery_generation="synthetic-new-generation")
    assert (
        await newer.interact(
            binding_id=binding,
            body=update(999, code=code, message_id=message_id),
            secret_header=secret(bao, binding),
        )
    )["outcome"] == "wrong_binding"
    result = await action(code, message_id)
    assert result["outcome"] == "decided"
    await deliver(svc, provider, binding, result["reply_outbox_id"])
    assert (
        await svc.interact(
            binding_id=binding,
            body=update(number, code=code, message_id=message_id),
            secret_header=secret(bao, binding),
        )
    )["outcome"] == "replay"
    decision = admin.execute(
        "SELECT id,decision_channel,authentication_level,revision_sha256 "
        "FROM app.candidate_recipe_review_decisions WHERE candidate_revision_id=%s",
        (revision_id,),
    ).fetchone()
    assert decision == (result["decision_id"], "telegram", "primary", bytes.fromhex(revision_hash))
    return result["decision_id"]
