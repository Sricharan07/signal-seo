"""Privately import dedicated test-app configuration; never authorize a connector."""

import argparse
import getpass
import json
import re
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from integration_environment import load_integration_scope
from integration_secrets import (
    ROOT,
    OperatorError,
    Store,
    check_audit,
    private_directory,
    read_private,
    recovery,
)

sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.gsc_secrets import GscClientCredentials  # noqa: E402

PATHS = {
    "github": ("signal-github", "github/app", "signal-github-app-reader"),
    "google-login": ("signal-identity", "google/client", "signal-google-login-reader"),
    "gsc": ("signal-gsc", "oauth-client", "signal-gsc-client-reader"),
    "slack": ("signal-slack", "client", "signal-slack-client-reader"),
}
UPGRADING = (
    "Upgrading from non-versioned to versioned data. This backend will be unavailable "
    "for a brief period and will resume service shortly."
)


def wait_for_new_mount(store: Store, root: str, mount: str) -> None:
    deadline = time.monotonic() + 10
    while True:
        document = store.request("GET", f"/{mount}/config", token=root, expected=(200, 400))
        if isinstance(document.get("data"), dict):
            return
        if document.get("errors") != [UPGRADING] or time.monotonic() >= deadline:
            raise OperatorError(
                "New connector mount did not become ready; no secret write attempted."
            )
        time.sleep(0.1)


def private_download(path: Path, directory: Path) -> bytes:
    if not path.is_absolute() or path.is_symlink() or path.parent.resolve() != directory:
        raise OperatorError("Move the download into the private operator directory first.")
    return read_private(path, maximum=16384)


def github_configuration(content: bytes) -> dict:
    key = serialization.load_pem_private_key(content, password=None)
    if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
        raise OperatorError("GitHub requires an unencrypted RSA private key of at least 2048 bits.")
    return {
        "app_id": load_integration_scope().github_app_id,
        "private_key_pem": content.decode("ascii"),
    }


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise OperatorError("Duplicate client configuration fields were rejected.")
        result[key] = value
    return result


def google_configuration(content: bytes, provider: str) -> dict:
    document = json.loads(content, object_pairs_hook=unique_object)
    if not isinstance(document, dict) or set(document) != {"web"}:
        raise OperatorError("Use the downloaded Web application client JSON.")
    web = document["web"]
    required = {
        "client_id",
        "client_secret",
        "project_id",
        "auth_uri",
        "token_uri",
        "auth_provider_x509_cert_url",
        "redirect_uris",
    }
    if (
        not isinstance(web, dict)
        or not required.issubset(web)
        or set(web) - required - {"javascript_origins"}
        or web.get("project_id") != load_integration_scope().google_project_id
        or web.get("client_id")
        != (
            load_integration_scope().google_login_client_id
            if provider == "google-login"
            else load_integration_scope().google_gsc_client_id
        )
        or web.get("redirect_uris")
        != [
            load_integration_scope().origin
            + (
                "/identity/realms/signal/broker/google/endpoint"
                if provider == "google-login"
                else "/auth/gsc/callback"
            )
        ]
        or web.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
        or web.get("token_uri") != "https://oauth2.googleapis.com/token"
        or web.get("auth_provider_x509_cert_url") != "https://www.googleapis.com/oauth2/v1/certs"
        or web.get("javascript_origins", []) not in ([], [load_integration_scope().origin])
    ):
        raise OperatorError("Google project, callback, or endpoint configuration was rejected.")
    credentials = GscClientCredentials(web["client_id"], web["client_secret"])
    return {"client_id": credentials.client_id, "client_secret": credentials.client_secret}


def slack_configuration(client_secret: str, signing_secret: str) -> dict:
    if any(
        re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value) is None
        for value in (client_secret, signing_secret)
    ):
        raise OperatorError("Slack secret formats were rejected.")
    return {
        "client_id": load_integration_scope().slack_client_id,
        "client_secret": client_secret,
        "signing_secret": signing_secret,
    }


