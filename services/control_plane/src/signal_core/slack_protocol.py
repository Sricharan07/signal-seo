"""Closed Slack data protocol; no identity or publishing authority lives here."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import SharedEgressProvider

_ID = re.compile(r"[A-Z][A-Z0-9]{7,63}")
_SIGNATURE = re.compile(r"v0=[0-9a-f]{64}")


class SlackRejected(Exception):
    """A fixed nonsecret outcome; never render provider bodies or credentials."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def slack_id(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value[0] not in prefix:
        raise SlackRejected("SLACK_ID_REJECTED")
    return value


def verify_slack_signature(
    *, secret: str, timestamp: str, signature: str, body: bytes, now: int
) -> bytes:
    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= 32768
        or not isinstance(timestamp, str)
        or re.fullmatch(r"[0-9]{1,12}", timestamp) is None
        or type(now) is not int
        or abs(now - int(timestamp)) > 300
        or not isinstance(signature, str)
        or _SIGNATURE.fullmatch(signature) is None
        or not isinstance(secret, str)
        or not 16 <= len(secret) <= 512
    ):
        raise SlackRejected("SLACK_SIGNATURE_REJECTED")
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise SlackRejected("SLACK_SIGNATURE_REJECTED")
    return hashlib.sha256(signature.encode()).digest()


@dataclass(frozen=True)
class SlackAction:
    workspace_id: str
    channel_id: str
    user_id: str
    callback: str
    action: str
    message_ts: str


def parse_slack_action(body: bytes) -> SlackAction:
    try:
        fields = parse_qs(body.decode("utf-8"), strict_parsing=True, max_num_fields=1)
        if set(fields) != {"payload"} or len(fields["payload"]) != 1:
            raise ValueError
        payload = json.loads(fields["payload"][0])
        if payload["type"] != "block_actions" or len(payload["actions"]) != 1:
            raise ValueError
        action = payload["actions"][0]
        callback = action["value"]
        if not isinstance(callback, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", callback) is None:
            raise ValueError
        if action["action_id"] not in {"signal_approve", "signal_reject", "signal_link"}:
            raise ValueError
        timestamp = payload["message"]["ts"]
        if (
            not isinstance(timestamp, str)
            or re.fullmatch(r"[0-9]{1,16}\.[0-9]{1,8}", timestamp) is None
        ):
            raise ValueError
        return SlackAction(
            slack_id(payload["team"]["id"], "T"),
            slack_id(payload["channel"]["id"], "CDG"),
            slack_id(payload["user"]["id"], "UW"),
            callback,
            action["action_id"],
            timestamp,
        )
    except (KeyError, TypeError, ValueError, UnicodeError, SlackRejected):
        raise SlackRejected("SLACK_PAYLOAD_REJECTED") from None


def approval_message(
    *,
    channel: str,
    revision_id: UUID,
    revision_sha256: str,
    summary: str,
    evidence_url: str,
    approve_code: str,
    reject_code: str,
) -> dict:
    slack_id(channel, "CGUW")
    if not isinstance(revision_id, UUID) or re.fullmatch(r"[0-9a-f]{64}", revision_sha256) is None:
        raise ValueError("An exact sealed revision is required.")
    endpoint = urlsplit(evidence_url)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
    ):
        raise ValueError("A dashboard evidence link is required.")
    if not isinstance(summary, str) or len(summary) > 300 or len(evidence_url) > 2048:
        raise ValueError("A bounded summary and evidence link are required.")
    for code in (approve_code, reject_code):
        if not isinstance(code, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", code) is None:
            raise ValueError("An opaque callback is required.")
    escaped = summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = f"{escaped}\nRevision {revision_id}\nSHA-256 {revision_sha256}"
    return {
        "channel": channel,
        "text": text,
        "mrkdwn": False,
        "parse": "none",
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {"type": "section", "text": {"type": "plain_text", "text": text, "emoji": False}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Evidence"},
                        "url": evidence_url,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "action_id": "signal_approve",
                        "value": approve_code,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "action_id": "signal_reject",
                        "value": reject_code,
                    },
                ],
            },
        ],
    }


def slack_json(
    egress: SharedEgressProvider,
    *,
    method: str,
    document: dict,
    operation_id: UUID,
    bot_token: str | None = None,
) -> dict:
    if method not in {"oauth.v2.access", "chat.postMessage", "auth.revoke"}:
        raise SlackRejected("SLACK_METHOD_REJECTED")
    if (method == "oauth.v2.access") != (bot_token is None):
        raise SlackRejected("SLACK_CREDENTIAL_REJECTED")
    result = egress.request_json(
        method="POST",
        url=f"https://slack.com/api/{method}",
        profile=EgressProfile.SLACK_OAUTH if bot_token is None else EgressProfile.SLACK_BOT,
        authorization=None if bot_token is None else f"Bearer {bot_token}",
        body=(
            urlencode(document).encode("ascii")
            if method == "oauth.v2.access"
            else json.dumps(document, separators=(",", ":")).encode()
        ),
        operation_id=operation_id,
        timeout_seconds=5,
        max_response_bytes=16384,
    )
    try:
        response = json.loads(result.body)
    except (UnicodeError, ValueError):
        raise SlackRejected("SLACK_RESPONSE_REJECTED") from None
    if (
        result.status_code != 200
        or not isinstance(response, dict)
        or response.get("ok") is not True
    ):
        raise SlackRejected("SLACK_PROVIDER_REJECTED")
    return response
