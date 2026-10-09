"""Closed Telegram data protocol with nonsecret method-only request evidence."""

import hashlib
import hmac
import html
import json
import re
from dataclasses import dataclass
from uuid import UUID

from signal_core.crawl_http import TELEGRAM_METHODS, TelegramBotCredential
from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider


class TelegramRejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def telegram_id(value: object) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]{0,15}", value) is None:
        raise TelegramRejected("TELEGRAM_ID_REJECTED")
    if int(value) > 2**52 - 1:
        raise TelegramRejected("TELEGRAM_ID_REJECTED")
    return value


def verify_webhook_secret(expected: str, supplied: str) -> None:
    if (
        not isinstance(supplied, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", supplied) is None
        or not hmac.compare_digest(expected.encode("ascii"), supplied.encode("ascii"))
    ):
        raise TelegramRejected("TELEGRAM_SECRET_REJECTED")


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    user_id: str | None
    chat_id: str | None
    kind: str
    code: str | None = None
    message_id: str | None = None
    callback_id: str | None = None


def parse_update(body: bytes) -> TelegramUpdate:
    try:
        if not isinstance(body, bytes) or not 0 < len(body) <= 32768:
            raise ValueError
        payload = json.loads(body)
        number = payload["update_id"]
        if type(number) is not int or not 0 <= number <= 2**52 - 1:
            raise ValueError
        kinds = set(payload) & {"message", "callback_query", "channel_post", "edited_message"}
        if len(kinds) != 1:
            return TelegramUpdate(number, None, None, "ignored")
        kind = kinds.pop()
        if kind not in {"message", "callback_query"}:
            return TelegramUpdate(number, None, None, "ignored")
        item = payload[kind]
        message = item["message"] if kind == "callback_query" else item
        if message["chat"]["type"] != "private":
            return TelegramUpdate(number, None, None, "group_ignored")
        if item["from"].get("is_bot") is not False or any(
            key in message for key in ("forward_origin", "forward_from", "sender_chat", "via_bot")
        ):
            return TelegramUpdate(number, None, None, "ignored")
        user = telegram_id(item["from"]["id"])
        chat = telegram_id(message["chat"]["id"])
        if user != chat:
            return TelegramUpdate(number, user, chat, "wrong_binding")
        message_id = telegram_id(message["message_id"])
        if kind == "message":
            match = re.fullmatch(r"/start ([A-Za-z0-9_-]{43})", message.get("text", ""))
            return TelegramUpdate(
                number,
                user,
                chat,
                "pair" if match else "ignored",
                match[1] if match else None,
                message_id,
            )
        code, callback = item["data"], item["id"]
        if (
            not isinstance(code, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", code) is None
            or not isinstance(callback, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", callback) is None
        ):
            raise ValueError
        return TelegramUpdate(number, user, chat, "decision", code, message_id, callback)
    except (KeyError, TypeError, ValueError, UnicodeError, TelegramRejected):
        raise TelegramRejected("TELEGRAM_PAYLOAD_REJECTED") from None


def approval_message(
    *,
    chat_id: str,
    revision_id: UUID,
    revision_sha256: str,
    summary: str,
    evidence_url: str,
    approve_code: str,
    reject_code: str,
) -> dict:
    telegram_id(chat_id)
    if (
        not isinstance(revision_id, UUID)
        or re.fullmatch(r"[0-9a-f]{64}", revision_sha256) is None
        or not isinstance(summary, str)
        or len(summary) > 300
        or not evidence_url.startswith("https://")
        or len(evidence_url) > 2048
    ):
        raise ValueError("An exact revision and bounded dashboard evidence are required.")
    for code in (approve_code, reject_code):
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", code) is None:
            raise ValueError("An opaque callback is required.")
    return {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "text": f"{html.escape(summary)}\nRevision {revision_id}\nSHA-256 {revision_sha256}\n"
        + '<a href="'
        + html.escape(evidence_url, quote=True)
        + '">Evidence</a>',
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": approve_code},
                    {"text": "Reject", "callback_data": reject_code},
                ]
            ]
        },
    }


def telegram_json(
    egress: SharedEgressProvider, *, method: str, document: dict, operation_id: UUID, bot_token: str
):
    if method not in TELEGRAM_METHODS:
        raise TelegramRejected("TELEGRAM_METHOD_REJECTED")
    try:
        credential = TelegramBotCredential(bot_token)
        result = egress.request_json(
            method="POST",
            url="https://api.telegram.org/" + method,
            profile=EgressProfile.TELEGRAM_BOT,
            authorization=None,
            telegram_credential=credential,
            body=json.dumps(document, separators=(",", ":"), allow_nan=False).encode(),
            operation_id=operation_id,
            timeout_seconds=5,
            max_response_bytes=16384,
        )
        response = json.loads(result.body)
        if (
            result.status_code != 200
            or not isinstance(response, dict)
            or response.get("ok") is not True
            or "result" not in response
        ):
            raise ValueError
        return response["result"]
    except ProviderEgressUnavailable:
        raise
    except Exception:
        # Provider errors (including bodies and request targets) never escape this boundary.
        raise TelegramRejected("TELEGRAM_PROVIDER_REJECTED") from None


def code_hash(code: str) -> bytes:
    return hashlib.sha256(code.encode("ascii")).digest()
