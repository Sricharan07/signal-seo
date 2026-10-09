import os
import subprocess
import sys
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from signal_core.commands import accept_snapshot
from signal_core.database import Scope, scoped_transaction


def test_runtime_roles_are_not_owners_or_privileged(admin):
    rows = admin.execute(
        "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname LIKE 'signal_%'"
    ).fetchall()
    assert len(rows) == 10
    assert all(not any(row[1:]) for row in rows)
    tables = admin.execute(
        "SELECT relname, pg_get_userbyid(relowner), relrowsecurity, relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'app' AND relkind = 'r'"
    ).fetchall()
    assert len(tables) == 183
    assert all(row[1:] == ("signal_migrator", True, True) for row in tables)


def test_privileges_match_reviewed_manifest(admin):
    import json
    from pathlib import Path

    manifest = json.loads(Path("database/privileges.json").read_text())
    for role, tables in manifest.items():
        for schema, table in admin.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema IN ('app', 'control') AND table_type = 'BASE TABLE'"
        ):
            qualified = f"{schema}.{table}"
            actual = [
                privilege
                for privilege in [
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                ]
                if admin.execute(
                    "SELECT has_table_privilege(%s, %s, %s)", (role, qualified, privilege)
                ).fetchone()[0]
            ]
            assert sorted(actual) == sorted(tables.get(qualified, [])), (role, qualified, actual)
        assert not admin.execute(
            "SELECT has_schema_privilege(%s, 'public', 'CREATE')", (role,)
        ).fetchone()[0]
        assert not admin.execute(
            "SELECT has_schema_privilege(%s, 'app', 'CREATE')", (role,)
        ).fetchone()[0]


def test_context_function_execution_is_explicit_and_not_public(admin):
    functions = [
        "app.current_tenant_id()",
        "app.current_site_id()",
        "app.current_session_hash()",
        "control.current_identity_session_hash()",
        "control.current_oidc_state_hash()",
        "control.current_oidc_browser_binding_hash()",
    ]
    for function in functions:
        assert not admin.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE')", (function,)
        ).fetchone()[0]
    for function in functions:
        assert admin.execute(
            "SELECT has_function_privilege('signal_identity', %s, 'EXECUTE')", (function,)
        ).fetchone()[0]


def test_missing_scope_returns_no_tenant_rows(api, scopes):
    for table in ["tenants", "sites", "commands", "command_events", "outbox"]:
        assert api.execute(f"SELECT count(*) FROM app.{table}").fetchone()[0] == 0


def test_scope_is_transaction_local_and_reused_connections_do_not_leak(api, scopes):
    for scope in scopes:
        with scoped_transaction(api, scope):
            assert api.execute("SELECT tenant_id, id FROM app.sites").fetchall() == [
                (scope.tenant_id, scope.site_id)
            ]
        assert api.execute("SELECT count(*) FROM app.sites").fetchone()[0] == 0
        assert (
            api.execute("SELECT NULLIF(current_setting('signal.tenant_id', true), '')").fetchone()[
                0
            ]
            is None
        )


def test_scope_is_cleared_after_failure_and_cannot_be_nested(api, scopes):
    with pytest.raises(RuntimeError, match="injected"):
        with scoped_transaction(api, scopes[0]):
            with pytest.raises(ValueError, match="nested"):
                with scoped_transaction(api, scopes[1]):
                    pytest.fail("Nested scope must never be entered")
            raise RuntimeError("injected")
    assert api.execute("SELECT count(*) FROM app.sites").fetchone()[0] == 0


def test_scope_rejects_non_uuid_values():
    with pytest.raises(ValueError, match="UUID"):
        Scope("spoofed", uuid4())


def test_invalid_raw_scope_fails_closed(api):
    with pytest.raises(psycopg.errors.InvalidTextRepresentation), api.transaction():
        api.execute("SELECT set_config('signal.tenant_id', 'not-a-uuid', true)")
        api.execute("SELECT * FROM app.tenants").fetchall()


def test_cross_tenant_and_same_tenant_wrong_site_are_invisible(api, scopes):
    for scope in scopes:
        accept_snapshot(api, scope, actor_service="test", idempotency_key=str(uuid4()))
    with scoped_transaction(api, scopes[0]):
        assert api.execute("SELECT DISTINCT tenant_id, site_id FROM app.commands").fetchall() == [
            (scopes[0].tenant_id, scopes[0].site_id)
        ]


def test_cross_scope_foreign_keys_reject_even_when_rls_is_bypassed(admin, api, scopes):
    command = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key=str(uuid4()))
    for wrong in [scopes[1], scopes[2]]:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            admin.execute(
                "INSERT INTO app.command_events VALUES "
                "(%s, %s, %s, %s, 2, 'command.workflow_admitted', "
                "jsonb_build_object('consumer_key', 'workflow.command-start.v1', "
                "'schema_version', 1, 'workflow_id', %s::text))",
                (
                    wrong.tenant_id,
                    wrong.site_id,
                    uuid4(),
                    command.id,
                    f"signal:CrawlSite:{wrong.tenant_id}:{command.id}",
                ),
            )


