import json
import stat
import subprocess
from types import SimpleNamespace

import keycloak_lab as lab
import pytest


def test_disk_preflight_fails_before_docker_is_called(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=3 * 1024**3 - 1))
    monkeypatch.setattr(lab, "docker", lambda *a, **k: pytest.fail("Docker must not be invoked"))
    with pytest.raises(lab.LabError, match="3 GiB"):
        with lab.isolated_keycloak():
            pytest.fail("Low-space lab cannot start")
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=3 * 1024**3))
    lab.require_free_space(tmp_path)


def test_docker_error_does_not_expose_arguments_or_output(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["secret-argument"], stderr="secret-output")

    monkeypatch.setattr(lab.subprocess, "run", fail)
    with pytest.raises(lab.LabError) as result:
        lab.docker("info")
    assert "secret" not in str(result.value)
    assert "CalledProcessError" in str(result.value)


def test_cleanup_filters_by_run_label_and_removes_only_exact_name(monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return "unrelated\nrun-owned" if args[:2] == ("container", "ls") else ""

    monkeypatch.setattr(lab, "docker", docker)
    lab.cleanup("run-owned")
    listing = next(call for call in calls if call[:2] == ("container", "ls"))
    assert f"label={lab.LABEL}=run-owned" in listing
    assert ("container", "rm", "--force", "--volumes", "run-owned") in calls


def test_cleanup_does_not_remove_unmatched_container(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, "docker", lambda *a, **k: calls.append(a) or "unrelated")
    lab.cleanup("run-owned")
    assert all(call[:2] == ("container", "ls") for call in calls)


def test_container_user_matches_private_bind_owner_without_root(monkeypatch):
    monkeypatch.setattr(lab.os, "getuid", lambda: 1001)
    assert lab.keycloak_container_user() == "1001:0"


def test_container_user_rejects_root_host(monkeypatch):
    monkeypatch.setattr(lab.os, "getuid", lambda: 0)
    with pytest.raises(lab.LabError, match="non-root"):
        lab.keycloak_container_user()


def test_readiness_wait_retries_transient_status_then_accepts_discovery():
    calls = []
    sleeps = []
    responses = iter([SimpleNamespace(status_code=503), SimpleNamespace(status_code=200)])

    class Client:
        def get(self, url, *, headers):
            calls.append((url, headers))
            return next(responses)

    lab.wait_for_keycloak(
        Client(),
        "https://127.0.0.1:8443/realms/signal-lab/.well-known/openid-configuration",
        timeout_seconds=1,
        monotonic=iter([0.0, 0.0]).__next__,
        sleep=sleeps.append,
    )

    assert len(calls) == 2
    assert all(headers == {"Accept": "application/json"} for _, headers in calls)
    assert sleeps == [lab.KEYCLOAK_STARTUP_POLL_SECONDS]


def test_readiness_wait_fails_closed_at_deadline_after_transport_error():
    class Client:
        def get(self, url, *, headers):
            raise lab.httpx2.ConnectError("synthetic connection refusal")

    with pytest.raises(lab.LabError, match="within 0.5 seconds"):
        lab.wait_for_keycloak(
            Client(),
            "https://127.0.0.1:8443/realms/signal-lab/.well-known/openid-configuration",
            timeout_seconds=0.5,
            monotonic=iter([0.0, 0.5]).__next__,
            sleep=lambda _: pytest.fail("The deadline must not be extended"),
        )


def test_keycloak_cold_start_policy_is_bounded_for_ci():
    assert lab.KEYCLOAK_STARTUP_TIMEOUT_SECONDS == 180.0
    assert 0 < lab.KEYCLOAK_STARTUP_POLL_SECONDS <= 0.25


def test_login_parser_selects_only_keycloak_form_and_hidden_fields():
    parser = lab.LoginFormParser()
    parser.feed(
        '<form id="unrelated" action="https://attacker.invalid">'
        '<input type="hidden" name="outside" value="ignored"></form>'
        '<form id="kc-form-login" action="/realms/signal-lab/login-actions/authenticate">'
        '<input type="hidden" name="execution" value="one">'
        '<input type="text" name="username"></form>'
    )
    assert parser.action == "/realms/signal-lab/login-actions/authenticate"
    assert parser.hidden_inputs == {"execution": "one"}


@pytest.mark.parametrize(
    "candidate",
    [
        "https://attacker.invalid/realms/signal-lab/login",
        "http://127.0.0.1:8080/realms/other/login",
        "http://127.0.0.1:8080/realms/signal-lab/login#fragment",
    ],
)
def test_browser_flow_rejects_untrusted_redirects(candidate):
    with pytest.raises(lab.LabError, match="untrusted redirect"):
        lab._require_provider_url(candidate, "http://127.0.0.1:8080/realms/signal-lab")


def test_realm_is_synthetic_public_pkce_only():
    fixture = json.loads(lab.REALM_PATH.read_text())
    client = fixture["clients"][0]
    user = fixture["users"][0]
    assert fixture["realm"] == "signal-lab"
    assert fixture["registrationAllowed"] is False
    assert client["publicClient"] is True
    assert client["standardFlowEnabled"] is True
    assert client["implicitFlowEnabled"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["serviceAccountsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["redirectUris"] == [lab.REDIRECT_URI]
    assert "basic" in client["defaultClientScopes"]
    assert user["email"].endswith(".invalid")
    assert user["credentials"][0]["value"].startswith("signal-lab-only-")


def test_image_is_versioned_and_digest_pinned():
    assert lab.IMAGE.startswith("quay.io/keycloak/keycloak:26.7.3@sha256:")
    assert len(lab.IMAGE.rsplit(":", 1)[1]) == 64


def test_generated_tls_files_are_private_and_ephemeral(tmp_path):
    certificate, key = lab._generate_certificate(tmp_path)
    assert stat.S_IMODE(certificate.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert b"BEGIN CERTIFICATE" in certificate.read_bytes()
    assert b"BEGIN PRIVATE KEY" in key.read_bytes()
    grant = lab.AuthorizationGrant(code="code", code_verifier="verifier", nonce="nonce")
    assert "code" not in repr(grant)
    assert "verifier" not in repr(grant)
    assert "nonce" not in repr(grant)
