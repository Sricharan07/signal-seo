import json
import ssl
import subprocess
from types import SimpleNamespace

import httpx2
import openbao_lab as lab
import pytest


@pytest.mark.parametrize("delayed", [404, 503, "transport"])
def test_kv_mount_readiness_retries_transient_errors_only(monkeypatch, delayed):
    calls = []
    values = iter([delayed, 200])
    response = httpx2.Response(204)

    class Client:
        def post(self, path, json):
            calls.append(path)
            return response

        def get(self, path, **options):
            calls.append((path, options))
            status = next(values)
            if status == "transport":
                raise httpx2.ConnectError("synthetic-mount-not-ready")
            return httpx2.Response(status, json={"data": {"cas_required": False}})

    monkeypatch.setattr(lab.time, "sleep", lambda _: None)
    assert lab.mount_kv_v2(Client(), "synthetic-kv") is response
    assert calls[1:] == [("/v1/synthetic-kv/config", {"timeout": 0.5})] * 2


def test_kv_readiness_is_bounded_and_never_retries_forbidden(monkeypatch):
    clock = iter([0, 0, 6])
    monkeypatch.setattr(lab.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(lab.time, "sleep", lambda _: None)
    client = SimpleNamespace(
        post=lambda *a, **k: httpx2.Response(204), get=lambda *a, **k: httpx2.Response(503)
    )
    with pytest.raises(lab.LabError, match="timed out"):
        lab.mount_kv_v2(client, "synthetic-kv")
    monkeypatch.setattr(lab.time, "monotonic", lambda: 0)
    client.get = lambda *a, **k: httpx2.Response(403)
    with pytest.raises(lab.LabError, match="readiness failed"):
        lab.mount_kv_v2(client, "synthetic-kv")


def test_kv_mount_failure_keeps_existing_assertion_response():
    response = httpx2.Response(403)
    client = SimpleNamespace(
        post=lambda *a, **k: response,
        get=lambda *a, **k: pytest.fail("Failed mount must not be probed"),
    )
    assert lab.mount_kv_v2(client, "synthetic-kv") is response


def test_disk_preflight_fails_before_docker_is_called(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=3 * 1024**3 - 1))
    monkeypatch.setattr(lab, "docker", lambda *a, **k: pytest.fail("Docker must not be invoked"))
    with pytest.raises(lab.LabError, match="3 GiB"):
        with lab.isolated_openbao():
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


def test_image_and_expected_version_are_pinned():
    assert lab.IMAGE.startswith("ghcr.io/openbao/openbao:2.6.1@sha256:")
    assert len(lab.IMAGE.rsplit(":", 1)[1]) == 64
    assert lab.EXPECTED_VERSION == "OpenBao v2.6.1"


def test_lab_server_representation_hides_root_token_and_tls_context():
    context = ssl.create_default_context()
    server = lab.LabServer(
        base_url="https://localhost:8200",
        version=lab.EXPECTED_VERSION,
        tls_context=context,
        root_token="root-sensitive-token-value",
    )
    rendered = repr(server)
    assert "root-sensitive" not in rendered
    assert repr(context) not in rendered


