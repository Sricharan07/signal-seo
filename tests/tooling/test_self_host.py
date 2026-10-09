"""Synthetic tooling tests; no customer configuration or authority is admitted."""

import base64
import json
import os
import subprocess
from copy import deepcopy
from dataclasses import asdict

import pytest
import self_host as cli
from cryptography.exceptions import InvalidTag
from integration_secrets import OperatorError, read_private
from self_host_material import ingress, prepare, realm, tls_bundle
from signal_core.self_host_config import SelfHostConfig, provider_configuration, provider_projection


def configuration():
    data = json.loads((cli.ROOT / "deploy/self-host/config.example.json").read_text())
    data["images"] = {
        name: "registry.example.invalid/synthetic-" + name + "@sha256:" + "a" * 64
        for name in data["images"]
    }
    return SelfHostConfig.parse(data)


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.initialized = False
        self.lose_response = False

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        if path == "/sys/init":
            if method == "GET":
                return {"initialized": self.initialized}
            self.initialized = True
            if self.lose_response:
                raise OperatorError("Synthetic lost acknowledgement.")
            return {
                "keys_base64": ["synthetic-encrypted-share"] * 3,
                "root_token": "synthetic-encrypted-root",
            }
        if method == "GET":
            if path not in self.values:
                return cli.OperatorReply({}, 404)
            value, version = self.values[path]
            return cli.OperatorReply(
                {
                    "data": {
                        "data": value,
                        "metadata": {"version": version, "deletion_time": "", "destroyed": False},
                    }
                }
            )
        payload = kwargs["payload"]
        assert payload["options"]["cas"] == 0 and path not in self.values
        self.values[path] = (payload["data"], 1)
        if self.lose_response:
            raise OperatorError("Synthetic lost acknowledgement.")
        return {}


def test_create_only_retry_and_unknown_write_never_duplicate():
    store = MemoryStore()
    value = {"api_key": "synthetic-owner-key"}
    assert cli.create_secret(store, "synthetic-token", "/synthetic", value)
    assert not cli.create_secret(store, "synthetic-token", "/synthetic", value)
    with pytest.raises(OperatorError):
        cli.create_secret(store, "synthetic-token", "/synthetic", {"api_key": "synthetic-other"})
    store.lose_response = True
    with pytest.raises(OperatorError):
        cli.create_secret(store, "synthetic-token", "/unknown", value)
    assert not cli.create_secret(store, "synthetic-token", "/unknown", value)
    assert len([call for call in store.calls if call == ("POST", "/unknown")]) == 1


def test_malformed_success_is_not_absence():
    class Malformed:
        def request(self, *args, **kwargs):
            return cli.OperatorReply({}, 200)

    with pytest.raises((OperatorError, KeyError)):
        cli.kv_read(Malformed(), "synthetic-token", "/synthetic")


def test_initialize_is_encrypted_create_only_and_unknown_outcome_stops(tmp_path):
    recipients = [base64.b64encode(bytes([number]) * 32).decode() for number in range(1, 4)]
    store = MemoryStore()
    assert cli.initialize(store, tmp_path, recipients, recipients[0])
    assert not cli.initialize(store, tmp_path, recipients, recipients[0])
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.iterdir())
    assert "synthetic-token" not in (tmp_path / "openbao-init.pgp.json").read_text()
    (tmp_path / "openbao-init.pgp.json").unlink()
    with pytest.raises(OperatorError):
        cli.initialize(store, tmp_path, recipients, recipients[0])


def test_initialize_unknown_acknowledgement_is_not_retried(tmp_path):
    store = MemoryStore()
    store.lose_response = True
    recipients = [base64.b64encode(bytes([number]) * 32).decode() for number in range(1, 4)]
    with pytest.raises(OperatorError):
        cli.initialize(store, tmp_path, recipients, recipients[0])
    with pytest.raises(OperatorError):
        cli.initialize(store, tmp_path, recipients, recipients[0])
    assert store.calls.count(("POST", "/sys/init")) == 1
    assert not (tmp_path / "openbao-init.pgp.json").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "http://signal.example.invalid"},
        {"origin": "https://127.0.0.1"},
        {"origin": "https://signal.example.invalid:443"},
        {"origin": "https://signal.example.invalid/"},
        {"project": "another-track"},
        {"schema_version": True},
        {"owner_username": "owner\nroot"},
        {"images": {}},
        {"unknown": "synthetic-value"},
    ],
)
def test_unsafe_configuration_is_denied(changes):
    data = {"schema_version": 1, **asdict(configuration())}
    data.update(changes)
    with pytest.raises(ValueError):
        SelfHostConfig.parse(data)


