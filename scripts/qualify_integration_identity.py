"""Qualify the private test identity service without exposing operator credentials."""

import argparse
import json
import re
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
from integration_environment import load_integration_scope
from integration_secrets import OperatorError, private_directory, read_private

BASE = "https://localhost:18220/identity"
CLIENT = "signal-test-dashboard"


def require(condition: bool, label: str) -> None:
    if not condition:
        raise OperatorError(f"Identity check failed: {label}.")


def certificate_failure(error: BaseException) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, ssl.SSLCertVerificationError):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


def request(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    response = client.request(method, BASE + path, **kwargs)
    require(len(response.content) < 1024 * 1024, "bounded response")
    return response


def qualify(directory: Path) -> dict:
    context = ssl.create_default_context(cadata=read_private(directory / "ca.pem").decode("ascii"))
    checks = []
    with httpx.Client(
        verify=context, trust_env=False, timeout=10, follow_redirects=False
    ) as client:
        response = request(client, "GET", "/realms/signal/.well-known/openid-configuration")
        require(response.status_code == 200, "discovery")
        discovery = response.json()
        require(discovery["issuer"] == load_integration_scope().issuer, "exact issuer")
        require(
            discovery["authorization_endpoint"]
            == load_integration_scope().issuer + "/protocol/openid-connect/auth",
            "exact authorization endpoint",
        )
        checks.append("private_ca_tls_exact_public_issuer")
        response = request(client, "GET", "/realms/signal/protocol/openid-connect/certs")
        require(response.status_code == 200, "JWKS")
        keys = response.json()["keys"]
        require(
            bool(keys) and all(not set(key) & {"d", "p", "q", "k"} for key in keys),
            "public signing keys only",
        )
        require(any(key.get("alg") == "RS256" for key in keys), "RS256 signing key")
        checks.append("public_jwks_no_private_key")
        response = request(client, "GET", "/admin/realms/signal")
        require(response.status_code in {401, 403}, "unauthenticated administration denied")
        checks.append("unauthenticated_admin_denied")
        for index, parameters in enumerate(
            (
                {
                    "client_id": CLIENT,
                    "redirect_uri": "https://attacker.invalid/callback",
                    "response_type": "code",
                    "scope": "openid",
                    "code_challenge": "a" * 43,
                    "code_challenge_method": "S256",
                },
                {
                    "client_id": CLIENT,
                    "redirect_uri": load_integration_scope().origin + "/auth/callback",
                    "response_type": "code",
                    "scope": "openid",
                },
            )
        ):
            response = request(
                client, "GET", "/realms/signal/protocol/openid-connect/auth", params=parameters
            )
            if index == 0:
                require(response.status_code == 400, "invalid redirect denied")
            else:
                location = urlsplit(response.headers.get("location", ""))
                query = parse_qs(location.query)
                require(
                    response.status_code == 302
                    and location._replace(query="").geturl()
                    == load_integration_scope().origin + "/auth/callback"
                    and query.get("error") == ["invalid_request"]
                    and "code" not in query,
                    "missing PKCE denied at exact registered callback",
                )
        checks.append("wrong_redirect_and_missing_pkce_denied")
        response = request(client, "GET", "/realms/signal/broker/google/endpoint")
        require(response.status_code == 400, "broker callback without state denied")
        checks.append("missing_broker_state_denied")
        for form in (
            {
                "client_id": CLIENT,
                "grant_type": "authorization_code",
                "code": "unissued-negative-test-code",
                "redirect_uri": load_integration_scope().origin + "/auth/callback",
                "code_verifier": "a" * 43,
            },
            {
                "client_id": CLIENT,
                "grant_type": "password",
                "username": "unissued-negative-test-user",
                "password": "not-a-credential",
            },
        ):
            response = request(
                client, "POST", "/realms/signal/protocol/openid-connect/token", data=form
            )
            require(
                response.status_code == 400 and "access_token" not in response.json(),
                "unissued code and password grants denied",
            )
        checks.append("unissued_code_and_password_grants_denied")
        password = read_private(directory / "bootstrap-password").decode("ascii")
        response = request(
            client,
            "POST",
            "/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "signal-test-operator",
                "password": password,
            },
        )
        require(response.status_code == 200, "private temporary operator login")
        tokens = response.json()
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        try:
            response = request(client, "GET", "/admin/realms/signal", headers=headers)
            require(response.status_code == 200, "realm readback")
            realm = response.json()
            require(
                realm["sslRequired"] == "all"
                and not realm["registrationAllowed"]
                and not realm["resetPasswordAllowed"],
                "closed realm",
            )
            realm_id = realm["id"]
            response = request(client, "GET", "/admin/realms/signal/users/count", headers=headers)
            require(
                response.status_code == 200 and response.json() == 0, "no public synthetic users"
            )
            response = request(
                client,
                "GET",
                "/admin/realms/signal/clients",
                headers=headers,
                params={"clientId": CLIENT},
            )
            require(response.status_code == 200 and len(response.json()) == 1, "one exact client")
            registration = response.json()[0]
            require(
                registration["redirectUris"] == [load_integration_scope().origin + "/auth/callback"]
                and registration["webOrigins"] == [],
                "exact client origins",
            )
            require(
                registration["attributes"]["pkce.code.challenge.method"] == "S256"
                and not registration["directAccessGrantsEnabled"]
                and not registration["implicitFlowEnabled"],
                "PKCE-only code client",
            )
            response = request(
                client,
                "GET",
                "/admin/realms/signal/identity-provider/instances/google",
                headers=headers,
            )
            require(response.status_code == 200, "Google broker readback")
            provider = response.json()
            config = provider["config"]
            require(
                provider["enabled"]
                and not provider["storeToken"]
                and config["clientId"] == load_integration_scope().google_login_client_id,
                "exact non-token-storing Google client",
            )
            require(
                config["clientSecret"] == "${vault.google}"
                and config["filteredByClaim"] == "true"
                and config["claimFilterName"] == "email"
                and config["claimFilterValue"]
                == "^" + re.escape(load_integration_scope().owner_email) + "$",
                "owner email filter and file vault",
            )
            require(
                config["disableNonce"] == "false"
                and config["validateSignature"] == "true"
                and config["pkceMethod"] == "S256",
                "broker nonce signature PKCE",
            )
            checks.append("admin_readback_closed_realm_exact_client_google_filter")
        finally:
            response = request(
                client,
                "POST",
                "/realms/master/protocol/openid-connect/logout",
                data={"client_id": "admin-cli", "refresh_token": tokens["refresh_token"]},
            )
            require(response.status_code == 204, "operator session logout")
        checks.append("temporary_operator_session_logged_out")
    try:
        with socket.create_connection(("127.0.0.1", 18220), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname="wrong-identity.invalid"):
                pass
    except ssl.SSLCertVerificationError:
        checks.append("wrong_tls_hostname_denied")
    else:
        raise OperatorError("Identity check failed: wrong TLS hostname accepted.")
    try:
        with httpx.Client(trust_env=False, timeout=5) as untrusted:
            untrusted.get(BASE + "/realms/signal/.well-known/openid-configuration")
    except httpx.ConnectError as error:
        require(certificate_failure(error), "untrusted CA failure")
        checks.append("untrusted_ca_denied")
    else:
        raise OperatorError("Identity check failed: untrusted CA accepted.")
    return {
        "result": "PASS",
        "realm_id": realm_id,
        "checks": checks,
        "google_consent": "NOT_EXECUTED",
        "public_ingress": "NOT_EXECUTED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(qualify(private_directory(args.directory)), sort_keys=True))
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(
            f"Private identity qualification failed ({reason}); values suppressed.", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
