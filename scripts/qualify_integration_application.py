"""Exercise the dedicated private login runtime without a synthetic owner."""

import argparse
import json
import socket
import ssl
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
from integration_environment import load_integration_scope
from integration_secrets import OperatorError, read_private


def require(condition, message):
    if not condition:
        raise OperatorError(message)


def rejected_callback(response, destination, attempt_cookie=None):
    require(
        response.status_code == 303 and response.headers.get("location") == destination,
        "invalid callback rejection",
    )
    require(
        response.headers.get("referrer-policy") == "no-referrer"
        and "no-store" in response.headers.get("cache-control", "")
        and "content-disposition" not in response.headers,
        "callback credential URL and response suppression",
    )
    if attempt_cookie is not None:
        cookies = response.headers.get_list("set-cookie")
        require(
            len(cookies) == 1
            and cookies[0].startswith(attempt_cookie + "=;")
            and all(
                attribute in cookies[0]
                for attribute in ("Path=/", "Secure", "HttpOnly", "SameSite=Lax", "Max-Age=0")
            ),
            "invalid connector callback clears only its correlation cookie",
        )


def provider_capabilities(capabilities, *, owner_connectors=False):
    connectors = {"provider.slack", "provider.gsc", "provider.github"}
    observed = set()
    require(capabilities["production_writes_enabled"] is False, "production write prohibition")
    for capability in capabilities["capabilities"]:
        key = capability["key"]
        if key.startswith("provider.") or key == "database.command_acceptance":
            require(key not in observed, "duplicate capability rejection")
            observed.add(key)
            expected = "internal_only" if owner_connectors and key in connectors else "disabled"
            require(capability["availability"] == expected, "unqualified work unavailable")
    require("database.command_acceptance" in observed, "worker denial must be declared")
    if owner_connectors:
        require(connectors <= observed, "exact test connectors must be declared")
    return "INTERNAL_ONLY_TEST_CONNECTORS_WORK_DISABLED" if owner_connectors else "DISABLED"


