"""Real private AppRole, PKCE, ACL, renewal and revocation qualification."""

import argparse
import asyncio
import ssl
from pathlib import Path
from uuid import uuid4

from integration_application import POLICIES
from integration_secrets import Store, private_directory, read_private, recovery
from qualify_integration_identity import require
from signal_core.pkce_secrets import OpenBaoPkceClient, PkceSecretUnavailable
from signal_core.recovery_authority import OpenBaoRecoveryAuthority


async def qualify(directory: Path):
    store = Store("https://localhost:18200", directory / "ca.pem")
    root = recovery(directory)["root_token"]
    context = ssl.create_default_context(cadata=read_private(directory / "ca.pem").decode())
    tokens = {}
    try:
        for role in POLICIES:
            name = "signal-test-" + role
            role_id = store.request("GET", "/auth/approle/role/" + name + "/role-id", token=root)[
                "data"
            ]["role_id"]
            secret_id = store.request(
                "POST", "/auth/approle/role/" + name + "/secret-id", token=root
            )["data"]["secret_id"]
            credential = {"role_id": role_id, "secret_id": secret_id}
            auth = store.request("POST", "/auth/approle/login", payload=credential)["auth"]
            token = auth["client_token"]
            tokens[role] = token
            require(
                auth["policies"] == [name] and auth["lease_duration"] == 300 and auth["renewable"],
                "scoped renewable workload token",
            )
            store.request("POST", "/auth/approle/login", payload=credential, expected=(400, 403))
            store.request("POST", "/auth/token/create", token=token, payload={}, expected=(403,))
            store.request(
                "GET", "/signal-identity/data/google/client", token=token, expected=(403,)
            )
            store.request(
                "POST",
                "/signal-identity/data/platform/application",
                token=token,
                payload={"options": {"cas": 1}, "data": {}},
                expected=(403,),
            )
            renew = store.request(
                "POST", "/auth/token/renew-self", token=token, payload={"increment": "300s"}
            )["auth"]
            require(
                renew["client_token"] == token and renew["lease_duration"] == 300,
                "same-token renewal",
            )
        writer = OpenBaoPkceClient("https://localhost:18200", tokens["pkce-writer"])
        consumer = OpenBaoPkceClient("https://localhost:18200", tokens["pkce-consumer"])
        identifier = uuid4()
        reference = await writer.store_verifier(
            attempt_id=identifier, code_verifier="a" * 64, verify=context
        )
        store.request(
            "GET",
            f"/signal-ephemeral/data/oidc-login/{identifier}",
            token=tokens["pkce-writer"],
            expected=(403,),
        )
        require(
            await consumer.consume_verifier(secret_reference=reference, verify=context) == "a" * 64,
            "durable one-time PKCE",
        )
        try:
            await consumer.consume_verifier(secret_reference=reference, verify=context)
        except PkceSecretUnavailable:
            pass
        else:
            raise RuntimeError("PKCE replay accepted.")
        authority = OpenBaoRecoveryAuthority("https://localhost:18200", tokens["recovery-reader"])
        generation = await authority.current_generation(verify=context)
        require(
            generation.version == 1 and generation.value.startswith("test-"),
            "independent recovery generation",
        )
        store.request(
            "POST",
            "/signal-authority/data/recovery/current",
            token=tokens["recovery-reader"],
            payload={"options": {"cas": 1}, "data": {"generation": "denied"}},
            expected=(403,),
        )
    finally:
        for token in tokens.values():
            store.request("POST", "/auth/token/revoke-self", token=token)
            store.request("GET", "/auth/token/lookup-self", token=token, expected=(403,))
        store.close()
    print(
        "Real AppRole login, single-use ID, exact ACLs, PKCE consume/replay, "
        "renewal/revocation: PASS."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(qualify(private_directory(args.directory)))
        return 0
    except Exception as error:
        print(
            f"Application credential qualification failed ({type(error).__name__}); "
            "values suppressed."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
