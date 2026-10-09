import json
from uuid import uuid4

import pytest
from signal_core.chat_messages import render_chat
from signal_core.telegram_protocol import TelegramRejected


@pytest.mark.parametrize(
    "channel,destination",
    [("slack_channel", "C00000133"), ("slack_dm", "U00000133"), ("telegram", "9000133")],
)
def test_chat_report_fixed_fields_escape_injection_and_withhold_secrets(channel, destination):
    site = uuid4()
    secret = "synthetic-secret-report-token-0133"
    projection = {
        "site_id": str(site),
        "week_start": "2026-09-28",
        "stages": [
            {"stage": "report", "outcome": "completed", "detail_code": secret + "<!channel>"}
        ],
        "delivery": [
            {
                "operation_state": "opened",
                "pr_url": "https://github.com/example/site/pull/133",
                "delivery_outcome": "verified",
                "summary": secret,
            }
        ],
        "waiting_for_owner": [secret],
        "deferred": [{"reason": "<script>" + secret}],
        "stop_reason": secret,
        "measurements": [
            {
                "horizon": 7,
                "observation": {
                    "state": "measured_as_reported",
                    "reason": secret,
                    "observed_change": {"gsc_page": {"clicks": 12, "impressions": secret}},
                },
            }
        ],
    }
    result = render_chat(
        channel=channel,
        destination=destination,
        site_id=site,
        origin="https://dashboard.example.invalid",
        category="weekly_report",
        projection=projection,
    )
    text = json.dumps(result.payload)
    assert secret not in text and "<!channel>" not in text and "<script>" not in text
    assert "https://github.com/example/site/pull/133" in text and "1 decisions waiting" in text
    assert "1 measurement windows available" in text
    assert "7-day GSC change: clicks +12.00" in text
    assert all(
        "https://dashboard.example.invalid/" + path in text
        for path in ("approvals", "changes", "settings")
    )
    assert "/work" not in text and "Activity:" in text
    assert "blocks" not in result.payload and "reply_markup" not in result.payload
    assert result == render_chat(
        channel=channel,
        destination=destination,
        site_id=site,
        origin="https://dashboard.example.invalid",
        category="weekly_report",
        projection=projection,
    )


@pytest.mark.parametrize("category", ["pause", "revocation", "failed_delivery", "stale_binding"])
@pytest.mark.parametrize(
    "channel,destination", [("slack_channel", "C00000133"), ("telegram", "9000133")]
)
def test_chat_report_alerts_are_escaped_bounded_and_have_no_callbacks(
    category, channel, destination
):
    payload = render_chat(
        channel=channel,
        destination=destination,
        site_id=uuid4(),
        origin="https://dashboard.example.invalid",
        category=category,
        projection={"text": "synthetic-secret <a href='https://evil.invalid'>"},
    ).payload
    assert "synthetic-secret" not in json.dumps(payload)
    assert len(payload["text"]) <= 4096
    assert "actions" not in json.dumps(payload) and "callback" not in json.dumps(payload)


@pytest.mark.parametrize("destination", ["-9000133", "@group", "0", "9007199254740992"])
def test_chat_report_group_and_invalid_chat_rejected(destination):
    with pytest.raises(TelegramRejected):
        render_chat(
            channel="telegram",
            destination=destination,
            site_id=uuid4(),
            origin="https://dashboard.example.invalid",
            category="pause",
            projection={},
        )


def test_chat_report_bounds_scope_and_invalid_pr_links():
    site = uuid4()
    base = {"site_id": str(site), "week_start": "2026-09-28"}
    for projection in (
        {**base, "delivery": [{}] * 65},
        {**base, "site_id": str(uuid4())},
        {**base, "measurements": [{}] * 65},
        {**base, "stages": [{"stage": "<!channel>", "outcome": "completed"}]},
        {
            **base,
            "delivery": [
                {
                    "operation_state": "opened",
                    "pr_url": "https://github.com/example/site/pull/1?token=synthetic-secret",
                }
            ],
        },
    ):
        with pytest.raises(ValueError):
            render_chat(
                channel="slack_channel",
                destination="C00000133",
                site_id=site,
                origin="https://dashboard.example.invalid",
                category="weekly_report",
                projection=projection,
            )