def qualify(ca: Path, *, owner_connectors=False):
    context = ssl.create_default_context(cadata=read_private(ca).decode())
    results = {}
    with httpx2.Client(
        verify=context, trust_env=False, timeout=10, follow_redirects=False
    ) as client:
        api, dashboard = "https://localhost:18380", "https://localhost:18480"
        require(client.get(api + "/health/ready").status_code == 200, "private readiness")
        require(client.get(dashboard + "/").status_code == 200, "private web page")
        results["private_tls_readiness"] = "PASS"
        capabilities = client.get(api + "/v1/capabilities").json()
        results["unqualified_providers_and_work"] = provider_capabilities(
            capabilities, owner_connectors=owner_connectors
        )
        for path in ("/v1/commands", "/v1/sites/unregistered/commands/snapshot"):
            require(
                client.post(api + path, json={}).status_code == 503, "undeployed worker rejection"
            )
        require(client.get(api + "/v1/sites").status_code == 401, "site directory authentication")
        require(
            client.post(api + "/v1/sites", json={}).status_code == 403,
            "unauthenticated site mutation rejection",
        )
        require(
            client.put(api + "/v1/session/site", json={}).status_code == 403,
            "unauthenticated site selection rejection",
        )
        results["protected_site_routes"] = "PASS"
        require(
            client.get(
                api + "/v1/session/login", params={"return_path": "https://attacker.invalid"}
            ).status_code
            == 422,
            "external return path rejection",
        )
        response = client.get(api + "/v1/session/login")
        require(response.status_code == 303, "durable PKCE start")
        location = urlsplit(response.headers["location"])
        query = parse_qs(location.query)
        require(
            location.scheme == "https"
            and location.netloc == load_integration_scope().host
            and location.path == "/identity/realms/signal/protocol/openid-connect/auth",
            "exact public identity endpoint",
        )
        require(
            query["redirect_uri"] == [load_integration_scope().origin + "/auth/callback"]
            and query["code_challenge_method"] == ["S256"],
            "exact callback and PKCE",
        )
        cookies = response.headers.get_list("set-cookie")
        require(
            any(
                "__Host-signal_oidc_binding=" in cookie
                and "Secure" in cookie
                and "HttpOnly" in cookie
                and "SameSite=lax" in cookie
                for cookie in cookies
            ),
            "secure browser binding",
        )
        for params in (
            {"state": "invalid", "code": "invalid"},
            {"state": query["state"][0], "code": "unissued-test-code"},
        ):
            rejected = client.get(api + "/v1/session/callback", params=params)
            require(rejected.status_code == 401, "unissued callback rejection")
            require(
                not any(
                    "__Host-signal_identity=" in cookie and "Max-Age=0" not in cookie
                    for cookie in rejected.headers.get_list("set-cookie")
                ),
                "no identity from invalid callback",
            )
        results["pkce_start_and_callback_rejection"] = "PASS"
        for headers in (
            {},
            {"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "same-origin"},
        ):
            require(
                client.post(dashboard + "/auth/login", headers=headers).status_code == 403,
                "cross-origin mutation rejection",
            )
        started = client.post(
            dashboard + "/auth/login",
            headers={"Origin": load_integration_scope().origin, "Sec-Fetch-Site": "same-origin"},
        )
        require(
            started.status_code == 303
            and started.headers["location"].startswith(
                load_integration_scope().issuer + "/protocol/openid-connect/auth?"
            ),
            "private web-to-API login relay",
        )
        results["same_origin_web_relay"] = "PASS"
    for context, hostname in (
        (ssl.create_default_context(), "localhost"),
        (ssl.create_default_context(cadata=read_private(ca).decode()), "unregistered.invalid"),
    ):
        try:
            with socket.create_connection(("127.0.0.1", 18380), timeout=5) as connection:
                context.wrap_socket(connection, server_hostname=hostname).close()
        except ssl.SSLCertVerificationError:
            continue
        raise OperatorError("Untrusted certificate or wrong hostname was admitted.")
    results["untrusted_ca_and_wrong_hostname"] = "REJECTED"
    results["human_google_mfa_and_owner_session"] = "PENDING"
    return results


def qualify_public():
    results = {}
    with httpx2.Client(trust_env=False, timeout=10, follow_redirects=False) as client:
        page = client.get(load_integration_scope().origin + "/")
        require(page.status_code == 200, "public HTTPS page")
        require(page.headers["referrer-policy"] == "same-origin", "real form Origin preservation")
        require("no-store" in page.headers["cache-control"], "private page cache denial")
        for path in (
            "/identity/admin/realms",
            "/identity/realms/master/.well-known/openid-configuration",
            "/identity/realms/signal/protocol/openid-connect/token",
            "/identity/realms/signal/broker/unregistered/endpoint",
            "/identity/realms/signal/broker/unregistered",
            "/v1/capabilities",
            "/health/ready",
            "/metrics",
            "/docs",
            "/openapi.json",
        ):
            require(
                client.get(load_integration_scope().origin + path).status_code == 404,
                "private route isolation",
            )
        require(
            client.get("http://" + load_integration_scope().host + "/").status_code == 404,
            "HTTP limited to ACME",
        )
        discovery = client.get(
            load_integration_scope().issuer + "/.well-known/openid-configuration"
        )
        require(
            discovery.status_code == 200
            and discovery.json()["issuer"] == load_integration_scope().issuer,
            "public identity issuer",
        )
        for suffix in ("after-first-broker-login", "after-post-broker-login"):
            rejected = client.get(load_integration_scope().issuer + "/broker/" + suffix)
            require(
                rejected.status_code == 400
                and rejected.headers.get("referrer-policy") == "no-referrer"
                and "Signal Test" in rejected.text,
                "exact broker completion route reaches identity and rejects missing state",
            )
        results["broker_completion_routes_and_missing_state"] = "PASS"
        for origin in ("null", "https://attacker.invalid"):
            require(
                client.post(
                    load_integration_scope().origin + "/auth/login",
                    headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"},
                ).status_code
                == 403,
                "null and foreign Origin rejection",
            )
        started = client.post(
            load_integration_scope().origin + "/auth/login",
            headers={"Origin": load_integration_scope().origin, "Sec-Fetch-Site": "same-origin"},
        )
        require(
            started.status_code == 303
            and started.headers["location"].startswith(
                load_integration_scope().issuer + "/protocol/openid-connect/auth?"
            ),
            "public login start",
        )
        for path, destination, attempt_cookie in (
            ("/auth/callback", load_integration_scope().origin + "/?auth=callback-rejected", None),
            (
                "/auth/slack/callback",
                load_integration_scope().origin + "/connectors?slack=unavailable",
                "__Host-signal-slack-attempt",
            ),
            (
                "/auth/gsc/callback",
                load_integration_scope().origin + "/connectors?gsc=unavailable",
                "__Host-signal-gsc-attempt",
            ),
        ):
            callback = client.get(load_integration_scope().origin + path)
            rejected_callback(callback, destination, attempt_cookie)
    for port in (5432, 8200, 8280, 8380, 8480, 9000, 7233, 2019):
        try:
            socket.create_connection(
                (load_integration_scope().public_ipv4, port), timeout=1
            ).close()
        except (TimeoutError, OSError):
            continue
        raise OperatorError("A private administrative port was exposed.")
    results["public_tls_login_and_private_route_isolation"] = "PASS"
    results["public_private_port_isolation"] = "PASS"
    results["slack_installation_gsc_binding_github_runtime"] = "NOT_EXECUTED"
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--owner-connectors", action="store_true")
    args = parser.parse_args()
    try:
        results = qualify(args.ca, owner_connectors=args.owner_connectors)
        if args.public:
            results.update(qualify_public())
        print(json.dumps(results, sort_keys=True))
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(f"Runtime qualification failed ({reason}); credential values suppressed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
