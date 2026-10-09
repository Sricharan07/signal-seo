"""Require real OTP after Google login; no synthetic MFA claim or owner binding."""

import argparse
import ssl
from pathlib import Path

import httpx2
from integration_secrets import OperatorError, private_directory, read_private
from qualify_integration_identity import CLIENT, request, require

FLOW = "signal-google-required-otp"


def configure(directory: Path):
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
        require(response.status_code == 200, "private operator authentication")
        tokens = response.json()
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        try:

            def admin(method, path, **kwargs):
                response = request(
                    client, method, "/admin/realms/signal" + path, headers=headers, **kwargs
                )
                require(response.status_code in {200, 201, 204}, "MFA configuration operation")
                return response

            flows = admin("GET", "/authentication/flows").json()
            require(not any(flow["alias"] == FLOW for flow in flows), "create-only OTP flow")
            admin(
                "POST",
                "/authentication/flows",
                json={
                    "alias": FLOW,
                    "providerId": "basic-flow",
                    "topLevel": True,
                    "builtIn": False,
                    "description": "Verified Google identity followed by required OTP.",
                },
            )
            admin(
                "POST",
                f"/authentication/flows/{FLOW}/executions/execution",
                json={"provider": "auth-otp-form"},
            )
            execution = admin("GET", f"/authentication/flows/{FLOW}/executions").json()
            require(
                len(execution) == 1 and execution[0]["providerId"] == "auth-otp-form",
                "sole OTP execution",
            )
            execution = execution[0]
            execution["requirement"] = "REQUIRED"
            admin("PUT", f"/authentication/flows/{FLOW}/executions", json=execution)
            admin(
                "POST",
                f"/authentication/executions/{execution['id']}/config",
                json={
                    "alias": "signal-verified-otp-method",
                    "config": {"default.reference.value": "otp", "default.reference.maxAge": "300"},
                },
            )
            provider = admin("GET", "/identity-provider/instances/google").json()
            require(
                provider["config"]["clientSecret"] == "${vault.google}",
                "unchanged private Google secret",
            )
            provider["postBrokerLoginFlowAlias"] = FLOW
            admin("PUT", "/identity-provider/instances/google", json=provider)
            registration = admin("GET", "/clients", params={"clientId": CLIENT}).json()
            require(len(registration) == 1, "exact dashboard client")
            client_id = registration[0]["id"]
            admin(
                "POST",
                f"/clients/{client_id}/protocol-mappers/models",
                json={
                    "name": "signal-completed-authentication-methods",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-amr-mapper",
                    "consentRequired": False,
                    "config": {"id.token.claim": "true", "access.token.claim": "false"},
                },
            )
            updated = admin("GET", f"/authentication/flows/{FLOW}/executions").json()
            require(
                len(updated) == 1 and updated[0]["requirement"] == "REQUIRED",
                "required OTP readback",
            )
            config = admin(
                "GET", "/authentication/config/" + updated[0]["authenticationConfig"]
            ).json()
            require(
                set(config["config"]) == {"default.reference.value", "default.reference.maxAge"}
                and all(value == "**********" for value in config["config"].values()),
                "masked completed-method configuration; independent database check required",
            )
            require(
                admin("GET", "/identity-provider/instances/google").json()[
                    "postBrokerLoginFlowAlias"
                ]
                == FLOW,
                "Google post-login binding",
            )
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
            require(response.status_code == 204, "operator logout")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        configure(private_directory(args.directory))
        print(
            "Required Google OTP and signed completed-method mapper configured; "
            "human MFA test pending."
        )
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(f"MFA configuration failed ({reason}); values suppressed; no automatic retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
