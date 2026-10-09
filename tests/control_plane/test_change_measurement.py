import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from tests.control_plane.measurement_support import bind_sources, clone_fixture_row, import_fixture
from tests.control_plane.test_gsc_binding import owner_and_verified_origin, session_args


def test_source_windows_pin_generations_and_preserve_partial_metadata(
    admin, identity, scopes, identity_context
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    unbound = admin.execute(
        "SELECT control.measurement_source_window(%s,%s,%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            "https://example.invalid/",
            date(2026, 8, 1),
            date(2026, 8, 7),
            datetime.now(UTC),
        ),
    ).fetchone()[0]
    assert unbound["gsc_page"]["state"] == "unavailable"
    assert unbound["bing_site_context"]["state"] == "unavailable"
    bindings, page = bind_sources(admin, identity, scope, identity_context)
    start, end = date(2026, 8, 1), date(2026, 8, 7)

    def read(cutoff=None):
        return admin.execute(
            "SELECT control.measurement_source_window(%s,%s,%s,%s,%s,%s)",
            (scope.tenant_id, scope.site_id, page, start, end, cutoff or datetime.now(UTC)),
        ).fetchone()[0]

    assert read()["gsc_page"]["state"] == "awaiting_data"
    imported_at = admin.execute("SELECT transaction_timestamp() - interval '1 minute'").fetchone()[
        0
    ]
    g, b, coverage, bing_coverage = import_fixture(
        admin, scope, bindings, page, start, end, lag=True, imported_at=imported_at
    )
    assert read(datetime.now(UTC))["gsc_page"]["reason"] == "PROVIDER_LAG"
    prior_g, prior_b = g, b
    g, b, coverage, bing_coverage = import_fixture(
        admin,
        scope,
        bindings,
        page,
        start,
        end,
        imported_at=imported_at + timedelta(seconds=1),
    )
    pinned = read(imported_at)
    assert pinned["gsc_page"]["generation_id"] == str(prior_g)
    assert pinned["gsc_page"]["reason"] == "PROVIDER_LAG"
    assert pinned["bing_site_context"]["generation_id"] == str(prior_b)
    value = read(datetime.now(UTC))
    assert value["gsc_page"] == {
        "state": "measured_as_reported",
        "reason": "AS_REPORTED_COMPLETENESS_NOT_GUARANTEED",
        "generation_id": str(g),
        "coverage": coverage,
        "metrics": {"clicks": 70, "impressions": 700, "ctr": 0.1, "position": 4},
    }
    assert value["bing_site_context"]["generation_id"] == str(b)
    assert value["bing_site_context"]["coverage"] == bing_coverage
    assert value["bing_site_context"]["metrics"] == {
        "clicks": 140,
        "impressions": 1400,
        "ctr": None,
        "position": None,
    }
    assert value["bing_page"]["metrics"] is None
    assert value["bing_page"]["state"] == "unavailable"
    assert (
        identity.execute(
            "SELECT outcome FROM control.revoke_gsc_binding(%s,%s,%s,%s,%s)",
            (
                *session_args(identity_context, scope.site_id),
                bindings["gsc"],
                uuid4(),
            ),
        ).fetchone()[0]
        == "revoked"
    )
    assert read(datetime.now(UTC))["gsc_page"]["state"] == "unavailable"
    assert (
        identity.execute(
            "SELECT outcome FROM control.revoke_bing_binding(%s,%s,%s,%s,%s)",
            (*session_args(identity_context, scope.site_id), bindings["bing"], uuid4()),
        ).fetchone()[0]
        == "revoked"
    )
    assert read(datetime.now(UTC))["bing_site_context"]["state"] == "unavailable"


