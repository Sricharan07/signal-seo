import gzip
import json
import os
from pathlib import Path

import httpx2
import integration_secrets as operator
import pytest
import qualify_integration_secrets_restore as restore
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def test_recovery_directory_is_private_and_outside_repository(tmp_path):
    directory = tmp_path / "operator"
    assert operator.private_directory(directory) == directory.resolve()
    assert directory.stat().st_mode & 0o777 == 0o700
    with pytest.raises(operator.OperatorError, match="outside the repository"):
        operator.private_directory(operator.ROOT / ".runtime/operator")
    with pytest.raises(operator.OperatorError, match="absolute"):
        operator.private_directory(Path("relative"))


def test_recovery_directory_rejects_shared_permissions_and_symlinks(tmp_path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    with pytest.raises(operator.OperatorError, match="0700"):
        operator.private_directory(directory)
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(operator.OperatorError, match="nonsymlink"):
        operator.private_directory(link)


def test_private_file_is_exclusive_and_rejects_unsafe_reads(tmp_path):
    path = tmp_path / "recovery.json"
    operator.write_private(path, b"synthetic-private-fixture")
    assert operator.read_private(path) == b"synthetic-private-fixture"
    with pytest.raises(FileExistsError):
        operator.write_private(path, b"synthetic-replacement")
    path.chmod(0o644)
    with pytest.raises(operator.OperatorError, match="owner-only"):
        operator.read_private(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        operator.read_private(link)
    hardlink = tmp_path / "hardlink"
    os.link(path, hardlink)
    with pytest.raises(operator.OperatorError, match="owner-only"):
        operator.read_private(path)


def test_private_file_rejects_large_material(tmp_path):
    path = tmp_path / "large"
    operator.write_private(path, b"x" * (operator.LIMIT + 1))
    with pytest.raises(operator.OperatorError, match="small"):
        operator.read_private(path)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:18200",
        "https://example.com:8200",
        "https://127.0.0.1:8200",
        "https://user:password@localhost:8200",
        "https://localhost",
        "https://localhost:8200/v1",
        "https://localhost:8200?token=x",
        "https://localhost:8200#fragment",
    ],
)
def test_operator_transport_rejects_nonlocal_or_ambiguous_destinations(url):
    with pytest.raises(operator.OperatorError):
        operator.local_url(url)


def test_tls_is_fresh_bounded_and_does_not_overwrite_existing_keys(tmp_path):
    operator.prepare_tls(tmp_path)
    ca = x509.load_pem_x509_certificate(operator.read_private(tmp_path / "ca.pem"))
    server = x509.load_pem_x509_certificate(operator.read_private(tmp_path / "server.pem"))
    assert server.issuer == ca.subject
    server.verify_directly_issued_by(ca)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert not server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert (server.not_valid_after_utc - server.not_valid_before_utc).days == 30
    assert server.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName) == ["localhost", "openbao"]
    original = operator.read_private(tmp_path / "ca-key.pem")
    with pytest.raises(FileExistsError):
        operator.prepare_tls(tmp_path)
    assert operator.read_private(tmp_path / "ca-key.pem") == original


def mock_store(tmp_path, handler):
    operator.prepare_tls(tmp_path)
    store = operator.Store("https://localhost:18200", tmp_path / "ca.pem")
    store.client.close()
    store.client = httpx2.Client(
        base_url="https://localhost:18200", transport=httpx2.MockTransport(handler)
    )
    return store


def test_transport_bounds_and_redacts_provider_errors(tmp_path):
    store = mock_store(
        tmp_path,
        lambda _: httpx2.Response(500, json={"errors": ["synthetic-secret-not-for-output"]}),
    )
    try:
        with pytest.raises(operator.OperatorError, match="HTTP 500") as error:
            store.request("GET", "/sys/health")
        assert "synthetic-secret" not in str(error.value)
    finally:
        store.close()


@pytest.mark.parametrize(
    "reply",
    [
        httpx2.Response(200, text="synthetic-non-json"),
        httpx2.Response(200, json=[]),
        httpx2.Response(
            200, content=b"x" * (operator.LIMIT + 1), headers={"Content-Type": "application/json"}
        ),
        httpx2.Response(302, headers={"Location": "https://other.invalid"}),
    ],
)
def test_transport_rejects_untrusted_responses(tmp_path, reply):
    store = mock_store(tmp_path, lambda _: reply)
    try:
        with pytest.raises(operator.OperatorError):
            store.request("GET", "/sys/health")
    finally:
        store.close()


