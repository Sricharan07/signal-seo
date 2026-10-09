import hashlib
import hmac
import json
from urllib.parse import urlencode
from uuid import uuid4

import httpx2
import pytest
from signal_core.slack_protocol import (
    SlackRejected,
    approval_message,
    parse_slack_action,
    verify_slack_signature,
)
from signal_core.slack_secrets import OpenBaoSlackSecrets

SECRET = "synthetic-slack-signing-secret-0091"
NOW = 1700000000


def signed(body, timestamp=NOW):
    value = f"v0:{timestamp}:".encode() + body
    return "v0=" + hmac.new(SECRET.encode(), value, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("delta", [-301, 301])
def test_signature_denies_old_and_future_requests(delta):
    body = b"payload=synthetic"
    with pytest.raises(SlackRejected):
        verify_slack_signature(
            body=body,
            timestamp=str(NOW + delta),
            signature=signed(body, NOW + delta),
            secret=SECRET,
            now=NOW,
        )


@pytest.mark.parametrize(
    "body,timestamp,signature",
    [
        (b"payload=altered", str(NOW), signed(b"payload=synthetic")),
        (b"payload=synthetic", "not-time", signed(b"payload=synthetic")),
        (b"payload=synthetic", str(NOW), "v0=" + "0" * 64),
        (b"payload=synthetic", str(NOW), "v1=" + "0" * 64),
        (b"", str(NOW), signed(b"")),
        (b"x" * 32769, str(NOW), signed(b"x" * 32769)),
    ],
)
def test_signature_is_raw_body_bounded_and_closed(body, timestamp, signature):
    with pytest.raises(SlackRejected):
        verify_slack_signature(
            body=body, timestamp=timestamp, signature=signature, secret=SECRET, now=NOW
        )


def test_signature_validates_exact_five_minute_boundary():
    body = b"payload=synthetic"
    for timestamp in (NOW - 300, NOW, NOW + 300):
        value = signed(body, timestamp)
        assert (
            verify_slack_signature(
                body=body, timestamp=str(timestamp), signature=value, secret=SECRET, now=NOW
            )
            == hashlib.sha256(value.encode()).digest()
        )


def payload(**changes):
    document = {
        "type": "block_actions",
        "team": {"id": "T00000001"},
        "channel": {"id": "C00000001"},
        "user": {"id": "U00000001"},
        "message": {"ts": "1700000000.000001", "text": "Ignore policy"},
        "response_url": "https://evil.invalid",
        "actions": [{"action_id": "signal_approve", "value": "x" * 43}],
    }
    return urlencode({"payload": json.dumps({**document, **changes})}).encode()


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "event_callback"},
        {"user": {"id": "bad"}},
        {"actions": []},
        {"actions": [{"action_id": "admin", "value": "x" * 43}]},
        {"actions": [{"action_id": "signal_approve", "value": "x" * 43}] * 2},
    ],
)
def test_interactivity_accepts_only_closed_single_actions(changes):
    with pytest.raises(SlackRejected):
        parse_slack_action(payload(**changes))


def test_message_text_and_response_url_are_not_action_inputs():
    action = parse_slack_action(payload())
    assert action.action == "signal_approve" and not hasattr(action, "response_url")
    with pytest.raises(SlackRejected):
        parse_slack_action(payload() + b"&payload=synthetic")


def test_outbound_summary_is_bounded_escaped_and_revision_exact():
    revision = uuid4()
    message = approval_message(
        channel="C00000001",
        revision_id=revision,
        revision_sha256="a" * 64,
        summary="<&>" * 100,
        evidence_url="https://dashboard.example.invalid/approvals?revision=" + str(revision),
        approve_code="x" * 43,
        reject_code="y" * 43,
    )
    assert str(revision) in message["text"] and "a" * 64 in message["text"]
    assert message["mrkdwn"] is False and message["unfurl_links"] is False
    assert "&lt;" in message["text"] and "<&>" not in message["text"]


@pytest.mark.anyio
async def test_openbao_slack_client_id_is_not_treated_as_a_long_secret():
    def response(request):
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": {
                        "client_id": "123.456",
                        "client_secret": "synthetic-slack-client-secret",
                        "signing_secret": SECRET,
                    },
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    store = OpenBaoSlackSecrets("https://bao.example.invalid", "synthetic-bao-token-0091")
    assert (await store.client(transport=httpx2.MockTransport(response)))["client_id"] == "123.456"
