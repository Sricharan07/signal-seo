"""Real SMTP/TLS protocol plus closed-profile, response, and message negatives."""

import json
import re
import ssl
import time
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import httpx2
import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile
from signal_core.email_messages import render_email
from signal_core.shared_egress import SharedEgressRequest
from signal_core.smtp_submission import (
    OpenBaoSmtpCredential,
    PinnedSmtpSubmitter,
    SmtpConfiguration,
    SmtpCredential,
    _PinnedSmtp,
)


def config(port=587, mode="starttls"):
    return SmtpConfiguration(
        "smtp.example.invalid",
        port,
        mode,
        "signal@example.invalid",
        "https://dashboard.example.invalid",
    )


@pytest.mark.parametrize("mode", ["stall_tls", "stall_implicit"])
def test_smtp_total_deadline_covers_a_stalled_tls_handshake(smtp_server, monkeypatch, mode):
    initialize = _PinnedSmtp.__init__

    def short_deadline(self, *args):
        initialize(self, *args)
        self.deadline = time.monotonic() + 0.2

    monkeypatch.setattr(_PinnedSmtp, "__init__", short_deadline)
    smtp_server.mode = mode
    submitter = PinnedSmtpSubmitter(
        resolver=lambda *_: ("127.0.0.1",), tls_context=smtp_server.tls_context
    )
    start = time.monotonic()
    assert (
        submitter.submit(
            config(smtp_server.port, "implicit" if mode == "stall_implicit" else "starttls"),
            SmtpCredential("synthetic-user", "synthetic-password"),
            "owner@example.invalid",
            message().body,
        )
        == "transient"
    )
    assert time.monotonic() - start < 2
    assert not smtp_server.messages
    assert not any(command in {b"AUTH", b"MAIL", b"DATA"} for command, _ in smtp_server.commands)


def message(**overrides):
    site = uuid4()
    values = dict(
        identifier=uuid4(),
        site_id=site,
        sender="signal@example.invalid",
        recipient="owner@example.invalid",
        origin="https://dashboard.example.invalid",
        category="weekly_report",
        projection={
            "site_id": str(site),
            "week_start": "2026-09-28",
            "stages": [],
            "delivery": [],
            "deferred": [],
            "waiting_for_owner": [],
        },
    )
    values.update(overrides)
    return render_email(**values)


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("starttls", "accepted"),
        ("implicit", "accepted"),
        ("no_starttls", "permanent"),
        ("bounce", "bounced"),
        ("transient", "transient"),
        ("drop", "unknown"),
    ],
)
def test_smtp_real_protocol_tls_and_failure_classes(smtp_server, mode, expected):
    smtp_server.mode = mode
    submitter = PinnedSmtpSubmitter(
        resolver=lambda *_: ("127.0.0.1",), tls_context=smtp_server.tls_context
    )
    result = submitter.submit(
        config(smtp_server.port, "implicit" if mode == "implicit" else "starttls"),
        SmtpCredential("synthetic-user", "synthetic-password"),
        "owner@example.invalid",
        message().body,
    )
    assert result == expected
    assert all(
        secured
        for verb, secured in smtp_server.commands
        if verb in {b"AUTH", b"MAIL", b"RCPT", b"DATA"}
    )
    if mode == "no_starttls":
        assert not smtp_server.messages
        assert not any(verb == b"AUTH" for verb, _ in smtp_server.commands)


def test_smtp_certificate_validation_is_not_optional(smtp_server):
    submitter = PinnedSmtpSubmitter(resolver=lambda *_: ("127.0.0.1",))
    assert (
        submitter.submit(
            config(smtp_server.port),
            SmtpCredential("synthetic-user", "synthetic-password"),
            "owner@example.invalid",
            message().body,
        )
        == "permanent"
    )
    assert not smtp_server.messages
    context = ssl.create_default_context()
    context.check_hostname = False
    with pytest.raises(ValueError, match="verified TLS"):
        PinnedSmtpSubmitter(tls_context=context)


@pytest.mark.parametrize("mode", ["plain", "none", "STARTTLS", None])
def test_plaintext_configuration_is_rejected(mode):
    with pytest.raises(ValueError):
        config(mode=mode)


def test_smtp_cannot_borrow_http_authority():
    with pytest.raises(ValueError, match="HTTP boundary"):
        SharedEgressRequest(
            "connector",
            EgressHttpRequest("GET", "https://example.invalid"),
            EgressProfile.SMTP_SUBMIT,
        )


@pytest.mark.parametrize(
    "addresses", [("127.0.0.1",), ("192.0.2.1",), ("2001:db8::1",), ("169.254.169.254",), ()]
)
def test_smtp_shared_public_address_screening_denies_before_connect(addresses):
    submitter = PinnedSmtpSubmitter(resolver=lambda *_: addresses)
    assert (
        submitter.submit(
            config(),
            SmtpCredential("synthetic-user", "synthetic-password"),
            "owner@example.invalid",
            message().body,
        )
        == "permanent"
    )