@pytest.mark.parametrize("insert_order", [(1, 2**128 - 1), (2**128 - 1, 1)])
def test_equal_import_timestamps_use_uuid_tiebreak_not_insertion_order(
    admin, identity, scopes, identity_context, insert_order
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    bindings, page = bind_sources(admin, identity, scope, identity_context)
    start, end = date(2026, 8, 1), date(2026, 8, 7)
    imported_at = admin.execute("SELECT transaction_timestamp() - interval '1 minute'").fetchone()[
        0
    ]
    g, b, *_ = import_fixture(admin, scope, bindings, page, start, end, imported_at=imported_at)
    for table, original in (("gsc_import_generations", g), ("bing_import_generations", b)):
        for identifier in insert_order:
            clone_fixture_row(admin, table, "id=%s", (original,), {"id": UUID(int=identifier)})
        assert admin.execute(
            sql.SQL(
                "SELECT count(*),count(DISTINCT imported_at) FROM app.{} "
                "WHERE tenant_id=%s AND site_id=%s"
            ).format(sql.Identifier(table)),
            (scope.tenant_id, scope.site_id),
        ).fetchone() == (3, 1)
    for _ in range(20):
        value = admin.execute(
            "SELECT control.measurement_source_window(%s,%s,%s,%s,%s,%s)",
            (scope.tenant_id, scope.site_id, page, start, end, imported_at),
        ).fetchone()[0]
        for source in ("gsc_page", "bing_site_context"):
            assert value[source]["state"] == "measured_as_reported"
            assert value[source]["generation_id"] == str(UUID(int=2**128 - 1))


@pytest.mark.parametrize("table", ["change_measurement_plans", "change_measurement_observations"])
def test_measurement_storage_is_forced_rls_and_function_only(admin, api, table):
    assert admin.execute(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
        (f"app.{table}",),
    ).fetchone() == (True, True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(f"SELECT * FROM app.{table}")


def test_unknown_scope_and_nonworker_cannot_measure(workflow, api, scopes):
    from uuid import uuid4

    scope = scopes[0]
    assert workflow.execute(
        "SELECT control.measure_scheduled_change(%s,%s,%s,%s)",
        (scope.tenant_id, scopes[1].site_id, uuid4(), 7),
    ).fetchone() == (None,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(
            "SELECT control.measure_scheduled_change(%s,%s,%s,%s)",
            (scope.tenant_id, scope.site_id, uuid4(), 7),
        )


def test_article_integration_preserves_technical_measurement_functions(admin):
    source = Path("database/migrations/versions/0066_change_measurement.sql").read_text()
    for original, installed in (
        ("capture_change_measurement_baseline", "capture_technical_change_measurement_baseline"),
        ("measure_change_horizon", "measure_change_horizon"),
    ):
        expected = source.split(f"CREATE FUNCTION control.{original}(", 1)[1]
        expected = expected.split("AS $$", 1)[1].split("$$;", 1)[0].replace("%%", "%")
        if original == "measure_change_horizon":
            expected = expected.replace(
                "BETWEEN p.baseline_start AND p.post_end;",
                "BETWEEN COALESCE(p.baseline_start,(p.verified_live_at AT TIME ZONE "
                "'America/Los_Angeles')::date) AND p.post_end;",
            )
            expected = expected.replace(
                "v_post->'bing_site_context'->'metrics')),",
                "v_post->'bing_site_context'->'metrics'),'bing_page',"
                "control.measurement_delta(p.baseline->'bing_page'->'metrics',"
                "v_post->'bing_page'->'metrics')),",
            )
        actual = admin.execute(
            "SELECT prosrc FROM pg_proc JOIN pg_namespace n ON n.oid=pronamespace "
            "WHERE n.nspname='control' AND proname=%s",
            (installed,),
        ).fetchone()[0]
        assert actual == expected


def test_article_measurement_adds_no_runtime_write_authority(admin):
    for function in (
        "capture_change_measurement_baseline(app.github_delivery_receipts)",
        "capture_technical_change_measurement_baseline(app.github_delivery_receipts)",
        "new_page_measurement_baseline()",
        "measure_change_horizon(uuid,uuid,uuid,integer,timestamptz)",
    ):
        for role in ("signal_api", "signal_identity", "signal_workflow", "signal_scheduler"):
            assert not admin.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                (role, "control." + function),
            ).fetchone()[0]


def test_absent_page_dimension_wrong_page_and_invalid_source_stay_explicit(
    admin, identity, scopes, identity_context
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    bindings, page = bind_sources(admin, identity, scope, identity_context)
    start, end = date(2026, 8, 1), date(2026, 8, 7)

    def read(target=page):
        return admin.execute(
            "SELECT control.measurement_source_window(%s,%s,%s,%s,%s,%s)",
            (scope.tenant_id, scope.site_id, target, start, end, datetime.now(UTC)),
        ).fetchone()[0]

    import_fixture(admin, scope, bindings, page, start, end, dimensions=["date"])
    assert read()["gsc_page"]["reason"] == "NO_PAGE_GENERATION_COVERS_WINDOW"
    generation, *_ = import_fixture(admin, scope, bindings, page, start, end)
    assert read(page + "other")["gsc_page"]["reason"] == "PAGE_ROWS_NOT_REPORTED"
    clone_fixture_row(
        admin,
        "gsc_import_generations",
        "id=%s",
        (generation,),
        {
            "id": uuid4(),
            "imported_at": datetime.now(UTC).isoformat(),
            "rows": [
                {
                    "keys": ["synthetic-invalid-date", page],
                    "clicks": 10,
                    "impressions": 100,
                    "ctr": 0.1,
                    "position": 4,
                }
            ],
        },
    )
    assert read()["gsc_page"]["state"] == "failed"
    assert read()["gsc_page"]["metrics"] is None


def test_failed_measurement_migration_preserves_prior_revision(admin):
    name = "synthetic_measurement_migration_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        env = dict(
            os.environ,
            SIGNAL_MIGRATION_DSN=make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name),
        )
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        command = [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade"]
        prior = subprocess.run(
            command + ["0060"], env=env, capture_output=True, text=True, timeout=30
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.change_measurement_observations (sentinel text)")
        failed = subprocess.run(
            command + ["head"], env=env, capture_output=True, text=True, timeout=30
        )
        assert failed.returncode != 0
        with psycopg.connect(dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0060",)]
            assert connection.execute(
                "SELECT to_regclass('app.change_measurement_plans')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regprocedure('control.measure_scheduled_change(uuid,uuid,uuid,integer)')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
