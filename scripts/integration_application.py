"""Explicit operator preparation for the dedicated persistent test login runtime."""

import argparse
import json
import secrets
import sys
from pathlib import Path

from integration_identity import leaf_certificates
from integration_secrets import (
    OperatorError,
    Store,
    check_audit,
    private_directory,
    read_private,
    recovery,
    write_private,
)

POLICIES = {
    "pkce-writer": 'path "signal-ephemeral/data/oidc-login/*" { capabilities = ["create"] }',
    "pkce-consumer": "\n".join(
        (
            'path "signal-ephemeral/data/oidc-login/*" { capabilities = ["read"] }',
            'path "signal-ephemeral/metadata/oidc-login/*" { capabilities = ["delete"] }',
        )
    ),
    "recovery-reader": 'path "signal-authority/data/recovery/current" { capabilities = ["read"] }',
}
SELF = "\n".join(
    (
        'path "auth/token/renew-self" { capabilities = ["update"] }',
        'path "auth/token/revoke-self" { capabilities = ["update"] }',
        'path "auth/token/lookup-self" { capabilities = ["read"] }',
    )
)
CONFIG_PATH = "/signal-identity/data/platform/application"


def provision(store: Store, root: str):
    check_audit(store, root)
    mounts = store.request("GET", "/sys/mounts", token=root)["data"]
    auth = store.request("GET", "/sys/auth", token=root)["data"]
    if "signal-ephemeral/" in mounts or "signal-authority/" in mounts or "approle/" in auth:
        raise OperatorError("Application authority already exists; no replacement attempted.")
    store.request(
        "GET", "/signal-identity/metadata/platform/application", token=root, expected=(404,)
    )
    for mount, maximum, expiry in (("signal-ephemeral", 1, "10m"), ("signal-authority", 10, "0s")):
        store.request(
            "POST",
            "/sys/mounts/" + mount,
            token=root,
            payload={"type": "kv", "options": {"version": "2"}},
        )
        store.request(
            "POST",
            "/" + mount + "/config",
            token=root,
            payload={"cas_required": True, "max_versions": maximum, "delete_version_after": expiry},
        )
    store.request(
        "POST",
        "/signal-authority/data/recovery/current",
        token=root,
        payload={"options": {"cas": 0}, "data": {"generation": "test-" + secrets.token_hex(16)}},
    )
    data = {
        "postgres_password": secrets.token_urlsafe(32),
        "migrator_password": secrets.token_urlsafe(32),
        "identity_db_password": secrets.token_urlsafe(32),
        "csrf_key": secrets.token_hex(32),
    }
    store.request("POST", CONFIG_PATH, token=root, payload={"options": {"cas": 0}, "data": data})
    store.request("POST", "/sys/auth/approle", token=root, payload={"type": "approle"})
    for role, policy in POLICIES.items():
        name = "signal-test-" + role
        store.request(
            "PUT", "/sys/policies/acl/" + name, token=root, payload={"policy": policy + "\n" + SELF}
        )
        store.request(
            "POST",
            "/auth/approle/role/" + name,
            token=root,
            payload={
                "bind_secret_id": True,
                "secret_id_num_uses": 1,
                "secret_id_ttl": "30m",
                "token_policies": [name],
                "token_no_default_policy": True,
                "token_period": "300s",
                "token_type": "service",
            },
        )


def render(store: Store, root: str, authority: Path, destination: Path):
    envelope = store.request("GET", CONFIG_PATH, token=root)["data"]
    data = envelope["data"]
    if envelope["metadata"]["version"] != 1 or set(data) != {
        "postgres_password",
        "migrator_password",
        "identity_db_password",
        "csrf_key",
    }:
        raise OperatorError("Application configuration generation rejected.")
    if list(destination.iterdir()):
        raise OperatorError("Use an empty private application directory.")
    leaf_certificates(destination, authority, ("application-database", "api", "dashboard"))
    for role in POLICIES:
        name = "signal-test-" + role
        role_id = store.request("GET", "/auth/approle/role/" + name + "/role-id", token=root)[
            "data"
        ]["role_id"]
        secret_id = store.request("POST", "/auth/approle/role/" + name + "/secret-id", token=root)[
            "data"
        ]["secret_id"]
        write_private(
            destination / (role + ".json"),
            json.dumps({"role_id": role_id, "secret_id": secret_id}).encode(),
        )
    write_private(
        destination / "application.json",
        json.dumps(
            {"identity_db_password": data["identity_db_password"], "csrf_key": data["csrf_key"]}
        ).encode(),
    )
    write_private(destination / "postgres-password", data["postgres_password"].encode())
    write_private(destination / "migrator-password", data["migrator_password"].encode())
    from integration_environment import render

    environment_destination = private_directory(destination / "environment-render")
    render(environment_destination)
    for filename in ("environment.json", "environment.env", "dashboard-origin.json", "Caddyfile"):
        write_private(destination / filename, read_private(environment_destination / filename))
    sql = (Path(__file__).resolve().parents[1] / "database/bootstrap.sql").read_text()
    sql += "\nREVOKE ALL ON DATABASE signal FROM PUBLIC;\n"
    for role, field in (
        ("signal_migrator", "migrator_password"),
        ("signal_identity", "identity_db_password"),
    ):
        value = data[field]
        if not isinstance(value, str) or not value.replace("_", "").replace("-", "").isalnum():
            raise OperatorError("Database credential rejected.")
        sql += (
            f"ALTER ROLE {role} LOGIN PASSWORD '{value}';\n"
            f"GRANT CONNECT ON DATABASE signal TO {role};\n"
        )
    write_private(destination / "initialize.sql", sql.encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "render"))
    parser.add_argument("--authority-directory", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--operator-file", type=Path)
    args = parser.parse_args()
    store = None
    try:
        authority = private_directory(args.authority_directory)
        destination = private_directory(args.directory)
        if list(destination.iterdir()) or destination == authority:
            raise OperatorError("Use a separate empty protected destination.")
        operator_path = args.operator_file or authority / "private-operator.json"
        if args.operator_file is not None and args.operation != "render":
            raise OperatorError("An explicit limited operator is only valid for rendering.")
        if args.operator_file is not None and not operator_path.exists():
            raise OperatorError("Explicit operator material is missing; no root fallback.")
        if args.operation == "render" and operator_path.exists():
            operator = json.loads(read_private(operator_path))
            if (
                set(operator) != {"role", "token"}
                or operator["role"] != "signal-test-private-operator"
            ):
                raise OperatorError("Private operator material rejected.")
            root = operator["token"]
        else:
            root = recovery(authority)["root_token"]
        store = Store("https://localhost:18200", authority / "ca.pem")
        if args.operation == "prepare":
            provision(store, root)
        render(store, root, authority, destination)
        print("Private application material prepared; values suppressed.")
        return 0
    except Exception as error:
        print(
            f"Application preparation failed ({type(error).__name__}); values suppressed.",
            file=sys.stderr,
        )
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