def test_smtp_rejects_oversized_message_before_dns():
    def forbidden(*_):
        pytest.fail("DNS must not run")

    with pytest.raises(ValueError):
        PinnedSmtpSubmitter(resolver=forbidden).submit(
            config(),
            SmtpCredential("synthetic-user", "synthetic-password"),
            "owner@example.invalid",
            b"x" * 32769,
        )


def test_report_never_renders_raw_text_secret_or_token_and_html_is_escaped():
    site = uuid4()
    secret = "synthetic-" + "k" * 33
    projection = {
        "site_id": str(site),
        "week_start": "2026-09-28",
        "stages": [],
        "delivery": [
            {
                "pr_url": "https://github.com/example/site/pull/12",
                "operation_state": "opened",
                "delivery_outcome": "inconclusive",
                "delivery_reason": secret,
            }
        ],
        "deferred": [{"reason": '<script>alert("x")</script> ' + secret}],
        "waiting_for_owner": [secret],
        "raw_customer_text": secret,
        "access_token": secret,
    }
    rendered = message(site_id=site, projection=projection)
    parsed = BytesParser(policy=policy.default).parsebytes(rendered.body)
    plain = parsed.get_body(preferencelist=("plain",)).get_content()
    html = parsed.get_body(preferencelist=("html",)).get_content()
    assert "PR #12 opened. Not live verified." in plain
    assert "Activity: https://dashboard.example.invalid/changes" in plain
    assert "/work" not in plain and "/work" not in html
    assert "1 decisions waiting" in plain and "Reason available in the dashboard" in plain
    assert "https://github.com" not in plain
    assert secret not in rendered.body.decode() and secret not in plain and secret not in html
    assert "<script>" not in html and "&lt;" not in html  # Free text is withheld, not guessed safe.
    assert re.search(r"[A-Za-z0-9_-]{43,}", rendered.body.decode()) is None
    assert "<pre>" in html and "</pre>" in html
    assert rendered == message(
        identifier=UUID_from_message(parsed), site_id=site, projection=projection
    )


def UUID_from_message(parsed):
    from uuid import UUID

    return UUID(str(parsed["Message-ID"]).split("@", 1)[0].lstrip("<"))


@pytest.mark.parametrize(
    "origin",
    [
        "http://dashboard.example.invalid",
        "https://dashboard.example.invalid/?token=synthetic",
        "https://synthetic-secret@dashboard.example.invalid",
        "https://dashboard.example.invalid/<script>",
    ],
)
def test_message_links_cannot_carry_tokens_or_injection(origin):
    with pytest.raises(ValueError):
        message(origin=origin)


@pytest.mark.parametrize("category", ["pause", "revocation", "failed_delivery", "stale_binding"])
def test_alerts_are_fixed_token_free_dashboard_handoffs(category):
    body = message(category=category, projection={"secret": "synthetic-secret"}).body
    parsed = BytesParser(policy=policy.default).parsebytes(body)
    plain = parsed.get_body(preferencelist=("plain",)).get_content()
    assert "Activity: https://dashboard.example.invalid/changes" in plain
    assert "/work" not in plain
    assert b"synthetic-secret" not in body
    assert b"/approvals" in body and b"/settings" in body


def test_configuration_digest_changes_on_every_operator_setting():
    base = config()
    for changed in (
        replace(base, port=465),
        replace(base, tls_mode="implicit"),
        replace(base, daily_cap=1),
    ):
        assert changed.sha256 != base.sha256


@pytest.mark.anyio
async def test_openbao_smtp_only_reads_exact_secret_and_redacts_failures():
    def handler(request):
        assert request.url.path == "/v1/signal-email/data/smtp/default"
        return httpx2.Response(
            200,
            json={
                "data": {
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                    "data": {"username": "synthetic-user", "password": "synthetic-password"},
                }
            },
        )

    reader = OpenBaoSmtpCredential("https://bao.example.invalid", "synthetic-openbao-token")
    credential = await reader.read(transport=httpx2.MockTransport(handler))
    assert credential.password == "synthetic-password"
    assert "synthetic" not in repr(reader) and "synthetic" not in repr(credential)
    with pytest.raises(Exception) as error:
        await reader.read(
            transport=httpx2.MockTransport(
                lambda _: httpx2.Response(
                    500, content=json.dumps({"secret": "synthetic-password"}).encode()
                )
            )
        )
    assert "synthetic-password" not in str(error.value)


@pytest.fixture
def anyio_backend():
    return "asyncio"
