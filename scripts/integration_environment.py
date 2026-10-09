"""Render operator-supplied nonsecret identifiers into protected test configuration."""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.integration_scope import load_integration_scope  # noqa: E402


def render_realm(scope):
    template = json.loads((ROOT / "deploy/integration-test/identity/realm.json").read_text())
    client = template["clients"][0]
    provider = template["identityProviders"][0]["config"]
    if (
        client["redirectUris"] != ["__SIGNAL_ORIGIN__/auth/callback"]
        or provider["clientId"] != "__GOOGLE_LOGIN_CLIENT_ID__"
        or provider["claimFilterValue"] != "__OWNER_EMAIL_REGEX__"
    ):
        raise ValueError("Identity template configuration rejected.")
    client["redirectUris"] = [scope.origin + "/auth/callback"]
    provider["clientId"] = scope.google_login_client_id
    provider["claimFilterValue"] = "^" + re.escape(scope.owner_email) + "$"
    return template


def deployment_variables(scope):
    target = {
        key: getattr(scope, key)
        for key in (
            "github_installation_id",
            "github_owner",
            "github_repository",
            "github_base_branch",
            "github_content_path",
        )
    }
    return {
        "SIGNAL_DASHBOARD_ORIGIN": scope.origin,
        "SIGNAL_IDENTITY_PROVIDER_ORIGIN": scope.origin,
        "SIGNAL_IDENTITY_HOSTNAME": scope.origin + "/identity",
        "SIGNAL_OWNER_CONNECTOR_SCOPE": json.dumps(target, separators=(",", ":")),
    }


def render(directory):
    from dataclasses import asdict

    from integration_secrets import private_directory, write_private

    scope = load_integration_scope()
    destination = private_directory(directory)
    if any(destination.iterdir()):
        raise ValueError("An empty protected render destination is required.")
    write_private(destination / "environment.json", json.dumps(asdict(scope)).encode())
    write_private(
        destination / "dashboard-origin.json",
        json.dumps({"origin": scope.origin}, separators=(",", ":")).encode(),
    )
    write_private(destination / "realm.json", json.dumps(render_realm(scope)).encode())
    template = (ROOT / "deploy/integration-test/application/Caddyfile").read_text()
    if template.count("__SIGNAL_INTEGRATION_HOST__") != 2:
        raise ValueError("Ingress template configuration rejected.")
    write_private(
        destination / "Caddyfile",
        template.replace("__SIGNAL_INTEGRATION_HOST__", scope.host).encode(),
    )
    environment = "\n".join(
        f"{key}='{value}'" for key, value in deployment_variables(scope).items()
    )
    write_private(destination / "environment.env", (environment + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(args.directory)
        print("Protected environment configuration rendered; all identifier values suppressed.")
        return 0
    except Exception as error:
        print(f"Environment unavailable ({type(error).__name__}); no credentials or defaults used.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