def test_outbox_cannot_reference_another_commands_event(admin, api, scopes):
    first = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key=str(uuid4()))
    second = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key=str(uuid4()))
    event_id = uuid4()
    admin.execute(
        "INSERT INTO app.command_events VALUES "
        "(%s, %s, %s, %s, 2, 'command.workflow_admitted', "
        "jsonb_build_object('consumer_key', 'workflow.command-start.v1', "
        "'schema_version', 1, 'workflow_id', %s::text))",
        (
            scopes[0].tenant_id,
            scopes[0].site_id,
            event_id,
            second.id,
            f"signal:CrawlSite:{scopes[0].tenant_id}:{second.id}",
        ),
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute(
            "INSERT INTO app.outbox (tenant_id, site_id, id, event_id, aggregate_kind, "
            "aggregate_id, event_type, schema_version, payload) "
            "VALUES (%s, %s, %s, %s, 'command', %s, 'command.accepted', "
            "1, '{\"schema_version\":1}')",
            (scopes[0].tenant_id, scopes[0].site_id, uuid4(), event_id, first.id),
        )


def test_runtime_cannot_disable_rls_write_control_or_truncate(api, scopes):
    for statement in [
        "ALTER TABLE app.commands DISABLE ROW LEVEL SECURITY",
        "TRUNCATE app.commands",
        "SELECT * FROM control.tenant_directory",
        "SET ROLE signal_migrator",
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(statement)


def test_forced_rls_applies_even_to_table_owner(scopes):
    with psycopg.connect(os.environ["SIGNAL_MIGRATION_DSN"], autocommit=True) as owner:
        assert owner.execute("SELECT count(*) FROM app.tenants").fetchone()[0] == 0


def test_immutable_records_have_privilege_and_trigger_protection(admin, api, scopes):
    command = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key=str(uuid4()))
    with scoped_transaction(api, scopes[0]):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), api.transaction():
            api.execute("DELETE FROM app.commands WHERE id = %s", (command.id,))
    for table in ["commands", "command_events", "outbox"]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(f"DELETE FROM app.{table} WHERE tenant_id = %s", (scopes[0].tenant_id,))


def test_tenant_context_functions_are_stable_not_immutable(admin):
    values = admin.execute(
        "SELECT provolatile FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
        "WHERE n.nspname = 'app' AND proname IN ('current_tenant_id', 'current_site_id')"
    ).fetchall()
    assert values == [("s",), ("s",)]


def test_residual_session_scope_is_rejected_instead_of_restored(api, scopes):
    api.execute("SELECT set_config('signal.tenant_id', %s, false)", (str(scopes[1].tenant_id),))
    with pytest.raises(ValueError, match="residual session scope"):
        with scoped_transaction(api, scopes[0]):
            pytest.fail("A contaminated connection cannot be reused")


def test_non_autocommit_connection_is_rejected(scopes):
    with psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"]) as connection:
        with pytest.raises(ValueError, match="autocommit"):
            with scoped_transaction(connection, scopes[0]):
                pytest.fail("The transaction helper must own the outer transaction")


def test_runtime_cannot_run_migrations():
    env = dict(os.environ, SIGNAL_MIGRATION_DSN=os.environ["SIGNAL_TEST_API_DSN"])
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "dedicated non-superuser migrator" in result.stderr


def test_migration_revision_is_persisted_and_downgrade_refuses_data_loss(admin):
    assert admin.execute("SELECT version_num FROM control.alembic_version").fetchall() == [
        ("0103",)
    ]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "downgrade", "base"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Destructive downgrade is disabled" in result.stderr
    assert admin.execute("SELECT version_num FROM control.alembic_version").fetchall() == [
        ("0103",)
    ]


