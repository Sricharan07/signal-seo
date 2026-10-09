"""One approved, empty test-user cleanup; never an account-linking mechanism."""

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from integration_environment import load_integration_scope
from integration_secrets import OperatorError, private_directory, read_private, write_private
from psycopg import sql

IDENTITY = "signal-integration-identity-identity-1"
DATABASE = "signal-integration-identity-database-1"
DEPENDENCIES = (
    "credential",
    "federated_identity",
    "user_attribute",
    "user_consent",
    "user_group_membership",
    "user_required_action",
    "issued_ver_credential",
    "user_ver_credential",
    "broker_link",
    "offline_user_session",
    "login_failure",
)


def ssh_command():
    scope = load_integration_scope()
    return [
        "ssh",
        "-b",
        scope.ssh_source_ipv6,
        "-i",
        scope.ssh_key_path,
        "-o",
        "ConnectTimeout=10",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + scope.ssh_known_hosts_path,
        "ubuntu@" + scope.public_ipv6,
    ]


def remote(arguments, sql=None):
    result = subprocess.run(
        [*ssh_command(), shlex.join(arguments)],
        input=sql,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    if result.returncode or len(result.stdout) > 65536:
        raise OperatorError("Private identity maintenance failed; output suppressed.")
    return result.stdout.strip()


def database(sql):
    return remote(
        [
            "sudo",
            "docker",
            "exec",
            "-i",
            "-u",
            "70",
            DATABASE,
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            "keycloak",
            "-Atq",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        sql,
    )


def snapshot_sql():
    scope = load_integration_scope()
    counts = ",".join(
        f"'{table}',(SELECT count(*) FROM {table} WHERE user_id='{scope.incomplete_user_id}')"
        for table in DEPENDENCIES
    )
    return (
        "SELECT jsonb_build_object('user',"
        f"(SELECT to_jsonb(u) FROM user_entity u WHERE u.id='{scope.incomplete_user_id}'),'roles',"
        "(SELECT jsonb_agg(jsonb_build_object('mapping',to_jsonb(m),"
        "'name',r.name,'realm',r.realm_id) ORDER BY r.id) FROM user_role_mapping m "
        f"JOIN keycloak_role r ON r.id=m.role_id WHERE m.user_id='{scope.incomplete_user_id}'),"
        f"'dependencies',jsonb_build_object({counts}))"
    )


def validate(snapshot):
    user, roles = snapshot.get("user"), snapshot.get("roles")
    if (
        not isinstance(user, dict)
        or user.get("id") != load_integration_scope().incomplete_user_id
        or user.get("realm_id") != load_integration_scope().realm_id
        or user.get("email") != load_integration_scope().owner_email
        or user.get("username") != load_integration_scope().owner_email
        or user.get("created_timestamp") != load_integration_scope().incomplete_created_timestamp
        or user.get("last_modified_timestamp")
        != load_integration_scope().incomplete_created_timestamp
        or user.get("email_verified") is not False
        or user.get("enabled") is not True
        or user.get("federation_link") is not None
        or user.get("service_account_client_link") is not None
        or snapshot.get("dependencies") != dict.fromkeys(DEPENDENCIES, 0)
        or not isinstance(roles, list)
        or len(roles) != 1
        or roles[0].get("name") != "default-roles-signal"
        or roles[0].get("realm") != load_integration_scope().realm_id
        or roles[0].get("mapping", {}).get("user_id") != load_integration_scope().incomplete_user_id
    ):
        raise OperatorError("Account is not the exact empty, unlinked test record.")


def literal(value):
    return sql.Literal(json.dumps(value, sort_keys=True)).as_string() + "::jsonb"


def cleanup_sql(snapshot, *, rehearse):
    scope = load_integration_scope()
    expected = literal(snapshot)
    guard_body = (
        "BEGIN IF ("
        + snapshot_sql()
        + ") IS DISTINCT FROM "
        + expected
        + " THEN RAISE EXCEPTION 'Maintenance precondition changed'; END IF; END"
    )
    guard = "DO " + sql.Literal(guard_body).as_string() + ";"
    deletion = (
        f"DELETE FROM user_role_mapping WHERE user_id='{scope.incomplete_user_id}';"
        f"DELETE FROM user_entity WHERE id='{scope.incomplete_user_id}' "
        f"AND realm_id='{scope.realm_id}';"
        f"DO $gone$ BEGIN IF EXISTS (SELECT 1 FROM user_entity "
        f"WHERE id='{scope.incomplete_user_id}') "
        "THEN RAISE EXCEPTION 'Deletion did not complete'; END IF; END $gone$;"
    )
    restore = ""
    if rehearse:
        restore = (
            "INSERT INTO user_entity SELECT * FROM jsonb_populate_record(NULL::user_entity,"
            + literal(snapshot["user"])
            + ");"
            "INSERT INTO user_role_mapping SELECT * FROM jsonb_populate_record("
            "NULL::user_role_mapping," + literal(snapshot["roles"][0]["mapping"]) + ");" + guard
        )
    locks = (
        "SET LOCAL lock_timeout='5s';SET LOCAL statement_timeout='10s';LOCK TABLE "
        + ",".join(("user_entity", "user_role_mapping", "keycloak_role", *DEPENDENCIES))
        + " IN SHARE ROW EXCLUSIVE MODE;"
    )
    return "BEGIN;" + locks + guard + deletion + restore + ("ROLLBACK;" if rehearse else "COMMIT;")


def cleanup(directory, *, approved):
    if approved is not True:
        raise OperatorError("Exact cleanup approval is required.")
    directory = private_directory(directory)
    if any(directory.iterdir()):
        raise OperatorError("Use a new empty recovery directory; no files overwritten.")
    stopped = False
    try:
        stopped = True
        remote(["sudo", "docker", "stop", IDENTITY])
        snapshot = json.loads(database(snapshot_sql() + ";"))
        validate(snapshot)
        content = json.dumps(snapshot, sort_keys=True).encode()
        key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
        encrypted = nonce + AESGCM(key).encrypt(nonce, content, b"signal-empty-test-user-v1")
        write_private(directory / "recovery-key.bin", key)
        write_private(directory / "incomplete-user.enc", encrypted)
        recovered = json.loads(
            AESGCM(key).decrypt(encrypted[:12], encrypted[12:], b"signal-empty-test-user-v1")
        )
        validate(recovered)
        database(cleanup_sql(recovered, rehearse=True))
        if json.loads(database(snapshot_sql() + ";")) != recovered:
            raise OperatorError("Recovery rehearsal changed identity data.")
        changed = dict(
            recovered,
            user=dict(
                recovered["user"],
                created_timestamp=load_integration_scope().incomplete_created_timestamp + 1,
            ),
        )
        try:
            database(cleanup_sql(changed, rehearse=False))
        except OperatorError:
            if json.loads(database(snapshot_sql() + ";")) != recovered:
                raise OperatorError("Rejected cleanup changed identity data.") from None
        else:
            raise OperatorError("Changed-precondition cleanup was admitted.")
        database(cleanup_sql(recovered, rehearse=False))
        final = json.loads(database(snapshot_sql() + ";"))
        if final["user"] is not None or final["roles"] is not None:
            raise OperatorError("Cleanup outcome is unknown; recovery copy retained.")
        write_private(
            directory / "receipt.json",
            json.dumps(
                {
                    "human_approved": True,
                    "exact_empty_test_user_removed": True,
                    "encrypted_recovery_rehearsal": "PASS",
                    "changed_precondition_rejected": "PASS",
                    "credential_and_provider_accounts_changed": False,
                }
            ).encode(),
        )
    finally:
        if stopped:
            remote(["sudo", "docker", "start", IDENTITY])


def rehearse_recovery_copy(directory):
    """Exercise final SQL serialization in session-local clones, never live users."""
    directory = private_directory(directory)
    encrypted = read_private(directory / "incomplete-user.enc")
    key = read_private(directory / "recovery-key.bin")
    snapshot = json.loads(
        AESGCM(key).decrypt(encrypted[:12], encrypted[12:], b"signal-empty-test-user-v1")
    )
    validate(snapshot)
    snapshot["user"]["last_name"] = "$guard$; DROP TABLE user_entity; -- ' \\\n"
    tables = ("user_entity", "user_role_mapping", "keycloak_role", *DEPENDENCIES)
    clones = "".join(
        f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING DEFAULTS);" for table in tables
    )
    seed = (
        "INSERT INTO keycloak_role SELECT * FROM public.keycloak_role WHERE id="
        + sql.Literal(snapshot["roles"][0]["mapping"]["role_id"]).as_string()
        + ";"
        "INSERT INTO user_entity SELECT * FROM jsonb_populate_record(NULL::user_entity,"
        + literal(snapshot["user"])
        + ");"
        "INSERT INTO user_role_mapping SELECT * FROM jsonb_populate_record("
        "NULL::user_role_mapping," + literal(snapshot["roles"][0]["mapping"]) + ");"
    )
    database("BEGIN;" + clones + seed + cleanup_sql(snapshot, rehearse=True)[len("BEGIN;") :])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--rehearse-recovery-copy", action="store_true")
    args = parser.parse_args()
    try:
        if args.rehearse_recovery_copy:
            rehearse_recovery_copy(args.directory)
            print(
                "Recovery SQL serialization and untrusted profile rollback PASS; "
                "live users unchanged."
            )
            return 0
        cleanup(args.directory, approved=args.approved)
        print("Exact incomplete test account removed; encrypted recovery and rollback checks PASS.")
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(f"Cleanup failed ({reason}); private values suppressed; no linking or MFA bypass.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