def test_initialize_refuses_existing_or_unknown_state(tmp_path):
    class Store:
        def request(self, *args, **kwargs):
            return {"initialized": True}

    with pytest.raises(operator.OperatorError, match="already initialized"):
        operator.initialize(Store(), tmp_path)
    operator.write_private(tmp_path / "recovery.json", b"{}")
    with pytest.raises(operator.OperatorError, match="already exists"):
        operator.initialize(Store(), tmp_path)


def test_initialization_persists_shares_before_unsealing_and_audits(tmp_path):
    calls = []
    bundle = {
        "root_token": "synthetic-root-token",
        "keys_base64": ["synthetic-share-1", "synthetic-share-2", "synthetic-share-3"],
    }

    class Store:
        def request(self, method, path, **kwargs):
            calls.append(path)
            if path == "/sys/init":
                return {"initialized": False} if method == "GET" else bundle
            assert json.loads(operator.read_private(tmp_path / "recovery.json")) == bundle
            if path == "/sys/seal-status":
                return {"sealed": False, "t": 2, "n": 3}
            if path == "/sys/audit":
                return {
                    "data": {
                        "operator-file/": {
                            "type": "file",
                            "options": {
                                "file_path": "/openbao/logs/audit.jsonl",
                                "log_raw": "false",
                                "mode": "0600",
                            },
                        }
                    }
                }
            return {}

    operator.initialize(Store(), tmp_path)
    assert calls == [
        "/sys/init",
        "/sys/init",
        "/sys/unseal",
        "/sys/unseal",
        "/sys/seal-status",
        "/sys/audit",
    ]


def test_unseal_fails_if_final_threshold_or_state_is_wrong(tmp_path):
    operator.write_private(
        tmp_path / "recovery.json",
        json.dumps({"root_token": "synthetic-root-token", "keys_base64": ["a", "b", "c"]}).encode(),
    )

    class Store:
        def request(self, *args, **kwargs):
            return {"sealed": True, "t": 1, "n": 1}

    with pytest.raises(operator.OperatorError, match="final state"):
        operator.unseal(Store(), tmp_path)


def test_initialization_timeout_is_not_retried_or_marked_success(tmp_path):
    calls = []

    class Store:
        def request(self, method, path, **kwargs):
            calls.append((method, path))
            if method == "GET":
                return {"initialized": False}
            assert kwargs["timeout"] == 90
            raise httpx2.ReadTimeout("synthetic unknown initialization outcome")

    with pytest.raises(httpx2.ReadTimeout):
        operator.initialize(Store(), tmp_path)
    assert calls == [("GET", "/sys/init"), ("PUT", "/sys/init")]
    assert not (tmp_path / "recovery.json").exists()


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"file_path": "stdout", "log_raw": "true", "mode": "0600"},
        {"file_path": "/openbao/logs/audit.jsonl", "log_raw": "false", "mode": "0644"},
    ],
)
def test_missing_or_unsafe_audit_is_not_qualified(options):
    class Store:
        def request(self, *args, **kwargs):
            return {"data": {"operator-file/": {"type": "file", "options": options}}}

    with pytest.raises(operator.OperatorError, match="audit configuration"):
        operator.check_audit(Store(), "synthetic-root-token")


@pytest.mark.parametrize(
    "keys",
    [
        {},
        {"openai": "invalid", "jev": "synthetic-jev-key"},
        {"openai": "sk-synthetic-openai-key", "jev": "white space"},
        {"openai": "sk-synthetic-openai-key", "jev": "synthetic-jev-key", "other": "x"},
    ],
)
def test_invalid_model_credentials_never_reach_storage(tmp_path, keys):
    class Store:
        def request(self, *args, **kwargs):
            pytest.fail("Invalid credentials reached storage")

    with pytest.raises(operator.OperatorError, match="formats were rejected"):
        operator.put_model_keys(Store(), tmp_path, keys)


