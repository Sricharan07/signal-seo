"""Only synthetic runner-owned database and pinned browser images are accepted."""

import os
from uuid import uuid4

import psycopg
import pytest
from signal_core.database import Scope, scoped_transaction

if os.environ.get("SIGNAL_BROWSER_WORKER_LAB") != "1":
    raise pytest.UsageError("Use scripts/run-browser-worker-tests.py.")


def connection_fixture(variable):
    @pytest.fixture
    def fixture():
        with psycopg.connect(os.environ[variable], autocommit=True) as connection:
            yield connection

    return fixture


admin = connection_fixture("SIGNAL_TEST_ADMIN_DSN")
api = connection_fixture("SIGNAL_TEST_API_DSN")
scheduler = connection_fixture("SIGNAL_TEST_SCHEDULER_DSN")
workflow = connection_fixture("SIGNAL_TEST_WORKFLOW_DSN")
crawl_admission = connection_fixture("SIGNAL_TEST_CRAWL_ADMISSION_DSN")
crawl_ingest = connection_fixture("SIGNAL_TEST_CRAWL_INGEST_DSN")


@pytest.fixture
def scopes():
    scopes = [Scope(uuid4(), uuid4()), Scope(uuid4(), uuid4())]
    with psycopg.connect(os.environ["SIGNAL_TEST_BOOTSTRAP_DSN"], autocommit=True) as connection:
        for scope in scopes:
            with scoped_transaction(connection, scope):
                connection.execute(
                    "INSERT INTO app.tenants (tenant_id,name,home_region) "
                    "VALUES (%s,'Synthetic browser tenant','test')",
                    (scope.tenant_id,),
                )
                connection.execute(
                    "INSERT INTO control.tenant_directory VALUES (%s,'active',1)",
                    (scope.tenant_id,),
                )
                connection.execute(
                    "INSERT INTO app.sites "
                    "(tenant_id,id,name,primary_origin,timezone,reporting_currency) "
                    "VALUES (%s,%s,'Synthetic browser site','http://browser.example.invalid','UTC','USD')",
                    (scope.tenant_id, scope.site_id),
                )
    return scopes
