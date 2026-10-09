import copy
import json
import re

import integration_cleanup_incomplete_identity as cleanup
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from integration_secrets import OperatorError


def empty_user():
    scope = cleanup.load_integration_scope()
    return {
        "user": {
            "id": cleanup.load_integration_scope().incomplete_user_id,
            "realm_id": cleanup.load_integration_scope().realm_id,
            "email": cleanup.load_integration_scope().owner_email,
            "username": cleanup.load_integration_scope().owner_email,
            "created_timestamp": cleanup.load_integration_scope().incomplete_created_timestamp,
            "last_modified_timestamp": scope.incomplete_created_timestamp,
            "enabled": True,
            "email_verified": False,
            "federation_link": None,
            "service_account_client_link": None,
        },
        "roles": [
            {
                "name": "default-roles-signal",
                "realm": cleanup.load_integration_scope().realm_id,
                "mapping": {
                    "user_id": cleanup.load_integration_scope().incomplete_user_id,
                    "role_id": "fixture-default-role",
                },
            }
        ],
        "dependencies": dict.fromkeys(cleanup.DEPENDENCIES, 0),
    }


def test_exact_empty_record_is_admitted_without_identity_or_credential_changes():
    snapshot = empty_user()
    cleanup.validate(snapshot)
    sql = cleanup.cleanup_sql(snapshot, rehearse=True)
    assert sql.startswith("BEGIN;") and sql.endswith("ROLLBACK;")
    assert "INSERT INTO user_entity" in sql
    assert "INSERT INTO user_role_mapping" in sql
    assert "UPDATE" not in sql and "INSERT INTO credential" not in sql
    assert "INSERT INTO federated_identity" not in sql
    assert "LOCK TABLE user_entity,user_role_mapping,keycloak_role," in sql
    assert "IN SHARE ROW EXCLUSIVE MODE" in sql
    assert "lock_timeout='5s'" in sql


@pytest.mark.parametrize("table", cleanup.DEPENDENCIES)
def test_any_credential_link_or_other_dependency_denies_cleanup(table):
    snapshot = empty_user()
    snapshot["dependencies"][table] = 1
    with pytest.raises(OperatorError):
        cleanup.validate(snapshot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other"),
        ("realm_id", "other"),
        ("email", "other"),
        ("username", "other"),
        ("created_timestamp", 1),
        ("last_modified_timestamp", 1),
        ("email_verified", True),
        ("enabled", False),
        ("federation_link", "other"),
        ("service_account_client_link", "other"),
    ],
)
def test_changed_identity_is_never_cleaned_up(field, value):
    snapshot = empty_user()
    snapshot["user"][field] = value
    with pytest.raises(OperatorError):
        cleanup.validate(snapshot)


def test_nondefault_privileges_and_extra_roles_deny_cleanup():
    snapshot = empty_user()
    snapshot["roles"][0]["name"] = "admin"
    with pytest.raises(OperatorError):
        cleanup.validate(snapshot)
    snapshot = empty_user()
    snapshot["roles"].append(copy.deepcopy(snapshot["roles"][0]))
    with pytest.raises(OperatorError):
        cleanup.validate(snapshot)


def test_profile_strings_cannot_terminate_a_do_block():
    snapshot = empty_user()
    snapshot["user"]["last_name"] = "$guard$; DROP TABLE user_entity; -- ' \\\n"
    generated = cleanup.cleanup_sql(snapshot, rehearse=True)
    assert "DO $guard$" not in generated
    assert re.search(r";DO\s+E?'", generated)
    assert "\\\\" in generated


def test_approval_and_empty_recovery_directory_precede_remote_operations(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cleanup, "remote", lambda args: calls.append(args))
    with pytest.raises(OperatorError, match="approval"):
        cleanup.cleanup(tmp_path, approved=False)
    (tmp_path / "existing").touch()
    with pytest.raises(OperatorError, match="empty recovery"):
        cleanup.cleanup(tmp_path, approved=True)
    assert calls == []


def test_backup_failure_restarts_identity_without_deletion(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cleanup, "remote", lambda args: calls.append(args))
    monkeypatch.setattr(cleanup, "database", lambda sql: json.dumps(empty_user()))

    def fail(*args):
        raise OSError("fixture storage failure")

    monkeypatch.setattr(cleanup, "write_private", fail)
    with pytest.raises(OSError):
        cleanup.cleanup(tmp_path, approved=True)
    assert [call[2] for call in calls] == ["stop", "start"]


def test_authenticated_encrypted_recovery_and_guarded_cleanup(tmp_path, monkeypatch):
    snapshot = empty_user()
    queries, calls = [], []
    deleted = False

    def database(sql):
        nonlocal deleted
        queries.append(sql)
        if sql.startswith("SELECT"):
            return json.dumps(dict(snapshot, user=None, roles=None) if deleted else snapshot)
        if str(cleanup.load_integration_scope().incomplete_created_timestamp + 1) in sql:
            raise OperatorError("fixture changed precondition")
        if sql.endswith("COMMIT;"):
            deleted = True
        return ""

    monkeypatch.setattr(cleanup, "remote", lambda args: calls.append(args))
    monkeypatch.setattr(cleanup, "database", database)
    cleanup.cleanup(tmp_path, approved=True)
    encrypted = (tmp_path / "incomplete-user.enc").read_bytes()
    key = (tmp_path / "recovery-key.bin").read_bytes()
    assert (
        json.loads(
            AESGCM(key).decrypt(encrypted[:12], encrypted[12:], b"signal-empty-test-user-v1")
        )
        == snapshot
    )
    changed = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    with pytest.raises(InvalidTag):
        AESGCM(key).decrypt(changed[:12], changed[12:], b"signal-empty-test-user-v1")
    assert all(path.stat().st_mode & 0o077 == 0 for path in tmp_path.iterdir())
    assert len([sql for sql in queries if sql.endswith("COMMIT;")]) == 2
    assert [call[2] for call in calls] == ["stop", "start"]
    assert json.loads((tmp_path / "receipt.json").read_text())["exact_empty_test_user_removed"]
