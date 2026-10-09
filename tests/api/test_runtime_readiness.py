from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from signal_api.browser_security import runtime_readiness


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_shared_readiness_checks_all_dependencies_and_fails_closed():
    recovery = SimpleNamespace(current_generation=AsyncMock())
    application = SimpleNamespace(
        state=SimpleNamespace(browser_login=SimpleNamespace(recovery_authority=recovery))
    )
    database = SimpleNamespace(execute=Mock())
    options = dict(
        tokens=SimpleNamespace(healthy=True),
        connection_factory=lambda: nullcontext(database),
        verify=True,
    )
    assert await runtime_readiness(application, **options) is True
    database.execute.assert_called_once_with("SELECT 1")
    recovery.current_generation.assert_awaited_once_with(verify=True)
    options["tokens"].healthy = False
    assert await runtime_readiness(application, **options) is False
    assert database.execute.call_count == 1
    options["tokens"].healthy = True
    database.execute.side_effect = RuntimeError("synthetic-database-unavailable")
    assert await runtime_readiness(application, **options) is False
    database.execute.side_effect = None
    recovery.current_generation.side_effect = RuntimeError("synthetic-recovery-unavailable")
    assert await runtime_readiness(application, **options) is False
