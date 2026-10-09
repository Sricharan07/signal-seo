"""Independent legacy decisions and normalized owner/worker divergence checks."""

import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

BASELINE = json.loads(
    Path(__file__).with_name("fixtures").joinpath("shared_permission_0100.json").read_text()
)
SAMPLES = {f[0] for f in BASELINE["functions"]}
GROUPS = [g for g in BASELINE["groups"] if g["owner"] in SAMPLES]
RUNTIME = (
    "signal_api",
    "signal_identity",
    "signal_workflow",
    "signal_scheduler",
    "signal_bootstrap",
    "signal_crawl_admission",
    "signal_crawl_ingest",
    "signal_authority_dispatcher",
    "signal_release_manager",
)
MIGRATIONS = Path(__file__).resolve().parents[2] / "database/migrations/versions"
LEGACY_PERMISSION = (MIGRATIONS / "0101_shared_permission_check.sql").read_text()
FUNCTIONS_0101 = {
    match[1]: match[0]
    for match in re.finditer(
        r"CREATE OR REPLACE FUNCTION control\.(\w+)\([\s\S]*?AS (\$\w*\$)[\s\S]*?\2;",
        LEGACY_PERMISSION,
    )
}
NULLABLE_CORES = {
    name
    for name, definition in FUNCTIONS_0101.items()
    if re.search(r"port_permission\([^;]*,false\)", definition)
}
NULLABLE_GROUPS = [g for g in BASELINE["groups"] if "port_" + g["owner"] in NULLABLE_CORES]
for name in sorted(NULLABLE_CORES - {"port_" + g["owner"] for g in NULLABLE_GROUPS}):
    arguments = re.match(
        r"CREATE OR REPLACE FUNCTION control\.\w+\(([^)]*)\)", FUNCTIONS_0101[name]
    )[1]
    NULLABLE_GROUPS.append(
        {"owner": name, "variants": [{"name": name, "actor": "owner", "arguments": arguments}]}
    )


def old_name(name):
    return "permission_old_" + hashlib.sha256(name.encode()).hexdigest()[:20]


def test_all_entrypoints_are_literal_actor_wrappers_with_unchanged_acl(admin):
    for group in BASELINE["groups"]:
        for variant in group["variants"]:
            name = variant["name"]
            row = admin.execute(
                "SELECT p.prosrc,p.prosecdef,p.proconfig,r.rolname FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner "
                "WHERE n.nspname='control' AND p.proname=%s",
                (name,),
            ).fetchone()
            assert row[1:] == (True, ["search_path=pg_catalog"], "signal_migrator")
            body = re.sub(r"\s+", " ", row[0]).strip()
            assert re.fullmatch(
                r"BEGIN RETURN (?:QUERY SELECT \* FROM )?control\.port_"
                + re.escape(group["owner"])
                + r"\('"
                + re.escape(variant["actor"])
                + r"',[a-z_0-9,NULL ]+\); END;",
                body,
            ), (name, body)
            before = sorted((a[2], a[3], a[4]) for a in BASELINE["acl"] if a[0] == name)
            after = admin.execute(
                "SELECT COALESCE(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "CROSS JOIN LATERAL aclexplode(p.proacl) a LEFT JOIN pg_roles r ON r.oid=a.grantee "
                "WHERE n.nspname='control' AND p.proname=%s ORDER BY 1,2,3",
                (name,),
            ).fetchall()
            assert after == before, name


def test_private_permission_and_cores_have_no_runtime_execution(admin):
    functions = admin.execute(
        "SELECT p.oid,p.proname,p.prosecdef,p.proconfig,r.rolname "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname='control' "
        "AND (p.proname LIKE 'port_%%' OR p.proname='admit_port_context')"
    ).fetchall()
    assert len(functions) == len(BASELINE["groups"]) + 5
    for oid, name, secure, config, owner in functions:
        assert (secure, config, owner) == (True, ["search_path=pg_catalog"], "signal_migrator")
        for role in RUNTIME:
            assert not admin.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, oid)
            ).fetchone()[0], (role, name)
    assert admin.execute(
        "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
        "CROSS JOIN LATERAL aclexplode(t.typacl) a WHERE n.nspname='control' "
        "AND t.typname='port_authority_context' AND a.grantee=0"
    ).fetchone() == (0,)


