"""Real OpenBao CAS, audit, idempotence and workload downscope checks."""

from dataclasses import replace

import pytest
from integration_secrets import OperatorError, check_audit, snapshot
from qualify_integration_secrets_restore import decrypt_snapshot
from self_host import INSTALL, PREFIX, configured_providers, kv_read, provision, put_provider
from self_host_lab import isolated_bootstrap
from signal_core.self_host_config import SelfHostConfig


@pytest.fixture(scope="module")
def bao():
    with pytest.MonkeyPatch.context() as monkeypatch, isolated_bootstrap(monkeypatch) as server:
        yield server


@pytest.fixture
def config():
    return SelfHostConfig(
        "signal-self-host",
        "https://signal.example.invalid",
        "owner",
        "Synthetic workspace",
        "self-host",
        {
            name: f"registry.example.invalid/synthetic-{name}@sha256:" + "a" * 64
            for name in ("api", "dashboard", "identity", "ingress", "workflow")
        },
    )


def test_real_bootstrap_idempotence_and_immutable_configuration(bao, config):
    store, token, tls = bao
    first = provision(store, token, config, tls)
    assert provision(store, token, config, tls) == first
    assert kv_read(store, token, INSTALL)[1] == 1
    check_audit(store, token)
    with pytest.raises(OperatorError):
        provision(store, token, replace(config, owner_username="different"), tls)


def test_real_owner_credentials_only_storage_and_rotation_denied(bao):
    store, token, _ = bao
    assert configured_providers(store, token) == []
    assert put_provider(store, token, "openai", {"api_key": "synthetic-owner-openai-key"})
    assert not put_provider(store, token, "openai", {"api_key": "synthetic-owner-openai-key"})
    assert configured_providers(store, token) == ["openai"]
    with pytest.raises(OperatorError):
        put_provider(store, token, "openai", {"api_key": "synthetic-unreviewed-rotation"})


def test_real_workload_cannot_read_operator_or_provider_secrets(bao):
    store, root, _ = bao
    path = "/auth/approle/role/" + PREFIX + "recovery-reader"
    role = store.request("GET", path + "/role-id", token=root)["data"]["role_id"]
    secret = store.request("POST", path + "/secret-id", token=root)["data"]["secret_id"]
    token = store.request(
        "POST", "/auth/approle/login", payload={"role_id": role, "secret_id": secret}
    )["auth"]["client_token"]
    try:
        assert store.request("GET", "/signal-authority/data/recovery/current", token=token)["data"]
        for method, path in [
            ("GET", INSTALL),
            ("GET", "/signal-model/data/openai/default"),
            ("POST", "/sys/seal"),
        ]:
            store.request(method, path, token=token, expected=(403,))
        store.request(
            "POST",
            "/auth/approle/login",
            payload={"role_id": role, "secret_id": secret},
            expected=(400,),
        )
    finally:
        store.request("POST", "/auth/token/revoke-self", token=token)


def test_real_mount_safety_mismatch_refused_without_repair(bao, config):
    store, token, tls = bao
    store.request("POST", "/signal-self-host/config", token=token, payload={"cas_required": False})
    with pytest.raises(OperatorError):
        provision(store, token, config, tls)
    assert (
        store.request("GET", "/signal-self-host/config", token=token)["data"]["cas_required"]
        is False
    )


def test_reused_real_snapshot_capture_is_encrypted_and_create_only(bao, tmp_path):
    store, token, _ = bao
    snapshot(store, tmp_path, operator_token=token)
    assert decrypt_snapshot(tmp_path).startswith(b"\x1f\x8b")
    assert (tmp_path / "snapshot.enc").stat().st_mode & 0o777 == 0o600
    with pytest.raises(OperatorError):
        snapshot(store, tmp_path, operator_token=token)
