"""Explicit private quorum provisioning of dedicated connector workload roles."""

import argparse
import base64
import json
import secrets
import sys
from pathlib import Path

from integration_application import POLICIES as LOGIN_POLICIES
from integration_application import SELF
from integration_retire_root import ROLE as OPERATOR_ROLE
from integration_secrets import (
    OperatorError,
    Store,
    check_audit,
    private_directory,
    recovery,
    snapshot,
    write_private,
)

POLICIES = {
    "slack-connector": "\n".join(
        (
            'path "signal-slack/data/client" { capabilities = ["read"] }',
            'path "signal-slack/data/bots/*" { capabilities = ["create", "read"] }',
            'path "signal-slack/metadata/bots/*" { capabilities = ["delete"] }',
        )
    ),
    "gsc-connector": "\n".join(
        (
            'path "signal-gsc/data/oauth-client" { capabilities = ["read"] }',
            'path "signal-gsc/data/verifiers/*" { capabilities = ["create", "read"] }',
            'path "signal-gsc/metadata/verifiers/*" { capabilities = ["delete"] }',
            'path "signal-gsc/data/refresh/*" { capabilities = ["create", "read", "update"] }',
            'path "signal-gsc/metadata/refresh/*" { capabilities = ["delete"] }',
        )
    ),
    "github-reader": 'path "signal-github/data/github/app" { capabilities = ["read"] }',
    "owner-artifact-reader": 'path "signal-identity/data/platform/owner-artifacts" '
    '{ capabilities = ["read"] }',
}


def operator_policy(roles):
    policy = [SELF]
    for path in (
        "signal-identity/data/platform/application",
        "signal-authority/data/recovery/current",
        "sys/storage/raft/snapshot",
        "sys/health",
    ):
        policy.append(f'path "{path}" {{ capabilities = ["read"] }}')
    for role in roles:
        policy.extend(
            (
                f'path "auth/approle/role/signal-test-{role}/role-id" '
                '{ capabilities = ["read"] }',
                f'path "auth/approle/role/signal-test-{role}/secret-id" '
                '{ capabilities = ["update"] }',
            )
        )
    return "\n".join(policy)


def provision(store, root):
    check_audit(store, root)
    old_policy = store.request("GET", "/sys/policies/acl/" + OPERATOR_ROLE, token=root)["data"][
        "policy"
    ]
    if old_policy != operator_policy(LOGIN_POLICIES):
        raise OperatorError("Existing deployment policy changed; no replacement attempted.")
    for role in POLICIES:
        store.request("GET", "/auth/approle/role/signal-test-" + role, token=root, expected=(404,))
        store.request("GET", "/sys/policies/acl/signal-test-" + role, token=root, expected=(404,))
    store.request(
        "GET", "/signal-identity/metadata/platform/owner-artifacts", token=root, expected=(404,)
    )
    for mount in ("signal-slack", "signal-gsc", "signal-github", "signal-identity"):
        if (
            store.request("GET", "/" + mount + "/config", token=root)["data"].get("cas_required")
            is not True
        ):
            raise OperatorError("Existing connector mount must enforce CAS.")
    store.request(
        "POST",
        "/signal-identity/data/platform/owner-artifacts",
        token=root,
        payload={
            "options": {"cas": 0},
            "data": {
                "reference": "owner-robots:test-v1",
                "material_hex": secrets.token_hex(32),
            },
        },
    )
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
    # Keep provider secrets inaccessible to the deployment operator. It may only
    # issue the reviewed roles' one-use workload login credentials.
    store.request(
        "PUT",
        "/sys/policies/acl/" + OPERATOR_ROLE,
        token=root,
        payload={"policy": operator_policy((*LOGIN_POLICIES, *POLICIES))},
    )


def issue_existing_operator(store, root, destination):
    role = store.request("GET", "/auth/approle/role/" + OPERATOR_ROLE, token=root)["data"]
    if (
        role.get("token_policies") != [OPERATOR_ROLE]
        or role.get("token_no_default_policy") is not True
        or role.get("secret_id_num_uses") != 1
        or role.get("token_ttl") != 3600
        or role.get("token_max_ttl") != 14400
    ):
        raise OperatorError("Existing limited deployment role changed.")
    policy = store.request("GET", "/sys/policies/acl/" + OPERATOR_ROLE, token=root)["data"][
        "policy"
    ]
    if policy != operator_policy((*LOGIN_POLICIES, *POLICIES)):
        raise OperatorError("Exact reviewed deployment policy required.")
    role_id = store.request("GET", "/auth/approle/role/" + OPERATOR_ROLE + "/role-id", token=root)[
        "data"
    ]["role_id"]
    secret_id = store.request(
        "POST", "/auth/approle/role/" + OPERATOR_ROLE + "/secret-id", token=root
    )["data"]["secret_id"]
    auth = store.request(
        "POST", "/auth/approle/login", payload={"role_id": role_id, "secret_id": secret_id}
    )["auth"]
    operator = auth["client_token"]
    retained = False
    try:
        if (
            auth.get("policies") != [OPERATOR_ROLE]
            or auth.get("lease_duration") != 3600
            or auth.get("renewable") is not True
        ):
            raise OperatorError("Limited deployment authentication rejected.")
        for method, path, payload in (
            ("GET", "/signal-github/data/github/app", None),
            ("GET", "/signal-gsc/data/oauth-client", None),
            ("GET", "/signal-slack/data/client", None),
            ("GET", "/signal-identity/data/platform/owner-artifacts", None),
            ("POST", "/auth/token/create", {}),
            ("PUT", "/sys/policies/acl/authority-enlargement", {}),
        ):
            store.request(method, path, token=operator, payload=payload, expected=(403,))
        write_private(
            destination / "private-operator.json",
            json.dumps({"role": OPERATOR_ROLE, "token": operator}).encode(),
        )
        retained = True
        return operator
    finally:
        if not retained:
            try:
                (destination / "private-operator.json").unlink(missing_ok=True)
            finally:
                store.request("POST", "/auth/token/revoke-self", token=operator)
                store.request("GET", "/auth/token/lookup-self", token=operator, expected=(403,))


