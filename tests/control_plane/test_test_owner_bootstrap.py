import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from integration_test_owner import bootstrap_sql
from psycopg import sql

TABLES = (
    "control.users",
    "app.tenants",
    "app.memberships",
    "control.tenant_directory",
    "control.oidc_login_attempts",
)


@pytest.fixture
def private_bootstrap_schema(admin):
    namespace = "test_bootstrap_" + uuid4().hex
    admin.execute(sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(namespace)))
    try:
        for table in TABLES:
            name = table.replace(".", "_")
            admin.execute(
                sql.SQL("CREATE TABLE {}.{} (LIKE {} INCLUDING ALL)").format(
                    sql.Identifier(namespace),
                    sql.Identifier(name),
                    sql.SQL(table),
                )
            )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO signal_bootstrap").format(
                sql.Identifier(namespace),
            )
        )
        for table in ("app.tenants", "app.memberships", "control.tenant_directory"):
            name = sql.SQL("{}.{}").format(
                sql.Identifier(namespace),
                sql.Identifier(table.replace(".", "_")),
            )
            admin.execute(sql.SQL("GRANT SELECT,INSERT ON {} TO signal_bootstrap").format(name))
            if table.startswith("app."):
                admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(name))
                admin.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(name))
                admin.execute(
                    sql.SQL(
                        "CREATE POLICY scoped ON {} TO signal_bootstrap "
                        "USING (tenant_id=app.current_tenant_id()) "
                        "WITH CHECK (tenant_id=app.current_tenant_id())"
                    ).format(name)
                )
        yield namespace
    finally:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(namespace)))


def statement(intent, namespace, *, rehearse):
    rendered = bootstrap_sql(intent, rehearse=rehearse)
    for table in TABLES:
        rendered = rendered.replace(table, namespace + "." + table.replace(".", "_"))
    return rendered


def seed_attempt(admin, namespace):
    now = int(datetime.now(UTC).timestamp())
    intent = {key: str(uuid4()) for key in ("user_id", "tenant_id", "membership_id", "attempt_id")}
    intent.update(approved_at=now - 5, nonce_hash="ab" * 32)
    admin.execute(
        sql.SQL(
            "INSERT INTO {}.control_oidc_login_attempts "
            "(id,state_hash,nonce_hash,browser_binding_hash,oidc_issuer,client_id,redirect_uri,"
            "pkce_secret_reference,return_path,purpose,expires_at,consumed_at) "
            "VALUES (%s,%s,%s,%s,%s,'signal-test-dashboard',%s,%s,'/','login',"
            "now()+interval '5 minutes',now())"
        ).format(sql.Identifier(namespace)),
        (
            intent["attempt_id"],
            hashlib.sha256(b"state").digest(),
            bytes.fromhex(intent["nonce_hash"]),
            hashlib.sha256(b"binding").digest(),
            "https://signal-test.example.invalid/identity/realms/signal",
            "https://signal-test.example.invalid/auth/callback",
            "secret://oidc-login/" + intent["attempt_id"] + "/1",
        ),
    )
    return intent


def count_users(admin, namespace):
    return admin.execute(
        sql.SQL("SELECT count(*) FROM {}.control_users").format(
            sql.Identifier(namespace),
        )
    ).fetchone()[0]


def test_real_postgres_create_only_owner_and_rollback(admin, private_bootstrap_schema):
    namespace = private_bootstrap_schema
    intent = seed_attempt(admin, namespace)
    admin.execute(statement(intent, namespace, rehearse=True))
    assert count_users(admin, namespace) == 0
    admin.execute(statement(intent, namespace, rehearse=False))
    assert count_users(admin, namespace) == 1
    assert admin.execute(
        sql.SQL("SELECT role_key,state FROM {}.app_memberships").format(sql.Identifier(namespace))
    ).fetchone() == ("owner", "active")
    with pytest.raises(psycopg.errors.RaiseException, match="create-only"):
        admin.execute(statement(intent, namespace, rehearse=False))
    admin.execute("ROLLBACK")
    assert count_users(admin, namespace) == 1


@pytest.mark.parametrize("change", ["nonce", "future_approval", "missing_attempt"])
def test_real_postgres_changed_proof_rolls_back_without_owner(
    admin,
    private_bootstrap_schema,
    change,
):
    namespace = private_bootstrap_schema
    intent = seed_attempt(admin, namespace)
    if change == "nonce":
        intent["nonce_hash"] = "00" * 32
    elif change == "future_approval":
        intent["approved_at"] += 300
    else:
        intent["attempt_id"] = str(uuid4())
    with pytest.raises(psycopg.errors.RaiseException, match="proof required"):
        admin.execute(statement(intent, namespace, rehearse=False))
    admin.execute("ROLLBACK")
    assert count_users(admin, namespace) == 0
