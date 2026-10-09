from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.github_pr_delivery import GitHubPrOperation

SITE_ID = UUID("11111111-1111-4111-8111-111111111111")
TOKEN = "t" * 43


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind,channel",
    [
        ("owner_inbox", "dashboard"),
        ("owner_inbox", "slack"),
        ("owner_inbox", "telegram"),
        ("standing_grant", None),
        ("owner_editorial", "dashboard"),
    ],
)
async def test_operation_contract_names_exact_authorization_source(kind, channel):
    authorization, owner = uuid4(), uuid4()
    current = replace(
        operation(),
        authority_kind=kind,
        authority_id=authorization,
        authority_owner_user_id=owner,
        authority_decision_channel=channel,
    )
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_github_pr_operations=StubOperations((current,)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(
            f"/v1/sites/{SITE_ID}/github-pr-operations",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
    assert response.status_code == 200
    body = response.json()["operations"][0]
    assert body["schema_version"] == 2
    assert body["authority"] == {
        "kind": kind,
        "record_id": str(authorization),
        "owner_user_id": str(owner),
        "decision_channel": channel,
    }


@dataclass
class StubOperations:
    operations: tuple[GitHubPrOperation, ...]

    async def github_pr_operations(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[GitHubPrOperation, ...]:
        assert session_token == TOKEN and site_id == SITE_ID
        return self.operations


def operation(*, state: str = "opened") -> GitHubPrOperation:
    now = datetime.now(UTC)
    number = 7 if state == "opened" else None
    return GitHubPrOperation(
        uuid4(),
        uuid4(),
        "a" * 64,
        "signal/" + "b" * 32,
        state,
        "done" if state == "opened" else "branch",
        "c" * 40,
        "d" * 40,
        "e" * 40,
        now,
        number,
        "https://github.com/SignalOwner/website/pull/7" if number else None,
        uuid4(),
        3,
        "f" * 64,
        now,
    )


@pytest.mark.anyio
async def test_changes_operation_endpoint_exposes_pr_without_claiming_live_delivery():
    current = operation()
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_github_pr_operations=StubOperations((current,)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(
            f"/v1/sites/{SITE_ID}/github-pr-operations",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["operations"][0]["pr_url"] == current.pr_url
    assert body["operations"][0]["journal_body_hash"] == "f" * 64
    assert "deployed" not in str(body).lower()


@pytest.mark.anyio
async def test_changes_operation_endpoint_rejects_invalid_projection():
    invalid = operation(state="opened")
    invalid = GitHubPrOperation(
        invalid.id,
        invalid.revision_id,
        invalid.revision_sha256,
        invalid.branch_name,
        invalid.state,
        invalid.step,
        invalid.base_sha,
        invalid.expected_tree_sha,
        invalid.expected_commit_sha,
        invalid.created_at,
        invalid.pr_number,
        "https://example.invalid/steal",
        invalid.journal_generation,
        invalid.journal_position,
        invalid.journal_body_hash,
        invalid.updated_at,
    )
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_github_pr_operations=StubOperations((invalid,)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(
            f"/v1/sites/{SITE_ID}/github-pr-operations",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
    assert response.status_code == 500


@pytest.mark.anyio
async def test_changes_operation_endpoint_requires_session():
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_github_pr_operations=StubOperations(()),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(f"/v1/sites/{SITE_ID}/github-pr-operations")
    assert response.status_code == 401
