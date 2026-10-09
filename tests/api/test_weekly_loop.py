"""Owner-visible weekly pause and report HTTP boundary."""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from pydantic import ValidationError
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_api.weekly_report_contracts import WeeklyDeliveryResponse, WeeklySkillResponse
from signal_core.weekly_control import PauseResult

TOKEN = "synthetic-" + "w" * 33
ORIGIN = "https://dashboard.example.test"


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["extra", "wrong_site", "wrong_week", "invented_stage"])
async def test_weekly_report_fails_closed_on_invalid_committed_projection(security, mutation):
    site = uuid4()
    report = {
        "schema_version": 1,
        "cycle_id": str(uuid4()),
        "site_id": str(site),
        "week_start": "2026-09-28",
        "status": "completed",
        "stop_reason": "CYCLE_REPORTED",
        "started_at": "2026-09-29T00:00:00Z",
        "closed_at": "2026-09-29T00:01:00Z",
        "stages": [],
        "gate_decisions": [],
        "waiting_for_owner": [],
        "handoffs": [],
        "deferred": [],
        "delivery": [],
        "observation_command": None,
    }
    if mutation == "extra":
        report["unrecorded_success"] = True
    elif mutation == "wrong_site":
        report["site_id"] = str(uuid4())
    elif mutation == "wrong_week":
        report["week_start"] = "2026-09-21"
    else:
        report["stages"] = [
            {
                "stage": "deploy",
                "outcome": "completed",
                "detail_code": "DONE",
                "recorded_at": "2026-09-29T00:01:00Z",
                "evidence_refs": [],
            }
        ]
    async with _client(StubWeeklyGateway(report=report), security) as api:
        response = await api.get(
            f"/v1/sites/{site}/weekly-cycles/2026-09-28", headers=_headers(security)
        )
    assert response.status_code == 500
    assert "unrecorded_success" not in response.text


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def security():
    return BrowserSecurity(
        csrf_hmac_key=b"synthetic-weekly-csrf-key" * 3,
        allowed_origins=frozenset({ORIGIN}),
    )


@dataclass
class StubWeeklyGateway:
    seen: list[tuple] = field(default_factory=list)
    report: dict | None = None
    deny: bool = False

    async def read_site_pause(self, *, session_token, site_id):
        if self.deny:
            raise PermissionError()
        return {"site_id": str(site_id), "paused": False}

    async def set_site_paused(
        self, *, session_token: str, site_id: UUID, paused: bool
    ) -> PauseResult:
        self.seen.append((session_token, site_id, paused))
        if self.deny:
            raise PermissionError()
        return PauseResult(
            "paused" if paused else "pause_cleared", 2, 1, "AUTHORITY_DURABILITY_PENDING"
        )

    async def read_weekly_report(
        self, *, session_token: str, site_id: UUID, week_start: date
    ) -> dict | None:
        self.seen.append((session_token, site_id, week_start))
        return self.report


def _headers(security):
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }


def _client(gateway, security):
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_login=gateway,
        browser_security=security,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://signal.example.test",
    )


@pytest.mark.anyio
async def test_owner_pause_resume_and_report_are_exact_scope(security):
    site = uuid4()
    command = uuid4()
    gateway = StubWeeklyGateway(
        report={
            "schema_version": 1,
            "cycle_id": str(uuid4()),
            "site_id": str(site),
            "week_start": "2026-09-28",
            "stop_reason": None,
            "started_at": "2026-09-29T00:00:00Z",
            "closed_at": None,
            "gate_decisions": [],
            "waiting_for_owner": [],
            "deferred": [],
            "delivery": [],
            "status": "running",
            "stages": [],
            "handoffs": [],
            "observation_command": {
                "command_id": str(command),
                "status": "accepted",
                "result_reference": None,
                "status_url": f"/v1/sites/{site}/weekly-cycles/2026-09-28/observation/{command}",
            },
        }
    )
    async with _client(gateway, security) as api:
        paused = await api.post(f"/v1/sites/{site}/weekly-loop/pause", headers=_headers(security))
        report = await api.get(
            f"/v1/sites/{site}/weekly-cycles/2026-09-28", headers=_headers(security)
        )
        observation = await api.get(
            f"/v1/sites/{site}/weekly-cycles/2026-09-28/observation/{command}",
            headers=_headers(security),
        )
        wrong_observation = await api.get(
            f"/v1/sites/{site}/weekly-cycles/2026-09-28/observation/{uuid4()}",
            headers=_headers(security),
        )
        resumed = await api.post(f"/v1/sites/{site}/weekly-loop/resume", headers=_headers(security))
    assert paused.json() == {
        "state": "paused",
        "epoch": 2,
        "draining_observations": 1,
        "durability": "AUTHORITY_DURABILITY_PENDING",
    }
    assert report.json()["status"] == "running"
    assert observation.json()["command_id"] == str(command)
    assert wrong_observation.status_code == 404
    assert resumed.json()["state"] == "pause_cleared"
    assert gateway.seen == [
        (TOKEN, site, True),
        (TOKEN, site, date(2026, 9, 28)),
        (TOKEN, site, date(2026, 9, 28)),
        (TOKEN, site, date(2026, 9, 28)),
        (TOKEN, site, False),
    ]


