import json

import httpx2
import pytest
from signal_core.github_app import GitHubAppProtocolError, inspect_github_installation_permissions

from tests.connectors.test_github_app import APP_ID, CREDENTIALS, TARGET


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        None,
        "permissions",
        "suspended",
        "wrong_id",
        "wrong_app",
        "broad",
        "malformed",
        "unavailable",
    ],
)
async def test_actual_installation_permissions_are_bounded_read_only_and_fail_closed(change):
    calls = []

    async def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert (
            str(request.url) == f"https://api.github.com/app/installations/{TARGET.installation_id}"
        )
        assert request.headers["authorization"].startswith("Bearer ")
        document = {
            "id": TARGET.installation_id,
            "app_id": APP_ID,
            "suspended_at": None,
            "repository_selection": "selected",
            "permissions": {"contents": "read", "metadata": "read"},
        }
        if change == "permissions":
            document["permissions"]["pull_requests"] = "write"
        if change == "suspended":
            document["suspended_at"] = "2026-10-02T00:00:00Z"
        if change == "wrong_id":
            document["id"] += 1
        if change == "wrong_app":
            document["app_id"] += 1
        if change == "broad":
            document["repository_selection"] = "all"
        if change == "malformed":
            document["permissions"]["contents"] = {}
        if change == "unavailable":
            raise httpx2.ConnectError("synthetic-provider-unavailable")
        return httpx2.Response(
            200, headers={"content-type": "application/json"}, content=json.dumps(document).encode()
        )

    if change not in {None, "permissions"}:
        with pytest.raises(GitHubAppProtocolError):
            await inspect_github_installation_permissions(
                credentials=CREDENTIALS, target=TARGET, transport=httpx2.MockTransport(handler)
            )
    else:
        digest = await inspect_github_installation_permissions(
            credentials=CREDENTIALS, target=TARGET, transport=httpx2.MockTransport(handler)
        )
        assert len(digest) == 64
    assert len(calls) == 1