def test_operation_cores_and_owner_helpers_have_no_separate_permission_formula(admin):
    definitions = admin.execute(
        "SELECT p.proname,p.prosrc FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' "
        "AND (p.proname LIKE 'port_%%' OR p.proname=ANY(%s))",
        (
            [
                "business_brain_owner",
                "brand_document_owner",
                "ga4_owner_context",
                "gsc_owner_context",
                "assistant_scope",
            ],
        ),
    ).fetchall()
    separate_formula = re.compile(
        r"\b\w+\.(?:outcome|role_key)\s*"
        r"(?:IS (?:NOT )?DISTINCT FROM|<>|!=|=)\s*'(?:authorized|owner|workload)'"
    )
    for name, definition in definitions:
        assert separate_formula.search(definition) is None, name
        if not name.startswith("port_"):
            assert "control.port_permission(" in definition, name


def test_permission_is_total_and_only_reduces_legacy_authority(admin):
    roles = ("owner", "workload", "viewer", "analyst", "editor", "approver", "admin")
    for outcome, role, required in itertools.product(
        (
            "authorized",
            "invalid_session",
            "authorization_denied",
            "authority_changed",
            "unknown",
            "",
            None,
        ),
        (*roles, "unknown", "", None),
        ("owner", "workload", "unknown", "", None),
    ):
        expected = (
            outcome == "authorized" and required in ("owner", "workload") and role == required
        )
        actual = admin.execute(
            "SELECT control.port_permission(%s,%s,%s)", (outcome, role, required)
        ).fetchone()[0]
        assert actual is expected, (outcome, role, required)
        old = admin.execute(
            "SELECT (%s = 'authorized') AND (%s::text IS NULL OR %s = %s)",
            (outcome, required, role, required),
        ).fetchone()[0]
        assert not actual or old is True
        if required in ("owner", "workload") and role in roles and outcome is not None:
            assert actual == old
        assert admin.execute("SELECT control.port_permission(%s,%s)", (outcome, role)).fetchone()[
            0
        ] is (outcome == "authorized" and role in roles)
        assert admin.execute("SELECT control.port_permission(%s)", (outcome,)).fetchone()[0] is (
            outcome == "authorized"
        )
    assert admin.execute(
        "SELECT to_regprocedure('control.port_permission(text,text,text,boolean)')"
    ).fetchone() == (None,)


def test_runtime_cannot_choose_an_actor_or_invoke_private_policy(api, workflow):
    for connection in (api, workflow):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT control.port_permission('authorized','owner','owner')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "SELECT control.port_read_github_base_risk('owner',%s,%s,%s,%s)",
                (bytes.fromhex("ab" * 32), uuid4(), "synthetic-generation", uuid4()),
            )


def test_all_permission_callers_are_forward_replaced_without_domain_changes(admin):
    # This mechanically checks every literal body against the immutable migration,
    # not a freshly generated expected implementation.
    old_calls = re.compile(r"control\.port_permission\(([^\n;]*?),(true|false)\)")
    new_source = (MIGRATIONS / "0102_fail_closed_permission.sql").read_text()
    for name, old in FUNCTIONS_0101.items():
        if "control.port_permission(" not in old:
            continue
        body = admin.execute(
            "SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='control' AND p.proname=%s",
            (name,),
        ).fetchone()[0]
        # 0103 (Ask Signal grounded prose) later swaps the pending-reply guard in the
        # budget ports for control.assistant_model_pending; restore it so this test
        # compares only 0102's permission rewrite. 0103 is verified by its own tests.
        body = body.replace(
            "control.assistant_model_pending(p_id)",
            "EXISTS(SELECT 1 FROM app.assistant_messages WHERE id=p_id"
            " AND role='signal' AND NOT finalized)",
        )
        for admission in re.finditer(
            r"SELECT \* INTO (\w+) FROM control\.(admit_port_context|resolve_snapshot_authority)"
            r"\([\s\S]*?\);",
            body,
        ):
            if admission[2] == "admit_port_context" and "'authority'" not in admission[0]:
                continue
            variable = admission[1]
            expected_guard = (
                f"\n    IF control.port_permission({variable}.outcome)\n"
                f"        AND NOT control.port_permission({variable}.outcome,"
                f"{variable}.role_key) THEN\n"
                f"        {variable}.outcome := 'authorization_denied';\n    END IF;"
            )
            assert body[admission.end() :].startswith(expected_guard), name
        body = re.sub(
            r"\n    IF control\.port_permission\((\w+)\.outcome\)\n"
            r"        AND NOT control\.port_permission\(\1\.outcome,\1\.role_key\) THEN\n"
            r"        \1\.outcome := 'authorization_denied';\n    END IF;",
            "",
            body,
        )
        if name == "port_github_pr_operation_eligible":
            extra_guard = """
    IF NOT control.port_permission(a.outcome,a.role_key) THEN
        RETURN QUERY SELECT coalesce(a.outcome,'authorization_denied'),NULL::uuid,
            NULL::uuid,NULL::bigint,NULL::bigint; RETURN;
    END IF;"""
            assert extra_guard in body
            assert body.index(extra_guard) < body.index("control.github_pr_eligible_before_astro(")
            body = body.replace(extra_guard, "", 1)
        old_body = re.search(r"AS (\$\w*\$)([\s\S]*)\1;", old)[2].replace("%%", "%")
        old_body = old_calls.sub("PERMISSION_CHECK", old_body)
        # Arguments can contain a nested port_required_role call but no other
        # parentheses except CASE guards; consume that closed helper explicitly.
        new_calls = re.compile(
            r"control\.port_permission\((?:[^()\n;]|control\.port_required_role\(p_actor\))*\)"
        )
        body = new_calls.sub("PERMISSION_CHECK", body)
        assert body == old_body, name
        assert "FUNCTION control." + name + "(" in new_source
    assert not admin.execute(
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='control' AND p.prosrc LIKE '%%port_permission%%' "
        "AND p.prosrc ~ 'port_permission\\([^;]*,(true|false)\\)'"
    ).fetchall()