def test_provision_creates_bounded_mount_disjoint_policies_and_tokens(monkeypatch):
    calls = []
    issued_tokens = iter(("hvs." + "w" * 43, "hvs." + "c" * 43, "hvs." + "r" * 43))

    class RootClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, json):
            calls.append(("POST", path, json))
            if path == "/v1/auth/token/create":
                return httpx2.Response(200, json={"auth": {"client_token": next(issued_tokens)}})
            if path == f"/v1/{lab.AUTHORITY_MOUNT}/data/recovery/current":
                return httpx2.Response(200, json={"data": {"version": 1}})
            return httpx2.Response(204)

        def put(self, path, json):
            calls.append(("PUT", path, json))
            return httpx2.Response(204)

        def get(self, path, **kwargs):
            if not kwargs:
                calls.append(("GET", path, None))
            if path == f"/v1/{lab.AUTHORITY_MOUNT}/config":
                return httpx2.Response(
                    200,
                    json={
                        "data": {
                            "max_versions": 10,
                            "cas_required": True,
                            "delete_version_after": "0s",
                        }
                    },
                )
            return httpx2.Response(
                200,
                json={
                    "data": {
                        "max_versions": 1,
                        "cas_required": True,
                        "delete_version_after": "10m0s",
                    }
                },
            )

    monkeypatch.setattr(lab.httpx2, "Client", lambda **kwargs: RootClient())
    server = lab.LabServer(
        "https://localhost:8200",
        lab.EXPECTED_VERSION,
        ssl.create_default_context(),
        "root-sensitive-token-value",
    )
    writer, consumer, recovery_authority, generation = lab.provision(server)

    mount = next(item for item in calls if item[1] == f"/v1/sys/mounts/{lab.MOUNT}")
    assert mount[2] == {"type": "kv", "options": {"version": "2"}}
    configuration = next(item for item in calls if item[1] == f"/v1/{lab.MOUNT}/config")
    assert configuration[2] == {
        "max_versions": 1,
        "cas_required": True,
        "delete_version_after": "10m",
    }
    authority_mount = next(
        item for item in calls if item[1] == f"/v1/sys/mounts/{lab.AUTHORITY_MOUNT}"
    )
    assert authority_mount[2] == {"type": "kv", "options": {"version": "2"}}
    authority_configuration = next(
        item for item in calls if item[1] == f"/v1/{lab.AUTHORITY_MOUNT}/config"
    )
    assert authority_configuration[2] == {"max_versions": 10, "cas_required": True}
    authority_write = next(
        item for item in calls if item[1] == f"/v1/{lab.AUTHORITY_MOUNT}/data/recovery/current"
    )
    assert authority_write[2]["options"] == {"cas": 0}
    assert authority_write[2]["data"] == {"generation": generation}
    policy_calls = [item for item in calls if item[0] == "PUT"]
    assert len(policy_calls) == 3
    writer_policy = next(item[2]["policy"] for item in policy_calls if lab.WRITER_POLICY in item[1])
    consumer_policy = next(
        item[2]["policy"] for item in policy_calls if lab.CONSUMER_POLICY in item[1]
    )
    recovery_policy = next(
        item[2]["policy"] for item in policy_calls if lab.RECOVERY_POLICY in item[1]
    )
    assert 'capabilities = ["create"]' in writer_policy
    assert 'capabilities = ["read"]' not in writer_policy
    assert 'capabilities = ["delete"]' not in writer_policy
    assert 'capabilities = ["read"]' in consumer_policy
    assert 'capabilities = ["delete"]' in consumer_policy
    assert 'capabilities = ["create"]' not in consumer_policy
    assert recovery_policy == (
        f'path "{lab.AUTHORITY_MOUNT}/data/recovery/current" {{\n  capabilities = ["read"]\n}}\n'
    )
    assert "*" not in recovery_policy
    assert 'capabilities = ["create"]' not in recovery_policy
    assert 'capabilities = ["update"]' not in recovery_policy
    assert 'capabilities = ["delete"]' not in recovery_policy
    token_calls = [item for item in calls if item[1] == "/v1/auth/token/create"]
    assert [item[2]["policies"] for item in token_calls] == [
        [lab.WRITER_POLICY],
        [lab.CONSUMER_POLICY],
        [lab.RECOVERY_POLICY],
    ]
    assert all(item[2]["no_default_policy"] is True for item in token_calls)
    assert all(item[2]["renewable"] is False for item in token_calls)
    assert all(item[2]["explicit_max_ttl"] == "15m" for item in token_calls)
    assert writer.mount == consumer.mount == lab.MOUNT
    assert recovery_authority.mount == lab.AUTHORITY_MOUNT
    assert writer.token != consumer.token != recovery_authority.token


def test_model_credential_provisioning_is_single_secret_and_read_only(monkeypatch):
    calls = []
    api_key = "sk-local-pilot-model-credential-00000000"

    class RootClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, json):
            calls.append(("POST", path, json))
            if path == "/v1/auth/token/create":
                return httpx2.Response(
                    200,
                    json={"auth": {"client_token": "hvs." + "m" * 43}},
                )
            if path == f"/v1/{lab.MODEL_MOUNT}/data/openai/default":
                return httpx2.Response(200)
            return httpx2.Response(204)

        def get(self, path, **kwargs):
            return httpx2.Response(200, json={"data": {"cas_required": False}})

        def put(self, path, json):
            calls.append(("PUT", path, json))
            return httpx2.Response(204)

    monkeypatch.setattr(lab.httpx2, "Client", lambda **kwargs: RootClient())
    server = lab.LabServer(
        "https://localhost:8200",
        lab.EXPECTED_VERSION,
        ssl.create_default_context(),
        "root-sensitive-token-value",
    )
    credential = lab.provision_model_credential(server, api_key)

    secret_write = next(
        item for item in calls if item[1] == f"/v1/{lab.MODEL_MOUNT}/data/openai/default"
    )
    assert secret_write[2] == {"options": {"cas": 0}, "data": {"api_key": api_key}}
    policy = next(item[2]["policy"] for item in calls if item[0] == "PUT")
    assert policy == (
        f'path "{lab.MODEL_MOUNT}/data/openai/default" {{\n  capabilities = ["read"]\n}}\n'
    )
    assert "*" not in policy
    assert "create" not in policy and "update" not in policy and "delete" not in policy
    token_call = next(item for item in calls if item[1] == "/v1/auth/token/create")
    assert token_call[2]["policies"] == [lab.MODEL_READER_POLICY]
    assert api_key not in repr(credential)


