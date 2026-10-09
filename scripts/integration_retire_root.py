"""Replace test bootstrap root with an exact-path, short-lived private operator."""

import argparse
import json
from pathlib import Path

from integration_application import POLICIES, SELF
from integration_secrets import OperatorError, Store, private_directory, recovery, write_private

ROLE = "signal-test-private-operator"


def retire(directory: Path):
    destination = directory / "private-operator.json"
    if destination.exists():
        raise OperatorError("Operator material already exists; inspect the prior outcome.")
    store = Store("https://localhost:18200", directory / "ca.pem")
    root = recovery(directory)["root_token"]
    try:
        store.request("GET", "/auth/approle/role/" + ROLE, token=root, expected=(404,))
        policy = [SELF]
        for path in (
            "signal-identity/data/platform/application",
            "signal-authority/data/recovery/current",
            "sys/storage/raft/snapshot",
            "sys/health",
        ):
            policy.append(f'path "{path}" {{ capabilities = ["read"] }}')
        for name in POLICIES:
            policy.extend(
                (
                    f'path "auth/approle/role/signal-test-{name}/role-id" '
                    '{ capabilities = ["read"] }',
                    f'path "auth/approle/role/signal-test-{name}/secret-id" '
                    '{ capabilities = ["update"] }',
                )
            )
        store.request(
            "PUT", "/sys/policies/acl/" + ROLE, token=root, payload={"policy": "\n".join(policy)}
        )
        store.request(
            "POST",
            "/auth/approle/role/" + ROLE,
            token=root,
            payload={
                "bind_secret_id": True,
                "secret_id_num_uses": 1,
                "secret_id_ttl": "1h",
                "token_policies": [ROLE],
                "token_no_default_policy": True,
                "token_ttl": "1h",
                "token_max_ttl": "4h",
                "token_type": "service",
            },
        )
        role_id = store.request("GET", "/auth/approle/role/" + ROLE + "/role-id", token=root)[
            "data"
        ]["role_id"]
        secret_id = store.request("POST", "/auth/approle/role/" + ROLE + "/secret-id", token=root)[
            "data"
        ]["secret_id"]
        auth = store.request(
            "POST", "/auth/approle/login", payload={"role_id": role_id, "secret_id": secret_id}
        )["auth"]
        token = auth["client_token"]
        if auth["policies"] != [ROLE] or auth["lease_duration"] != 3600:
            raise OperatorError("Private operator authority rejected.")
        for method, path, payload in (
            ("GET", "/signal-identity/data/google/client", None),
            ("POST", "/auth/token/create", {}),
            ("POST", "/sys/policies/acl/authority-enlargement", {}),
        ):
            store.request(method, path, token=token, payload=payload, expected=(403,))
        store.request("GET", "/signal-authority/data/recovery/current", token=token)
        write_private(destination, json.dumps({"token": token, "role": ROLE}).encode())
        store.request("POST", "/auth/token/revoke-self", token=root)
        store.request("GET", "/sys/mounts", token=root, expected=(403,))
        write_private(
            directory / "root-retirement.json",
            json.dumps(
                {
                    "bootstrap_root": "REVOKED",
                    "private_operator": ROLE,
                    "operator_lease_seconds": 3600,
                    "runtime_has_root": False,
                }
            ).encode(),
        )
        print("Bootstrap root revoked and denied; scoped private operator retained only on Mac.")
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        retire(private_directory(args.directory))
        return 0
    except Exception as error:
        print(
            f"Root retirement incomplete ({type(error).__name__}); "
            "values suppressed; inspect before retry."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
