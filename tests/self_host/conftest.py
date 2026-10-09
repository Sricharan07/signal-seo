"""Only the dedicated disposable self-host runner can exercise bootstrap writes."""

import os

import psycopg
import pytest

if os.environ.get("SIGNAL_SELF_HOST_LAB") != "1":
    raise pytest.UsageError("Use scripts/run-self-host-tests.py; no owner database accepted.")


@pytest.fixture
def admin():
    with psycopg.connect(os.environ["SIGNAL_TEST_ADMIN_DSN"], autocommit=True) as connection:
        connection.execute(
            "TRUNCATE control.users, app.tenants, control.tenant_directory, "
            "control.oidc_login_attempts CASCADE"
        )
        yield connection