@pytest.mark.anyio
async def test_latest_cycle_returns_last_recorded_window_and_skill_evidence(security):
    site = uuid4()
    skill = {
        "stage": "brain_refresh",
        "outcome": "unavailable",
        "detail_code": "PORT_UNCONFIGURED",
        "work_type": "research_audit",
        "budget_source": "standing_authorization",
        "cap_source": "standing_authorization",
        "reserved_cents": 0,
        "units": 0,
        "spend_status": "no_paid_io",
        "evidence_refs": [],
        "recorded_at": None,
    }
    gateway = StubWeeklyGateway(
        report={
            "schema_version": 1,
            "cycle_id": str(uuid4()),
            "site_id": str(site),
            "week_start": "2026-09-21",
            "status": "completed",
            "stop_reason": "CYCLE_REPORTED",
            "started_at": "2026-09-21T09:00:00Z",
            "closed_at": "2026-09-21T10:00:00Z",
            "stages": [],
            "skill_stages": [skill],
            "gate_decisions": [],
            "waiting_for_owner": [],
            "handoffs": [],
            "deferred": [],
            "delivery": [],
            "observation_command": None,
        }
    )
    async with _client(gateway, security) as api:
        response = await api.get(
            f"/v1/sites/{site}/weekly-cycles/latest", headers=_headers(security)
        )
    assert response.status_code == 200
    assert response.json()["week_start"] == "2026-09-21"
    assert response.json()["skill_stages"][0]["detail_code"] == "PORT_UNCONFIGURED"
    assert gateway.seen == [(TOKEN, site, None)]


@pytest.mark.parametrize(
    "mutation",
    [
        {"stage": "approve"},
        {"work_type": "metadata_pr"},
        {"outcome": "delivered"},
        {"cap_source": "standing_authorization_and_email"},
        {"reserved_cents": -1},
        {"units": 65},
        {"reserved_cents": 2},
        {"grant_override": True},
        {"evidence_refs": ["<script>untrusted</script>"]},
        {"evidence_refs": ["x" * 513]},
    ],
)
def test_skill_projection_rejects_fabricated_authority_and_budget(mutation):
    row = {
        "stage": "strategy_rebuild",
        "outcome": "completed",
        "detail_code": "STRATEGY_RECORDED",
        "work_type": "research_audit",
        "budget_source": "standing_authorization",
        "cap_source": "standing_authorization",
        "reserved_cents": 0,
        "units": 1,
        "spend_status": "no_paid_io",
        "evidence_refs": [],
        "recorded_at": None,
    }
    with pytest.raises(ValidationError):
        WeeklySkillResponse.model_validate({**row, **mutation})


@pytest.mark.anyio
async def test_cross_origin_or_missing_owner_never_exposes_weekly_state(security):
    site = uuid4()
    gateway = StubWeeklyGateway(deny=True)
    async with _client(gateway, security) as api:
        cross = await api.post(
            f"/v1/sites/{site}/weekly-loop/pause",
            headers={**_headers(security), "Origin": "https://other.example.test"},
        )
        denied = await api.post(f"/v1/sites/{site}/weekly-loop/pause", headers=_headers(security))
        missing = await api.get(
            f"/v1/sites/{site}/weekly-cycles/2026-09-28", headers=_headers(security)
        )
        missing_observation = await api.get(
            f"/v1/sites/{site}/weekly-cycles/2026-09-28/observation/{uuid4()}",
            headers=_headers(security),
        )
    assert cross.status_code == 403
    assert denied.status_code == 401
    assert missing.status_code == 404
    assert missing_observation.status_code == 404
    assert gateway.seen == [
        (TOKEN, site, True),
        (TOKEN, site, date(2026, 9, 28)),
        (TOKEN, site, date(2026, 9, 28)),
    ]


def test_weekly_delivery_contract_keeps_telegram_exact_inbox_authority():
    value = {
        "workload_id": uuid4(),
        "finding_id": uuid4(),
        "recipe_release_id": uuid4(),
        "revision_id": uuid4(),
        "revision_sha256": "a" * 64,
        "operation_id": uuid4(),
        "operation_state": "opened",
        "pr_url": "https://github.com/SignalOwner/website/pull/7",
        "authority_kind": "owner_inbox",
        "authority_id": uuid4(),
        "authorization_owner_id": uuid4(),
        "decision_channel": "telegram",
        "review_status": "approved",
        "observation_id": None,
        "observation_sha256": None,
        "delivery_outcome": None,
        "delivery_reason": None,
        "delivery_stage": "pr_opened",
    }
    assert WeeklyDeliveryResponse.model_validate(value).decision_channel == "telegram"
    for change in ({"decision_channel": "unknown"}, {"authority_kind": "standing_grant"}):
        with pytest.raises(ValidationError):
            WeeklyDeliveryResponse.model_validate({**value, **change})