def test_model_secret_paths_are_cas_create_only_and_reader_policies_are_narrow(
    tmp_path, monkeypatch
):
    verified = []
    monkeypatch.setattr(operator, "verify_model_reader", lambda *args: verified.append(args[2:5]))
    operator.write_private(
        tmp_path / "recovery.json",
        json.dumps({"root_token": "synthetic-root-token", "keys_base64": ["a", "b", "c"]}).encode(),
    )
    calls = []

    class Store:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs.get("payload")))
            if path == "/sys/audit":
                return {
                    "data": {
                        "operator-file/": {
                            "type": "file",
                            "options": {
                                "file_path": "/openbao/logs/audit.jsonl",
                                "log_raw": "false",
                                "mode": "0600",
                            },
                        }
                    }
                }
            return {}

    operator.put_model_keys(
        Store(), tmp_path, {"openai": "sk-synthetic-openai-key", "jev": "synthetic-jev-key"}
    )
    writes = [(path, payload) for method, path, payload in calls if "/data/" in path]
    assert [path for path, _ in writes] == [
        "/signal-model/data/openai/default",
        "/signal-decision/data/typesafe/default",
    ]
    assert all(payload["options"] == {"cas": 0} for _, payload in writes)
    policies = [payload["policy"] for _, path, payload in calls if "/policies/" in path]
    assert len(policies) == 2
    assert all('capabilities = ["read"]' in policy and "*" not in policy for policy in policies)
    assert verified == [
        ("signal-model", "openai/default", "signal-model-reader"),
        ("signal-decision", "typesafe/default", "signal-decision-reader"),
    ]


@pytest.mark.parametrize("encoded", [False, True])
def test_snapshot_is_authenticated_encrypted_and_exclusive(tmp_path, encoded):
    operator.write_private(
        tmp_path / "recovery.json",
        json.dumps({"root_token": "synthetic-root-token", "keys_base64": ["a", "b", "c"]}).encode(),
    )
    payload = gzip.compress(b"synthetic-snapshot-bytes") if encoded else b"synthetic-snapshot-bytes"
    store = mock_store(
        tmp_path,
        lambda _: httpx2.Response(
            200,
            stream=httpx2.ByteStream(payload),
            headers={"Content-Encoding": "gzip"} if encoded else {},
        ),
    )
    try:
        operator.snapshot(store, tmp_path)
        key = operator.read_private(tmp_path / "snapshot-key.bin")
        encrypted = operator.read_private(tmp_path / "snapshot.enc")
        assert b"synthetic-snapshot" not in encrypted
        assert (
            AESGCM(key).decrypt(encrypted[:12], encrypted[12:], b"signal-test-snapshot-v1")
            == payload
        )
        with pytest.raises(InvalidTag):
            AESGCM(key).decrypt(
                encrypted[:12],
                encrypted[12:-1] + bytes([encrypted[-1] ^ 1]),
                b"signal-test-snapshot-v1",
            )
        with pytest.raises(operator.OperatorError, match="already exist"):
            operator.snapshot(store, tmp_path)
        assert operator.read_private(tmp_path / "snapshot-key.bin") == key
        assert restore.decrypt_snapshot(tmp_path) == payload
    finally:
        store.close()


@pytest.mark.parametrize(
    "source,target",
    [
        ({}, {}),
        ({"cluster_id": "same"}, {"cluster_id": "same", "sealed": False}),
        ({"cluster_id": "source"}, {"cluster_id": "target", "sealed": True}),
        ({"cluster_id": "source"}, {"cluster_id": "", "sealed": False}),
    ],
)
def test_restore_rejects_unknown_primary_or_unprepared_targets(source, target):
    with pytest.raises(operator.OperatorError, match="distinct"):
        restore.distinct_clusters(source, target)


def test_restore_requires_distinct_cluster_identity():
    restore.distinct_clusters({"cluster_id": "source"}, {"cluster_id": "target", "sealed": False})


@pytest.mark.parametrize("matches", [True, False])
def test_model_reader_is_readonly_and_revoked_even_on_mismatch(matches):
    calls = []

    class Store:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == "/auth/token/create":
                return {"auth": {"client_token": "synthetic-reader-token"}}
            if method == "GET" and path.endswith("/openai/default"):
                return {
                    "data": {
                        "data": {
                            "api_key": "sk-synthetic-matching-key"
                            if matches
                            else "sk-synthetic-other-key"
                        },
                        "metadata": {"version": 1},
                    }
                }
            if path != "/auth/token/revoke":
                assert kwargs["expected"] == (403,)
            return {}

    if matches:
        operator.verify_model_reader(
            Store(),
            "synthetic-root-token",
            "signal-model",
            "openai/default",
            "signal-model-reader",
            "sk-synthetic-matching-key",
        )
    else:
        with pytest.raises(operator.OperatorError, match="did not match"):
            operator.verify_model_reader(
                Store(),
                "synthetic-root-token",
                "signal-model",
                "openai/default",
                "signal-model-reader",
                "sk-synthetic-matching-key",
            )
    assert calls[-1][1] == "/auth/token/revoke"
    assert calls[-1][2]["payload"] == {"token": "synthetic-reader-token"}
