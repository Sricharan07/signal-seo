"""Fixtures for the invocation-owned joint page-attempt qualification lab."""

import os
from uuid import uuid4

import psycopg
import pytest
from signal_core.database import Scope, scoped_transaction

if os.environ.get("SIGNAL_PAGE_ATTEMPT_LAB") != "1":
    raise pytest.UsageError("Use scripts/run-page-attempt-tests.py for this qualification suite.")


@pytest.fixture
def admin():
    with psycopg.connect(os.environ["SIGNAL_TEST_ADMIN_DSN"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def api():
    with psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True) as connection:
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
def scope():
    value = Scope(uuid4(), uuid4())
    with psycopg.connect(os.environ["SIGNAL_TEST_BOOTSTRAP_DSN"], autocommit=True) as connection:
        with scoped_transaction(connection, value):
            connection.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Page lab tenant', 'test')",
                (value.tenant_id,),
            )
            connection.execute(
                "INSERT INTO control.tenant_directory VALUES (%s, 'active', 1)",
                (value.tenant_id,),
            )
            connection.execute(
                "INSERT INTO app.sites (tenant_id, id, name, primary_origin, timezone, "
                "reporting_currency) VALUES (%s, %s, 'Page lab site', "
                "'http://crawl.example', 'UTC', 'USD')",
                (value.tenant_id, value.site_id),
            )
    return value