@pytest.mark.parametrize("prior_revision,target_revision", [("0100", "0101"), ("0101", "0102")])
def test_mid_permission_migration_failure_restores_exact_entrypoint(
    admin, prior_revision, target_revision
):
    name = "permission_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        command = [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade"]
        prior = subprocess.run(
            [*command, prior_revision], env=env, capture_output=True, text=True, timeout=30
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            before = connection.execute(
                "SELECT pg_get_functiondef("
                "'control.read_github_base_risk(bytea,uuid,text,uuid)'::regprocedure)"
            ).fetchone()[0]
            collision = (
                "control.port_read_github_base_risk(text,bytea,uuid,text,uuid)"
                if target_revision == "0101"
                else "control.port_permission(text,text)"
            )
            connection.execute(
                "CREATE FUNCTION " + collision + " RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"
            )
        failed = subprocess.run(
            [*command, target_revision], env=env, capture_output=True, text=True, timeout=30
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchone() == (prior_revision,)
            if target_revision == "0101":
                assert connection.execute(
                    "SELECT to_regtype('control.port_authority_context')"
                ).fetchone() == (None,)
            else:
                assert connection.execute(
                    "SELECT to_regprocedure('control.port_permission(text)'),"
                    "to_regprocedure('control.port_permission(text,text,text,boolean)') IS NOT NULL"
                ).fetchone() == (None, True)
            assert (
                connection.execute(
                    "SELECT pg_get_functiondef("
                    "'control.read_github_base_risk(bytea,uuid,text,uuid)'::regprocedure)"
                ).fetchone()[0]
                == before
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture
def normalized_admission(admin, identity_context, scopes):
    # Only this rollback-owned fixture replaces credential admission. Real handle,
    # stage, resource, generation and revocation admission remains lab-qualified.
    with admin.transaction(force_rollback=True):
        identity_context = {**identity_context, "scope": scopes[0], "resource_id": uuid4()}
        for key, value in {
            "tenant": identity_context["scope"].tenant_id,
            "user": identity_context["user_id"],
            "site": identity_context["scope"].site_id,
            "generation": identity_context["generation"],
        }.items():
            admin.execute("SELECT set_config(%s,%s,true)", ("permission_test." + key, str(value)))
        admin.execute(
            "SELECT set_config('signal.tenant_id',%s,true),set_config('signal.site_id',%s,true)",
            (str(identity_context["scope"].tenant_id), str(identity_context["scope"].site_id)),
        )
        admin.execute("""
            CREATE FUNCTION control.permission_test_authority(p_actor text,p_hash bytea,
                p_site uuid,p_generation text,p_mode text DEFAULT NULL)
            RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,role_key text,
                authentication_level text,membership_epoch bigint,site_authorization_epoch bigint)
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
            BEGIN
                IF current_setting('permission_test.failure',true)='yes' THEN
                    RAISE EXCEPTION 'synthetic-admission-failure' USING ERRCODE='40001';
                END IF;
                IF p_site::text IS DISTINCT FROM current_setting('permission_test.site')
                    OR p_generation IS DISTINCT FROM current_setting('permission_test.generation')
                    OR octet_length(p_hash) IS DISTINCT FROM 32 THEN
                    RAISE EXCEPTION 'synthetic-scope-denied' USING ERRCODE='42501';
                END IF;
                RETURN QUERY SELECT nullif(current_setting('permission_test.outcome'),'NULL'),
                    current_setting('permission_test.tenant')::uuid,
                    current_setting('permission_test.user')::uuid,
                    CASE current_setting('permission_test.role') WHEN 'valid' THEN
                        CASE WHEN p_actor IN ('weekly_delivery','internal_link_skill')
                            THEN 'workload' ELSE 'owner' END
                        WHEN 'missing' THEN NULL WHEN 'unknown' THEN 'synthetic-unknown-role'
                        ELSE 'analyst' END,
                    'mfa'::text,1::bigint,1::bigint;
            END $$;
            ALTER FUNCTION control.permission_test_authority(text,bytea,uuid,text,text)
                OWNER TO signal_migrator;
            REVOKE ALL ON FUNCTION control.permission_test_authority(text,bytea,uuid,text,text)
                FROM PUBLIC;
            CREATE FUNCTION control.permission_test_scope(
                p_hash bytea,p_generation text,p_site uuid)
            RETURNS TABLE(tenant_id uuid,user_id uuid,membership_id uuid)
            LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
                SELECT a.tenant_id,a.user_id,NULL::uuid FROM
                    control.permission_test_authority('owner',p_hash,p_site,p_generation) a
                    WHERE a.outcome='authorized';
            $$;
            ALTER FUNCTION control.permission_test_scope(bytea,text,uuid)
                OWNER TO signal_migrator;
            REVOKE ALL ON FUNCTION control.permission_test_scope(bytea,text,uuid) FROM PUBLIC;
            CREATE OR REPLACE FUNCTION control.admit_port_context(p_actor text,p_context text,
                p_hash bytea,p_generation text,p_site uuid,p_mode text)
            RETURNS SETOF control.port_authority_context
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
            BEGIN
                IF p_context='authority' THEN
                    RETURN QUERY SELECT a.*,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid
                        FROM control.permission_test_authority(
                            p_actor,p_hash,p_site,p_generation,p_mode) a;
                ELSE
                    RETURN QUERY SELECT 'authorized'::text,a.tenant_id,a.user_id,NULL::text,
                        NULL::text,NULL::bigint,NULL::bigint,a.membership_id,NULL::text,NULL::uuid,NULL::uuid
                        FROM control.permission_test_scope(p_hash,p_generation,p_site) a;
                END IF;
            END $$;
            CREATE OR REPLACE FUNCTION control.assert_weekly_skill_stage(
                p_hash bytea,p_generation text,p_site uuid,p_stages text[])
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
                AS $$ BEGIN END $$;
            CREATE OR REPLACE FUNCTION control.assert_weekly_skill_resource(
                p_hash bytea,p_generation text,p_site uuid,p_key text,p_id uuid)
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
                AS $$ BEGIN END $$;
            CREATE OR REPLACE FUNCTION control.assert_weekly_delivery_scope(
                p_handle bytea,p_site_id uuid,p_generation text,p_arguments jsonb)
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
                AS $$ BEGIN END $$;
            CREATE OR REPLACE FUNCTION control.assert_internal_link_skill_resource(
                p_hash bytea,p_site uuid,p_generation text,p_kind text,p_id uuid)
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
                AS $$ BEGIN END $$;
        """)
        admin.execute("""
            CREATE OR REPLACE FUNCTION control.resolve_snapshot_authority(
                p_session_hash bytea,p_requested_site_id uuid,p_current_recovery_generation text)
            RETURNS TABLE(outcome text,tenant_id uuid,user_id uuid,role_key text,
                authentication_level text,membership_epoch bigint,site_authorization_epoch bigint)
            LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
                SELECT * FROM control.permission_test_authority(
                    'owner',p_session_hash,p_requested_site_id,p_current_recovery_generation);
            $$;
        """)
        for name, arguments, definition in BASELINE["functions"]:
            definition = definition.replace(
                "FUNCTION control." + name + "(", "FUNCTION control.permission_before_" + name + "("
            )
            for source, actor in (
                ("resolve_snapshot_authority", "owner"),
                ("resolve_weekly_delivery_authority", "weekly_delivery"),
                ("resolve_internal_link_skill_authority", "internal_link_skill"),
            ):
                definition = definition.replace(
                    "control." + source + "(", "control.permission_test_authority('" + actor + "',"
                )
            for source in (
                "business_brain_owner",
                "brand_document_owner",
                "weekly_skill_context",
                "assistant_scope",
            ):
                definition = definition.replace(
                    "control." + source + "(", "control.permission_test_scope("
                )
            admin.execute(definition)
            identity = ",".join(a.split(" ", 1)[1] for a in arguments.split(", "))
            admin.execute(
                sql.SQL("ALTER FUNCTION control.{}({}) OWNER TO signal_migrator").format(
                    sql.Identifier("permission_before_" + name), sql.SQL(identity)
                )
            )
        yield identity_context


@pytest.fixture
def normalized_0101(admin, normalized_admission):
    admin.execute("""
        CREATE FUNCTION control.permission_old_predicate(p_outcome text,p_role text,
            p_required_role text,p_fail_closed boolean) RETURNS boolean
        LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT CASE WHEN p_fail_closed THEN p_outcome IS NOT DISTINCT FROM 'authorized'
                AND (p_required_role IS NULL OR p_role IS NOT DISTINCT FROM p_required_role)
                ELSE p_outcome='authorized'
                    AND (p_required_role IS NULL OR p_role=p_required_role) END;
        $$;
        ALTER FUNCTION control.permission_old_predicate(text,text,text,boolean)
            OWNER TO signal_migrator;
        REVOKE ALL ON FUNCTION control.permission_old_predicate(text,text,text,boolean) FROM PUBLIC;
    """)
    for name, definition in FUNCTIONS_0101.items():
        copied = definition.replace(
            "CREATE OR REPLACE FUNCTION control." + name + "(",
            "CREATE FUNCTION control." + old_name(name) + "(",
            1,
        )
        copied = copied.replace("control.port_permission(", "control.permission_old_predicate(")
        for target in FUNCTIONS_0101:
            copied = copied.replace("control." + target + "(", "control." + old_name(target) + "(")
        admin.execute(copied.replace("%%", "%"))
        signature = admin.execute(
            "SELECT p.oid::regprocedure::text FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='control' AND p.proname=%s",
            (old_name(name),),
        ).fetchone()[0]
        admin.execute("ALTER FUNCTION " + signature + " OWNER TO signal_migrator")
        admin.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
    return normalized_admission


def invoke(admin, name, arguments, context):
    values = []
    for argument in arguments.split(", "):
        parameter, kind = argument.split(" ", 1)
        if kind == "bytea":
            values.append(bytes.fromhex("ab" * 32))
        elif kind == "text":
            values.append(context["generation"])
        elif kind in ("integer", "bigint"):
            values.append(1)
        elif kind == "boolean":
            values.append(False)
        elif kind == "jsonb":
            values.append(psycopg.types.json.Jsonb({}))
        elif "site" in parameter:
            values.append(context["scope"].site_id)
        elif "snapshot" in parameter:
            values.append(None)
        else:
            values.append(context["resource_id"])
    try:
        with admin.transaction(force_rollback=True):
            return admin.execute(
                sql.SQL("SELECT * FROM control.{}({})").format(
                    sql.Identifier(name), sql.SQL(",").join(sql.Placeholder() for _ in values)
                ),
                values,
            ).fetchall()
    except psycopg.Error as error:
        return ("error", error.sqlstate)


def decisions(admin, group, context, *, baseline=False):
    # All read samples use an absent resource; identical UUIDs are necessary even
    # though none can exist. Stable defaults also prevent accidental replay noise.
    return [
        invoke(
            admin, ("permission_before_" if baseline else "") + v["name"], v["arguments"], context
        )
        for v in group["variants"]
    ]


@pytest.mark.parametrize("group", GROUPS, ids=lambda g: g["owner"])
def test_ports_and_owners_cannot_diverge_after_equivalent_admission(
    admin, normalized_admission, group
):
    for outcome, role, failure in [
        *itertools.product(
            (
                "authorized",
                "invalid_session",
                "authorization_denied",
                "authority_changed",
                "unknown",
                "NULL",
            ),
            ("valid", "wrong", "unknown", "missing"),
            ("no",),
        ),
        ("authorized", "valid", "yes"),
    ]:
        for key, value in (("outcome", outcome), ("role", role), ("failure", failure)):
            admin.execute("SELECT set_config(%s,%s,true)", ("permission_test." + key, value))
        before = decisions(admin, group, normalized_admission, baseline=True)
        after = decisions(admin, group, normalized_admission)
        # NULL comparisons deliberately cease being equivalent. Known admitted
        # decisions and failures retain the independent 0100 behavior.
        if outcome != "NULL" and role not in ("missing", "unknown"):
            assert after == before, (group["owner"], outcome, role, failure, before, after)
        assert all(value == after[0] for value in after), (
            group["owner"],
            outcome,
            role,
            failure,
            after,
        )


def test_divergence_assertion_detects_an_actual_port_policy_fork(admin, normalized_admission):
    group = next(g for g in GROUPS if g["owner"] == "read_candidate_build")
    for key, value in (("outcome", "authorized"), ("role", "valid"), ("failure", "no")):
        admin.execute("SELECT set_config(%s,%s,true)", ("permission_test." + key, value))
    normal = decisions(admin, group, normalized_admission)
    assert all(value == normal[0] for value in normal)
    definition = admin.execute(
        "SELECT pg_get_functiondef("
        "'control.weekly_read_candidate_build(bytea,uuid,text,uuid)'::regprocedure)"
    ).fetchone()[0]
    admin.execute(definition.replace("'weekly_delivery'", "'synthetic_invalid_actor'"))
    forked = decisions(admin, group, normalized_admission)
    with pytest.raises(AssertionError):
        assert all(value == forked[0] for value in forked)


@pytest.mark.parametrize("group", NULLABLE_GROUPS, ids=lambda g: g["owner"])
def test_every_former_nullable_entrypoint_denies_unknown_admission(
    admin, normalized_admission, group
):
    for outcome, role in (
        ("NULL", "valid"),
        ("unknown", "valid"),
        ("authorized", "missing"),
        ("authorized", "unknown"),
    ):
        for key, value in (("outcome", outcome), ("role", role), ("failure", "no")):
            admin.execute("SELECT set_config(%s,%s,true)", ("permission_test." + key, value))
        # Fail the test immediately if any authority-bearing predicate admits a
        # malformed context, even when the missing resource would mask a leak.
        definitions = admin.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='control' "
            "AND p.proname='port_permission' AND p.pronargs IN (2,3)"
        ).fetchall()
        with admin.transaction(force_rollback=True):
            for (definition,) in definitions:
                header, delimiter, body = re.split(r"AS (\$\w*\$)", definition, maxsplit=1)
                expression = (
                    body[: body.rfind(delimiter)].strip().removeprefix("SELECT ").removesuffix(";")
                )
                admin.execute(
                    header.replace("LANGUAGE sql", "LANGUAGE plpgsql")
                    + "AS "
                    + delimiter
                    + """
                    DECLARE permitted boolean; BEGIN
                    SELECT """
                    + expression
                    + """ INTO permitted;
                    IF permitted THEN RAISE EXCEPTION 'synthetic-unexpected-admission'
                        USING ERRCODE='P0170'; END IF;
                    RETURN permitted; END;
                """
                    + delimiter
                )
            results = decisions(admin, group, normalized_admission)
            assert ("error", "P0170") not in results, (group["owner"], outcome, role, results)
            assert all(
                "authorized" not in str(value)
                and "permitted" not in str(value)
                and "dispatched" not in str(value)
                for value in results
            ), results


@pytest.mark.parametrize("group", NULLABLE_GROUPS, ids=lambda g: g["owner"])
def test_every_former_nullable_entrypoint_preserves_authorized_decisions(
    admin, normalized_0101, group
):
    for role, failure in itertools.product(("valid", "wrong"), ("no", "yes")):
        for key, value in (("outcome", "authorized"), ("role", role), ("failure", failure)):
            admin.execute("SELECT set_config(%s,%s,true)", ("permission_test." + key, value))
        for variant in group["variants"]:
            actual = invoke(admin, variant["name"], variant["arguments"], normalized_0101)
            before = invoke(admin, old_name(variant["name"]), variant["arguments"], normalized_0101)
            assert actual == before, (variant["name"], role, failure, actual, before)