def test_model_credential_provisioning_rejects_invalid_secret_before_io(monkeypatch):
    monkeypatch.setattr(
        lab.httpx2,
        "Client",
        lambda **kwargs: pytest.fail("Invalid credentials must not reach OpenBao"),
    )
    server = lab.LabServer(
        "https://localhost:8200",
        lab.EXPECTED_VERSION,
        ssl.create_default_context(),
        "root-sensitive-token-value",
    )
    with pytest.raises(lab.LabError, match="credential is invalid"):
        lab.provision_model_credential(server, "not-a-provider-key")


def test_jev_credential_provisioning_is_single_secret_and_read_only(monkeypatch):
    calls = []
    api_key = "typesafe-local-decision-credential-00000000"

    class RootClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, json):
            calls.append(("POST", path, json))
            if path == "/v1/auth/token/create":
                return httpx2.Response(
                    200,
                    json={"auth": {"client_token": "hvs." + "j" * 43}},
                )
            if path == f"/v1/{lab.DECISION_MOUNT}/data/typesafe/default":
                return httpx2.Response(200)
            return httpx2.Response(204)

        def get(self, path, **kwargs):
            return httpx2.Response(200, json={"data": {"cas_required": False}})

        def put(self, path, json):
            calls.append(("PUT", path, json))
            return httpx2.Response(204)

    monkeypatch.setattr(lab.httpx2, "Client", lambda **kwargs: RootClient())
    server = lab.LabServer(
        "https://localhost:8200",
        lab.EXPECTED_VERSION,
        ssl.create_default_context(),
        "root-sensitive-token-value",
    )
    credential = lab.provision_jev_credential(server, api_key)

    secret_write = next(
        item for item in calls if item[1] == f"/v1/{lab.DECISION_MOUNT}/data/typesafe/default"
    )
    assert secret_write[2] == {"options": {"cas": 0}, "data": {"api_key": api_key}}
    policy = next(item[2]["policy"] for item in calls if item[0] == "PUT")
    assert policy == (
        f'path "{lab.DECISION_MOUNT}/data/typesafe/default" {{\n  capabilities = ["read"]\n}}\n'
    )
    assert "*" not in policy
    assert "create" not in policy and "update" not in policy and "delete" not in policy
    token_call = next(item for item in calls if item[1] == "/v1/auth/token/create")
    assert token_call[2]["policies"] == [lab.DECISION_READER_POLICY]
    assert api_key not in repr(credential)


def test_jev_credential_provisioning_rejects_invalid_secret_before_io(monkeypatch):
    monkeypatch.setattr(
        lab.httpx2,
        "Client",
        lambda **kwargs: pytest.fail("Invalid credentials must not reach OpenBao"),
    )
    server = lab.LabServer(
        "https://localhost:8200",
        lab.EXPECTED_VERSION,
        ssl.create_default_context(),
        "root-sensitive-token-value",
    )
    with pytest.raises(lab.LabError, match="Jev credential is invalid"):
        lab.provision_jev_credential(server, "contains whitespace")


@pytest.mark.parametrize(
    "configuration",
    [
        {"max_versions": 2, "cas_required": True, "delete_version_after": "10m0s"},
        {"max_versions": 1, "cas_required": False, "delete_version_after": "10m0s"},
        {"max_versions": 1, "cas_required": True, "delete_version_after": "0s"},
    ],
)
def test_provision_rejects_unbounded_mount_configuration(monkeypatch, configuration):
    class RootClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, json):
            if path == "/v1/auth/token/create":
                return httpx2.Response(200, json={"auth": {"client_token": "hvs." + "x" * 43}})
            return httpx2.Response(204)

        def put(self, path, json):
            return httpx2.Response(204)

        def get(self, path, **kwargs):
            return httpx2.Response(200, json={"data": configuration})

    monkeypatch.setattr(lab.httpx2, "Client", lambda **kwargs: RootClient())
    server = lab.LabServer(
        "https://localhost:8200",
        lab.EXPECTED_VERSION,
        ssl.create_default_context(),
        "root-sensitive-token-value",
    )
    with pytest.raises(lab.LabError, match="bounded profile"):
        lab.provision(server)


def test_report_contract_has_no_secret_or_token_material(tmp_path):
    report = {
        "tests": [{"name": "CAS-zero PKCE verifier creation", "status": "PASS"}],
        "token_material_recorded": False,
        "secret_material_recorded": False,
        "recovery_generation_recorded": False,
    }
    encoded = json.dumps(report)
    assert "code_verifier" not in encoded
    assert "client_token" not in encoded
    assert report["token_material_recorded"] is False
    assert report["secret_material_recorded"] is False
    assert report["recovery_generation_recorded"] is False
