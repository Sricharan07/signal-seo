"""Disable the private Keycloak bootstrap operator; never create a public user."""

import argparse
import ssl
from pathlib import Path

import httpx2
from integration_secrets import OperatorError, read_private
from qualify_integration_identity import request, require


def retire(directory: Path):
    context = ssl.create_default_context(cadata=read_private(directory / "ca.pem").decode())
    with httpx2.Client(
        verify=context, trust_env=False, timeout=10, follow_redirects=False
    ) as client:
        response = request(
            client,
            "POST",
            "/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "signal-test-operator",
                "password": read_private(directory / "bootstrap-password").decode(),
            },
        )
        require(response.status_code == 200, "bootstrap authentication")
        tokens = response.json()
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        try:
            users = request(
                client,
                "GET",
                "/admin/realms/master/users",
                headers=headers,
                params={"username": "signal-test-operator", "exact": "true"},
            )
            require(users.status_code == 200 and len(users.json()) == 1, "sole private operator")
            user = users.json()[0]
            require(
                user["username"] == "signal-test-operator" and user["enabled"] is True,
                "enabled bootstrap operator",
            )
            # Bounded private provider events support the later human subject/MFA audit.
            events = request(
                client,
                "PUT",
                "/admin/realms/signal",
                headers=headers,
                json={"eventsEnabled": True, "eventsExpiration": 3600},
            )
            require(events.status_code == 204, "bounded private identity events")
            response = request(
                client,
                "PUT",
                "/admin/realms/master/users/" + user["id"],
                headers=headers,
                json={"enabled": False},
            )
            require(response.status_code == 204, "operator disabled")
            readback = request(
                client, "GET", "/admin/realms/master/users/" + user["id"], headers=headers
            )
            require(readback.status_code == 401, "disabled operator access token denied")
        finally:
            response = request(
                client,
                "POST",
                "/realms/master/protocol/openid-connect/logout",
                data={
                    "client_id": "admin-cli",
                    "refresh_token": tokens["refresh_token"],
                },
            )
            require(response.status_code in {204, 400, 401}, "bootstrap session unavailable")
        denied = request(
            client,
            "POST",
            "/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "signal-test-operator",
                "password": read_private(directory / "bootstrap-password").decode(),
            },
        )
        require(
            denied.status_code == 400 and denied.json()["error"] == "invalid_grant",
            "disabled credential denied",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        retire(args.directory)
        print("Private identity bootstrap operator disabled; access and credential denied.")
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(f"Identity bootstrap retirement incomplete ({reason}); no automatic retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
