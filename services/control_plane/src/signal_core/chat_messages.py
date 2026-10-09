"""Bounded, token-free chat reports. Customer/model text is deliberately withheld."""

import hashlib
import html
import json
import math
from dataclasses import dataclass
from uuid import UUID

from signal_core.email_messages import notification_lines
from signal_core.slack_protocol import slack_id
from signal_core.telegram_protocol import telegram_id


@dataclass(frozen=True, repr=False)
class RenderedChat:
    payload: dict
    sha256: bytes


def render_chat(
    *, channel: str, destination: str, site_id: UUID, origin: str, category: str, projection: dict
) -> RenderedChat:
    lines = notification_lines(
        site_id=site_id, origin=origin, category=category, projection=projection, pr_links=True
    )
    # Chat limits are smaller than email. Preserve counts and dashboard links while
    # bounding the list of actual PR references, without truncating a URL mid-link.
    if category == "weekly_report":
        measurements = projection.get("measurements", [])
        if not isinstance(measurements, list) or len(measurements) > 64:
            raise ValueError("Measurement list exceeds its report bound.")
        observed = sum(
            item.get("observation", {}).get("state") == "measured_as_reported"
            for item in measurements
        )
        index = lines.index("Measurement is not inferred from missing data.")
        lines[index:index] = [
            "What it did",
            f"{observed} measurement windows available; "
            f"{len(measurements) - observed} pending or incomplete.",
            "Provider-reported changes carry uncertainty and confounders; not proof of causation.",
        ]
        for item in measurements[:3]:
            observation = item.get("observation", {})
            if observation.get("state") != "measured_as_reported" or item.get("horizon") not in {
                7,
                28,
                90,
            }:
                continue
            deltas = observation.get("observed_change", {}).get("gsc_page") or {}
            values = []
            for metric in ("clicks", "impressions", "ctr", "position"):
                value = deltas.get(metric)
                if type(value) in {int, float} and abs(value) <= 10**12 and math.isfinite(value):
                    values.append(metric + " " + format(value, "+.2f"))
            if values:
                lines.insert(
                    index + 2, str(item["horizon"]) + "-day GSC change: " + ", ".join(values)
                )
        lines[lines.index("Deferred")] = "What is next"
        pr_lines = [
            line
            for line in lines
            if line.startswith("PR #") or line.startswith("https://github.com/")
        ]
        if len(pr_lines) > 8:
            lines = [line for line in lines if line not in pr_lines[8:]]
            lines.insert(lines.index("Inbox"), "Additional pull requests: " + origin + "/changes")
    text = "\n".join(lines)
    if channel in {"slack_channel", "slack_dm"}:
        slack_id(destination, "CG" if channel == "slack_channel" else "UW")
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        payload = {
            "channel": destination,
            "text": escaped,
            "mrkdwn": False,
            "parse": "none",
            "unfurl_links": False,
            "unfurl_media": False,
        }
    elif channel == "telegram":
        telegram_id(destination)
        payload = {
            "chat_id": destination,
            "text": html.escape(text, quote=True),
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
    else:
        raise ValueError("Unknown chat channel.")
    if len(payload["text"]) > 4096 or len(payload["text"].encode()) > 8192:
        raise ValueError("Chat report exceeds its message bound.")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return RenderedChat(payload, hashlib.sha256(body).digest())
