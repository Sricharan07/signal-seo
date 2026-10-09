"""Token-free notification rendering from closed committed report fields."""

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.policy import SMTP
from urllib.parse import urlsplit
from uuid import UUID

from signal_core.identity_conditions import normalize_ascii_mailbox

MAX_MESSAGE_BYTES = 32768
ALERTS = {
    "health_alert": "A health check needs attention. Review its state and remediation in Settings.",
    "pause": "The weekly cycle is paused.",
    "revocation": "An authorization was revoked. No new work is authorized by it.",
    "failed_delivery": "A delivery failed or has an unknown outcome. Review it in the dashboard.",
    "stale_binding": "A connector binding is stale. Review it in the dashboard.",
}
_STAGES = frozenset(
    {"observe", "analyze", "plan", "prepare", "gate", "handoff", "verify", "measure", "report"}
)
_OUTCOMES = frozenset(
    {"completed", "unavailable", "deferred", "waiting_owner", "stopped", "failed"}
)
_REASONS = {
    "weekly_cap_reached": "Weekly capacity reached.",
    "WEEKLY_CAP_REACHED": "Weekly capacity reached.",
    "SITE_PAUSED": "Site paused.",
    "GRANT_REVOKED": "Authorization revoked.",
    "PROVIDER_UNAVAILABLE": "Provider unavailable.",
    "NEXT_WEEK": "Queued for the next week.",
}


def dashboard_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not value.isascii()
        or any(ord(c) < 33 or ord(c) == 127 for c in value)
        or "\\" in value
    ):
        raise ValueError("An exact HTTPS dashboard origin is required.")
    return value


@dataclass(frozen=True, repr=False)
class RenderedEmail:
    body: bytes
    sha256: bytes


def notification_lines(
    *,
    site_id: UUID,
    origin: str,
    category: str,
    projection: dict,
    pr_links: bool = False,
) -> list[str]:
    origin = dashboard_origin(origin)
    if not isinstance(site_id, UUID):
        raise ValueError("An exact site identity is required.")
    # Never render arbitrary customer/model strings, URLs, evidence, IDs, or digests.
    # A fixed vocabulary is stronger than guessing whether free text contains a secret.
    if category == "weekly_report":
        if UUID(projection["site_id"]) != site_id:
            raise ValueError("Report scope differs from the notification.")
        week = date.fromisoformat(projection["week_start"])
        subject = "Signal weekly report"
        lines = [subject, "Week of " + week.isoformat(), "", "What ran"]
        stages = projection.get("stages", [])
        delivery = projection.get("delivery", [])
        deferred = projection.get("deferred", [])
        waiting = projection.get("waiting_for_owner", [])
        if any(
            not isinstance(items, list) or len(items) > 64
            for items in (stages, delivery, deferred, waiting)
        ):
            raise ValueError("The report exceeds its notification bounds.")
        for item in stages[:9]:
            stage, outcome = item.get("stage"), item.get("outcome")
            if stage not in _STAGES or outcome not in _OUTCOMES:
                raise ValueError("Unknown report stage.")
            lines.append(stage.capitalize() + ": " + outcome.replace("_", " "))
        if not stages:
            lines.append("No stages recorded.")
        lines += ["", "What shipped"]
        shipped = 0
        for item in delivery:
            url = item.get("pr_url")
            if url is None or item.get("operation_state") != "opened":
                continue
            match = re.fullmatch(
                r"https://github[.]com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/([1-9][0-9]{0,9})",
                url,
            )
            if match is None:
                raise ValueError("Invalid committed PR reference.")
            shipped += 1
            state = (
                "Live verified"
                if item.get("delivery_outcome") == "verified"
                else "Not live verified"
            )
            lines.append("PR #" + match[1] + " opened. " + state + ".")
            if pr_links:
                lines.append(url)
        if not shipped:
            lines.append("No opened pull requests recorded.")
        lines += [
            "Activity: " + origin + "/changes",
            "",
            "Inbox",
            str(len(waiting)) + " decisions waiting.",
            origin + "/approvals",
            "",
            "Deferred",
        ]
        for item in deferred[:32]:
            lines.append(_REASONS.get(item.get("reason"), "Reason available in the dashboard."))
        if not deferred:
            lines.append("No deferrals recorded.")
        lines += [
            "",
            "Measurement is not inferred from missing data.",
            "Activity: " + origin + "/changes",
        ]
    elif category in ALERTS:
        subject = "Signal alert"
        lines = [
            subject,
            "",
            ALERTS[category],
            "Activity: " + origin + "/changes",
            origin + "/approvals",
        ]
        if category == "health_alert":
            lines.append(origin + "/settings")
    else:
        raise ValueError("Unknown notification category.")
    lines += ["", "Notification preferences: " + origin + "/settings"]
    return lines


def render_email(
    *,
    identifier: UUID,
    site_id: UUID,
    sender: str,
    recipient: str,
    origin: str,
    category: str,
    projection: dict,
) -> RenderedEmail:
    origin = dashboard_origin(origin)
    sender, recipient = normalize_ascii_mailbox(sender), normalize_ascii_mailbox(recipient)
    if not isinstance(identifier, UUID):
        raise ValueError("An exact notification identity is required.")
    lines = notification_lines(
        site_id=site_id, origin=origin, category=category, projection=projection
    )
    subject = lines[0]
    text = "\n".join(lines)
    message = EmailMessage(policy=SMTP)
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message["Message-ID"] = f"<{identifier}@{urlsplit(origin).hostname}>"
    message.set_content(text, cte="quoted-printable")
    message.add_alternative(
        "<html><body><pre>" + html.escape(text, quote=True) + "</pre></body></html>",
        subtype="html",
        cte="quoted-printable",
    )
    message.set_boundary("signal-email-parts")
    body = message.as_bytes()
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError("Notification exceeds the message bound.")
    return RenderedEmail(body, hashlib.sha256(body).digest())
