import json
import os
from dataclasses import FrozenInstanceError, asdict, replace

import httpx2
import pytest
from integration_environment import deployment_variables, render, render_realm
from signal_api.integration_runtime import IntegrationOriginResolver, create_integration_app
from signal_core.crawl_http import CrawlFetchRejected
from signal_core.integration_scope import IntegrationScopeUnavailable, load_integration_scope

from tests.conftest import INTEGRATION_FIXTURE


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("missing", list(INTEGRATION_FIXTURE))
def test_every_missing_scope_field_is_unavailable(dedicated_integration_configuration, missing):
    data = dict(INTEGRATION_FIXTURE)
    del data[missing]
    dedicated_integration_configuration.write_text(json.dumps(data))
    with pytest.raises(IntegrationScopeUnavailable, match="configuration is unavailable"):
        load_integration_scope()


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "http://signal-test.example.invalid"),
        ("origin", "https://signal-test.example.invalid/"),
        ("origin", "https://user@signal-test.example.invalid"),
        ("origin", "https://signal-test.example.invalid:8443"),
        ("origin", "https://signal-test.example.invalid?redirect=bad"),
        ("owner_email", "not-an-email"),
        ("owner_subject", "bad\nsubject"),
        ("site_id", "00000000-0000-1000-8000-000000000002"),
        ("slack_workspace_id", "wrong"),
        ("slack_channel_id", "T0000000000"),
        ("slack_client_id", "wrong"),
        ("google_project_id", "wrong scope"),
        ("google_login_client_id", "not-google"),
        ("google_gsc_client_id", "not-google"),
        ("github_app_id", True),
        ("github_installation_id", 0),
        ("origin", "https://192.0.2.10"),
        (
            "google_gsc_client_id",
            "000000000000-placeholder.apps.googleusercontent.com",
        ),
        ("github_owner", "../other"),
        ("github_repository", "repo\nother"),
        ("github_base_branch", "main~1"),
        ("github_content_path", "../secret"),
        ("public_ipv4", "not-an-address"),
        ("public_ipv6", "not-an-address"),
        ("ssh_source_ipv6", "not-an-address"),
        ("ssh_key_path", "relative"),
        ("ssh_known_hosts_path", "/private/../wrong"),
        ("realm_id", "not-a-uuid"),
        ("incomplete_user_id", "not-a-uuid"),
        ("incomplete_created_timestamp", False),
        ("tenant_name", "bad\nname"),
        ("home_region", "bad region"),
    ],
)
def test_malformed_scope_fields_never_gain_defaults(
    dedicated_integration_configuration, field, value
):
    data = dict(INTEGRATION_FIXTURE, **{field: value})
    dedicated_integration_configuration.write_text(json.dumps(data))
    with pytest.raises(IntegrationScopeUnavailable):
        load_integration_scope()


@pytest.mark.parametrize(
    "kind",
    ["missing", "broad", "symlink", "hardlink", "duplicate", "extra", "oversized", "invalid"],
)
def test_protected_scope_read_failures_are_closed(
    dedicated_integration_configuration, kind, tmp_path
):
    path = dedicated_integration_configuration
    if kind == "missing":
        path.unlink()
    elif kind == "broad":
        path.chmod(0o644)
    elif kind in {"symlink", "hardlink"}:
        copy = tmp_path / "scope-copy"
        path.rename(copy)
        if kind == "symlink":
            path.symlink_to(copy)
        else:
            os.link(copy, path)
    elif kind == "duplicate":
        path.write_text('{"origin":"https://first.invalid",' + path.read_text()[1:])
    elif kind == "extra":
        path.write_text(json.dumps(dict(INTEGRATION_FIXTURE, unexpected="rejected")))
    elif kind == "oversized":
        path.write_text(" " * 16385)
    else:
        path.write_bytes(b"\xff")
    with pytest.raises(IntegrationScopeUnavailable):
        load_integration_scope()


def test_scope_snapshot_is_immutable_and_values_are_not_diagnostics(
    dedicated_integration_configuration,
):
    scope = load_integration_scope()
    assert asdict(scope) == INTEGRATION_FIXTURE
    assert scope.site_id == str(scope.site)
    assert scope.owner_email not in repr(scope)
    with pytest.raises(FrozenInstanceError):
        scope.origin = "https://other.invalid"
    dedicated_integration_configuration.write_text(
        json.dumps(dict(INTEGRATION_FIXTURE, slack_workspace_id="T1111111111"))
    )
    assert scope.slack_workspace_id == "T0000000000"
    assert load_integration_scope().slack_workspace_id == "T1111111111"


def test_render_uses_only_protected_scope_and_preserves_exact_broker_policy(tmp_path):
    scope = load_integration_scope()
    realm = render_realm(scope)
    provider = realm["identityProviders"][0]
    assert provider["config"]["clientId"] == scope.google_login_client_id
    assert provider["config"]["claimFilterValue"] == r"^owner@example\.invalid$"
    assert provider["storeToken"] is False
    assert realm["clients"][0]["redirectUris"] == [scope.origin + "/auth/callback"]
    assert realm["clients"][0]["attributes"]["pkce.code.challenge.method"] == "S256"
    target = tmp_path / "render"
    render(target)
    assert json.loads((target / "environment.json").read_text()) == INTEGRATION_FIXTURE
    assert (target / "Caddyfile").read_text().count(scope.host) == 2
    assert "__SIGNAL" not in (target / "Caddyfile").read_text()
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in target.iterdir())
    assert deployment_variables(scope)["SIGNAL_DASHBOARD_ORIGIN"] == scope.origin
    with pytest.raises(ValueError, match="empty protected"):
        render(target)


def test_documentation_ip_is_not_an_egress_exception(tmp_path):
    scope = replace(load_integration_scope(), public_ipv4="192.0.2.10")
    path = tmp_path / "origin-pin.json"
    path.write_text(
        json.dumps(
            dict(origin=scope.origin, address=scope.public_ipv4, issued_at=900, expires_at=1100)
        )
    )
    path.chmod(0o600)
    resolver = IntegrationOriginResolver(path, lambda: 1000, integration_scope=scope)
    with pytest.raises(CrawlFetchRejected):
        resolver(scope.host, 443, 5)


@pytest.mark.anyio
async def test_missing_runtime_scope_is_visibly_unavailable_without_loading_credentials(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SIGNAL_INTEGRATION_CONFIG_FILE", str(tmp_path / "missing"))
    app = create_integration_app()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/health/ready", "/v1/session/login", "/v1/session", "/v1/sites"):
            response = await client.get(path)
            assert response.status_code == 503
            assert response.json() == {"error": {"code": "TEST_CONFIGURATION_UNAVAILABLE"}}
        assert (await client.get("/health/live")).status_code == 200
        capabilities = (await client.get("/v1/capabilities")).json()
        assert capabilities["production_writes_enabled"] is False
        assert all(item["availability"] != "internal_only" for item in capabilities["capabilities"])