def test_failed_pagespeed_migration_preserves_owner_egress_authority(admin):
    name = "pagespeed_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0082"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE FUNCTION control.valid_pagespeed_projection(jsonb) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT false $$"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0083"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchone() == ("0082",)
            for table in ("pagespeed_sample", "pagespeed_observations", "pagespeed_failures"):
                assert connection.execute(
                    "SELECT to_regclass(%s)", ("app." + table,)
                ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regprocedure('control.owner_request_before_pagespeed(text,text,text)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT control.owner_connector_request_allowed('pagespeed','GET',"
                "'https://www.googleapis.com/pagespeedonline/v5/runPagespeed')"
            ).fetchone() == (False,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_github_binding_migration_preserves_crawl_audit_revision(admin):
    name = "github_binding_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0038"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.github_read_bindings (id integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0038",)]
            assert (
                connection.execute(
                    "SELECT to_regclass('app.github_read_binding_events')"
                ).fetchone()[0]
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_pr_extension_migration_preserves_github_read_revision(admin):
    name = "pr_extension_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0039"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.github_pr_extensions (id integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0039",)]
            assert (
                connection.execute(
                    "SELECT to_regclass('app.github_pr_extension_events')"
                ).fetchone()[0]
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_candidate_build_migration_preserves_pr_extension_revision(admin):
    name = "candidate_build_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0042"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.candidate_build_intents (id integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0043"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0042",)]
            assert (
                connection.execute("SELECT to_regclass('app.candidate_build_receipts')").fetchone()[
                    0
                ]
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_technical_recipe_migration_preserves_candidate_revision(admin):
    name = "technical_recipe_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0043"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.crawl_page_image_evidence (id integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0044"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0043",)]
            assert (
                connection.execute(
                    "SELECT to_regclass('app.candidate_recipe_revisions')"
                ).fetchone()[0]
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_candidate_inbox_migration_preserves_recipe_revision(admin):
    name = "candidate_inbox_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0052"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.candidate_recipe_review_decisions (id integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0053"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0052",)]
            assert (
                connection.execute(
                    "SELECT to_regclass('app.candidate_recipe_revisions')"
                ).fetchone()[0]
                == "app.candidate_recipe_revisions"
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_scheduler_directory_does_not_grant_direct_outbox_access(api, scopes):
    accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="scheduler")
    with psycopg.connect(os.environ["SIGNAL_TEST_SCHEDULER_DSN"], autocommit=True) as scheduler:
        assert scheduler.execute("SELECT count(*) FROM control.tenant_directory").fetchone()[0] > 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            scheduler.execute("SELECT count(*) FROM app.outbox")
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            scoped_transaction(scheduler, scopes[0]),
        ):
            scheduler.execute("SELECT aggregate_id FROM app.outbox")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), scheduler.transaction():
            scheduler.execute("SELECT * FROM app.commands")


def test_tenant_and_site_insert_policy_denies_wrong_context(scopes):
    with psycopg.connect(os.environ["SIGNAL_TEST_BOOTSTRAP_DSN"], autocommit=True) as bootstrap:
        with scoped_transaction(bootstrap, scopes[0]):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), bootstrap.transaction():
                bootstrap.execute(
                    "INSERT INTO app.tenants (tenant_id, name, home_region) "
                    "VALUES (%s, 'No', 'test')",
                    (uuid4(),),
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege), bootstrap.transaction():
                bootstrap.execute(
                    "INSERT INTO app.sites (tenant_id, id, name, primary_origin, "
                    "timezone, reporting_currency) VALUES (%s, %s, 'No', 'invalid', 'UTC', 'USD')",
                    (scopes[0].tenant_id, scopes[1].site_id),
                )


def test_failed_initial_migration_leaves_no_partial_schema(admin):
    name = "migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
            connection.execute("CREATE TABLE app.sites (intentional_collision integer)")
        env = dict(
            os.environ,
            SIGNAL_MIGRATION_DSN=make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name),
        )
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0
        assert "already exists" in result.stderr
        with psycopg.connect(dsn, autocommit=True) as connection:
            assert connection.execute("SELECT to_regclass('app.tenants')").fetchone()[0] is None
            assert (
                connection.execute("SELECT to_regclass('control.alembic_version')").fetchone()[0]
                is None
            )
            assert connection.execute("SELECT count(*) FROM app.sites").fetchone()[0] == 0
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_identity_migration_preserves_prior_revision_only(admin):
    name = "identity_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        first = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0001"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert first.returncode == 0, first.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE control.users (intentional_collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0001",)]
            assert connection.execute("SELECT to_regclass('app.memberships')").fetchone()[0] is None
            assert (
                connection.execute(
                    "SELECT to_regprocedure('app.current_session_hash()')"
                ).fetchone()[0]
                is None
            )
            assert connection.execute("SELECT count(*) FROM control.users").fetchone()[0] == 0
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_oidc_migration_preserves_identity_revision_only(admin):
    name = "oidc_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0002"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE TABLE control.oidc_login_attempts (intentional_collision integer)"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0002",)]
            assert (
                connection.execute(
                    "SELECT to_regprocedure('control.current_oidc_state_hash()')"
                ).fetchone()[0]
                is None
            )
            assert (
                connection.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0]
                == 0
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_session_issuance_migration_preserves_oidc_revision_only(admin):
    name = "session_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0003"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE FUNCTION control.current_identity_session_hash() RETURNS bytea "
                "LANGUAGE sql AS 'SELECT NULL::bytea'"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0003",)]
            assert connection.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'control' AND c.relname = 'identity_sessions'"
            ).fetchone() == (False, False)
            assert not connection.execute(
                "SELECT has_any_column_privilege("
                "'signal_identity', 'control.identity_sessions', 'INSERT')"
            ).fetchone()[0]
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_identity_audit_migration_preserves_session_revision_only(admin):
    name = "identity_audit_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0004"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE control.platform_events (collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0004",)]
            assert (
                connection.execute("SELECT count(*) FROM control.platform_events").fetchone()[0]
                == 0
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_login_failure_audit_migration_preserves_identity_audit_revision(admin):
    name = "login_failure_audit_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0005"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "ALTER TABLE control.platform_events "
                "DROP CONSTRAINT platform_events_event_type_check"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "does not exist" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0005",)]
            assert connection.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'control' AND table_name = 'platform_events' "
                "AND column_name = 'actor_user_id'"
            ).fetchone() == ("NO",)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_invitation_migration_preserves_login_audit_revision(admin):
    name = "invitation_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0006"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.invitations (collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0006",)]
            assert (
                connection.execute(
                    "SELECT to_regprocedure('app.current_actor_user_id()')"
                ).fetchone()[0]
                is None
            )
            assert connection.execute("SELECT count(*) FROM app.invitations").fetchone() == (0,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_acceptance_migration_preserves_invitation_revision(admin):
    name = "acceptance_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0007"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE control.invitation_routes (collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0007",)]
            assert (
                connection.execute(
                    "SELECT to_regprocedure("
                    "'control.accept_site_invitation(uuid,bytea,text,text,text,text,"
                    "uuid,uuid,uuid,uuid)')"
                ).fetchone()[0]
                is None
            )
            assert (
                connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'app' AND table_name = 'invitations' "
                    "AND column_name = 'accepted_user_id'"
                ).fetchone()
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_membership_directory_migration_preserves_acceptance_revision(admin):
    name = "membership_directory_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0008"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE control.user_membership_routes (collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0008",)]
            assert (
                connection.execute(
                    "SELECT to_regprocedure('control.list_identity_memberships(bytea,text)')"
                ).fetchone()[0]
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_membership_directory_migration_backfills_existing_membership(admin):
    name = "membership_directory_backfill_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0008"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        tenant_id = uuid4()
        user_id = uuid4()
        membership_id = uuid4()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Existing tenant', 'test')",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO control.tenant_directory VALUES (%s, 'active', 1)",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO control.users "
                "(id, oidc_issuer, oidc_subject, display_name) "
                "VALUES (%s, 'https://identity.example.test', %s, 'Existing user')",
                (user_id, f"subject-{user_id}"),
            )
            connection.execute(
                "INSERT INTO app.memberships "
                "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
                "VALUES (%s, %s, %s, 'viewer', 'active', 1)",
                (tenant_id, membership_id, user_id),
            )
        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert upgraded.returncode == 0, upgraded.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT tenant_id, membership_id, user_id FROM control.user_membership_routes"
            ).fetchall() == [(tenant_id, membership_id, user_id)]
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0103",)]
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_browser_session_revocation_migration_preserves_directory_revision(admin):
    name = "browser_session_revocation_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0009"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE FUNCTION control.revoke_browser_session(bytea, text, uuid) "
                "RETURNS boolean LANGUAGE sql AS 'SELECT false'"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0009",)]
            assert (
                connection.execute(
                    "SELECT event_type FROM control.platform_events "
                    "WHERE event_type = 'identity.session.revoked'"
                ).fetchall()
                == []
            )
            assert (
                connection.execute(
                    "SELECT policyname FROM pg_policies WHERE schemaname = 'app' "
                    "AND tablename = 'sessions' AND policyname = 'session_exact_revoke_scope'"
                ).fetchone()
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_invitation_identity_proof_migration_preserves_revocation_revision(admin):
    name = "invitation_identity_proof_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0010"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE TABLE control.invitation_identity_proofs (collision integer)"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0010",)]
            assert (
                connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'control' AND table_name = 'oidc_login_attempts' "
                    "AND column_name = 'purpose'"
                ).fetchone()
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_atomic_proof_acceptance_migration_preserves_identity_proof_revision(admin):
    name = "atomic_proof_acceptance_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0011"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE FUNCTION control.guard_invitation_identity_proof_transition() "
                "RETURNS trigger LANGUAGE plpgsql AS "
                "'BEGIN RETURN NEW; END'"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0011",)]
            assert connection.execute(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'invitation_identity_proofs_immutable' "
                "AND NOT tgisinternal"
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT has_function_privilege("
                "'signal_identity', "
                "'control.accept_site_invitation(uuid,bytea,text,text,text,text,"
                "uuid,uuid,uuid,uuid)', 'EXECUTE')"
            ).fetchone() == (True,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_invitation_completion_audit_migration_preserves_proof_revision(admin):
    name = "invitation_completion_audit_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0012"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "ALTER TABLE control.platform_events "
                "RENAME CONSTRAINT platform_events_contract_check "
                "TO unexpected_platform_events_contract_check"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0
        assert "does not exist" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0012",)]
            assert connection.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'control.platform_events'::regclass "
                "AND conname = 'unexpected_platform_events_contract_check'"
            ).fetchone() == ("unexpected_platform_events_contract_check",)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_authority_journal_migration_preserves_prior_revision(admin):
    name = "authority_journal_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0040"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE TABLE control.authority_restriction_outbox (collision integer)"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0041"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0040",)]
            assert connection.execute(
                "SELECT to_regclass('control.authority_denial_tombstones')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_recipe_registry_migration_preserves_journal_revision(admin):
    name = "recipe_registry_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0041"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE control.recipe_releases (collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0042"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0041",)]
            assert connection.execute(
                "SELECT to_regclass('control.recipe_release_events')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_autonomy_gate_migration_preserves_standing_revision(admin):
    name = "autonomy_gate_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0049"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.autonomy_gate_records (collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0050"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0049",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.autonomy_gate_preflight("
                "uuid,uuid,uuid,text,uuid,text,text,bigint)')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_weekly_loop_migration_preserves_gate_revision(admin):
    name = "weekly_loop_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0050"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.weekly_cycles (collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0051"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0050",)]
            assert connection.execute(
                "SELECT to_regclass('app.site_weekly_control')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_human_command_migration_preserves_existing_service_intent(admin):
    name = "human_command_migration_upgrade_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0013"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert prior.returncode == 0, prior.stderr
        tenant_id, site_id, command_id = uuid4(), uuid4(), uuid4()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Existing tenant', 'test')",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO app.sites (tenant_id, id, name, primary_origin, timezone, "
                "reporting_currency) VALUES (%s, %s, 'Existing site', "
                "'https://example.invalid', 'UTC', 'USD')",
                (tenant_id, site_id),
            )
            connection.execute(
                "INSERT INTO app.commands "
                "(tenant_id, id, site_id, actor_service, kind, schema_version, principal_key, "
                "route_key, scope_kind, idempotency_key, request_fingerprint, payload) "
                "VALUES (%s, %s, %s, 'existing', 'site.snapshot', 1, 'service:existing', "
                "'internal.site.snapshot', 'site', 'before-upgrade', %s, "
                "'{\"schema_version\":1}')",
                (tenant_id, command_id, site_id, b"x" * 32),
            )
        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert upgraded.returncode == 0, upgraded.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT actor_service, actor_user_id, principal_key, route_key "
                "FROM app.commands WHERE tenant_id = %s AND id = %s",
                (tenant_id, command_id),
            ).fetchone() == (
                "existing",
                None,
                "service:existing",
                "internal.site.snapshot",
            )
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0103",)]
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_outbox_dispatch_migration_preserves_human_command_revision(admin):
    name = "outbox_dispatch_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0014"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE FUNCTION app.guard_outbox_delivery_mutation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0014",)]
            assert connection.execute(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'app.outbox'::regclass AND tgname = 'outbox_immutable'"
            ).fetchone() == ("outbox_immutable",)
            assert (
                connection.execute(
                    "SELECT to_regprocedure("
                    "'control.claim_outbox_batch(uuid,text,integer,integer)')"
                ).fetchone()[0]
                is None
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_outbox_dispatch_migration_preserves_and_claims_pending_message(admin):
    name = "outbox_dispatch_upgrade_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        scheduler_dsn = make_conninfo(os.environ["SIGNAL_TEST_SCHEDULER_DSN"], dbname=name)
        workflow_dsn = make_conninfo(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
            connection.execute("GRANT USAGE ON SCHEMA app, control TO signal_scheduler")
            connection.execute("GRANT USAGE ON SCHEMA control TO signal_workflow")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0014"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        tenant_id, site_id = uuid4(), uuid4()
        command_id, event_id, outbox_id = uuid4(), uuid4(), uuid4()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Existing tenant', 'test')",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO control.tenant_directory VALUES (%s, 'active', 1)",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO app.sites (tenant_id, id, name, primary_origin, timezone, "
                "reporting_currency) VALUES (%s, %s, 'Existing site', "
                "'https://example.invalid', 'UTC', 'USD')",
                (tenant_id, site_id),
            )
            connection.execute(
                "INSERT INTO app.commands "
                "(tenant_id, id, site_id, actor_service, kind, schema_version, principal_key, "
                "route_key, scope_kind, idempotency_key, request_fingerprint, payload) "
                "VALUES (%s, %s, %s, 'existing', 'site.snapshot', 1, 'service:existing', "
                "'internal.site.snapshot', 'site', 'pending-upgrade', %s, "
                "'{\"schema_version\":1}')",
                (tenant_id, command_id, site_id, b"x" * 32),
            )
            connection.execute(
                "INSERT INTO app.command_events "
                "(tenant_id, site_id, id, command_id, event_number, event_type, facts) "
                "VALUES (%s, %s, %s, %s, 1, 'command.accepted', "
                "'{\"schema_version\":1}')",
                (tenant_id, site_id, event_id, command_id),
            )
            connection.execute(
                "INSERT INTO app.outbox "
                "(tenant_id, site_id, id, event_id, aggregate_kind, aggregate_id, "
                "event_type, schema_version, payload) "
                "VALUES (%s, %s, %s, %s, 'command', %s, 'command.accepted', 1, "
                "'{\"schema_version\":1}')",
                (tenant_id, site_id, outbox_id, event_id, command_id),
            )
        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert upgraded.returncode == 0, upgraded.stderr
        with psycopg.connect(scheduler_dsn, autocommit=True) as scheduler:
            claimed = scheduler.execute(
                "SELECT outbox_id, event_id, site_id, command_id, attempt_count "
                "FROM control.claim_outbox_batch(%s, 'worker.upgrade', 1, 30)",
                (tenant_id,),
            ).fetchone()
        with psycopg.connect(workflow_dsn, autocommit=True) as workflow:
            admitted = workflow.execute(
                "SELECT command_id, site_id, workflow_id, state_projection, duplicate, outcome "
                "FROM control.admit_command_event(" + ", ".join(["%s"] * 10) + ")",
                (
                    tenant_id,
                    outbox_id,
                    event_id,
                    site_id,
                    command_id,
                    "command",
                    "command.accepted",
                    1,
                    '{"schema_version":1}',
                    "workflow.command-start.v1",
                ),
            ).fetchone()
        assert claimed == (outbox_id, event_id, site_id, command_id, 1)
        assert admitted == (
            command_id,
            site_id,
            f"signal:CrawlSite:{tenant_id}:{command_id}",
            "admitted",
            False,
            "admitted",
        )
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0103",)]
            assert connection.execute(
                "SELECT status, row_version FROM app.commands WHERE tenant_id = %s AND id = %s",
                (tenant_id, command_id),
            ).fetchone() == ("workflow_admitted", 2)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_workflow_admission_migration_preserves_outbox_revision(admin):
    name = "workflow_admission_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0015"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.consumer_inbox (intentional_collision integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0015",)]
            assert connection.execute("SELECT to_regclass('app.workflow_refs')").fetchone() == (
                None,
            )
            assert connection.execute(
                "SELECT to_regprocedure("
                "'control.admit_command_event(uuid,uuid,uuid,uuid,uuid,text,text,integer,jsonb,text)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'app.commands'::regclass "
                "AND tgname = 'commands_immutable'"
            ).fetchone() == ("commands_immutable",)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_workflow_start_migration_preserves_admitted_projection(admin):
    name = "workflow_start_upgrade_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        workflow_dsn = make_conninfo(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
            connection.execute("GRANT USAGE ON SCHEMA control TO signal_workflow")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0016"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        tenant_id, site_id = uuid4(), uuid4()
        command_id, event_id, outbox_id = uuid4(), uuid4(), uuid4()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Existing tenant', 'test')",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO control.tenant_directory VALUES (%s, 'active', 1)",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO app.sites (tenant_id, id, name, primary_origin, timezone, "
                "reporting_currency) VALUES (%s, %s, 'Existing site', "
                "'https://example.invalid', 'UTC', 'USD')",
                (tenant_id, site_id),
            )
            connection.execute(
                "INSERT INTO app.commands "
                "(tenant_id, id, site_id, actor_service, kind, schema_version, principal_key, "
                "route_key, scope_kind, idempotency_key, request_fingerprint, payload) "
                "VALUES (%s, %s, %s, 'existing', 'site.snapshot', 1, 'service:existing', "
                "'internal.site.snapshot', 'site', 'admitted-before-upgrade', %s, "
                "'{\"schema_version\":1}')",
                (tenant_id, command_id, site_id, b"x" * 32),
            )
            connection.execute(
                "INSERT INTO app.command_events "
                "(tenant_id, site_id, id, command_id, event_number, event_type, facts) "
                "VALUES (%s, %s, %s, %s, 1, 'command.accepted', "
                "'{\"schema_version\":1}')",
                (tenant_id, site_id, event_id, command_id),
            )
            connection.execute(
                "INSERT INTO app.outbox "
                "(tenant_id, site_id, id, event_id, aggregate_kind, aggregate_id, "
                "event_type, schema_version, payload) "
                "VALUES (%s, %s, %s, %s, 'command', %s, 'command.accepted', 1, "
                "'{\"schema_version\":1}')",
                (tenant_id, site_id, outbox_id, event_id, command_id),
            )
        workflow_id = f"signal:CrawlSite:{tenant_id}:{command_id}"
        with psycopg.connect(workflow_dsn, autocommit=True) as workflow:
            admitted = workflow.execute(
                "SELECT workflow_id, state_projection, outcome "
                "FROM control.admit_command_event(" + ", ".join(["%s"] * 10) + ")",
                (
                    tenant_id,
                    outbox_id,
                    event_id,
                    site_id,
                    command_id,
                    "command",
                    "command.accepted",
                    1,
                    '{"schema_version":1}',
                    "workflow.command-start.v1",
                ),
            ).fetchone()
        assert admitted == (workflow_id, "admitted", "admitted")

        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert upgraded.returncode == 0, upgraded.stderr
        run_id = str(uuid4())
        with psycopg.connect(workflow_dsn, autocommit=True) as workflow:
            recorded = workflow.execute(
                "SELECT first_run_id, state_projection, duplicate, outcome "
                "FROM control.record_workflow_started(%s, %s, %s, %s, %s, %s, %s)",
                (
                    tenant_id,
                    site_id,
                    command_id,
                    workflow_id,
                    run_id,
                    "start_acknowledged",
                    uuid4(),
                ),
            ).fetchone()
        assert recorded == (run_id, "running", False, "recorded")
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0103",)]
            assert connection.execute(
                "SELECT status, row_version FROM app.commands WHERE tenant_id = %s AND id = %s",
                (tenant_id, command_id),
            ).fetchone() == ("processing", 3)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_workflow_start_migration_preserves_admission_revision(admin):
    name = "workflow_start_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0016"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "ALTER TABLE app.workflow_refs "
                "RENAME CONSTRAINT workflow_refs_state_projection_check "
                "TO unexpected_workflow_state_check"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "does not exist" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0016",)]
            assert connection.execute(
                "SELECT to_regprocedure("
                "'control.record_workflow_started(uuid,uuid,uuid,text,text,text,uuid)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'app.workflow_refs'::regclass "
                "AND tgname = 'workflow_refs_admission_immutable'"
            ).fetchone() == ("workflow_refs_admission_immutable",)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_stable_admission_migration_preserves_workflow_start_revision(admin):
    name = "stable_admission_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0017"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        signature = (
            "control.admit_command_event(uuid,uuid,uuid,uuid,uuid,text,text,integer,jsonb,text)"
        )
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("ALTER FUNCTION {} OWNER TO postgres").format(sql.SQL(signature))
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "must be owner" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0017",)]
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_terminal_projection_migration_preserves_stable_admission_revision(admin):
    name = "terminal_projection_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0018"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "ALTER FUNCTION app.guard_command_progress_mutation() OWNER TO postgres"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "must be owner" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0018",)]
            status_constraint = connection.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'app.commands'::regclass "
                "AND conname = 'commands_status_check'"
            ).fetchone()[0]
            assert "processing" in status_constraint
            assert "succeeded" not in status_constraint
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_crawl_frontier_migration_preserves_terminal_projection_revision(admin):
    name = "crawl_frontier_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0019"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.crawl_runs (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0019",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.valid_crawl_scope_snapshot(jsonb)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regclass('app.crawl_frontier_leases')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'app.workflow_refs'::regclass "
                "AND conname = 'workflow_refs_exact_run_reference'"
            ).fetchone() == (0,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_crawl_artifact_migration_preserves_frontier_revision(admin):
    name = "crawl_artifact_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0020"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.artifacts (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0020",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.valid_fetch_headers(jsonb)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regclass('app.fetch_observations')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'app.crawl_frontier'::regclass "
                "AND conname = 'crawl_frontier_exact_fetch_identity'"
            ).fetchone() == (0,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_robots_snapshot_migration_preserves_artifact_revision(admin):
    name = "robots_snapshot_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0021"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.robots_snapshots (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0021",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.get_current_robots_snapshot("
                "uuid,uuid,uuid,text,bytea,timestamp with time zone)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regprocedure('app.guard_robots_snapshot_mutation()')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_global_origin_admission_migration_preserves_robots_revision(admin):
    name = "global_origin_admission_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0022"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE control.origin_buckets (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0022",)]
            assert connection.execute(
                "SELECT to_regclass('control.admission_leases')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regprocedure('control.acquire_origin_permit("
                "uuid,uuid,uuid,uuid,uuid,text,uuid,text,integer,text,text,integer,integer)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regprocedure('control.reconcile_expired_origin_permits(integer)')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_crawl_page_attempt_migration_preserves_origin_admission_revision(admin):
    name = "crawl_page_attempt_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0023"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.crawl_page_attempts (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0023",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.begin_crawl_page_attempt("
                "uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT to_regprocedure('control.get_fetch_observation_for_lease("
                "uuid,uuid,uuid,uuid,uuid,text)')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_site_directory_migration_backfills_existing_site_membership(admin):
    name = "site_directory_backfill_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0024"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        tenant_id, site_id, user_id = uuid4(), uuid4(), uuid4()
        membership_id, site_membership_id = uuid4(), uuid4()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Existing tenant', 'test')",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO app.sites (tenant_id, id, name, primary_origin, timezone, "
                "reporting_currency) VALUES (%s, %s, 'Existing site', "
                "'https://example.invalid', 'UTC', 'USD')",
                (tenant_id, site_id),
            )
            connection.execute(
                "INSERT INTO control.users "
                "(id, oidc_issuer, oidc_subject, display_name) "
                "VALUES (%s, 'https://identity.example.invalid', %s, 'Existing user')",
                (user_id, f"subject-{user_id}"),
            )
            connection.execute(
                "INSERT INTO app.memberships "
                "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
                "VALUES (%s, %s, %s, 'viewer', 'active', 1)",
                (tenant_id, membership_id, user_id),
            )
            connection.execute(
                "INSERT INTO app.site_memberships "
                "(tenant_id, site_id, id, user_id, permission_set, authorization_epoch, state) "
                "VALUES (%s, %s, %s, %s, %s, 1, 'active')",
                (
                    tenant_id,
                    site_id,
                    site_membership_id,
                    user_id,
                    '{"permissions":["site.snapshot.request"],"schema_version":1}',
                ),
            )
        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert upgraded.returncode == 0, upgraded.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT tenant_id, site_id, site_membership_id, user_id "
                "FROM control.user_site_membership_routes"
            ).fetchall() == [(tenant_id, site_id, site_membership_id, user_id)]
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0103",)]
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_site_directory_migration_preserves_page_attempt_revision(admin):
    name = "site_directory_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0024"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE TABLE control.user_site_membership_routes (placeholder integer)"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0024",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.list_tenant_sites(bytea,text)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'site_membership_route_after_change'"
            ).fetchone() == (0,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_site_onboarding_migration_backfills_and_tracks_existing_site(admin):
    name = "site_onboarding_backfill_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0026"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        tenant_id, site_id = uuid4(), uuid4()
        historical_origin = "https://Example.invalid/legacy"
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Existing tenant', 'test')",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO app.sites (tenant_id, id, name, primary_origin, timezone, "
                "reporting_currency) VALUES (%s, %s, 'Historical site', %s, 'UTC', 'USD')",
                (tenant_id, site_id, historical_origin),
            )
        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert upgraded.returncode == 0, upgraded.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT primary_origin, state FROM control.tenant_site_routes "
                "WHERE tenant_id = %s AND site_id = %s",
                (tenant_id, site_id),
            ).fetchone() == (historical_origin, "onboarding")
            connection.execute(
                "UPDATE app.sites SET state = 'archived' WHERE tenant_id = %s AND id = %s",
                (tenant_id, site_id),
            )
            assert connection.execute(
                "SELECT state FROM control.tenant_site_routes "
                "WHERE tenant_id = %s AND site_id = %s",
                (tenant_id, site_id),
            ).fetchone() == ("archived",)
            connection.execute(
                "DELETE FROM app.sites WHERE tenant_id = %s AND id = %s",
                (tenant_id, site_id),
            )
            assert connection.execute(
                "SELECT count(*) FROM control.tenant_site_routes "
                "WHERE tenant_id = %s AND site_id = %s",
                (tenant_id, site_id),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0103",)]
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_site_onboarding_migration_preserves_active_site_revision(admin):
    name = "site_onboarding_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0026"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.site_onboarding_events (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0026",)]
            assert connection.execute(
                "SELECT to_regclass('control.tenant_site_routes')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'site_onboarding_events'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.onboard_site(bytea,text,uuid,uuid,uuid,"
                "uuid,uuid,bytea,text,text,text,text,bigint,integer)')"
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'tenant_site_route_after_change'"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT relforcerowsecurity FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = 'app' AND c.relname = 'sites'"
            ).fetchone() == (True,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_origin_verification_migration_preserves_onboarding_revision(admin):
    name = "origin_verification_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0027"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.site_origin_challenges (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0027",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'site_origin_challenges'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regclass('app.site_origin_verification_attempts'), "
                "to_regclass('app.site_origin_verifications'), "
                "to_regclass('control.public_origin_claims')"
            ).fetchone() == (None, None, None)
            assert connection.execute(
                "SELECT to_regprocedure('control.issue_site_origin_challenge(bytea,text,"
                "uuid,uuid,uuid,bytea,text,integer,integer)')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_latest_snapshot_command_migration_preserves_origin_revision(admin):
    name = "latest_snapshot_command_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0028"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE FUNCTION control.read_latest_authenticated_snapshot_command("
                "bytea, uuid, text) RETURNS integer LANGUAGE sql AS 'SELECT 7'"
            )
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0028",)]
            assert connection.execute(
                "SELECT control.read_latest_authenticated_snapshot_command("
                "decode('00', 'hex'), %s, 'generation')",
                (uuid4(),),
            ).fetchone() == (7,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_audit_evidence_migration_preserves_latest_snapshot_revision(admin):
    name = "audit_evidence_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0029"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.evidence_records (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0029",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'evidence_records'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regclass('app.findings'), to_regclass('app.finding_evidence'), "
                "to_regprocedure('control.read_authenticated_findings(bytea,uuid,text)')"
            ).fetchone() == (None, None, None)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_proposal_migration_preserves_audit_evidence_revision(admin):
    name = "proposal_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0030"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.proposals (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0030",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'proposals'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regclass('app.proposal_revisions'), "
                "to_regclass('app.approval_requests'), "
                "to_regclass('app.approval_decisions'), "
                "to_regprocedure('control.read_authenticated_local_fixture_proposals("
                "bytea,uuid,text)')"
            ).fetchone() == (None, None, None, None)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_model_run_migration_preserves_proposal_revision(admin):
    name = "model_run_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0031"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.agent_runs (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0031",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'agent_runs'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regclass('app.model_calls'), "
                "to_regprocedure('control.begin_authenticated_fixture_model_run("
                "bytea,uuid,text,uuid,uuid,uuid,uuid,uuid,bytea,bytea)')"
            ).fetchone() == (None, None)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_decision_record_migration_preserves_verified_model_revision(admin):
    name = "decision_record_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0034"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.decision_records (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0034",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'decision_records'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.record_decision_recommendation("
                "uuid,uuid,uuid,text,text,text,text,bytea,bytea,jsonb,jsonb,numeric,numeric,"
                "text,text,boolean,text)')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_shared_egress_migration_preserves_decision_record_revision(admin):
    name = "shared_egress_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0035"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.egress_operations (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0035",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'egress_operations'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.begin_shared_egress_operation("
                "uuid,uuid,uuid,text,uuid,text,text,text,text,bytea,bytea,integer,integer,"
                "boolean,uuid,text,integer,integer,integer)'), "
                "to_regprocedure('control.finish_shared_egress_operation("
                "uuid,uuid,uuid,text,uuid,uuid,text,integer,bytea,uuid,text,integer,jsonb,"
                "bytea,integer,text,inet,text,integer,integer)')"
            ).fetchone() == (None, None)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_full_site_crawl_migration_preserves_shared_egress_revision(admin):
    name = "full_site_crawl_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0036"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.crawl_manifests (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0036",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'crawl_manifests'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regclass('app.crawl_robots_dispatches'), "
                "to_regprocedure('control.finalize_crawl_run(uuid,uuid,uuid,uuid,bytea)')"
            ).fetchone() == (None, None)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_failed_crawl_audit_migration_preserves_full_crawl_revision(admin):
    name = "crawl_audit_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        prior = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "0037"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.crawl_audit_reports (placeholder integer)")
        failed = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert failed.returncode != 0
        assert "already exists" in failed.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0037",)]
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND table_name = 'crawl_audit_reports'"
            ).fetchall() == [("placeholder",)]
            assert connection.execute(
                "SELECT to_regprocedure('control.load_crawl_audit_inputs(uuid,uuid,uuid)')"
            ).fetchone() == (None,)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
