"""Qualify workload ACLs on disposable TLS/Raft OpenBao with synthetic data only."""

import asyncio
import ssl
import sys
import time
from uuid import uuid4

import qualify_integration_connector_secrets as lab
from integration_application import POLICIES as LOGIN_POLICIES
from integration_application import provision as provision_login
from integration_connector_authority import (
    OPERATOR_ROLE,
    POLICIES,
    issue_existing_operator,
    operator_policy,
    provision,
)
from integration_connector_secrets import store_configuration
from integration_secrets import (
    OperatorError,
    initialize,
    private_directory,
    read_private,
    recovery,
    snapshot,
)

sys.path.insert(0, str(lab.ROOT / "services/control_plane/src"))
from signal_core.github_read_binding import OpenBaoGitHubAppCredential  # noqa: E402
from signal_core.gsc_secrets import OpenBaoGscSecrets  # noqa: E402
from signal_core.slack_secrets import OpenBaoSlackSecrets  # noqa: E402


async def exercise(base_url, context, tokens):
    options = {"verify": context}
    slack = OpenBaoSlackSecrets(base_url, tokens["slack-connector"])
    assert (await slack.client(**options))["client_id"] == "00000000000.00000000000000"
    reference = await slack.store_bot(uuid4(), "synthetic-slack-bot-token", **options)
    assert await slack.bot(reference, **options) == "synthetic-slack-bot-token"
    await slack.destroy_bot(reference, **options)
    gsc = OpenBaoGscSecrets(base_url, tokens["gsc-connector"])
    assert (await gsc.client_credentials(**options)).client_id.startswith("synthetic-gsc-")
    attempt = uuid4()
    await gsc.store_verifier(attempt, "v" * 43, **options)
    assert await gsc.consume_verifier(attempt, **options) == "v" * 43
    try:
        await gsc.consume_verifier(attempt, **options)
    except Exception:
        pass
    else:
        raise OperatorError("Consumed verifier was available again.")
    reference = await gsc.store_refresh_token(uuid4(), "synthetic-refresh-token-before", **options)
    assert await gsc.refresh_token(reference, **options) == "synthetic-refresh-token-before"
    await gsc.replace_refresh_token(
        reference, "synthetic-refresh-token-before", "synthetic-refresh-token-after", **options
    )
    assert await gsc.refresh_token(reference, **options) == "synthetic-refresh-token-after"
    await gsc.destroy_refresh_token(reference, **options)
    github = OpenBaoGitHubAppCredential(base_url, tokens["github-reader"])
    assert (await github.credentials(**options)).app_id == 1234567


def qualify(store, directory):
    initialize(store, directory)
    for provider, data in lab.synthetic_configurations().items():
        store_configuration(store, directory, provider, data)
    root = recovery(directory)["root_token"]
    provision_login(store, root)
    store.request(
        "PUT",
        "/sys/policies/acl/" + OPERATOR_ROLE,
        token=root,
        payload={"policy": operator_policy(LOGIN_POLICIES)},
    )
    store.request(
        "POST",
        "/auth/approle/role/" + OPERATOR_ROLE,
        token=root,
        payload={
            "bind_secret_id": True,
            "secret_id_num_uses": 1,
            "secret_id_ttl": "1h",
            "token_policies": [OPERATOR_ROLE],
            "token_no_default_policy": True,
            "token_ttl": "1h",
            "token_max_ttl": "4h",
            "token_type": "service",
        },
    )
    provision(store, root)
    operator = issue_existing_operator(store, root, directory)
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
            if (
                auth["policies"] != [name]
                or auth["lease_duration"] != 300
                or auth["renewable"] is not True
            ):
                raise OperatorError("Workload token shape rejected.")
            tokens[role] = auth["client_token"]
            store.request("POST", "/auth/approle/login", payload=credential, expected=(400, 403))
            for method, path, payload in (
                ("POST", "/auth/token/create", {}),
                ("PUT", "/sys/policies/acl/authority-enlargement", {}),
                ("GET", "/signal-identity/data/platform/application", None),
                ("GET", "/signal-secrets/data/providers/openai", None),
                ("POST", "/auth/approle/role/" + name + "/secret-id", {}),
            ):
                store.request(method, path, token=tokens[role], payload=payload, expected=(403,))
            store.request(
                "POST", "/auth/token/renew-self", token=tokens[role], payload={"increment": "300s"}
            )
        context = ssl.create_default_context(cadata=read_private(directory / "ca.pem").decode())
        asyncio.run(exercise(str(store.client.base_url).rstrip("/"), context, tokens))
        document = store.request(
            "GET",
            "/signal-identity/data/platform/owner-artifacts",
            token=tokens["owner-artifact-reader"],
        )["data"]["data"]
        if (
            document["reference"] != "owner-robots:test-v1"
            or len(bytes.fromhex(document["material_hex"])) != 32
        ):
            raise OperatorError("Artifact key generation rejected.")
        for role in ("slack-connector", "gsc-connector", "owner-artifact-reader"):
            store.request(
                "GET", "/signal-github/data/github/app", token=tokens[role], expected=(403,)
            )
        try:
            provision(store, root)
        except OperatorError:
            pass
        else:
            raise OperatorError("Repeat provisioning was not rejected.")
        store.request("POST", "/sys/seal", token=root)
        store.request(
            "GET", "/signal-github/data/github/app", token=tokens["github-reader"], expected=(503,)
        )
        for share in recovery(directory)["keys_base64"][:2]:
            store.request("POST", "/sys/unseal", payload={"key": share})
        deadline = time.monotonic() + 15
        while True:
            state = store.request("GET", "/sys/health", expected=(200, 429, 473, 503))
            if state.get("sealed") is False and state.get("standby") is False:
                break
            if time.monotonic() >= deadline:
                raise OperatorError("Disposable store did not regain active leadership.")
            time.sleep(0.1)
    finally:
        for token in tokens.values():
            store.request("POST", "/auth/token/revoke-self", token=token)
            store.request("GET", "/auth/token/lookup-self", token=token, expected=(403,))
    store.request("POST", "/auth/token/revoke-self", token=root)
    store.request("GET", "/sys/mounts", token=root, expected=(403,))
    try:
        snapshot(
            store, private_directory(directory / "after-root-retirement"), operator_token=operator
        )
    finally:
        store.request("POST", "/auth/token/revoke-self", token=operator)
        store.request("GET", "/auth/token/lookup-self", token=operator, expected=(403,))


if __name__ == "__main__":
    lab.qualify = qualify
    raise SystemExit(lab.main())