def recover_and_provision(authority, destination, *, approved):
    if approved is not True or any(destination.iterdir()):
        raise OperatorError("Explicit approval and empty protected audit destination required.")
    store = Store("https://localhost:18200", authority / "ca.pem")
    root = None
    operator = None
    started = False
    succeeded = False
    root_retired = False
    backup_complete = False
    try:
        if store.request("GET", "/sys/generate-root/attempt").get("started") is not False:
            raise OperatorError("Another private recovery is in progress.")
        state = store.request("POST", "/sys/generate-root/attempt", payload={})
        started = True
        otp, nonce = state["otp"], state["nonce"]
        if state["required"] != 2 or state["progress"] != 0:
            raise OperatorError("Private recovery threshold changed.")
        for share in recovery(authority)["keys_base64"][:2]:
            state = store.request(
                "POST", "/sys/generate-root/update", payload={"key": share, "nonce": nonce}
            )
        if state.get("complete") is not True or state.get("nonce") != nonce:
            raise OperatorError("Private recovery quorum unavailable.")
        encoded = state["encoded_token"]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if len(decoded) != len(otp):
            raise OperatorError("Recovery encoding rejected.")
        root = bytes(
            left ^ right for left, right in zip(decoded, otp.encode(), strict=True)
        ).decode()
        snapshot(store, destination, operator_token=root)
        provision(store, root)
        operator = issue_existing_operator(store, root, destination)
        succeeded = True
    finally:
        try:
            if root is not None:
                store.request("POST", "/auth/token/revoke-self", token=root)
                store.request("GET", "/sys/mounts", token=root, expected=(403,))
                write_private(
                    destination / "root-revocation.json",
                    json.dumps(
                        {
                            "temporary_root": "REVOKED_AND_DENIED",
                            "provisioning_succeeded": succeeded,
                            "workload_roles": list(POLICIES),
                            "provider_permissions_changed": False,
                            "secure_listener_restore_required": True,
                            "operator_lease_seconds": 3600,
                        }
                    ).encode(),
                )
                root_retired = True
            elif started:
                write_private(
                    destination / "recovery-incomplete.json",
                    json.dumps(
                        {
                            "resume_application": False,
                            "root_revocation_confirmed": False,
                        }
                    ).encode(),
                )
                store.request("DELETE", "/sys/generate-root/attempt")
        finally:
            try:
                if succeeded and root_retired:
                    snapshot(
                        store,
                        private_directory(destination / "after-root-retirement"),
                        operator_token=operator,
                    )
                    write_private(
                        destination / "connector-provisioning.json",
                        json.dumps(
                            {
                                "workloads_provisioned": True,
                                "new_key_backup": "ENCRYPTED_AFTER_ROOT_RETIREMENT",
                                "provider_connections": "NOT_EXECUTED",
                                "resume_application": False,
                            }
                        ).encode(),
                    )
                    backup_complete = True
            finally:
                try:
                    if operator is not None and not (
                        succeeded and root_retired and backup_complete
                    ):
                        try:
                            (destination / "private-operator.json").unlink(missing_ok=True)
                        finally:
                            store.request("POST", "/auth/token/revoke-self", token=operator)
                            store.request(
                                "GET", "/auth/token/lookup-self", token=operator, expected=(403,)
                            )
                finally:
                    store.close()


def render(store, operator, destination):
    if any(destination.iterdir()):
        raise OperatorError("Use an empty protected workload credential destination.")
    for role in POLICIES:
        name = "signal-test-" + role
        role_id = store.request("GET", "/auth/approle/role/" + name + "/role-id", token=operator)[
            "data"
        ]["role_id"]
        secret_id = store.request(
            "POST", "/auth/approle/role/" + name + "/secret-id", token=operator
        )["data"]["secret_id"]
        write_private(
            destination / (role + ".json"),
            json.dumps(
                {
                    "role_id": role_id,
                    "secret_id": secret_id,
                }
            ).encode(),
        )
    write_private(
        destination / "connector-workloads.json", json.dumps({"roles": list(POLICIES)}).encode()
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-directory", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--human-approved", action="store_true")
    args = parser.parse_args()
    try:
        recover_and_provision(
            private_directory(args.authority_directory),
            private_directory(args.directory),
            approved=args.human_approved,
        )
        print("Connector workload authority provisioned; temporary root revoked and denied.")
        return 0
    except Exception as error:
        print(
            f"Connector provisioning incomplete ({type(error).__name__}); "
            "values suppressed; keep API paused.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