def test_secrets_require_hidden_tty_and_failure_output_is_constant(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(OperatorError):
        cli.hidden("synthetic-prompt")
    monkeypatch.setattr(
        cli,
        "configuration",
        lambda _: (_ for _ in ()).throw(RuntimeError("synthetic-must-not-leak")),
    )
    assert (
        cli.main(
            [
                "status",
                "--config",
                str(tmp_path / "config"),
                "--runtime-directory",
                str(tmp_path / "runtime"),
                "--recovery-directory",
                str(tmp_path / "recovery"),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert output.out == "" and "synthetic-must-not-leak" not in output.err
    assert list(tmp_path.iterdir()) == []


def test_repository_symlink_and_non_tmpfs_runtime_denied(tmp_path):
    with pytest.raises(OperatorError):
        cli.external_path(cli.ROOT / "synthetic-private-file")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "target")
    with pytest.raises(OperatorError):
        cli.external_path(link)
    with pytest.raises(OperatorError):
        cli.runtime_directory(tmp_path)


def test_tls_recovery_is_ciphertext_idempotent_and_context_bound(tmp_path, monkeypatch):
    # No host chown in a portable tooling test; production requires Linux root/tmpfs.
    monkeypatch.setattr("self_host_material.os.chown", lambda *args: None)
    monkeypatch.setattr("self_host_material.UIDS", {"openbao": os.getuid()})
    monkeypatch.setattr("self_host_material.write_runtime", lambda *args, **kwargs: None)
    runtime = tmp_path / "runtime"
    recovery = tmp_path / "recovery"
    recovery.mkdir(mode=0o700)
    config = configuration()
    passphrase = "synthetic-independent-long-passphrase"
    prepare(config, runtime, recovery, passphrase)
    original = read_private(recovery / "tls-bootstrap.enc")
    prepare(config, runtime, recovery, passphrase)
    assert read_private(recovery / "tls-bootstrap.enc") == original
    assert b"PRIVATE KEY" not in original and passphrase.encode() not in original
    assert tls_bundle(config, recovery, passphrase)["ca-key.pem"].startswith("-----BEGIN")
    with pytest.raises(InvalidTag):
        tls_bundle(config, recovery, "synthetic-wrong-long-passphrase")


def test_provider_storage_does_not_admit_runtime_or_callbacks():
    projection = provider_projection(["openai", "google"])
    assert len(projection) == 9 and all(item["availability"] == "disabled" for item in projection)
    assert (
        next(item for item in projection if item["provider"] == "jev")["reason"] == "not_configured"
    )
    assert provider_configuration("openai", {"api_key": "synthetic-owner-key"})
    with pytest.raises(ValueError):
        provider_configuration(
            "openai", {"api_key": "synthetic-key", "endpoint": "https://other.invalid"}
        )
    rendered = ingress(configuration(), ["google"])
    assert "@configured_callbacks path /auth/gsc/callback\n" in rendered
    assert "/webhooks/github" not in rendered and "Provider runtime unavailable" in rendered
    assert "@configured_callbacks" not in ingress(configuration(), [])


def test_identity_import_contains_only_initial_owner_and_requires_otp():
    document = realm(
        configuration(), {"subject": "synthetic-subject"}, "synthetic-initial-password"
    )
    assert document["users"][0]["requiredActions"] == ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]
    assert document["registrationAllowed"] is False
    assert document["clients"][0]["directAccessGrantsEnabled"] is False
    assert (
        document["authenticationFlows"][0]["authenticationExecutions"][1]["requirement"]
        == "REQUIRED"
    )
    assert "users" not in realm(configuration())


def test_actual_compose_normalization_and_unsafe_changes(tmp_path):
    config = configuration()
    composed = cli.Compose(config, tmp_path)
    normalized = json.loads(composed.call("config", "--format", "json"))
    cli.validate_compose(normalized, config)
    for field, value in [
        ("ports", [{"published": "5432", "target": 5432}]),
        ("privileged", True),
        ("user", "0:0"),
        ("read_only", False),
        ("image", "postgres:latest"),
    ]:
        changed = deepcopy(normalized)
        changed["services"]["application-database"][field] = value
        with pytest.raises(OperatorError):
            cli.validate_compose(changed, config)


def test_subprocess_failure_cannot_print_secret_diagnostics(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr=b"synthetic-private-secret")

    monkeypatch.setattr(cli.subprocess, "run", fail)
    with pytest.raises(OperatorError) as caught:
        cli.run(["synthetic-operation"], content=b"synthetic-private-secret")
    assert "synthetic-private-secret" not in str(caught.value)
    assert capsys.readouterr().out == ""


def test_reviewed_compose_arguments_accept_dash_without_broadening_commands(tmp_path, monkeypatch):
    config = configuration()
    monkeypatch.setattr(cli, "configuration", lambda _: config)
    monkeypatch.setattr(cli, "runtime_directory", lambda _: tmp_path)
    calls = []

    class Composed:
        def __init__(self, *args):
            pass

        def ensure_volumes(self):
            calls.append("volumes")

        def call(self, *args):
            calls.append(args)

    monkeypatch.setattr(cli, "Compose", Composed)
    flags = [
        "--config",
        str(tmp_path / "config"),
        "--runtime-directory",
        str(tmp_path),
        "--recovery-directory",
        str(tmp_path),
    ]
    assert cli.main(["compose", *flags, "up", "-d", "openbao"]) == 0
    assert calls == ["volumes", ("up", "-d", "openbao")]
    assert cli.main(["compose", *flags, "down", "--volumes"]) == 1
    assert calls == ["volumes", ("up", "-d", "openbao")]


def test_bootstrap_retry_preserves_approval_and_expired_window_requires_rearm(monkeypatch):
    store = MemoryStore()
    bootstrap = {
        "subject": "synthetic-subject",
        "user_id": "synthetic-user",
        "tenant_id": "synthetic-tenant",
        "membership_id": "synthetic-member",
    }
    existing = {**bootstrap, "status": "armed", "approved_at": 100, "expires_at": 3700}
    store.values[cli.APPROVAL] = (existing, 1)
    monkeypatch.setattr(cli.time, "time", lambda: 200)
    assert cli.approval(store, "synthetic-token", {"bootstrap": bootstrap}, arm=True) == existing
    assert store.calls == [("GET", cli.APPROVAL)]
    monkeypatch.setattr(cli.time, "time", lambda: 3700)
    with pytest.raises(OperatorError):
        cli.approval(store, "synthetic-token", {"bootstrap": bootstrap}, arm=True)
