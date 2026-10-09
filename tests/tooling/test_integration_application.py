import json
import subprocess

import httpx2
import pytest
from integration_application import POLICIES, provision
from integration_identity_egress import HOSTS, pins, render
from integration_retire_identity_operator import retire as retire_identity
from integration_retire_root import ROLE, retire
from integration_secrets import OperatorError


def test_existing_application_authority_cannot_be_replaced():
    class Store:
        def request(self, method, path, **kwargs):
            if path == "/sys/audit":
                return {
                    "data": {
                        "file/": {"type": "file", "options": {"file_path": "/bao/audit/audit.json"}}
                    }
                }
            if path == "/sys/mounts":
                return {"data": {"signal-authority/": {}}}
            if path == "/sys/auth":
                return {"data": {}}
            raise AssertionError("No authority write is allowed")

    with pytest.raises(OperatorError):
        provision(Store(), "fixture")


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1"])
def test_identity_pins_reject_private_and_non_ipv4_destinations(tmp_path, address):
    with pytest.raises((OperatorError, ValueError)):
        render(tmp_path, dict.fromkeys(HOSTS, address))
    assert not list(tmp_path.iterdir())


def test_identity_pins_require_all_exact_hosts(tmp_path):
    with pytest.raises(OperatorError):
        render(tmp_path, {"attacker.invalid": "8.8.8.8"})
    assert not list(tmp_path.iterdir())


def test_pins_reuse_public_address_screening():
    with pytest.raises(ValueError):
        pins(lambda host, port, timeout: ("169.254.169.254",))


def test_generated_rules_expire_even_established_traffic_and_keep_product_connectors_disabled(
    tmp_path,
):
    addresses = dict(zip(HOSTS, ("8.8.8.8", "1.1.1.1", "8.8.4.4", "1.0.0.1"), strict=True))
    render(tmp_path, addresses)
    document = json.loads((tmp_path / "google-pins.json").read_text())
    assert len(document["services"]["identity"]["extra_hosts"]) == 4
    assert "openidconnect.googleapis.com" in addresses
    assert (
        document["services"]["identity"]["environment"][
            "KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__CONNECTION_REQUEST_TIMEOUT_MILLIS"
        ]
        == "5000"
    )
    script = (tmp_path / "google-egress.sh").read_text()
    assert 'test "$(date +%s)" -lt' in script
    for address in addresses.values():
        assert f"-d {address}/32 -p tcp --dport 443 -j DROP" in script
    assert "-j ACCEPT" in script and "-m conntrack --ctstate NEW" in script
    assert "-j MASQUERADE" in script and "-d " in script
    assert "Persistent=true" in (tmp_path / "signal-google-egress-expiry.timer").read_text()
    assert (
        json.loads((tmp_path / "google-pins-evidence.json").read_text())["provider_data_connectors"]
        == "DISABLED"
    )
    subprocess.run(["sh", "-n", str(tmp_path / "google-egress.sh")], check=True)


def test_root_retirement_has_only_exact_runtime_restart_authority(tmp_path, monkeypatch):
    calls = []

    class Store:
        def __init__(self, *args):
            pass

        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path.endswith("/role-id"):
                return {"data": {"role_id": "fixture-role"}}
            if path.endswith("/secret-id"):
                return {"data": {"secret_id": "fixture-secret"}}
            if path == "/auth/approle/login":
                return {
                    "auth": {
                        "client_token": "fixture-operator",
                        "policies": [ROLE],
                        "lease_duration": 3600,
                    }
                }
            return {}

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("integration_retire_root.Store", Store)
    monkeypatch.setattr(
        "integration_retire_root.recovery", lambda path: {"root_token": "fixture-root"}
    )
    retire(tmp_path)
    policy = next(
        call[2]["payload"]["policy"]
        for call in calls
        if isinstance(call, tuple) and call[1] == "/sys/policies/acl/" + ROLE
    )
    assert "*" not in policy and '"root"' not in policy
    assert "google/client" not in policy and "sys/policies/acl" not in policy
    for role in POLICIES:
        assert f"auth/approle/role/signal-test-{role}/secret-id" in policy
    assert (
        next(
            call
            for call in calls
            if isinstance(call, tuple) and call[1] == "/auth/token/revoke-self"
        )[2]["token"]
        == "fixture-root"
    )
    assert (
        json.loads((tmp_path / "root-retirement.json").read_text())["bootstrap_root"] == "REVOKED"
    )
    with pytest.raises(OperatorError, match="already exists"):
        retire(tmp_path)


@pytest.mark.parametrize("readback_status", [401, 200, 503])
def test_identity_retirement_denies_bootstrap_access_and_always_logs_out(
    tmp_path, monkeypatch, readback_status
):
    calls = []

    class Client:
        def __aenter__(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def request(client, method, path, **kwargs):
        calls.append((method, path))
        if path.endswith("/token"):
            if calls.count((method, path)) == 1:
                return httpx2.Response(
                    200, json={"access_token": "fixture", "refresh_token": "fixture"}
                )
            return httpx2.Response(400, json={"error": "invalid_grant"})
        if path == "/admin/realms/master/users":
            return httpx2.Response(
                200,
                json=[{"id": "fixture-user", "username": "signal-test-operator", "enabled": True}],
            )
        if method == "GET":
            return httpx2.Response(readback_status, json={})
        return httpx2.Response(204)

    monkeypatch.setattr(
        "integration_retire_identity_operator.read_private", lambda path: b"fixture"
    )
    monkeypatch.setattr(
        "integration_retire_identity_operator.ssl.create_default_context", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "integration_retire_identity_operator.httpx2.Client", lambda **kwargs: Client()
    )
    monkeypatch.setattr("integration_retire_identity_operator.request", request)
    if readback_status == 401:
        retire_identity(tmp_path)
        assert calls[-1][1].endswith("/token")
    else:
        with pytest.raises(OperatorError, match="access token denied"):
            retire_identity(tmp_path)
    assert ("POST", "/realms/master/protocol/openid-connect/logout") in calls
    assert ("PUT", "/admin/realms/master/users/fixture-user") in calls
