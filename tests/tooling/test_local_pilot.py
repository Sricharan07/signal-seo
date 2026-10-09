import json

import local_pilot as pilot
import pytest
from signal_core.crawl_artifacts import ArtifactEncryptionKey
from signal_core.crawl_http import CrawlFetchRejected
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.full_site_crawl import FullSiteCrawlExecutor


class _AvailableServer:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def _available_server(_address):
    return _AvailableServer()


def _refused_connection(_address, *, timeout):
    assert timeout == 0.1
    raise OSError("synthetic connection refused")


def test_realm_is_disposable_public_pkce_only():
    fixture = json.loads(pilot.PILOT_REALM_PATH.read_text())
    client = fixture["clients"][0]
    user = fixture["users"][0]

    assert fixture["realm"] == pilot.PILOT_REALM
    assert fixture["registrationAllowed"] is False
    assert fixture["sslRequired"] == "none"
    assert client["clientId"] == pilot.PILOT_CLIENT_ID
    assert client["publicClient"] is True
    assert client["standardFlowEnabled"] is True
    assert client["implicitFlowEnabled"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["serviceAccountsEnabled"] is False
    assert client["redirectUris"] == list(pilot.PILOT_REDIRECT_URIS)
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "basic" in client["defaultClientScopes"]
    assert user["id"] == pilot.PILOT_SUBJECT
    assert user["username"] == pilot.PILOT_USERNAME
    assert user["email"].endswith(".invalid")
    assert user["credentials"] == [
        {"type": "password", "value": pilot.PILOT_PASSWORD, "temporary": False}
    ]


def test_cookie_adapter_maps_only_exact_pilot_cookie_names():
    adapter = pilot.LocalPilotCookieTransport(object(), generation="a1b2c3d4")
    header = (
        b"unrelated=signal_local_a1b2c3d4_session=value; "
        b"signal_local_a1b2c3d4_identity=identity; "
        b"signal_local_a1b2c3d4_session=session"
    )
    assert adapter._to_secure_cookie_header(header) == (
        b"unrelated=signal_local_a1b2c3d4_session=value; "
        b"__Host-signal_identity=identity; "
        b"__Host-signal_session=session"
    )
    assert (
        adapter._to_local_set_cookie(
            b"__Host-signal_session=session; Path=/; HttpOnly; SameSite=lax; Secure"
        )
        == b"signal_local_a1b2c3d4_session=session; Path=/; HttpOnly; SameSite=lax"
    )
    assert (
        adapter._to_local_set_cookie(b"unrelated=value; Path=/; Secure")
        == b"unrelated=value; Path=/; Secure"
    )


def test_pilot_origin_fetcher_resolves_and_rejects_private_destinations(monkeypatch):
    monkeypatch.setattr(
        pilot.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (pilot.socket.AF_INET, pilot.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    boundary = pilot.build_pilot_origin_fetcher()
    policy = CrawlScopePolicy(
        schema_version=1,
        allowed_origins=("https://example.com",),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        max_body_bytes=1024,
    )

    with pytest.raises(CrawlFetchRejected, match="resolution was rejected"):
        boundary.fetch_text("https://example.com/proof.txt", policy=policy)


def test_verified_pilot_registers_real_executor_only_with_ephemeral_artifacts(tmp_path):
    inputs = {
        "crawl_admission_dsn": "postgresql://unused-admission.invalid/db",
        "crawl_ingest_dsn": "postgresql://unused-ingest.invalid/db",
        "artifact_directory": str(tmp_path),
        "artifact_key": ArtifactEncryptionKey("local-pilot-invocation", b"k" * 32),
    }
    assert isinstance(
        pilot.build_pilot_crawl_executor(verified_crawl=False, **inputs),
        pilot.LocalPilotCrawlExecutor,
    )
    assert isinstance(
        pilot.build_pilot_crawl_executor(verified_crawl=True, **inputs),
        FullSiteCrawlExecutor,
    )
    with pytest.raises(pilot.LocalPilotError, match="artifacts are unavailable"):
        pilot.build_pilot_crawl_executor(verified_crawl=True, **{**inputs, "artifact_key": None})


def test_verified_pilot_canonicalizes_a_system_temp_directory_alias(tmp_path):
    real_root = tmp_path / "real"
    real_root.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    executor = pilot.build_pilot_crawl_executor(
        verified_crawl=True,
        crawl_admission_dsn="postgresql://unused-admission.invalid/db",
        crawl_ingest_dsn="postgresql://unused-ingest.invalid/db",
        artifact_directory=str(alias),
        artifact_key=ArtifactEncryptionKey("local-pilot-invocation", b"k" * 32),
    )
    assert executor._store.root == real_root.resolve(strict=True)


def test_verified_pilot_mode_rejects_ambiguous_setting_before_startup(monkeypatch):
    monkeypatch.setenv("SIGNAL_LOCAL_PILOT_VERIFIED_CRAWL", "maybe")
    with pytest.raises(pilot.LocalPilotError, match="must be 0 or 1"):
        pilot.build_and_run()


@pytest.mark.parametrize("generation", ["", "A1B2C3D4", "abc", "../escape"])
def test_cookie_generation_rejects_ambiguous_namespaces(generation):
    with pytest.raises(pilot.LocalPilotError, match="cookie generation"):
        pilot.pilot_cookie_names(generation)


def test_port_preflight_reports_the_exact_busy_loopback_port(monkeypatch):
    def create_server(address):
        if address[1] == 8000:
            raise OSError("synthetic busy port")
        return _available_server(address)

    monkeypatch.setattr(pilot.socket, "create_server", create_server)
    monkeypatch.setattr(pilot.socket, "create_connection", _refused_connection)

    with pytest.raises(pilot.LocalPilotError, match="API port 8000 is already in use"):
        pilot.require_available_ports()


def test_port_preflight_uses_bounded_fallback_without_stopping_another_app(monkeypatch):
    def create_server(address):
        if address[1] == 3000:
            raise OSError("synthetic busy port")
        return _available_server(address)

    monkeypatch.delenv("SIGNAL_LOCAL_PILOT_DASHBOARD_PORT", raising=False)
    monkeypatch.setattr(pilot.socket, "create_server", create_server)
    monkeypatch.setattr(pilot.socket, "create_connection", _refused_connection)

    assert pilot.require_available_ports() == 3001


def test_port_preflight_prefers_default_dashboard_port(monkeypatch):
    monkeypatch.delenv("SIGNAL_LOCAL_PILOT_DASHBOARD_PORT", raising=False)
    monkeypatch.setattr(pilot.socket, "create_server", _available_server)
    monkeypatch.setattr(pilot.socket, "create_connection", _refused_connection)

    assert pilot.require_available_ports() == 3000


@pytest.mark.parametrize("configured", ["3000", "3001"])
def test_port_preflight_accepts_exact_dashboard_port_override(monkeypatch, configured):
    monkeypatch.setenv("SIGNAL_LOCAL_PILOT_DASHBOARD_PORT", configured)
    monkeypatch.setattr(pilot.socket, "create_server", _available_server)
    monkeypatch.setattr(pilot.socket, "create_connection", _refused_connection)

    assert pilot.require_available_ports() == int(configured)


def test_port_preflight_rejects_when_both_dashboard_ports_are_busy(monkeypatch):
    def create_server(address):
        if address[1] in pilot.PILOT_DASHBOARD_PORTS:
            raise OSError("synthetic busy port")
        return _available_server(address)

    monkeypatch.delenv("SIGNAL_LOCAL_PILOT_DASHBOARD_PORT", raising=False)
    monkeypatch.setattr(pilot.socket, "create_server", create_server)
    monkeypatch.setattr(pilot.socket, "create_connection", _refused_connection)

    with pytest.raises(pilot.LocalPilotError, match="ports 3000 and 3001 are already in use"):
        pilot.require_available_ports()


@pytest.mark.parametrize("configured", ["", "2999", "3002", "not-a-port"])
def test_port_preflight_rejects_unapproved_dashboard_ports(monkeypatch, configured):
    monkeypatch.setenv("SIGNAL_LOCAL_PILOT_DASHBOARD_PORT", configured)

    with pytest.raises(pilot.LocalPilotError, match="must be 3000 or 3001"):
        pilot.require_available_ports()


def test_port_preflight_rejects_a_connectable_port_even_when_bind_would_succeed(
    monkeypatch,
):
    def create_connection(address, *, timeout):
        assert timeout == 0.1
        if address[1] == 3000:
            return _AvailableServer()
        raise OSError("synthetic connection refused")

    monkeypatch.delenv("SIGNAL_LOCAL_PILOT_DASHBOARD_PORT", raising=False)
    monkeypatch.setattr(pilot.socket, "create_server", _available_server)
    monkeypatch.setattr(pilot.socket, "create_connection", create_connection)

    assert pilot.require_available_ports() == 3001