def verify_reader(store: Store, root: str, mount: str, path: str, policy: str, data: dict) -> None:
    issued = store.request(
        "POST",
        "/auth/token/create",
        token=root,
        payload={"policies": [policy], "no_default_policy": True, "ttl": "5m"},
    )
    reader = issued["auth"]["client_token"]
    try:
        envelope = store.request("GET", f"/{mount}/data/{path}", token=reader).get("data", {})
        if envelope.get("data") != data or envelope.get("metadata", {}).get("version") != 1:
            raise OperatorError("Stored connector configuration or generation did not match.")
        for method, target, payload in (
            ("POST", f"/{mount}/data/{path}", {"data": {"value": "synthetic-denied"}}),
            ("DELETE", f"/{mount}/metadata/{path}", None),
            ("GET", f"/{mount}/data/other", None),
            ("POST", "/auth/token/create", {}),
        ):
            store.request(method, target, token=reader, payload=payload, expected=(403,))
    finally:
        store.request("POST", "/auth/token/revoke", token=root, payload={"token": reader})
    store.request("GET", f"/{mount}/data/{path}", token=reader, expected=(403,))


def store_configuration(store: Store, directory: Path, provider: str, data: dict) -> None:
    mount, path, policy = PATHS[provider]
    root = recovery(directory)["root_token"]
    check_audit(store, root)
    policies = store.request("LIST", "/sys/policies/acl", token=root).get("data", {})
    if not isinstance(policies.get("keys"), list) or policy in policies["keys"]:
        raise OperatorError(
            "Reader policy already exists or its state is unknown; no changes made."
        )
    mounts = store.request("GET", "/sys/mounts", token=root)
    current = mounts.get("data", mounts).get(f"{mount}/")
    if current is None:
        store.request(
            "POST",
            f"/sys/mounts/{mount}",
            token=root,
            payload={"type": "kv", "options": {"version": "2"}},
        )
        wait_for_new_mount(store, root, mount)
        store.request("POST", f"/{mount}/config", token=root, payload={"cas_required": True})
    elif current.get("type") != "kv" or current.get("options", {}).get("version") != "2":
        raise OperatorError("Existing connector mount is incompatible.")
    config = store.request("GET", f"/{mount}/config", token=root).get("data", {})
    if config.get("cas_required") is not True:
        raise OperatorError("Existing mount must already enforce CAS; no settings changed.")
    store.request(
        "POST", f"/{mount}/data/{path}", token=root, payload={"options": {"cas": 0}, "data": data}
    )
    store.request(
        "PUT",
        f"/sys/policies/acl/{policy}",
        token=root,
        payload={
            "policy": f'path "{mount}/data/{path}" {{ capabilities = ["read"] }}',
            "cas": 0,
            "cas_required": True,
        },
    )
    verify_reader(store, root, mount, path, policy, data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=tuple(PATHS))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--download", type=Path)
    parser.add_argument("--url", default="https://localhost:18200")
    args = parser.parse_args()
    store = None
    try:
        directory = private_directory(args.directory)
        if args.provider == "slack":
            if args.download is not None or not sys.stdin.isatty():
                raise OperatorError("Slack entry requires the non-echoed interactive prompts.")
            data = slack_configuration(
                getpass.getpass("Slack client secret (hidden): "),
                getpass.getpass("Slack signing secret (hidden): "),
            )
        else:
            if args.download is None:
                raise OperatorError("An explicit private download path is required.")
            content = private_download(args.download, directory)
            data = (
                github_configuration(content)
                if args.provider == "github"
                else google_configuration(content, args.provider)
            )
        store = Store(args.url, directory / "ca.pem")
        store_configuration(store, directory, args.provider, data)
        print("Connector configuration stored and read-only ACL verified; runtime remains unbound.")
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(
            f"Connector import failed ({reason}); values suppressed; no replacement attempted.",
            file=sys.stderr,
        )
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
