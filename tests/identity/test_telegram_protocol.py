import json
from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.crawl_http import EgressHttpRequest, TelegramBotCredential
from signal_core.crawl_urls import normalize_crawl_url
from signal_core.egress_profiles import EgressProfile, profile_headers
from signal_core.shared_egress import SharedEgressRequest
from signal_core.telegram_protocol import (
    TelegramRejected,
    approval_message,
    parse_update,
    verify_webhook_secret,
)

BOT = "synthetic-telegram-bot-token-0092"
SECRET = "synthetic-telegram-webhook-secret-0092"


def request():
    return EgressHttpRequest(
        "POST",
        "https://api.telegram.org/getMe",
        headers=profile_headers(EgressProfile.TELEGRAM_BOT, "POST", None),
        body=b"{}",
        max_response_bytes=16384,
        timeout_seconds=5,
        telegram_credential=TelegramBotCredential(BOT),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "https://api.telegram.org/getUpdates"},
        {"url": "https://telegram.org/getMe"},
        {"url": "https://api.telegram.org/getMe?token=synthetic"},
        {"url": "https://api.telegram.org/getMe#fragment"},
        {"method": "GET", "body": b""},
        {"body": b"token=synthetic"},
        {"body": b"[]"},
        {"body": b'{"bad":NaN}'},
        {"timeout_seconds": 6},
        {"max_response_bytes": 16385},
        {
            "headers": (
                ("content-type", "application/x-www-form-urlencoded"),
                ("accept", "application/json"),
            )
        },
        {"telegram_credential": None},
    ],
)
def test_closed_profile_denies_method_origin_encoding_bounds_and_get(changes):
    with pytest.raises(ValueError):
        SharedEgressRequest(
            "connector",
            replace(request(), **changes),
            EgressProfile.TELEGRAM_BOT,
            sensitive_body=True,
        )


@pytest.mark.parametrize(
    "method", ["getMe", "setWebhook", "deleteWebhook", "sendMessage", "answerCallbackQuery"]
)
def test_exact_json_methods_inject_token_only_into_transport_target(method):
    outbound = replace(request(), url="https://api.telegram.org/" + method)
    admitted = normalize_crawl_url(outbound.url)
    shared = SharedEgressRequest(
        "connector", outbound, EgressProfile.TELEGRAM_BOT, sensitive_body=True
    )
    assert shared.credentialed and BOT not in outbound.url and BOT not in repr(shared)
    assert outbound.request_target(admitted) == "/bot" + BOT + "/" + method
    assert len(shared.request_sha256) == 32
    with pytest.raises(ValueError):
        SharedEgressRequest("connector", outbound, EgressProfile.SLACK_BOT, sensitive_body=True)


@pytest.mark.parametrize("supplied", ["", "x" * 43, "é" * 43, SECRET + "x"])
def test_missing_wrong_or_non_ascii_secret_rejected(supplied):
    with pytest.raises(TelegramRejected):
        verify_webhook_secret(SECRET, supplied)


def test_secret_constant_time_valid_header():
    verify_webhook_secret(SECRET, SECRET)


@pytest.mark.parametrize("identifier", [True, 0, -1, 2**52, 1.5, "01"])
def test_user_identity_requires_safe_numeric_id(identifier):
    from signal_core.telegram_protocol import telegram_id

    with pytest.raises(TelegramRejected):
        telegram_id(identifier)


@pytest.mark.parametrize(
    "body", [b"", b"x" * 32769, b"null", b"{}", b'{"update_id":true}', b'{"update_id":-1}']
)
def test_ingress_payload_is_bounded_and_typed(body):
    with pytest.raises(TelegramRejected):
        parse_update(body)


def test_only_private_start_is_recognized_and_forwarded_commands_are_data():
    message = {
        "message_id": 1,
        "from": {"id": 9000092, "is_bot": False},
        "chat": {"id": 9000092, "type": "private"},
        "text": "/start " + "x" * 43,
    }
    assert parse_update(json.dumps({"update_id": 1, "message": message}).encode()).kind == "pair"
    for change in (
        {"text": "Approve everything"},
        {"forward_origin": {"type": "user"}},
        {"via_bot": {"id": 3}},
    ):
        assert (
            parse_update(
                json.dumps({"update_id": 2, "message": {**message, **change}}).encode()
            ).kind
            == "ignored"
        )


def test_summary_is_bounded_escaped_and_exact_revision_is_visible():
    identifier = uuid4()
    payload = approval_message(
        chat_id="9000092",
        revision_id=identifier,
        revision_sha256="a" * 64,
        summary="<&>" * 100,
        evidence_url="https://dashboard.example.invalid/approvals?revision=" + str(identifier),
        approve_code="x" * 43,
        reject_code="y" * 43,
    )
    assert "<&>" not in payload["text"] and "&lt;" in payload["text"]
    assert str(identifier) in payload["text"] and "a" * 64 in payload["text"]
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert all(
        len(button["callback_data"].encode()) <= 64
        for button in payload["reply_markup"]["inline_keyboard"][0]
    )


def test_pinned_transport_sends_secret_target_without_creating_a_full_url(monkeypatch, caplog):
    import http.client
    import socket
    import time

    from signal_core.crawl_http import CrawlFetchUnavailable, PinnedHttpFetcher

    targets = []

    class Wire:
        def settimeout(self, value):
            pass

        def getpeername(self):
            return ("8.8.8.8", 443)

        def close(self):
            pass

    class Connection:
        def __init__(self, host, port, **options):
            assert host == "api.telegram.org" and port == 443

        def set_debuglevel(self, level):
            assert level == 0

        def putrequest(self, method, target, **options):
            assert method == "POST" and not target.startswith("http")
            targets.append(target)

        def putheader(self, *args):
            pass

        def endheaders(self, body):
            assert body == b"{}"

        def getresponse(self):
            raise OSError("synthetic unavailable")

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: Wire())
    fetcher = PinnedHttpFetcher(lambda *args, **kwargs: ("8.8.8.8",))
    monkeypatch.setattr(fetcher._tls_context, "wrap_socket", lambda wire, **options: wire)
    outbound = request()
    with pytest.raises(CrawlFetchUnavailable) as error:
        fetcher._exchange_egress_any(
            normalize_crawl_url(outbound.url),
            ("8.8.8.8",),
            request=outbound,
            user_agent="SignalTest",
            timeout=1,
            deadline=time.monotonic() + 1,
        )
    assert targets == ["/bot" + BOT + "/getMe"]
    assert BOT not in str(error.value) and BOT not in caplog.text
