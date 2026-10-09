"""All integration tests require the runner-owned disposable PostgreSQL instance."""

import json
import os
import secrets
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from signal_core.database import Scope, scoped_transaction
from test_shards import partition_cases

if os.environ.get("SIGNAL_DATABASE_LAB") != "1":
    raise pytest.UsageError(
        "Use scripts/run-database-tests.py; no external database is accepted by default."
    )


def pytest_collection_modifyitems(config, items):
    raw = os.environ.get("SIGNAL_DATABASE_TEST_SHARD")
    if raw is None:
        return
    count = os.environ.get("SIGNAL_DATABASE_TEST_SHARDS")
    if count not in {str(i) for i in range(1, 7)} or raw not in {str(i) for i in range(6)}:
        raise pytest.UsageError("Database shards require a bounded count and valid index.")
    try:
        selected, deselected = partition_cases(items, int(raw), int(count))
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None
    report = os.environ.get("SIGNAL_DATABASE_COLLECTION_REPORT")
    if report:
        Path(report).write_text(
            json.dumps(
                {
                    "all": sorted(item.nodeid for item in items),
                    "selected": [item.nodeid for item in selected],
                }
            )
        )
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.fixture
def admin():
    with psycopg.connect(os.environ["SIGNAL_TEST_ADMIN_DSN"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def api():
    with psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def identity():
    with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def scheduler():
    with psycopg.connect(os.environ["SIGNAL_TEST_SCHEDULER_DSN"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def workflow():
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def crawl_admission():
    with psycopg.connect(
        os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
    ) as connection:
        yield connection


@pytest.fixture
def crawl_ingest():
    with psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def scopes():
    tenant = uuid4()
    scopes = [Scope(tenant, uuid4()), Scope(tenant, uuid4()), Scope(uuid4(), uuid4())]
    with psycopg.connect(os.environ["SIGNAL_TEST_BOOTSTRAP_DSN"], autocommit=True) as connection:
        for scope in scopes:
            with scoped_transaction(connection, scope):
                connection.execute(
                    "INSERT INTO app.tenants (tenant_id, name, home_region) "
                    "VALUES (%s, 'Synthetic tenant', 'test') "
                    "ON CONFLICT DO NOTHING",
                    (scope.tenant_id,),
                )
                connection.execute(
                    "INSERT INTO control.tenant_directory VALUES (%s, 'active', 1) "
                    "ON CONFLICT DO NOTHING",
                    (scope.tenant_id,),
                )
                connection.execute(
                    "INSERT INTO app.sites (tenant_id, id, name, primary_origin, "
                    "timezone, reporting_currency) "
                    "VALUES (%s, %s, 'Synthetic site', 'https://example.invalid', 'UTC', 'USD')",
                    (scope.tenant_id, scope.site_id),
                )
    return scopes


@pytest.fixture
def identity_context(admin, scopes):
    user_id = uuid4()
    identity_session_id = uuid4()
    tenant_session_id = uuid4()
    membership_id = uuid4()
    site_membership_id = uuid4()
    session_token = secrets.token_urlsafe(32)
    now = admin.execute("SELECT statement_timestamp()").fetchone()[0]
    expires_at = now + timedelta(hours=1)
    generation = "test-generation-1"
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, 'https://identity.example.invalid', %s, 'Synthetic user', %s)",
        (user_id, f"subject-{user_id}", f"{user_id}@example.invalid"),
    )
    admin.execute(
        "INSERT INTO control.identity_sessions "
        "(id, user_id, token_hash, auth_time, authentication_level, recovery_generation, "
        "expires_at, last_seen_at) VALUES (%s, %s, %s, %s, 'primary', %s, %s, %s)",
        (
            identity_session_id,
            user_id,
            sha256(secrets.token_bytes(32)).digest(),
            now,
            generation,
            expires_at,
            now,
        ),
    )
    admin.execute(
        "INSERT INTO app.memberships "
        "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
        "VALUES (%s, %s, %s, 'analyst', 'active', 1)",
        (scopes[0].tenant_id, membership_id, user_id),
    )
    admin.execute(
        "INSERT INTO app.sessions "
        "(tenant_id, id, identity_session_id, user_id, session_token_hash, auth_time, "
        "mfa_level, expires_at, last_seen_at, active_site_id, session_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'primary', %s, %s, %s, 2)",
        (
            scopes[0].tenant_id,
            tenant_session_id,
            identity_session_id,
            user_id,
            sha256(session_token.encode("ascii")).digest(),
            now,
            expires_at,
            now,
            scopes[0].site_id,
        ),
    )
    admin.execute(
        "INSERT INTO app.site_memberships "
        "(tenant_id, site_id, id, user_id, permission_set, authorization_epoch, state) "
        "VALUES (%s, %s, %s, %s, %s, 1, 'active')",
        (
            scopes[0].tenant_id,
            scopes[0].site_id,
            site_membership_id,
            user_id,
            '{"permissions":["site.snapshot.request"],"schema_version":1}',
        ),
    )
    return {
        "user_id": user_id,
        "identity_session_id": identity_session_id,
        "tenant_session_id": tenant_session_id,
        "membership_id": membership_id,
        "site_membership_id": site_membership_id,
        "session_token": session_token,
        "generation": generation,
    }
