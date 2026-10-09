"""Approved quorum recovery into the existing narrow, temporary deployment role."""

import argparse
import base64
import json
from pathlib import Path

from integration_retire_root import ROLE
from integration_secrets import (
    OperatorError,
    Store,
    check_audit,
    private_directory,
    recovery,
    write_private,
)


def recover_operator(authority, destination, *, approved):
    if approved is not True or any(destination.iterdir()):
        raise OperatorError("Approved recovery requires an empty protected destination.")
    store = Store("https://localhost:18200", authority / "ca.pem")
    root = None
    operator = None
    retained = False
    root_retired = False
    started = False
    try:
        state = store.request("GET", "/sys/generate-root/attempt")
        if state.get("started") is not False:
            raise OperatorError("Another recovery is in progress; no interference attempted.")
        state = store.request("POST", "/sys/generate-root/attempt", payload={})
        started = True
        otp, nonce = state["otp"], state["nonce"]
        if state["required"] != 2 or state["progress"] != 0:
            raise OperatorError("Recovery threshold rejected.")
        for key in recovery(authority)["keys_base64"][:2]:
            state = store.request(
                "POST", "/sys/generate-root/update", payload={"key": key, "nonce": nonce}
            )
        if state.get("complete") is not True or state.get("nonce") != nonce:
            raise OperatorError("Recovery did not reach quorum.")
        encoded = state["encoded_token"]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if len(decoded) != len(otp):
            raise OperatorError("Recovery encoding rejected.")
        root = bytes(
            left ^ right for left, right in zip(decoded, otp.encode(), strict=True)
        ).decode()
        check_audit(store, root)
        role = store.request("GET", "/auth/approle/role/" + ROLE, token=root)["data"]
        if (
            role["token_policies"] != [ROLE]
            or role["token_no_default_policy"] is not True
            or role["secret_id_num_uses"] != 1
            or role["token_ttl"] != 3600
            or role["token_max_ttl"] != 14400
        ):
            raise OperatorError("Existing limited operator role changed.")
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
        operator = token
        if auth["policies"] != [ROLE] or auth["lease_duration"] != 3600:
            raise OperatorError("Recovered operator authority rejected.")
        for method, path, payload in (
            ("GET", "/signal-identity/data/google/client", None),
            ("POST", "/auth/token/create", {}),
        ):
            store.request(method, path, token=token, payload=payload, expected=(403,))
        write_private(
            destination / "private-operator.json",
            json.dumps(
                {
                    "role": ROLE,
                    "token": token,
                }
            ).encode(),
        )
        retained = True
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
                            "existing_policy_only": True,
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
                            "status": "INCOMPLETE_OR_UNKNOWN",
                            "resume_application": False,
                            "root_revocation_confirmed": False,
                        }
                    ).encode(),
                )
                store.request("DELETE", "/sys/generate-root/attempt")
        finally:
            try:
                if operator is not None and (not retained or not root_retired):
                    try:
                        (destination / "private-operator.json").unlink(missing_ok=True)
                    finally:
                        store.request("POST", "/auth/token/revoke-self", token=operator)
                        store.request(
                            "GET", "/auth/token/lookup-self", token=operator, expected=(403,)
                        )
            finally:
                store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-directory", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--human-approved", action="store_true")
    args = parser.parse_args()
    try:
        recover_operator(
            private_directory(args.authority_directory),
            private_directory(args.directory),
            approved=args.human_approved,
        )
        print("Temporary root revoked and denied; existing limited deployment operator recovered.")
        return 0
    except Exception as error:
        print(
            f"Approved recovery incomplete ({type(error).__name__}); "
            "values suppressed; do not resume the application."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
